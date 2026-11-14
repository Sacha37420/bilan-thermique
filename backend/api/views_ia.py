"""Intelligence artificielle sur le compte de l'utilisateur (Lot 21, refonte — § B8).

GET/PUT /api/ai-keys/        « configurée : oui/non » seulement ; chaîne vide = suppression ; sk-ant-oat… refusé
GET/PUT /api/ai-settings/    fournisseurs, modèles, préférences (sur le serveur, jamais dans le navigateur)
GET/POST /api/batiments/<id>/documents/        dépôt (multipart `file`), lecture locale ; XML DPE → propositions sans IA
DELETE /api/documents/<id>/
POST /api/documents/<id>/estimation/           {provider, model?, pages?} → pages et jetons estimés, avant envoi
POST /api/documents/<id>/extraire/             {provider, model?, pages?, confirm: true} → job (clé de l'appelant)
GET  /api/batiments/<id>/propositions/
POST /api/propositions/<id>/decision/          {decision: acceptee|rejetee}
Formes de réponse des clés et réglages : celles d'App Builder (`0ff4e4e`), identité par le `sub`.
"""

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, documents, facts, storage_client, tasks
from .ia import catalogue as catalogue_ia
from .ia import registre
from .models import AIExtractionRun, AiKeyVault, BuildingDocument, FactProposal, Job
from .serializers import JobSerializer

_VAULT_FIELDS = {f['champ']: p for p, f in registre.FOURNISSEURS.items()}
_KEY_MAX_LEN = 512


def vault_open():
    return bool(settings.VAULT_ENCRYPTION_KEY)


def key_of(sub, provider):
    v = AiKeyVault.objects.filter(owner_sub=sub).first() if sub else None
    f = registre.FOURNISSEURS.get(provider)
    return (getattr(v, f['champ'], '') if (v and f) else '') or ''


class AiKeysView(APIView):
    def get(self, request):
        v = AiKeyVault.objects.filter(owner_sub=access.sub_of(request)).first() if vault_open() else None
        return Response({**{f'{c}_configured': bool(v and getattr(v, c)) for c in _VAULT_FIELDS}, 'vault': vault_open()})

    def put(self, request):
        sub = access.sub_of(request)
        if not vault_open():
            return Response({'error': 'Coffre non configuré sur ce serveur (VAULT_ENCRYPTION_KEY).', 'code': 'vault_closed'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not sub:
            return Response({'error': 'Jeton sans identifiant (sub).'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data if isinstance(request.data, dict) else None
        if not d or set(d) - set(_VAULT_FIELDS):
            return Response({'error': 'Corps attendu : {"claude_key": "…"}, {"mistral_key": "…"} et/ou {"albert_key": "…"}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        values = {}
        for champ, val in d.items():
            if not isinstance(val, str):
                return Response({'error': f'{champ} : une chaîne est attendue.'}, status=status.HTTP_400_BAD_REQUEST)
            val = val.strip()
            if len(val) > _KEY_MAX_LEN or any(c.isspace() or not c.isprintable() for c in val):
                return Response({'error': f'{champ} : clé mal formée.'}, status=status.HTTP_400_BAD_REQUEST)
            if val.startswith('sk-ant-oat'):
                return Response({'error': "Jeton d'abonnement Claude : utilisez-le dans Claude Code, relié au serveur MCP "
                                          "de bilan-thermique, pas ici. Ce coffre attend une clé d'API (sk-ant-api…).",
                                 'code': 'oauth_token_refused'}, status=status.HTTP_400_BAD_REQUEST)
            values[champ] = val
        v, _ = AiKeyVault.objects.get_or_create(owner_sub=sub)
        for champ, val in values.items():
            setattr(v, champ, val)
        v.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AiSettingsView(APIView):
    def _models(self, sub):
        out = {}
        for p, f in registre.FOURNISSEURS.items():
            k = key_of(sub, p) if (f.get('catalogue') == 'dynamique' and vault_open()) else ''
            out[p] = catalogue_ia.modeles(p, k) if k else registre.modeles_fixes(p)
        return out

    def _state(self, sub, models=None):
        models = models or self._models(sub)
        v = AiKeyVault.objects.filter(owner_sub=sub).first() if (sub and vault_open()) else None
        conf = {p: bool(v and getattr(v, f['champ'])) for p, f in registre.FOURNISSEURS.items()}
        chosen = v.preferred_models if v and isinstance(v.preferred_models, dict) else {}
        per = {}
        for p in registre.FOURNISSEURS:
            ids = {m['id'] for m in models[p]}
            m = chosen.get(p)
            per[p] = m if isinstance(m, str) and m in ids else registre.MODELE_PAR_DEFAUT[p]
        pref = v.preferred_provider if v else ''
        if not registre.fournisseur_existe(pref):
            pref = next((p for p, ok in conf.items() if ok), 'claude')
        return {'providers': [{'id': p, 'label': f['label'], 'help': f['aide'], 'configured': conf[p],
                               'default_model': registre.MODELE_PAR_DEFAUT[p], 'models': models[p]}
                              for p, f in registre.FOURNISSEURS.items()],
                'preferences': {'provider': pref, 'models': per}, 'vault': vault_open()}

    def get(self, request):
        return Response(self._state(access.sub_of(request)))

    def put(self, request):
        sub = access.sub_of(request)
        d = request.data if isinstance(request.data, dict) else None
        if not d or set(d) - {'provider', 'models'}:
            return Response({'error': 'Corps attendu : {"provider": "…"} et/ou {"models": {…}}.'}, status=status.HTTP_400_BAD_REQUEST)
        if not vault_open() or not sub:
            return Response({'error': 'Coffre non configuré sur ce serveur.', 'code': 'vault_closed'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        models = self._models(sub)
        if 'provider' in d and not registre.fournisseur_existe(d['provider']):
            return Response({'error': 'Fournisseur inconnu.'}, status=status.HTTP_400_BAD_REQUEST)
        choice = d.get('models', {})
        if not isinstance(choice, dict):
            return Response({'error': 'models : objet attendu.'}, status=status.HTTP_400_BAD_REQUEST)
        for p, m in choice.items():
            if not registre.fournisseur_existe(p) or not isinstance(m, str) or m not in {x['id'] for x in models[p]}:
                return Response({'error': 'Modèle non proposé pour ce fournisseur.'}, status=status.HTTP_400_BAD_REQUEST)
        v, _ = AiKeyVault.objects.get_or_create(owner_sub=sub)
        if 'provider' in d:
            v.preferred_provider = d['provider']
        if choice:
            v.preferred_models = {**(v.preferred_models or {}), **choice}
        v.save()
        return Response(self._state(sub, models))


def _doc(d):
    return {'id': d.pk, 'kind': d.kind, 'filename': d.filename, 'size_bytes': d.size_bytes, 'n_pages': len(d.pages),
            'pages_with_text': [i + 1 for i, t in enumerate(d.pages) if t.strip()], 'stored': bool(d.storage_path),
            'created_at': d.created_at}


def _prop(p):
    spec = facts.BY_KEY.get(p.key)
    return {'id': p.pk, 'key': p.key, 'label': spec.label if spec else p.key, 'value': p.value, 'unit': p.unit, 'page': p.page,
            'extract': p.extract, 'check': p.control, 'check_message': p.control_message, 'decision': p.decision,
            'document': p.run.document.filename, 'provider': p.run.provider, 'run_id': p.run_id}


def _my_doc(request, pk):
    d = get_object_or_404(BuildingDocument.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
    if not access.can_see_personal(request, d.building, d.owner_sub):
        from django.http import Http404
        raise Http404
    return d


class DocumentsView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        docs = [d for d in b.documents.all() if access.can_see_personal(request, b, d.owner_sub)]
        return Response({'results': [_doc(d) for d in docs], 'storage': storage_client.enabled()})

    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        f = request.FILES.get('file')
        if f is None:
            return Response({'detail': 'Fichier attendu (champ « file »).'}, status=status.HTTP_400_BAD_REQUEST)
        if f.size > documents.MAX_BYTES:
            return Response({'detail': 'Fichier trop volumineux (15 Mo au plus).'}, status=status.HTTP_400_BAD_REQUEST)
        data = f.read()
        try:
            kind, pages, local = documents.read_upload(f.name, data)
        except documents.DocumentError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        path, note = '', None
        if storage_client.enabled():
            try:
                path = storage_client.upload_personal(request.auth, request.user.username, b.pk, f.name, data,
                                                      getattr(f, 'content_type', '') or 'application/octet-stream')
            except storage_client.StorageError as exc:
                note = f'Original non conservé ({exc}).'
        else:
            note = 'Original non conservé : storage non relié (seul le texte lu est gardé).'
        d = BuildingDocument.objects.create(building=b, owner_sub=access.sub_of(request), kind=kind, filename=f.name[:200],
                                            size_bytes=len(data), storage_path=path, pages=pages)
        out = {**_doc(d), 'note': note, 'proposals': []}
        if local:
            run = AIExtractionRun.objects.create(document=d, owner_sub=d.owner_sub, provider='local', status='DONE',
                                                 message='XML DPE lu sans IA')
            rows = [(k, v, 1, ex) for k, v, ex in local]
            out['proposals'] = [_prop(p) for p in documents.make_proposals(run, b, rows, [d.pages[0] + '\n' + '\n'.join(ex for *_, ex in local)])]
        return Response(out, status=status.HTTP_201_CREATED)


class DocumentDetailView(APIView):
    def delete(self, request, pk):
        _my_doc(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _params(request, d):
    provider = request.data.get('provider')
    if not registre.fournisseur_existe(provider):
        raise documents.DocumentError('Fournisseur inconnu.')
    model = request.data.get('model') or registre.MODELE_PAR_DEFAUT[provider]
    pages = request.data.get('pages')
    if pages is not None and (not isinstance(pages, list) or not all(isinstance(i, int) for i in pages)):
        raise documents.DocumentError('Pages : liste de numéros.')
    idx = documents.pages_to_send(d.pages, pages)
    if not idx:
        raise documents.DocumentError('Aucune page avec du texte à envoyer (document scanné : lecture par image non prise en charge).')
    return provider, model, idx


class EstimateView(APIView):
    def post(self, request, pk):
        d = _my_doc(request, pk)
        try:
            provider, model, idx = _params(request, d)
        except documents.DocumentError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        fiche = registre.modele(provider, model)
        est = documents.estimate(d.pages, idx)
        return Response({'provider': provider, 'provider_label': registre.FOURNISSEURS[provider]['label'], 'model': model,
                         'pages': idx, 'tokens_estimated': est, 'fits': est < fiche['context_window'],
                         'key_configured': bool(vault_open() and key_of(access.sub_of(request), provider)),
                         'warning': 'Le texte de ces pages sera envoyé au fournisseur, avec votre clé et sur votre compte.'})


class ExtractView(APIView):
    def post(self, request, pk):
        d = _my_doc(request, pk)
        sub = access.sub_of(request)
        if d.owner_sub != sub:
            return Response({'detail': 'Seul l’auteur du document peut l’envoyer à son IA.'}, status=status.HTTP_403_FORBIDDEN)
        if request.data.get('confirm') is not True:
            return Response({'detail': 'Confirmation attendue (voir l’estimation avant envoi).', 'code': 'confirm'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            provider, model, idx = _params(request, d)
        except documents.DocumentError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not (vault_open() and key_of(sub, provider)):
            return Response({'detail': 'Aucune clé configurée pour ce fournisseur (page « Intelligence artificielle »).',
                             'code': 'no_key'}, status=status.HTTP_400_BAD_REQUEST)
        run = AIExtractionRun.objects.create(document=d, owner_sub=sub, provider=provider, model=model, pages_sent=idx,
                                             tokens_estimated=documents.estimate(d.pages, idx))
        job = Job.objects.create(kind='extract_document', params={'document_id': d.pk, 'run_id': run.pk}, owner_sub=sub)
        tasks.extract_document.delay(job.id, run.pk)
        return Response({**JobSerializer(job).data, 'run_id': run.pk}, status=status.HTTP_202_ACCEPTED)


class ProposalsView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        qs = b.proposals.select_related('run__document').order_by('-run__created_at', 'key')
        rows = [_prop(p) for p in qs if access.can_see_personal(request, b, p.run.owner_sub)]
        return Response({'results': rows[:300]})


class DecisionView(APIView):
    def post(self, request, pk):
        p = get_object_or_404(FactProposal.objects.select_related('run__document', 'building')
                              .filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_see_personal(request, p.building, p.run.owner_sub) or not access.can_declare(request, p.building):
            return Response(status=status.HTTP_404_NOT_FOUND)
        decision = request.data.get('decision')
        try:
            decide(p, decision, access.sub_of(request), access.name_of(request))
        except documents.DocumentError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_prop(p))


def decide(p, decision, sub, name, via=''):
    """Décision sur une proposition (interface ou MCP). Acceptée ⇒ fait `document` avec extrait et page."""
    if decision not in ('acceptee', 'rejetee') or p.decision != 'en_attente':
        raise documents.DocumentError('Décision attendue (acceptee ou rejetee) sur une proposition en attente.')
    if decision == 'acceptee':
        if p.control not in documents.ACCEPTABLE:
            raise documents.DocumentError('Proposition écartée par le contrôle automatique.')
        src = f'document « {p.run.document.filename} »' + (f' p. {p.page}' if p.page else '') + via
        p.fact = facts.record(p.building, p.key, p.value, 'document', source=src, author_sub=sub, author_name=name,
                              extract={'document_id': p.run.document_id, 'page': p.page, 'texte': p.extract[:300]})
    p.decision, p.decided_by, p.decided_at = decision, sub, timezone.now()
    p.save(update_fields=['decision', 'decided_by', 'decided_at', 'fact'])
    return p
