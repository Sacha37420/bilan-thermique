"""Initialisation automatique d'un bâtiment (Lot 9, refonte). Routes nouvelles uniquement.

POST /api/batiments/initialiser/            {address | rnb_id | lat+lon, name?} → crée le bâtiment, lance A1→A6
POST /api/batiments/<id>/initialiser/       rejoue la chaîne (n'écrase aucune surcharge)
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, tasks
from .environment_service import unique_name
from .models import Building, Job
from .serializers import JobSerializer


class InitSerializer(serializers.Serializer):
    address = serializers.CharField(max_length=300, required=False, allow_blank=True, default='')
    rnb_id = serializers.RegexField(r'^[A-Z0-9]{12}$', required=False, allow_blank=True, default='')
    lat = serializers.FloatField(min_value=41.0, max_value=51.5, required=False, allow_null=True, default=None)
    lon = serializers.FloatField(min_value=-5.5, max_value=10.0, required=False, allow_null=True, default=None)
    name = serializers.CharField(max_length=140, required=False, allow_blank=True, default='')

    def validate(self, d):
        if not (d['address'].strip() or d['rnb_id'] or (d['lat'] is not None and d['lon'] is not None)):
            raise serializers.ValidationError('Adresse, identifiant RNB ou position (lat, lon) requis.')
        return d


def launch_init(building, request):
    job = Job.objects.create(kind='init_building', params={'building_id': building.pk},
                             owner_sub=access.sub_of(request))
    Building.objects.filter(pk=building.pk).update(pipeline_status='en_attente')
    tasks.init_building.delay(job.id, building.pk)
    return job


def create_building(d, portfolio=None, name_hint=''):
    name = (d.get('name') or '').strip() or name_hint or d.get('address') or d.get('rnb_id') or \
        f"Bâtiment {d.get('lat'):.5f}, {d.get('lon'):.5f}"
    return Building.objects.create(
        name=unique_name(Building, name[:140]), address=(d.get('address') or '')[:300], rnb_id=d.get('rnb_id') or '',
        georef_lat=d.get('lat'), georef_lon=d.get('lon'), pipeline_status='en_attente',
        **({'portfolio': portfolio} if portfolio is not None else {}))


class InitNewBuildingView(APIView):
    def post(self, request):
        ser = InitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        b = create_building(ser.validated_data)
        job = launch_init(b, request)
        return Response({'building_id': b.pk, 'job': JobSerializer(job).data}, status=status.HTTP_202_ACCEPTED)


class ReinitBuildingView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Action réservée aux gestionnaires de ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        if not (b.address or b.rnb_id or b.georef_lat is not None):
            return Response({'detail': 'Ce bâtiment n’a ni adresse, ni identifiant RNB, ni position.'},
                            status=status.HTTP_400_BAD_REQUEST)
        job = launch_init(b, request)
        return Response({'building_id': b.pk, 'job': JobSerializer(job).data}, status=status.HTTP_202_ACCEPTED)
