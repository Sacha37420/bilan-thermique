"""Fiche d'un bâtiment : faits et provenance (Lot 8, refonte). Routes nouvelles uniquement.

GET    /api/fiche/catalogue/                 clés, libellés, unités, plages, blocs
GET    /api/batiments/<id>/fiche/            fiche effective + scores (?scenario=<id>)
GET    /api/batiments/<id>/faits/?cle=…      historique d'une clé (tous les faits, retirés compris)
POST   /api/batiments/<id>/faits/            {key, value, method?, source?} — surcharge de l'utilisateur
DELETE /api/faits/<id>/                      « revenir à l'automatique » (retire la surcharge)
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, facts
from .models import BuildingFact, Scenario


class FactWriteSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=80)
    value = serializers.JSONField(allow_null=True)
    method = serializers.ChoiceField(choices=list(facts.USER_METHODS), default='declare')
    source = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    scenario = serializers.IntegerField(required=False, allow_null=True, default=None)


def fiche_payload(building, viewer_sub, scenario=None):
    eff = facts.effective(building, scenario)
    auto = {}
    # Valeur automatique sous-jacente quand une surcharge prime (affichée « automatique : … »).
    for f in facts.active_facts(building, scenario):
        if f.method in facts.SYSTEM_METHODS:
            cur = auto.get(f.key)
            if cur is None or facts._better(f, cur):
                auto[f.key] = f
    out = {}
    for k, f in eff.items():
        d = facts.fact_dict(f, viewer_sub)
        a = auto.get(k)
        d['automatic'] = ({'value': a.value, 'method': a.method, 'source': a.source}
                          if a is not None and a.pk != f.pk else None)
        out[k] = d
    conf, comp = facts.scores(eff)
    return {'building': building.pk, 'scenario': scenario.pk if scenario else None, 'facts': out,
            'confidence': conf, 'completeness': comp, 'fiche_hash': facts.fiche_hash(building, scenario)}


def _scenario(building, raw):
    if raw in (None, '', 'base'):
        return None
    try:
        return Scenario.objects.get(pk=int(raw), building=building)
    except (ValueError, Scenario.DoesNotExist):
        return None


class CatalogueView(APIView):
    def get(self, request):
        return Response({'keys': facts.catalogue_json(), 'blocks': facts.BLOCKS,
                         'methods': list(facts.METHOD_RANK), 'user_methods': list(facts.USER_METHODS)})


class FicheView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        return Response(fiche_payload(b, access.sub_of(request), _scenario(b, request.query_params.get('scenario'))))


class FactsView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        qs = BuildingFact.objects.filter(building=b)
        key = request.query_params.get('cle')
        if key:
            qs = qs.filter(key=key)
        viewer = access.sub_of(request)
        rows = []
        for f in qs.order_by('-created_at')[:200]:
            d = facts.fact_dict(f, viewer)
            d.update(retired=f.retired_at is not None, superseded=f.superseded_by_id is not None)
            rows.append(d)
        return Response({'results': rows})

    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': "Vous ne pouvez pas modifier la fiche de ce bâtiment."},
                            status=status.HTTP_403_FORBIDDEN)
        ser = FactWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        scenario = None
        if d['scenario'] is not None:
            scenario = _scenario(b, d['scenario'])
            if scenario is None:
                return Response({'detail': 'Variante inconnue.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            f = facts.record(b, d['key'], d['value'], d['method'], source=d['source'] or 'saisie',
                             author_sub=access.sub_of(request), author_name=request.user.username,
                             scenario=scenario)
        except facts.FactError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'fact': facts.fact_dict(f, access.sub_of(request)),
                         **fiche_payload(b, access.sub_of(request), scenario)}, status=status.HTTP_201_CREATED)


class FactDetailView(APIView):
    def delete(self, request, pk):
        f = get_object_or_404(BuildingFact.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_retire(request, f):
            return Response({'detail': "Seul l'auteur de cette valeur (ou un gestionnaire) peut la retirer."},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            facts.retire(f, access.sub_of(request))
        except facts.FactError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(fiche_payload(f.building, access.sub_of(request), f.scenario))
