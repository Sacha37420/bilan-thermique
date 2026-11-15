"""Historique des calculs (Lot 3, refonte) — routes nouvelles, aucune route existante modifiée.

GET /api/batiments/<id>/calculs/   liste (sans séries), plus récent d'abord ; `?niveau=N1`
GET /api/calculs/<id>/             un calcul complet (séries et entrées figées comprises) : rechargeable sans recalcul
GET /api/calculs/<a>/comparer/<b>/ deux calculs d'un même bâtiment : entrées qui diffèrent, écart des indicateurs (Lot 24)
POST /api/batiments/<id>/calculer/ {niveaux: ['N0','N1','N2'], scenario?, meteo?, force?} — N0/N1 synchrones (Lot 10) ;
    N2 en tâche de fond, gestionnaire seulement (Lot 23) : la réponse porte le calcul en attente et son `job`
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
        fields = ['id', 'building', 'scenario', 'level', 'status', 'message', 'indicators', 'weather', 'settings',
                  'inputs_hash', 'duration_s', 'created_at', 'job']


class CalcRunDetailSerializer(CalcRunSummarySerializer):
    class Meta(CalcRunSummarySerializer.Meta):
        fields = CalcRunSummarySerializer.Meta.fields + ['results', 'inputs']


def with_state(runs):
    """Ajoute « à jour », « depuis la fiche » et le mode à des calculs déjà sérialisés (empreinte courante
    calculée une fois par niveau, variante et météo)."""
    cache = {}
    out = []
    for run, d in runs:
        d = dict(d)
        d['from_fiche'] = calc_service.from_fiche(run)
        d['mode'] = (run.settings or {}).get('mode') or ('fiche' if d['from_fiche'] else 'calcul_3d')
        d['scenario_name'] = run.scenario.name if run.scenario_id else None
        if run.status == 'DONE' and d['from_fiche']:
            key = (run.level, run.scenario_id, (run.weather or {}).get('hash'))
            if key not in cache:
                cache[key] = calc_service.inputs_hash_for(run.building, run.level, run.scenario, key[2])
            d['up_to_date'] = run.inputs_hash == cache[key]
        else:
            d['up_to_date'] = None
        d['settings'] = {k: v for k, v in (run.settings or {}).items() if k != 'facts'}
        out.append(d)
    return out


class BuildingCalcRunsView(APIView):
    def get(self, request, pk):
        building = get_object_or_404(visible_buildings(request), pk=pk)
        qs = building.calc_runs.all()
        level = request.query_params.get('niveau')
        if level in ('N0', 'N1', 'N2'):
            qs = qs.filter(level=level)
        sc = request.query_params.get('scenario')
        if sc is not None:   # Lot 23 : « scenario= » vide = l'état actuel (base)
            qs = qs.filter(scenario__isnull=True) if sc in ('', 'base') else qs.filter(scenario_id=int(sc) if sc.isdigit() else -1)
        runs = list(qs.select_related('scenario', 'building')[:MAX_LIST])
        return Response({'results': with_state((r, CalcRunSummarySerializer(r).data) for r in runs)})


class CalcRunDetailView(APIView):
    def get(self, request, pk):
        run = get_object_or_404(CalcRun.objects.filter(building__in=visible_buildings(request)), pk=pk)
        d = with_state([(run, CalcRunDetailSerializer(run).data)])[0]
        d['settings'] = run.settings or {}
        return Response(d)


def _flat(d, prefix=''):
    out = {}
    for k, v in (d or {}).items():
        key = f'{prefix}{k}'
        if isinstance(v, dict):
            out.update(_flat(v, key + '.'))
        else:
            out[key] = v
    return out


def compare_runs(a, b):
    """Entrées qui diffèrent (faits, puis le reste des entrées figées) et écart des indicateurs."""
    fa = (a.inputs or {}).get('facts') or (a.settings or {}).get('facts') or {}
    fb = (b.inputs or {}).get('facts') or (b.settings or {}).get('facts') or {}
    facts_diff = []
    for k in sorted(set(fa) | set(fb)):
        x, y = fa.get(k), fb.get(k)
        if (x or {}).get('value') != (y or {}).get('value') or (x or {}).get('method') != (y or {}).get('method'):
            facts_diff.append({'key': k, 'a': x, 'b': y})
    skip = ('facts',)
    ia = _flat({k: v for k, v in (a.inputs or a.settings or {}).items() if k not in skip})
    ib = _flat({k: v for k, v in (b.inputs or b.settings or {}).items() if k not in skip})
    other = [{'key': k, 'a': ia.get(k), 'b': ib.get(k)} for k in sorted(set(ia) | set(ib)) if ia.get(k) != ib.get(k)]
    ind = []
    for k in sorted(set(a.indicators or {}) | set(b.indicators or {})):
        x, y = (a.indicators or {}).get(k), (b.indicators or {}).get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
            ind.append({'key': k, 'a': x, 'b': y, 'delta': round(y - x, 3),
                        'pct': round((y / x - 1) * 100, 1) if x else None})
    weather = None
    if (a.weather or {}).get('hash') != (b.weather or {}).get('hash'):
        weather = {'a': (a.weather or {}).get('source'), 'b': (b.weather or {}).get('source')}
    return {'facts': facts_diff, 'inputs': other, 'indicators': ind, 'weather': weather}


class CalcRunCompareView(APIView):
    def get(self, request, a, b):
        qs = CalcRun.objects.filter(building__in=visible_buildings(request))
        ra, rb = get_object_or_404(qs, pk=a), get_object_or_404(qs, pk=b)
        if ra.building_id != rb.building_id:
            return Response({'detail': 'On ne compare que deux calculs du même bâtiment.'},
                            status=status.HTTP_400_BAD_REQUEST)
        summary = with_state([(r, CalcRunSummarySerializer(r).data) for r in (ra, rb)])
        return Response({'a': summary[0], 'b': summary[1], **compare_runs(ra, rb)})


SERIES_KEYS = ('t_air', 't_op', 't_mrt', 'phi_hc_w', 'envelope_flux_w', 'final_exterior_surface_temp',
               'final_interior_surface_temp')


def compact(run):
    """Résumé d'un calcul pour la fiche : indicateurs, bilan, mois — sans les séries horaires."""
    d = CalcRunSummarySerializer(run).data
    d['results'] = {k: v for k, v in (run.results or {}).items() if k not in SERIES_KEYS}
    d['settings'] = {k: v for k, v in (run.settings or {}).items() if k != 'facts'}
    return d


class CalculerSerializer(serializers.Serializer):
    niveaux = serializers.ListField(child=serializers.ChoiceField(choices=['N0', 'N1', 'N2']), min_length=1, max_length=3,
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
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        ser = CalculerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        sc = scenario_of(b, d['scenario'])
        if d['scenario'] is not None and sc is None:
            return Response({'detail': 'Variante inconnue.'}, status=status.HTTP_400_BAD_REQUEST)
        meteo = {k: v for k, v in d['meteo'].items() if k in ('dataset_id', 'start', 'end')}
        levels = list(dict.fromkeys(d['niveaux']))
        if 'N2' in levels and not access.can_manage(request, b):
            return Response({'detail': 'Le N2 est réservé aux gestionnaires du bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        runs = []
        try:
            for lvl in levels:
                if lvl == 'N2':
                    run, fresh = calc_service.start_n2(b, scenario=sc, weather_spec=meteo or None,
                                                       owner_sub=access.sub_of(request), force=d['force'])
                else:
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
            d['up_to_date'] = calc_service.is_up_to_date(run)
            d['from_fiche'] = calc_service.from_fiche(run)
            out[lvl] = d
        out['can_n2'] = access.can_manage(request, b)  # Lot 23 : le N2 est réservé aux gestionnaires
        return Response(out)
