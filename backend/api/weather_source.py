"""Import météo automatique (Open-Meteo Archive, PVGIS TMY) + position solaire
réelle — Lot L (année réelle datée) et Lot S (année type).

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
        # wind_speed_10m (Lot R) : wind_speed_unit=ms demandé explicitement — le
        # défaut d'Open-Meteo est km/h, alors que PVGIS TMY (WS10m, voir
        # _assemble_tmy_series) est nativement en m/s. Les deux sources sont
        # ainsi normalisées en m/s dès la requête, aucune conversion nécessaire
        # côté appelant (vérifié en réel : ordres de grandeur cohérents entre
        # les deux sources sur les mêmes coordonnées).
        'hourly': 'temperature_2m,direct_normal_irradiance,diffuse_radiation,wind_speed_10m',
        'wind_speed_unit': 'ms',
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


def _enrich_hour(lat, lon, dt_utc, t_ext, e_dir_raw, e_dif_raw, north_offset_deg, wind_m_s=None,
                  hour_index=None):
    """Position solaire + clamping vers les bornes de BuildingWeatherPointSerializer
    pour UNE heure — partagé par les deux sources (Open-Meteo Archive, PVGIS TMY,
    Lot S), qui n'ont en commun que cette physique, pas le format brut de leur
    réponse (voir _assemble_weather_series et _assemble_tmy_series).

    wind_m_s (Lot R, optionnel) : contrairement à t_ext/e_dir/e_dif, une valeur
    manquante n'annule PAS l'heure entière (voir les appelants) — h_e_dynamic
    reste simplement inutilisable pour ce point si absent, sans empêcher le
    reste du calcul de fonctionner en h_e constant.

    hour_index (Lot AB1) : nombre d'heures écoulées depuis minuit du PREMIER
    jour de la série — donc `hour_index % 24` = heure du jour et
    `hour_index // 24` = numéro du jour. Porté PAR POINT parce que les
    appelants sautent les heures à donnée manquante (voir _assemble_weather_series) :
    la position dans la liste ne correspond alors plus à l'heure réelle, et tout
    ce qui indexait le temps par cette position (planning de ventilation/volets
    côté serveur, calendrier d'occupation côté client) dérivait définitivement
    d'autant d'heures qu'il en manquait, en silence."""
    real_azimuth, elevation = solar_position(lat, lon, dt_utc)
    local_azimuth = to_local_azimuth(real_azimuth, north_offset_deg)
    return {
        't_ext': max(T_EXT_MIN, min(T_EXT_MAX, t_ext)),
        'sun_azimuth': local_azimuth,
        'sun_elevation': elevation,
        'e_dir': max(0.0, min(E_DIR_MAX, e_dir_raw)),
        'e_dif': max(0.0, min(E_DIF_MAX, e_dif_raw)),
        'wind_m_s': max(0.0, wind_m_s) if wind_m_s is not None else None,
        'hour_index': hour_index,
    }


def _assemble_weather_series(lat, lon, data, north_offset_deg=0.0):
    """Partie pure (testable sans réseau) : transforme la réponse JSON brute
    d'Open-Meteo Archive en liste de dicts {t_ext, sun_azimuth, sun_elevation,
    e_dir, e_dif, wind_m_s} prête pour BuildingWeatherPointSerializer."""
    hourly = data.get('hourly') or {}
    times = hourly.get('time') or []
    temps = hourly.get('temperature_2m') or []
    dni = hourly.get('direct_normal_irradiance') or []
    dif = hourly.get('diffuse_radiation') or []
    wind = hourly.get('wind_speed_10m') or []

    if not times:
        raise WeatherSourceError("Open-Meteo Archive n'a retourné aucune donnée horaire pour cette période.")
    if len(times) > MAX_WEATHER_POINTS:
        raise WeatherSourceError(
            f"{len(times)} heures demandées, au-delà de la limite de {MAX_WEATHER_POINTS} "
            "(un an) — réduire la période."
        )

    series = []
    n_missing = 0
    first_date = None
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

        # wind_m_s (Lot R) n'est volontairement PAS de la même famille que
        # t_ext/e_dir/e_dif ci-dessus : une valeur manquante ne fait PAS sauter
        # l'heure (h_e_dynamic devient simplement inutilisable pour ce point,
        # voir _enrich_hour), le reste du bilan n'en dépend pas.
        wind_m_s = wind[i] if i < len(wind) else None
        dt_utc = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        # hour_index dérivé de la DATE de la ligne, jamais de sa position :
        # c'est ce qui le rend insensible aux heures sautées juste au-dessus,
        # y compris si une journée entière manque (l'écart de dates la compte).
        if first_date is None:
            first_date = dt_utc.date()
        hour_index = (dt_utc.date() - first_date).days * 24 + dt_utc.hour
        series.append(_enrich_hour(lat, lon, dt_utc, t_ext, e_dir_raw, e_dif_raw, north_offset_deg,
                                    wind_m_s=wind_m_s, hour_index=hour_index))

    if not series:
        raise WeatherSourceError("Toutes les heures de cette période ont une donnée manquante côté Open-Meteo.")

    return series, n_missing


def build_weather_series(lat, lon, start_date, end_date, north_offset_deg=0.0):
    """Point d'entrée « année réelle datée » — orchestration réseau + assemblage.
    Retourne (series, n_missing) : series prête pour BuildingWeatherPointSerializer,
    n_missing = nombre d'heures ignorées faute de donnée (0 la plupart du temps)."""
    data = fetch_open_meteo_archive(lat, lon, start_date, end_date)
    return _assemble_weather_series(lat, lon, data, north_offset_deg=north_offset_deg)


# ── PVGIS TMY (Lot S — année type) ───────────────────────────────────────────────

PVGIS_TMY_URL = 'https://re.jrc.ec.europa.eu/api/v5_2/tmy'


def _parse_pvgis_timestamp(ts):
    """'YYYYMMDD:HHMM' (format PVGIS) -> datetime UTC. L'année encodée n'est PAS
    une vraie année calendaire unique : une TMY assemble des mois issus d'années
    source différentes (voir outputs.months_selected de la réponse) — sans
    incidence ici, seuls le jour/mois/heure comptent pour la position solaire
    (déclinaison ne dépend que du jour de l'année), et l'année encodée est de
    toute façon une vraie année réelle (celle du mois effectivement échantillonné),
    jamais une date invalide."""
    date_part, time_part = ts.split(':')
    year, month, day = int(date_part[0:4]), int(date_part[4:6]), int(date_part[6:8])
    hour, minute = int(time_part[0:2]), int(time_part[2:4])
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def fetch_pvgis_tmy(lat, lon):
    """lat/lon (°). Retourne la réponse JSON brute (dict) de PVGIS TMY (JRC,
    Commission européenne — gratuite, sans clé). Lève WeatherSourceError si la
    zone n'est pas couverte — vérifié en réel : PVGIS renvoie le même message
    générique « Location over the sea » aussi bien pour un point réellement en
    mer que pour une zone polaire non couverte (Svalbard, pôle Sud) — ou en cas
    d'erreur réseau. À charge de l'appelant de replier sur Open-Meteo Archive
    (voir build_tmy_or_fallback_series), PVGIS ne fournissant aucune notion de
    « repli » lui-même."""
    params = {'lat': lat, 'lon': lon, 'outputformat': 'json'}
    try:
        resp = _get_with_retry(PVGIS_TMY_URL, params)
    except requests.RequestException as exc:
        raise WeatherSourceError(f"PVGIS TMY injoignable ({exc}).") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise WeatherSourceError(f"Réponse PVGIS TMY invalide ({exc}).") from exc

    if resp.status_code != 200 or 'outputs' not in data:
        reason = data.get('message', f"HTTP {resp.status_code}")
        raise WeatherSourceError(f"PVGIS TMY : {reason}.")

    return data


def _assemble_tmy_series(lat, lon, data, north_offset_deg=0.0):
    """Partie pure (testable sans réseau) : transforme la réponse JSON brute de
    PVGIS TMY en liste de dicts {t_ext, sun_azimuth, sun_elevation, e_dir, e_dif,
    wind_m_s} — Gb(n) (irradiance directe normale au rayon) et Gd(h) (irradiance
    diffuse horizontale) sont exactement les mêmes grandeurs physiques que
    direct_normal_irradiance/diffuse_radiation d'Open-Meteo, seuls les noms de
    champ diffèrent ; WS10m (Lot R) est nativement en m/s, comme
    wind_speed_10m?wind_speed_unit=ms côté Open-Meteo — aucune conversion."""
    hourly = ((data.get('outputs') or {}).get('tmy_hourly')) or []

    if not hourly:
        raise WeatherSourceError("PVGIS TMY n'a retourné aucune donnée horaire.")
    if len(hourly) > MAX_WEATHER_POINTS:
        raise WeatherSourceError(
            f"{len(hourly)} heures reçues de PVGIS TMY, au-delà de la limite de {MAX_WEATHER_POINTS}."
        )

    series = []
    n_missing = 0
    # hour_index (Lot AB1) : compté ici par CHANGEMENT DE (mois, jour) et non
    # par différence de dates comme pour Open-Meteo Archive — une TMY assemble
    # des mois issus d'ANNÉES SOURCE DIFFÉRENTES (voir _parse_pvgis_timestamp),
    # donc une soustraction de dates sauterait d'années entières d'un mois à
    # l'autre. L'année encodée n'a aucune signification ici, seul l'ordre
    # chronologique (janvier -> décembre) en a.
    day_index = -1
    previous_day = None
    for row in hourly:
        ts = row.get('time(UTC)')
        t_ext = row.get('T2m')
        e_dir_raw = row.get('Gb(n)')
        e_dif_raw = row.get('Gd(h)')
        if ts is None or t_ext is None or e_dir_raw is None or e_dif_raw is None:
            n_missing += 1
            continue

        dt_utc = _parse_pvgis_timestamp(ts)
        current_day = (dt_utc.month, dt_utc.day)
        if current_day != previous_day:
            day_index += 1
            previous_day = current_day
        series.append(_enrich_hour(
            lat, lon, dt_utc, t_ext, e_dir_raw, e_dif_raw, north_offset_deg, wind_m_s=row.get('WS10m'),
            hour_index=day_index * 24 + dt_utc.hour,
        ))

    if not series:
        raise WeatherSourceError("Toutes les heures TMY ont une donnée manquante côté PVGIS.")

    return series, n_missing


def build_tmy_series(lat, lon, north_offset_deg=0.0):
    """Point d'entrée « année type » seul (sans repli) — orchestration réseau +
    assemblage PVGIS TMY. Lève WeatherSourceError si la zone n'est pas couverte
    (voir fetch_pvgis_tmy) ; utiliser build_tmy_or_fallback_series pour un repli
    automatique sur une année réelle Open-Meteo Archive."""
    data = fetch_pvgis_tmy(lat, lon)
    return _assemble_tmy_series(lat, lon, data, north_offset_deg=north_offset_deg)


def build_tmy_or_fallback_series(lat, lon, fallback_start_date, fallback_end_date, north_offset_deg=0.0):
    """Point d'entrée principal du Lot S : tente PVGIS TMY (année type,
    statistiquement représentative) en priorité ; si la zone n'est pas couverte
    (ou toute autre erreur PVGIS), replie automatiquement sur une année réelle
    Open-Meteo Archive (fallback_start_date/fallback_end_date, 'YYYY-MM-DD' —
    mêmes bornes que build_weather_series).

    Retourne (series, n_missing, source, warning) : source = 'pvgis-tmy' |
    'open-meteo-archive', warning non None uniquement en cas de repli — à afficher
    à l'utilisateur (to_do.md, Lot S étape 3 : ne jamais laisser la source
    ambiguë, un résultat en kWh/m²/an n'a pas le même sens statistique selon la
    source)."""
    try:
        series, n_missing = build_tmy_series(lat, lon, north_offset_deg=north_offset_deg)
        return series, n_missing, 'pvgis-tmy', None
    except WeatherSourceError as exc:
        series, n_missing = build_weather_series(
            lat, lon, fallback_start_date, fallback_end_date, north_offset_deg=north_offset_deg,
        )
        warning = (
            f"PVGIS TMY indisponible pour cette zone ({exc}) — repli sur l'historique réel "
            "Open-Meteo Archive."
        )
        return series, n_missing, 'open-meteo-archive', warning
