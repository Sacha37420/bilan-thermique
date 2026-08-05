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


MAX_REFINE_ITERATIONS = 20


def _canonical_edge(i, j):
    return (i, j) if i < j else (j, i)


def _edge_length(vertices, edge):
    i, j = edge
    return math.dist(vertices[i], vertices[j])


def refine_envelope(vertices, triangles, max_edge_length):
    """Subdivise les triangles dont un côté dépasse max_edge_length, en
    préservant la qualité (pas de triangles très allongés) et la conformité
    du maillage (pas de fissure entre triangles voisins).

    Principe : dès qu'UN côté d'un triangle est trop long, ses TROIS côtés
    sont marqués à diviser (propagés par voisinage jusqu'à un point fixe),
    puis chaque triangle marqué subit une division "rouge" classique en 4
    sous-triangles semblables (mêmes proportions que le parent, à l'échelle
    1/2) via les milieux de ses 3 côtés. Répété jusqu'à ce qu'aucun côté ne
    dépasse le seuil. Toujours une division complète (jamais de division
    "verte" partielle à 2 ou 3 triangles) : plus de triangles que le strict
    minimum dans certains cas, mais aucun risque de créer un triangle très
    étiré — exactement ce qu'on cherche à éviter.

    triangles : liste de dicts avec au moins 'v' — les autres clés (group,
    paroi_model_id) sont copiées telles quelles sur chaque enfant, PAS les
    champs géométriques calculés (area/normal/tilt_deg/azimuth_deg), à
    recalculer par l'appelant via compute_envelope_geometry.

    Retourne (nouveaux_sommets, nouveaux_triangles).
    """
    if max_edge_length <= 0:
        raise GeometryError("max_edge_length doit être strictement positif.")

    vertices = [list(v) for v in vertices]
    triangles = [{'v': list(t['v']), **{k: v for k, v in t.items() if k not in ('v', 'area', 'normal', 'tilt_deg', 'azimuth_deg')}}
                 for t in triangles]

    for _iteration in range(MAX_REFINE_ITERATIONS):
        edge_to_triangles = {}
        for ti, tri in enumerate(triangles):
            v = tri['v']
            for e in (_canonical_edge(v[0], v[1]), _canonical_edge(v[1], v[2]), _canonical_edge(v[2], v[0])):
                edge_to_triangles.setdefault(e, []).append(ti)

        seed_long = [e for e in edge_to_triangles if _edge_length(vertices, e) > max_edge_length]
        if not seed_long:
            break

        # Propagation par largeur : un triangle avec un côté marqué a ses
        # TROIS côtés marqués — répercuté aux voisins jusqu'à stabilité.
        to_split = set(seed_long)
        queue = list(seed_long)
        triangle_processed = set()
        qi = 0
        while qi < len(queue):
            e = queue[qi]
            qi += 1
            for ti in edge_to_triangles[e]:
                if ti in triangle_processed:
                    continue
                triangle_processed.add(ti)
                v = triangles[ti]['v']
                for e2 in (_canonical_edge(v[0], v[1]), _canonical_edge(v[1], v[2]), _canonical_edge(v[2], v[0])):
                    if e2 not in to_split:
                        to_split.add(e2)
                        queue.append(e2)

        if len(vertices) + len(to_split) > MAX_VERTICES or len(triangles) * 4 > MAX_TRIANGLES:
            raise GeometryError(
                f"Le raffinement à {max_edge_length} m dépasserait la limite de {MAX_TRIANGLES} triangles — "
                "augmenter la taille maximale."
            )

        midpoint_of = {}
        for e in to_split:
            i, j = e
            midpoint_of[e] = len(vertices)
            vertices.append([(vertices[i][k] + vertices[j][k]) / 2.0 for k in range(3)])

        new_triangles = []
        for tri in triangles:
            v = tri['v']
            e01, e12, e20 = _canonical_edge(v[0], v[1]), _canonical_edge(v[1], v[2]), _canonical_edge(v[2], v[0])
            if e01 not in to_split:
                new_triangles.append(tri)
                continue
            m01, m12, m20 = midpoint_of[e01], midpoint_of[e12], midpoint_of[e20]
            extra = {k: val for k, val in tri.items() if k != 'v'}
            new_triangles.append({'v': [v[0], m01, m20], **extra})
            new_triangles.append({'v': [v[1], m12, m01], **extra})
            new_triangles.append({'v': [v[2], m20, m12], **extra})
            new_triangles.append({'v': [m01, m12, m20], **extra})

        triangles = new_triangles
    else:
        raise GeometryError(
            f"Le raffinement n'a pas convergé après {MAX_REFINE_ITERATIONS} itérations — "
            "max_edge_length est probablement beaucoup trop petit."
        )

    return vertices, triangles


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
