"""Géométrie du bâtiment étudié croisée BD TOPO × LiDAR HD (Lot 30).

La BD TOPO seule ne suffit pas à la géométrie : sa hauteur est souvent fausse de plusieurs mètres
(10 m pour 20 m mesurés sur l'hôtel de ville de Tours, 1,4 m pour 8 m à Langeais — 2026-11-16), ses
emprises sont parfois décalées de 1 à 2 m, des bâtiments y manquent. L'environnement relevé
(`observed_env`) recale déjà chaque emprise BD TOPO sur le nuage LiDAR et en reconstruit le toit ; ce
module en tire la géométrie **du bâtiment étudié** et la substitue à celle de la chaîne :

- **emprise** recalée (décalage global de la zone puis ajustement individuel borné) ;
- **hauteur à l'égout** mesurée (médiane, pondérée par la longueur, du haut des murs) et faîtage ;
- **toiture** : terrasse, deux ou quatre pans, et pente, d'après les pans relevés ;
- **voisins** (mitoyenneté, masques) : les bâtiments de l'environnement, recalés et à leur hauteur
  mesurée, plus les arbres pour l'horizon (`api.horizon`).

Faits écrits en méthode `infere`, source « LiDAR HD × BD TOPO (IGN) », avec une confiance supérieure à
celle de la BD TOPO : une correction de l'utilisateur prime toujours (`facts._better`). La géométrie BD
TOPO de la chaîne est gardée (`pipeline.bdtopo`) : détacher l'environnement la rétablit (`restore`).
L'enveloppe 3D du bâtiment n'est remplacée que si c'est encore celle que la chaîne avait posée.
Repli : sans environnement relié, sans bâtiment étudié retrouvé, ou dans un autre repère, la géométrie
BD TOPO reste, et le compte rendu dit pourquoi.
"""

import math

import numpy as np
import shapely.geometry
import shapely.ops

SOURCE = 'LiDAR HD × BD TOPO (IGN)'
# Lot 31 : sans nuage LiDAR, la même géométrie croisée vient d'un modèle numérique de surface (`api.mns_source`).
LABELS = {'lidar': SOURCE, 'mns_lidar': 'MNS LiDAR HD × BD TOPO (IGN)', 'mns_correl': 'MNS Correl × BD TOPO (IGN)'}
CONFIDENCE = {'lidar': 0.8, 'mns_lidar': 0.75, 'mns_correl': 0.6}
ALL_LABELS = tuple(LABELS.values())
SELF_IOU_MIN = 0.3
SAME_FRAME_M = 0.5
DERIVED_LEVELS = 'hauteur ÷ 3 m'
DERIVED_SURFACE = 'emprise × niveaux × 0,8'
INCOHERENT_LEVELS = 'hauteur mesurée ÷ 3 m (niveaux de la source incohérents avec la hauteur)'
STOREY_MIN_M, STOREY_MAX_M = 2.3, 5.0


class NotCrossed(Exception):
    """Croisement impossible — le message (fixe) dit pourquoi."""


def _largest(poly):
    if poly is None or poly.is_empty:
        return None
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.geom_type == 'MultiPolygon':
        poly = max(poly.geoms, key=lambda p: p.area)
    return poly if poly.geom_type == 'Polygon' else None


def _iou(a, b):
    u = a.union(b).area
    return a.intersection(b).area / u if u > 0 else 0.0


def same_frame(building, env):
    """L'environnement est-il dans le repère du bâtiment (même origine, même nord) ?"""
    from . import geodata
    if None in (env.georef_lat, env.georef_lon, building.georef_lat, building.georef_lon):
        return False
    dx, dy = geodata.local_xy(env.georef_lat, env.georef_lon, building.georef_lat, building.georef_lon)
    return (math.hypot(dx, dy) <= SAME_FRAME_M
            and abs((env.georef_north_offset_deg or 0.0) - (building.georef_north_offset_deg or 0.0)) < 0.1)


def studied(building, env=None, reference=None):
    """Objets de l'environnement qui SONT le bâtiment : marqués « étudié » pour lui (ou sans bâtiment désigné),
    sinon l'objet bâtiment dont l'emprise ressemble le plus à `reference` (indice de Jaccard > 0,3)."""
    from .observed_env import STATUS_ACTIVE, STATUS_STUDIED, rings_to_poly
    env = env or building.environment
    objs = [o for o in env.scene_objects or [] if o.get('kind') == 'building' and o.get('footprint')]
    mine = [o for o in objs if o.get('status') == STATUS_STUDIED and o.get('building_id') in (None, building.pk)]
    if mine or reference is None:
        return mine
    best = None
    for o in objs:
        if o.get('status') != STATUS_ACTIVE:
            continue
        p = _largest(rings_to_poly(o['footprint']))
        iou = _iou(p, reference) if p is not None else 0.0
        if iou > SELF_IOU_MIN and (best is None or iou > best[0]):
            best = (iou, o)
    return [best[1]] if best else []


def obstacles(env, exclude_ids=(), z0=None):
    """[(emprise, sommet au-dessus de z0)] des bâtiments et arbres actifs, et la même liste pour les seuls
    bâtiments. Les altitudes de l'environnement sont relatives au terrain à l'origine ; z0 = base du bâtiment
    étudié. Sans elle (bâtiment étudié non retrouvé, repli BD TOPO / OSM aux altitudes parfois absolues), chaque
    obstacle compte pour sa hauteur propre (sommet − base), comme avant le Lot 30."""
    from .observed_env import STATUS_ACTIVE, rings_to_poly
    obs, buildings = [], []
    for obj in env.scene_objects or []:
        # Les arbres sont des objets `vegetation` (observed_env) : jusqu'au Lot 30, l'horizon cherchait `tree`
        # et n'en voyait donc aucun.
        if obj.get('kind') not in ('building', 'vegetation') or obj.get('status', STATUS_ACTIVE) != STATUS_ACTIVE:
            continue
        if obj.get('id') in exclude_ids:
            continue
        verts = obj.get('vertices') or []
        if not verts:
            continue
        hull = _largest(rings_to_poly(obj['footprint'])) if obj.get('footprint') else None
        if hull is None:
            hull = _largest(shapely.geometry.MultiPoint([(v[0], v[1]) for v in verts]).convex_hull)
        if hull is None or hull.area < 0.5:
            continue
        top = max(v[2] for v in verts) - (z0 if z0 is not None else min(v[2] for v in verts))
        if top <= 0:
            continue
        item = (hull, top)
        obs.append(item)
        if obj['kind'] == 'building':
            buildings.append(item)
    return obs, buildings


def _tri_geometry(vertices, tri):
    a, b, c = (np.asarray(vertices[i], dtype=float) for i in tri['v'])
    n = np.cross(b - a, c - a)
    area = 0.5 * float(np.linalg.norm(n))
    if area < 1e-9:
        return 0.0, 0.0, 0.0
    n = n / (2 * area)
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, abs(n[2])))))
    az = math.degrees(math.atan2(n[0], n[1])) % 360
    return area, tilt, az


def _weighted_median(values, weights):
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    c = np.cumsum(w)
    return float(v[np.searchsorted(c, c[-1] / 2.0)])


def measure(objs, north_deg=0.0):
    """Mesures d'un bâtiment (un ou plusieurs objets de l'environnement) : emprise, base, égout, faîtage,
    toiture. `mesure` est faux si le toit n'a pas été relevé (prisme à la hauteur BD TOPO)."""
    from .observed_env import rings_to_poly
    polys = [_largest(rings_to_poly(o['footprint'])) for o in objs]
    polys = [p for p in polys if p is not None]
    if not polys:
        raise NotCrossed('Emprise du bâtiment étudié absente de l’environnement.')
    poly = _largest(shapely.ops.unary_union([p.buffer(0.3, join_style=2) for p in polys]).buffer(-0.3, join_style=2))
    base = min(float((o.get('info') or {}).get('base_z', 0.0) or 0.0) for o in objs)
    measured = any(str((o.get('info') or {}).get('roof', '')).startswith('relevé') for o in objs)
    tops, lens, ridge = [], [], base
    roof = []                                                    # (aire, pente, azimut)
    for o in objs:
        V = o.get('vertices') or []
        for t in o.get('triangles') or []:
            g = t.get('group') or ''
            zs = [V[i][2] for i in t['v']]
            ridge = max(ridge, *zs)
            if g.startswith('mur'):
                up = [V[i] for i in t['v'] if V[i][2] > base + 0.3]
                if len(up) == 2:                                 # triangle qui porte le haut du mur
                    length = math.hypot(up[0][0] - up[1][0], up[0][1] - up[1][1])
                    if length > 0.05:
                        tops.append((up[0][2] + up[1][2]) / 2.0 - base)
                        lens.append(length)
            elif g.startswith('toiture'):
                roof.append(_tri_geometry(V, t))
    eave = _weighted_median(tops, lens) if tops else None
    out = {'poly': poly, 'base_z': round(base, 2), 'mesure': measured and eave is not None,
           'egout_m': round(eave, 2) if eave is not None else None, 'faitage_m': round(ridge - base, 2),
           'toiture': None, 'pente_deg': None}
    total = sum(a for a, _t, _z in roof)
    if measured and total > 0:
        pitched = [(a, t, z) for a, t, z in roof if t >= 10.0]
        p_area = sum(a for a, _t, _z in pitched)
        if p_area < 0.4 * total:
            out['toiture'], out['pente_deg'] = 'terrasse', 0.0
        else:
            out['pente_deg'] = round(min(70.0, _weighted_median([t for _a, t, _z in pitched], [a for a, _t, _z in pitched])), 1)
            sectors = {}
            for a, _t, z in pitched:
                s = int(((z + north_deg) % 360 + 45) // 90) % 4
                sectors[s] = sectors.get(s, 0.0) + a
            n_main = sum(1 for v in sectors.values() if v >= 0.15 * p_area)
            out['toiture'] = '4pans' if n_main >= 3 else '2pans'
    return out


def cross(building, bdtopo_poly=None, bdtopo_height=None):
    """Géométrie croisée du bâtiment (rien n'est écrit). Lève NotCrossed (message fixe) si impossible.

    bdtopo_poly / bdtopo_height : emprise et hauteur de la chaîne (pour retrouver le bâtiment dans
    l'environnement s'il n'y est pas désigné, et pour dire l'écart)."""
    from .pipeline import geometry_from_footprints
    env = building.environment
    if env is None:
        raise NotCrossed('Aucun environnement relevé relié.')
    if not same_frame(building, env):
        raise NotCrossed('L’environnement relié n’est pas dans le repère du bâtiment.')
    if bdtopo_poly is None:
        fp = (building.pipeline or {}).get('footprint_xy')
        bdtopo_poly = _largest(shapely.geometry.Polygon(fp)) if fp and len(fp) >= 3 else None
    objs = studied(building, env, bdtopo_poly)
    if not objs:
        raise NotCrossed('Bâtiment étudié non retrouvé dans l’environnement relevé.')
    kind = (env.generation or {}).get('source', 'lidar')
    if kind not in LABELS:
        raise NotCrossed('Environnement sans LiDAR ni MNS (repli BD TOPO / OpenStreetMap) : géométrie BD TOPO gardée.')
    label, conf = LABELS[kind], CONFIDENCE[kind]
    north = building.georef_north_offset_deg or 0.0
    m = measure(objs, north)
    poly = m['poly']
    if not m['mesure'] and bdtopo_height is None:
        raise NotCrossed('Toit du bâtiment non relevé par le LiDAR.')
    eave = m['egout_m'] if m['mesure'] and m['egout_m'] and m['egout_m'] >= 2.0 else None
    height = eave or float(bdtopo_height or 6.0)
    ids = {o['id'] for o in objs}
    obs, buildings = obstacles(env, ids, m['base_z'])
    ids_b = {id(b) for b in buildings}
    trees = [t for t in obs if id(t) not in ids_b]
    g = geometry_from_footprints(poly, buildings, height, obstacles=trees, north_deg=north)
    footprint_xy = [[round(x, 2), round(y, 2)] for x, y in
                    list(shapely.geometry.polygon.orient(poly, sign=1.0).simplify(0.2).exterior.coords)[:-1]]
    g['footprint_xy'] = footprint_xy
    neighbours = [{'xy': [[round(x, 2), round(y, 2)] for x, y in list(q.simplify(0.3).exterior.coords)[:-1]],
                   'h': round(h, 1)} for q, h in sorted(buildings, key=lambda t: t[0].distance(poly))[:40]]
    info = [o.get('info') or {} for o in objs]
    shifts = [s for s in (i.get('shift_m') for i in info) if s]
    covers = [c for c in (i.get('lidar_cover') for i in info) if c is not None]
    report = {
        'source': kind if eave else f'{kind} (emprise seule)', 'libelle': label, 'environment_id': env.pk,
        'objets': sorted(ids), 'emprise_m2': round(poly.area, 1),
        'decalage_m': round(max(math.hypot(*s) for s in shifts), 2) if shifts else None,
        'couverture_lidar': round(min(covers), 3) if covers else None,
        'hauteur_egout_m': eave, 'hauteur_faitage_m': m['faitage_m'] if m['mesure'] else None,
        'toiture': m['toiture'], 'pente_deg': m['pente_deg'],
        'bdtopo': {'hauteur_m': round(float(bdtopo_height), 1) if bdtopo_height is not None else None,
                   'emprise_m2': round(bdtopo_poly.area, 1) if bdtopo_poly is not None else None,
                   'recouvrement': round(_iou(poly, bdtopo_poly), 2) if bdtopo_poly is not None else None},
    }
    if not eave:
        report['note'] = ('Toit du bâtiment non vu par le relevé : emprise recalée, mais hauteur de la BD TOPO '
                          'gardée — à vérifier.')
    rows = [('identite.emprise_m2', round(poly.area, 1), 'infere', conf, label),
            ('identite.mitoyennete', min(0.9, g['mitoyennete']), 'infere', conf - 0.05, f'{label}, voisins accolés')]
    if eave:
        rows.append(('identite.hauteur_m', round(eave, 1), 'infere', conf, f'{label}, hauteur à l’égout mesurée'))
    if m['toiture']:
        rows.append(('toiture.type', m['toiture'], 'infere', conf - 0.2, f'{label}, pans relevés'))
        rows.append(('toiture.pente_deg', m['pente_deg'], 'infere', conf - 0.25, f'{label}, pans relevés'))
    return {'geometry': g, 'footprint_xy': footprint_xy, 'neighbours': neighbours, 'rows': rows,
            'report': report, 'objects': objs, 'height': height}


def _envelope_hash(envelope):
    from .fingerprint import stable_hash
    tris = (envelope or {}).get('triangles') or []
    if not tris:
        return None
    # Assignations comprises : une enveloppe dont une paroi a été choisie à la main n'est plus « automatique ».
    return stable_hash([(t.get('v'), t.get('group'), t.get('paroi_model_id'), t.get('boundary'), t.get('shading_profile_id'))
                        for t in tris], (envelope or {}).get('vertices') or [])[:16]


def _lidar_envelope(building, objs):
    """Enveloppe 3D (Building.envelope) depuis l'objet relevé, murs mitoyens nommés ; None si impossible."""
    from . import geometry, observed_env
    env = building.environment
    try:
        if len(objs) == 1:
            V, T = observed_env.building_envelope_from_object(objs[0], env.scene_objects)
        else:
            mv, mt, _p, _i = observed_env.merge_objects(objs)
            V, T = observed_env.building_envelope_from_mesh(mv, mt, {o['id'] for o in objs}, env.scene_objects)
        return {'vertices': V, 'triangles': geometry.compute_envelope_geometry(V, T)}
    except Exception:  # noqa: BLE001 — l'enveloppe 3D n'est qu'un complément ; les calculs n'en dépendent pas
        return None


def _rederive(building):
    """Niveaux et surface que la chaîne avait déduits de la hauteur et de l'emprise (A5) : recalculés."""
    from . import facts, pipeline
    eff = facts.effective(building)
    h, emp = eff.get('identite.hauteur_m'), eff.get('identite.emprise_m2')
    niv, surf = eff.get('identite.niveaux'), eff.get('identite.surface_m2')
    rows = []
    redo_surface = surf is not None and surf.source == DERIVED_SURFACE and not surf.author_sub
    levels = niv.value if niv is not None else None
    if niv is not None and niv.source == DERIVED_LEVELS and not niv.author_sub and h is not None:
        levels = max(1, round(float(h.value) / 3.0))
        rows.append(('identite.niveaux', int(levels), 'infere', 0.35, DERIVED_LEVELS))
    elif (niv is not None and not niv.author_sub and niv.method in ('infere', 'defaut') and h is not None
          and str(h.source).startswith(ALL_LABELS) and levels):
        # Niveaux d'une source automatique (BDNB) incohérents avec la hauteur mesurée : 1 niveau pour 14,8 m
        # d'égout (hôtel de ville de Tours, 2026-11-16) donnait 720 m² chauffés pour 900 m² d'emprise.
        storey = float(h.value) / float(levels)
        if not (STOREY_MIN_M <= storey <= STOREY_MAX_M):
            levels = max(1, round(float(h.value) / 3.0))
            rows.append(('identite.niveaux', int(levels), 'infere', 0.4, INCOHERENT_LEVELS))
            redo_surface = surf is not None and not surf.author_sub and surf.method in ('infere', 'defaut')
    if redo_surface and emp is not None and levels:
        rows.append(('identite.surface_m2', round(float(emp.value) * int(levels) * 0.8, 1), 'infere', 0.35, DERIVED_SURFACE))
    pipeline._record_all(building, rows)


def _write(building, pipe, rows, envelope_from):
    from . import facts, pipeline
    building.pipeline = pipe
    fields = ['pipeline']
    if envelope_from is not None:
        new_env = envelope_from()
        if new_env is not None:
            building.envelope = new_env
            building.sun_visibility_stale = True
            pipe['envelope_auto'] = _envelope_hash(new_env)
            fields += ['envelope', 'sun_visibility_stale']
    building.save(update_fields=fields)
    pipeline._record_all(building, rows)
    _rederive(building)
    facts.refresh_scores(building)


def _envelope_is_auto(building, pipe):
    current = _envelope_hash(building.envelope)
    return current is None or current == pipe.get('envelope_auto')


def apply(building):
    """Croise la géométrie du bâtiment avec son environnement relié et l'écrit (pipeline, faits, enveloppe
    3D si elle est encore automatique). Rend le compte rendu `{'ok': True, …}` ou `{'ok': False, 'error'}` ;
    ne lève jamais."""
    from . import net
    pipe = dict(building.pipeline or {})
    bd = pipe.get('bdtopo') or {}
    if not bd and (pipe.get('geometrie') or {}).get('source', 'bdtopo') == 'bdtopo' and pipe.get('geometry'):
        bd = {k: pipe.get(k) for k in ('geometry', 'footprint_xy', 'neighbours', 'horizon_deg')}
    bd_fp = bd.get('footprint_xy') or pipe.get('footprint_xy')
    bd_poly = _largest(shapely.geometry.Polygon(bd_fp)) if bd_fp and len(bd_fp) >= 3 else None
    bd_h = (bd.get('geometry') or {}).get('hauteur_m')
    try:
        c = cross(building, bd_poly, bd_h)
    except NotCrossed as exc:
        return {'ok': False, 'error': str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': net.reason(exc)}
    if bd:
        pipe['bdtopo'] = bd
    auto_env = _envelope_is_auto(building, pipe)
    pipe.update(geometry=c['geometry'], footprint_xy=c['footprint_xy'], neighbours=c['neighbours'],
                horizon_deg=c['geometry']['horizon_deg'], geometrie=c['report'])
    _write(building, pipe, c['rows'], (lambda: _lidar_envelope(building, c['objects'])) if auto_env else None)
    return {'ok': True, **{k: c['report'][k] for k in ('source', 'hauteur_egout_m', 'decalage_m', 'toiture')}}


def restore(building):
    """Environnement détaché : la géométrie BD TOPO gardée par la chaîne revient (faits, voisins, enveloppe
    3D si c'était celle du croisement). Sans géométrie BD TOPO gardée, rien ne change."""
    from .pipeline import envelope_from_polygon
    pipe = dict(building.pipeline or {})
    bd = pipe.get('bdtopo')
    if not bd or (pipe.get('geometrie') or {}).get('source', 'bdtopo') == 'bdtopo':
        return False
    from django.utils import timezone
    from .models import BuildingFact
    from django.db.models import Q
    crossed = Q()
    for lab in ALL_LABELS:
        crossed |= Q(source__startswith=lab)
    BuildingFact.objects.filter(crossed, building=building, author_sub='', retired_at__isnull=True,
                                superseded_by__isnull=True).update(retired_at=timezone.now())
    g = bd.get('geometry') or {}
    pipe.update(geometry=g, footprint_xy=bd.get('footprint_xy'), neighbours=bd.get('neighbours') or [],
                horizon_deg=bd.get('horizon_deg') or g.get('horizon_deg'),
                geometrie={'source': 'bdtopo', 'note': 'Environnement relevé détaché : géométrie BD TOPO.'})
    rows = []
    if g.get('emprise_m2'):
        rows.append(('identite.emprise_m2', g['emprise_m2'], 'infere', 0.65, 'BD TOPO (IGN)'))
    if g.get('hauteur_m'):
        rows.append(('identite.hauteur_m', round(g['hauteur_m'], 1), 'infere', 0.65, 'BD TOPO (IGN)'))
    if 'mitoyennete' in g:
        rows.append(('identite.mitoyennete', min(0.9, g['mitoyennete']), 'infere', 0.6, 'BD TOPO (IGN), voisins accolés'))
    lod1 = None
    fp = bd.get('footprint_xy')
    if _envelope_is_auto(building, pipe) and fp and len(fp) >= 3 and g.get('hauteur_m'):
        lod1 = lambda: envelope_from_polygon(shapely.geometry.Polygon(fp), g['hauteur_m'])  # noqa: E731
    _write(building, pipe, rows, lod1)
    return True
