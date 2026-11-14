"""Historique des calculs (Lot 3, refonte) — routes nouvelles, aucune route existante modifiée.

GET /api/batiments/<id>/calculs/   liste (sans séries), plus récent d'abord ; `?niveau=N1`
GET /api/calculs/<id>/             un calcul complet (séries comprises) : rechargeable sans recalcul
POST /api/batiments/<id>/calculer/ {niveaux: ['N0','N1'], scenario?, meteo?, force?} — N0/N1 synchrones (Lot 10)
GET /api/batiments/<id>/resultat/  dernier N0 / N1 / N2 terminé, avec « à jour » ou « périmé » (Lot 10)
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service
from .access import visible_buildings
from .models import CalcRun, Scenario

MAX_LIST = 50


class CalcRunSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = CalcRun
        fields = ['id', 'building', 'level', 'status', 'message', 'indicators', 'weather', 'settings',
                  'inputs_hash', 'duration_s', 'created_at', 'job']


class CalcRunDetailSerializer(CalcRunSummarySerializer):
    class Meta(CalcRunSummarySerializer.Meta):
        fields = CalcRunSummarySerializer.Meta.fields + ['results']


class BuildingCalcRunsView(APIView):
    def get(self, request, pk):
        building = get_object_or_404(visible_buildings(request), pk=pk)
        qs = building.calc_runs.all()
        level = request.query_params.get('niveau')
        if level in ('N0', 'N1', 'N2'):
            qs = qs.filter(level=level)
        return Response({'results': CalcRunSummarySerializer(qs[:MAX_LIST], many=True).data})


class CalcRunDetailView(APIView):
    def get(self, request, pk):
        run = get_object_or_404(CalcRun.objects.filter(building__in=visible_buildings(request)), pk=pk)
        return Response(CalcRunDetailSerializer(run).data)


SERIES_KEYS = ('t_air', 't_op', 'phi_hc_w', 'envelope_flux_w', 'final_exterior_surface_temp',
               'final_interior_surface_temp')


def compact(run):
    """Résumé d'un calcul pour la fiche : indicateurs, bilan, mois — sans les séries horaires."""
    d = CalcRunSummarySerializer(run).data
    d['results'] = {k: v for k, v in (run.results or {}).items() if k not in SERIES_KEYS}
    d['settings'] = {k: v for k, v in (run.settings or {}).items() if k != 'facts'}
    return d


class CalculerSerializer(serializers.Serializer):
    niveaux = serializers.ListField(child=serializers.ChoiceField(choices=['N0', 'N1']), min_length=1, max_length=2,
                                    default=['N0', 'N1'])
    scenario = serializers.IntegerField(required=False, allow_null=True, default=None)
    meteo = serializers.DictField(required=False, default=dict)
    force = serializers.BooleanField(required=False, default=False)


def scenario_of(building, sid):
    if sid in (None, ''):
        return None
    return Scenario.objects.filter(pk=sid, building=building).first()


class CalculerView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(visible_buildings(request), pk=pk)
        ser = CalculerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        sc = scenario_of(b, d['scenario'])
        if d['scenario'] is not None and sc is None:
            return Response({'detail': 'Variante inconnue.'}, status=status.HTTP_400_BAD_REQUEST)
        meteo = {k: v for k, v in d['meteo'].items() if k in ('dataset_id', 'start', 'end')}
        runs = []
        try:
            for lvl in d['niveaux']:
                run, fresh = calc_service.run_level(b, lvl, scenario=sc, weather_spec=meteo or None,
                                                    owner_sub=access.sub_of(request), force=d['force'])
                runs.append({**compact(run), 'fresh': fresh})
        except calc_service.Busy as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except calc_service.CalcError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'runs': runs})


class ResultatView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(visible_buildings(request), pk=pk)
        sc = scenario_of(b, request.query_params.get('scenario'))
        out = {}
        for lvl in ('N0', 'N1', 'N2'):
            run = calc_service.current_or_latest(b, lvl, sc)
            if run is None:
                out[lvl] = None
                continue
            d = compact(run)
            d['up_to_date'] = calc_service.is_up_to_date(run) if lvl != 'N2' else None
            out[lvl] = d
        return Response(out)
