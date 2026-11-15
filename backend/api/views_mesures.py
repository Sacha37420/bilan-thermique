"""Mesures, sensibilité, calage (Lot 17, refonte). Routes nouvelles uniquement.

GET/POST /api/batiments/<id>/mesures/            {kind: facture|courbe|sonde|test, energy?, label?, rows? | csv? | n50?}
DELETE   /api/mesures/<id>/
POST     /api/batiments/<id>/sensibilite/        criblage de Morris (N1 vectorisé)
POST     /api/batiments/<id>/calage/             {measurement_id, dataset_id?} → job (proposition)
POST     /api/batiments/<id>/calage/appliquer/   {job_id} → valeurs ajustées entrées comme « inféré par calage »
"""

import time

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, calibration, facts, geometry_service, tasks
from .models import Job, Measurement
from .serializers import JobSerializer

ENERGIES = ('electricite', 'gaz', 'fioul', 'bois', 'reseau')


def _m(m):
    return {'id': m.pk, 'kind': m.kind, 'energy': m.energy, 'label': m.label, 'period_start': m.period_start,
            'period_end': m.period_end, 'n_values': len(m.values), 'values': m.values if m.kind != 'sonde' else m.values[:48],
            'created_at': m.created_at}


class MeasurementsView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        rows = [_m(m) for m in b.measurements.all() if access.can_see_personal(request, b, m.owner_sub)]
        return Response({'results': rows})

    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        d = request.data
        kind, energy = d.get('kind'), d.get('energy') or ''
        try:
            if kind == 'test':
                n50 = float(d.get('n50'))
                f = facts.record(b, 'ventilation.n50', n50, 'mesure', source=f"test d'étanchéité {d.get('label') or ''}".strip(),
                                 author_sub=access.sub_of(request), author_name=access.name_of(request))
                return Response({'fact': facts.fact_dict(f, access.sub_of(request))}, status=status.HTTP_201_CREATED)
            if kind in ('facture', 'courbe') and energy not in ENERGIES:
                raise calibration.MeasureError('Énergie : électricité, gaz, fioul, bois ou réseau.')
            if kind == 'facture':
                values = calibration.parse_bills(d.get('rows') or [])
            elif kind == 'courbe':
                csv_text = d.get('csv')
                if not isinstance(csv_text, str) or len(csv_text) > 3_000_000:
                    raise calibration.MeasureError('Fichier attendu (3 Mo au plus).')
                values = calibration.parse_curve_csv(csv_text)
            elif kind == 'sonde':
                values = calibration.parse_sensor_csv(str(d.get('csv') or ''))
            else:
                raise calibration.MeasureError('Type de mesure inconnu.')
        except (calibration.MeasureError, TypeError, ValueError, facts.FactError) as exc:
            msg = str(exc) if isinstance(exc, (calibration.MeasureError, facts.FactError)) else 'Valeur invalide.'
            return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
        starts = [v.get('start') or v.get('t') for v in values]
        ends = [v.get('end') or v.get('t') for v in values]
        m = Measurement.objects.create(building=b, owner_sub=access.sub_of(request), kind=kind, energy=energy,
                                       label=str(d.get('label') or '')[:150], values=values,
                                       period_start=min(starts)[:10], period_end=max(ends)[:10])
        return Response(_m(m), status=status.HTTP_201_CREATED)


class MeasurementDetailView(APIView):
    def delete(self, request, pk):
        m = get_object_or_404(Measurement.objects.filter(building__in=access.visible_buildings(request)), pk=pk)
        if not access.can_see_personal(request, m.building, m.owner_sub):
            return Response(status=status.HTTP_404_NOT_FOUND)
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SensitivityView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        try:
            weather, _ = calc_service.resolve_weather(b)
        except calc_service.CalcError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        t0 = time.monotonic()
        rows = calibration.morris(b, facts.effective(b), weather, geometry_service.effective_summary(b), r=6)
        return Response({'ranking': rows, 'duration_s': round(time.monotonic() - t0, 2)})


class CalibrateView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        m = Measurement.objects.filter(pk=request.data.get('measurement_id'), building=b).first()
        if m is None or not access.can_see_personal(request, b, m.owner_sub) or m.kind not in ('facture', 'courbe'):
            return Response({'detail': 'Factures ou courbe de ce bâtiment attendues.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(m.values) < 3:
            return Response({'detail': 'Au moins trois périodes facturées pour caler.'}, status=status.HTTP_400_BAD_REQUEST)
        if b.georef_lat is None and not request.data.get('dataset_id'):
            return Response({'detail': 'Bâtiment sans position : choisissez une météo.'}, status=status.HTTP_400_BAD_REQUEST)
        job = Job.objects.create(kind='calibrate', params={'building_id': b.pk, 'measurement_id': m.pk},
                                 owner_sub=access.sub_of(request))
        tasks.calibrate_building.delay(job.id, b.pk, m.pk, request.data.get('dataset_id'))
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ApplyCalibrationView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        job = Job.objects.filter(pk=request.data.get('job_id'), kind='calibrate', owner_sub=access.sub_of(request),
                                 status=Job.DONE, params__building_id=b.pk).first()
        if job is None or not (job.result or {}).get('parameters'):
            return Response({'detail': 'Calage introuvable ou sans paramètre.'}, status=status.HTTP_400_BAD_REQUEST)
        calibration.record_calibration(b, job.result, access.sub_of(request))
        return Response({'applied': job.result['parameters']})
