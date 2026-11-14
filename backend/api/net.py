"""Appels sortants : identité de l'app et messages d'erreur sans fuite (Lot 2, refonte).

Deux règles du gabarit du lab, tenues ici en un seul endroit :

- **Une exception `requests` porte l'URL demandée** (paramètres compris : coordonnées,
  identifiants, parfois une clé). Son texte ne se renvoie ni ne se journalise : `reason(exc)`
  rend une cause fixe (« délai dépassé », « réponse HTTP 503 »…), jamais `str(exc)`.
- **Aucune donnée personnelle dans une requête vers un service externe** : le `User-Agent`
  nomme l'app, pas une personne (les anciens `User-Agent` portaient une adresse email).

Les erreurs métier de l'app (`BdnbError`, `GeodataError`, `BuildingSimulationError`…) sont
définies dans `api.*` et construites avec des messages fixes : `reason()` les laisse passer
telles quelles, ce qui permet d'emboîter les messages (« BD TOPO indisponible (délai dépassé) »).
Toute autre exception est réduite à son type.
"""

import requests

USER_AGENT = 'bilan-thermique-lab/1.0 (+https://github.com/Sacha37420/bilan-thermique)'
HEADERS = {'User-Agent': USER_AGENT}


def is_domain_error(exc) -> bool:
    """Erreur levée par le code de l'app (message construit par nous, sans URL)."""
    return type(exc).__module__.startswith('api.')


def reason(exc) -> str:
    """Cause d'échec affichable — jamais le texte d'une exception `requests`."""
    if is_domain_error(exc):
        return str(exc)
    if isinstance(exc, requests.Timeout):
        return 'délai dépassé'
    if isinstance(exc, requests.ConnectionError):
        return 'connexion impossible'
    if isinstance(exc, requests.HTTPError):
        code = getattr(getattr(exc, 'response', None), 'status_code', None)
        return f'réponse HTTP {code}' if code else 'réponse HTTP en erreur'
    if isinstance(exc, requests.RequestException):
        return 'requête refusée'
    # Autres bibliothèques (urllib3, laspy, onnxruntime…) : leur texte peut citer une adresse.
    # On ne le garde que s'il ne ressemble en rien à une URL ni à une requête.
    text = str(exc)
    if text and len(text) <= 200 and not any(m in text.lower() for m in ('://', 'http', '?', 'www.', '&')):
        return text
    if isinstance(exc, ValueError):
        return 'réponse invalide'
    return f'erreur {type(exc).__name__}'


def public_message(exc, default='Erreur inattendue pendant le calcul.') -> str:
    """Message d'un job en erreur : celui d'une erreur métier, sinon un message fixe."""
    if is_domain_error(exc):
        return str(exc)[:500]
    return f'{default} ({reason(exc)})'
