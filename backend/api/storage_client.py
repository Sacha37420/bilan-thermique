"""Dépôt des originaux dans `storage`, espace personnel de l'utilisateur, avec SON jeton (Lot 21).

Variante `carto-lab` (jeton transmis) : n'a besoin que de `bilan-thermique` dans la liste blanche
`KEYCLOAK_TRUSTED_CLIENTS` de `storage` (DEMANDES_LAB.md, DL-2). Désactivé tant que
`STORAGE_DOCUMENTS_ENABLED` n'est pas vrai : l'original n'est alors pas gardé. Aucune route de
`storage` n'est modifiée. Message d'erreur fixe (jamais le texte d'une exception `requests`).
"""

import re
import uuid

import requests
from django.conf import settings

from . import net


class StorageError(ValueError):
    pass


def enabled():
    return bool(settings.STORAGE_DOCUMENTS_ENABLED)


def _safe(name):
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name)[-120:] or 'document'


def upload_personal(token, username, building_id, filename, data, content_type='application/octet-stream'):
    """Rend le chemin relatif dans l'espace `username`, ou lève StorageError."""
    rel = f'bilan-thermique/batiment-{int(building_id)}/{uuid.uuid4().hex}-{_safe(filename)}'
    try:
        r = requests.post(f'{settings.STORAGE_API_URL.rstrip("/")}/api/files/{username}/',
                          headers={'Authorization': f'Bearer {token}', 'User-Agent': net.USER_AGENT},
                          data={'relative_path': rel}, files={'file': (_safe(filename), data, content_type)}, timeout=30)
    except requests.RequestException as exc:
        raise StorageError(f'storage : {net.reason(exc)}') from exc
    if r.status_code != 201:
        raise StorageError(f'storage : réponse HTTP {r.status_code}')
    return rel
