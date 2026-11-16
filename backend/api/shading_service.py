"""Protections solaires d'un bâtiment appliquées aux calculs (Lot 14, refonte — § B4).

- N0/N1 : `apply(params, building, scenario)` pose la liste des protections sur les paramètres ;
  `engines.shading` calcule les facteurs horaires avec la météo.
- N2 : `geometric_occluders(building)` construit, pour chaque protection fixe en mode géométrique (toutes les
  fixes géométrisables pour un N2 lancé depuis la fiche, lot 36), des panneaux (triangles) au-dessus ou à côté des
  baies de la façade visée ; ils sont ajoutés aux obstacles du lancer de rayons (précalcul et temps réel). Les
  autres protections (mobiles, lames, vitrages, végétation) sont appliquées par triangle de baie
  (`engines.n2_inputs.mobile_factors`, `building_solver`).
"""

import math

from .fingerprint import stable_hash

ORIENT_AZ = {'N': 0.0, 'E': 90.0, 'S': 180.0, 'O': 270.0}
VALID_ORIENTATIONS = ('N', 'E', 'S', 'O', 'all')


def devices_of(building, scenario=None):
    from django.db.models import Q
    qs = building.shading_devices.all()
    qs = qs.filter(scenario__isnull=True) if scenario is None else qs.filter(Q(scenario__isnull=True) | Q(scenario=scenario))
    return [{'id': d.pk, 'kind': d.kind, 'orientation': d.orientation, 'mode': d.mode, 'params': d.params,
             'control': d.control} for d in qs.order_by('pk')]


def apply(params, building, scenario=None):
    if building is not None and building.pk:
        params.devices = devices_of(building, scenario)
    return params


def signature(building, scenario=None):
    devs = devices_of(building, scenario) if building is not None and building.pk else []
    return stable_hash([{k: v for k, v in d.items() if k != 'id'} for d in devs]) if devs else ''


# ── Obstacles géométriques pour le N2 ───────────────────────────────────────

def _facade_windows(building, orientation, glazing_ids, envelope=None):
    """Rectangles (dans le repère local) des groupes de vitrage des murs d'une orientation.
    `envelope` : enveloppe à utiliser à la place de celle du bâtiment (N2 depuis la fiche, Lot 23)."""
    env = (envelope if envelope is not None else building.envelope) or {}
    V = env.get('vertices') or []
    north = building.georef_north_offset_deg or 0.0
    target = (ORIENT_AZ[orientation] - north) % 360
    groups = {}
    for t in env.get('triangles') or []:
        if t.get('paroi_model_id') not in glazing_ids or not (60 < t.get('tilt_deg', 90) < 120):
            continue
        d = abs((t.get('azimuth_deg', 0) - target + 180) % 360 - 180)
        if d > 45:
            continue
        key = t.get('group') or 'baie'
        groups.setdefault(key, {'pts': [], 'normal': t['normal']})
        groups[key]['pts'] += [V[i] for i in t['v']]
    out = []
    for g in groups.values():
        n = g['normal']
        nx, ny = n[0], n[1]
        norm = math.hypot(nx, ny) or 1.0
        nx, ny = nx / norm, ny / norm
        tx, ty = -ny, nx  # tangente horizontale de la façade
        us = [p[0] * tx + p[1] * ty for p in g['pts']]
        zs = [p[2] for p in g['pts']]
        ds = [p[0] * nx + p[1] * ny for p in g['pts']]
        out.append({'u0': min(us), 'u1': max(us), 'z0': min(zs), 'z1': max(zs), 'd': max(ds), 'n': (nx, ny), 't': (tx, ty)})
    return out


def _quad(a, b, c, d, verts, tris):
    i = len(verts)
    verts += [a, b, c, d]
    tris += [{'v': [i, i + 1, i + 2]}, {'v': [i, i + 2, i + 3]}]


def geometric_occluders(building, scenario=None, envelope=None, all_fixed=False):
    """Enveloppe d'obstacles {'vertices', 'triangles'} des protections fixes construites en géométrie.

    Débords (casquette, balcon, débord de toiture, plafond de loggia) : panneau d'épaisseur nulle à la sous-face,
    profondeur D (projection horizontale), incliné de β (bord extérieur abaissé de D·tan β), débordant de e_g à
    gauche et e_d à droite de la baie (vu de l'extérieur ; repli : 2 m de chaque côté, comme avant le lot 36).
    Joues : panneaux verticaux de profondeur F, de l'appui à la hauteur H_j (toute la baie à défaut), côté(s) choisi(s).
    `all_fixed` : toutes les fixes géométrisables, quel que soit leur mode (N2 depuis la fiche, lot 36) ; sinon
    seulement celles en mode géométrique (page Calcul 3D, ombrage précalculé).
    `envelope` : celle du calcul si elle n'est pas `building.envelope` (N2 depuis la fiche, Lot 23)."""
    from .engines import shading
    from .models import ParoiModel
    devs = [d for d in devices_of(building, scenario) if d['kind'] in shading.GEOMETRIC and
            (all_fixed or d['mode'] == 'geometrique')]
    if not devs:
        return None
    glazing_ids = set(ParoiModel.objects.filter(is_glazing=True).values_list('pk', flat=True)) | \
        {pid for pid, layers in ParoiModel.objects.values_list('pk', 'layers') if any((l.get('tau') or 0) > 0 for l in layers)} | \
        {'fiche:vitrage'}
    verts, tris = [], []
    for dev in devs:
        kind = dev['kind']
        for o in (['N', 'E', 'S', 'O'] if dev['orientation'] == 'all' else [dev['orientation']]):
            for w in _facade_windows(building, o, glazing_ids, envelope):
                (nx, ny), (tx, ty) = w['n'], w['t']

                def pt(u, d, z):
                    return [u * tx + d * nx, u * ty + d * ny, z]
                d0 = w['d'] + 0.01
                depth = float(shading.param(dev, 'depth_m') or 0.0)
                if kind in shading.OVERHANGS and depth > 0:
                    left, right = shading.extensions(dev)
                    left = 2.0 if left is None else left
                    right = 2.0 if right is None else right
                    tilt = float(shading.param(dev, 'inclinaison_deg') or 0.0) if kind != 'loggia' else 0.0
                    z = w['z1'] + float(shading.param(dev, 'gap_m') or 0.0)
                    z_out = z - depth * math.tan(math.radians(tilt))
                    d1 = d0 + depth
                    # u croît vers la droite vu de l'extérieur : la gauche est du côté u0.
                    _quad(pt(w['u0'] - left, d0, z), pt(w['u1'] + right, d0, z), pt(w['u1'] + right, d1, z_out),
                          pt(w['u0'] - left, d1, z_out), verts, tris)
                fin = float(shading.param(dev, 'fin_depth_m') or 0.0)
                if kind in shading.FINS and fin > 0:
                    d1 = d0 + fin
                    fh = shading.param(dev, 'fin_height_m') if kind == 'joues' else None
                    z_top = w['z1'] if fh is None else w['z0'] + float(fh)
                    sides = (shading.param(dev, 'fin_cote') or 'deux') if kind == 'joues' else 'deux'
                    us = {'deux': (w['u0'], w['u1']), 'gauche': (w['u0'],), 'droite': (w['u1'],)}[sides]
                    for u in us:
                        _quad(pt(u, d0, w['z0']), pt(u, d1, w['z0']), pt(u, d1, z_top), pt(u, d0, z_top), verts, tris)
    return {'vertices': verts, 'triangles': tris} if tris else None


def merge_envelopes(*envs):
    verts, tris = [], []
    for e in envs:
        if not e or not e.get('triangles'):
            continue
        off = len(verts)
        verts += e['vertices']
        tris += [{**t, 'v': [i + off for i in t['v']]} for t in e['triangles']]
    return {'vertices': verts, 'triangles': tris} if tris else None
