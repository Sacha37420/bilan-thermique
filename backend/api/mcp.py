"""Serveur MCP de bilan-thermique et jetons personnels `btt_` (refonte, Lot 22 — § B8).

Modèle : App Builder `0ff4e4e` (`api/mcp.py`), même protocole (JSON-RPC 2.0, « streamable HTTP » sans
SSE et sans état). Le Claude Code ou le Mistral Vibe de l'utilisateur pilote la fiche avec SON
abonnement : l'app n'expose que des outils, sans boucle ni appel à une IA.

- Gestion des jetons (`/api/mcp-tokens/`, `/api/mcp/info/`) : serrure Keycloak ordinaire.
- Point MCP (`POST /api/mcp/`) : n'accepte QUE `Authorization: Bearer btt_…` ; un jeton Keycloak y est
  refusé et un `btt_` est refusé partout ailleurs (ce n'est pas un JWT).
- Un jeton agit avec les droits de son titulaire (`api/access.py`), restreints au bâtiment ou au parc
  auquel il est lié ; portée `lire` ⇒ aucun outil qui écrit ou calcule (D31).
- L'IA ne pose jamais un fait : elle propose (`proposer_faits`, rattaché à un document et contrôlé) ;
  `appliquer_propositions` exige la portée `ecrire` et l'accord explicite de l'utilisateur (D32).
- Journal : nom d'outil, `sub`, bâtiment — jamais les arguments ni le jeton.

Écarts à la copie : préfixe `btt_`, identité par `sub`, liaison bâtiment/parc, outils propres à l'app.
"""
import hashlib
import json
import logging
import secrets
from datetime import timedelta
from types import SimpleNamespace

from decouple import config
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import MethodNotAllowed, NotAuthenticated, ParseError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, documents, facts
from . import scenarios as sc_service
from .authentication import McpTokenAuthentication, McpUser
from .models import Building, BuildingDocument, FactProposal, McpToken, Scenario, WeatherDataset

log = logging.getLogger(__name__)

PORTEES = ('lire', 'ecrire')
JOURS_DEFAUT, JOURS_MAX = 30, 90
NOM_SERVEUR = 'bilan-thermique'
VERSIONS_PROTOCOLE = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
TEXTE_MAX = 60_000          # caractères rendus par lire_document


def url_publique() -> str:
    domaine = (config('DOMAIN', default='') or '').strip()
    prefixe = (settings.FORCE_SCRIPT_NAME or '').rstrip('/')
    if domaine and domaine != 'CHANGE_ME':
        return f'https://{domaine}{prefixe}/api/mcp/'
    base = (config('BACKEND_URL', default='') or 'http://localhost:8000').rstrip('/')
    return f'{base}/api/mcp/'


# ── Gestion des jetons (serrure Keycloak) ─────────────────────────────────────

def _jeton_public(j: McpToken) -> dict:
    return {'id': j.pk, 'name': j.name, 'prefix': j.prefix, 'scope': j.scope, 'building_id': j.building_id,
            'portfolio_id': j.portfolio_id, 'created_at': j.created_at, 'expires_at': j.expires_at,
            'last_used_at': j.last_used_at, 'revoked': j.revoked_at is not None}


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class McpTokenListCreateView(APIView):
    """GET /api/mcp-tokens/ — mes jetons ; POST {name, scope, building_id?, portfolio_id?, days?} — 201 + `token` une fois."""

    def get(self, request):
        return Response([_jeton_public(j) for j in
                         McpToken.objects.filter(owner_sub=access.sub_of(request)).order_by('-created_at')])

    def post(self, request):
        sub = access.sub_of(request)
        if not sub:
            return Response({'error': 'Jeton sans identifiant (sub).'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data if isinstance(request.data, dict) else {}
        nom = d.get('name')
        if not isinstance(nom, str) or not nom.strip() or len(nom.strip()) > 80:
            return Response({'error': 'name : 1 à 80 caractères.'}, status=status.HTTP_400_BAD_REQUEST)
        portee = d.get('scope', 'lire')
        if portee not in PORTEES:
            return Response({'error': 'scope : « lire » ou « ecrire ».'}, status=status.HTTP_400_BAD_REQUEST)
        jours = d.get('days', JOURS_DEFAUT)
        if _int(jours) is None or not 1 <= jours <= JOURS_MAX:
            return Response({'error': f'days : entier de 1 à {JOURS_MAX}.'}, status=status.HTTP_400_BAD_REQUEST)
        building = portfolio = None
        if d.get('building_id') is not None and d.get('portfolio_id') is not None:
            return Response({'error': 'Un bâtiment ou un parc, pas les deux.'}, status=status.HTTP_400_BAD_REQUEST)
        if d.get('building_id') is not None:
            building = access.visible_buildings(request).filter(pk=_int(d['building_id']) or -1).first()
            if building is None:
                return Response({'error': 'building_id : bâtiment introuvable.'}, status=status.HTTP_400_BAD_REQUEST)
        if d.get('portfolio_id') is not None:
            portfolio = access.visible_portfolios(request).filter(pk=_int(d['portfolio_id']) or -1).first()
            if portfolio is None:
                return Response({'error': 'portfolio_id : parc introuvable.'}, status=status.HTTP_400_BAD_REQUEST)
        valeur = 'btt_' + secrets.token_urlsafe(32)
        jeton = McpToken.objects.create(
            owner_sub=sub, owner_name=access.name_of(request), name=nom.strip(), prefix=valeur[:12],
            token_hash=hashlib.sha256(valeur.encode()).hexdigest(), scope=portee, building=building,
            portfolio=portfolio, expires_at=timezone.now() + timedelta(days=jours))
        log.info('MCP : jeton %s créé par %s (portée %s).', jeton.prefix, sub, portee)
        return Response({**_jeton_public(jeton), 'token': valeur}, status=status.HTTP_201_CREATED)


class McpTokenDetailView(APIView):
    """DELETE /api/mcp-tokens/<id>/ — révoque (204) ; jeton d'autrui ou inconnu : 404."""

    def delete(self, request, pk):
        jeton = McpToken.objects.filter(pk=pk, owner_sub=access.sub_of(request)).first()
        if jeton is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if jeton.revoked_at is None:
            jeton.revoked_at = timezone.now()
            jeton.save(update_fields=['revoked_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class McpInfoView(APIView):
    """GET /api/mcp/info/ — l'adresse et les commandes à copier (avec `<JETON>` littéral, jamais un vrai)."""

    def get(self, request):
        url = url_publique()
        return Response({
            'url': url,
            'claude': (f'claude mcp add --scope user --transport http {NOM_SERVEUR} {url} '
                       '--header "Authorization: Bearer <JETON>"'),
            'vibe': ("export BILAN_THERMIQUE_MCP_TOKEN='<JETON>' && vibe mcp add "
                     f"{NOM_SERVEUR} --url {url} --transport streamable-http --api-key-env BILAN_THERMIQUE_MCP_TOKEN "
                     "--api-key-header Authorization --api-key-format 'Bearer {token}'"),
        })


# ── Outils ────────────────────────────────────────────────────────────────────

INSTRUCTIONS = """Tu aides à renseigner et à analyser la fiche thermique d'un bâtiment (application bilan-thermique).
Règles :
- Tu ne calcules jamais la physique toi-même : lance_calcul (N0 mensuel, N1 horaire) le fait, et lire_resultats rend
  ce qui est calculé (avec fourchette et confiance).
- Chaque valeur de la fiche a une provenance (mesure > document > déclaré > inféré > défaut). Tu ne poses jamais
  une valeur : tu PROPOSES des valeurs tirées d'un document (proposer_faits, avec la page et l'extrait exact du
  texte, recopié mot pour mot). Chaque proposition est contrôlée par l'application (plage, cohérence, extrait
  retrouvé).
- appliquer_propositions accepte des propositions dans la fiche : ne l'appelle qu'après avoir montré chaque
  proposition à l'utilisateur et obtenu son accord explicite.
- Clés de la fiche : lire_catalogue. Bâtiments accessibles : lister_batiments.
Une erreur d'outil est un objet JSON {error, detail}."""

_B = {'batiment_id': {'type': 'integer', 'description': 'Identifiant du bâtiment (lister_batiments).'}}
_SC = {'scenario_id': {'type': 'integer', 'description': 'Variante (facultatif ; absent = état actuel).'}}

# nom : (description, propriétés, requis, écrit_ou_calcule)
OUTILS = {
    'lister_batiments': ('Bâtiments accessibles par ce jeton (id, nom, parc, confiance, complétude).', {}, [], False),
    'lire_catalogue': ('Clés de la fiche : libellé, unité, type, valeurs permises, plage.', {}, [], False),
    'lire_fiche': ('Fiche effective : chaque valeur avec sa provenance et sa confiance.', {**_B, **_SC}, ['batiment_id'], False),
    'lire_resultats': ('Derniers résultats N0, N1, N2 (indicateurs, fourchette P10–P90, à jour ou périmé).',
                       {**_B, **_SC}, ['batiment_id'], False),
    'lister_documents': ('Documents du bâtiment que ce jeton peut lire (pages lisibles).', _B, ['batiment_id'], False),
    'lire_document': ('Texte lu d’un document, page par page (pages : liste de numéros, facultatif).',
                      {'document_id': {'type': 'integer'}, 'pages': {'type': 'array', 'items': {'type': 'integer'}}},
                      ['document_id'], False),
    'lister_propositions': ('Propositions de valeurs (en attente, acceptées, rejetées) avec leur contrôle.',
                            _B, ['batiment_id'], False),
    'proposer_faits': ('Propose des valeurs tirées d’un document ; rien n’entre dans la fiche sans validation.',
                       {'document_id': {'type': 'integer'},
                        'faits': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': {
                            'type': 'object', 'properties': {'cle': {'type': 'string'}, 'valeur': {},
                                                             'page': {'type': 'integer'}, 'extrait': {'type': 'string'}},
                            'required': ['cle', 'valeur', 'extrait']}}},
                       ['document_id', 'faits'], True),
    'lancer_calcul': ('Calcule N0 et/ou N1 (quelques secondes) et rend les indicateurs.',
                      {**_B, **_SC, 'niveaux': {'type': 'array', 'items': {'type': 'string', 'enum': ['N0', 'N1']}}},
                      ['batiment_id'], True),
    'comparer_scenarios': ('Compare l’état actuel et des variantes (N1 avec fourchette) sous une ou plusieurs météos.',
                           {**_B, 'scenarios': {'type': 'array', 'maxItems': 5,
                                                'items': {'type': ['integer', 'null']}},
                            'meteos': {'type': 'array', 'maxItems': 3, 'items': {'type': 'integer'}}},
                           ['batiment_id'], True),
    'appliquer_propositions': ('Accepte (ou rejette) des propositions après accord explicite de l’utilisateur.',
                               {'ids': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': {'type': 'integer'}},
                                'decision': {'type': 'string', 'enum': ['acceptee', 'rejetee']}},
                               ['ids'], True),
}


class ErreurOutil(Exception):
    def __init__(self, error: str, detail, **extra):
        super().__init__(error)
        self.charge = {'error': error, 'detail': detail, **extra}


def _req(user: McpUser):
    """Objet « requête » minimal pour `api/access.py` (qui ne lit que `request.user.sub`)."""
    return SimpleNamespace(user=user)


def _outils_visibles(user: McpUser) -> list[str]:
    return [n for n, (_d, _p, _r, ecrit) in OUTILS.items() if not ecrit or user.jeton.scope == 'ecrire']


def _liste_outils(user: McpUser) -> list[dict]:
    return [{'name': n, 'description': OUTILS[n][0],
             'inputSchema': {'type': 'object', 'properties': OUTILS[n][1], 'required': OUTILS[n][2]}}
            for n in _outils_visibles(user)]


def _batiments(user: McpUser):
    qs = access.visible_buildings(_req(user))
    j = user.jeton
    if j.building_id is not None:
        qs = qs.filter(pk=j.building_id)
    elif j.portfolio_id is not None:
        qs = qs.filter(portfolio_id=j.portfolio_id)
    return qs


def _batiment(user: McpUser, args: dict, ecrire=False) -> Building:
    b = _batiments(user).filter(pk=_int(args.get('batiment_id')) or -1).first()
    if b is None:
        raise ErreurOutil('introuvable', 'Bâtiment introuvable (ou hors de la portée de ce jeton).')
    if ecrire and not access.can_declare(_req(user), b):
        raise ErreurOutil('lecture_seule', 'Lecture seule sur ce bâtiment.')
    return b


def _scenario(b, args):
    sid = args.get('scenario_id')
    if sid is None:
        return None
    sc = Scenario.objects.filter(pk=_int(sid) or -1, building=b).first()
    if sc is None:
        raise ErreurOutil('introuvable', 'Variante inconnue.')
    return sc


def _document(user: McpUser, args: dict) -> BuildingDocument:
    d = BuildingDocument.objects.filter(pk=_int(args.get('document_id')) or -1,
                                        building__in=_batiments(user)).select_related('building').first()
    if d is None or not access.can_see_personal(_req(user), d.building, d.owner_sub):
        raise ErreurOutil('introuvable', 'Document introuvable (ou hors de la portée de ce jeton).')
    return d


def _resume_run(run):
    from .views_calc import compact
    d = compact(run)
    return {k: d.get(k) for k in ('id', 'level', 'engine_version', 'created_at', 'indicators', 'uncertainty', 'results')
            if k in d}


def _executer(nom: str, user: McpUser, args: dict):
    from .views_fiche import fiche_payload
    from .views_ia import _prop, decide

    if nom == 'lister_batiments':
        return [{'id': b.pk, 'name': b.name, 'portfolio_id': b.portfolio_id,
                 'confidence': b.confidence, 'completeness': b.completeness}
                for b in _batiments(user).order_by('name')[:500]]
    if nom == 'lire_catalogue':
        return facts.catalogue_json()
    if nom == 'appliquer_propositions':
        ids = args.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) > 40 or any(_int(i) is None for i in ids):
            raise ErreurOutil('arguments', 'ids : liste de 1 à 40 identifiants.')
        decision = args.get('decision', 'acceptee')
        out, refus = [], []
        for p in FactProposal.objects.filter(pk__in=ids, building__in=_batiments(user)).select_related('run__document',
                                                                                                         'building'):
            req = _req(user)
            if not access.can_see_personal(req, p.building, p.run.owner_sub) or not access.can_declare(req, p.building):
                continue
            try:
                decide(p, decision, user.sub, user.username, via=f' — via MCP ({user.jeton.prefix}…)')
                out.append(_prop(p))
            except documents.DocumentError as exc:
                refus.append({'id': p.pk, 'detail': str(exc)})
        connus = {r['id'] for r in out} | {r['id'] for r in refus}
        refus += [{'id': i, 'detail': 'Proposition introuvable.'} for i in ids if i not in connus]
        return {'appliquees': out, 'refus': refus}
    if nom in ('lire_document', 'proposer_faits'):
        d = _document(user, args)
        if nom == 'lire_document':
            pages = args.get('pages')
            if pages is not None and (not isinstance(pages, list) or any(_int(i) is None for i in pages)):
                raise ErreurOutil('arguments', 'pages : liste de numéros.')
            idx = [i for i in (pages or range(1, len(d.pages) + 1)) if 1 <= i <= len(d.pages)]
            out, total = [], 0
            for i in idx:
                t = d.pages[i - 1][:max(0, TEXTE_MAX - total)]
                total += len(t)
                out.append({'page': i, 'texte': t})
            return {'document_id': d.pk, 'filename': d.filename, 'n_pages': len(d.pages), 'pages': out,
                    'tronque': total >= TEXTE_MAX}
        if not access.can_declare(_req(user), d.building):
            raise ErreurOutil('lecture_seule', 'Lecture seule sur ce bâtiment.')
        rows = []
        faits = args.get('faits')
        if not isinstance(faits, list) or not faits or len(faits) > 40:
            raise ErreurOutil('arguments', 'faits : liste de 1 à 40 objets {cle, valeur, page, extrait}.')
        for f in faits:
            if not isinstance(f, dict) or not isinstance(f.get('cle'), str):
                raise ErreurOutil('arguments', 'Chaque fait : {cle, valeur, page, extrait}.')
            rows.append((f['cle'], f.get('valeur'), _int(f.get('page')), str(f.get('extrait') or '')[:500]))
        from .models import AIExtractionRun
        run = AIExtractionRun.objects.create(document=d, owner_sub=user.sub, provider='mcp', status='DONE',
                                             message=f'propositions par MCP ({user.jeton.prefix}…)')
        return {'propositions': [_prop(p) for p in documents.make_proposals(run, d.building, rows, d.pages)],
                'rappel': 'Montrez ces propositions à l’utilisateur ; rien n’est dans la fiche avant son accord.'}

    b = _batiment(user, args, ecrire=OUTILS[nom][3])
    if nom == 'lire_fiche':
        return fiche_payload(b, user.sub, _scenario(b, args))
    if nom == 'lire_resultats':
        sc = _scenario(b, args)
        out = {}
        for lvl in ('N0', 'N1', 'N2'):
            run = calc_service.current_or_latest(b, lvl, sc)
            out[lvl] = None if run is None else {**_resume_run(run),
                                                 'up_to_date': calc_service.is_up_to_date(run)}
        return out
    if nom == 'lister_documents':
        return [{'id': d.pk, 'filename': d.filename, 'kind': d.kind, 'n_pages': len(d.pages),
                 'pages_with_text': [i + 1 for i, t in enumerate(d.pages) if t.strip()]}
                for d in b.documents.all() if access.can_see_personal(_req(user), b, d.owner_sub)]
    if nom == 'lister_propositions':
        return [_prop(p) for p in b.proposals.select_related('run__document').order_by('-run__created_at', 'key')
                if access.can_see_personal(_req(user), b, p.run.owner_sub)][:300]
    if nom == 'lancer_calcul':
        niveaux = args.get('niveaux') or ['N0', 'N1']
        if not isinstance(niveaux, list) or not niveaux or any(n not in ('N0', 'N1') for n in niveaux):
            raise ErreurOutil('arguments', 'niveaux : « N0 » et/ou « N1 ».')
        sc = _scenario(b, args)
        runs = []
        try:
            for lvl in dict.fromkeys(niveaux):
                run, fresh = calc_service.run_level(b, lvl, scenario=sc, owner_sub=user.sub)
                runs.append({**_resume_run(run), 'fresh': fresh})
        except calc_service.Busy as exc:
            raise ErreurOutil('occupe', str(exc))
        except calc_service.CalcError as exc:
            raise ErreurOutil('calcul', str(exc))
        return {'runs': runs}
    if nom == 'comparer_scenarios':
        ids = args.get('scenarios') or [None]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5:
            raise ErreurOutil('arguments', 'De une à cinq variantes (null = état actuel).')
        scs = []
        for i in ids:
            scs.append(None if i is None else _scenario(b, {'scenario_id': i}))
        meteos = args.get('meteos') or []
        if not isinstance(meteos, list) or len(meteos) > 3 or any(_int(m) is None for m in meteos) \
                or WeatherDataset.objects.filter(pk__in=meteos).count() != len(set(meteos)):
            raise ErreurOutil('arguments', 'meteos : trois jeux météo connus au plus.')
        rows = sc_service.compare(b, scs, meteos, user.sub)
        vig = [v for s in scs if s is not None for v in sc_service.vigilance(s)]
        return {'rows': rows, 'vigilance': vig}
    raise ErreurOutil('outil_inconnu', nom)


# ── Point JSON-RPC ────────────────────────────────────────────────────────────

def _rpc_erreur(ident, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}}


class McpView(APIView):
    """POST /api/mcp/ — JSON-RPC 2.0 sans état. Toute autre méthode : 405."""

    authentication_classes = [McpTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        if request.method != 'POST':
            raise MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)
        if not isinstance(request.user, McpUser):
            raise NotAuthenticated()

    def post(self, request):
        try:
            message = request.data
        except ParseError:
            return Response(_rpc_erreur(None, -32700, 'Parse error'))
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return Response(_rpc_erreur(message.get('id') if isinstance(message, dict) else None, -32600, 'Invalid Request'))
        methode, ident = message['method'], message.get('id')
        params = message.get('params') if isinstance(message.get('params'), dict) else {}
        if 'id' not in message:
            return Response(status=status.HTTP_202_ACCEPTED)
        user = request.user
        if methode == 'initialize':
            demandee = params.get('protocolVersion')
            version = demandee if demandee in VERSIONS_PROTOCOLE else VERSIONS_PROTOCOLE[1]
            return Response({'jsonrpc': '2.0', 'id': ident, 'result': {
                'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': NOM_SERVEUR, 'version': '1'}, 'instructions': INSTRUCTIONS}})
        if methode == 'ping':
            return Response({'jsonrpc': '2.0', 'id': ident, 'result': {}})
        if methode == 'tools/list':
            return Response({'jsonrpc': '2.0', 'id': ident, 'result': {'tools': _liste_outils(user)}})
        if methode != 'tools/call':
            return Response(_rpc_erreur(ident, -32601, 'Method not found'))
        nom = params.get('name')
        args = params.get('arguments') if isinstance(params.get('arguments'), dict) else {}
        if nom not in OUTILS:
            return Response(_rpc_erreur(ident, -32602, 'Outil inconnu.'))
        log.info('MCP : outil %s par %s (bâtiment %s).', nom, user.sub,
                 args.get('batiment_id') if _int(args.get('batiment_id')) else '-')
        if nom not in _outils_visibles(user):
            charge, erreur = {'error': 'portee', 'detail': 'Ce jeton (portée « lire ») ne permet pas cet outil.'}, True
        else:
            try:
                with transaction.atomic():
                    charge, erreur = _executer(nom, user, args), False
            except ErreurOutil as exc:
                charge, erreur = exc.charge, True
        return Response({'jsonrpc': '2.0', 'id': ident, 'result': {
            'content': [{'type': 'text', 'text': json.dumps(charge, ensure_ascii=False, default=str)}], 'isError': erreur}})
