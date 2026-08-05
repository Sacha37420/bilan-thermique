"""Précalcul de la visibilité solaire de chaque triangle d'un bâtiment.

Approche (voir discussion Lot C) : tester un rayon par (triangle × position
solaire) en temps réel serait intraitable sur un run météo horaire complet.
On précalcule donc, une fois, la visibilité de chaque triangle sur une grille
grossière (azimut, élévation), réutilisée ensuite par lookup à chaque heure
réelle (Lot D) plutôt que relancée à chaque pas.

Occlusion testée contre l'enveloppe du bâtiment ET l'environnement fournis en
entrée (auto-ombrage inclus, décidé lors du Lot B) — le fusionnement des deux
maillages est fait ici, jamais stocké fusionné en base.

Convention : même repère que api.geometry — Z-up, azimuth 0° = +Y, sens
horaire vu de dessus, élévation 0..90° (le soleil sous l'horizon n'est pas
dans la grille : à l'usage, une élévation <= 0 signifie "pas de soleil",
inutile d'interroger la grille — même logique que solver._assemble_F).
"""

import math

import numpy as np
import trimesh

DEFAULT_AZIMUTH_STEP_DEG = 15.0
DEFAULT_ELEVATION_STEP_DEG = 15.0

# Décalage de l'origine du rayon le long de la normale, pour éviter une
# auto-intersection numérique avec le triangle d'origine lui-même.
RAY_ORIGIN_EPSILON = 1e-3

MAX_TRIANGLES_FOR_SHADOW = 20_000  # cohérent avec api.geometry.MAX_TRIANGLES


class ShadowError(ValueError):
    pass


def build_occluder_mesh(*envelopes):
    """Fusionne un ou plusieurs {'vertices':[[x,y,z],...], 'triangles':[{'v':[i,j,k]},...]}
    en un unique trimesh.Trimesh, pour servir de scène d'occlusion."""
    vertices = []
    faces = []
    for env in envelopes:
        if not env:
            continue
        base = len(vertices)
        vertices.extend(env['vertices'])
        for tri in env['triangles']:
            i, j, k = tri['v']
            faces.append([base + i, base + j, base + k])

    if not faces:
        raise ShadowError("Aucun triangle occulteur (bâtiment + environnement vides).")

    return trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=float),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )


def sun_direction(azimuth_deg, elevation_deg):
    """Vecteur unitaire pointant du sol VERS le soleil (même convention que
    api.geometry.compute_triangle_geometry : azimuth 0°=+Y, 90°=+X)."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    return np.array([
        math.sin(az) * math.cos(el),
        math.cos(az) * math.cos(el),
        math.sin(el),
    ])


def compute_visibility_grid(
    building_envelope,
    environment_envelope=None,
    azimuth_step_deg=DEFAULT_AZIMUTH_STEP_DEG,
    elevation_step_deg=DEFAULT_ELEVATION_STEP_DEG,
    progress_cb=None,
):
    """Calcule, pour chaque triangle de building_envelope, sa visibilité
    solaire sur la grille (azimuth, elevation). progress_cb(done, total),
    si fourni, est appelé après chaque position solaire traitée.

    Retourne {'azimuths_deg': [...], 'elevations_deg': [...], 'per_triangle': [...]}
    où per_triangle[i][a][e] vaut 1 si le triangle i voit le soleil à la
    position (azimuths_deg[a], elevations_deg[e]), 0 sinon.
    """
    triangles = building_envelope['triangles']
    n_tri = len(triangles)
    if n_tri == 0:
        raise ShadowError("Le bâtiment n'a aucun triangle.")
    if n_tri > MAX_TRIANGLES_FOR_SHADOW:
        raise ShadowError(f"{n_tri} triangles, au-delà de la limite de {MAX_TRIANGLES_FOR_SHADOW}.")

    occluder = build_occluder_mesh(building_envelope, environment_envelope)
    intersector = trimesh.ray.ray_triangle.RayMeshIntersector(occluder)

    azimuths = [float(a) for a in np.arange(0.0, 360.0, azimuth_step_deg)]
    elevations = [float(e) for e in np.arange(0.0, 90.0 + 1e-9, elevation_step_deg)]

    vertices = building_envelope['vertices']
    centroids = np.zeros((n_tri, 3))
    normals = np.zeros((n_tri, 3))
    for i, tri in enumerate(triangles):
        p = np.array([vertices[j] for j in tri['v']])
        n = np.array(tri['normal'])
        centroids[i] = p.mean(axis=0) + n * RAY_ORIGIN_EPSILON
        normals[i] = n

    # per_triangle[i][a][e]
    per_triangle = np.zeros((n_tri, len(azimuths), len(elevations)), dtype=np.int8)

    total_cells = len(azimuths) * len(elevations)
    done_cells = 0
    for ai, az in enumerate(azimuths):
        for ei, el in enumerate(elevations):
            direction = sun_direction(az, el)
            facing = normals @ direction > 1e-6
            if facing.any():
                idx = np.nonzero(facing)[0]
                origins = centroids[idx]
                directions = np.tile(direction, (len(idx), 1))
                blocked = intersector.intersects_any(origins, directions)
                per_triangle[idx[~blocked], ai, ei] = 1
            done_cells += 1
            if progress_cb:
                progress_cb(done_cells, total_cells)

    return {
        'azimuths_deg': azimuths,
        'elevations_deg': elevations,
        'per_triangle': per_triangle.tolist(),
    }


def lookup_visibility(sun_visibility, triangle_index, azimuth_deg, elevation_deg):
    """Consultation rapide (Lot D) : la case de grille la plus proche de
    (azimuth_deg, elevation_deg) pour un triangle donné. Retourne True/False.
    """
    azimuths = sun_visibility['azimuths_deg']
    elevations = sun_visibility['elevations_deg']

    az_step = azimuths[1] - azimuths[0] if len(azimuths) > 1 else 360.0
    ai = round((azimuth_deg % 360.0) / az_step) % len(azimuths)

    el_clamped = min(max(elevation_deg, elevations[0]), elevations[-1])
    ei = min(range(len(elevations)), key=lambda k: abs(elevations[k] - el_clamped))

    return bool(sun_visibility['per_triangle'][triangle_index][ai][ei])
