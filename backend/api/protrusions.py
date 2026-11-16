"""Parties de l'enveloppe hors du volume chauffé — protubérances (Lot 33).

Le N2 relie chaque triangle de l'enveloppe au nœud d'air intérieur : sa face « intérieure » échange avec l'air
chauffé. C'est juste pour une paroi qui borne le volume chauffé, aberrant pour un **mur qui dépasse** du bâtiment
(mur de clôture ou de refend prolongé, ailette, acrotère, cheminée, double face d'une paroi de maillage) : ses deux
faces sont dehors, et il ne doit rien échanger avec l'intérieur (propriétaire, 2026-11-16).

Détection, pour chaque triangle, par un rayon lancé depuis son centre vers l'intérieur (sens opposé à sa normale
sortante) sur le maillage du bâtiment :
- il **ressort sans rien rencontrer** : le triangle ne borne aucun volume (face isolée, maillage ouvert) ;
- il rencontre, **à moins de `THIN_M`**, une face qui lui est opposée (normales quasi opposées) : le triangle est
  une face d'un élément mince — personne ne chauffe un volume de moins de 80 cm d'épaisseur (un coin en biseau, pan
  de toit contre mur près de l'égout, n'en est pas un) ;
- la première face rencontrée l'est **par l'extérieur** (sa normale fait face au rayon) : l'espace traversé n'était
  pas un volume fermé — c'est le bout d'un mur qui dépasse, qui bute sur la façade.
Garde-fou : si ces triangles dépassent `MAX_SHARE` de l'aire de l'enveloppe, le maillage suit une autre convention
(murs épais modélisés en deux faces, maillage très ouvert) : rien n'est reclassé et l'avertissement le dit.

Un triangle « hors volume » (`boundary = 'hors_volume'`) reste dans le calcul comme une ailette : il conduit, stocke,
reçoit le soleil et porte ombre, mais ses **deux faces** échangent avec l'air extérieur ; il n'entre ni dans le
flux de l'enveloppe ni dans la température radiante intérieure (`building_solver`).
"""

import numpy as np

THIN_M = 0.8
ANTIPARALLEL = 0.9      # cos de l'angle : faces opposées d'une même plaque
MAX_SHARE = 0.5
EPS_M = 1e-3
BOUNDARY = 'hors_volume'


def detect(vertices, triangles):
    """Indices des triangles hors du volume chauffé, et compte rendu {n, aire_m2, part, motif, avertissement}."""
    import trimesh
    V = np.asarray(vertices, dtype=float)
    F = np.asarray([t['v'] for t in triangles], dtype=int)
    if len(F) == 0:
        return [], {'n': 0, 'aire_m2': 0.0, 'part': 0.0}
    mesh = trimesh.Trimesh(V, F, process=False)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(n, axis=1)
    normal = n / np.maximum(2 * area, 1e-12)[:, None]
    centre = (a + b + c) / 3.0
    origins = centre - normal * EPS_M
    dirs = -normal
    candidates = [i for i, t in enumerate(triangles)
                  if t.get('boundary', 'exterior_air') in ('exterior_air', BOUNDARY) and area[i] > 1e-6]
    if not candidates:
        return [], {'n': 0, 'aire_m2': 0.0, 'part': 0.0}
    idx = np.asarray(candidates)
    tri_hit, ray_hit, loc = mesh.ray.intersects_id(origins[idx], dirs[idx], multiple_hits=True, return_locations=True)
    nearest = np.full(len(idx), np.inf)
    first = np.full(len(idx), -1)
    for t_i, r_i, p in zip(tri_hit, ray_hit, loc):
        if t_i == idx[r_i]:
            continue
        d = float(np.dot(p - origins[idx[r_i]], dirs[idx[r_i]]))
        if d > EPS_M / 2 and d < nearest[r_i]:
            nearest[r_i], first[r_i] = d, t_i
    out = []
    reasons = {'ouvert': 0, 'mince': 0, 'bute': 0}
    for k in range(len(idx)):
        if not np.isfinite(nearest[k]):
            out.append(int(idx[k]))
            reasons['ouvert'] += 1
        elif nearest[k] < THIN_M and float(np.dot(normal[first[k]], normal[idx[k]])) < -ANTIPARALLEL:
            # Les deux faces d'une même plaque (normales opposées) : pas un coin en biseau du volume (pan de toit qui
            # rejoint un mur près de l'égout, vu sur les enveloppes LiDAR).
            out.append(int(idx[k]))
            reasons['mince'] += 1
        elif float(np.dot(normal[first[k]], dirs[idx[k]])) < 0:
            # Le rayon rencontre une face par l'extérieur : l'espace traversé n'était pas un volume fermé
            # (bout d'un mur qui dépasse, qui bute sur la façade).
            out.append(int(idx[k]))
            reasons['bute'] += 1
    total = float(area.sum()) or 1.0
    share = float(area[out].sum()) / total if out else 0.0
    report = {'n': len(out), 'aire_m2': round(float(area[out].sum()), 1) if out else 0.0, 'part': round(share, 3),
              'seuil_m': THIN_M, 'motifs': reasons}
    if share > MAX_SHARE:
        report.update(n=0, aire_m2=0.0, avertissement=(
            f'{share:.0%} de l’enveloppe ne borne aucun volume de plus de {THIN_M:.1f} m : maillage hors convention '
            '(murs épais en deux faces, ou maillage ouvert). Aucune paroi n’a été retirée du volume chauffé.'))
        return [], report
    return out, report


def mark(envelope):
    """Copie de l'enveloppe où les triangles hors volume portent `boundary = 'hors_volume'`, et le compte rendu."""
    tris = (envelope or {}).get('triangles') or []
    out, report = detect(envelope['vertices'], tris)
    if not out:
        return envelope, report
    keep = set(out)
    new = [dict(t, boundary=BOUNDARY) if i in keep else t for i, t in enumerate(tris)]
    return {**envelope, 'triangles': new}, report
