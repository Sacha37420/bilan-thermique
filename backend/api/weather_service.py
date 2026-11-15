"""Météo choisie et comparée (Lot 13, refonte — § B2). Jeux partagés `WeatherDataset`.

Sources :
- **tmy** : année type PVGIS du site (repli Open-Meteo) — défaut, comparaison entre bâtiments ;
- **reel** : période réelle quelconque (≤ 1 an) par la réanalyse Open-Meteo Archive — factures ;
- **station** : station Météo-France, données climatologiques horaires (fichiers par département et par
  décennie sur data.gouv.fr, lus **en flux** : seules les lignes de la station et de la période sont
  gardées) ; rayonnement de la station (GLO/DIR/DIF) s'il existe, sinon celui d'Open-Meteo au même lieu ;
- **tracc** : climat futur de la trajectoire de réchauffement (+2 / +2,7 / +4 °C, avec ou sans vague de
  chaleur), fichiers prospectifs ADEME / Météo-France pour 8 stations de référence de métropole ;
- **import** : fichier EPW ou CSV de l'utilisateur.

Un jeu est stocké une fois (empreinte de la série) et partagé entre bâtiments. Les trous de quelques
heures sont comblés par interpolation et signalés (correctif AB1 : chaque point garde son
`hour_index`).
"""

import csv
import gzip
import io
import math
from datetime import date, datetime, timedelta, timezone

import requests

from . import net, sources, weather_source
from .fingerprint import stable_hash

MF_LIST_URL = 'https://www.data.gouv.fr/api/1/datasets/donnees-climatologiques-de-base-horaires/'
MF_FILE_URL = 'https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/HOR/H_{dep}_{period}.csv.gz'
GEO_COMMUNE_URL = 'https://geo.api.gouv.fr/communes'
TRACC_DATASETS = {
    '+2': '1f2aqm2g04n1srjjnmb3ib8-', '+2.7': 'jq7m4bbpft0u4bnontmelat6', '+4': 'di6gk6vlm2ky-5oxttymgg57',
    '+2-chaleur': 'myp7wubg7b9-nxdf411cu4l9', '+2.7-chaleur': 'ib2w-7-1se7721vpfwyqo136', '+4-chaleur': 'osotdn4s-17pqh7795nsovqz',
}
TRACC_LINES_URL = 'https://data.ademe.fr/data-fair/api/v1/datasets/{id}/lines'
TRACC_STATIONS = {  # station de référence → (lat, lon, altitude)
    'AGEN': (44.1721, 0.5946, 58), 'CARPENTRAS': (44.0811, 5.0586, 99), 'MACON': (46.2967, 4.7964, 216),
    'MARIGNANE': (43.4377, 5.2161, 9), 'NANCY': (48.6836, 6.2228, 212), 'RENNES': (48.0689, -1.7342, 36),
    'TOURS': (47.4444, 0.7272, 108), 'TRAPPES': (48.7744, 2.0097, 168),
}
MAX_GAP_FILL_H = 3
ALT_WARNING_M = 150
STATION_MAX_KM = 60


class WeatherError(ValueError):
    pass


# ── Outils ──────────────────────────────────────────────────────────────────

def distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def erbs_split(ghi, sun_elevation_deg, day_of_year):
    """(DNI, DHI) depuis le global horizontal par la corrélation d'Erbs (1982)."""
    if ghi <= 0 or sun_elevation_deg <= 1:
        return 0.0, max(ghi, 0.0)
    sin_h = math.sin(math.radians(sun_elevation_deg))
    i0 = 1367.0 * (1 + 0.033 * math.cos(2 * math.pi * day_of_year / 365.0)) * sin_h
    kt = min(ghi / i0, 1.0) if i0 > 0 else 0.0
    if kt <= 0.22:
        kd = 1.0 - 0.09 * kt
    elif kt <= 0.80:
        kd = 0.9511 - 0.1604 * kt + 4.388 * kt ** 2 - 16.638 * kt ** 3 + 12.336 * kt ** 4
    else:
        kd = 0.165
    dhi = kd * ghi
    dni = (ghi - dhi) / sin_h
    return max(0.0, min(dni, 1400.0)), max(0.0, min(dhi, 600.0))


def point(lat, lon, dt_utc, t_ext, dni, dhi, wind, hour_index):
    az, el = weather_source.solar_position(lat, lon, dt_utc)
    return {'t_ext': round(max(-60.0, min(60.0, t_ext)), 2), 'sun_azimuth': round(az, 2), 'sun_elevation': round(el, 2),
            'e_dir': round(max(0.0, min(1400.0, dni)), 1), 'e_dif': round(max(0.0, min(600.0, dhi)), 1),
            'wind_m_s': round(max(0.0, wind), 1) if wind is not None else None, 'hour_index': hour_index}


def fill_gaps(rows, start_utc, n_hours):
    """rows : {heure_depuis_début: (t, dni, dhi, vent)} → (liste complète, nb d'heures comblées, nb absentes).
    Un trou ≤ MAX_GAP_FILL_H heures est interpolé linéairement ; au-delà les heures restent absentes."""
    filled = missing = 0
    out = dict(rows)
    keys = sorted(rows)
    for a, b in zip(keys, keys[1:]):
        gap = b - a - 1
        if 0 < gap <= MAX_GAP_FILL_H:
            for k in range(1, gap + 1):
                w = k / (gap + 1)
                out[a + k] = tuple(None if (x is None or y is None) else x + (y - x) * w for x, y in zip(rows[a], rows[b]))
                filled += 1
    missing = n_hours - len(out)
    return out, filled, missing


def stats(series):
    """Degrés-jours (base 18 °C), ensoleillement global horizontal, extrêmes."""
    if not series:
        return {}
    by_day = {}
    ghi = 0.0
    tmax, tmin = -99.0, 99.0
    hot = 0
    for p in series:
        d = (p.get('hour_index') or 0) // 24
        by_day.setdefault(d, []).append(p['t_ext'])
        el = p['sun_elevation']
        ghi += p['e_dir'] * max(math.sin(math.radians(el)), 0.0) + p['e_dif']
        tmax, tmin = max(tmax, p['t_ext']), min(tmin, p['t_ext'])
        hot += p['t_ext'] > 30
    dju = sum(max(0.0, 18.0 - sum(v) / len(v)) for v in by_day.values())
    return {'n_hours': len(series), 'dju_18': round(dju, 0), 'ghi_kwh_m2': round(ghi / 1000.0, 0),
            't_mean': round(sum(p['t_ext'] for p in series) / len(series), 2), 't_max': round(tmax, 1),
            't_min': round(tmin, 1), 'hours_over_30': hot}


# ── Création / partage ──────────────────────────────────────────────────────

def save(kind, label, series, *, lat=None, lon=None, owner_sub='', meta=None, quality=None):
    from .models import WeatherDataset
    if not series:
        raise WeatherError('Série météo vide.')
    if len(series) > 8784:
        raise WeatherError('Série de plus d’un an : choisissez une période plus courte.')
    h = stable_hash(series)
    existing = WeatherDataset.objects.filter(series_hash=h).first()
    if existing is not None:
        return existing
    return WeatherDataset.objects.create(
        kind=kind, label=label[:200], lat=lat, lon=lon, series=series, series_hash=h, stats=stats(series),
        meta=meta or {}, quality=quality or {}, owner_sub=owner_sub or '')


def from_tmy(lat, lon, owner_sub=''):
    from .calc_service import CalcError, tmy_weather
    try:
        w = tmy_weather(lat, lon)
    except CalcError as exc:
        raise WeatherError(str(exc)) from exc
    label = 'Année type PVGIS' if w['source'] == 'pvgis-tmy' else 'Dernière année réelle (repli : PVGIS indisponible)'
    return save('tmy', f'{label} — {lat:.2f}, {lon:.2f}', w['series'], lat=lat, lon=lon, owner_sub=owner_sub,
                meta={'source': w['source'], 'warning': w.get('warning')}, quality={'n_missing': w.get('n_missing', 0)})


def from_reanalysis(lat, lon, start, end, owner_sub=''):
    if end < start or (end - start).days > 366:
        raise WeatherError('Période invalide (un an au plus).')
    try:
        series, n_missing = weather_source.build_weather_series(lat, lon, start.isoformat(), end.isoformat())
    except weather_source.WeatherSourceError as exc:
        raise WeatherError(f'Open-Meteo : {exc}') from exc
    return save('reel', f'Réanalyse {start:%d/%m/%Y} – {end:%d/%m/%Y} — {lat:.2f}, {lon:.2f}', series, lat=lat, lon=lon,
                owner_sub=owner_sub, meta={'source': 'open-meteo-archive', 'start': start.isoformat(), 'end': end.isoformat()},
                quality={'n_missing': n_missing})


# ── Stations Météo-France ───────────────────────────────────────────────────

def department_of(lat, lon):
    def fetch():
        try:
            r = requests.get(GEO_COMMUNE_URL, params={'lat': f'{lat:.5f}', 'lon': f'{lon:.5f}', 'fields': 'codeDepartement'},
                             headers=net.HEADERS, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise WeatherError(f'Découpage administratif : {net.reason(exc)}') from exc
        return rows[0]['codeDepartement'] if rows else None
    dep = sources.cached('geo_dep', f'{lat:.3f},{lon:.3f}', fetch, ttl_days=3650)
    if not dep:
        raise WeatherError('Département introuvable pour cette position.')
    return dep


def mf_periods(start_year, end_year):
    """Noms de fichiers (décennies) couvrant les années demandées."""
    out = []
    for y in range(start_year, end_year + 1):
        if y >= 2020:
            p = 'previous-2020-2024' if y <= 2024 else 'latest-2025-2026'
        else:
            p = f'{y // 10 * 10}-{y // 10 * 10 + 9}'
        if p not in out:
            out.append(p)
    return out


def _mf_lines(dep, period):
    """Lignes d'un fichier horaire Météo-France, décompressées en flux (jamais stocké en entier)."""
    url = MF_FILE_URL.format(dep=dep, period=period)
    try:
        r = requests.get(url, headers=net.HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        r.raw.decode_content = False
        with gzip.GzipFile(fileobj=r.raw) as gz:
            for line in io.TextIOWrapper(gz, encoding='utf-8', errors='replace'):
                yield line
    except (requests.RequestException, OSError, EOFError) as exc:
        raise WeatherError(f'Météo-France : {net.reason(exc)}') from exc


def parse_mf_stations(lines):
    """{NUM_POSTE: {name, lat, lon, alt, first, last, has_radiation}} depuis les lignes d'un fichier horaire."""
    reader = csv.reader(lines, delimiter=';')
    header = next(reader)
    ix = {k: header.index(k) for k in ('NUM_POSTE', 'NOM_USUEL', 'LAT', 'LON', 'ALTI', 'AAAAMMJJHH', 'T', 'GLO')}
    st = {}
    for row in reader:
        if len(row) <= ix['GLO']:
            continue
        sid = row[ix['NUM_POSTE']]
        s = st.get(sid)
        stamp = row[ix['AAAAMMJJHH']]
        if s is None:
            s = st[sid] = {'id': sid, 'name': row[ix['NOM_USUEL']], 'lat': float(row[ix['LAT']]), 'lon': float(row[ix['LON']]),
                           'alt': float(row[ix['ALTI']] or 0), 'first': stamp, 'last': stamp, 'has_t': False,
                           'has_radiation': False}
        s['last'] = max(s['last'], stamp)
        s['first'] = min(s['first'], stamp)
        s['has_t'] = s['has_t'] or bool(row[ix['T']])
        s['has_radiation'] = s['has_radiation'] or bool(row[ix['GLO']])
    return {k: v for k, v in st.items() if v['has_t']}


def stations_near(lat, lon, alt=None):
    dep = department_of(lat, lon)
    stations = sources.cached('mf_stations', dep, lambda: parse_mf_stations(_mf_lines(dep, 'previous-2020-2024')),
                              ttl_days=180)
    out = []
    for s in stations.values():
        d = distance_km(lat, lon, s['lat'], s['lon'])
        if d > STATION_MAX_KM:
            continue
        warn = alt is not None and abs(s['alt'] - alt) > ALT_WARNING_M
        out.append({**s, 'distance_km': round(d, 1), 'department': dep,
                    'warning': f'Écart d’altitude {abs(s["alt"] - alt):.0f} m' if warn else None})
    return sorted(out, key=lambda x: x['distance_km'])[:15]


def parse_mf_series(lines, station_id, start, end):
    """Points horaires (UTC) d'une station sur [start, end] depuis les lignes d'un fichier horaire.
    Rayonnement GLO/DIR/DIF en J/cm² par heure → W/m² (× 10 000 / 3 600)."""
    reader = csv.reader(lines, delimiter=';')
    header = next(reader)
    ix = {k: header.index(k) for k in ('NUM_POSTE', 'LAT', 'LON', 'AAAAMMJJHH', 'T', 'FF', 'GLO', 'DIR', 'DIF')}
    lo, hi = start.strftime('%Y%m%d00'), end.strftime('%Y%m%d23')
    rows, meta = {}, {}

    def f(v):
        try:
            return float(v) if v != '' else None
        except ValueError:
            return None
    for row in reader:
        if not row or row[0] != station_id:
            continue
        stamp = row[ix['AAAAMMJJHH']]
        if not (lo <= stamp <= hi):
            continue
        meta.setdefault('lat', float(row[ix['LAT']]))
        meta.setdefault('lon', float(row[ix['LON']]))
        rows[stamp] = (f(row[ix['T']]), f(row[ix['GLO']]), f(row[ix['DIR']]), f(row[ix['DIF']]), f(row[ix['FF']]))
    return rows, meta


def series_from_station_rows(rows, meta, start, end, radiation_fallback=None):
    """Série au format de l'app. Rayonnement : DIR/DIF de la station, sinon GLO décomposé (Erbs),
    sinon `radiation_fallback(dt) → (dni, dhi)` (Open-Meteo au même lieu)."""
    t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    n = int(((end - start).days + 1) * 24)
    lat, lon = meta['lat'], meta['lon']
    hourly = {}
    rad_source = set()
    for stamp, (t, glo, dirr, dif, ff) in rows.items():
        dt = datetime.strptime(stamp, '%Y%m%d%H').replace(tzinfo=timezone.utc)
        k = int((dt - t0).total_seconds() // 3600)
        if t is None or not (0 <= k < n):
            continue
        w = 10000.0 / 3600.0
        _az, el = weather_source.solar_position(lat, lon, dt)
        if dirr is not None and dif is not None:
            dhi = dif * w
            sin_h = math.sin(math.radians(el))
            dni = (dirr * w) / sin_h if sin_h > 0.05 else 0.0
            rad_source.add('station (direct + diffus)')
        elif glo is not None:
            dni, dhi = erbs_split(glo * w, el, dt.timetuple().tm_yday)
            rad_source.add('station (global, décomposé Erbs)')
        elif radiation_fallback is not None:
            dni, dhi = radiation_fallback(dt)
            rad_source.add('Open-Meteo (station sans rayonnement)')
        else:
            dni = dhi = None
        hourly[k] = (t, dni, dhi, ff)
    full, filled, missing = fill_gaps(hourly, t0, n)
    series = []
    for k in sorted(full):
        t, dni, dhi, ff = full[k]
        if t is None or dni is None:
            continue
        series.append(point(lat, lon, t0 + timedelta(hours=k), t, dni, dhi, ff, k))
    return series, {'filled_hours': filled, 'missing_hours': n - len(series), 'radiation': sorted(rad_source)}


def from_station(station, start, end, owner_sub=''):
    if end < start or (end - start).days > 366:
        raise WeatherError('Période invalide (un an au plus).')
    dep, sid = station['department'], station['id']

    def collect():
        rows, meta = {}, {}
        for period in mf_periods(start.year, end.year):
            r, m = parse_mf_series(_mf_lines(dep, period), sid, start, end)
            rows.update(r)
            meta.update(m)
        if not rows:
            raise WeatherError('Aucune donnée horaire pour cette station sur cette période.')
        return {'rows': rows, 'meta': meta}
    data = sources.cached('station', f'{sid}:{start}:{end}', collect, ttl_days=3650)
    fallback = None
    if not any(v[1] is not None or v[2] is not None for v in data['rows'].values()):
        try:
            om, _ = weather_source.build_weather_series(data['meta']['lat'], data['meta']['lon'],
                                                        start.isoformat(), end.isoformat())
            t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
            by_k = {p.get('hour_index'): (p['e_dir'], p['e_dif']) for p in om}
            fallback = lambda dt: by_k.get(int((dt - t0).total_seconds() // 3600), (0.0, 0.0))
        except weather_source.WeatherSourceError:
            fallback = None
    series, quality = series_from_station_rows(data['rows'], data['meta'], start, end, fallback)
    label = f'Station {station.get("name", sid)} ({sid}) {start:%d/%m/%Y} – {end:%d/%m/%Y}'
    return save('station', label, series, lat=data['meta']['lat'], lon=data['meta']['lon'], owner_sub=owner_sub,
                meta={'station_id': sid, 'station': station.get('name'), 'alt': station.get('alt'),
                      'start': start.isoformat(), 'end': end.isoformat(), 'distance_km': station.get('distance_km')},
                quality=quality)


# ── Climat futur TRACC ──────────────────────────────────────────────────────

def nearest_tracc_station(lat, lon):
    return min(TRACC_STATIONS, key=lambda s: distance_km(lat, lon, *TRACC_STATIONS[s][:2]))


def tracc_series_from_rows(rows, station):
    """Lignes ADEME (mois, jour, heure UTC, t2m, rdn, rdi, ff) → série de l'app (calendrier non bissextile)."""
    lat, lon, _alt = TRACC_STATIONS[station]
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    out = []
    for r in sorted(rows, key=lambda r: (r['mois'], r['jour'], r['heure'])):
        dt = datetime(2023, int(r['mois']), int(r['jour']), int(r['heure']), tzinfo=timezone.utc)
        k = int((dt - t0).total_seconds() // 3600)
        out.append(point(lat, lon, dt, float(r['t2m']), float(r.get('rdn') or 0), float(r.get('rdi') or 0),
                         float(r['ff']) if r.get('ff') is not None else None, k))
    return out


def from_tracc(lat, lon, scenario, owner_sub=''):
    if scenario not in TRACC_DATASETS:
        raise WeatherError('Scénario TRACC inconnu.')
    station = nearest_tracc_station(lat, lon)

    def fetch():
        url = TRACC_LINES_URL.format(id=TRACC_DATASETS[scenario])
        rows, after = [], None
        for _ in range(4):
            params = {'qs': f'station:"{station}"', 'size': 10000, 'select': 'mois,jour,heure,t2m,rdn,rdi,ff'}
            if after:
                params['after'] = after
            try:
                r = requests.get(url, params=params, headers=net.HEADERS, timeout=60)
                r.raise_for_status()
                d = r.json()
            except (requests.RequestException, ValueError) as exc:
                raise WeatherError(f'ADEME (TRACC) : {net.reason(exc)}') from exc
            rows += d.get('results') or []
            nxt = d.get('next')
            if not nxt or len(rows) >= 8760:
                break
            after = nxt.split('after=')[-1].split('&')[0]
        if len(rows) < 8000:
            raise WeatherError('Fichier TRACC incomplet.')
        return rows
    rows = sources.cached('tracc', f'{scenario}:{station}', fetch, ttl_days=3650)
    series = tracc_series_from_rows(rows, station)
    d = distance_km(lat, lon, *TRACC_STATIONS[station][:2])
    label = f'Climat futur TRACC {scenario.replace("-chaleur", "")} °C{" — vague de chaleur" if "chaleur" in scenario else ""} (station {station.title()})'
    return save('tracc', label, series, lat=TRACC_STATIONS[station][0], lon=TRACC_STATIONS[station][1], owner_sub=owner_sub,
                meta={'scenario': scenario, 'station': station, 'distance_km': round(d, 1),
                      'source': 'ADEME / Météo-France / CSTB / DHUP, données climatiques prospectives'},
                quality={'warning': f'Station de référence à {d:.0f} km' if d > 80 else None})


# ── Import EPW / CSV ────────────────────────────────────────────────────────

def parse_epw(text, owner_sub=''):
    lines = text.splitlines()
    if not lines or not lines[0].startswith('LOCATION'):
        raise WeatherError('Fichier EPW attendu (première ligne LOCATION).')
    loc = lines[0].split(',')
    try:
        lat, lon, tz = float(loc[6]), float(loc[7]), float(loc[8])
    except (IndexError, ValueError):
        raise WeatherError('En-tête LOCATION illisible.') from None
    data = [l.split(',') for l in lines[8:] if l.strip()]
    if not (24 <= len(data) <= 8784):
        raise WeatherError('Nombre d’heures inattendu dans le fichier EPW.')
    out = []
    t0 = None
    for k, r in enumerate(data):
        try:
            y, m, d, h = int(r[0]), int(r[1]), int(r[2]), int(r[3])
            t, dni, dhi, wind = float(r[6]), float(r[14]), float(r[15]), float(r[21])
        except (IndexError, ValueError):
            raise WeatherError(f'Ligne {k + 9} illisible.') from None
        # EPW : heure locale standard, h = 1..24 désigne l'heure qui FINIT à h.
        dt = datetime(2023, m, d, tzinfo=timezone.utc) + timedelta(hours=h - 0.5 - tz)
        t0 = t0 or dt
        out.append(point(lat, lon, dt, t, dni, dhi, wind, k))
    return out, lat, lon, loc[1] if len(loc) > 1 else 'EPW'


def parse_csv_weather(text, lat, lon):
    """CSV horaire : colonnes `date` (ISO, UTC), `t_ext`, puis `dni` + `dhi` ou `ghi` ; `vent` facultatif."""
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')), delimiter=';' if text.count(';') > text.count(',') else ',')
    out = []
    t0 = None
    for n, r in enumerate(reader, start=2):
        if n > 8786:
            raise WeatherError('Plus d’un an d’heures.')
        try:
            dt = datetime.fromisoformat(r['date'].strip().replace('Z', '+00:00'))
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            t = float(r['t_ext'].replace(',', '.'))
            wind = float(r['vent'].replace(',', '.')) if r.get('vent') else None
            t0 = t0 or dt
            _az, el = weather_source.solar_position(lat, lon, dt)
            if r.get('dni') not in (None, '') and r.get('dhi') not in (None, ''):
                dni, dhi = float(r['dni'].replace(',', '.')), float(r['dhi'].replace(',', '.'))
            else:
                dni, dhi = erbs_split(float(r['ghi'].replace(',', '.')), el, dt.timetuple().tm_yday)
        except (KeyError, ValueError, AttributeError, TypeError):
            raise WeatherError(f'Ligne {n} illisible (colonnes date, t_ext, dni+dhi ou ghi).') from None
        out.append(point(lat, lon, dt, t, dni, dhi, wind, int((dt - t0).total_seconds() // 3600)))
    if len(out) < 24:
        raise WeatherError('Au moins 24 heures attendues.')
    return out


# ── Période et usage par le calcul ──────────────────────────────────────────

def slice_period(series, start=None, end=None, dataset=None):
    """Sous-série d'une période (dates) : pour un jeu daté, par date réelle ; pour une année type, par
    mois et jour. Le `hour_index` est rebasé sur le début de la période."""
    if not start and not end:
        return series
    base = date(2023, 1, 1)
    if dataset is not None and dataset.meta.get('start'):
        base = date.fromisoformat(dataset.meta['start'])
    s = date.fromisoformat(start) if start else None
    e = date.fromisoformat(end) if end else None

    def day_of(p):
        return base + timedelta(days=(p.get('hour_index') or 0) // 24)
    out = []
    for p in series:
        d = day_of(p)
        if dataset is not None and dataset.kind in ('tmy', 'tracc'):
            key = (d.month, d.day)
            ok = (not s or key >= (s.month, s.day)) and (not e or key <= (e.month, e.day))
        else:
            ok = (not s or d >= s) and (not e or d <= e)
        if ok:
            out.append(p)
    if not out:
        raise WeatherError('Aucune heure dans cette période.')
    first = out[0].get('hour_index') or 0
    first -= first % 24
    return [{**p, 'hour_index': (p.get('hour_index') or 0) - first} for p in out]


def series_for(spec, building=None):
    """(série, descripteur) pour `calc_service.resolve_weather` : {'dataset_id', 'start'?, 'end'?}."""
    from .models import WeatherDataset
    ds = WeatherDataset.objects.filter(pk=spec.get('dataset_id')).first()
    if ds is None:
        from .calc_service import CalcError
        raise CalcError('Jeu météo inconnu.')
    try:
        series = slice_period(ds.series, spec.get('start'), spec.get('end'), ds)
    except WeatherError as exc:
        from .calc_service import CalcError
        raise CalcError(str(exc)) from exc
    desc = {'source': ds.label, 'dataset_id': ds.pk, 'kind': ds.kind, 'n_hours': len(series),
            'start': spec.get('start'), 'end': spec.get('end'), 'hash': stable_hash(ds.series_hash, spec.get('start'), spec.get('end'))[:16]}
    return series, desc
