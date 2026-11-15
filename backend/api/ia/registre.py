# Copie d'app-builder `0ff4e4e` (backend/api/budget.py, parties « modèles » et « fournisseurs ») —
# registre FERMÉ des fournisseurs d'IA : aucune adresse saisie par l'utilisateur (risque SSRF).
# Écarts : seules les parties utiles à la lecture de documents ; modèle Claude par défaut
# `claude-opus-5-5` (référence API Claude en vigueur) au lieu de `claude-sonnet-5-5`.
"""Registre des fournisseurs et modèles d'IA (Lot 21, refonte)."""
import math

MODELES = (
    {'id': 'claude-sonnet-5-5', 'provider': 'claude', 'label': 'Sonnet 5.5 — Recommandé ★',
     'context_window': 1_000_000, 'max_output': 128_000, 'tools': True,
     'effort': 'medium', 'thinking': None, 'fallbacks': True, 'cache_min': 512},
    {'id': 'claude-opus-5-5', 'provider': 'claude', 'label': 'Opus 5.5 — Le plus puissant',
     'context_window': 1_000_000, 'max_output': 128_000, 'tools': True,
     'effort': 'medium', 'thinking': None, 'fallbacks': True, 'cache_min': 512},
    {'id': 'claude-haiku-5-5', 'provider': 'claude', 'label': 'Haiku 5.5 — Rapide / économique',
     'context_window': 1_000_000, 'max_output': 128_000, 'tools': True,
     'effort': 'low', 'thinking': None, 'fallbacks': False, 'cache_min': 512},
    {'id': 'claude-sonnet-4-6', 'provider': 'claude', 'label': 'Sonnet 4.6 — Génération précédente',
     'context_window': 1_000_000, 'max_output': 128_000, 'tools': True,
     'effort': 'medium', 'thinking': 'adaptive', 'fallbacks': False, 'cache_min': 1024, 'strict': False},
    {'id': 'claude-opus-4-8', 'provider': 'claude', 'label': 'Opus 4.8 — Génération précédente',
     'context_window': 1_000_000, 'max_output': 128_000, 'tools': True,
     'effort': 'medium', 'thinking': 'adaptive', 'fallbacks': False, 'cache_min': 1024},
    {'id': 'claude-haiku-4-5', 'provider': 'claude', 'label': 'Haiku 4.5 — Génération précédente',
     'context_window': 200_000, 'max_output': 64_000, 'tools': True,
     'effort': None, 'thinking': None, 'fallbacks': False, 'cache_min': 4096},
    {'id': 'mistral-small-latest', 'provider': 'mistral', 'label': 'Small — Recommandé ★',
     'context_window': 32_000, 'max_output': 8_192, 'tools': True},
    {'id': 'mistral-medium-latest', 'provider': 'mistral', 'label': 'Medium — Équilibré',
     'context_window': 128_000, 'max_output': 8_192, 'tools': True},
    {'id': 'mistral-large-latest', 'provider': 'mistral', 'label': 'Large — Le plus puissant',
     'context_window': 128_000, 'max_output': 8_192, 'tools': True},
    {'id': 'codestral-latest', 'provider': 'mistral', 'label': 'Codestral — Spécialisé code',
     'context_window': 32_000, 'max_output': 8_192, 'tools': True},
    # Albert (DINUM) — table PRUDENTE d'après la doc du 2026-11-11 (guides.ia.numerique.gouv.fr,
    # « Modèles disponibles ») ; la vraie liste est lue par `GET /v1/models` avec la clé de
    # l'utilisateur (api/ia/catalogue.py) et la complète. Les alias `openweight-*` survivent aux
    # remplacements de modèle annoncés par la DINUM.
    {'id': 'openweight-large', 'provider': 'albert', 'label': 'openweight-large (gpt-oss-120b) — Recommandé ★',
     'context_window': 131_072, 'max_output': 8_192, 'tools': True},
    {'id': 'openweight-medium', 'provider': 'albert', 'label': 'openweight-medium (Mistral Small 3.2)',
     'context_window': 131_072, 'max_output': 8_192, 'tools': True},
    {'id': 'openweight-small', 'provider': 'albert', 'label': 'openweight-small (Ministral 3 8B) — Rapide',
     'context_window': 262_144, 'max_output': 8_192, 'tools': True},
)
MODELE_PAR_DEFAUT = {'claude': 'claude-opus-5-5', 'mistral': 'mistral-small-latest', 'albert': 'openweight-large'}

FOURNISSEURS = {
    'claude': {'label': 'Claude (Anthropic)', 'protocole': 'anthropic', 'champ': 'claude_key',
               'catalogue': 'fixe', 'aide': 'Clé d’API Anthropic (sk-ant-api…), console.anthropic.com.'},
    'mistral': {'label': 'Mistral', 'protocole': 'openai', 'url': 'https://api.mistral.ai/v1',
                'champ': 'mistral_key', 'catalogue': 'fixe', 'aide': 'Clé d’API Mistral, console.mistral.ai.'},
    'albert': {'label': 'Albert (DINUM)', 'protocole': 'openai', 'url': 'https://albert.api.etalab.gouv.fr/v1',
               'champ': 'albert_key', 'catalogue': 'dynamique',
               'aide': 'Clé d’API Albert (agents publics, accès par ProConnect) — guides.ia.numerique.gouv.fr/albert-api.'},
}
_FENETRE_INCONNUE = 32_000
SORTIE_PAR_APPEL = {'claude': 16_000, 'mistral': 8_192, 'albert': 8_192}
_OCTETS_PAR_JETON = 3
_CHAMPS_PUBLICS = ('id', 'label', 'context_window', 'max_output', 'tools')
_SIGNES_DE_DEPASSEMENT = ('prompt is too long', 'context window', 'context length', 'context_length',
                          'too many tokens', 'maximum context', 'too large for model', 'input is too long')


def estimer_jetons(texte) -> int:
    if not texte:
        return 0
    return math.ceil(len(str(texte).encode('utf-8')) / _OCTETS_PAR_JETON)


def fournisseur_existe(provider) -> bool:
    return isinstance(provider, str) and provider in FOURNISSEURS


def modele(provider, identifiant=None) -> dict:
    identifiant = identifiant or MODELE_PAR_DEFAUT[provider]
    for m in MODELES:
        if m['id'] == identifiant and m['provider'] == provider:
            return dict(m)
    from .catalogue import fiche_connue
    connu = fiche_connue(provider, identifiant)
    if connu:
        return dict(connu)
    return {'id': identifiant, 'provider': provider, 'label': identifiant, 'context_window': _FENETRE_INCONNUE,
            'max_output': 4_096, 'tools': False, 'effort': None, 'thinking': None, 'fallbacks': False, 'cache_min': 0}


def sortie(fiche: dict) -> int:
    return min(fiche['max_output'], SORTIE_PAR_APPEL[fiche['provider']])


def modeles_fixes(provider: str) -> list[dict]:
    return [{c: m[c] for c in _CHAMPS_PUBLICS} for m in MODELES if m['provider'] == provider]


def est_un_depassement(erreur) -> bool:
    texte = str(erreur).lower()
    reponse = getattr(erreur, 'response', None)
    try:
        texte += ' ' + (getattr(reponse, 'text', '') or '').lower()
    except Exception:  # noqa: BLE001
        pass
    return any(s in texte for s in _SIGNES_DE_DEPASSEMENT)
