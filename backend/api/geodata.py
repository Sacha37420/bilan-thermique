"""Génération automatique d'un maillage d'Environment (obstacles voisins) à partir de
sources géographiques ouvertes — IGN BD TOPO en priorité (France), repli OpenStreetMap
sinon (hors de France, ou si IGN est indisponible/ne couvre pas la zone).

Module de logique pure comme geometry.py/shadow.py : pas de dépendance aux modèles
Django, prend lat/lon/rayon, retourne des dicts/listes Python — seule différence avec
ces deux modules, celui-ci fait des appels réseau (c'est son rôle).

Convention géométrique : même repère que geometry.py — Z-up, mètres. Le point
(lat, lon) demandé par l'utilisateur devient l'origine (0,0) du plan XY local, via une
projection tangente équirectangulaire (approximation suffisante aux rayons visés, au
plus quelques centaines de mètres). Terrain supposé plat : pas d'appel séparé à l'API
Altimétrie IGN (limitée à 5 req/s, sans intérêt vu la faible variation de relief sur un
rayon de ce type). Seule exception, « gratuite » : la réponse BD TOPO porte déjà
altitude_minimale_sol par bâtiment, réutilisée telle quelle comme Z de base pour les
bâtiments issus d'IGN — les bâtiments issus d'OpenStreetMap restent posés à Z=0.
"""

import math
import re
import time

import requests
import shapely.geometry
import shapely.ops
import trimesh.creation

from . import geometry

IGN_WFS_URL = 'https://data.geopf.fr/wfs/ows'
IGN_TYPENAME = 'BDTOPO_V3:batiment'
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
USER_AGENT = 'bilan-thermique-lab/1.0 (usage interne, contact: sacha.mailler@gmail.com)'

HTTP_TIMEOUT_S = 25
MAX_BUILDINGS_PER_REQUEST = 1000

DEFAULT_HEIGHT_M = 6.0
METERS_PER_LEVEL = 3.0
EARTH_RADIUS_M = 6_378_137.0

# Bbox approximatives (lat_min, lat_max, lon_min, lon_max) — France métropolitaine
# (+ Corse) et les 5 DOM. Sert uniquement à choisir IGN vs OSM en amont de l'appel ;
# une imprécision de quelques dixièmes de degré ici ne fait au pire que déclencher un
# appel IGN inutile (qui échouera/retournera vide, avec repli OSM automatique).
FRANCE_BBOXES = [
    (41.0, 51.5, -5.5, 9.8),     # métropole
    (15.8, 16.6, -61.9, -60.9),  # Guadeloupe
    (14.3, 15.0, -61.3, -60.7),  # Martinique
    (2.0, 6.0, -54.7, -51.5),    # Guyane
    (-21.5, -20.7, 55.0, 55.9),  # Réunion
    (-13.1, -12.5, 44.9, 45.4),  # Mayotte
]

_NUMBER_RE = re.compile(r'[-+]?\d*\.?\d+')


class GeodataError(Exception):
    pass


def is_in_france(lat, lon):
    return any(lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
               for lat_min, lat_max, lon_min, lon_max in FRANCE_BBOXES)


def bbox_from_radius(lat, lon, radius_m):
    """Retourne (lat_min, lon_min, lat_max, lon_max) — même approximation
    équirectangulaire que local_xy."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = math.degrees(radius_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def local_xy(lat, lon, lat0, lon0):
    """Projection tangente locale vers un plan XY métrique — x = est, y = nord,
    origine au point (lat0, lon0). Cohérent avec le repère Z-up de geometry.py."""
    x = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def latlon_from_local_xy(x, y, lat0, lon0, north_offset_deg=0.0):
    """Inverse exact de `_rotate_xy(*local_xy(lat, lon, lat0, lon0), north_offset_deg)` :
    d'un point du repère LOCAL d'un bâtiment vers (lat, lon). Sert au Lot AA, qui
    doit demander l'altitude d'une grille définie en coordonnées locales.
    La rotation s'inverse en la rejouant avec l'angle opposé."""
    east, north = _rotate_xy(x, y, -north_offset_deg)
    lat = lat0 + math.degrees(north / EARTH_RADIUS_M)
    lon = lon0 + math.degrees(east / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
    return lat, lon


def _rotate_xy(x, y, angle_deg):
    """Rotation standard d'angle angle_deg (°, sens horaire — même convention que
    Building.georef_north_offset_deg / geometry.py azimuth) autour de l'origine.
    angle_deg=0 → identité. Sert à convertir un point (est, nord) du repère réel vers
    le repère local d'un bâtiment dont l'axe +Y ne pointe pas le nord vrai."""
    if angle_deg == 0.0:
        return x, y
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    return x * cos_t - y * sin_t, x * sin_t + y * cos_t


def _parse_meters(value):
    if value is None:
        return None
    match = _NUMBER_RE.search(str(value))
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _request_with_retry(method, url, retries=1, backoff_s=2.0, **kwargs):
    """Une seule retentative après un court délai — les instances publiques IGN/Overpass
    répondent occasionnellement en 5xx/timeout sous charge (observé empiriquement lors du
    développement), sans que ce soit une vraie indisponibilité."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = method(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_s)
    raise last_exc


def _footprint_center(footprint_latlon):
    lat = sum(p[0] for p in footprint_latlon) / len(footprint_latlon)
    lon = sum(p[1] for p in footprint_latlon) / len(footprint_latlon)
    return lat, lon


# ── IGN — BD TOPO v3, couche bâtiments ────────────────────────────────────────────

def _first_exterior_ring(geometry_geojson):
    gtype = (geometry_geojson or {}).get('type')
    coords = (geometry_geojson or {}).get('coordinates')
    if gtype == 'Polygon' and coords:
        return coords[0]
    if gtype == 'MultiPolygon' and coords and coords[0]:
        return coords[0][0]
    return None


def _resolve_ign_height(props):
    hauteur = props.get('hauteur')
    if hauteur:
        return float(hauteur), False
    etages = props.get('nombre_d_etages')
    if etages:
        return float(etages) * METERS_PER_LEVEL, True
    return DEFAULT_HEIGHT_M, True


def fetch_ign_buildings(bbox):
    """bbox = (lat_min, lon_min, lat_max, lon_max). Lève GeodataError si la requête
    échoue — laisse l'appelant décider du repli OSM."""
    lat_min, lon_min, lat_max, lon_max = bbox
    params = {
        'SERVICE': 'WFS',
        'VERSION': '2.0.0',
        'REQUEST': 'GetFeature',
        'TYPENAMES': IGN_TYPENAME,
        'OUTPUTFORMAT': 'application/json',
        'SRSNAME': 'EPSG:4326',
        # BBOX en ordre lon,lat malgré SRSNAME=EPSG:4326 — comportement vérifié
        # empiriquement du service data.geopf.fr (GeoServer), pas de la norme WFS.
        'BBOX': f'{lon_min},{lat_min},{lon_max},{lat_max},EPSG:4326',
        'COUNT': str(MAX_BUILDINGS_PER_REQUEST),
    }
    try:
        resp = _request_with_retry(requests.get, IGN_WFS_URL, params=params,
                                    headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT_S)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeodataError(f"IGN Géoplateforme WFS injoignable ou en erreur ({exc}).") from exc

    buildings = []
    for feature in data.get('features', []):
        props = feature.get('properties') or {}
        ring = _first_exterior_ring(feature.get('geometry'))
        if ring is None or len(ring) < 3:
            continue
        footprint_latlon = [[lat, lon] for lon, lat, *_rest in ring]
        height_m, approx = _resolve_ign_height(props)
        base_z = props.get('altitude_minimale_sol')
        buildings.append({
            'footprint_latlon': footprint_latlon,
            'height_m': height_m,
            'approx_height': approx,
            'base_z': float(base_z) if base_z is not None else None,
            'source': 'ign',
        })
    return buildings


# ── OpenStreetMap — Overpass API ──────────────────────────────────────────────────

def _resolve_osm_height(tags):
    height = _parse_meters(tags.get('height'))
    if height:
        return height, False
    levels = _parse_meters(tags.get('building:levels'))
    if levels:
        return levels * METERS_PER_LEVEL, True
    return DEFAULT_HEIGHT_M, True


def fetch_osm_buildings(bbox):
    """bbox = (lat_min, lon_min, lat_max, lon_max). Lève GeodataError si la requête
    échoue."""
    lat_min, lon_min, lat_max, lon_max = bbox
    query = (
        f'[out:json][timeout:{HTTP_TIMEOUT_S}];'
        f'way["building"]({lat_min},{lon_min},{lat_max},{lon_max});'
        'out geom;'
    )
    try:
        resp = _request_with_retry(requests.post, OVERPASS_URL, data={'data': query},
                                    headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT_S + 5)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeodataError(f"OpenStreetMap (Overpass) injoignable ou en erreur ({exc}).") from exc

    buildings = []
    for element in data.get('elements', []):
        geom = element.get('geometry')
        if not geom or len(geom) < 3:
            continue
        footprint_latlon = [[pt['lat'], pt['lon']] for pt in geom]
        height_m, approx = _resolve_osm_height(element.get('tags') or {})
        buildings.append({
            'footprint_latlon': footprint_latlon,
            'height_m': height_m,
            'approx_height': approx,
            'base_z': None,
            'source': 'osm',
        })
    return buildings


# ── Extrusion et assemblage du maillage ───────────────────────────────────────────

def extrude_footprint(footprint_xy, height_m):
    """footprint_xy : [(x,y), ...] en mètres, plan local. Retourne (vertices, faces)
    d'un volume extrudé fermé (parois + toit + sol) via trimesh — évite d'écrire une
    triangulation ear-clipping maison pour des empreintes potentiellement concaves."""
    polygon = shapely.geometry.Polygon(footprint_xy)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area < 1e-3:
        raise GeodataError("Empreinte de bâtiment dégénérée.")
    mesh = trimesh.creation.extrude_polygon(polygon, height=max(height_m, 1.0))
    return mesh.vertices.tolist(), mesh.faces.tolist()


def extrude_footprint_grouped(footprint_xy, height_m):
    """Comme extrude_footprint, mais retourne des triangles groupés
    ('sol'/'toiture'/'mur_1'..'mur_N') plutôt qu'une liste plate — pour un
    bâtiment dont chaque paroi doit être assignable indépendamment (Lot T,
    mode simplifié), contrairement aux obstacles d'environnement (juste de la
    géométrie d'occlusion, jamais assignée à un ParoiModel).

    Ordre des faces retournées par trimesh.creation.extrude_polygon, vérifié
    empiriquement (non documenté explicitement par trimesh) sur un rectangle
    (N=4) et un polygone en L non convexe (N=6) : pour une empreinte à N
    sommets, les N-2 premières faces triangulent le sol (normale -Z), les N-2
    suivantes le toit (normale +Z), puis EXACTEMENT 2 triangles par arête du
    polygone, dans l'ordre des arêtes (arête i = sommet i -> sommet (i+1)%N).
    Vérification de structure ci-dessous (nombre de faces attendu) plutôt que
    de faire confiance aveuglément à ce comportement non garanti par l'API de
    trimesh — un changement de version qui le romprait doit échouer bruyamment,
    pas assigner silencieusement les mauvais groupes.

    Retourne (vertices, triangles) — triangles : [{'v':[i,j,k], 'group': str,
    'boundary': 'ground'|'exterior_air'}, ...], le format attendu par
    TriangleInputSerializer (Building.envelope). Le sol est marqué
    boundary='ground' (Lot K) — un plancher bas issu d'une empreinte réelle
    touche le terrain par construction."""
    polygon = shapely.geometry.Polygon(footprint_xy)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.area < 1e-3:
        raise GeodataError("Empreinte de bâtiment dégénérée.")

    n = len(polygon.exterior.coords) - 1  # dernier point = premier point (anneau fermé)
    mesh = trimesh.creation.extrude_polygon(polygon, height=max(height_m, 1.0))
    faces = mesh.faces.tolist()

    cap_size = n - 2
    if cap_size < 1 or len(faces) != 2 * cap_size + 2 * n:
        raise GeodataError(
            f"Structure de faces inattendue pour cette empreinte ({len(faces)} faces pour "
            f"{n} sommets, {2 * max(cap_size, 0) + 2 * n} attendues) — extrusion groupée abandonnée."
        )

    triangles = []
    for i, f in enumerate(faces):
        if i < cap_size:
            group, boundary = 'sol', 'ground'
        elif i < 2 * cap_size:
            group, boundary = 'toiture', 'exterior_air'
        else:
            edge_idx = (i - 2 * cap_size) // 2
            group, boundary = f'mur_{edge_idx + 1}', 'exterior_air'
        triangles.append({'v': list(f), 'group': group, 'boundary': boundary})

    return mesh.vertices.tolist(), triangles


MAX_WALLS_SIMPLIFIED_MODE = 30
# Vérifié en réel (2026-08-07) : un bâtiment IGN en zone urbaine dense peut
# avoir une empreinte à plusieurs centaines de sommets (un pâté de maisons
# entier digitalisé comme un seul bâtiment complexe — 515 murs observés en
# plein Paris) — inutilisable pour une configuration paroi par paroi. Une zone
# pavillonnaire donne typiquement 4-26 murs. Le mode simplifié étant justement
# pensé pour un cas simple, on écarte les candidats trop complexes plutôt que
# de produire une UI à des centaines de menus déroulants.


def search_nearby_buildings(lat, lon, radius_m, max_results=5):
    """Lot T (mode simplifié) — cherche les bâtiments réels les plus proches de
    (lat, lon) dans un rayon donné (IGN BD TOPO en France, repli OpenStreetMap
    sinon — même bascule que generate_environment_mesh) et retourne jusqu'à
    max_results candidats triés par distance, chacun DÉJÀ extrudé en enveloppe
    groupée (extrude_footprint_grouped) — extruder au moment de la recherche
    évite un second aller-retour réseau une fois le bâtiment choisi par
    l'utilisateur. Contrairement à generate_environment_mesh (tous les
    bâtiments d'un rayon, utilisés comme obstacles bruts non assignables),
    chaque candidat ici est individuellement assignable ensuite.

    Retourne (candidates, n_skipped_too_complex) : candidates avec au plus
    MAX_WALLS_SIMPLIFIED_MODE parois (les autres sont ignorés, comptés dans
    n_skipped_too_complex — à afficher pour que l'utilisateur comprenne
    pourquoi un bâtiment proche n'apparaît pas). candidates=[] si la zone a
    été interrogée avec succès mais ne contient aucun bâtiment exploitable
    (repli déjà tenté) ; lève GeodataError seulement si LES DEUX sources ont
    échoué (panne réseau/service, pas juste « zone vide »)."""
    bbox = bbox_from_radius(lat, lon, radius_m)

    buildings = []
    ign_error = None
    if is_in_france(lat, lon):
        try:
            buildings = fetch_ign_buildings(bbox)
        except GeodataError as exc:
            ign_error = exc

    if not buildings:
        try:
            buildings = fetch_osm_buildings(bbox)
        except GeodataError as exc:
            if ign_error is not None:
                raise GeodataError(f"{ign_error} Repli OpenStreetMap également en échec : {exc}") from exc
            raise

    if not buildings:
        return [], 0

    for b in buildings:
        clat, clon = _footprint_center(b['footprint_latlon'])
        x, y = local_xy(clat, clon, lat, lon)
        b['_dist'] = math.hypot(x, y)
    buildings.sort(key=lambda b: b['_dist'])

    candidates = []
    n_skipped_too_complex = 0
    for b in buildings:
        if len(candidates) >= max_results:
            break
        footprint_xy = [local_xy(plat, plon, lat, lon) for plat, plon in b['footprint_latlon']]
        try:
            vertices, triangles = extrude_footprint_grouped(footprint_xy, b['height_m'])
        except GeodataError:
            continue
        # Compté sur les triangles réellement produits (pas sur le nombre brut
        # de sommets de l'empreinte) : robuste aux conventions de fermeture
        # d'anneau qui diffèrent entre IGN (GeoJSON) et OSM (way Overpass).
        n_walls = len({t['group'] for t in triangles if t['group'].startswith('mur_')})
        if n_walls > MAX_WALLS_SIMPLIFIED_MODE:
            n_skipped_too_complex += 1
            continue
        clat, clon = _footprint_center(b['footprint_latlon'])
        candidates.append({
            'lat': clat, 'lon': clon, 'distance_m': round(b['_dist'], 1),
            'height_m': b['height_m'], 'approx_height': b['approx_height'], 'source': b['source'],
            'n_walls': n_walls, 'vertices': vertices, 'triangles': triangles,
        })
    return candidates, n_skipped_too_complex


# ── Lot X : écarter le bâtiment étudié de son propre environnement ────────────────

SELF_OVERLAP_RATIO = 0.3
# Un candidat est considéré comme étant LE BÂTIMENT ÉTUDIÉ lui-même si son
# empreinte recouvre plus que cette fraction de la plus petite des deux
# empreintes (la sienne ou celle du bâtiment étudié). Rapporté au MINIMUM des
# deux aires et non à celle du candidat : « ces deux empreintes désignent le
# même bâtiment » doit se détecter que la modélisation de l'utilisateur soit
# plus petite ou plus grande que la donnée IGN/OSM (un OBJ approximatif, une
# boîte du générateur…).
#
# Un doublon exact donne un rapport de 1 ; un MITOYEN réel, qui partage un mur
# avec le bâtiment étudié, donne une intersection d'aire NULLE (deux polygones
# qui se touchent par une arête) et reste donc conservé — c'est voulu, un
# mitoyen est un obstacle légitime. C'est aussi pourquoi le critère porte sur
# une AIRE et non sur `intersects` : `intersects` est vrai dès qu'il y a
# contact, donc supprimerait tous les mitoyens d'un tissu urbain dense.


def envelope_footprint_polygon(envelope):
    """Empreinte 2D du bâtiment étudié, dans le plan XY de SON repère local —
    exactement le repère dans lequel generate_environment_mesh place les
    empreintes candidates (local_xy puis _rotate_xy(north_offset_deg)). Aucune
    rotation supplémentaire à appliquer ici, donc : le piège serait précisément
    d'en appliquer une deuxième.

    Projette TOUS les triangles, sans se limiter à ceux marqués
    boundary='ground' : les murs verticaux se projettent en segments d'aire
    nulle (éliminés par le seuil d'aire), les triangles de sol et de toiture
    donnent la silhouette réelle, concavités et cours intérieures comprises. Ça
    évite surtout de dépendre d'un marquage 'ground' que l'utilisateur n'a pas
    forcément fait (un OBJ importé n'en a aucun par défaut).

    Retourne un polygone shapely, ou None si l'enveloppe est vide/inexploitable
    (auquel cas l'appelant ne filtre rien plutôt que de deviner)."""
    vertices = (envelope or {}).get('vertices') or []
    triangles = (envelope or {}).get('triangles') or []
    if not vertices or not triangles:
        return None

    n = len(vertices)
    polygons = []
    for tri in triangles:
        idx = tri.get('v') if isinstance(tri, dict) else None
        if not idx or len(idx) != 3 or any(not (0 <= i < n) for i in idx):
            continue
        ring = [(vertices[i][0], vertices[i][1]) for i in idx]
        polygon = shapely.geometry.Polygon(ring)
        # Un mur vertical se projette en segment : aire nulle, écarté ici.
        if polygon.is_valid and polygon.area > 1e-9:
            polygons.append(polygon)

    if not polygons:
        return None
    merged = shapely.ops.unary_union(polygons)
    if merged.is_empty or merged.area < 1e-3:
        return None
    return merged


def _is_studied_building(footprint_xy, self_polygon):
    """footprint_xy : empreinte d'un candidat, DÉJÀ dans le repère local du
    bâtiment étudié. self_polygon : sortie de envelope_footprint_polygon."""
    if self_polygon is None:
        return False
    candidate = shapely.geometry.Polygon(footprint_xy)
    if not candidate.is_valid:
        candidate = candidate.buffer(0)
    if candidate.is_empty or candidate.area < 1e-9:
        return False
    intersection = candidate.intersection(self_polygon).area
    return intersection / min(candidate.area, self_polygon.area) > SELF_OVERLAP_RATIO


def generate_environment_mesh(lat, lon, radius_m, progress_cb=None,
                               north_offset_deg=0.0, ground_z_ref=None,
                               self_envelope=None):
    """Orchestrateur principal. progress_cb(stage: str, pct: int) est optionnel (appelé
    par la tâche Celery pour la barre de progression du Job).

    north_offset_deg/ground_z_ref permettent de générer directement dans le repère
    local d'un Building géoréférencé (voir Building.georef_* et _rotate_xy) plutôt
    que dans le repère « est/nord réel » brut — valeurs par défaut = comportement
    d'origine (génération autonome, non alignée à un bâtiment) inchangé.

    self_envelope (Lot X, optionnel) : enveloppe du bâtiment ÉTUDIÉ
    (`Building.envelope`). Le bâtiment étudié étant lui-même un bâtiment réel
    présent dans BD TOPO / OSM, il ressort de la recherche comme n'importe quel
    voisin — et api.shadow fusionne ensuite enveloppe + environnement, donc le
    solveur verrait DEUX exemplaires superposés du même bâtiment (doublon exact
    s'il vient du mode simplifié, volumes qui s'interpénètrent s'il vient d'un
    OBJ ou du générateur de boîte). Fourni, tout candidat qui recouvre
    significativement cette empreinte est écarté — voir _is_studied_building.
    Absent (défaut) : aucun filtrage, comportement d'origine — c'est le cas de
    la génération AUTONOME (page Environnement), qui n'a aucun bâtiment de
    référence à écarter.

    Retourne {'vertices': [[x,y,z],...], 'triangles': [{'v':[i,j,k]},...],
    'warnings': [str, ...], 'stats': {...}} — 'vertices'/'triangles' déjà dans le
    format attendu par Environment.envelope (api.serializers.EnvironmentSerializer)."""
    def report(stage, pct):
        if progress_cb:
            progress_cb(stage, pct)

    report('bbox', 0)
    bbox = bbox_from_radius(lat, lon, radius_m)

    warnings = []
    buildings = []
    n_ign = 0
    n_osm = 0

    if is_in_france(lat, lon):
        report('ign', 10)
        try:
            buildings = fetch_ign_buildings(bbox)
            n_ign = len(buildings)
        except GeodataError as exc:
            warnings.append(f"{exc} Repli sur OpenStreetMap.")

    if not buildings:
        report('osm', 40)
        try:
            osm_buildings = fetch_osm_buildings(bbox)
            n_osm = len(osm_buildings)
            buildings = osm_buildings
        except GeodataError as exc:
            if n_ign == 0 and not warnings:
                raise
            warnings.append(str(exc))

    if not buildings:
        return {
            'vertices': [], 'triangles': [],
            'warnings': warnings + ["Aucun bâtiment trouvé dans ce rayon."],
            'stats': {'buildings_used': 0, 'buildings_ign': n_ign, 'buildings_osm': n_osm,
                      'buildings_skipped': 0, 'buildings_self': 0},
        }

    for b in buildings:
        clat, clon = _footprint_center(b['footprint_latlon'])
        x, y = local_xy(clat, clon, lat, lon)
        b['_dist'] = math.hypot(x, y)
    buildings.sort(key=lambda b: b['_dist'])

    report('extrude', 60)
    self_polygon = envelope_footprint_polygon(self_envelope)
    vertices = []
    triangles = []
    n_approx_height = 0
    n_self = 0
    processed = 0
    for b in buildings:
        footprint_xy = [
            _rotate_xy(*local_xy(plat, plon, lat, lon), north_offset_deg)
            for plat, plon in b['footprint_latlon']
        ]
        # Filtré AVANT l'extrusion et avant le contrôle de limite de maillage :
        # sinon le doublon consommerait des sommets/triangles sur
        # MAX_VERTICES/MAX_TRIANGLES et pourrait, via le `break` ci-dessous,
        # évincer un vrai voisin plus éloigné.
        if _is_studied_building(footprint_xy, self_polygon):
            n_self += 1
            continue
        try:
            v, f = extrude_footprint(footprint_xy, b['height_m'])
        except GeodataError:
            continue
        if len(vertices) + len(v) > geometry.MAX_VERTICES or len(triangles) + len(f) > geometry.MAX_TRIANGLES:
            break
        # Sans ground_z_ref (génération autonome, non attachée à un Building), on
        # garde le comportement d'origine : altitude réelle telle quelle si connue
        # (IGN), 0 sinon. Avec ground_z_ref, on la recale sur le Z=0 du bâtiment.
        if b.get('base_z') is not None:
            base_z = b['base_z'] - (ground_z_ref or 0.0)
        else:
            base_z = 0.0
        offset = len(vertices)
        vertices.extend([vx, vy, vz + base_z] for vx, vy, vz in v)
        triangles.extend({'v': [i0 + offset, i1 + offset, i2 + offset]} for i0, i1, i2 in f)
        processed += 1
        if b.get('approx_height'):
            n_approx_height += 1

    # n_self retranché : un bâtiment écarté parce qu'il EST le bâtiment étudié
    # n'a rien à voir avec la limite de maillage, et le compter ici donnerait un
    # message faux (« réduire le rayon »).
    n_skipped = len(buildings) - processed - n_self
    if n_skipped > 0:
        warnings.append(
            f"Zone dense : {processed} bâtiment(s) le(s) plus proche(s) conservé(s), "
            f"{n_skipped} ignoré(s) (limite de maillage atteinte — réduire le rayon pour tous les inclure)."
        )
    if n_self > 0:
        warnings.append(
            f"{n_self} bâtiment(s) écarté(s) : recouvrement avec le bâtiment étudié — c'est lui-même, "
            "tel qu'il figure dans IGN BD TOPO / OpenStreetMap. Le garder en ferait un obstacle "
            "superposé à sa propre enveloppe."
            + (
                " Plus d'un bâtiment écarté : c'est inhabituel et signale plutôt un géoréférencement "
                "erroné (latitude/longitude ou cap nord) qu'un vrai doublon — à vérifier."
                if n_self > 1 else ""
            )
        )
    elif self_polygon is not None:
        warnings.append(
            "Aucun bâtiment écarté : le bâtiment étudié n'a pas été retrouvé dans les données "
            "IGN/OpenStreetMap à cet endroit. Normal s'il n'y figure pas (construction récente, "
            "zone non couverte) ; sinon, vérifiez son géoréférencement."
        )
    if n_approx_height > 0:
        warnings.append(
            f"Hauteur non disponible pour {n_approx_height} bâtiment(s) — "
            "estimée depuis le nombre d'étages ou une valeur par défaut."
        )

    report('done', 100)
    return {
        'vertices': vertices,
        'triangles': triangles,
        'warnings': warnings,
        'stats': {
            'buildings_used': processed, 'buildings_ign': n_ign, 'buildings_osm': n_osm,
            'buildings_skipped': n_skipped, 'buildings_self': n_self,
        },
    }
