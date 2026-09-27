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

    Principe (raffinement « rouge-vert-bleu » par le plus long côté) : les
    côtés trop longs sont marqués ; tout triangle qui a un côté marqué voit
    aussi son PLUS LONG côté marqué (propagé aux voisins jusqu'à un point
    fixe). Chaque triangle est alors coupé selon le nombre de côtés marqués :
    1 (forcément le plus long) → 2 enfants par son milieu, 2 → 3 enfants,
    3 → 4 enfants semblables via les milieux. Couper toujours par le plus long
    côté borne la dégradation des angles, et un milieu partagé par les deux
    triangles voisins d'un côté garantit l'absence de fissure. Répété jusqu'à
    ce qu'aucun côté ne dépasse le seuil.

    Le raffinement reste LOCAL : l'ancienne version marquait les trois côtés
    de tout triangle touché, ce qui se propageait de proche en proche à tout
    le maillage à chaque itération — un mur percé de petites fenêtres
    (triangulation fine autour des baies, Lot AK) et d'un côté de 37 m
    faisait alors quadrupler l'ensemble jusqu'à dépasser la limite de
    triangles, même à 7 m de maille.

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

        def longest(tri):
            v = tri['v']
            edges = (_canonical_edge(v[0], v[1]), _canonical_edge(v[1], v[2]), _canonical_edge(v[2], v[0]))
            return max(edges, key=lambda e: (_edge_length(vertices, e), e))

        to_split = set(seed_long)
        queue = list(seed_long)
        qi = 0
        while qi < len(queue):
            e = queue[qi]
            qi += 1
            for ti in edge_to_triangles[e]:
                le = longest(triangles[ti])
                if le not in to_split:
                    to_split.add(le)
                    queue.append(le)

        n_new = 0
        for tri in triangles:
            v = tri['v']
            n_new += 1 + sum(_canonical_edge(v[i], v[(i + 1) % 3]) in to_split for i in range(3))
        if len(vertices) + len(to_split) > MAX_VERTICES or n_new > MAX_TRIANGLES:
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
            marked = [_canonical_edge(v[i], v[(i + 1) % 3]) in to_split for i in range(3)]
            if not any(marked):
                new_triangles.append(tri)
                continue
            extra = {k: val for k, val in tri.items() if k != 'v'}
            if all(marked):
                m01 = midpoint_of[_canonical_edge(v[0], v[1])]
                m12 = midpoint_of[_canonical_edge(v[1], v[2])]
                m20 = midpoint_of[_canonical_edge(v[2], v[0])]
                children = [[v[0], m01, m20], [v[1], m12, m01], [v[2], m20, m12], [m01, m12, m20]]
            else:
                # Rotation (orientation conservée) pour que le plus long côté,
                # toujours marqué ici, soit a→b.
                le = longest(tri)
                r = next(i for i in range(3) if _canonical_edge(v[i], v[(i + 1) % 3]) == le)
                a, b, c = v[r], v[(r + 1) % 3], v[(r + 2) % 3]
                m = midpoint_of[le]
                bc, ca = _canonical_edge(b, c), _canonical_edge(c, a)
                children = []
                if bc in to_split:
                    n = midpoint_of[bc]
                    children += [[m, b, n], [m, n, c]]
                else:
                    children.append([m, b, c])
                if ca in to_split:
                    p = midpoint_of[ca]
                    children += [[a, m, p], [p, m, c]]
                else:
                    children.append([a, m, c])
            new_triangles.extend({'v': ch, **extra} for ch in children)

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
