"""Chaîne automatique d'un bâtiment (Lot 9, refonte — § 4.2, étapes A1 à A6).

A1 identification (géocodage BAN → RNB `closest`), A2 fiche BDNB (groupe + DPE représentatif),
A3 géométrie (BD TOPO : emprise, hauteur, mitoyenneté, enveloppe LOD1), A4/A5 baies et
compositions (DPE sinon archétype), A6 horizon par façade (voisins BD TOPO). A7 à A9 (météo,
calcul N1, indicateurs) sont au Lot 10.

Règles :
- une étape en erreur n'arrête pas la chaîne : le bâtiment passe « incomplet » avec la raison
  (message fixe), et se rejoue seul ;
- toute valeur entre dans la fiche comme un **fait** avec sa provenance ; une réimportation ne
  touche jamais une surcharge de l'utilisateur (`facts.record`).
"""

import math

import shapely.geometry
import shapely.ops

from . import archetypes, facts, geodata, geometry, net, sources
from .bdnb import cardinal_of

# ── Correspondances BDNB / DPE → clés de la fiche ──────────────────────────

USAGE_FROM_BDNB = {
    'résidentiel individuel': 'maison', 'residentiel individuel': 'maison',
    'résidentiel collectif': 'logement_collectif', 'residentiel collectif': 'logement_collectif',
    'tertiaire & autres': 'bureau', 'tertiaire': 'bureau',
}


def map_usage(groupe):
    raw = (groupe.get('usage_niveau_1_txt') or groupe.get('usage_principal_bdnb_open') or '').strip().lower()
    for k, v in USAGE_FROM_BDNB.items():
        if raw.startswith(k):
            return v
    if raw.startswith('résidentiel') or raw.startswith('residentiel'):
        return 'maison' if (groupe.get('nb_log') or 0) <= 2 else 'logement_collectif'
    return 'autre' if raw else None


def map_wall_material(txt):
    t = (txt or '').lower()
    if not t or 'indetermin' in t:
        return None
    for needle, key in (('pierre', 'pierre'), ('meuli', 'pierre'), ('brique', 'brique_pleine'),
                        ('cellulaire', 'monomur'), ('bloc', 'parpaing'), ('creux', 'parpaing'), ('aggl', 'parpaing'),
                        ('parpaing', 'parpaing'), ('beton', 'beton'), ('béton', 'beton'), ('bois', 'bois')):
        if needle in t:
            return key
    return 'autre'


def map_glazing(txt):
    t = (txt or '').lower()
    if 'triple' in t:
        return 'triple'
    if 'double' in t:
        return 'double_fe' if ('faible' in t or 'peu emissif' in t or 'argon' in t) else 'double'
    if 'simple' in t:
        return 'simple'
    return None


def map_ventilation(txt):
    t = (txt or '').lower()
    if not t:
        return None
    if 'double flux' in t:
        return 'vmc_df'
    if 'hygro' in t:
        return 'vmc_sf_hygro'
    if 'mécanique' in t or 'mecanique' in t or 'vmc' in t:
        return 'vmc_sf_auto'
    if 'naturelle' in t or 'ouverture' in t or 'conduit' in t:
        return 'naturelle'
    return None


def map_generator(gen_txt, energy_txt):
    g, e = (gen_txt or '').lower(), (energy_txt or '').lower()
    if 'pac' in g or 'pompe' in g:
        return 'pac_air_air' if 'air/air' in g else 'pac_air_eau'
    if 'joule' in g or 'convecteur' in g or 'radiateur electrique' in g:
        return 'joule'
    if 'reseau' in g or 'réseau' in g or 'reseau' in e:
        return 'reseau_chaleur'
    if 'bois' in g or 'bois' in e or 'granul' in g:
        return 'bois'
    if 'fioul' in e or 'fioul' in g:
        return 'chaudiere_fioul'
    if 'condensation' in g:
        return 'chaudiere_gaz_cond'
    if 'gaz' in e or 'chaudiere' in g:
        return 'chaudiere_gaz'
    if 'electri' in e:
        return 'joule'
    return None


def map_insulation(txt):
    t = (txt or '').upper()
    if not t:
        return None
    if 'ITE' in t and 'ITI' in t:
        return 'iti'
    if 'ITE' in t:
        return 'ite'
    if 'ITI' in t or 'ITR' in t:
        return 'iti'
    if 'NON ISOL' in t:
        return 'aucune'
    return 'inconnue'


def map_inertia(txt):
    t = (txt or '').lower()
    if 'lourde' in t:
        return 'lourde'
    if 'moyenne' in t:
        return 'moyenne'
    if 'légère' in t or 'legere' in t or 'légere' in t:
        return 'legere'
    return None


def map_ecs(gen_txt):
    t = (gen_txt or '').lower()
    if 'thermodynamique' in t:
        return 'thermodynamique'
    if 'solaire' in t:
        return 'solaire'
    if 'ballon' in t or 'electri' in t:
        return 'ballon_elec'
    if 'instantan' in t:
        return 'instantane_gaz'
    if 'chaudiere' in t or 'chaudière' in t:
        return 'chaudiere'
    return None


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def bdnb_facts(data):
    """[(clé, valeur, méthode, confiance, source)] tirés de la BDNB. Pur : testable sans réseau."""
    out = []
    groupes, dpes = data.get('groupes') or [], data.get('dpe') or []
    if groupes:
        g = max(groupes, key=lambda x: _num(x.get('s_geom_groupe')) or 0)
        src = f"BDNB {g.get('batiment_groupe_id', '')}"
        y = _num(g.get('annee_construction'))
        if y and 1500 <= y <= 2030:
            out.append(('identite.annee_construction', int(y), 'infere', 0.6, src))
        u = map_usage(g)
        if u:
            out.append(('identite.usage', u, 'infere', 0.6, src))
        for key, field, lo, hi in (('identite.logements', 'nb_log', 0, 5000), ('identite.niveaux', 'nb_niveau', 1, 80)):
            v = _num(g.get(field))
            if v is not None and lo <= v <= hi:
                out.append((key, int(round(v)), 'infere', 0.55, src))
        h = _num(g.get('hauteur_mean'))
        if h and 2 <= h <= 300:
            out.append(('identite.hauteur_m', round(h, 1), 'infere', 0.55, src))
        e = _num(g.get('s_geom_groupe') or g.get('surface_emprise_sol'))
        if e and 5 <= e <= 100000:
            out.append(('identite.emprise_m2', round(e, 1), 'infere', 0.55, src))
        m = map_wall_material(g.get('mat_mur_txt'))
        if m:
            out.append(('mur.materiau', m, 'infere', 0.5, src))
        if 'raccord' in (g.get('indicateur_distance_au_reseau') or '').lower():
            out.append(('systeme.generateur', 'reseau_chaleur', 'infere', 0.45, src))
    if dpes:
        d = max(dpes, key=lambda x: x.get('date_etablissement_dpe') or x.get('date_reception_dpe') or '')
        house = (d.get('type_batiment_dpe') or '').lower() == 'maison' and len(dpes) == 1
        # DPE d'une maison = le bâtiment ; DPE « représentatif » d'un immeuble = UN logement.
        meth, conf = ('document', 0.75) if house else ('infere', 0.5)
        src = f"DPE {d.get('identifiant_dpe', '')} (BDNB)"
        for key, field, lo, hi in (('mur.u', 'u_mur_exterieur', 0.08, 4), ('toiture.u', 'u_plancher_haut_deperditif', 0.06, 5),
                                   ('plancher.u', 'u_plancher_bas_final_deperditif', 0.08, 4),
                                   ('baies.g', 'facteur_solaire_baie_vitree', 0.05, 0.9)):
            v = _num(d.get(field))
            if v is not None and lo <= v <= hi:
                out.append((key, round(v, 3), meth, conf, src))
        uw = _num(d.get('uw')) or _num(d.get('u_baie_vitree'))
        if uw and 0.5 <= uw <= 6.5:
            out.append(('baies.uw', round(uw, 2), meth, conf, src))
        for key, val in (('baies.vitrage', map_glazing(d.get('type_vitrage'))),
                         ('enveloppe.inertie', map_inertia(d.get('classe_inertie'))),
                         ('mur.isolation', map_insulation(d.get('type_isolation_mur_exterieur'))),
                         ('ventilation.type', map_ventilation(d.get('type_ventilation'))),
                         ('systeme.generateur', map_generator(d.get('type_generateur_chauffage'), d.get('type_energie_chauffage'))),
                         ('systeme.ecs', map_ecs(d.get('type_generateur_ecs'))),
                         ('mur.materiau', map_wall_material(d.get('materiaux_structure_mur_exterieur')))):
            if val:
                out.append((key, val, meth, conf - 0.05, src))
        fermeture = (d.get('type_fermeture') or '').lower()
        if fermeture:
            out.append(('baies.occultations', 'sans' not in fermeture and 'aucun' not in fermeture, meth, conf - 0.1, src))
        if house:
            s = _num(d.get('surface_habitable_logement'))
            if s and 5 <= s <= 5000:
                out.append(('identite.surface_m2', round(s, 1), 'document', 0.75, src))
        label = d.get('classe_bilan_dpe') or d.get('classe_conso_energie_arrete_2012')
        if label in tuple('ABCDEFG'):
            out.append(('dpe.etiquette', label, 'document', 0.8, src))
        ep = _num(d.get('conso_5_usages_ep_m2')) or _num(d.get('conso_3_usages_ep_m2_arrete_2012'))
        if ep and 0 < ep < 1500:
            out.append(('dpe.conso_ep_kwh_m2', round(ep, 1), 'document', 0.8, src))
        # Taux de vitrage : surfaces par orientation (maison), sinon la proportion globale.
        pct = _num(d.get('pourcentage_surface_baie_vitree_exterieur'))
        surf = {o: _num(d.get(f)) for o, f in (('N', 'surface_vitree_nord'), ('E', 'surface_vitree_est'),
                                               ('S', 'surface_vitree_sud'), ('O', 'surface_vitree_ouest'))}
        wall = _num(d.get('surface_mur_deperditif')) or _num(d.get('surface_mur_totale'))
        if house and wall and any(v is not None for v in surf.values()):
            glazed = sum(v or 0 for v in surf.values())
            per_face = (wall + glazed) / 4.0
            for o, v in surf.items():
                out.append((f'baies.ratio_{o}', round(min(0.6, (v or 0) / per_face), 3), 'infere', 0.55, src))
        elif pct is not None and 0 < pct < 0.9:
            for o in 'NESO':
                out.append((f'baies.ratio_{o}', round(pct, 3), 'infere', 0.45, src))
    return out


# ── A3 / A6 : géométrie, mitoyenneté, horizon ──────────────────────────────

def _polygon_xy(ring_latlon, lat0, lon0):
    return shapely.geometry.Polygon([geodata.local_xy(la, lo, lat0, lon0) for la, lo in ring_latlon])


def geometry_from_footprints(target, neighbours, height_m, obstacles=(), north_deg=0.0):
    """Géométrie simplifiée d'un bâtiment, dans un repère métrique local (x est, y nord).

    target : Polygon ; neighbours : [(Polygon, hauteur)] — bâtiments voisins (mitoyenneté, masques) ;
    height_m : hauteur à l'égout ; obstacles : [(Polygon, sommet)] en plus (arbres) ; north_deg : rotation
    du repère (0 pour la chaîne). Rend : emprise, périmètre, surfaces de murs exposés par orientation
    (N/E/S/O), part mitoyenne, horizon par orientation (`horizon_deg`, degrés) et son profil par
    tranche d'azimut (`horizon_profil`, Lot 30 — `api.horizon`)."""
    poly = target if target.is_valid else target.buffer(0)
    if poly.geom_type == 'MultiPolygon':
        poly = max(poly.geoms, key=lambda p: p.area)
    ring = shapely.geometry.polygon.orient(poly, sign=1.0).exterior  # sens trigonométrique
    coords = list(ring.coords)
    nb_union = shapely.ops.unary_union([p.buffer(0.6) for p, _h in neighbours]) if neighbours else None
    walls = {o: 0.0 for o in 'NESO'}
    shared_len = perim = 0.0
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.05:
            continue
        perim += length
        nx, ny = dy / length, -dx / length  # normale sortante (anneau trigonométrique)
        az = math.degrees(math.atan2(nx, ny)) % 360
        shared = 0.0
        if nb_union is not None and length > 1.6:
            # Segment raccourci de 0,7 m à chaque bout : sans cela, le tampon de 0,6 m autour d'un
            # voisin accolé « mord » sur les murs perpendiculaires aux angles.
            seg = shapely.ops.substring(shapely.geometry.LineString([(x1, y1), (x2, y2)]), 0.7, length - 0.7)
            shared = min(length, seg.intersection(nb_union).length * length / (length - 1.4))
        shared_len += shared
        walls[cardinal_of((az + north_deg) % 360)] += (length - shared) * height_m
    from . import horizon as hz
    horizon, profil = hz.profile(poly, list(neighbours) + list(obstacles), height_m,
                                 adjacency=[p for p, _h in neighbours], north_deg=north_deg)
    return {
        'emprise_m2': round(poly.area, 1), 'perimetre_m': round(perim, 1), 'hauteur_m': round(height_m, 2),
        'murs_exposes_m2': {o: round(v, 1) for o, v in walls.items()},
        'mitoyennete': round(shared_len / perim, 3) if perim else 0.0,
        'horizon_deg': horizon, 'horizon_profil': profil,
    }


DEFAULT_RADIUS_M = 60


def fetch_footprints(lat, lon, radius_m=DEFAULT_RADIUS_M):
    """(polygone visé, voisins, hauteur, source) autour d'un point — BD TOPO, en cache."""
    bbox = geodata.bbox_from_radius(lat, lon, radius_m)
    items = sources.cached('bdtopo', f'{lat:.5f},{lon:.5f},{radius_m}', lambda: geodata.fetch_ign_buildings(bbox))
    pt = shapely.geometry.Point(0.0, 0.0)
    polys = []
    for it in items or []:
        try:
            p = _polygon_xy(it['footprint_latlon'], lat, lon)
            if not p.is_valid:
                p = p.buffer(0)
            if p.area > 1.0:
                polys.append((p, float(it.get('height_m') or geodata.DEFAULT_HEIGHT_M), bool(it.get('approx_height'))))
        except (ValueError, TypeError, KeyError):
            continue
    if not polys:
        return None
    target = min(polys, key=lambda t: (0 if t[0].contains(pt) else 1, t[0].distance(pt)))
    if target[0].distance(pt) > 15:
        return None
    neighbours = [(p, h) for p, h, _a in polys if p is not target[0]]
    return target[0], neighbours, target[1], target[2]


def envelope_from_polygon(poly, height_m):
    """Enveloppe LOD1 (murs groupés, toiture, sol) pour le N2, repère local du bâtiment."""
    p = poly if poly.geom_type == 'Polygon' else max(poly.geoms, key=lambda q: q.area)
    xy = list(shapely.geometry.polygon.orient(p.simplify(0.3), sign=1.0).exterior.coords)[:-1]
    if len(xy) > geodata.MAX_WALLS_SIMPLIFIED_MODE:
        return None
    vertices, triangles = geodata.extrude_footprint_grouped(xy, height_m)
    return {'vertices': vertices, 'triangles': geometry.compute_envelope_geometry(vertices, triangles)}


# ── Chaîne complète ────────────────────────────────────────────────────────

def _record_all(building, rows, refresh=False):
    for key, value, method, conf, src in rows:
        try:
            facts.record(building, key, value, method, source=src, confidence=conf, refresh=False)
        except facts.FactError:
            continue  # valeur hors plage dans la source : ignorée (l'archétype reste)


SKIPPED = 'Étape désactivée dans les réglages du parc.'


def initialize(building, progress=None):
    """Joue A1 → A6 sur un bâtiment (adresse, rnb_id ou position déjà posés). Rend le compte rendu.

    Bâtiment de parc : les réglages « orchestration » (BDNB, BD TOPO, rayon des voisins, RNB) et
    « parametrage » (archétypes, ventilation, rendements) du parc s'appliquent (`api.reglages`) ;
    une étape désactivée est notée `{'ok': True, 'skipped': True}`. Hors parc : inchangé."""
    from . import reglages
    from django.utils import timezone

    orch = reglages.for_building(building, 'orchestration')
    param = reglages.for_building(building, 'parametrage') or None

    def step(name, msg, pct):
        if progress:
            progress(msg, pct)

    report = {'steps': {}, 'sources': [], 'version': 1, 'date': timezone.now().isoformat()}

    def skipped(name):
        report['steps'][name] = {'ok': True, 'skipped': True, 'message': SKIPPED}

    def ok(name, **extra):
        report['steps'][name] = {'ok': True, **extra}

    def fail(name, exc):
        report['steps'][name] = {'ok': False, 'error': net.reason(exc) if not isinstance(exc, str) else exc}

    lat, lon = building.georef_lat, building.georef_lon
    # A1 — identification
    step('A1', 'Identification (BAN, RNB)…', 5)
    try:
        if lat is None and building.address:
            g = sources.geocode(building.address)
            lat, lon = g['lat'], g['lon']
            building.address = g['label'][:300]
            report['geocode_score'] = g.get('score')
        if lat is None and building.rnb_id:
            sh = sources.rnb_shape(building.rnb_id)
            lon, lat = ((sh or {}).get('point') or {}).get('coordinates', [None, None])[:2]
        if lat is None:
            raise sources.SourceError('Ni adresse, ni identifiant RNB, ni position.')
        if not building.rnb_id and orch.get('identification.rnb_auto', True):
            r = sources.rnb_closest(lat, lon)
            building.rnb_id = r['rnb_id']
            lat, lon = r['lat'], r['lon']
            if not building.address and r.get('addresses'):
                building.address = r['addresses'][0][:300]
        building.georef_lat, building.georef_lon = lat, lon
        ok('A1', rnb_id=building.rnb_id)
    except Exception as exc:  # noqa: BLE001 — une étape ne casse pas la chaîne
        fail('A1', exc)

    # A2 — BDNB
    step('A2', 'Fiche BDNB (année, usage, DPE)…', 25)
    bdnb_rows = []
    if not orch.get('bdnb.actif', True):
        skipped('A2')
    elif building.rnb_id:
        try:
            data = sources.bdnb_for_rnb(building.rnb_id)
            bdnb_rows = bdnb_facts(data)
            ok('A2', n_groupes=len(data.get('groupes') or []), n_dpe=len(data.get('dpe') or []), n_faits=len(bdnb_rows))
            report['sources'].append('BDNB (CSTB, Licence Ouverte 2.0)')
        except Exception as exc:  # noqa: BLE001
            fail('A2', exc)
    else:
        fail('A2', 'Pas d’identifiant RNB.')

    # A3 / A6 — géométrie, mitoyenneté, horizon
    step('A3', 'Géométrie (BD TOPO), mitoyenneté, masques…', 55)
    geo_rows = []
    if not orch.get('bdtopo.actif', True):
        skipped('A3')
    elif lat is not None:
        try:
            fp = fetch_footprints(lat, lon, orch.get('bdtopo.rayon_voisins_m', DEFAULT_RADIUS_M))
            if fp is None:
                raise sources.SourceError('Emprise BD TOPO introuvable à cette position.')
            poly, neighbours, h_bdtopo, approx = fp
            bdnb_h = next((v for k, v, *_ in bdnb_rows if k == 'identite.hauteur_m'), None)
            height = h_bdtopo if not approx else (bdnb_h or h_bdtopo)
            g = geometry_from_footprints(poly, neighbours, height)
            report['geometry'] = g
            # Lot 15 : emprise et voisins gardés (repère local, m) pour recalculer après une édition.
            report['footprint_xy'] = [[round(x, 2), round(y, 2)] for x, y in
                                      list(shapely.geometry.polygon.orient(poly if poly.geom_type == 'Polygon' else
                                           max(poly.geoms, key=lambda q: q.area), sign=1.0).simplify(0.2).exterior.coords)[:-1]]
            report['neighbours'] = [{'xy': [[round(x, 2), round(y, 2)] for x, y in list(q.simplify(0.3).exterior.coords)[:-1]],
                                     'h': round(h, 1)} for q, h in sorted(neighbours, key=lambda t: t[0].distance(poly))[:40]
                                    if q.geom_type == 'Polygon']
            report['horizon_deg'] = g['horizon_deg']
            geo_rows += [('identite.emprise_m2', g['emprise_m2'], 'infere', 0.65, 'BD TOPO (IGN)'),
                         ('identite.hauteur_m', round(height, 1), 'infere', 0.4 if approx else 0.65, 'BD TOPO (IGN)'),
                         ('identite.mitoyennete', min(0.9, g['mitoyennete']), 'infere', 0.6, 'BD TOPO (IGN), voisins accolés')]
            from . import croisement
            prev_auto = (building.pipeline or {}).get('envelope_auto')
            auto_env = croisement._envelope_is_auto(building, building.pipeline or {})
            env = envelope_from_polygon(poly, height)
            if env is not None and (not building.envelope.get('triangles') or auto_env):
                building.envelope = env
                building.sun_visibility_stale = True
                report['envelope_auto'] = croisement._envelope_hash(env)
            elif prev_auto:
                report['envelope_auto'] = prev_auto
            report['bdtopo'] = {k: report.get(k) for k in ('geometry', 'footprint_xy', 'neighbours', 'horizon_deg')}
            report['geometrie'] = {'source': 'bdtopo'}
            # Lot 30 : un environnement relevé déjà relié donne la géométrie croisée BD TOPO × LiDAR (emprise
            # recalée, hauteur mesurée, toiture, voisins réels) à la place de la BD TOPO seule.
            if building.environment_id:
                try:
                    c = croisement.cross(building, poly, height)
                except croisement.NotCrossed as exc:
                    report['geometrie'] = {'source': 'bdtopo', 'note': str(exc)}
                else:
                    report.update(geometry=c['geometry'], footprint_xy=c['footprint_xy'], neighbours=c['neighbours'],
                                  horizon_deg=c['geometry']['horizon_deg'], geometrie=c['report'])
                    geo_rows = c['rows']
                    env3d = croisement._lidar_envelope(building, c['objects']) if auto_env else None
                    if env3d is not None:
                        building.envelope = env3d
                        building.sun_visibility_stale = True
                        report['envelope_auto'] = croisement._envelope_hash(env3d)
                    report['sources'].append('LiDAR HD (IGN, Licence Ouverte 2.0)')
            ok('A3', mitoyennete=report['geometry']['mitoyennete'], n_voisins=len(report['neighbours']),
               geometrie=report['geometrie']['source'])
            report['sources'].append('BD TOPO (IGN, Licence Ouverte 2.0)')
        except Exception as exc:  # noqa: BLE001
            fail('A3', exc)
    else:
        fail('A3', 'Position inconnue.')

    # A4 / A5 — archétype, compositions par défaut (sous les données BDNB/DPE)
    step('A5', 'Compositions par archétype…', 80)
    known = {k: v for k, v, *_ in bdnb_rows + geo_rows}
    year = known.get('identite.annee_construction')
    usage = known.get('identite.usage')
    defaults, period = archetypes.defaults(year, usage, known.get('mur.materiau'), param)
    arch_src = f'archétype {period} · {usage or "usage inconnu"}'
    rows = [(k, v, 'defaut', 0.15, arch_src) for k, v in defaults.items()]
    if not year:
        rows.append(('identite.annee_construction', archetypes.DEFAULT_YEAR, 'defaut', 0.1, 'valeur par défaut'))
    if not usage:
        rows.append(('identite.usage', 'autre', 'defaut', 0.1, 'valeur par défaut'))
    # Contrôle de cohérence : la surface d'un DPE « maison » peut être celle d'une petite maison
    # rattachée au même groupe BDNB (loge, conciergerie) — constaté sur un musée à 113 m² le
    # 2026-11-14. Écartée si elle s'éloigne de plus d'un facteur 2 de emprise × niveaux × 0,8.
    emprise_k, niveaux_k = known.get('identite.emprise_m2'), known.get('identite.niveaux')
    if 'identite.surface_m2' in known and emprise_k:
        est = emprise_k * (niveaux_k or max(1, round((known.get('identite.hauteur_m') or 3) / 3.0))) * 0.8
        s_dpe = known['identite.surface_m2']
        if not (0.5 * est <= s_dpe <= 2.0 * est):
            bdnb_rows = [r for r in bdnb_rows if r[0] != 'identite.surface_m2']
            known.pop('identite.surface_m2')
            report['warnings'] = report.get('warnings', []) + [
                f'Surface du DPE ({s_dpe:.0f} m²) incohérente avec l’emprise ({est:.0f} m² estimés) : écartée.']
    # Surface chauffée : DPE (maison) sinon emprise × niveaux × 0,8.
    if 'identite.surface_m2' not in known:
        emprise = known.get('identite.emprise_m2')
        niveaux = known.get('identite.niveaux') or (max(1, round((known.get('identite.hauteur_m') or 3) / 3.0)))
        if emprise:
            rows.append(('identite.surface_m2', round(emprise * niveaux * 0.8, 1), 'infere', 0.35,
                         'emprise × niveaux × 0,8'))
        if 'identite.niveaux' not in known:
            rows.append(('identite.niveaux', int(niveaux), 'infere', 0.35, 'hauteur ÷ 3 m'))
    ok('A5', periode=period)

    _record_all(building, rows)
    # Une valeur par clé et par méthode : la BD TOPO (mesurée sur l'emprise) prime sur la BDNB pour
    # l'emprise et la hauteur. Sans cela, deux sources de même méthode se remplaceraient en boucle
    # à chaque nouvelle initialisation.
    merged = {}
    for row in bdnb_rows + geo_rows:
        merged[(row[0], row[2])] = row
    _record_all(building, list(merged.values()))
    report['n_faits'] = len(rows) + len(bdnb_rows) + len(geo_rows)
    complete = all(report['steps'].get(s, {}).get('ok') for s in ('A1', 'A2', 'A3'))
    building.pipeline_status = 'complet' if complete else 'incomplet'
    building.pipeline = {**(building.pipeline or {}), **report}
    building.save()
    facts.refresh_scores(building)
    step('fin', 'Fiche initiale prête.', 95)
    return report


# ── Après la chaîne (A6, A7, A8) : environnement, météo par défaut et calculs automatiques ──

ENV_STAGES = {'lidar-index': 'dalles', 'lidar-read': 'lecture du nuage', 'bdtopo': 'emprises BD TOPO',
              'cosia': 'essences des arbres', 'rasters': 'terrain et hauteurs', 'registration': 'recalage',
              'buildings': 'bâtiments et toitures', 'lidar-only': 'bâtiments hors BD TOPO',
              'vegetation': 'arbres', 'terrain': 'terrain', 'ortho': 'albédo du sol', 'save': 'enregistrement'}


def apply_environment(building, progress=None):
    """Étape A6 (réglage « environnement.mode = chaine », par défaut depuis le Lot 30) : génère
    l'environnement observé (LiDAR HD × BD TOPO) s'il n'y en a pas de relié, puis croise la géométrie du
    bâtiment avec lui (`api.croisement`), **avant** N0/N1. Rend le compte rendu de l'étape, ou None si
    rien n'est à faire. Ne lève jamais."""
    from . import environment_service
    if not environment_service.wanted(building, 'chaine'):
        return None
    cb = None
    if progress:
        def cb(stage, pct):
            progress(f'Environnement observé (LiDAR HD) : {ENV_STAGES.get(stage, "préparation")}…', 95)
    step = environment_service.ensure_for_building(building, progress_cb=cb)
    if step.get('ok'):
        # Lot 30 : la géométrie du bâtiment est aussitôt croisée avec l'environnement (avant N0/N1).
        from . import croisement
        step['geometrie'] = croisement.apply(building)
    return step


def auto_levels(building):
    """Niveaux calculés automatiquement après la chaîne : N0 et N1, sauf réglage contraire du parc."""
    from . import reglages
    orch = reglages.for_building(building, 'orchestration')
    return [lvl for lvl, key in (('N0', 'calcul.n0'), ('N1', 'calcul.n1')) if orch.get(key, True)]


def apply_default_weather(building, owner_sub=''):
    """Réglage « meteo.type = reel » du parc : construit l'année réelle (Open-Meteo) et la relie au
    bâtiment s'il n'a pas déjà un jeu météo choisi. Rend le compte rendu de l'étape A7, ou None si
    rien n'est à faire (année type par défaut, comme hors parc). Ne lève jamais."""
    from datetime import date
    from . import reglages, weather_service
    orch = reglages.for_building(building, 'orchestration')
    if orch.get('meteo.type', 'tmy') != 'reel' or building.weather_dataset_id or building.georef_lat is None:
        return None
    year = orch.get('meteo.annee') or date.today().year - 1
    try:
        ds = weather_service.from_reanalysis(building.georef_lat, building.georef_lon, date(year, 1, 1),
                                             date(year, 12, 31), owner_sub)
    except Exception as exc:  # noqa: BLE001 — repli : année type du site
        return {'ok': False, 'error': net.reason(exc) if not isinstance(exc, weather_service.WeatherError) else str(exc)[:300]}
    building.weather_dataset = ds
    building.__class__.objects.filter(pk=building.pk).update(weather_dataset=ds)
    return {'ok': True, 'dataset_id': ds.pk, 'annee': year}
