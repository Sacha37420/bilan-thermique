"""Opérations sur les environnements décomposés en objets (Lot AH) — la couche
Django entre les vues/tâches et la logique pure d'api.observed_env.

Trois responsabilités :
- génération complète (LiDAR HD × BD TOPO, avec repli BD TOPO / OpenStreetMap
  hors couverture), jusqu'à l'Environment enregistré ;
- recomposition de `Environment.envelope` après tout changement d'objet, et
  péremption de l'ombrage des bâtiments liés ;
- création du bâtiment étudié à partir d'un objet, ou d'un modèle importé
  placé à la place d'un objet.
"""

import math
from datetime import date

from django.db import transaction
from rest_framework import serializers as drf_serializers

from . import elevation, geodata, lidar_source, observed_env
from .models import Building, Environment


class EnvironmentServiceError(ValueError):
    pass


# ── Utilitaires ────────────────────────────────────────────────────────────────

def unique_name(model, base, max_length=150):
    base = (base or 'Sans nom').strip()[:max_length - 6]
    name = base
    k = 2
    while model.objects.filter(name=name).exists():
        name = f'{base} ({k})'
        k += 1
    return name


def recompose(env):
    """Reconstruit l'enveloppe d'occlusion à partir des objets actifs, et périme
    l'ombrage de tout bâtiment lié : la même règle que
    EnvironmentSerializer.update (correctif L1) — une grille calculée contre
    l'ancienne géométrie donnerait un résultat faux sans le moindre signe."""
    env.envelope = observed_env.compose_envelope(env.scene_objects)
    env.save(update_fields=['envelope', 'scene_objects', 'updated_at'])
    Building.objects.filter(environment=env).update(sun_visibility_stale=True)


def find_object(env, obj_id):
    for obj in env.scene_objects or []:
        if obj['id'] == obj_id:
            return obj
    raise EnvironmentServiceError(f"Objet {obj_id} introuvable dans cet environnement.")


# ── Statut des objets ──────────────────────────────────────────────────────────

def set_status(env, ids, status):
    """Retire ou restaure des objets. Un objet `studied` restauré redevient un
    obstacle ordinaire — utile quand le bâtiment étudié a été supprimé, ou pour
    revenir sur un choix ; il perd alors son lien vers ce bâtiment."""
    wanted = set(ids)
    found = set()
    for obj in env.scene_objects or []:
        if obj['id'] in wanted:
            found.add(obj['id'])
            obj['status'] = status
            if status == observed_env.STATUS_ACTIVE:
                obj['building_id'] = None
                obj['reason'] = None
    missing = wanted - found
    if missing:
        raise EnvironmentServiceError(f"Objet(s) introuvable(s) : {sorted(missing)}.")
    recompose(env)


# ── Bâtiment étudié ────────────────────────────────────────────────────────────

def _building_payload(env, name, vertices, triangles):
    return {
        'name': name, 'description': f"Issu de l'environnement « {env.name} ».",
        'vertices': vertices, 'triangles': triangles,
        'environment_id': env.pk,
        # Même repère que l'environnement, par construction : c'est ce qui garantit
        # l'alignement exact entre le bâtiment et ses voisins (position, cap,
        # altitude), sans aucune saisie.
        'georef_lat': env.georef_lat, 'georef_lon': env.georef_lon,
        'georef_north_offset_deg': env.georef_north_offset_deg,
        'georef_ground_z': env.georef_ground_z,
    }


def _save_building(payload):
    from .serializers import BuildingSerializer
    serializer = BuildingSerializer(data=payload)
    try:
        serializer.is_valid(raise_exception=True)
    except drf_serializers.ValidationError as exc:
        raise EnvironmentServiceError(f"Enveloppe refusée : {exc.detail}") from exc
    return serializer.save()


def _assign_materials(triangles, info, catalogue):
    """Pré-assignation (Lot AI) : un modèle de paroi par famille de groupes
    (murs / toiture / sol) d'après les matériaux et l'année BD TOPO, et
    l'absorptance extérieure par triangle (toiture mesurée sur l'orthophoto).
    Les murs mitoyens reçoivent le modèle de mur mais pas d'absorptance : ils
    ne voient pas le soleil. `catalogue` : {nom: id} des modèles en base — un
    nom suggéré absent du catalogue laisse la famille non assignée.
    Retourne le rapport de suggestion (pour l'interface)."""
    suggestion = observed_env.suggest_materials(info)
    ids = {k: catalogue.get(suggestion[k]) for k in ('wall', 'roof', 'floor')}
    for tri in triangles:
        group = tri.get('group') or ''
        if group.startswith('mur_'):
            tri['paroi_model_id'] = ids['wall']
            if suggestion['wall_alpha'] is not None and not group.endswith('_mitoyen'):
                tri['alpha_ext'] = suggestion['wall_alpha']
        elif group.startswith('toiture'):
            tri['paroi_model_id'] = ids['roof']
            if suggestion['roof_alpha'] is not None:
                tri['alpha_ext'] = suggestion['roof_alpha']
        elif group == 'sol':
            tri['paroi_model_id'] = ids['floor']
    suggestion['assigned'] = {k: (suggestion[k] if ids[k] else None) for k in ('wall', 'roof', 'floor')}
    return suggestion


@transaction.atomic
def study_objects(env, obj_ids, name='', assign_materials=True):
    """Le bâtiment étudié EST un (ou plusieurs) objet(s) de l'environnement.

    Un objet : son maillage (murs par arête d'emprise, pans de toiture, plancher
    au sol) devient l'enveloppe d'un Building, dans le même repère.
    Plusieurs : enveloppe REconstruite sur l'union des emprises
    (observed_env.merge_objects) — jamais la juxtaposition des volumes, qui
    garderait les murs entre parties comme des parois extérieures.
    Retourne (building, env, rapport de matériaux ou None)."""
    env = Environment.objects.select_for_update().get(pk=env.pk)
    objs = [find_object(env, oid) for oid in dict.fromkeys(obj_ids)]
    for obj in objs:
        if obj['kind'] != 'building':
            raise EnvironmentServiceError("Seuls des bâtiments peuvent former le bâtiment étudié.")
        if obj['status'] == observed_env.STATUS_STUDIED:
            raise EnvironmentServiceError(
                f"L'objet {obj['id']} est déjà le bâtiment étudié d'un autre bâtiment."
            )
    ids = {o['id'] for o in objs}
    try:
        if len(objs) == 1:
            info = objs[0]['info']
            vertices, triangles = observed_env.building_envelope_from_object(objs[0], env.scene_objects)
            label = objs[0]['label']
        else:
            mv, mt, _poly, info = observed_env.merge_objects(objs)
            vertices, triangles = observed_env.building_envelope_from_mesh(mv, mt, ids, env.scene_objects)
            label = f"{len(objs)} bâtiments réunis"
    except observed_env.ObservedEnvError as exc:
        raise EnvironmentServiceError(str(exc)) from exc

    materials = None
    if assign_materials:
        from .models import ParoiModel
        catalogue = dict(ParoiModel.objects.values_list('name', 'id'))
        materials = _assign_materials(triangles, info, catalogue)

    building = _save_building(_building_payload(
        env, unique_name(Building, name or f"{label} — {env.name}"), vertices, triangles,
    ))
    for obj in objs:
        obj['status'] = observed_env.STATUS_STUDIED
        obj['building_id'] = building.pk
        obj['reason'] = "Fusionné avec d'autres emprises en un seul bâtiment étudié." if len(objs) > 1 else None
    recompose(env)
    return building, env, materials


def study_object(env, obj_id, name=''):
    """Compatibilité : un seul objet, sans pré-assignation."""
    building, env, _m = study_objects(env, [obj_id], name, assign_materials=False)
    return building, env


# ── Orthophoto ─────────────────────────────────────────────────────────────────

ORTHO_CACHE_DIR = '/tmp/bilan-thermique-ortho'


def _ortho_extent(env):
    radius = (env.generation or {}).get('radius_m')
    if env.georef_lat is None or radius is None or not geodata.is_in_france(env.georef_lat, env.georef_lon):
        return None, None
    frame = observed_env.LocalFrame(env.georef_lat, env.georef_lon, env.georef_north_offset_deg or 0.0)
    return frame, frame.l93_bbox(float(radius) + observed_env.RASTER_MARGIN_M)


def ortho_descriptor(env):
    frame, bbox = _ortho_extent(env)
    if frame is None:
        return None
    xmin, ymin, xmax, ymax = bbox
    # E = e0 + s(x·cos + y·sin) ; N = n0 + s(−x·sin + y·cos) — voir LocalFrame.to_l93.
    c, sn, k = frame._cos, frame._sin, frame.scale
    w, h = xmax - xmin, ymax - ymin
    return {
        'u': [k * c / w, k * sn / w, (frame.e0 - xmin) / w],
        'v': [-k * sn / h, k * c / h, (frame.n0 - ymin) / h],
        'url': f'/api/environnements/{env.pk}/orthophoto/',
    }


def orthophoto_bytes(env):
    import hashlib
    import os
    frame, bbox = _ortho_extent(env)
    if frame is None:
        raise EnvironmentServiceError("Pas d'orthophoto pour cet environnement (non géoréférencé ou hors France).")
    key = hashlib.sha1(repr(tuple(round(b, 2) for b in bbox)).encode()).hexdigest()
    path = os.path.join(ORTHO_CACHE_DIR, f'{key}.jpg')
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            return fh.read()
    try:
        data, _w, _h = lidar_source.fetch_orthophoto(bbox, lidar_source.ORTHO_RGB_LAYER)
    except geodata.GeodataError as exc:
        raise EnvironmentServiceError(str(exc)) from exc
    os.makedirs(ORTHO_CACHE_DIR, exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data)
    return data


@transaction.atomic
def replace_object(env, obj_id, vertices, triangles, name='', up_axis='auto', scale='auto'):
    """Modèle importé (OBJ/STL) posé à la place d'un bâtiment de l'environnement :
    orientation, position, unité et axe vertical ajustés automatiquement sur
    l'emprise de l'objet (observed_env.fit_import), qui est ensuite retiré des
    obstacles."""
    env = Environment.objects.select_for_update().get(pk=env.pk)
    obj = find_object(env, obj_id)
    if obj['kind'] != 'building' or not obj.get('footprint'):
        raise EnvironmentServiceError("Seul un bâtiment peut être remplacé par un modèle importé.")
    if obj['status'] == observed_env.STATUS_STUDIED:
        raise EnvironmentServiceError("Cet objet est déjà le bâtiment étudié d'un autre bâtiment.")
    target = observed_env.rings_to_poly(obj['footprint'])
    base_z = obj.get('info', {}).get('base_z')
    if base_z is None:
        base_z = min(v[2] for v in obj['vertices'])
    try:
        fitted, kept, report = observed_env.fit_import(
            vertices, triangles, target, base_z, up_axis=up_axis,
            scale='auto' if scale == 'auto' else float(scale),
        )
    except observed_env.ObservedEnvError as exc:
        raise EnvironmentServiceError(str(exc)) from exc
    tri_payload = [
        {'v': t['v'], 'group': t.get('group'), 'paroi_model_id': None,
         'boundary': 'exterior_air', 'shading_profile_id': None}
        for t in kept
    ]
    building = _save_building(_building_payload(
        env, unique_name(Building, name or f"Modèle importé — {env.name}"), fitted, tri_payload,
    ))
    obj['status'] = observed_env.STATUS_STUDIED
    obj['building_id'] = building.pk
    obj['reason'] = "Remplacé par un modèle importé."
    recompose(env)
    return building, env, report


# ── Génération ─────────────────────────────────────────────────────────────────

def generate(params, building=None, progress_cb=None):
    """Génère et ENREGISTRE un environnement. Retourne (env, summary).

    Repère : celui du bâtiment de référence s'il y en a un (son origine, son
    cap, son altitude), sinon le point demandé, nord vrai, z = 0 au sol."""
    def report(stage, pct):
        if progress_cb:
            progress_cb(stage, pct)

    if building is not None:
        lat0, lon0 = building.georef_lat, building.georef_lon
        north = building.georef_north_offset_deg or 0.0
        ground_z = building.georef_ground_z
        self_polygon = geodata.envelope_footprint_polygon(building.envelope)
    else:
        lat0, lon0 = params['lat'], params['lon']
        north, ground_z, self_polygon = 0.0, None, None

    half = float(params['radius_m'])
    include_veg = params.get('include_vegetation', True)
    include_terrain = params.get('include_terrain', True)

    source = 'lidar'
    lidar_meta = {}
    try:
        if not geodata.is_in_france(lat0, lon0):
            raise lidar_source.LidarUnavailable("hors de France")
        frame = observed_env.LocalFrame(lat0, lon0, north, ground_z)
        report('lidar-index', 5)
        margin = half + observed_env.RASTER_MARGIN_M
        tiles = lidar_source.fetch_lidar_tiles(frame.wgs84_bbox(margin + 5))
        if not tiles:
            raise lidar_source.LidarUnavailable("zone pas encore couverte par le LiDAR HD")
        lidar_meta = lidar_source.describe_tiles(tiles)
        report('lidar-read', 10)
        points, read_stats = lidar_source.read_points(
            tiles, frame.l93_bbox(margin + 5),
            progress_cb=lambda done, total: report('lidar-read', 10 + int(20 * done / total)),
        )
        if len(points[0]) < 1000:
            raise lidar_source.LidarUnavailable("nuage vide sur cette zone")
        report('bdtopo', 32)
        warnings_pre = []
        try:
            bdtopo = lidar_source.fetch_bdtopo_buildings_l93(frame.l93_bbox(half + 10))
        except geodata.GeodataError as exc:
            bdtopo = []
            warnings_pre.append(
                f"BD TOPO indisponible ({exc}) : bâtiments reconstruits depuis le LiDAR seul, "
                "sans identifiant ni murs rectilignes."
            )
        months = sorted({int(t['acq_end'][5:7]) for t in tiles if len(t['acq_end']) >= 7}
                        | {int(t['acq_start'][5:7]) for t in tiles if len(t['acq_start']) >= 7})
        # Lot AJ : CoSIA (conifère / feuillu) pour la forme et la transparence des arbres.
        cosia = None
        if include_veg:
            report('cosia', 34)
            years = [int(t['acq_end'][:4]) for t in tiles if len(t['acq_end']) >= 4]
            cbbox = frame.l93_bbox(half + observed_env.RASTER_MARGIN_M)
            try:
                cosia = observed_env.CosiaImage(
                    lidar_source.fetch_cosia(cbbox, max(years) if years else None), cbbox)
            except (geodata.GeodataError, OSError, ValueError) as exc:
                warnings_pre.append(f"CoSIA indisponible ({exc}) : essence des arbres inconnue.")
        objects, ground_z, stats, warnings, rasters = observed_env.build_objects(
            frame, half, points, bdtopo, months, include_vegetation=include_veg,
            include_terrain=include_terrain, self_polygon=self_polygon, progress_cb=report,
            return_rasters=True, cosia=cosia,
        )
        warnings = warnings_pre + warnings
        # Lot AI : orthophotos RVB + infrarouge → albédo du sol (réflexion vers
        # les parois) et albédo de chaque toiture (absorptance pour le calcul).
        report('ortho', 90)
        try:
            bbox = frame.l93_bbox(half + observed_env.RASTER_MARGIN_M)
            rgb, _w, _h = lidar_source.fetch_orthophoto(bbox, lidar_source.ORTHO_RGB_LAYER, px_per_m=2.0)
            irc, _w, _h = lidar_source.fetch_orthophoto(bbox, lidar_source.ORTHO_IRC_LAYER, px_per_m=2.0)
            ortho = observed_env.OrthoImage(lidar_source.decode_jpeg(rgb), lidar_source.decode_jpeg(irc), bbox)
            ground_albedo = observed_env.apply_ortho(objects, ortho, frame, rasters, half)
            vals = ground_albedo['values']
            stats['ground_albedo_mean'] = round(sum(vals) / len(vals), 3)
        except (geodata.GeodataError, OSError, ValueError) as exc:
            ground_albedo = None
            warnings.append(f"Orthophoto indisponible ({exc}) : ni albédo du sol ni couleur des toits.")
        stats.update({'lidar_requests': read_stats['requests'],
                      'lidar_mb': round(read_stats['bytes'] / 1e6, 1), 'bdtopo': len(bdtopo)})
    except lidar_source.LidarUnavailable as exc:
        source = 'legacy'
        ground_albedo = None
        objects, ground_z, stats, warnings = legacy_objects(
            lat0, lon0, half, north, ground_z, include_veg, include_terrain,
            params.get('terrain_spacing_m') or 10.0, self_polygon, report,
        )
        warnings.insert(0, f"LiDAR HD indisponible ({exc}) : repli sur la BD TOPO / OpenStreetMap — "
                           "hauteurs issues des attributs, toits plats, arbres à hauteur forfaitaire.")

    if ground_z is not None:
        ground_z = round(float(ground_z), 2)

    report('save', 96)
    name = params.get('name') or f"Environnement {lat0:.5f}, {lon0:.5f} — {date.today().isoformat()}"
    env = Environment.objects.create(
        name=unique_name(Environment, name),
        georef_lat=lat0, georef_lon=lon0, georef_north_offset_deg=north, georef_ground_z=ground_z,
        scene_objects=objects, envelope=observed_env.compose_envelope(objects),
        generation={
            'source': source, 'radius_m': half, 'lidar': lidar_meta, 'stats': stats,
            'warnings': warnings, 'generated_at': date.today().isoformat(),
            'include_vegetation': include_veg, 'include_terrain': include_terrain,
            'ground_albedo': ground_albedo,
        },
    )
    if building is not None:
        for obj in env.scene_objects:
            if obj['status'] == observed_env.STATUS_STUDIED and not obj.get('building_id'):
                obj['building_id'] = building.pk
        env.save(update_fields=['scene_objects'])
        if building.georef_ground_z is None:
            # z = 0 du bâtiment = sol à son origine, désormais mesuré : on le fixe,
            # sans quoi toute génération ultérieure repartirait d'une autre valeur.
            building.georef_ground_z = ground_z
            building.save(update_fields=['georef_ground_z'])

    summary = {
        'environment_id': env.pk, 'source': source, 'stats': stats, 'warnings': warnings,
        'n_objects': len(objects), 'n_triangles': len(env.envelope['triangles']),
    }
    return env, summary


def legacy_objects(lat0, lon0, half, north, ground_z, include_veg, include_terrain,
                   terrain_spacing, self_polygon, report):
    """Repli hors couverture LiDAR : le générateur historique (geodata /
    elevation), mais découpé en objets comme le générateur observé, pour que
    retirer, restaurer ou promouvoir un objet fonctionne partout.

    Contrairement à l'ancien comportement, z = 0 est TOUJOURS fixé à
    l'altitude du sol au point d'origine quand aucune n'est imposée : sans
    ça, les bâtiments IGN arrivaient à leur altitude NGF absolue (100 m et
    plus) au-dessus d'un bâtiment posé à z = 0."""
    warnings = []
    stats = {}

    def elevation_lookup(points):
        return elevation.fetch_elevations(points)[0]

    if ground_z is None:
        try:
            ground_z, _src = elevation.ground_altitude(lat0, lon0)
        except elevation.ElevationError as exc:
            warnings.append(f"Altitude du sol indisponible ({exc}) : altitudes relatives approximatives.")

    report('legacy-buildings', 20)
    bbox = geodata.bbox_from_radius(lat0, lon0, half)
    buildings, origin = [], 'osm'
    if geodata.is_in_france(lat0, lon0):
        try:
            buildings, origin = geodata.fetch_ign_buildings(bbox), 'bdtopo'
        except geodata.GeodataError as exc:
            warnings.append(f"{exc} Repli sur OpenStreetMap.")
    if not buildings:
        buildings, origin = geodata.fetch_osm_buildings(bbox), 'osm'

    for b in buildings:
        b['point_latlon'] = geodata._footprint_center(b['footprint_latlon'])
    base_z = geodata.resolve_base_z(buildings, ground_z, elevation_lookup, warnings, label="bâtiment(s)")

    objects = []
    next_id = [1]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1

    for b, z0 in zip(buildings, base_z):
        footprint = [geodata._rotate_xy(*geodata.local_xy(p[0], p[1], lat0, lon0), north)
                     for p in b['footprint_latlon']]
        poly = geodata._as_polygon(footprint)
        if poly.is_empty or poly.area < 4.0:
            continue
        try:
            verts, tris = observed_env.prism_solid(poly, z0, z0 + max(b['height_m'], 1.0))
        except (observed_env.ObservedEnvError, ValueError):
            continue
        dist = math.hypot(poly.centroid.x, poly.centroid.y)
        objects.append(observed_env.make_object(
            new_id(), 'building', origin, 'Bâtiment', verts, tris, poly,
            {'base_z': round(z0, 2), 'height_m': round(b['height_m'], 1),
             'approx_height': b.get('approx_height'), 'distance_m': round(dist, 1), 'roof': 'plat'},
        ))

    if include_veg:
        report('legacy-vegetation', 50)
        try:
            veg = geodata.generate_vegetation_mesh(
                lat0, lon0, half, north_offset_deg=north, ground_z_ref=ground_z,
                elevation_lookup=elevation_lookup,
            )
            warnings.extend(veg['warnings'])
            by_obj = {}
            for tri in veg['triangles']:
                by_obj.setdefault(tri['obj'], []).append(tri)
            for tris in by_obj.values():
                used = sorted({i for t in tris for i in t['v']})
                remap = {old: new for new, old in enumerate(used)}
                verts = [veg['vertices'][i] for i in used]
                cx = sum(v[0] for v in verts) / len(verts)
                cy = sum(v[1] for v in verts) / len(verts)
                objects.append(observed_env.make_object(
                    new_id(), 'vegetation', 'bdtopo-osm', 'Végétation', verts,
                    [{'v': [remap[i] for i in t['v']]} for t in tris],
                    info={'distance_m': round(math.hypot(cx, cy), 1)}, k=tris[0]['k'],
                ))
        except geodata.GeodataError as exc:
            warnings.append(f"Végétation non chargée ({exc}).")

    if include_terrain:
        report('legacy-terrain', 70)
        try:
            mesh, source, n = elevation.build_terrain_for_building(
                lat0, lon0, half, terrain_spacing, north_offset_deg=north, ground_z_ref=ground_z,
            )
            objects.append(observed_env.make_object(
                new_id(), 'terrain', source, 'Terrain', mesh['vertices'], mesh['triangles'],
                info={'points': n},
            ))
        except elevation.ElevationError as exc:
            warnings.append(f"Terrain non chargé ({exc}).")

    observed_env.mark_studied(objects, self_polygon, warnings)
    objects = observed_env._apply_budget(objects, warnings)
    stats.update({'buildings': sum(o['kind'] == 'building' for o in objects),
                  'trees': sum(o['kind'] == 'vegetation' for o in objects)})
    return objects, ground_z, stats, warnings
