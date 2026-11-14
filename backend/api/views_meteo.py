"""Météo choisie et comparée (Lot 13, refonte). Routes nouvelles uniquement.

GET  /api/meteo/jeux/?batiment=<id>          jeux utilisables pour ce bâtiment (proches, ou les miens)
POST /api/meteo/jeux/                        {type: tmy|reel|station|tracc, batiment, start?, end?, station?, scenario?} → job
POST /api/meteo/stations/                    {batiment} → job (stations Météo-France proches)
POST /api/meteo/importer/                    {format: epw|csv, content, batiment?, label?}
PUT  /api/batiments/<id>/meteo/              {dataset_id | null} : météo retenue pour N0/N1
POST /api/batiments/<id>/comparer-meteo/     {jeux: [ids], start?, end?} → N1 sous chaque météo
"""

from datetime import date

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, tasks, weather_service as ws
from .models import Job, WeatherDataset
from .serializers import JobSerializer

MAX_IMPORT_BYTES = 3 * 1024 * 1024


def summary(ds):
    return {'id': ds.pk, 'kind': ds.kind, 'label': ds.label, 'lat': ds.lat, 'lon': ds.lon, 'stats': ds.stats,
            'meta': {k: v for k, v in (ds.meta or {}).items() if k != 'raw'}, 'quality': ds.quality,
            'created_at': ds.created_at}


def _building(request, pk):
    return get_object_or_404(access.visible_buildings(request), pk=pk)


class DatasetsView(APIView):
    def get(self, request):
        sub = access.sub_of(request)
        qs = WeatherDataset.objects.defer('series')
        bid = request.query_params.get('batiment')
        out = []
        if bid:
            b = _building(request, bid)
            if b.georef_lat is not None:
                for ds in qs.filter(Q(kind__in=['tmy', 'reel', 'station', 'tracc']) | Q(owner_sub=sub))[:300]:
                    if ds.lat is None or ws.distance_km(b.georef_lat, b.georef_lon, ds.lat, ds.lon) <= \
                            (300 if ds.kind == 'tracc' else 80) or ds.owner_sub == sub:
                        out.append(summary(ds))
            return Response({'results': out[:60], 'selected': b.weather_dataset_id})
        return Response({'results': [summary(d) for d in qs.filter(owner_sub=sub)[:60]]})

    def post(self, request):
        d = request.data
        kind = d.get('type')
        b = _building(request, d.get('batiment'))
        if b.georef_lat is None:
            return Response({'detail': 'Bâtiment sans position.'}, status=status.HTTP_400_BAD_REQUEST)
        params = {'type': kind, 'lat': b.georef_lat, 'lon': b.georef_lon}
        try:
            if kind in ('reel', 'station'):
                s, e = date.fromisoformat(str(d.get('start'))), date.fromisoformat(str(d.get('end')))
                if e < s or (e - s).days > 366 or e >= date.today():
                    raise ValueError
                params.update(start=s.isoformat(), end=e.isoformat())
            if kind == 'station':
                st = d.get('station') or {}
                if not (str(st.get('id', '')).isdigit() and str(st.get('department', '')).isalnum()):
                    raise ValueError
                params['station'] = {k: st.get(k) for k in ('id', 'department', 'name', 'alt', 'distance_km')}
            if kind == 'tracc':
                if d.get('scenario') not in ws.TRACC_DATASETS:
                    raise ValueError
                params['scenario'] = d['scenario']
            if kind not in ('tmy', 'reel', 'station', 'tracc'):
                raise ValueError
        except (TypeError, ValueError):
            return Response({'detail': 'Paramètres météo invalides (type, période passée d’un an au plus, station, scénario).'},
                            status=status.HTTP_400_BAD_REQUEST)
        job = Job.objects.create(kind='weather_dataset', params=params, owner_sub=access.sub_of(request))
        tasks.build_weather_dataset.delay(job.id, params)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class StationsView(APIView):
    def post(self, request):
        b = _building(request, request.data.get('batiment'))
        if b.georef_lat is None:
            return Response({'detail': 'Bâtiment sans position.'}, status=status.HTTP_400_BAD_REQUEST)
        params = {'lat': b.georef_lat, 'lon': b.georef_lon, 'alt': b.georef_ground_z}
        job = Job.objects.create(kind='weather_stations', params=params, owner_sub=access.sub_of(request))
        tasks.list_weather_stations.delay(job.id, params)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class ImportView(APIView):
    def post(self, request):
        content, fmt = request.data.get('content'), request.data.get('format')
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_IMPORT_BYTES:
            return Response({'detail': 'Fichier attendu (3 Mo au plus).'}, status=status.HTTP_400_BAD_REQUEST)
        label = str(request.data.get('label') or '')[:120]
        try:
            if fmt == 'epw':
                series, lat, lon, place = ws.parse_epw(content)
                label = label or f'EPW {place}'
            elif fmt == 'csv':
                b = _building(request, request.data.get('batiment'))
                if b.georef_lat is None:
                    raise ws.WeatherError('Bâtiment sans position (nécessaire pour la position du soleil).')
                lat, lon = b.georef_lat, b.georef_lon
                series = ws.parse_csv_weather(content, lat, lon)
                label = label or 'Import CSV'
            else:
                raise ws.WeatherError('Format attendu : epw ou csv.')
            ds = ws.save('import', label, series, lat=lat, lon=lon, owner_sub=access.sub_of(request),
                         meta={'format': fmt}, quality={'n_hours': len(series)})
        except ws.WeatherError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(summary(ds), status=status.HTTP_201_CREATED)


class BuildingWeatherView(APIView):
    def put(self, request, pk):
        b = _building(request, pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        did = request.data.get('dataset_id')
        b.weather_dataset = get_object_or_404(WeatherDataset, pk=did) if did else None
        b.save(update_fields=['weather_dataset'])
        return Response({'selected': b.weather_dataset_id})


class CompareWeatherView(APIView):
    def post(self, request, pk):
        b = _building(request, pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        ids = request.data.get('jeux') or []
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4:
            return Response({'detail': 'De un à quatre jeux météo.'}, status=status.HTTP_400_BAD_REQUEST)
        rows = []
        for did in ids:
            ds = get_object_or_404(WeatherDataset.objects.defer('series'), pk=did)
            spec = {'dataset_id': ds.pk, 'start': request.data.get('start') or None, 'end': request.data.get('end') or None}
            try:
                run, _ = calc_service.run_level(b, 'N1', weather_spec=spec, owner_sub=access.sub_of(request))
                series, _desc = ws.series_for(spec)
            except calc_service.CalcError as exc:
                rows.append({'dataset': summary(ds), 'error': str(exc)})
                continue
            rows.append({'dataset': summary(ds), 'weather_stats': ws.stats(series), 'indicators': run.indicators,
                         'run_id': run.pk})
        return Response({'rows': rows})
