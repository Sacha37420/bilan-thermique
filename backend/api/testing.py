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
