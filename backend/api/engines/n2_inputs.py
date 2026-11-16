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
planchers intermédiaires ni de refends, D9) ; la consigne réduite hors occupation du tertiaire n'est pas
reproduite (le planning du N2 ne porte pas de consigne) ; les protections paramétriques ne jouent que
dans N0/N1 (le N2 voit les protections géométriques).
"""

import math

import numpy as np
import shapely.geometry
import shapely.ops

from .inputs import CEILING_H, GROUND_T_DEFAULT, ORIENTATIONS, RESIDENTIAL, _v, effective_ventilation, u_from_layers

ENGINE_VERSION = 'n2-2'   # -2 : réflexion du sol relevée (albédo de l'environnement relié)
FICHE_ENGINES = ('n2-1', ENGINE_VERSION)   # N2 lancés depuis la fiche, toutes versions
H_I, H_E, R_GROUND = 7.7, 25.0, 0.5
DX_MAX = 0.05
FRAME_FRACTION = 0.2          # part de cadre d'une baie : même valeur que N1 (frame_factor 0,8)
AIR_CAPACITY_FACTOR = 5.0     # air + mobilier (même hypothèse que la recette N1/N2 du Lot 10)
T_MAX_FREE = 60.0             # pas de climatisation : la température flotte au-dessus de la consigne
IDS = {'mur': 'fiche:mur', 'toiture': 'fiche:toiture', 'plancher': 'fiche:plancher', 'vitrage': 'fiche:vitrage'}
LABELS = {'mur': 'Murs', 'toiture': 'Toiture', 'plancher': 'Plancher bas', 'vitrage': 'Vitrages'}

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


def paroi_layers_from_fiche(values):
    """{id: couches} des quatre parois de la fiche, et leur description (U obtenu, composition)."""
    inertia = _v(values, 'enveloppe.inertie', 'moyenne')
    material = values.get('mur.materiau')
    structure = MASONRY.get(material) or MASONRY[BY_INERTIA.get(inertia, 'parpaing')]
    insulation = _v(values, 'mur.isolation', 'iti')
    alpha = float(_v(values, 'enveloppe.couleur_absorptance', 0.6))
    films = 1.0 / H_I + 1.0 / H_E
    roof_type = _v(values, 'toiture.type', 'terrasse')
    roof_structure = SLAB if roof_type == 'terrasse' else BOARD
    layers = {
        IDS['mur']: opaque_layers(_v(values, 'mur.u', 1.0), structure, insulation=insulation, alpha=alpha,
                                  r_films=films),
        IDS['toiture']: opaque_layers(_v(values, 'toiture.u', 1.0), roof_structure, insulation='ite',
                                      alpha=alpha, r_films=films, plaster=roof_type == 'terrasse'),
        IDS['plancher']: opaque_layers(_v(values, 'plancher.u', 1.0), (0.15,) + SLAB[1:], insulation='ite',
                                       alpha=0.6, r_films=1.0 / H_I + R_GROUND, plaster=False),
        IDS['vitrage']: glazing_layers(_v(values, 'baies.uw', 2.8), _v(values, 'baies.g', 0.6),
                                       values.get('baies.vitrage')),
    }
    frames = {IDS['vitrage']: (float(_v(values, 'baies.uw', 2.8)), FRAME_FRACTION)}
    described = {}
    for part, pid in IDS.items():
        ls = layers[pid]
        if part == 'plancher':
            u = 1.0 / (1.0 / H_I + sum(l['e'] / l['lam'] for l in ls) + R_GROUND)
        else:
            u = u_from_layers(ls, H_I, H_E)
        described[part] = {'id': pid, 'label': LABELS[part], 'u': round(u, 3),
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

def occluders(building, envelope, scenario=None):
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
        env, shading_service.geometric_occluders(building, scenario, envelope=envelope)) or env
    return merged, source


def payload_from_fiche(values, a_floor):
    vol = a_floor * CEILING_H
    t_heat = float(_v(values, 'usage.consigne_chauffage_c', 19.0))
    t_cool = values.get('usage.consigne_froid_c')
    gains_w = float(_v(values, 'usage.apports_w_m2', 4.0)) * a_floor
    debit = effective_ventilation(values) * vol
    payload = {
        'dx_max': DX_MAX, 'h_e': H_E, 'h_e_dynamic': False, 't_init': t_heat, 'shadow_mode': 'precomputed',
        't_ground': GROUND_T_DEFAULT, 'r_ground': R_GROUND, 'heure_debut': 0,
        'interior': {'mode': 'thermostat', 'h_i': H_I, 'c_air_int': 1.2 * 1006 * vol * AIR_CAPACITY_FACTOR,
                     't_min': t_heat, 't_max': float(t_cool) if t_cool is not None else T_MAX_FREE,
                     'debit_vent_m3h': round(debit, 2), 'eta_recup_vent': 0.0, 'apports_internes_w': round(gains_w, 1)},
    }
    residential = _v(values, 'identite.usage', 'autre') in RESIDENTIAL
    if not residential:
        # Mêmes horaires que N1 (engines.n1.schedules) : occupé 8 h–19 h du lundi au vendredi.
        def slot(h, open_day):
            f = 2.2 if open_day and 8 <= h < 19 else 0.3
            return {'debit_vent_m3h': round(debit, 2), 'eta_recup_vent': 0.0, 'apports_internes_w': round(gains_w * f, 1)}
        payload['planning'] = [slot(h, True) for h in range(24)]
        payload['planning_ferme'] = [slot(h, False) for h in range(24)]
    return payload, residential


def weather_for_n2(series, north, residential):
    """Série météo du N2 : azimut solaire dans le repère local du bâtiment ; jours ouvrés marqués."""
    from ..weather_source import to_local_azimuth
    out = []
    for i, w in enumerate(series):
        p = dict(w)
        p['sun_azimuth'] = round(to_local_azimuth(float(w['sun_azimuth']), north), 3) if north else w['sun_azimuth']
        if not residential:
            p['occupied'] = (i // 24) % 7 < 5
        out.append(p)
    return out


# ── Point d'entrée ───────────────────────────────────────────────────────────

def build(building, scenario=None, values=None):
    """Tout ce qu'il faut pour lancer le N2, et sa description figée (`snapshot`), sans la météo."""
    from .. import facts
    vals = values if values is not None else facts.effective_values(building, scenario)
    a_floor = float(_v(vals, 'identite.surface_m2', 100.0))
    payload, residential = payload_from_fiche(vals, a_floor)
    if _expert_envelope(building):
        mode = 'experte'
        envelope = building.envelope
        layers, frames, described = _expert_parois(building)
        geom = {'source': 'enveloppe experte (assignée à la main)', 'n_triangles': len(envelope['triangles'])}
    else:
        mode = 'fiche'
        envelope, geom = envelope_from_fiche(building, scenario, vals)
        layers, frames, described = paroi_layers_from_fiche(vals)
    env, env_source = occluders(building, envelope, scenario)
    limits = ['Un seul nœud d’air : planchers intermédiaires et refends non modélisés (D9).']
    if not residential:
        limits.append('Consigne réduite hors occupation non reproduite (N1 la baisse de 3 °C).')
    from .. import shading_service
    if any(d['mode'] != 'geometrique' for d in shading_service.devices_of(building, scenario)):
        limits.append('Protections solaires paramétriques : prises en compte par N0/N1 seulement.')
    snapshot = {
        'engine': ENGINE_VERSION, 'mode': mode, 'surface_m2': a_floor, 'volume_m3': round(a_floor * CEILING_H, 1),
        'geometry': geom, 'parois': described, 'obstacles': env_source,
        'reglages': {k: v for k, v in payload.items() if k not in ('planning', 'planning_ferme')},
        'occupation': 'logement (constante)' if residential else 'tertiaire : 8 h–19 h, lundi–vendredi',
        'limites': limits,
    }
    return {'envelope': envelope, 'layers': layers, 'frames': frames, 'payload': payload,
            'environment': env, 'residential': residential, 'a_floor': a_floor, 'values': vals,
            'snapshot': snapshot}
