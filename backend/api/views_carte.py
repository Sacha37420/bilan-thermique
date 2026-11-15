"""Carte (Lot 19, refonte — U4). Routes nouvelles uniquement.

POST /api/carte/batiments/        {lat_min, lon_min, lat_max, lon_max} → emprises BD TOPO de la zone (≤ 800 m de côté)
POST /api/parcs/<id>/ajouter/     {points: [{lat, lon, name?}]} → bâtiments ajoutés au parc et analysés (file `parc`)
"""

import math

import shapely.geometry
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, geodata, parc, sources, tasks
from .environment_service import unique_name
from .models import Building
from .views_parc import _portfolio

MAX_SIDE_M = 800
MAX_POINTS = 50


def _f(d, k):
    v = float(d[k])
    if not math.isfinite(v):
        raise ValueError
    return v


class AreaBuildingsView(APIView):
    def post(self, request):
        try:
            la0, lo0, la1, lo1 = (_f(request.data, k) for k in ('lat_min', 'lon_min', 'lat_max', 'lon_max'))
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'Zone attendue (lat_min, lon_min, lat_max, lon_max).'}, status=status.HTTP_400_BAD_REQUEST)
        if not (41 <= la0 < la1 <= 51.5 and -5.5 <= lo0 < lo1 <= 10):
            return Response({'detail': 'Zone hors de France métropolitaine.'}, status=status.HTTP_400_BAD_REQUEST)
        h = (la1 - la0) * 111_320
        w = (lo1 - lo0) * 111_320 * math.cos(math.radians((la0 + la1) / 2))
        if h > MAX_SIDE_M or w > MAX_SIDE_M:
            return Response({'detail': f'Zone trop grande : zoomez (côté de {MAX_SIDE_M} m au plus).', 'code': 'zoom'},
                            status=status.HTTP_400_BAD_REQUEST)
        key = f'{la0:.5f},{lo0:.5f},{la1:.5f},{lo1:.5f}'
        try:
            items = sources.cached('bdtopo_bbox', key, lambda: geodata.fetch_ign_buildings((la0, lo0, la1, lo1)), ttl_days=60)
        except geodata.GeodataError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        out = []
        for i, it in enumerate(items[:300]):
            ring = it['footprint_latlon']
            try:
                poly = shapely.geometry.Polygon([(lo, la) for la, lo in ring])
            except (ValueError, TypeError):
                continue
            if poly.is_empty:
                continue
            c = poly.representative_point()
            area = poly.area * (111_320 ** 2) * math.cos(math.radians(c.y))
            out.append({'id': i, 'lat': round(c.y, 6), 'lon': round(c.x, 6), 'height_m': it.get('height_m'),
                        'area_m2': round(area, 0), 'ring': [[round(a, 6), round(b, 6)] for a, b in ring]})
        return Response({'results': out})


class AddPointsView(APIView):
    def post(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        pts = request.data.get('points')
        if not isinstance(pts, list) or not 1 <= len(pts) <= MAX_POINTS:
            return Response({'detail': f'De 1 à {MAX_POINTS} points.'}, status=status.HTTP_400_BAD_REQUEST)
        created = []
        for pt in pts:
            try:
                lat, lon = _f(pt, 'lat'), _f(pt, 'lon')
            except (KeyError, TypeError, ValueError):
                return Response({'detail': 'Point invalide.'}, status=status.HTTP_400_BAD_REQUEST)
            if not (41 <= lat <= 51.5 and -5.5 <= lon <= 10):
                return Response({'detail': 'Point hors de France métropolitaine.'}, status=status.HTTP_400_BAD_REQUEST)
            name = str(pt.get('name') or f'Bâtiment {lat:.5f}, {lon:.5f}')[:140]
            created.append(Building.objects.create(portfolio=p, name=unique_name(Building, name), georef_lat=lat, georef_lon=lon,
                                                   pipeline_status='en_attente'))
        tasks.process_parc_next.apply_async(args=[p.pk, access.sub_of(request)], queue='parc')
        return Response({'created': [b.pk for b in created], 'counts': parc.counts(p)}, status=status.HTTP_201_CREATED)
