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


def _ray_intersector(mesh):
    """Moteur de lancer de rayons : Embree (Intel, via `embreex`) s'il est
    installé, sinon l'implémentation numpy de trimesh — même interface.

    Mesuré sur un quartier réel (Lot AI, 2026-09-27) : 200 000 rayons en 4,9 s
    avec Embree contre 2 313 s en numpy, pour ZÉRO désaccord de résultat. Sans
    Embree, le précalcul d'ombrage d'un bâtiment subdivisé (12 000 triangles)
    prenait plus de dix minutes."""
    try:
        from trimesh.ray import ray_pyembree
        return ray_pyembree.RayMeshIntersector(mesh)
    except Exception:  # noqa: BLE001 — module absent ou plateforme non gérée
        return trimesh.ray.ray_triangle.RayMeshIntersector(mesh)


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
        self.intersector = _ray_intersector(mesh)
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
    return _ray_intersector(mesh)


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
    intersector = _ray_intersector(occluder)
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
    intersector = _ray_intersector(occluder)
    vegetation = build_vegetation_scene(building_envelope, environment_envelope)

    sphere_dirs = _fibonacci_sphere(n_samples)
    is_sky = sphere_dirs[:, 2] > 0.0  # "vrai ciel" : au-dessus de l'horizon réel (Z-up)

    # Rayons de TOUS les triangles regroupés en lots (Lot AI) : un appel
    # d'intersection par triangle coûtait plus de 5 minutes pour un bâtiment de
    # 12 000 triangles (collectif subdivisé, constaté). Même calcul, même
    # résultat — seul le découpage des appels change.
    vertices = np.asarray(building_envelope['vertices'], dtype=float)
    f_flat = np.zeros(n_tri)
    origins_all, dirs_all, weights_all, owner = [], [], [], []
    has_dirs = np.zeros(n_tri, dtype=bool)
    for idx, tri in enumerate(triangles):
        normal = np.asarray(tri['normal'], dtype=float)
        f_flat[idx] = (1.0 + math.cos(math.radians(tri['tilt_deg']))) / 2.0
        cos_normal = sphere_dirs @ normal
        valid = is_sky & (cos_normal > 1e-9)
        if not valid.any():
            continue
        has_dirs[idx] = True
        centroid = vertices[tri['v']].mean(axis=0)
        d = sphere_dirs[valid]
        origins_all.append(np.tile(centroid + normal * RAY_ORIGIN_EPSILON, (len(d), 1)))
        dirs_all.append(d)
        weights_all.append(cos_normal[valid])
        owner.append(np.full(len(d), idx))
    if not origins_all:
        return [0.0] * n_tri

    O = np.concatenate(origins_all)
    D = np.concatenate(dirs_all)
    W = np.concatenate(weights_all)
    OWN = np.concatenate(owner)
    open_fraction = np.zeros(len(O))
    chunk = 50_000
    for start in range(0, len(O), chunk):
        sl = slice(start, start + chunk)
        blocked = intersector.intersects_any(O[sl], D[sl])
        part = np.where(blocked, 0.0, 1.0)
        # Lot Z : la végétation ne bloque pas, elle atténue — le poids de chaque
        # direction est multiplié par sa transmittance plutôt que mis à zéro.
        if vegetation is not None:
            unblocked = ~blocked
            if unblocked.any():
                part[unblocked] = vegetation.transmittance(O[sl][unblocked], D[sl][unblocked])
        open_fraction[sl] = part

    num = np.bincount(OWN, weights=W * open_fraction, minlength=n_tri)
    den = np.bincount(OWN, weights=W, minlength=n_tri)
    factors = []
    for idx in range(n_tri):
        if not has_dirs[idx]:
            factors.append(0.0)
        else:
            factors.append(float(num[idx] / den[idx]) * f_flat[idx])
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


def compute_ground_reflection_factors(building_envelope, environment_envelope, albedo_at,
                                      ground_obj_ids=(), n_samples=DEFAULT_SKY_SAMPLES):
    """Lot AI — facteur de réflexion du sol de chaque triangle : la fraction du
    rayonnement global horizontal (GHI) que le sol lui renvoie, soit

        E_réfléchi = facteur × GHI,   facteur = ρ_vu × (1 − cos β) / 2

    (1 − cos β)/2 est le facteur de vue du sol d'un plan incliné de β au-dessus
    d'un sol infini ; ρ_vu est l'albédo MOYEN du sol que ce triangle voit
    réellement, pondéré en Lambert : on lance des rayons dans les directions
    descendantes de son hémisphère, et chaque rayon prend l'albédo du point de
    sol touché (`albedo_at(x, y)`) — ou 0 s'il heurte d'abord un bâtiment ou
    le bâtiment lui-même (obstacles noirs, même convention que le reste de
    l'ombrage). Un rayon qui ne touche rien (environnement sans terrain, ou
    sorti de la zone) est prolongé jusqu'au plan du pied du bâtiment.

    Même principe que compute_sky_view_factors : un RAPPORT multipliant la
    formule analytique — sans obstacle et sol uniforme, résultat exactement
    ρ(1 − cos β)/2, sans bruit d'échantillonnage.

    Hypothèse assumée (modèle isotrope standard) : le sol vu est éclairé par
    tout le GHI, ombres portées au sol ignorées.

    ground_obj_ids : identifiants `obj` des triangles d'environnement qui sont
    du SOL (objet terrain) — les seuls qui renvoient de la lumière."""
    triangles = building_envelope['triangles']
    n_tri = len(triangles)
    if n_tri == 0:
        raise ShadowError("Le bâtiment n'a aucun triangle.")

    vertices, faces, is_ground = [], [], []
    for env, ground_ok in ((building_envelope, False), (environment_envelope, True)):
        if not env:
            continue
        base = len(vertices)
        vertices.extend(env['vertices'])
        for tri in env['triangles']:
            if _is_translucent(tri):
                continue
            i, j, k = tri['v']
            faces.append([base + i, base + j, base + k])
            is_ground.append(ground_ok and tri.get('obj') in ground_obj_ids)
    mesh = trimesh.Trimesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64), process=False)
    intersector = _ray_intersector(mesh)
    is_ground = np.asarray(is_ground, dtype=bool)

    bverts = np.asarray(building_envelope['vertices'], dtype=float)
    z_foot = float(bverts[:, 2].min())
    dirs = _fibonacci_sphere(n_samples)
    down = dirs[dirs[:, 2] < -1e-6]

    origins_all, dirs_all, weights_all, owner = [], [], [], []
    factors = np.zeros(n_tri)
    analytic = np.zeros(n_tri)
    for idx, tri in enumerate(triangles):
        # Un plancher au contact du sol (boundary 'ground', Lot K) « voit » tout
        # le sol sous lui — mais c'est sa face ENTERRÉE : aucun rayonnement ne
        # l'atteint. Sans cette exclusion, il recevait ρ × GHI (constaté : 0,74).
        if tri.get('boundary') == 'ground':
            continue
        normal = np.asarray(tri['normal'], dtype=float)
        analytic[idx] = (1.0 - math.cos(math.radians(tri['tilt_deg']))) / 2.0
        cos_n = down @ normal
        valid = cos_n > 1e-9
        if not valid.any() or analytic[idx] < 1e-9:
            continue
        centroid = bverts[tri['v']].mean(axis=0) + normal * RAY_ORIGIN_EPSILON
        d = down[valid]
        origins_all.append(np.tile(centroid, (len(d), 1)))
        dirs_all.append(d)
        weights_all.append(cos_n[valid])
        owner.append(np.full(len(d), idx))
    if not origins_all:
        return factors.tolist()

    O = np.concatenate(origins_all)
    D = np.concatenate(dirs_all)
    W = np.concatenate(weights_all)
    OWN = np.concatenate(owner)
    albedo = np.zeros(len(O))
    hit_any = np.zeros(len(O), dtype=bool)
    chunk = 50_000
    for start in range(0, len(O), chunk):
        sl = slice(start, start + chunk)
        locs, ray_idx, tri_idx = intersector.intersects_location(O[sl], D[sl], multiple_hits=False)
        if len(ray_idx):
            glob = ray_idx + start
            hit_any[glob] = True
            ground_hit = is_ground[tri_idx]
            albedo[glob[ground_hit]] = albedo_at(locs[ground_hit, 0], locs[ground_hit, 1])
    miss = ~hit_any
    if miss.any():
        t = (z_foot - O[miss, 2]) / D[miss, 2]
        px = O[miss, 0] + t * D[miss, 0]
        py = O[miss, 1] + t * D[miss, 1]
        albedo[miss] = albedo_at(px, py)

    num = np.bincount(OWN, weights=W * albedo, minlength=n_tri)
    den = np.bincount(OWN, weights=W, minlength=n_tri)
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = np.where(den > 0, num / den, 0.0)
    factors = ratio * analytic
    return [round(float(f), 5) for f in factors]
