"""Import météo automatique (Open-Meteo Archive) + position solaire réelle — Lot L.

Module de logique pure comme geodata.py : pas de dépendance aux modèles Django, prend
lat/lon/période, retourne des dicts/listes Python déjà dans le format attendu par
BuildingWeatherPointSerializer. Fait des appels réseau (Open-Meteo Archive, API
publique, gratuite, sans clé) — seule différence avec geometry.py/shadow.py, même
raison que geodata.py.

Open-Meteo Archive ne fournit ni azimuth ni élévation solaire — seulement température
et rayonnement. Ce module calcule donc la position solaire réelle heure par heure
(déclinaison + équation du temps de Spencer (1971), formules citées par
https://gml.noaa.gov/grad/solcalc/solareqns.PDF et Duffie & Beckman, « Solar
Engineering of Thermal Processes »), converties en (élévation, azimuth) par un
changement de repère équatorial -> horizon dérivé à la main (voir _elevation_azimuth)
plutôt que la formule cos(azimuth) usuelle des calculatrices solaires, dont la
disambiguïsation de quadrant (signe de l'angle horaire) est une source classique
d'erreur de signe. Recoupé numériquement avec la bibliothèque tierce `astral` sur 7
cas (latitudes/saisons/hémisphères variés, Paris/Sydney/New York/équateur) : écart
< 0,4° partout sauf au voisinage du zénith près de l'équateur à l'équinoxe
(singularité géométrique de l'azimuth quand élévation -> 90°, pas un bug).
"""

import math
import time
from datetime import datetime, timezone

import requests

OPEN_METEO_ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'
USER_AGENT = 'bilan-thermique-lab/1.0 (usage interne, contact: sacha.mailler@gmail.com)'
HTTP_TIMEOUT_S = 30

# Mêmes bornes que BuildingWeatherPointSerializer (serializers.py) — on y clampe
# systématiquement pour garantir qu'une série construite ici passe toujours la
# validation du payload de calcul, plutôt que de le découvrir à la soumission.
T_EXT_MIN, T_EXT_MAX = -60.0, 60.0
E_DIR_MAX = 1400.0
E_DIF_MAX = 600.0
MAX_WEATHER_POINTS = 8784  # un an d'heures, même limite que solver.py/building_solver.py


class WeatherSourceError(Exception):
    pass


def _get_with_retry(url, params, retries=1, backoff_s=2.0):
    """Une seule retentative après un court délai — même principe que
    geodata._request_with_retry (dupliqué plutôt qu'importé : modules purs
    autonomes, voir la docstring de geodata.py). Ne retente PAS sur une réponse
    HTTP reçue avec un code d'erreur (400 « date hors plage », par ex.) : seules
    les erreurs réseau/transport (timeout, DNS, connexion refusée) sont transitoires.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=HTTP_TIMEOUT_S)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_s)
    raise last_exc


# ── Position solaire réelle ─────────────────────────────────────────────────────

def _day_angle(dt_utc):
    """γ (radians) — jour de l'année 1-indexé, formule de Spencer (1971)."""
    n = dt_utc.timetuple().tm_yday
    return 2.0 * math.pi * (n - 1) / 365.0


def _declination_deg(gamma):
    """Déclinaison solaire (°) — série de Fourier de Spencer (1971), précision
    annoncée ~0,0006 rad (~0,03°)."""
    return math.degrees(
        0.006918
        - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )


def _equation_of_time_min(gamma):
    """Équation du temps (minutes) — même source que _declination_deg."""
    return 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )


def _elevation_azimuth(lat_deg, decl_deg, hour_angle_deg):
    """Cœur géométrique, indépendant de toute notion de date/heure : élévation et
    azimuth RÉEL (convention boussole, 0°=Nord, 90°=Est, sens horaire) à partir de
    la latitude, la déclinaison et l'angle horaire solaire (0°=midi solaire vrai,
    croît vers l'après-midi).

    Dérivation (changement de repère équatorial -> horizon local ENU) : le point
    (déclinaison δ, angle horaire H) a pour coordonnées, dans le repère local
    Est/Nord/Zénith de l'observateur à la latitude φ —
        Est    = -cos(δ)·sin(H)
        Nord   =  sin(δ)·cos(φ) - cos(δ)·sin(φ)·cos(H)
        Zénith =  sin(φ)·sin(δ) + cos(φ)·cos(δ)·cos(H)   (= sin(élévation))
    élévation = arcsin(Zénith), azimuth = atan2(Est, Nord) mod 360°.
    """
    phi = math.radians(lat_deg)
    decl = math.radians(decl_deg)
    ha = math.radians(hour_angle_deg)

    sin_elevation = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, sin_elevation))))

    east = -math.cos(decl) * math.sin(ha)
    north = math.sin(decl) * math.cos(phi) - math.cos(decl) * math.sin(phi) * math.cos(ha)
    azimuth = math.degrees(math.atan2(east, north)) % 360.0

    return azimuth, elevation


def solar_position(lat_deg, lon_deg, dt_utc):
    """Position solaire réelle à l'instant dt_utc (datetime UTC) pour l'observateur
    (lat_deg, lon_deg). Retourne (azimuth_deg, elevation_deg), azimuth en convention
    boussole (0°=Nord) — PAS encore la convention interne de l'app, voir
    to_local_azimuth ci-dessous."""
    gamma = _day_angle(dt_utc)
    decl_deg = _declination_deg(gamma)
    eot_min = _equation_of_time_min(gamma)

    time_offset_min = eot_min + 4.0 * lon_deg
    minutes_utc = dt_utc.hour * 60.0 + dt_utc.minute + dt_utc.second / 60.0
    true_solar_time_min = minutes_utc + time_offset_min
    hour_angle_deg = (true_solar_time_min / 4.0) - 180.0

    return _elevation_azimuth(lat_deg, decl_deg, hour_angle_deg)


def to_local_azimuth(real_azimuth_deg, north_offset_deg):
    """Convertit un azimuth réel (convention boussole, 0°=Nord) vers la convention
    interne de l'app (geometry.py : azimuth 0°=+Y, sens horaire, +X=Est) — même
    rotation que geodata._rotate_xy (Building.georef_north_offset_deg), appliquée
    ici à un vecteur direction plutôt qu'à un point. north_offset_deg=0 -> identité
    (azimuth local = azimuth réel), cas d'un bâtiment non géoréférencé."""
    az = math.radians(real_azimuth_deg)
    east, north = math.sin(az), math.cos(az)
    theta = math.radians(north_offset_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    local_east = east * cos_t - north * sin_t
    local_north = east * sin_t + north * cos_t
    return math.degrees(math.atan2(local_east, local_north)) % 360.0


# ── Open-Meteo Archive ──────────────────────────────────────────────────────────

def fetch_open_meteo_archive(lat, lon, start_date, end_date):
    """start_date/end_date : 'YYYY-MM-DD'. Retourne la réponse JSON brute (dict)
    d'Open-Meteo Archive — heures explicitement en UTC (timezone=UTC demandé),
    cohérent avec solar_position (dt_utc). Lève WeatherSourceError sur toute
    erreur réseau ou de validation (ex. date hors de la plage couverte)."""
    params = {
        'latitude': lat, 'longitude': lon,
        'start_date': start_date, 'end_date': end_date,
        'hourly': 'temperature_2m,direct_normal_irradiance,diffuse_radiation',
        'timezone': 'UTC',
    }
    try:
        resp = _get_with_retry(OPEN_METEO_ARCHIVE_URL, params)
    except requests.RequestException as exc:
        raise WeatherSourceError(f"Open-Meteo Archive injoignable ({exc}).") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise WeatherSourceError(f"Réponse Open-Meteo Archive invalide ({exc}).") from exc

    if data.get('error'):
        raise WeatherSourceError(f"Open-Meteo Archive : {data.get('reason', 'erreur inconnue')}.")
    if resp.status_code != 200:
        raise WeatherSourceError(f"Open-Meteo Archive : HTTP {resp.status_code}.")

    return data


def _assemble_weather_series(lat, lon, data, north_offset_deg=0.0):
    """Partie pure (testable sans réseau) : transforme la réponse JSON brute
    d'Open-Meteo Archive en liste de dicts {t_ext, sun_azimuth, sun_elevation,
    e_dir, e_dif} prête pour BuildingWeatherPointSerializer."""
    hourly = data.get('hourly') or {}
    times = hourly.get('time') or []
    temps = hourly.get('temperature_2m') or []
    dni = hourly.get('direct_normal_irradiance') or []
    dif = hourly.get('diffuse_radiation') or []

    if not times:
        raise WeatherSourceError("Open-Meteo Archive n'a retourné aucune donnée horaire pour cette période.")
    if len(times) > MAX_WEATHER_POINTS:
        raise WeatherSourceError(
            f"{len(times)} heures demandées, au-delà de la limite de {MAX_WEATHER_POINTS} "
            "(un an) — réduire la période."
        )

    series = []
    n_missing = 0
    for i, t in enumerate(times):
        t_ext = temps[i] if i < len(temps) else None
        e_dir_raw = dni[i] if i < len(dni) else None
        e_dif_raw = dif[i] if i < len(dif) else None
        if t_ext is None or e_dir_raw is None or e_dif_raw is None:
            # Donnée manquante pour cette heure (bord de la couverture temporelle
            # d'Open-Meteo) — on saute l'heure plutôt que d'inventer une valeur qui
            # fausserait silencieusement le bilan.
            n_missing += 1
            continue

        dt_utc = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        real_azimuth, elevation = solar_position(lat, lon, dt_utc)
        local_azimuth = to_local_azimuth(real_azimuth, north_offset_deg)

        series.append({
            't_ext': max(T_EXT_MIN, min(T_EXT_MAX, t_ext)),
            'sun_azimuth': local_azimuth,
            'sun_elevation': elevation,
            'e_dir': max(0.0, min(E_DIR_MAX, e_dir_raw)),
            'e_dif': max(0.0, min(E_DIF_MAX, e_dif_raw)),
        })

    if not series:
        raise WeatherSourceError("Toutes les heures de cette période ont une donnée manquante côté Open-Meteo.")

    return series, n_missing


def build_weather_series(lat, lon, start_date, end_date, north_offset_deg=0.0):
    """Point d'entrée principal — orchestration réseau + assemblage. Retourne
    (series, n_missing) : series prête pour BuildingWeatherPointSerializer,
    n_missing = nombre d'heures ignorées faute de donnée (0 la plupart du temps)."""
    data = fetch_open_meteo_archive(lat, lon, start_date, end_date)
    return _assemble_weather_series(lat, lon, data, north_offset_deg=north_offset_deg)
