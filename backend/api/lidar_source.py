"""Accès réseau aux données de l'environnement « observé » (Lot AH) : nuage de
points LiDAR HD classifié et bâtiments BD TOPO, tous deux en Lambert 93.

Module réseau seul — toute la reconstruction (recalage, toitures, arbres,
terrain) vit dans api.observed_env, testable sans réseau. Même partage que
geodata/elevation : la partie réseau est isolée pour que le reste ne dépende
d'aucun service extérieur.

Vérifié par appels réels le 2026-09-27 :

- **Index des dalles** : couche WFS `IGNF_LIDAR-HD_METADONNEE:metadata`, une
  entité par dalle de 1 km², avec l'URL du nuage (`url_npl`, COPC) et les dates
  d'acquisition. Une zone non encore couverte renvoie simplement 0 entité.
- **Nuage COPC** : le service de téléchargement accepte les requêtes Range
  (`accept-ranges: bytes`). Une emprise de 300 × 300 m dans une dalle de 112 Mo
  se lit en 18 requêtes, 18,5 Mo, 8,5 s (1,3 million de points), grâce au
  regroupement des nœuds contigus fait par laspy.
- **Limite de débit** : `x-ratelimit-limit-second: 1` sur le téléchargement ET
  le WMS-R. Le lecteur HTTP de laspy lance jusqu'à 10 requêtes en parallèle et
  ne réessaie jamais un 429 : c'est pourquoi on lui passe ici un flux
  séquentiel maison (`ThrottledRangeStream`), jamais une URL.
- **Classes présentes** (procédé IGN_AUTO_V5) : 1 non classé, 2 sol,
  3/4/5 végétation basse/moyenne/haute, 6 bâtiment, 9 eau, 17 pont,
  64 sursol pérenne, 66 points virtuels, 67 divers-bâti.
"""

import io
import time

import numpy as np
import requests

from .geodata import IGN_WFS_URL, USER_AGENT, GeodataError

LIDAR_INDEX_TYPENAME = 'IGNF_LIDAR-HD_METADONNEE:metadata'
BDTOPO_TYPENAME = 'BDTOPO_V3:batiment'
HTTP_TIMEOUT_S = 60

# Espacement minimal entre deux requêtes vers la Géoplateforme : la limite
# annoncée est d'une requête par seconde, avec une tolérance en rafale (les 18
# requêtes du test réel sont passées sans 429 en 8,5 s). On reste sous la
# rafale sans ralentir inutilement, et un 429 est de toute façon réessayé.
MIN_REQUEST_INTERVAL_S = 0.35
MAX_RETRIES = 5

# Classes lues dans le nuage. Tout le reste (eau, ponts, points virtuels…)
# n'intervient ni dans le terrain ni dans les masques solaires.
CLASS_GROUND = 2
CLASS_VEG_LOW, CLASS_VEG_MID, CLASS_VEG_HIGH = 3, 4, 5
CLASS_BUILDING = 6
CLASS_UNCLASSIFIED = 1
CLASS_WATER = 9
CLASS_PERENNIAL = 64
KEPT_CLASSES = (CLASS_UNCLASSIFIED, CLASS_GROUND, CLASS_VEG_LOW, CLASS_VEG_MID,
                CLASS_VEG_HIGH, CLASS_BUILDING, CLASS_WATER, CLASS_PERENNIAL)

WMS_R_URL = 'https://data.geopf.fr/wms-r'
ORTHO_RGB_LAYER = 'ORTHOIMAGERY.ORTHOPHOTOS'
# Infrarouge couleur : canal rouge = proche infrarouge, vert = rouge, bleu = vert.
# C'est lui qui donne la part infrarouge de l'albédo solaire (la moitié de
# l'énergie solaire), et un indice de végétation (NDVI).
ORTHO_IRC_LAYER = 'ORTHOIMAGERY.ORTHOPHOTOS.IRC'
ORTHO_MAX_PX = 2048
# CoSIA (couverture du sol par IA), par millésime — rendu en couleurs de légende.
COSIA_LAYERS = [((2017, 2020), 'IGNF_COSIA_2017-2020'), ((2021, 2023), 'IGNF_COSIA_2021-2023'),
                ((2024, 2026), 'IGNF_COSIA_2024-2026')]


def cosia_layer_for(year):
    """Millésime CoSIA le plus proche de l'année du relevé LiDAR."""
    if year is None:
        return COSIA_LAYERS[1][1]
    for (a, b), name in COSIA_LAYERS:
        if a <= year <= b:
            return name
    return COSIA_LAYERS[-1][1] if year > 2026 else COSIA_LAYERS[0][1]


def fetch_cosia(bbox_l93, year, px_per_m=2.0):
    """Raster CoSIA (PNG RGBA décodé) de l'emprise ; lève GeodataError."""
    from PIL import Image
    xmin, ymin, xmax, ymax = bbox_l93
    width = max(16, min(ORTHO_MAX_PX, int(round((xmax - xmin) * px_per_m))))
    height = max(16, min(ORTHO_MAX_PX, int(round((ymax - ymin) * px_per_m))))
    params = {
        'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap', 'LAYERS': cosia_layer_for(year),
        'FORMAT': 'image/png', 'STYLES': '', 'CRS': 'EPSG:2154',
        'BBOX': f'{xmin},{ymin},{xmax},{ymax}', 'WIDTH': str(width), 'HEIGHT': str(height),
    }
    try:
        resp = _throttled_get(WMS_R_URL, params=params)
    except requests.RequestException as exc:
        raise GeodataError(f"CoSIA injoignable ({exc}).") from exc
    if not resp.headers.get('content-type', '').startswith('image/'):
        raise GeodataError(f"CoSIA : réponse inattendue ({resp.text[:200]}).")
    return np.asarray(Image.open(io.BytesIO(resp.content)).convert('RGBA'))


class LidarUnavailable(GeodataError):
    """Zone sans LiDAR HD publié (ou service injoignable) — l'appelant bascule
    sur le générateur historique BD TOPO / OpenStreetMap."""


_last_request_at = [0.0]


def _throttled_get(url, **kwargs):
    """GET avec espacement minimal et reprise sur 429/5xx. `Retry-After` est
    respecté quand le serveur le fournit."""
    headers = {'User-Agent': USER_AGENT, **kwargs.pop('headers', {})}
    last_exc = None
    for attempt in range(MAX_RETRIES):
        wait = _last_request_at[0] + MIN_REQUEST_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at[0] = time.monotonic()
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_S, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(1.0 + attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get('Retry-After')
            try:
                delay = float(retry_after) if retry_after else 1.0 + attempt
            except ValueError:
                delay = 1.0 + attempt
            last_exc = requests.HTTPError(f"HTTP {resp.status_code}")
            time.sleep(min(delay, 10.0))
            continue
        resp.raise_for_status()
        return resp
    raise last_exc or requests.HTTPError("échec sans réponse")


class ThrottledRangeStream(io.RawIOBase):
    """Flux fichier minimal (seek/read/readinto) au-dessus de requêtes Range.

    laspy.CopcReader, quand on lui passe un objet qui n'est PAS son propre
    HttpRangeStream mais possède `readinto`, lit les blocs séquentiellement —
    ce qui est exactement ce que la limite de débit impose. Les nœuds contigus
    de l'octree restent regroupés en une seule requête par laspy."""

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.pos = 0
        self.n_requests = 0
        self.n_bytes = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def seek(self, pos, whence=io.SEEK_SET):
        if whence != io.SEEK_SET:
            raise io.UnsupportedOperation("seul SEEK_SET est pris en charge")
        self.pos = pos
        return pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            raise io.UnsupportedOperation("lecture de taille inconnue")
        if n == 0:
            return b''
        resp = _throttled_get(self.url, headers={'Range': f'bytes={self.pos}-{self.pos + n - 1}'})
        data = resp.content
        self.pos += len(data)
        self.n_requests += 1
        self.n_bytes += len(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def fetch_lidar_tiles(bbox_wgs84):
    """bbox_wgs84 = (lat_min, lon_min, lat_max, lon_max). Retourne la liste des
    dalles couvrant la zone : [{'url', 'name', 'acq_start', 'acq_end',
    'classification'}]. Lève LidarUnavailable si le service échoue ; liste vide
    si la zone n'est pas (encore) couverte."""
    lat_min, lon_min, lat_max, lon_max = bbox_wgs84
    params = {
        'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetFeature',
        'TYPENAMES': LIDAR_INDEX_TYPENAME, 'OUTPUTFORMAT': 'application/json',
        'SRSNAME': 'EPSG:4326',
        # Même ordre lon,lat que pour la BD TOPO (voir geodata.fetch_ign_buildings).
        'BBOX': f'{lon_min},{lat_min},{lon_max},{lat_max},EPSG:4326',
        'COUNT': '50',
    }
    try:
        data = _throttled_get(IGN_WFS_URL, params=params).json()
    except (requests.RequestException, ValueError) as exc:
        raise LidarUnavailable(f"Index LiDAR HD injoignable ({exc}).") from exc

    tiles = []
    for feature in data.get('features', []):
        props = feature.get('properties') or {}
        url = props.get('url_npl')
        if not url:
            continue
        tiles.append({
            'url': url,
            'name': url.rsplit('/', 1)[-1],
            'acq_start': (props.get('date_debut_acquisition') or '')[:10],
            'acq_end': (props.get('date_fin_acquisition') or '')[:10],
            'classification': props.get('procede_classement') or '',
        })
    return tiles


def read_points(tiles, bbox_l93, progress_cb=None):
    """Lit, dans chaque dalle, les points compris dans bbox_l93 =
    (xmin, ymin, xmax, ymax) en Lambert 93. Retourne (x, y, z, classification)
    en tableaux numpy, plus des statistiques de transfert.

    Seules les dimensions utiles sont décompressées (XY, Z, classification) :
    le décodage des autres canaux (intensité, temps GPS, couleur…) coûterait
    le gros du temps CPU pour rien."""
    import laspy
    from laspy import Bounds, CopcReader
    from laspy.compression import DecompressionSelection

    selection = (DecompressionSelection.XY_RETURNS_CHANNEL | DecompressionSelection.Z
                 | DecompressionSelection.CLASSIFICATION)
    bounds = Bounds(mins=np.array(bbox_l93[:2], dtype=float), maxs=np.array(bbox_l93[2:], dtype=float))

    xs, ys, zs, cs = [], [], [], []
    stats = {'requests': 0, 'bytes': 0, 'points_read': 0}
    for index, tile in enumerate(tiles):
        stream = ThrottledRangeStream(tile['url'])
        try:
            reader = CopcReader(stream, close_fd=False, decompression_selection=selection)
            points = reader.query(bounds)
        except (requests.RequestException, laspy.LaspyException, OSError, ValueError) as exc:
            raise LidarUnavailable(f"Lecture de la dalle {tile['name']} impossible ({exc}).") from exc
        stats['requests'] += stream.n_requests
        stats['bytes'] += stream.n_bytes
        if len(points):
            cls = np.asarray(points.classification, dtype=np.uint8)
            keep = np.isin(cls, KEPT_CLASSES)
            xs.append(np.asarray(points.x, dtype=float)[keep])
            ys.append(np.asarray(points.y, dtype=float)[keep])
            zs.append(np.asarray(points.z, dtype=float)[keep])
            cs.append(cls[keep])
            stats['points_read'] += int(len(points))
        if progress_cb:
            progress_cb(index + 1, len(tiles))

    if not xs:
        return (np.empty(0), np.empty(0), np.empty(0), np.empty(0, dtype=np.uint8)), stats
    return (np.concatenate(xs), np.concatenate(ys), np.concatenate(zs), np.concatenate(cs)), stats


def fetch_bdtopo_buildings_l93(bbox_l93, max_features=2000):
    """Bâtiments BD TOPO directement en Lambert 93 — le repère du LiDAR, sans
    passer par WGS84 (une double projection ajouterait une erreur à ce qu'on
    cherche précisément à mesurer : le décalage entre les deux sources).

    Retourne [{'id', 'rings': [[(x, y), ...], ...] (anneau extérieur puis
    trous), 'hauteur', 'etages', 'z_sol', 'z_toit_max', 'nature', 'usage'}]."""
    xmin, ymin, xmax, ymax = bbox_l93
    buildings = []
    start = 0
    while True:
        params = {
            'SERVICE': 'WFS', 'VERSION': '2.0.0', 'REQUEST': 'GetFeature',
            'TYPENAMES': BDTOPO_TYPENAME, 'OUTPUTFORMAT': 'application/json',
            'SRSNAME': 'EPSG:2154',
            'BBOX': f'{xmin},{ymin},{xmax},{ymax},EPSG:2154',
            'COUNT': '1000', 'STARTINDEX': str(start),
        }
        try:
            data = _throttled_get(IGN_WFS_URL, params=params).json()
        except (requests.RequestException, ValueError) as exc:
            raise GeodataError(f"BD TOPO injoignable ou en erreur ({exc}).") from exc
        features = data.get('features', [])
        for feature in features:
            geom = feature.get('geometry') or {}
            polygons = []
            if geom.get('type') == 'Polygon':
                polygons = [geom.get('coordinates') or []]
            elif geom.get('type') == 'MultiPolygon':
                polygons = geom.get('coordinates') or []
            props = feature.get('properties') or {}
            for p_index, rings in enumerate(polygons):
                if not rings or len(rings[0]) < 4:
                    continue
                buildings.append({
                    'id': (props.get('cleabs') or feature.get('id') or '') + (f'#{p_index}' if p_index else ''),
                    'rings': [[(pt[0], pt[1]) for pt in ring] for ring in rings],
                    'hauteur': _float_or_none(props.get('hauteur')),
                    'z_sol': _float_or_none(props.get('altitude_minimale_sol')),
                    'z_toit_max': _float_or_none(props.get('altitude_maximale_toit')),
                    'nature': props.get('nature') or '',
                    'usage': props.get('usage_1') or '',
                    # Lot AI — codes fichiers fonciers à deux chiffres (voir
                    # observed_env.decode_materials) et année d'apparition.
                    'mat_murs': props.get('materiaux_des_murs') or None,
                    'mat_toit': props.get('materiaux_de_la_toiture') or None,
                    'annee': _year_or_none(props.get('date_d_apparition')),
                    'logements': props.get('nombre_de_logements'),
                    'etages': _float_or_none(props.get('nombre_d_etages')),
                })
        if len(features) < 1000 or len(buildings) >= max_features:
            break
        start += 1000
    return buildings


def _year_or_none(value):
    try:
        year = int(str(value)[:4])
    except (TypeError, ValueError):
        return None
    return year if 1000 < year < 2100 else None


def fetch_orthophoto(bbox_l93, layer, px_per_m=4.0):
    """Orthophoto IGN (WMS-R, JPEG) couvrant bbox_l93 = (xmin, ymin, xmax, ymax),
    axes Lambert 93 (x = colonnes, y = lignes vers le HAUT de l'emprise en haut
    de l'image). Retourne (octets JPEG, largeur, hauteur). 20 cm/pixel natifs ;
    on s'en tient par défaut à 25 cm, plafonné à ORTHO_MAX_PX de côté.
    Vérifié le 2026-09-27 : emprise arbitraire acceptée, JPEG 1500 px ≈ 380 ko."""
    xmin, ymin, xmax, ymax = bbox_l93
    width = max(16, min(ORTHO_MAX_PX, int(round((xmax - xmin) * px_per_m))))
    height = max(16, min(ORTHO_MAX_PX, int(round((ymax - ymin) * px_per_m))))
    params = {
        'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap', 'LAYERS': layer,
        'FORMAT': 'image/jpeg', 'STYLES': '', 'CRS': 'EPSG:2154',
        'BBOX': f'{xmin},{ymin},{xmax},{ymax}', 'WIDTH': str(width), 'HEIGHT': str(height),
    }
    try:
        resp = _throttled_get(WMS_R_URL, params=params)
    except requests.RequestException as exc:
        raise GeodataError(f"Orthophoto IGN injoignable ({exc}).") from exc
    if not resp.headers.get('content-type', '').startswith('image/'):
        raise GeodataError(f"Orthophoto IGN : réponse inattendue ({resp.text[:200]}).")
    return resp.content, width, height


def decode_jpeg(data):
    """Octets JPEG → tableau numpy (h, w, 3) uint8, ligne 0 = HAUT de l'image."""
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert('RGB'))


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def describe_tiles(tiles):
    """Résumé lisible (et sérialisable) des dalles utilisées, pour l'historique
    de génération de l'environnement."""
    dates = sorted({t['acq_end'] or t['acq_start'] for t in tiles if t['acq_end'] or t['acq_start']})
    return {
        'tiles': [t['name'] for t in tiles],
        'acquisition': dates,
        'classification': sorted({t['classification'] for t in tiles if t['classification']}),
    }

