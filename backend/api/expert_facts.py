"""Gestes experts → faits de la fiche (Lot 26, refonte — décision D35).

Ce qui se fait sur les pages expertes améliore la fiche, donc tous les niveaux de calcul :

- **enveloppe entièrement assignée** (page Bâtiment) → U des murs, de la toiture, du plancher bas et des
  vitrages, facteur solaire, taux de vitrage par orientation, part mitoyenne et emprise **déduits** des parois
  et des surfaces réelles : faits `declare`, source « enveloppe experte », au nom de qui a enregistré
  l'enveloppe. Une enveloppe qui n'est plus entièrement assignée retire ces faits (la fiche revient à ses
  autres valeurs). Mêmes coefficients d'échange que le N2 (`engines.n2_inputs`) ;
- **façades analysées** (Panoramax) → un document local « Analyse des façades », dont les taux de vitrage par
  orientation sont des **propositions** à valider dans l'onglet Documents, comme celles d'un DPE.

L'environnement relevé (LiDAR × BD TOPO) donne l'horizon par façade de N0/N1 : `env_horizon`, appelé par
`geometry_service.effective_summary` (ce n'est pas une clé de la fiche).
"""

import math

from . import facts
from .bdnb import cardinal_of
from .engines.inputs import ORIENTATIONS, _tau, u_from_layers
from .engines.n2_inputs import H_E, H_I, R_GROUND

SOURCE_ENVELOPE = 'enveloppe experte'


def envelope_values(building):
    """{clé: valeur} déduites d'une enveloppe entièrement assignée, sinon None."""
    from .models import ParoiModel
    env = building.envelope or {}
    tris = env.get('triangles') or []
    if not tris or any(t.get('paroi_model_id') is None for t in tris):
        return None
    models = {p.pk: p for p in ParoiModel.objects.filter(pk__in={t['paroi_model_id'] for t in tris})}
    if len(models) != len({t['paroi_model_id'] for t in tris}):
        return None
    north = building.georef_north_offset_deg or 0.0
    acc = {'wall_ua': 0.0, 'wall_a': 0.0, 'roof_ua': 0.0, 'roof_a': 0.0, 'floor_ua': 0.0, 'floor_a': 0.0,
           'win_ua': 0.0, 'win_a': 0.0, 'win_ga': 0.0, 'adiab_a': 0.0}
    by_o = {o: {'opaque': 0.0, 'window': 0.0} for o in ORIENTATIONS}
    for t in tris:
        p = models[t['paroi_model_id']]
        layers, area = p.layers, float(t.get('area') or 0.0)
        tilt = float(t['tilt_deg']) if t.get('tilt_deg') is not None else 90.0   # 0 = toit plat, pas « absent »
        if area <= 0:
            continue
        if t.get('boundary') == 'adiabatic':
            if 60 <= tilt <= 120:
                acc['adiab_a'] += area
            continue
        if t.get('boundary') == 'ground':
            acc['floor_ua'] += area / (1.0 / H_I + sum(l['e'] / l['lam'] for l in layers) + R_GROUND)
            acc['floor_a'] += area
            continue
        u = u_from_layers(layers, H_I, H_E)
        if tilt < 60:
            acc['roof_ua'] += area * u
            acc['roof_a'] += area
        elif tilt <= 120:
            o = cardinal_of((float(t.get('azimuth_deg') or 0.0) + north) % 360)
            tau = _tau(layers)
            if tau > 0:
                ff = p.frame_fraction if p.frame_u is not None and p.frame_fraction is not None else 0.0
                uw = (1 - ff) * u + ff * (p.frame_u or 0.0)
                acc['win_ua'] += area * uw
                acc['win_a'] += area
                acc['win_ga'] += area * tau
                by_o[o]['window'] += area
            else:
                acc['wall_ua'] += area * u
                acc['wall_a'] += area
                by_o[o]['opaque'] += area
    out = {}
    if acc['wall_a']:
        out['mur.u'] = acc['wall_ua'] / acc['wall_a']
    if acc['roof_a']:
        out['toiture.u'] = acc['roof_ua'] / acc['roof_a']
    if acc['floor_a']:
        out['plancher.u'] = acc['floor_ua'] / acc['floor_a']
        out['identite.emprise_m2'] = acc['floor_a']
    if acc['win_a']:
        out['baies.uw'] = acc['win_ua'] / acc['win_a']
        out['baies.g'] = acc['win_ga'] / acc['win_a']
    for o, w in by_o.items():
        if w['opaque'] + w['window'] > 0:
            out[f'baies.ratio_{o}'] = w['window'] / (w['opaque'] + w['window'])
    walls = acc['wall_a'] + acc['win_a'] + acc['adiab_a']
    if walls:
        out['identite.mitoyennete'] = min(acc['adiab_a'] / walls, 0.9)
    clean = {}
    for k, v in out.items():
        try:
            clean[k] = facts.validate_value(k, round(v, 3) if k != 'identite.emprise_m2' else round(v, 1))
        except facts.FactError:
            continue   # hors des bornes de la fiche (U extrême, etc.) : on ne l'impose pas
    return clean


def _active(building):
    return {f.key: f for f in building.facts.filter(source=SOURCE_ENVELOPE, scenario__isnull=True,
                                                    retired_at__isnull=True, superseded_by__isnull=True)}


def sync_envelope(building, author_sub='', author_name=''):
    """Aligne les faits « enveloppe experte » sur l'enveloppe actuelle. Rend {'ajoutes', 'retires'}."""
    values = envelope_values(building)
    active = _active(building)
    added, retired = [], []
    for k, f in active.items():
        if values is None or k not in values or f.value != values[k]:
            facts.retire(f, author_sub or 'enveloppe')
            retired.append(k)
    for k, v in (values or {}).items():
        if k in active and active[k].value == v:
            continue
        facts.record(building, k, v, 'declare', source=SOURCE_ENVELOPE, author_sub=author_sub,
                     author_name=author_name, refresh=False)
        added.append(k)
    if added or retired:
        facts.refresh_scores(building)
    return {'ajoutes': added, 'retires': [k for k in retired if k not in added]}


# ── Environnement relevé → horizon par façade (N0/N1) ───────────────────────

def env_horizon(building, footprint_xy, eave_m):
    """Horizon par façade (degrés) et son profil (`api.horizon`) depuis les objets de l'environnement relié
    (bâtiments et arbres) : `{'horizon_deg', 'horizon_profil'}`, ou None si rien d'exploitable."""
    env = building.environment
    if env is None or not footprint_xy:
        return None
    import shapely.geometry
    from . import horizon as hz
    poly = shapely.geometry.Polygon(footprint_xy)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area < 1:
        return None
    from . import croisement
    mine = croisement.studied(building, env, poly)
    z0 = min((float((o.get('info') or {}).get('base_z', 0.0) or 0.0) for o in mine), default=None)
    obstacles, buildings = croisement.obstacles(env, {o['id'] for o in mine}, z0)
    if not obstacles:
        return None
    degs, prof = hz.profile(poly, obstacles, eave_m, adjacency=[p for p, _h in buildings],
                            north_deg=building.georef_north_offset_deg or 0.0)
    return {'horizon_deg': degs, 'horizon_profil': prof}


# ── Façades analysées → propositions ────────────────────────────────────────

def facade_proposals(building, owner_sub):
    """Après une analyse des façades : document local et propositions de taux de vitrage par orientation.
    Rend le document, ou None si aucune façade n'a été vue sur photo."""
    from django.utils import timezone

    from . import documents
    from .models import AIExtractionRun, BuildingDocument
    data = (building.facades or {}).get('facades') or {}
    area = {o: 0.0 for o in ORIENTATIONS}
    glass = {o: 0.0 for o in ORIENTATIONS}
    n_open = {o: 0 for o in ORIENTATIONS}
    for f in data.values():
        if f.get('status') not in ('analysee', 'partielle') or not f.get('area'):
            continue
        o = cardinal_of(float(f.get('azimuth') or 0.0) % 360)
        area[o] += float(f['area'])
        for op in f.get('openings') or []:
            if op.get('label') == 'door':
                continue
            glass[o] += max(0.0, (op['s1'] - op['s0']) * (op['t1'] - op['t0']))
            n_open[o] += 1
    lines, rows = [], []
    for o in ORIENTATIONS:
        if area[o] <= 0:
            continue
        ratio = round(min(glass[o] / area[o], 0.9), 3)
        line = (f'Façade {o} : {n_open[o]} baie(s) détectée(s), {glass[o]:.1f} m² vitrés sur {area[o]:.1f} m² vus, '
                f'taux {ratio:.3f}')
        lines.append(line)
        rows.append((f'baies.ratio_{o}', ratio, 1, line))
    if not rows:
        return None
    # Une nouvelle analyse remplace les propositions encore en attente de la précédente.
    for old in building.documents.filter(kind='facades', owner_sub=owner_sub):
        if not old.runs.filter(proposals__decision='acceptee').exists():
            old.delete()
    stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
    doc = BuildingDocument.objects.create(building=building, owner_sub=owner_sub, kind='facades',
                                          filename=f'Analyse des façades (Panoramax) du {stamp}'[:200],
                                          pages=['\n'.join(lines)])
    run = AIExtractionRun.objects.create(document=doc, owner_sub=owner_sub, provider='local', status='DONE',
                                         message='Baies détectées sur les photos de rue, sans IA')
    documents.make_proposals(run, building, rows, doc.pages)
    return doc
