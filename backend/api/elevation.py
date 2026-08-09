"""Altitude du terrain — Lot AA.

Module de logique pure comme geodata.py/weather_source.py : pas de dépendance
aux modèles Django, prend des coordonnées, retourne des listes/dicts Python.
Fait des appels réseau (c'est son rôle), la partie assemblage restant testable
sans réseau.

Deux sources, même bascule que geodata (IGN en France, repli mondial) —
vérifiées par appel réel le 2026-08-09 :

- **IGN Géoplateforme** (`ign_rge_alti_wld`) : RGE ALTI, France, gratuite et sans
  clé. Accepte **60 points en un seul appel** (mesuré) via `lon=a|b|c` et
  `delimiter=|`. Documentée à 5 req/s, d'où le petit délai entre lots.
- **Open-Meteo Elevation** : mondiale (Copernicus DEM ~90 m), gratuite et sans
  clé. Plus grossière — suffisante pour une altitude de référence, insuffisante
  pour du micro-relief. Recoupée avec l'IGN sur les mêmes points : 1 m d'écart.

Piste écartée après vérification : dériver la hauteur de végétation d'un modèle
numérique de surface. L'API d'altimétrie REFUSE la ressource `ign_rge_mns_wld`
(`BAD_PARAMETER`, testé) — seul le terrain est exposé.
"""

import math
import time

import requests

from . import geodata

IGN_ELEVATION_URL = 'https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json'
IGN_RESOURCE = 'ign_rge_alti_wld'
OPEN_METEO_ELEVATION_URL = 'https://api.open-meteo.com/v1/elevation'

USER_AGENT = geodata.USER_AGENT
HTTP_TIMEOUT_S = 25

IGN_BATCH_SIZE = 60
OPEN_METEO_BATCH_SIZE = 100
# Débit annoncé par la Géoplateforme (5 req/s) — un lot toutes les 250 ms garde
# une marge confortable sans allonger notablement une génération de terrain
# (17 lots pour une grille de 150 m au pas de 10 m, soit ~4 s au total).
IGN_BATCH_DELAY_S = 0.25

MAX_TERRAIN_POINTS = 4000
# Le terrain est de loin le plus gros contributeur en triangles du maillage
# d'obstacles (2 par maille) : sans plafond, un grand rayon au pas fin
# saturerait geometry.MAX_TRIANGLES et évincerait les bâtiments voisins, qui
# comptent bien davantage pour l'ombrage.


class ElevationError(Exception):
    pass


def _get(url, params):
    return requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT_S)


def fetch_ign_elevations(points):
    """points : [(lat, lon), ...]. Retourne la liste des altitudes (m, NGF) dans
    le même ordre. Lève ElevationError — l'appelant décide du repli."""
    out = []
    for start in range(0, len(points), IGN_BATCH_SIZE):
        batch = points[start:start + IGN_BATCH_SIZE]
        params = {
            'lon': '|'.join(f'{lon:.6f}' for _, lon in batch),
            'lat': '|'.join(f'{lat:.6f}' for lat, _ in batch),
            'resource': IGN_RESOURCE, 'delimiter': '|', 'zonly': 'true',
        }
        try:
            resp = _get(IGN_ELEVATION_URL, params)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ElevationError(f"Altimétrie IGN injoignable ou en erreur ({exc}).") from exc
        if 'error' in data or 'elevations' not in data:
            raise ElevationError(f"Altimétrie IGN : {data.get('error', 'réponse inattendue')}.")
        values = data['elevations']
        if len(values) != len(batch):
            raise ElevationError(
                f"Altimétrie IGN : {len(values)} altitude(s) pour {len(batch)} point(s) demandé(s)."
            )
        out.extend(float(v) for v in values)
        if start + IGN_BATCH_SIZE < len(points):
            time.sleep(IGN_BATCH_DELAY_S)
    return out


def fetch_open_meteo_elevations(points):
    """Même contrat que fetch_ign_elevations, source mondiale."""
    out = []
    for start in range(0, len(points), OPEN_METEO_BATCH_SIZE):
        batch = points[start:start + OPEN_METEO_BATCH_SIZE]
        params = {
            'latitude': ','.join(f'{lat:.6f}' for lat, _ in batch),
            'longitude': ','.join(f'{lon:.6f}' for _, lon in batch),
        }
        try:
            resp = _get(OPEN_METEO_ELEVATION_URL, params)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ElevationError(f"Open-Meteo Elevation injoignable ou en erreur ({exc}).") from exc
        values = data.get('elevation')
        if not isinstance(values, list) or len(values) != len(batch):
            raise ElevationError("Open-Meteo Elevation : réponse inattendue.")
        out.extend(float(v) for v in values)
    return out


def fetch_elevations(points):
    """Altitudes de `points` ([(lat, lon), ...]), IGN en France avec repli
    mondial Open-Meteo — même bascule que geodata pour les bâtiments. Retourne
    (altitudes, source). Lève ElevationError si LES DEUX sources échouent."""
    if not points:
        return [], 'none'

    lat0, lon0 = points[0]
    ign_error = None
    if geodata.is_in_france(lat0, lon0):
        try:
            return fetch_ign_elevations(points), 'ign'
        except ElevationError as exc:
            ign_error = exc

    try:
        return fetch_open_meteo_elevations(points), 'open-meteo'
    except ElevationError as exc:
        if ign_error is not None:
            raise ElevationError(f"{ign_error} Repli Open-Meteo également en échec : {exc}") from exc
        raise


def ground_altitude(lat, lon):
    """Altitude d'UN point — usage principal : préremplir Building.georef_ground_z.
    Retourne (altitude_m, source)."""
    altitudes, source = fetch_elevations([(lat, lon)])
    if not altitudes:
        raise ElevationError("Aucune altitude retournée pour ce point.")
    return altitudes[0], source


# ── Maillage de terrain ───────────────────────────────────────────────────────

def terrain_grid_local(radius_m, spacing_m):
    """Partie pure : coordonnées LOCALES (x, y) d'une grille carrée centrée sur
    l'origine, pas `spacing_m`, demi-côté `radius_m`. Retourne (coords, n) avec
    coords = [(x, y), ...] balayé ligne par ligne (y croissant, x croissant) et
    n = nombre de points par axe — l'ordre importe : build_terrain_mesh en
    dépend pour trianguler."""
    if spacing_m <= 0:
        raise ElevationError("Le pas de la grille de terrain doit être strictement positif.")
    n = int(math.floor(2.0 * radius_m / spacing_m)) + 1
    if n < 2:
        raise ElevationError("Pas de grille trop grand pour ce rayon (moins de deux points par axe).")
    if n * n > MAX_TERRAIN_POINTS:
        raise ElevationError(
            f"Grille de terrain trop dense ({n}x{n} = {n * n} points, limite {MAX_TERRAIN_POINTS}) — "
            "augmenter le pas ou réduire le rayon."
        )
    coords = [
        (-radius_m + i * spacing_m, -radius_m + j * spacing_m)
        for j in range(n) for i in range(n)
    ]
    return coords, n


def build_terrain_mesh(coords, n, altitudes, ground_z_ref, footprint_polygon=None):
    """Partie pure : transforme une grille (coords/n, voir terrain_grid_local) et
    ses altitudes en {'vertices', 'triangles'} dans le repère local du bâtiment
    (Z relatif à ground_z_ref).

    footprint_polygon (optionnel) : empreinte du bâtiment étudié
    (geodata.envelope_footprint_polygon). Les sommets qui tombent dedans sont
    ramenés à z = 0 — sans quoi un terrain en pente traverserait les murs du
    bâtiment et bloquerait des rayons DEPUIS L'INTÉRIEUR de son enveloppe, ce
    qui fausserait l'ombrage au lieu de l'améliorer.
    """
    if len(coords) != len(altitudes):
        raise ElevationError(
            f"{len(altitudes)} altitude(s) pour {len(coords)} point(s) de grille."
        )

    vertices = []
    for (x, y), alt in zip(coords, altitudes):
        z = alt - (ground_z_ref or 0.0)
        if footprint_polygon is not None:
            import shapely.geometry
            if footprint_polygon.contains(shapely.geometry.Point(x, y)):
                z = 0.0
        vertices.append([x, y, z])

    triangles = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            b = a + 1
            c = a + n
            d = c + 1
            # Normales vers le haut (sens trigonométrique vu de +Z) — sans
            # incidence sur l'occultation (trimesh teste l'intersection, pas
            # l'orientation) mais cohérent avec le reste des maillages.
            triangles.append({'v': [a, b, d]})
            triangles.append({'v': [a, d, c]})
    return {'vertices': vertices, 'triangles': triangles}


def build_terrain_for_building(lat, lon, radius_m, spacing_m, north_offset_deg=0.0,
                                ground_z_ref=None, footprint_polygon=None, progress_cb=None):
    """Orchestrateur : grille locale -> lat/lon -> altitudes (réseau) -> maillage.
    Retourne (mesh, source, n_points)."""
    coords, n = terrain_grid_local(radius_m, spacing_m)
    latlon = [
        geodata.latlon_from_local_xy(x, y, lat, lon, north_offset_deg)
        for x, y in coords
    ]
    if progress_cb:
        progress_cb('terrain', 70)
    altitudes, source = fetch_elevations(latlon)
    mesh = build_terrain_mesh(coords, n, altitudes, ground_z_ref, footprint_polygon=footprint_polygon)
    return mesh, source, len(coords)
