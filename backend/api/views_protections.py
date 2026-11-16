"""Protections solaires (Lot 14, refonte ; catalogue v2 et paramètres complets : lot 36, chantier bt-potentiels I1).

GET          /api/protections/catalogue/                → I1 (`engines.shading.catalogue`)
GET, POST    /api/batiments/<id>/protections/          {kind, orientation, mode?, params?, control?, label?, scenario?}
PATCH, DELETE /api/protections/<id>/                    (rattacher à l'état actuel : scenario null ; à une amélioration : son id)
POST         /api/batiments/<id>/protections/effet/    N1 sans / avec toutes / avec chacune : confort d'été et hiver

`params` et `control` sont validés par le catalogue (clé inconnue, valeur hors bornes → 400, message fixe) et
enregistrés complets (défauts du catalogue posés). Écriture : qui peut déclarer sur le bâtiment (comme les faits).
"""

import copy

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, shading_service
from .engines import comfort, n1, shading
from .models import Building, Scenario, ShadingDevice

FIELDS = ('kind', 'orientation', 'mode', 'label', 'params', 'control', 'scenario')


class Refused(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def serialize(d):
    m = shading.KINDS.get(d.kind)
    return {'id': d.pk, 'kind': d.kind, 'orientation': d.orientation, 'mode': d.mode, 'label': d.label,
            'params': d.params, 'control': d.control, 'scenario': d.scenario_id,
            'famille': m['famille'] if m else None, 'libelle': m['libelle'] if m else d.kind,
            'created_at': d.created_at.isoformat() if d.created_at else None}


def _clean(data, building, instance=None):
    """Champs validés d'une protection (création ou modification partielle). Lève Refused (message fixe)."""
    if not isinstance(data, dict):
        raise Refused('Protection attendue.')
    if any(k not in FIELDS for k in data):
        raise Refused('Champ inconnu.')
    kind = data.get('kind', instance.kind if instance else None)
    if kind not in shading.KINDS:
        raise Refused('Type de protection inconnu.')
    model = shading.KINDS[kind]
    out = {'kind': kind}
    orientation = data.get('orientation', instance.orientation if instance else None)
    if orientation not in shading_service.VALID_ORIENTATIONS:
        raise Refused('Orientation : N, E, S, O ou all.')
    out['orientation'] = orientation
    mode = data.get('mode', instance.mode if instance else 'parametrique')
    if mode not in ('parametrique', 'geometrique'):
        raise Refused('Mode : parametrique ou geometrique.')
    if mode not in model['modes']:
        raise Refused('Seules les protections fixes à débord ou à joues ont un mode géométrique.')
    out['mode'] = mode
    kind_changed = instance is not None and kind != instance.kind
    try:
        if 'params' in data or instance is None or kind_changed:
            out['params'] = shading.validate_params(kind, data.get('params') if data.get('params') is not None else {})
        if 'control' in data or instance is None or kind_changed:
            out['control'] = shading.validate_control(kind, data.get('control') if data.get('control') is not None else {})
    except shading.CatalogueError as exc:
        raise Refused(str(exc)) from None
    if 'label' in data:
        out['label'] = str(data.get('label') or '')[:150]
    if 'scenario' in data or instance is None:
        sid = data.get('scenario')
        if sid in (None, ''):
            out['scenario'] = None
        else:
            if isinstance(sid, bool) or not isinstance(sid, int):
                raise Refused('Amélioration inconnue.')
            sc = Scenario.objects.filter(pk=sid).first()
            if sc is None or sc.building_id != building.pk:
                raise Refused('Amélioration d’un autre bâtiment.')
            if sc.genere:
                raise Refused('Amélioration calculée depuis un bouquet : modifiez le bouquet.')
            out['scenario'] = sc
    return out


def _building(request, pk, write=False):
    b = get_object_or_404(access.visible_buildings(request), pk=pk)
    if write and not access.can_declare(request, b):
        raise PermissionDenied('Lecture seule sur ce bâtiment.')
    return b


def _touch(building, device_mode):
    if device_mode == 'geometrique':
        Building.objects.filter(pk=building.pk).update(sun_visibility_stale=True)


class CatalogueView(APIView):
    def get(self, request):
        return Response(shading.catalogue())


class DevicesView(APIView):
    def get(self, request, pk):
        b = _building(request, pk)
        return Response({'results': [serialize(d) for d in b.shading_devices.all()]})

    def post(self, request, pk):
        b = _building(request, pk, write=True)
        try:
            data = _clean(request.data, b)
        except Refused as exc:
            return Response({'detail': exc.message}, status=status.HTTP_400_BAD_REQUEST)
        d = ShadingDevice.objects.create(building=b, author_sub=access.sub_of(request), **data)
        _touch(b, d.mode)
        return Response(serialize(d), status=status.HTTP_201_CREATED)


class DeviceDetailView(APIView):
    def _get(self, request, pk):
        d = get_object_or_404(ShadingDevice.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_declare(request, d.building):
            raise PermissionDenied('Lecture seule sur ce bâtiment.')
        return d

    def patch(self, request, pk):
        d = self._get(request, pk)
        if d.scenario_id and d.scenario.genere:
            return Response({'detail': 'Amélioration calculée depuis un bouquet : modifiez le bouquet.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            data = _clean(request.data, d.building, d)
        except Refused as exc:
            return Response({'detail': exc.message}, status=status.HTTP_400_BAD_REQUEST)
        old_mode = d.mode
        for k, v in data.items():
            setattr(d, k, v)
        d.save()
        _touch(d.building, old_mode)
        _touch(d.building, d.mode)
        return Response(serialize(d))

    def delete(self, request, pk):
        d = self._get(request, pk)
        b, mode = d.building, d.mode
        d.delete()
        _touch(b, mode)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _summary(r, t_ext, hidx, residential):
    dh, hours, _over = comfort.dh_re2020(r['t_op'], t_ext, hidx, residential)
    return {'heating_kwh': round(r['heating_kwh'], 0), 'cooling_kwh': round(r['cooling_kwh'], 0),
            't_op_max': r['t_op_max'], 'hours_over_26': int(sum(1 for t in r['t_op'] if t > 26)),
            'hours_over_28': int(sum(1 for t in r['t_op'] if t > 28)),
            'degree_hours_26': round(sum(max(0.0, t - 26) for t in r['t_op']), 0),
            'dh_re2020': round(dh, 0), 'heures_inconfort': hours,
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
        weather = calc_service.scenario_weather(weather, sc)
        base, _vals = calc_service.params_for(b, sc)
        devs = list(base.devices)
        variants = [('sans', [])] + [('toutes', devs)] + [(f"{d['kind']} {d['orientation']}", [d]) for d in devs]
        params = []
        for _label, ds in variants:
            q = copy.deepcopy(base)
            q.devices = ds
            params.append(q)
        res = n1.run(params, weather, keep_series=True)
        t_ext = [p['t_ext'] for p in weather]
        hidx = [p.get('hour_index') if p.get('hour_index') is not None else i for i, p in enumerate(weather)]
        rows = [{'label': lbl, 'device_id': (ds[0]['id'] if len(ds) == 1 and lbl not in ('sans', 'toutes') else None),
                 **_summary(r, t_ext, hidx, base.residential)} for (lbl, ds), r in zip(variants, res)]
        return Response({'weather': wdesc, 'rows': rows})
