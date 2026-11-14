"""Historique des calculs (Lot 3, refonte) — routes nouvelles, aucune route existante modifiée.

GET /api/batiments/<id>/calculs/   liste (sans séries), plus récent d'abord ; `?niveau=N1`
GET /api/calculs/<id>/             un calcul complet (séries comprises) : rechargeable sans recalcul
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import visible_buildings
from .models import CalcRun

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
