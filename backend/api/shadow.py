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
    en un unique trimesh.Trimesh, pour servir de scène d'occlusion.

    N'inclut QUE les triangles opaques : depuis le Lot Z, un triangle
    d'environnement peut porter `k` (transmittance) et `obj` (identifiant de
    l'objet auquel il appartient), auquel cas il relève de
    build_vegetation_scene ci-dessous et non de ce maillage-ci."""
    vertices = []
    faces = []
    for env in envelopes:
        if not env:
            continue
        base = len(vertices)
        vertices.extend(env['vertices'])
        for tri in env['triangles']:
            if _is_translucent(tri):
                continue
            i, j, k = tri['v']
            faces.append([base + i, base + j, base + k])

    if not faces:
        raise ShadowError("Aucun triangle occulteur (bâtiment + environnement vides).")

    return trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=float),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )


def _is_translucent(tri):
    k = tri.get('k')
    return k is not None and k > 0.0


class VegetationScene:
    """Occulteurs NON opaques (Lot Z) : végétation, traitée comme un écran qui
    laisse passer une fraction `k` du rayonnement.

    Pourquoi une classe à part et non un simple champ de plus sur le maillage
    principal : un rayon qui traverse un arbre touche DEUX faces (entrée et
    sortie), et parfois plus sur une forme concave. Multiplier la transmittance
    à chaque face donnerait k² ou k³ au lieu de k. Il faut donc regrouper les
    faces touchées PAR OBJET (`obj`) et n'appliquer `k` qu'une fois par objet
    réellement traversé — ce que `intersects_id(multiple_hits=True)` permet,
    contrairement au `intersects_any` utilisé pour les opaques.

    Le chemin opaque reste inchangé et rapide : la végétation n'est interrogée
    que pour les rayons qui ont DÉJÀ passé le test opaque, et seulement s'il y a
    de la végétation.
    """

    def __init__(self, mesh, face_obj, obj_k):
        self.intersector = trimesh.ray.ray_triangle.RayMeshIntersector(mesh)
        self.face_obj = face_obj      # indice d'objet par face
        self.obj_k = obj_k            # transmittance par objet

    def transmittance(self, origins, directions):
        """Fraction transmise pour chaque rayon (1.0 = rien sur le trajet)."""
        n = len(origins)
        out = np.ones(n)
        if n == 0:
            return out
        tri_idx, ray_idx = self.intersector.intersects_id(
            origins, directions, multiple_hits=True,
        )
        if len(tri_idx) == 0:
            return out
        seen = set()
        for t, r in zip(tri_idx, ray_idx):
            key = (int(r), int(self.face_obj[t]))
            if key in seen:
                continue
            seen.add(key)
            out[r] *= self.obj_k[self.face_obj[t]]
        return out


def build_vegetation_scene(*envelopes):
    """Retourne une VegetationScene, ou None si aucun triangle translucide."""
    vertices = []
    faces = []
    face_obj = []
    obj_key_to_index = {}
    obj_k = []

    for env_index, env in enumerate(envelopes):
        if not env:
            continue
        base = len(vertices)
        vertices.extend(env['vertices'])
        for tri in env['triangles']:
            if not _is_translucent(tri):
                continue
            i, j, k = tri['v']
            faces.append([base + i, base + j, base + k])
            # `obj` est propre à chaque enveloppe : on le préfixe par l'indice
            # de l'enveloppe pour que deux environnements fusionnés ne
            # confondent pas leurs objets.
            key = (env_index, tri.get('obj'))
            if key not in obj_key_to_index:
                obj_key_to_index[key] = len(obj_k)
                obj_k.append(float(tri['k']))
            face_obj.append(obj_key_to_index[key])

    if not faces:
        return None

    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=float),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )
    return VegetationScene(mesh, np.asarray(face_obj, dtype=np.int64), np.asarray(obj_k, dtype=float))


def build_occluder_intersector(building_envelope, environment_envelope=None):
    """Prêt à l'emploi pour un test d'occlusion en temps réel (mode 'realtime'
    de building_solver) : construit le maillage occulteur une seule fois,
    réutilisable pour autant de rayons/heures qu'on veut."""
    mesh = build_occluder_mesh(building_envelope, environment_envelope)
    return trimesh.ray.ray_triangle.RayMeshIntersector(mesh)


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
    vegetation = build_vegetation_scene(building_envelope, environment_envelope)

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

    # per_triangle[i][a][e] — fraction de rayonnement direct atteignant le
    # triangle : 0 ou 1 sans végétation (le cas historique, stocké en int8 pour
    # ne pas gonfler le JSON de Building.sun_visibility), une fraction sinon.
    dtype = np.float32 if vegetation is not None else np.int8
    per_triangle = np.zeros((n_tri, len(azimuths), len(elevations)), dtype=dtype)

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
                visible = idx[~blocked]
                if vegetation is None:
                    per_triangle[visible, ai, ei] = 1
                elif len(visible):
                    # Seuls les rayons ayant passé le test opaque sont soumis au
                    # test (plus coûteux) de la végétation.
                    k = vegetation.transmittance(
                        centroids[visible], np.tile(direction, (len(visible), 1)),
                    )
                    per_triangle[visible, ai, ei] = np.round(k, 3)
            done_cells += 1
            if progress_cb:
                progress_cb(done_cells, total_cells)

    return {
        'azimuths_deg': azimuths,
        'elevations_deg': elevations,
        'per_triangle': per_triangle.tolist(),
    }


DEFAULT_SKY_SAMPLES = 128


def _fibonacci_sphere(n):
    """n directions quasi uniformément réparties sur la sphère unité (spirale
    de Fibonacci) — déterministe, bonne couverture même pour n modeste, pas
    besoin d'aléatoire pour un résultat reproductible."""
    points = np.empty((n, 3))
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n):
        y = 1.0 - (i / (n - 1)) * 2.0 if n > 1 else 0.0
        radius = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden_angle * i
        points[i] = (math.cos(theta) * radius, y, math.sin(theta) * radius)
    return points


def compute_sky_view_factors(building_envelope, environment_envelope=None, n_samples=DEFAULT_SKY_SAMPLES):
    """Facteur de vue du ciel de chaque triangle, corrigé de l'occlusion
    réelle par l'environnement (et l'auto-ombrage du bâtiment) — généralise
    la formule analytique (1+cos(tilt))/2 (ciel isotrope, sans obstacle)
    utilisée par défaut dans solver.py/building_solver.py.

    Méthode : on échantillonne n_samples directions sur la sphère, pondérées
    par cos(theta) par rapport à la normale du triangle (pondération de
    Lambert, celle qui redonne exactement la formule analytique en l'absence
    d'obstacle) et restreintes aux directions de "vrai ciel" (composante Z
    positive). Le facteur retourné est :

        F_ciel_obstrué = (poids visible / poids total) * (1+cos(tilt))/2

    — un ratio d'occlusion (issu du lancer de rayons) multipliant la valeur
    analytique exacte, plutôt qu'une intégrale Monte-Carlo brute : en
    l'absence totale d'obstacle, AUCUN rayon n'est bloqué quel que soit
    n_samples, donc le ratio vaut exactement 1 et le résultat est
    EXACTEMENT la formule analytique, sans bruit de discrétisation.

    Retourne une liste de floats, un par triangle de building_envelope.
    """
    triangles = building_envelope['triangles']
    n_tri = len(triangles)
    if n_tri == 0:
        raise ShadowError("Le bâtiment n'a aucun triangle.")
    if n_tri > MAX_TRIANGLES_FOR_SHADOW:
        raise ShadowError(f"{n_tri} triangles, au-delà de la limite de {MAX_TRIANGLES_FOR_SHADOW}.")

    occluder = build_occluder_mesh(building_envelope, environment_envelope)
    intersector = trimesh.ray.ray_triangle.RayMeshIntersector(occluder)
    vegetation = build_vegetation_scene(building_envelope, environment_envelope)

    sphere_dirs = _fibonacci_sphere(n_samples)
    is_sky = sphere_dirs[:, 2] > 0.0  # "vrai ciel" : au-dessus de l'horizon réel (Z-up)

    vertices = building_envelope['vertices']
    factors = []
    for tri in triangles:
        p = np.array([vertices[j] for j in tri['v']])
        centroid = p.mean(axis=0)
        normal = np.array(tri['normal'])
        f_ciel_flat = (1.0 + math.cos(math.radians(tri['tilt_deg']))) / 2.0

        cos_normal = sphere_dirs @ normal
        valid = is_sky & (cos_normal > 1e-9)
        if not valid.any():
            factors.append(0.0)
            continue

        weight = cos_normal[valid]
        dirs_valid = sphere_dirs[valid]
        origins = np.tile(centroid + normal * RAY_ORIGIN_EPSILON, (dirs_valid.shape[0], 1))
        blocked = intersector.intersects_any(origins, dirs_valid)

        # Lot Z : la végétation ne bloque pas, elle atténue — le poids de chaque
        # direction est multiplié par sa transmittance plutôt que mis à zéro.
        # Sans végétation, `open_fraction` vaut exactement 1 ou 0 et l'on
        # retrouve le calcul d'origine au bit près.
        open_fraction = np.where(blocked, 0.0, 1.0)
        if vegetation is not None:
            unblocked = ~blocked
            if unblocked.any():
                open_fraction[unblocked] = vegetation.transmittance(
                    origins[unblocked], dirs_valid[unblocked],
                )

        visible_ratio = float((weight * open_fraction).sum() / weight.sum())
        factors.append(visible_ratio * f_ciel_flat)

    return factors


def lookup_visibility(sun_visibility, triangle_index, azimuth_deg, elevation_deg):
    """Consultation rapide (Lot D) : la case de grille la plus proche de
    (azimuth_deg, elevation_deg) pour un triangle donné.

    Retourne une FRACTION de rayonnement direct reçue (Lot Z) : 0.0 ou 1.0 sans
    végétation — le comportement binaire d'origine, simple cas particulier —
    une valeur intermédiaire derrière un couvert végétal.
    """
    azimuths = sun_visibility['azimuths_deg']
    elevations = sun_visibility['elevations_deg']

    az_step = azimuths[1] - azimuths[0] if len(azimuths) > 1 else 360.0
    ai = round((azimuth_deg % 360.0) / az_step) % len(azimuths)

    el_clamped = min(max(elevation_deg, elevations[0]), elevations[-1])
    ei = min(range(len(elevations)), key=lambda k: abs(elevations[k] - el_clamped))

    return float(sun_visibility['per_triangle'][triangle_index][ai][ei])
