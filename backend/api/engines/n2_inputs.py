"""Entrées du N2 (FEM par triangle) depuis la fiche (Lot 23, refonte — décisions D34 à D37).

`build(building, scenario)` rend tout ce que `building_solver.run_building_simulation` attend, sans
lire ni écrire `Building.envelope` en mode « fiche » :

- **géométrie** : emprise corrigée (Lot 15), sinon automatique (BD TOPO), sinon un carré de l'emprise
  déclarée ; hauteur, niveaux et toiture de la fiche ; enveloppe fermée (`geometry_service.envelope_for`) ;
- **mitoyenneté** : murs accolés à un voisin de la chaîne (même règle que N0/N1), sinon les murs qui
  approchent au mieux la part déclarée — rendus adiabatiques ;
- **baies** : une bande vitrée par niveau et par façade, dimensionnée pour le taux de vitrage de la
  fiche (`baies.ratio_<o>`), insérée par `facades.insert_openings` (enveloppe toujours fermée) ;
- **parois** reconstituées en mémoire, dont le U **égale** celui de la fiche : structure selon le
  matériau et l'inertie, isolant dimensionné (ITE dehors, ITI dedans) ; vitrages à `Uw` et `g` de la fiche ;
- **réglages** : ventilation effective, consignes, apports internes et horaires d'occupation **identiques
  à N1** (`engines.inputs`, `engines.n1.schedules`), pour que les niveaux restent comparables ;
- **obstacles** : environnement relié (LiDAR × BD TOPO) s'il existe, sinon les voisins de la chaîne,
  extrudés ; plus les protections solaires géométriques (Lot 14).

Mode « géométrie experte » (D35) : si l'enveloppe du bâtiment est **entièrement** assignée à la main
(page Bâtiment), elle est prise telle quelle avec ses parois du catalogue ; seuls les réglages et la
météo viennent de la fiche.

Limites assumées (écrites dans les entrées figées de chaque calcul) : un seul nœud d'air (pas de
planchers intermédiaires ni de refends, D9). Depuis `n2-6`, la consigne réduite hors occupation du tertiaire est
reproduite heure par heure (`weather_for_n2`, validation physique C3).

Lot 36 (chantier bt-potentiels) : protections fixes à débord ou à joues construites en obstacles géométriques quel que
soit leur mode ; mobiles, lames, vitrages et végétation appliqués par triangle de baie (`mobile_factors`) ; toiture et
murs végétalisés (couches, puits d'évapotranspiration), absorptance de toiture, albédo des abords, surventilation
nocturne, arbres plantés (houppiers translucides) et rafraîchissement de l'air — `engines.surcharges`.
"""

import math

import numpy as np
import shapely.geometry
import shapely.ops

from .inputs import HYPOTHESE_BYPASS, CEILING_H, GROUND_T_DEFAULT, ORIENTATIONS, RESIDENTIAL, _v, effective_ventilation, u_from_layers

ENGINE_VERSION = 'n2-6'   # -2 : réflexion du sol relevée (albédo de l'environnement relié) ; -3 : sol simulé (Lot 32) ; -4 : protections v2, améliorations, sorties par triangle (lot 36) ; -6 : albédo 0,20 par défaut sans environnement relevé, mitoyens sans réfléchi (validation physique C1, C2)
FICHE_ENGINES = ('n2-1', 'n2-2', 'n2-3', 'n2-4', 'n2-5', ENGINE_VERSION)   # N2 lancés depuis la fiche, toutes versions
H_I, H_E, R_GROUND = 7.7, 25.0, 0.5
DX_MAX = 0.05
FRAME_FRACTION = 0.2          # part de cadre d'une baie : même valeur que N1 (frame_factor 0,8)
AIR_CAPACITY_FACTOR = 5.0     # air + mobilier (même hypothèse que la recette N1/N2 du Lot 10)
T_MAX_FREE = 60.0             # pas de climatisation : la température flotte au-dessus de la consigne
IDS = {'mur': 'fiche:mur', 'toiture': 'fiche:toiture', 'plancher': 'fiche:plancher', 'vitrage': 'fiche:vitrage'}
LABELS = {'mur': 'Murs', 'toiture': 'Toiture', 'plancher': 'Plancher bas', 'vitrage': 'Vitrages'}
GREEN_WALL_ID = 'fiche:mur_vegetal'

# Structure porteuse par matériau de mur : (épaisseur m, λ W/m·K, ρ kg/m³, c J/kg·K) — ordres de grandeur.
MASONRY = {
    'pierre': (0.50, 2.30, 2300, 900), 'brique_pleine': (0.34, 0.80, 1800, 840),
    'brique_creuse': (0.20, 0.45, 1000, 840), 'parpaing': (0.20, 0.95, 1200, 1000),
    'beton': (0.18, 1.75, 2300, 1000), 'bois': (0.012, 0.13, 650, 1700),
    'pan_de_bois': (0.15, 0.35, 1200, 1200), 'monomur': (0.30, 0.12, 700, 1000),
}
BY_INERTIA = {'lourde': 'beton', 'moyenne': 'parpaing', 'legere': 'bois'}
PLASTER = (0.013, 0.35, 900, 1000)
INSULANT = (0.04, 30, 1400)     # λ, ρ, c
SLAB = (0.20, 1.75, 2300, 1000)
BOARD = (0.013, 0.25, 850, 1000)
GLASS_E, GAP_E = 0.004, 0.016
PANES = {'simple': 1, 'double': 2, 'double_fe': 2, 'triple': 3}


class N2InputError(ValueError):
    pass


def _layer(e, lam, rho, c, alpha=0.6):
    return {'e': round(float(e), 5), 'lam': round(float(lam), 5), 'rho': rho, 'c': c, 'tau': 0.0,
            'r': round(1.0 - alpha, 4), 'alpha': round(alpha, 4)}


def opaque_layers(u_target, structure, *, insulation='iti', alpha=0.6, r_films, plaster=True):
    """Couches (extérieur → intérieur) dont la résistance totale, films compris (`r_films`), vaut 1/U.

    L'isolant comble l'écart ; si la structure seule isole déjà trop, sa conductivité est relevée
    (sa masse est gardée) — le U de la fiche prime toujours."""
    u = max(float(u_target), 0.05)
    e, lam, rho, c = structure
    inner = [_layer(*PLASTER)] if plaster else []
    r_inner = sum(l['e'] / l['lam'] for l in inner)
    r_ins = 1.0 / u - r_films - e / lam - r_inner
    if r_ins >= 0.005:
        lam_i, rho_i, c_i = INSULANT
        ins = _layer(r_ins * lam_i, lam_i, rho_i, c_i)
        core = [_layer(e, lam, rho, c)]
        layers = [ins] + core + inner if insulation == 'ite' else core + [ins] + inner
    else:
        r_struct = max(1.0 / u - r_films - r_inner, 0.01)
        layers = [_layer(e, e / r_struct, rho, c)] + inner
    layers[0] = dict(layers[0], alpha=round(alpha, 4), r=round(1.0 - alpha, 4))
    return layers


def glazing_layers(uw, g, vitrage=None):
    """Vitrage dont le U (films compris) vaut `uw` et la transmission solaire directe vaut `g`."""
    uw, g = float(uw), min(max(float(g), 0.05), 0.9)
    n = PANES.get(vitrage) or (1 if uw >= 4.5 else 3 if uw < 1.0 else 2)
    tau = min(max((g / 0.97 ** (n - 1)) ** (1.0 / n), 0.05), 0.92)
    r = 0.08
    pane = {'e': GLASS_E, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': round(tau, 4), 'r': r,
            'alpha': round(max(1.0 - tau - r, 0.0), 4)}
    if n == 1:
        return [pane]
    r_gap = max((1.0 / uw - 1.0 / H_I - 1.0 / H_E - n * GLASS_E / 1.0) / (n - 1), 0.002)
    gap = {'e': GAP_E, 'lam': round(GAP_E / r_gap, 5), 'rho': 1.2, 'c': 1000, 'tau': 0.97, 'r': 0.01, 'alpha': 0.02}
    out = []
    for i in range(n):
        out.append(dict(pane))
        if i < n - 1:
            out.append(dict(gap))
    return out


def paroi_layers_from_fiche(values, green_roof=None, green_walls=None):
    """{id: couches} des parois de la fiche, et leur description (U obtenu, composition).

    Lot 36 : absorptance de toiture distincte (`toiture.absorptance`) ; toiture végétalisée : couche de substrat
    (épaisseur × part, λ et ρc de `engines.vegetation`) dehors, absorptance du feuillage ; murs végétalisés : paroi
    `fiche:mur_vegetal` (lame d'air R × part dehors, absorptance mêlée) pour les façades choisies."""
    from . import vegetation as veg
    inertia = _v(values, 'enveloppe.inertie', 'moyenne')
    material = values.get('mur.materiau')
    structure = MASONRY.get(material) or MASONRY[BY_INERTIA.get(inertia, 'parpaing')]
    insulation = _v(values, 'mur.isolation', 'iti')
    alpha = float(_v(values, 'enveloppe.couleur_absorptance', 0.6))
    alpha_roof = float(values['toiture.absorptance']) if values.get('toiture.absorptance') is not None else alpha
    films = 1.0 / H_I + 1.0 / H_E
    roof_type = _v(values, 'toiture.type', 'terrasse')
    roof_structure = SLAB if roof_type == 'terrasse' else BOARD
    roof = opaque_layers(_v(values, 'toiture.u', 1.0), roof_structure, insulation='ite',
                         alpha=alpha_roof, r_films=films, plaster=roof_type == 'terrasse')
    if green_roof:
        part = float(green_roof.get('part', 1.0))
        e = float(green_roof.get('substrat_m', 0.10)) * part
        a = veg.mixed_alpha(alpha_roof, part)
        if e > 0.001:
            sub = _layer(e, veg.SUBSTRATE_LAMBDA, 1200, veg.SUBSTRATE_RHO_C / 1200, alpha=a)
            roof = [sub] + roof                    # le substrat reçoit le soleil (première couche opaque)
        else:
            roof[0] = dict(roof[0], alpha=round(a, 4), r=round(1.0 - a, 4))
    layers = {
        IDS['mur']: opaque_layers(_v(values, 'mur.u', 1.0), structure, insulation=insulation, alpha=alpha,
                                  r_films=films),
        IDS['toiture']: roof,
        IDS['plancher']: opaque_layers(_v(values, 'plancher.u', 1.0), (0.15,) + SLAB[1:], insulation='ite',
                                       alpha=0.6, r_films=1.0 / H_I + R_GROUND, plaster=False),
        IDS['vitrage']: glazing_layers(_v(values, 'baies.uw', 2.8), _v(values, 'baies.g', 0.6),
                                       values.get('baies.vitrage')),
    }
    labels = dict(LABELS)
    ids = dict(IDS)
    if green_walls:
        part = float(green_walls.get('part', 0.5))
        a = veg.mixed_alpha(alpha, part)
        r = veg.WALL_LAYER_R * part
        base = layers[IDS['mur']]
        gap = {'e': 0.02, 'lam': round(0.02 / max(r, 1e-4), 5), 'rho': 1.2, 'c': 1000, 'tau': 0.0,
               'r': round(1.0 - a, 4), 'alpha': round(a, 4)}
        layers[GREEN_WALL_ID] = [gap] + base
        ids['mur_vegetal'] = GREEN_WALL_ID
        labels['mur_vegetal'] = 'Murs végétalisés'
    frames = {IDS['vitrage']: (float(_v(values, 'baies.uw', 2.8)), FRAME_FRACTION)}
    described = {}
    for part_name, pid in ids.items():
        ls = layers[pid]
        if part_name == 'plancher':
            u = 1.0 / (1.0 / H_I + sum(l['e'] / l['lam'] for l in ls) + R_GROUND)
        else:
            u = u_from_layers(ls, H_I, H_E)
        described[part_name] = {'id': pid, 'label': labels[part_name], 'u': round(u, 3),
                                'layers': [{k: l[k] for k in ('e', 'lam', 'rho', 'c', 'tau')} for l in ls]}
    return layers, frames, described


# ── Géométrie ────────────────────────────────────────────────────────────────

def _footprint(building, scenario, values):
    from .. import geometry_service
    edit = geometry_service.active_edit(building, scenario) or (
        geometry_service.active_edit(building) if scenario is not None else None)
    levels = max(1, int(_v(values, 'identite.niveaux', 1)))
    eave = float(_v(values, 'identite.hauteur_m', levels * 3.0))
    roof_type = _v(values, 'toiture.type', 'terrasse')
    pitch = float(_v(values, 'toiture.pente_deg', 35.0 if roof_type != 'terrasse' else 0.0))
    if edit is not None:
        return edit.footprint_xy, float(edit.eave_height_m), int(edit.levels), edit.roof_type, \
            float(edit.roof_pitch_deg or 0.0), 'géométrie corrigée'
    fp = (building.pipeline or {}).get('footprint_xy')
    source = 'emprise automatique (BD TOPO)'
    if not fp:
        fp = geometry_service.auto_footprint(building)
        source = 'emprise de l’enveloppe du bâtiment'
    if not fp:
        a_floor = float(_v(values, 'identite.surface_m2', 100.0))
        emprise = float(_v(values, 'identite.emprise_m2', a_floor / levels / 0.8))
        side = math.sqrt(max(emprise, 9.0))
        fp = [[0.0, 0.0], [side, 0.0], [side, side], [0.0, side]]
        source = 'carré de l’emprise déclarée (aucune emprise relevée)'
    return fp, eave, levels, roof_type, pitch, source


def _wall_groups(vertices, triangles):
    V = np.asarray(vertices, dtype=float)
    out = {}
    for i, t in enumerate(triangles):
        g = t.get('group') or ''
        if g.startswith('mur_'):
            out.setdefault(g, []).append(i)
    info = {}
    for g, idxs in out.items():
        area = sum(t['area'] for t in (triangles[i] for i in idxs))
        pts = V[sorted({j for i in idxs for j in triangles[i]['v']})]
        z0 = pts[:, 2].min()
        base = pts[np.abs(pts[:, 2] - z0) < 1e-3][:, :2]
        n = np.sum([np.asarray(triangles[i]['normal']) * triangles[i]['area'] for i in idxs], axis=0)
        seg = None
        if len(base) >= 2:
            d = np.linalg.norm(base[:, None, :] - base[None, :, :], axis=2)
            a, b = np.unravel_index(np.argmax(d), d.shape)
            seg = shapely.geometry.LineString([tuple(base[a]), tuple(base[b])])
        info[g] = {'idxs': idxs, 'area': area, 'segment': seg,
                   'azimuth': math.degrees(math.atan2(n[0], n[1])) % 360}
    return info


def _mitoyen(walls, neighbours, target, ratio_of=lambda az: 0.0):
    """Groupes de murs à rendre adiabatiques : accolés à un voisin, sinon au plus près de `target`,
    en commençant par les façades les moins vitrées (un mur mitoyen n'a pas de baie)."""
    if neighbours:
        union = shapely.ops.unary_union([p.buffer(0.6) for p, _h in neighbours])
        chosen = []
        for g, w in walls.items():
            s = w['segment']
            if s is None or s.length < 1.6:
                continue
            core = shapely.ops.substring(s, 0.7, s.length - 0.7)
            if core.intersection(union).length >= 0.5 * core.length:
                chosen.append(g)
        return chosen, 'voisins relevés'
    if target <= 0.02:
        return [], 'aucune'
    total = sum(w['area'] for w in walls.values()) or 1.0
    chosen, acc = [], 0.0
    for g, w in sorted(walls.items(), key=lambda kv: (ratio_of(kv[1]['azimuth']), -kv[1]['area'], kv[0])):
        if abs(acc + w['area'] - target * total) < abs(acc - target * total):
            chosen.append(g)
            acc += w['area']
    return chosen, 'part déclarée (murs choisis au plus près)'


def _band_openings(plane, ratio, storey_h, levels):
    """Une bande vitrée par niveau, centrée, dont la surface totale vaut `ratio` × surface de la façade."""
    if ratio <= 0.001 or plane['outline'] is None:
        return []
    n = max(1, min(levels, int(plane['height'] // 2.0) or 1))
    margin = 0.4
    width = plane['width'] - 2 * margin
    if width < 0.5:
        return []
    h = min(ratio * plane['area'] / (n * width), storey_h - 0.5)
    if h < 0.15:
        return []
    out = []
    for k in range(n):
        t0 = k * storey_h + (storey_h - h) / 2.0
        out.append({'label': 'window', 'score': None, 's0': round(margin, 3), 't0': round(t0, 3),
                    's1': round(margin + width, 3), 't1': round(t0 + h, 3), 'synthetic': True})
    return out


def _cardinal(azimuth_local, north):
    from ..bdnb import cardinal_of
    return cardinal_of((azimuth_local + north) % 360)


def envelope_from_fiche(building, scenario, values):
    from .. import facades, geometry, geometry_service
    fp, eave, levels, roof_type, pitch, source = _footprint(building, scenario, values)
    try:
        poly = geometry_service.validate(fp, eave, levels, roof_type, pitch)
    except geometry_service.GeometryEditError as exc:
        raise N2InputError(f'Géométrie inutilisable pour le N2 : {exc}') from exc
    envelope, note = geometry_service.envelope_for(poly, eave, roof_type, pitch)
    V, T = envelope['vertices'], envelope['triangles']
    floor_type = _v(values, 'plancher.type', 'terre_plein')
    for t in T:
        g = t.get('group') or ''
        if g == 'sol':
            t['paroi_model_id'] = IDS['plancher']
            t['boundary'] = 'adiabatic' if floor_type == 'local_chauffe' else 'ground'
        elif g.startswith('mur_'):
            t['paroi_model_id'] = IDS['mur']
        else:
            t['paroi_model_id'] = IDS['toiture']
    walls = _wall_groups(V, T)
    neighbours = geometry_service._neighbours(building)
    north = building.georef_north_offset_deg or 0.0
    mit_groups, mit_source = _mitoyen(walls, neighbours, float(_v(values, 'identite.mitoyennete', 0.0)),
                                      lambda az: float(_v(values, f'baies.ratio_{_cardinal(az, north)}', 0.15)))
    for g in mit_groups:
        for i in walls[g]['idxs']:
            T[i]['group'] = f'{g}_mitoyen'
            T[i]['boundary'] = 'adiabatic'
    storey_h = eave / max(levels, 1)
    inserted = 0
    gross = {o: 0.0 for o in ORIENTATIONS}   # surface des façades qui reçoivent des baies (comme N1 : jusqu'à l'égout)
    for g, plane in facades.facade_planes(V, T).items():
        az = math.degrees(math.atan2(plane['n'][0], plane['n'][1])) % 360
        o = _cardinal(az, north)
        ops = _band_openings(plane, float(_v(values, f'baies.ratio_{o}', 0.15)), storey_h, levels)
        V, T, k = facades.insert_openings(V, T, g, plane, ops, IDS['vitrage'])
        inserted += k
        gross[o] += plane['area']
    T = geometry.compute_envelope_geometry(V, T)
    geometry_service.check_closed(V, T)
    glazed = {o: 0.0 for o in ORIENTATIONS}
    for t in T:
        if t['paroi_model_id'] == IDS['vitrage']:
            glazed[_cardinal(t['azimuth_deg'], north)] += t['area']
    summary = {
        'source': source, 'note': note, 'emprise_m2': round(poly.area, 1), 'hauteur_m': round(eave, 2),
        'niveaux': levels, 'toiture': roof_type, 'pente_deg': pitch, 'n_triangles': len(T),
        'mitoyennete': {'source': mit_source, 'murs': len(mit_groups),
                        'part': round(sum(walls[g]['area'] for g in mit_groups) /
                                      (sum(w['area'] for w in walls.values()) or 1.0), 3)},
        'baies': {o: {'cible': round(float(_v(values, f'baies.ratio_{o}', 0.15)), 3),
                      'obtenu': round(glazed[o] / gross[o], 3) if gross[o] else 0.0,
                      'surface_m2': round(glazed[o], 1)} for o in ORIENTATIONS},
        'n_baies': inserted,
    }
    return {'vertices': V, 'triangles': T}, summary


def _expert_envelope(building):
    tris = (building.envelope or {}).get('triangles') or []
    return bool(tris) and all(t.get('paroi_model_id') is not None for t in tris)


def _expert_parois(building):
    from ..models import ParoiModel
    ids = sorted({t['paroi_model_id'] for t in building.envelope['triangles']})
    models = list(ParoiModel.objects.filter(pk__in=ids))
    if len(models) != len(ids):
        raise N2InputError('Une paroi assignée a été supprimée du catalogue : complétez l’assignation.')
    layers = {p.pk: p.layers for p in models}
    frames = {p.pk: (p.frame_u, p.frame_fraction) for p in models
              if p.frame_u is not None and p.frame_fraction is not None}
    area = {}
    for t in building.envelope['triangles']:
        area[t['paroi_model_id']] = area.get(t['paroi_model_id'], 0.0) + float(t.get('area') or 0.0)
    described = {str(p.pk): {'id': p.pk, 'label': p.name, 'u': round(u_from_layers(p.layers, H_I, H_E), 3),
                             'surface_m2': round(area.get(p.pk, 0.0), 1)} for p in models}
    return layers, frames, described


# ── Obstacles et réglages ───────────────────────────────────────────────────

def occluders(building, envelope, scenario=None, all_fixed=False):
    """Obstacles du lancer de rayons : environnement relié, sinon voisins extrudés ; plus les protections fixes
    construites en géométrie (lot 36 : toutes les fixes géométrisables depuis la fiche, `all_fixed`)."""
    from .. import shading_service
    from ..geodata import extrude_footprint
    if building.environment_id:
        env, source = building.environment.envelope, 'environnement relié'
    else:
        from .. import geometry_service
        verts, tris = [], []
        for poly, h in geometry_service._neighbours(building):
            v, f = extrude_footprint(list(poly.exterior.coords)[:-1], max(float(h or 3.0), 1.0))
            base = len(verts)
            verts += [list(map(float, p)) for p in v]
            tris += [{'v': [base + int(i) for i in face]} for face in f]
        env = {'vertices': verts, 'triangles': tris} if tris else None
        source = 'voisins relevés (BD TOPO)' if tris else 'aucun'
    merged = shading_service.merge_envelopes(
        env, shading_service.geometric_occluders(building, scenario, envelope=envelope, all_fixed=all_fixed)) or env
    return merged, source


def payload_from_fiche(values, a_floor):
    vol = a_floor * CEILING_H
    t_heat = float(_v(values, 'usage.consigne_chauffage_c', 19.0))
    t_cool = values.get('usage.consigne_froid_c')
    gains_w = float(_v(values, 'usage.apports_w_m2', 4.0)) * a_floor
    # Lot 40 : double flux — le débit du N2 est celui « échangeur by-passé » ; la récupération est retirée heure par
    # heure par le solveur quand l'échangeur est actif (`payload['bypass']`, `inputs.recovery_active`).
    from .inputs import BYPASS_MARGIN_C, recovery_split
    n_rec, eta = recovery_split(values)
    debit = (effective_ventilation(values) + n_rec * eta) * vol
    payload = {
        'dx_max': DX_MAX, 'h_e': H_E, 'h_e_dynamic': False, 't_init': t_heat, 'shadow_mode': 'precomputed',
        't_ground': GROUND_T_DEFAULT, 'r_ground': R_GROUND, 'heure_debut': 0,
        'interior': {'mode': 'thermostat', 'h_i': H_I, 'c_air_int': 1.2 * 1006 * vol * AIR_CAPACITY_FACTOR,
                     't_min': t_heat, 't_max': float(t_cool) if t_cool is not None else T_MAX_FREE,
                     'debit_vent_m3h': round(debit, 2), 'eta_recup_vent': 0.0, 'apports_internes_w': round(gains_w, 1)},
    }
    if n_rec * eta > 0:
        payload['bypass'] = {'g_recup_w_k': round(0.34 * n_rec * eta * vol, 3), 'seuil_c': t_heat + BYPASS_MARGIN_C}
    residential = _v(values, 'identite.usage', 'autre') in RESIDENTIAL
    if not residential:
        # Mêmes horaires que N1 (engines.n1.schedules) : occupé 8 h–19 h du lundi au vendredi.
        def slot(h, open_day):
            f = 2.2 if open_day and 8 <= h < 19 else 0.3
            return {'debit_vent_m3h': round(debit, 2), 'eta_recup_vent': 0.0, 'apports_internes_w': round(gains_w * f, 1)}
        payload['planning'] = [slot(h, True) for h in range(24)]
        payload['planning_ferme'] = [slot(h, False) for h in range(24)]
    return payload, residential


def weather_for_n2(series, north, residential, ground=None, t_heat=None):
    """Série météo du N2 : azimut solaire dans le repère local du bâtiment ; jours ouvrés marqués ; température
    vue par le plancher bas heure par heure (`ground`, Lot 32 — sol simulé, vide sanitaire, sous-sol).

    Validation physique (C3) : hors logement, consigne de chauffage de chaque heure (`t_min`) celle de N1 —
    réduite de 3 °C hors occupation (`engines.n1.schedules`) — si `t_heat` est donné ; calendrier lu sur l'indice
    horaire de la météo, comme N1 (et non sur la position dans la liste)."""
    from ..weather_source import to_local_azimuth
    from .n1 import schedules
    hidx = np.array([w.get('hour_index') if w.get('hour_index') is not None else i for i, w in enumerate(series)],
                    dtype=int)
    setpoint = schedules(hidx, residential, float(t_heat))[1] if (t_heat is not None and not residential) else None
    out = []
    for i, w in enumerate(series):
        p = dict(w)
        if ground is not None:
            p['t_ground'] = round(float(ground[i]), 3)
        p['sun_azimuth'] = round(to_local_azimuth(float(w['sun_azimuth']), north), 3) if north else w['sun_azimuth']
        if not residential:
            p['occupied'] = bool((hidx[i] // 24) % 7 < 5)
            if setpoint is not None:
                p['t_min'] = float(setpoint[i])
        out.append(p)
    return out


# ── Point d'entrée ───────────────────────────────────────────────────────────

def _glazing_ids(building):
    from ..models import ParoiModel
    return set(ParoiModel.objects.filter(is_glazing=True).values_list('pk', flat=True)) | \
        {pid for pid, layers in ParoiModel.objects.values_list('pk', 'layers') if any((l.get('tau') or 0) > 0 for l in layers)} | \
        {IDS['vitrage']}


def window_groups(envelope, north, glazing_ids):
    """Triangles de baie par orientation cardinale : {o: [indices]} (protections appliquées par triangle, lot 36)."""
    out = {}
    for i, t in enumerate(envelope['triangles']):
        if t.get('paroi_model_id') in glazing_ids and 60 < float(t.get('tilt_deg', 90)) < 120:
            out.setdefault(_cardinal(float(t.get('azimuth_deg', 0.0)), north), []).append(i)
    return out


def _mark_green(envelope, values, green_roof, green_walls, north, mode):
    """Lot 36 : parois végétalisées (mode fiche) — murs des façades choisies sur `fiche:mur_vegetal` ; coefficient
    d'évapotranspiration par triangle (`evapo`, `engines.vegetation.latent_coefficient`)."""
    from . import vegetation as veg
    if mode != 'fiche' or not (green_roof or green_walls):
        return envelope
    tris = [dict(t) for t in envelope['triangles']]
    ors = set((green_walls or {}).get('orientations') or [])
    for t in tris:
        if t.get('paroi_model_id') == IDS['mur'] and t.get('boundary') != 'adiabatic' and green_walls and \
                _cardinal(float(t.get('azimuth_deg', 0.0)), north) in ors:
            t['paroi_model_id'] = GREEN_WALL_ID
            t['evapo'] = round(veg.latent_coefficient(green_walls['lai'], green_walls['part']), 5)
        elif t.get('paroi_model_id') == IDS['toiture'] and green_roof:
            t['evapo'] = round(veg.latent_coefficient(green_roof['lai'], green_roof['part']), 5)
    return {**envelope, 'triangles': tris}


def _expert_roof_alpha(envelope, values, layers):
    """Enveloppe experte : `toiture.absorptance` posée comme absorptance extérieure des toitures opaques (α des
    couches), sans toucher aux modèles du catalogue."""
    a = values.get('toiture.absorptance')
    if a is None:
        return envelope, False
    tris = []
    for t in envelope['triangles']:
        ls = layers.get(t.get('paroi_model_id')) or []
        if t.get('boundary') != 'ground' and float(t.get('tilt_deg', 90)) < 60 and ls and not ls[0].get('tau'):
            t = {**t, 'alpha_ext': float(a)}
        tris.append(t)
    return {**envelope, 'triangles': tris}, True


def build(building, scenario=None, values=None):
    """Tout ce qu'il faut pour lancer le N2, et sa description figée (`snapshot`), sans la météo."""
    from .. import facts
    from . import shading as sh
    from . import surcharges
    vals = values if values is not None else facts.effective_values(building, scenario)
    s = surcharges.of(scenario)
    a_floor = float(_v(vals, 'identite.surface_m2', 100.0))
    payload, residential = payload_from_fiche(vals, a_floor)
    north = building.georef_north_offset_deg or 0.0
    green_roof = s.get('toiture_vegetalisee') if vals.get('toiture.vegetalisee') is not False else None
    if green_roof is None and vals.get('toiture.vegetalisee') is True:
        from .vegetation import GREEN_ROOF_DEFAULT
        green_roof = dict(GREEN_ROOF_DEFAULT)
    green_walls = s.get('murs_vegetalises') if vals.get('murs.vegetalises') is not False else None
    if green_walls is None and vals.get('murs.vegetalises') is True:
        from .vegetation import GREEN_WALLS_DEFAULT
        green_walls = dict(GREEN_WALLS_DEFAULT)
    limits = ['Un seul nœud d’air : planchers intermédiaires et refends non modélisés (D9).']
    if _expert_envelope(building):
        mode = 'experte'
        layers, frames, described = _expert_parois(building)
        envelope, alpha_set = _expert_roof_alpha(building.envelope, vals, layers)
        geom = {'source': 'enveloppe experte (assignée à la main)', 'n_triangles': len(envelope['triangles'])}
        if green_roof or green_walls:
            limits.append('Toiture ou murs végétalisés non appliqués : enveloppe experte (parois du catalogue).')
    else:
        mode = 'fiche'
        envelope, geom = envelope_from_fiche(building, scenario, vals)
        # Lot 32 : au-dessus d'un vide sanitaire ou d'un sous-sol, le plancher a son U propre (le local tampon et le
        # sol sont simulés à part) ; `plancher.u` est le U équivalent vers l'extérieur (`engines.sol.floor_u_node`).
        gp = _ground_params(building, scenario, vals)
        from .sol import floor_u_node
        u_node, _note = floor_u_node(gp.floor_type, gp.floor_area or gp.a_floor, gp.floor_perimeter, gp.u_floor,
                                     gp.basement_depth)
        layers, frames, described = paroi_layers_from_fiche({**vals, 'plancher.u': u_node} if gp.floor_area else vals,
                                                            green_roof, green_walls)
        envelope = _mark_green(envelope, vals, green_roof, green_walls, north, mode)
    env, env_source = occluders(building, envelope, scenario, all_fixed=True)
    from .. import shading_service
    devices = shading_service.devices_of(building, scenario)
    per_triangle = [d for d in devices if d['kind'] not in sh.GEOMETRIC]
    groups = window_groups(envelope, north, _glazing_ids(building)) if per_triangle else {}
    nocturne = None
    if float(_v(vals, 'ventilation.nocturne_vol_h', 0.0)) > 0:
        nocturne = {'debit_m3h': round(float(vals['ventilation.nocturne_vol_h']) * a_floor * CEILING_H, 1),
                    'seuil_c': float(_v(vals, 'ventilation.nocturne_seuil_c', 22.0))}
    albedo = vals.get('abords.albedo')
    ouverture = surcharges.window_opening(building)            # lot 40, I9 : réglage du parc (tous les états)
    snapshot = {
        'engine': ENGINE_VERSION, 'mode': mode, 'surface_m2': a_floor, 'volume_m3': round(a_floor * CEILING_H, 1),
        'geometry': geom, 'parois': described, 'obstacles': env_source,
        'reglages': {k: v for k, v in payload.items() if k not in ('planning', 'planning_ferme')},
        'occupation': 'logement (constante)' if residential else
        'tertiaire : 8 h–19 h, lundi–vendredi ; consigne réduite de 3 °C hors occupation (comme N1)',
        'limites': limits,
        'protections': {'geometriques': [d['id'] for d in devices if d['kind'] in sh.GEOMETRIC],
                        'par_triangle': [d['id'] for d in per_triangle],
                        'triangles_de_baie': {o: len(v) for o, v in groups.items()}},
        'surventilation_nocturne': nocturne,
        'ouverture_fenetres': ouverture,
        'double_flux': ({**payload['bypass'], 'hypothese': HYPOTHESE_BYPASS} if payload.get('bypass') else None),
        'albedo_abords': float(albedo) if albedo is not None else None,
        'arbres_plantes': len(s.get('arbres') or []),
        'ameliorations': surcharges.describe(building, scenario, 'N2', mode),
    }
    ground_params = _ground_params(building, scenario, vals)
    return {'envelope': envelope, 'layers': layers, 'frames': frames, 'payload': payload,
            'environment': env, 'residential': residential, 'a_floor': a_floor, 'values': vals,
            'snapshot': snapshot, 'ground_params': ground_params, 'devices_par_triangle': per_triangle,
            'window_groups': groups, 'nocturne': nocturne, 'ouverture': ouverture, 'albedo_abords': float(albedo) if albedo is not None else None,
            'arbres': s.get('arbres') or [], 'g_window': float(_v(vals, 'baies.g', 0.6)), 'north': north}


def mobile_factors(built, weather_geo):
    """Facteurs par triangle de baie, heure par heure, des protections non géométriques (mobiles, lames, vitrages,
    végétation) — mêmes fonctions que N0/N1 (`engines.shading.orientation_effects`), azimut géographique.
    Rend la liste attendue par `building_solver` (`payload['baies_facteurs']`)."""
    from . import shading as sh
    from . import solar
    devs, groups = built['devices_par_triangle'], built['window_groups']
    if not devs or not groups:
        return []
    t_e, az, el, dni, dhi, hidx = solar.arrays(weather_geo)
    occupied = None
    if not built['residential']:
        hour, day = hidx % 24, (hidx // 24) % 7
        occupied = (hour >= 8) & (hour < 19) & (day < 5)
    out = []
    for o, idxs in sorted(groups.items()):
        total, _beam = solar.surface_irradiance(az, el, dni, dhi, solar.ORIENT_AZ[o], 90.0)
        e = sh.orientation_effects(devs, o, solar.ORIENT_AZ[o], az, el, hidx, total, t_e, built['g_window'], occupied)
        if not any(d.get('orientation') in (o, 'all') for d in devs):
            continue
        out.append({'orientation': o, 'triangles': idxs,
                    'beam': np.round(e['beam'], 4).tolist(), 'diffuse': np.round(e['diffuse'], 4).tolist(),
                    'r_add': np.round(e['r_add'], 2).tolist()})
    return out


def _ground_params(building, scenario, vals):
    """Mêmes paramètres de plancher bas que N0/N1 (type, emprise, périmètre exposé, U, profondeur du sous-sol)."""
    from .. import geometry_service
    from .inputs import from_fiche
    return from_fiche(vals, geometry_service.effective_summary(building, scenario))


def ground_for(built, series):
    """Température vue par le plancher bas du N2, heure par heure, et sa description (Lot 32, `engines.sol`) ;
    intérieur pris à la consigne de chauffage de chaque heure, réduit du tertiaire compris (comme N0/N1, C3 ;
    couplage non itéré)."""
    from .inputs import ground_series
    from .n1 import schedules
    t_e = np.array([float(w['t_ext']) for w in series])
    hidx = np.array([w.get('hour_index') if w.get('hour_index') is not None else i for i, w in enumerate(series)],
                    dtype=int)
    t_in = schedules(hidx, built['residential'], float(built['payload']['interior']['t_min']))[1]
    t, _u, desc = ground_series(built['ground_params'], t_e, t_in)
    return t, desc
