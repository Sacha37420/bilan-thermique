"""Outils de test partagés (refonte) — pas un module de tests lui-même.

Patron commun du lab : jetons RS256 signés par une clé locale, passant par la **vraie** classe
d'authentification (`KeycloakJWTAuthentication`) ; seul le téléchargement du JWKS est remplacé.
Aucun appel réseau.
"""

import time
import uuid
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from rest_framework.test import APIClient

from .authentication import KeycloakJWTAuthentication

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


class _FakeSigningKey:
    key = _PUBLIC_KEY


class _FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey()


def token_for(sub=None, email=None, groups=None, azp=None, exp_in=600, **extra):
    """Jeton d'accès signé par la clé de test."""
    sub = sub or str(uuid.uuid4())
    claims = {
        'sub': sub,
        'email': email if email is not None else f'{sub[:8]}@exemple.test',
        'preferred_username': (email or sub)[:20],
        'azp': azp or settings.KEYCLOAK_CLIENT_ID,
        'groups': groups if groups is not None else _required_groups()[:1] or ['developers'],
        'exp': int(time.time()) + exp_in,
        'iat': int(time.time()),
    }
    claims.update(extra)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm='RS256')


def _required_groups():
    return [g.strip() for g in settings.KEYCLOAK_REQUIRED_GROUPS.split(',') if g.strip()]


def patch_jwks():
    """À utiliser en décorateur de classe ou en contexte : remplace seulement le JWKS."""
    return mock.patch.object(KeycloakJWTAuthentication, '_get_jwks_client',
                             classmethod(lambda cls: _FakeJwks()))


def without_auto_environment():
    """Tests qui ne portent pas sur l'environnement relevé : réglage « à la demande ». Depuis le Lot 30, la
    chaîne et le N2 génèrent par défaut l'environnement LiDAR — une vraie génération (réseau fermé : reprises
    et délais) que ces tests n'ont pas à jouer. À poser dans `setUpModule` / `tearDownModule`."""
    return mock.patch('api.environment_service.auto_mode', return_value='manuel')


def client_for(sub=None, **kwargs):
    """APIClient authentifié par un jeton de test (`sub` donné ou tiré au hasard)."""
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {token_for(sub=sub, **kwargs)}')
    return c


def box_envelope(w=10.0, d=8.0, h=6.0, paroi_id=None, glazing_id=None):
    """Boîte fermée (12 triangles) normales sortantes, Z vers le haut, paroi assignée."""
    from . import geometry
    v = [[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0], [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]]
    faces = [  # (indices, groupe)
        ((0, 2, 1), 'sol'), ((0, 3, 2), 'sol'),
        ((4, 5, 6), 'toiture'), ((4, 6, 7), 'toiture'),
        ((0, 1, 5), 'mur_sud'), ((0, 5, 4), 'mur_sud'),
        ((1, 2, 6), 'mur_est'), ((1, 6, 5), 'mur_est'),
        ((2, 3, 7), 'mur_nord'), ((2, 7, 6), 'mur_nord'),
        ((3, 0, 4), 'mur_ouest'), ((3, 4, 7), 'mur_ouest'),
    ]
    tris = [{'v': list(ix), 'group': g, 'paroi_model_id': paroi_id} for ix, g in faces]
    tris[0]['boundary'] = tris[1]['boundary'] = 'ground'
    return {'vertices': v, 'triangles': geometry.compute_envelope_geometry(v, tris)}


def synthetic_year(lat=47.39, lon=0.69, days=365, start_day=0, t_mean=11.5, t_amp=8.0, cloud=(1.0, 0.45, 0.75)):
    """Année météo synthétique (sans réseau) au format de l'app : température annuelle et
    journalière sinusoïdales, soleil réel (Spencer), ciel clair atténué par un motif nuageux."""
    import math
    from datetime import datetime, timedelta, timezone
    from . import weather_source
    out = []
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=start_day)
    for h in range(days * 24):
        dt = t0 + timedelta(hours=h)
        d = (start_day * 24 + h) / 24.0
        az, el = weather_source.solar_position(lat, lon, dt)
        c = cloud[int(d) % len(cloud)]
        s = math.sin(math.radians(el))
        dni = 850.0 * math.exp(-0.14 / s) * c if el > 2 else 0.0
        dhi = (60.0 + 90.0 * (1 - c)) * s if el > 0 else 0.0
        t = t_mean - t_amp * math.cos(2 * math.pi * (d - 20) / 365.0) + 3.5 * math.sin(2 * math.pi * ((h % 24) - 9) / 24.0)
        out.append({'t_ext': round(t, 2), 'sun_azimuth': round(az, 2), 'sun_elevation': round(el, 2),
                    'e_dir': round(dni, 1), 'e_dif': round(dhi, 1), 'wind_m_s': 3.0, 'hour_index': h})
    return out


def box_with_window(w=10.0, d=8.0, h=3.0, wall_id=None, roof_id=None, floor_id=None, glass_id=None,
                    win_ratio_south=0.3):
    """Boîte fermée dont le mur sud porte une baie rectangulaire (triangles de vitrage distincts)."""
    from . import geometry
    import math as _m
    wx = w * _m.sqrt(win_ratio_south)
    wz = h * _m.sqrt(win_ratio_south)
    x0, x1 = (w - wx) / 2, (w + wx) / 2
    z0, z1 = (h - wz) / 2, (h + wz) / 2
    v = [[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0], [0, 0, h], [w, 0, h], [w, d, h], [0, d, h],
         [x0, 0, z0], [x1, 0, z0], [x1, 0, z1], [x0, 0, z1]]  # 8..11 : baie au sud (y = 0)
    W, R, F, G = wall_id, roof_id or wall_id, floor_id or wall_id, glass_id
    faces = [
        ((0, 2, 1), 'sol', F), ((0, 3, 2), 'sol', F),
        ((4, 5, 6), 'toiture', R), ((4, 6, 7), 'toiture', R),
        ((1, 2, 6), 'mur_est', W), ((1, 6, 5), 'mur_est', W),
        ((2, 3, 7), 'mur_nord', W), ((2, 7, 6), 'mur_nord', W),
        ((3, 0, 4), 'mur_ouest', W), ((3, 4, 7), 'mur_ouest', W),
        # mur sud percé : 8 triangles autour de la baie, puis 2 triangles de vitrage
        ((0, 1, 9), 'mur_sud', W), ((0, 9, 8), 'mur_sud', W),
        ((1, 5, 10), 'mur_sud', W), ((1, 10, 9), 'mur_sud', W),
        ((5, 4, 11), 'mur_sud', W), ((5, 11, 10), 'mur_sud', W),
        ((4, 0, 8), 'mur_sud', W), ((4, 8, 11), 'mur_sud', W),
        ((8, 9, 10), 'baie_sud', G), ((8, 10, 11), 'baie_sud', G),
    ]
    tris = [{'v': list(ix), 'group': g, 'paroi_model_id': pid} for ix, g, pid in faces]
    tris[0]['boundary'] = tris[1]['boundary'] = 'ground'
    return {'vertices': v, 'triangles': geometry.compute_envelope_geometry(v, tris)}
