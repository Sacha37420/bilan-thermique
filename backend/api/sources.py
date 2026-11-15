"""Sources open data de la chaîne automatique (Lot 9, refonte) : cache, quota, messages fixes.

- **Cache** (`SourceCache`) : chaque réponse est gardée par clé (service + identifiant), avec une
  durée de vie ; relancer un parc ne refait pas le réseau.
- **Quota BDNB** : 120 requêtes/min (documenté par le service) — au plus une requête toutes les
  0,5 s. Le worker est en concurrence 1, l'intervalle tenu en mémoire suffit.
- **Erreurs** : `SourceError`, message fixe (`net.reason`), jamais l'URL.
- **Identité** : `User-Agent` de l'app (`net.USER_AGENT`), aucune donnée personnelle.
"""

import threading
import time
from datetime import timedelta

import requests
from django.utils import timezone

from . import net

GEOCODE_URL = 'https://data.geopf.fr/geocodage/search'
RNB_URL = 'https://rnb-api.beta.gouv.fr/api/alpha/buildings'
BDNB_URL = 'https://api.bdnb.io/v1/bdnb/donnees'
TIMEOUT_S = 25
BDNB_MIN_INTERVAL_S = 0.5
TTL_DAYS = {'geocode': 90, 'rnb': 30, 'rnb_shape': 30, 'bdnb': 60, 'bdtopo': 60, 'pvgis_tmy': 365,
            'open_meteo': 3650, 'station': 3650}

_bdnb_lock = threading.Lock()
_bdnb_last = [0.0]


class SourceError(ValueError):
    pass


def _throttle_bdnb():
    with _bdnb_lock:
        wait = _bdnb_last[0] + BDNB_MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _bdnb_last[0] = time.monotonic()


def _get_json(label, url, params=None):
    if url.startswith(BDNB_URL):
        _throttle_bdnb()
    try:
        r = requests.get(url, params=params, headers=net.HEADERS, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        raise SourceError(f'{label} : {net.reason(exc)}') from exc


def cached(service, ident, fetch, ttl_days=None):
    """Réponse en cache si fraîche, sinon `fetch()` puis mise en cache. Une erreur n'est pas cachée."""
    from .models import SourceCache
    key = f'{service}:{ident}'[:300]
    ttl = timedelta(days=ttl_days if ttl_days is not None else TTL_DAYS.get(service, 30))
    row = SourceCache.objects.filter(key=key).first()
    if row is not None and row.fetched_at >= timezone.now() - ttl:
        return row.payload
    payload = fetch()
    SourceCache.objects.update_or_create(key=key, defaults={'payload': payload, 'fetched_at': timezone.now(),
                                                            'service': service})
    return payload


# ── A1 : identification ─────────────────────────────────────────────────────

def geocode(address):
    q = ' '.join(str(address).split())[:200]
    if len(q) < 3:
        raise SourceError('Adresse trop courte.')

    def fetch():
        data = _get_json('Géocodage (BAN)', GEOCODE_URL, {'q': q, 'limit': 1, 'index': 'address'})
        feats = data.get('features') or []
        if not feats:
            return None
        f = feats[0]
        lon, lat = f['geometry']['coordinates'][:2]
        p = f.get('properties') or {}
        return {'lat': lat, 'lon': lon, 'label': p.get('label') or q, 'score': p.get('score'),
                'citycode': p.get('citycode'), 'type': p.get('type')}
    res = cached('geocode', q.lower(), fetch)
    if not res:
        raise SourceError('Adresse introuvable.')
    return res


def rnb_closest(lat, lon, radius_m=25):
    def fetch():
        data = _get_json('RNB', f'{RNB_URL}/closest/', {'point': f'{lat:.6f},{lon:.6f}', 'radius': radius_m})
        rows = [r for r in (data.get('results') or []) if r.get('rnb_id') and r.get('status', 'constructed') == 'constructed']
        if not rows:
            return None
        r = min(rows, key=lambda x: x.get('distance') or 0)
        lon_p, lat_p = (r.get('point') or {}).get('coordinates', [lon, lat])[:2]
        return {'rnb_id': r['rnb_id'], 'distance': r.get('distance'), 'lat': lat_p, 'lon': lon_p,
                'addresses': [' '.join(str(a.get(k) or '') for k in ('street_number', 'street', 'city_zipcode', 'city_name')).strip()
                              for a in (r.get('addresses') or [])][:5]}
    res = cached('rnb', f'{lat:.5f},{lon:.5f},{radius_m}', fetch)
    if not res:
        raise SourceError('Aucun bâtiment du RNB à cette position.')
    return res


def rnb_shape(rnb_id):
    def fetch():
        data = _get_json('RNB', f'{RNB_URL}/{rnb_id}/')
        return {'shape': data.get('shape'), 'point': data.get('point')}
    return cached('rnb_shape', rnb_id, fetch)


# ── A2 : fiche BDNB ─────────────────────────────────────────────────────────

GROUPE_FIELDS = ('batiment_groupe_id,annee_construction,usage_niveau_1_txt,usage_principal_bdnb_open,nb_log,'
                 'nb_niveau,hauteur_mean,s_geom_groupe,surface_emprise_sol,mat_mur_txt,mat_toit_txt,'
                 'libelle_adr_principale_ban,indicateur_distance_au_reseau,altitude_sol_mean')


def bdnb_for_rnb(rnb_id):
    """{'groupes': [...], 'dpe': [...]} pour un identifiant RNB ; listes vides si inconnu."""
    def fetch():
        cons = _get_json('BDNB', f'{BDNB_URL}/batiment_construction',
                         {'rnb_id': f'eq.{rnb_id}', 'select': 'batiment_groupe_id'})
        groups = sorted({c['batiment_groupe_id'] for c in cons or [] if c.get('batiment_groupe_id')})
        if not groups:
            return {'groupes': [], 'dpe': []}
        gl = ','.join(groups[:10])
        groupes = _get_json('BDNB', f'{BDNB_URL}/batiment_groupe_complet',
                            {'batiment_groupe_id': f'in.({gl})', 'select': GROUPE_FIELDS})
        dpe = _get_json('BDNB', f'{BDNB_URL}/batiment_groupe_dpe_representatif_logement',
                        {'batiment_groupe_id': f'in.({gl})'})
        strip = lambda d: {k: v for k, v in d.items() if v not in (None, '') and not k.startswith('geom')}
        return {'groupes': [strip(g) for g in groupes or []], 'dpe': [strip(d) for d in dpe or []]}
    return cached('bdnb', rnb_id, fetch)
