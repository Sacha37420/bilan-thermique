# Copie d'app-builder `0ff4e4e` (backend/api/fields.py) — pratique du lab : même coffre, mêmes fournisseurs.
# Écarts : aucun (l'identité du coffre est le sub Keycloak, dans le modèle et non dans ce champ).
"""Champ chiffré au repos — coffre des clés d'API (contrat « failles », I3).

Clé : `settings.VAULT_ENCRYPTION_KEY` (clé Fernet, `.env`), jamais dérivée de
`SECRET_KEY` : faire tourner `SECRET_KEY` ne doit ni détruire ni livrer les
secrets. Ce coffre est neuf, il n'a aucun ancien chiffrement à relire.

Une valeur qui ne se déchiffre pas est traitée comme ABSENTE (journalisée sans
sa valeur) — jamais rendue telle quelle : le texte chiffré n'est pas une clé.
Seule exception, une valeur qui n'a pas la forme d'un jeton Fernet (`gAAAAA…`) :
valeur historique en clair, rendue telle quelle (et rechiffrée au prochain
enregistrement).
"""
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

log = logging.getLogger(__name__)

_FERNET_PREFIX = 'gAAAAA'


def _fernet() -> Fernet:
    return Fernet(settings.VAULT_ENCRYPTION_KEY.encode())


def chiffrer(valeur: str) -> str:
    return _fernet().encrypt(valeur.encode()).decode()


def dechiffrer(valeur: str) -> str | None:
    if not valeur.startswith(_FERNET_PREFIX):
        return valeur
    try:
        return _fernet().decrypt(valeur.encode()).decode()
    except (InvalidToken, ValueError):
        log.warning('Coffre : une valeur chiffrée ne se déchiffre pas avec VAULT_ENCRYPTION_KEY ; traitée comme absente.')
        return None


class EncryptedTextField(models.TextField):

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return chiffrer(value)

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        clair = dechiffrer(value)
        return '' if clair is None else clair
