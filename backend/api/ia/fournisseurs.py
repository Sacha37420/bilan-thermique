# Copie d'app-builder `0ff4e4e` (backend/api/ia/fournisseurs.py) — pratique du lab : même coffre, mêmes fournisseurs.
# Écarts : « from .. import budget » → « from . import registre as budget » (registre réduit, propre à l'app).
"""Adaptateur unique Anthropic / compatible OpenAI (Mistral, Albert) : un appel non streamé, réponse normalisée.

Un tour de boucle ne connaît que `Reponse` (texte, appels d'outils, arrêt, usage)
et deux fonctions : `appeler` et `ajouter_resultats`. Les messages propres à
chaque fournisseur (blocs `tool_use`/`tool_result` d'Anthropic, `tool_calls` et
messages `tool` de Mistral) ne vivent que dans la liste de messages du tour :
ils ne sortent jamais du tour (l'historique entre deux tours est du texte).

Anthropic : SDK officiel (`anthropic==1.13.0`), point beta seulement pour le
repli serveur sur refus (`fallbacks: "default"`) des modèles qui le prennent.
Réponse de l'assistant rejouée ENTIÈRE dans le tour (réflexion comprise : on
n'édite jamais l'historique). Cache : bloc system stable marqué `ephemeral`,
plus le cache automatique de premier niveau pour la queue du tour.

Compatible OpenAI (Mistral, Albert — adresse dans `budget.FOURNISSEURS`) : HTTP
(`requests`), `tool_calls[].function.arguments` est une chaîne JSON à décoder —
illisible ⇒ résultat d'erreur rendu au modèle. `tool_choice: "auto"` toujours
explicite (DeepSeek V4 sur Albert n'émet aucun appel d'outil sans lui). Un appel
d'outil peut venir avec un contenu vide (Gemma 4 sur Albert) : c'est normal.
"""
import json
from dataclasses import dataclass, field

import requests as http_requests

MISTRAL_URL = 'https://api.mistral.ai/v1/chat/completions'
# Où régler une clé ou un modèle (les messages d'erreur y renvoient).
PAGE_IA = 'la page « Intelligence artificielle »'
BETA_FALLBACK = 'server-side-fallback-2026-07-01'


class ErreurFournisseur(Exception):
    """Panne du fournisseur, traduite en message FIXE (jamais le texte de
    l'exception d'origine : il peut citer la requête). `statut` : 502 ou 504."""

    def __init__(self, statut: int, message: str, depassement: bool = False):
        super().__init__(message)
        self.statut = statut
        self.message = message
        self.depassement = depassement


@dataclass
class Appel:
    id: str
    nom: str
    entree: object = None          # dict décodé, ou None si illisible
    illisible: bool = False


@dataclass
class Reponse:
    texte: str = ''
    appels: list = field(default_factory=list)
    arret: str = 'fin'             # fin | outils | tronque | refus
    usage: dict = field(default_factory=dict)
    message_assistant: object = None   # à rejouer tel quel dans le tour


def _jetons(usage, nom) -> int:
    v = getattr(usage, nom, None) if not isinstance(usage, dict) else usage.get(nom)
    return v if isinstance(v, int) else 0


# ── Anthropic ─────────────────────────────────────────────────────────────────

def client_anthropic(cle: str):
    import anthropic
    # Une seule reprise : le tour a sa propre horloge (≈ 90 s).
    return anthropic.Anthropic(api_key=cle, max_retries=1)


def _erreur_anthropic(exc) -> ErreurFournisseur:
    import anthropic
    from . import registre as budget
    if isinstance(exc, anthropic.APITimeoutError):
        return ErreurFournisseur(504, 'Le fournisseur claude n’a pas répondu à temps. Réessayez.')
    if budget.est_un_depassement(exc):
        return ErreurFournisseur(413, '', depassement=True)
    if isinstance(exc, anthropic.AuthenticationError) or isinstance(exc, anthropic.PermissionDeniedError):
        return ErreurFournisseur(502, f'Clé claude refusée par le fournisseur : vérifiez-la sur {PAGE_IA}.')
    if isinstance(exc, anthropic.RateLimitError):
        return ErreurFournisseur(502, 'Quota du fournisseur claude atteint : réessayez dans quelques instants.')
    return ErreurFournisseur(502, 'L’appel au fournisseur claude a échoué. Vérifiez votre clé ou réessayez.')


def appeler_anthropic(client, fiche: dict, systeme_stable: str, systeme_variable: str, messages: list,
                      outils: list | None, max_tokens: int, timeout: float, format_json=None) -> Reponse:
    params = {
        'model': fiche['id'], 'max_tokens': max_tokens, 'messages': messages,
        'system': [{'type': 'text', 'text': systeme_stable, 'cache_control': {'type': 'ephemeral'}},
                   {'type': 'text', 'text': systeme_variable}],
        # Cache automatique de la queue : chaque appel du tour relit le préfixe du précédent.
        'cache_control': {'type': 'ephemeral'},
        'timeout': timeout,
    }
    if outils:
        params['tools'] = outils
        params['tool_choice'] = {'type': 'auto'}      # forcer un outil ⇒ 400 sur les 5.5
    output_config = {}
    if fiche.get('effort'):
        output_config['effort'] = fiche['effort']
    if format_json is not None:
        output_config['format'] = {'type': 'json_schema', 'schema': format_json}
    if output_config:
        params['output_config'] = output_config
    if fiche.get('thinking'):
        params['thinking'] = {'type': fiche['thinking']}
    if fiche.get('fallbacks'):
        params['betas'] = [BETA_FALLBACK]
        params['fallbacks'] = 'default'
    try:
        resp = client.beta.messages.create(**params)
    except Exception as exc:  # noqa: BLE001 — traduit en message fixe
        raise _erreur_anthropic(exc) from exc

    texte, appels = [], []
    for bloc in resp.content or []:
        genre = getattr(bloc, 'type', None)
        if genre == 'text':
            texte.append(getattr(bloc, 'text', '') or '')
        elif genre == 'tool_use':
            entree = getattr(bloc, 'input', None)
            appels.append(Appel(id=bloc.id, nom=bloc.name, entree=entree if isinstance(entree, dict) else None,
                                illisible=not isinstance(entree, dict)))
    arret = {'tool_use': 'outils', 'max_tokens': 'tronque', 'refusal': 'refus',
             'model_context_window_exceeded': 'tronque'}.get(resp.stop_reason, 'fin')
    if arret == 'fin' and appels:
        arret = 'outils'
    u = resp.usage
    usage = {'input_tokens': _jetons(u, 'input_tokens') + _jetons(u, 'cache_creation_input_tokens')
             + _jetons(u, 'cache_read_input_tokens'),
             'output_tokens': _jetons(u, 'output_tokens'),
             'cache_read_tokens': _jetons(u, 'cache_read_input_tokens')}
    return Reponse(texte='\n\n'.join(t for t in texte if t.strip()).strip(), appels=appels, arret=arret,
                   usage=usage, message_assistant={'role': 'assistant', 'content': resp.content})


def resultats_anthropic(resultats) -> dict:
    """TOUS les résultats d'une réponse, dans UN message `user`."""
    return {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': i, 'content': contenu, **({'is_error': True} if erreur else {})}
        for i, _nom, contenu, erreur in resultats]}


# ── Compatible OpenAI (Mistral, Albert) ───────────────────────────────────────

def _url_chat(provider: str) -> str:
    from . import registre as budget
    if provider == 'mistral':
        return MISTRAL_URL          # gardée en constante : les tests la vérifient
    return budget.FOURNISSEURS[provider]['url'] + '/chat/completions'


def appeler_openai(provider: str, cle: str, fiche: dict, systeme_stable: str, systeme_variable: str,
                   messages: list, outils: list | None, max_tokens: int, timeout: float,
                   format_json=False) -> Reponse:
    from . import registre as budget
    nom = budget.FOURNISSEURS[provider]['label']
    payload = {'model': fiche['id'], 'max_tokens': max_tokens,
               'messages': [{'role': 'system', 'content': systeme_stable + '\n\n' + systeme_variable}] + messages}
    if outils:
        payload['tools'] = outils
        payload['tool_choice'] = 'auto'
    if format_json:
        payload['response_format'] = {'type': 'json_object'}
    try:
        resp = http_requests.post(_url_chat(provider), headers={'Authorization': f'Bearer {cle}',
                                                                'Content-Type': 'application/json'},
                                  json=payload, timeout=(10, max(timeout, 5)))
        resp.raise_for_status()
        donnees = resp.json()
        choix = donnees['choices'][0]
        message = choix.get('message') or {}
    except http_requests.exceptions.Timeout as exc:
        raise ErreurFournisseur(504, f'Impossible de joindre {nom} à temps. Réessayez dans quelques instants.') from exc
    except Exception as exc:  # noqa: BLE001
        if budget.est_un_depassement(exc):
            raise ErreurFournisseur(413, '', depassement=True) from exc
        code = getattr(getattr(exc, 'response', None), 'status_code', None)
        if code in (401, 403):
            raise ErreurFournisseur(502, f'Clé {nom} refusée par le fournisseur : vérifiez-la sur {PAGE_IA}.') from exc
        if code == 404:
            raise ErreurFournisseur(502, f'Modèle inconnu chez {nom} : choisissez-en un autre sur {PAGE_IA}.') from exc
        if code == 429:
            raise ErreurFournisseur(502, f'Quota du fournisseur {nom} atteint : réessayez dans quelques instants.') from exc
        raise ErreurFournisseur(502, f'L’appel au fournisseur {nom} a échoué. Vérifiez votre clé ou réessayez.') from exc

    contenu = message.get('content') or ''
    if isinstance(contenu, list):     # contenu en morceaux (certains modèles)
        contenu = ''.join(p.get('text', '') for p in contenu if isinstance(p, dict))
    appels_bruts = message.get('tool_calls') or []
    appels = []
    for a in appels_bruts:
        fonction = a.get('function') or {}
        arguments = fonction.get('arguments')
        try:
            entree = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (TypeError, ValueError):
            entree = None
        appels.append(Appel(id=a.get('id') or '', nom=fonction.get('name') or '',
                            entree=entree if isinstance(entree, dict) else None,
                            illisible=not isinstance(entree, dict)))
    fin = choix.get('finish_reason')
    arret = 'tronque' if fin in ('length', 'model_length') else ('outils' if appels else 'fin')
    u = donnees.get('usage') or {}
    usage = {'input_tokens': _jetons(u, 'prompt_tokens'), 'output_tokens': _jetons(u, 'completion_tokens'),
             'cache_read_tokens': 0}
    assistant = {'role': 'assistant', 'content': contenu}
    if appels_bruts:
        assistant['tool_calls'] = appels_bruts
    return Reponse(texte=contenu.strip(), appels=appels, arret=arret, usage=usage, message_assistant=assistant)


def appeler_mistral(cle: str, fiche: dict, systeme_stable: str, systeme_variable: str, messages: list,
                    outils: list | None, max_tokens: int, timeout: float, format_json=False) -> Reponse:
    return appeler_openai('mistral', cle, fiche, systeme_stable, systeme_variable, messages, outils,
                          max_tokens, timeout, format_json)


def resultats_mistral(resultats) -> list[dict]:
    return [{'role': 'tool', 'tool_call_id': i, 'name': nom, 'content': contenu}
            for i, nom, contenu, _erreur in resultats]
