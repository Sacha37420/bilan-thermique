"""Protections solaires (Lot 14, refonte). Routes nouvelles uniquement.

GET          /api/protections/catalogue/
GET, POST    /api/batiments/<id>/protections/          {kind, orientation, mode?, params?, control?, label?, scenario?}
PATCH, DELETE /api/protections/<id>/
POST         /api/batiments/<id>/protections/effet/    N1 sans / avec toutes / avec chacune : confort d'été et hiver
"""

import copy

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, shading_service
from .engines import n1, shading
from .models import Building, Scenario, ShadingDevice

PARAM_RANGES = {'depth_m': (0, 5), 'gap_m': (0, 3), 'window_height_m': (0.3, 5), 'window_width_m': (0.3, 20),
                'extension_m': (0, 5), 'fin_depth_m': (0, 3), 'g': (0.05, 0.9), 'fs_dir': (0, 1), 'fs_dif': (0, 1),
                't_summer': (0, 1), 't_winter': (0, 1)}


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShadingDevice
        fields = ['id', 'kind', 'orientation', 'mode', 'label', 'params', 'control', 'scenario', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_kind(self, v):
        if v not in shading.KINDS:
            raise serializers.ValidationError('Type de protection inconnu.')
        return v

    def validate_orientation(self, v):
        if v not in shading_service.VALID_ORIENTATIONS:
            raise serializers.ValidationError('Orientation : N, E, S, O ou all.')
        return v

    def validate_mode(self, v):
        if v not in ('parametrique', 'geometrique'):
            raise serializers.ValidationError('Mode : parametrique ou geometrique.')
        return v

    def validate_params(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError('Paramètres attendus.')
        out = {}
        for k, x in v.items():
            if k not in PARAM_RANGES:
                raise serializers.ValidationError('Paramètre inconnu.')
            try:
                x = float(x)
            except (TypeError, ValueError):
                raise serializers.ValidationError('Nombre attendu.') from None
            lo, hi = PARAM_RANGES[k]
            if not lo <= x <= hi:
                raise serializers.ValidationError(f'{k} : entre {lo} et {hi}.')
            out[k] = x
        return out

    def validate_control(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError('Pilotage attendu.')
        mode = v.get('mode', 'planning')
        if mode not in ('planning', 'auto'):
            raise serializers.ValidationError('Pilotage : planning ou auto.')
        out = {'mode': mode, 'position': min(1.0, max(0.0, float(v.get('position', 1.0))))}
        if mode == 'planning':
            hours = v.get('hours') or []
            months = v.get('months') or []
            if not all(isinstance(h, int) and 0 <= h <= 23 for h in hours) or not all(isinstance(m, int) and 1 <= m <= 12 for m in months):
                raise serializers.ValidationError('Heures 0-23 et mois 1-12.')
            out.update(hours=sorted(set(hours)), months=sorted(set(months)))
        else:
            out.update(irradiance_w_m2=min(800.0, max(50.0, float(v.get('irradiance_w_m2', 200)))),
                       t_ext_min_c=min(35.0, max(10.0, float(v.get('t_ext_min_c', 20)))),
                       close_when_absent=bool(v.get('close_when_absent', False)))
        return out

    def validate(self, d):
        kind = d.get('kind', getattr(self.instance, 'kind', None))
        if d.get('mode') == 'geometrique' and kind not in shading.FIXED:
            raise serializers.ValidationError('Seules les protections fixes ont un mode géométrique.')
        return d


def _building(request, pk, write=False):
    b = get_object_or_404(access.visible_buildings(request), pk=pk)
    if write and not access.can_declare(request, b):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Lecture seule sur ce bâtiment.')
    return b


def _touch(building, device_mode):
    if device_mode == 'geometrique':
        Building.objects.filter(pk=building.pk).update(sun_visibility_stale=True)


class CatalogueView(APIView):
    def get(self, request):
        return Response({'kinds': shading.CATALOGUE, 'ranges': PARAM_RANGES, 'mobile_defaults': shading.MOBILE_DEFAULTS})


class DevicesView(APIView):
    def get(self, request, pk):
        b = _building(request, pk)
        return Response({'results': DeviceSerializer(b.shading_devices.all(), many=True).data})

    def post(self, request, pk):
        b = _building(request, pk, write=True)
        ser = DeviceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        sc = ser.validated_data.get('scenario')
        if sc is not None and sc.building_id != b.pk:
            return Response({'detail': 'Variante d’un autre bâtiment.'}, status=status.HTTP_400_BAD_REQUEST)
        d = ser.save(building=b, author_sub=access.sub_of(request))
        _touch(b, d.mode)
        return Response(DeviceSerializer(d).data, status=status.HTTP_201_CREATED)


class DeviceDetailView(APIView):
    def _get(self, request, pk):
        d = get_object_or_404(ShadingDevice.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_declare(request, d.building):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Lecture seule sur ce bâtiment.')
        return d

    def patch(self, request, pk):
        d = self._get(request, pk)
        ser = DeviceSerializer(d, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        old_mode = d.mode
        ser.save()
        _touch(d.building, old_mode)
        _touch(d.building, d.mode)
        return Response(DeviceSerializer(d).data)

    def delete(self, request, pk):
        d = self._get(request, pk)
        b, mode = d.building, d.mode
        d.delete()
        _touch(b, mode)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _summary(r):
    return {'heating_kwh': round(r['heating_kwh'], 0), 'cooling_kwh': round(r['cooling_kwh'], 0),
            't_op_max': r['t_op_max'], 'hours_over_26': int(sum(1 for t in r['t_op'] if t > 26)),
            'hours_over_28': int(sum(1 for t in r['t_op'] if t > 28)),
            'degree_hours_26': round(sum(max(0.0, t - 26) for t in r['t_op']), 0),
            'solar_by_orientation_kwh': r.get('solar_windows_by_orientation_kwh') or {}}


class EffectView(APIView):
    """Effet de chaque protection (N1, sans tirages) : heures d'inconfort l'été, perte d'apports l'hiver."""

    def post(self, request, pk):
        b = _building(request, pk)
        sc = None
        if request.data.get('scenario'):
            sc = Scenario.objects.filter(pk=request.data['scenario'], building=b).first()
        try:
            weather, wdesc = calc_service.resolve_weather(b)
        except calc_service.CalcError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        base, _vals = calc_service.params_for(b, sc)
        devs = list(base.devices)
        variants = [('sans', [])] + [('toutes', devs)] + [(f"{d['kind']} {d['orientation']}", [d]) for d in devs]
        params = []
        for _label, ds in variants:
            q = copy.deepcopy(base)
            q.devices = ds
            params.append(q)
        res = n1.run(params, weather, keep_series=True)
        rows = [{'label': lbl, 'device_id': (ds[0]['id'] if len(ds) == 1 and lbl not in ('sans', 'toutes') else None),
                 **_summary(r)} for (lbl, ds), r in zip(variants, res)]
        return Response({'weather': wdesc, 'rows': rows})
