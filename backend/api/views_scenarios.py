"""Scénarios et comparateur (Lot 18, refonte). Routes nouvelles uniquement.

GET/POST /api/batiments/<id>/scenarios/     {name, description?, bundles?: [clé…], dataset_id?}
GET      /api/scenarios/bouquets/
DELETE   /api/scenarios/<id>/
POST     /api/scenarios/<id>/bouquets/       {bundles: [clé…]}
POST     /api/batiments/<id>/comparer/       {scenarios: [null | id…], meteos?: [id…]} → N1 de chaque variante × météo
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, scenarios as sc_service
from .models import Scenario, WeatherDataset


def _scenario(s, building):
    return {'id': s.pk, 'name': s.name, 'description': s.description, 'bundles': (s.weather or {}).get('bundles', []),
            'dataset_id': (s.weather or {}).get('dataset_id'), 'created_at': s.created_at,
            'differences': sc_service.differences(building, s), 'vigilance': sc_service.vigilance(s),
            # Chantier bt-potentiels : amélioration générée par le calcul d'un potentiel (recréée à chaque calcul).
            'genere': s.genere, 'bouquet': {'id': s.bouquet_id, 'nom': s.bouquet.nom} if s.bouquet_id else None}


def _building(request, pk, write=False):
    b = get_object_or_404(access.visible_buildings(request), pk=pk)
    if write and not access.can_declare(request, b):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Lecture seule sur ce bâtiment.')
    return b


class BundlesView(APIView):
    """`?parc=<id>` (chantier bt-navigation) : libellés et épaisseurs effectifs du parc, bouquets
    désactivés masqués. Sans `parc` : inchangé."""

    def get(self, request):
        pid = request.query_params.get('parc')
        if pid in (None, ''):
            return Response({'bundles': [{'key': k, 'label': v['label'], 'vigilance': v['vigilance']}
                                         for k, v in sc_service.BUNDLES.items()]})
        from . import reglages
        p = get_object_or_404(access.visible_portfolios(request), pk=int(pid) if str(pid).isdigit() else -1)
        cfg = reglages.effective(p, 'parametrage')
        out = []
        for k, v in sc_service.BUNDLES.items():
            if not reglages.bundle_active(cfg, k):
                continue
            row = {'key': k, 'label': reglages.bundle_label(k, v, cfg), 'vigilance': v['vigilance']}
            if k in reglages.BUNDLE_THICKNESS:
                row['epaisseur_cm'] = cfg.get(f'bouquets.{k}.epaisseur_cm')
            out.append(row)
        return Response({'bundles': out, 'parc': p.pk})


class ScenariosView(APIView):
    def get(self, request, pk):
        b = _building(request, pk)
        return Response({'results': [_scenario(s, b) for s in b.scenarios.select_related('bouquet')]})

    def post(self, request, pk):
        b = _building(request, pk, write=True)
        name = str(request.data.get('name') or '').strip()[:150]
        if not name:
            return Response({'detail': 'Nom de la variante attendu.'}, status=status.HTTP_400_BAD_REQUEST)
        if b.scenarios.filter(genere=False).count() >= 20:
            return Response({'detail': 'Vingt variantes au plus par bâtiment.'}, status=status.HTTP_400_BAD_REQUEST)
        weather = {}
        did = request.data.get('dataset_id')
        if did:
            weather['dataset_id'] = get_object_or_404(WeatherDataset.objects.defer('series'), pk=did).pk
        s = Scenario.objects.create(building=b, name=name, description=str(request.data.get('description') or '')[:2000],
                                    weather=weather, author_sub=access.sub_of(request))
        try:
            sc_service.apply_bundles(b, s, request.data.get('bundles') or [], access.sub_of(request), access.name_of(request))
        except sc_service.ScenarioError as exc:
            s.delete()
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_scenario(s, b), status=status.HTTP_201_CREATED)


class ScenarioDetailView(APIView):
    def _get(self, request, pk):
        s = get_object_or_404(Scenario.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_declare(request, s.building):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Lecture seule sur ce bâtiment.')
        return s

    def delete(self, request, pk):
        self._get(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScenarioBundlesView(ScenarioDetailView):
    def post(self, request, pk):
        s = self._get(request, pk)
        try:
            sc_service.apply_bundles(s.building, s, request.data.get('bundles') or [], access.sub_of(request), access.name_of(request))
        except sc_service.ScenarioError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_scenario(s, s.building))


class CompareView(APIView):
    def post(self, request, pk):
        b = _building(request, pk, write=True)
        ids = request.data.get('scenarios') or [None]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5:
            return Response({'detail': 'De une à cinq variantes.'}, status=status.HTTP_400_BAD_REQUEST)
        scs = []
        for i in ids:
            scs.append(None if i in (None, 'base') else get_object_or_404(Scenario, pk=i, building=b))
        meteos = request.data.get('meteos') or []
        if not isinstance(meteos, list) or len(meteos) > 3:
            return Response({'detail': 'Trois météos au plus.'}, status=status.HTTP_400_BAD_REQUEST)
        for m in meteos:
            get_object_or_404(WeatherDataset.objects.defer('series'), pk=m)
        rows = sc_service.compare(b, scs, meteos, access.sub_of(request))
        vig = []
        for s in scs:
            if s is not None:
                vig += sc_service.vigilance(s)
        return Response({'rows': rows, 'vigilance': vig})
