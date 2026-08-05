"""Géométrie des triangles d'une enveloppe de bâtiment.

Convention : axe vertical = Z (Z-up). Un triangle horizontal tourné vers le
ciel (toiture plate) a une normale (0,0,1) et un tilt de 0° ; un mur vertical
a une normale horizontale et un tilt de 90° — même convention que
wall_tilt_deg dans solver.py (béta = 90° mur vertical, 0° toiture plate).

Azimuth : direction de la normale dans le plan horizontal, 0° = +Y (« nord »
conventionnel de ce repère), sens horaire vu de dessus (+X = « est »). Cette
convention est provisoire — à confirmer avec la donnée météo réelle
(orientation du soleil) lors du Lot D, qui est le premier endroit où
l'azimuth est effectivement consommé.
"""

import math

MAX_VERTICES = 20_000
MAX_TRIANGLES = 20_000


class GeometryError(ValueError):
    pass


def compute_triangle_geometry(vertices, triangle_indices):
    """vertices : liste de [x,y,z]. triangle_indices : [i0,i1,i2].
    Retourne {area, normal: [x,y,z], tilt_deg, azimuth_deg}.
    """
    i0, i1, i2 = triangle_indices
    p0, p1, p2 = vertices[i0], vertices[i1], vertices[i2]

    e1 = [p1[k] - p0[k] for k in range(3)]
    e2 = [p2[k] - p0[k] for k in range(3)]
    cross = [
        e1[1] * e2[2] - e1[2] * e2[1],
        e1[2] * e2[0] - e1[0] * e2[2],
        e1[0] * e2[1] - e1[1] * e2[0],
    ]
    norm = math.sqrt(sum(c * c for c in cross))
    if norm < 1e-12:
        raise GeometryError(f"Triangle dégénéré (aire nulle) : sommets {i0},{i1},{i2}.")

    area = 0.5 * norm
    normal = [c / norm for c in cross]

    tilt_deg = math.degrees(math.acos(max(-1.0, min(1.0, normal[2]))))
    azimuth_deg = math.degrees(math.atan2(normal[0], normal[1])) % 360.0

    return {
        'area': area,
        'normal': normal,
        'tilt_deg': tilt_deg,
        'azimuth_deg': azimuth_deg,
    }


def compute_envelope_geometry(vertices, triangles):
    """Recalcule area/normal/tilt_deg/azimuth_deg pour chaque triangle,
    en place sur une copie. Lève GeometryError si un index est invalide ou un
    triangle dégénéré.
    """
    n_vertices = len(vertices)
    if n_vertices > MAX_VERTICES:
        raise GeometryError(f"{n_vertices} sommets, au-delà de la limite de {MAX_VERTICES}.")
    if len(triangles) > MAX_TRIANGLES:
        raise GeometryError(f"{len(triangles)} triangles, au-delà de la limite de {MAX_TRIANGLES}.")

    out = []
    for idx, tri in enumerate(triangles):
        v = tri['v']
        for i in v:
            if not (0 <= i < n_vertices):
                raise GeometryError(f"Triangle {idx} : indice de sommet {i} invalide (0..{n_vertices - 1}).")
        geom = compute_triangle_geometry(vertices, v)
        out.append({**tri, **geom})
    return out


def validate_indices(vertices, triangles):
    """Vérifie seulement les bornes d'indices, sans calculer de géométrie —
    pour un maillage d'environnement (Environment), dont les triangles ne
    servent qu'au test d'occlusion (api.shadow), jamais discrétisés en 1D."""
    n_vertices = len(vertices)
    if n_vertices > MAX_VERTICES:
        raise GeometryError(f"{n_vertices} sommets, au-delà de la limite de {MAX_VERTICES}.")
    if len(triangles) > MAX_TRIANGLES:
        raise GeometryError(f"{len(triangles)} triangles, au-delà de la limite de {MAX_TRIANGLES}.")
    for idx, tri in enumerate(triangles):
        for i in tri['v']:
            if not (0 <= i < n_vertices):
                raise GeometryError(f"Triangle {idx} : indice de sommet {i} invalide (0..{n_vertices - 1}).")
