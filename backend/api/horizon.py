"""Horizon des façades (Lot 30) : profil d'obstruction par tranche d'azimut, mesuré depuis les façades.

Remplace la règle d'avant — le plus haut voisin d'un cône de ±45° autour du *centre* du bâtiment,
appliqué à *toute* la façade, distance ramenée à 1 m au moins, direct coupé dès que le soleil passait
sous cet angle quel que soit son azimut. Elle comptait comme masque un voisin accolé (mitoyen) et un
immeuble décalé de côté : 71° à 85° sur des façades de centre-ville qui voient en réalité le soleil
une partie de la journée (constaté à Tours, 2026-11-16).

Profil, pour chaque orientation (N/E/S/O, mêmes groupes de murs que la chaîne) :
- des points sur les murs **exposés** (tous les 3 m, 8 au plus, rien le long d'un mur mitoyen), à
  0,3 m devant le mur, à trois hauteurs (1/6, 1/2 et 5/6 de la hauteur à l'égout) ;
- pour chaque point et chaque tranche de 10° d'azimut (centrée sur un multiple de 10°) **devant** le mur (±85° autour de la normale),
  l'angle au-dessus de l'horizontale du plus haut obstacle que traverse le rayon, 80 m au plus ;
  derrière le mur la valeur est 0 (le soleil n'y éclaire pas la façade, rien à masquer).
Le bâtiment lui-même est un obstacle (aile d'un bâtiment en L).

Usage par les moteurs (`engines.solar.surface_irradiance`) : le direct d'une façade est multiplié par
la part des points pour lesquels le soleil est au-dessus du profil, à son azimut — une façade à
moitié à l'ombre reçoit la moitié du direct. `horizon_deg` (affichage) : angle des masques **face à** la façade
— moyenne du profil sur les points, les hauteurs et les tranches à ±15° de la normale.
Limite assumée : le diffus du ciel n'est pas réduit par les masques (comme avant).
"""

import math

import shapely
import shapely.geometry
import shapely.ops

STEP_DEG = 10
N_BINS = 360 // STEP_DEG
MAX_DIST_M = 80.0
SPACING_M = 3.0
MAX_POINTS = 8
HEIGHT_FRACTIONS = (1 / 6, 1 / 2, 5 / 6)
OFFSET_M = 0.3
FRONT_DEG = 85.0
DISPLAY_DEG = 15.0
ADJACENT_M = 0.6
ORIENTATIONS = ('N', 'E', 'S', 'O')


def _cardinal(az):
    from .bdnb import cardinal_of
    return cardinal_of(az % 360)


def _wrap(d):
    return (d + 180.0) % 360.0 - 180.0


def _points(poly, adjacency, north_deg):
    """Points d'observation par orientation : (x, y, azimut géographique de la normale)."""
    ring = shapely.geometry.polygon.orient(poly, sign=1.0).exterior
    coords = list(ring.coords)
    near = shapely.ops.unary_union([p.buffer(ADJACENT_M) for p in adjacency]) if adjacency else None
    out = {o: [] for o in ORIENTATIONS}
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.5:
            continue
        nx, ny = dy / length, -dx / length            # normale sortante (anneau trigonométrique)
        az_geo = (math.degrees(math.atan2(nx, ny)) + north_deg) % 360
        n = max(1, int(length // SPACING_M))
        for k in range(n):
            t = (k + 0.5) / n
            px, py = x1 + dx * t, y1 + dy * t
            if near is not None and near.contains(shapely.geometry.Point(px, py)):
                continue                               # le long d'un mitoyen : pas une façade exposée
            out[_cardinal(az_geo)].append((px + nx * OFFSET_M, py + ny * OFFSET_M, az_geo))
    for o, pts in out.items():
        if len(pts) > MAX_POINTS:                      # répartis le long de la façade, pas les premiers
            step = len(pts) / MAX_POINTS
            out[o] = [pts[int(i * step + step / 2)] for i in range(MAX_POINTS)]
    return out


def profile(poly, obstacles, eave_m, adjacency=(), north_deg=0.0):
    """Profil d'horizon d'un bâtiment.

    poly : emprise (Polygon, repère local) ; obstacles : [(Polygon, sommet_m)] — sommet au-dessus du sol
    du bâtiment étudié ; adjacency : polygones des voisins (un point à moins de 0,6 m de l'un d'eux est
    sur un mur mitoyen) ; north_deg : azimut géographique = azimut local + north_deg.
    Rend (horizon_deg {o: degrés}, profil {o: [[N_BINS entiers], …]} une ligne par point et par hauteur)."""
    poly = poly if poly.is_valid else poly.buffer(0)
    if poly.geom_type == 'MultiPolygon':
        poly = max(poly.geoms, key=lambda p: p.area)
    eave = max(float(eave_m or 0.0), 1.0)
    obs = [(p, float(h)) for p, h in obstacles if p is not None and not p.is_empty and h > 0]
    obs.append((poly, eave))                          # le bâtiment se masque lui-même (ailes)
    tree = shapely.STRtree([p for p, _h in obs])
    zs = [eave * f for f in HEIGHT_FRACTIONS]
    degs, prof = {}, {}
    for o, pts in _points(poly, list(adjacency), north_deg).items():
        rows, shown = [], []
        for px, py, az_n in pts:
            origin = shapely.geometry.Point(px, py)
            per_z = [[0] * N_BINS for _ in zs]
            for b in range(N_BINS):
                az_b = b * STEP_DEG                             # tranches centrées : 0° = nord, 18 = plein sud
                off = _wrap(az_b - az_n)
                if abs(off) > FRONT_DEG:
                    continue
                a = math.radians(az_b - north_deg)
                ray = shapely.geometry.LineString([(px, py), (px + MAX_DIST_M * math.sin(a), py + MAX_DIST_M * math.cos(a))])
                best = [0.0] * len(zs)
                for idx in tree.query(ray):
                    p, top = obs[int(idx)]
                    hit = ray.intersection(p)
                    if hit.is_empty:
                        continue
                    d = max(origin.distance(hit), 0.5)
                    for k, z in enumerate(zs):
                        if top > z:
                            best[k] = max(best[k], math.degrees(math.atan2(top - z, d)))
                for k in range(len(zs)):
                    per_z[k][b] = int(round(best[k]))
                    if abs(off) <= DISPLAY_DEG:
                        shown.append(best[k])
            rows.extend(per_z)
        prof[o] = rows
        degs[o] = round(sum(shown) / len(shown), 1) if shown else 0.0
    return degs, prof


def beam_visible(rows, az_rad, el_rad):
    """Part (0–1) des points d'une façade qui voient le soleil, heure par heure (tableaux numpy)."""
    import numpy as np
    if not rows:
        return None
    P = np.radians(np.asarray(rows, dtype=float))                    # (points, N_BINS)
    bins = ((np.degrees(az_rad) % 360 + STEP_DEG / 2.0) // STEP_DEG).astype(int) % N_BINS
    return (el_rad[None, :] >= P[:, bins]).mean(axis=0)
