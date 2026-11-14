# Copie d'app-builder `0ff4e4e` (backend/api/ia/catalogue.py) — pratique du lab : même coffre, mêmes fournisseurs.
# Écarts : registre local (api/ia/registre.py) au lieu de api/budget.py.
"""Catalogue dynamique : les modèles d'un fournisseur lus chez lui, avec la clé de l'utilisateur.

Sert aux fournisseurs dont la liste change souvent (Albert, DINUM : un modèle retiré le
2026-10-31, un autre annoncé pour le 2026-12-01). `GET <url>/models` est compatible
OpenAI ; Albert y ajoute `type` (on ne garde que `text-generation`) et
`max_context_length` (la fenêtre).

La liste est gardée en cache (mémoire du processus, 10 min) par fournisseur et par
empreinte de clé : une clé n'est jamais une clé de cache en clair. Chaque modèle lu est
aussi gardé seul (`fiche_connue`), sans la clé : `budget.modele()` le retrouve alors sans
appel réseau. La fenêtre d'un modèle n'est pas un secret.

Panne, clé refusée, réponse illisible : la table fixe de `budget.MODELES` sert de repli,
sans erreur pour l'utilisateur (le chat dira lui-même si la clé est refusée).
"""
import hashlib
import logging

import requests as http_requests
from django.core.cache import cache

log = logging.getLogger(__name__)

DUREE_S = 600
SORTIE_PAR_DEFAUT = 8_192
_FENETRE_PAR_DEFAUT = 32_000
# Classés `text-generation` chez Albert mais qui ne conversent pas.
_EXCLUS = ('ocr',)


def _cle_cache(provider: str, cle: str) -> str:
    return f'ia-catalogue:{provider}:{hashlib.sha256(cle.encode()).hexdigest()[:24]}'


def _cle_fiche(provider: str, identifiant: str) -> str:
    return f'ia-modele:{provider}:{hashlib.sha256(identifiant.encode()).hexdigest()[:24]}'


def fiche_connue(provider: str, identifiant: str) -> dict | None:
    """La fiche d'un modèle déjà lu chez son fournisseur (aucun appel réseau)."""
    if not isinstance(identifiant, str) or not identifiant:
        return None
    return cache.get(_cle_fiche(provider, identifiant))


def _fiche(provider: str, brut: dict) -> dict | None:
    identifiant = brut.get('id')
    if not isinstance(identifiant, str) or not identifiant or len(identifiant) > 200:
        return None
    genre = brut.get('type')
    if genre is not None and genre != 'text-generation':
        return None
    if any(x in identifiant.lower() for x in _EXCLUS):
        return None
    fenetre = brut.get('max_context_length')
    fenetre = fenetre if isinstance(fenetre, int) and not isinstance(fenetre, bool) and fenetre > 0 else _FENETRE_PAR_DEFAUT
    return {'id': identifiant, 'provider': provider, 'label': identifiant,
            'context_window': fenetre, 'max_output': min(SORTIE_PAR_DEFAUT, max(fenetre // 4, 1_024)),
            # Albert : tous les modèles de génération listés par la DINUM appellent des outils.
            'tools': True}


def lire(provider: str, cle: str) -> list[dict] | None:
    """Les modèles de `provider` lus chez lui avec `cle` ; None si indisponible."""
    from . import registre as budget
    f = budget.FOURNISSEURS.get(provider)
    if not f or f.get('catalogue') != 'dynamique' or not cle:
        return None
    memo = cache.get(_cle_cache(provider, cle))
    if memo is not None:
        return memo
    try:
        resp = http_requests.get(f"{f['url']}/models", headers={'Authorization': f'Bearer {cle}'}, timeout=(5, 10))
        resp.raise_for_status()
        donnees = resp.json()
        bruts = donnees.get('data') if isinstance(donnees, dict) else donnees
        if not isinstance(bruts, list):
            raise ValueError('liste attendue')
    except Exception as exc:  # noqa: BLE001 — jamais le texte de l'exception (il peut citer l'URL)
        log.warning('Catalogue %s indisponible (%s).', provider, type(exc).__name__)
        return None
    fiches = []
    for b in bruts:
        fiche = _fiche(provider, b) if isinstance(b, dict) else None
        if fiche:
            fiches.append(fiche)
    fiches.sort(key=lambda m: m['id'])
    for fiche in fiches:
        cache.set(_cle_fiche(provider, fiche['id']), fiche, DUREE_S * 6)
    cache.set(_cle_cache(provider, cle), fiches, DUREE_S)
    return fiches


def modeles(provider: str, cle: str) -> list[dict]:
    """Ce que l'interface propose pour `provider` : les modèles connus de la table fixe
    (libellés soignés, en tête), puis ceux lus chez le fournisseur qui n'y sont pas."""
    from . import registre as budget
    fixes = budget.modeles_fixes(provider)
    lus = lire(provider, cle) or []
    connus = {m['id'] for m in fixes}
    return fixes + [{c: m[c] for c in budget._CHAMPS_PUBLICS} for m in lus if m['id'] not in connus]
