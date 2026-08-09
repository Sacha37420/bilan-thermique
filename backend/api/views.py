from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, status
from rest_framework.response import Response
from .models import Department, UserRecord, ParoiModel, Building, Environment, Job
from .serializers import (
    DepartmentSerializer, UserRecordSerializer, CalculRequestSerializer, ParoiModelSerializer,
    BuildingSerializer, EnvironmentSerializer, JobSerializer, BuildingCalculRequestSerializer,
    RefineMeshRequestSerializer, GenerateEnvironmentRequestSerializer,
    GenerateBuildingEnvironmentRequestSerializer, WeatherFetchRequestSerializer,
    SearchNearbyBuildingsRequestSerializer, GroundAltitudeRequestSerializer,
)
from . import solver
from . import tasks
from . import building_solver
from . import geometry
from . import geodata
from . import elevation


class MeView(APIView):
    """
    permission_classes = [IsAuthenticated]
    GET /api/me/
    Retourne l'identité de l'utilisateur authentifié (depuis le JWT + DB).
    Crée un UserRecord à la première visite.
    """

    def get(self, request):
        email    = request.user.email
        username = request.user.username
        groups   = request.user.claims.get('groups', [])

        record, created = UserRecord.objects.get_or_create(
            email=email,
            defaults={'display_name': username},
        )

        return Response({
            'email':        email,
            'username':     username,
            'groups':       groups,
            'display_name': record.display_name,
            'department':   DepartmentSerializer(record.department).data
                            if record.department else None,
            'registered_at': record.registered_at,
            'is_new':        created,
        })


class DepartmentListView(generics.ListAPIView):
    """GET /api/departments/ — liste tous les départements."""

    queryset         = Department.objects.all()
    serializer_class = DepartmentSerializer


class UserListView(generics.ListAPIView):
    """GET /api/users/ — liste tous les utilisateurs enregistrés."""

    queryset         = UserRecord.objects.select_related('department')
    serializer_class = UserRecordSerializer


class ParoiModelListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/paroi-modeles/ — bibliothèque de modèles de paroi réutilisables."""

    queryset         = ParoiModel.objects.all()
    serializer_class = ParoiModelSerializer


class ParoiModelDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/paroi-modeles/<id>/"""

    queryset         = ParoiModel.objects.all()
    serializer_class = ParoiModelSerializer


class BuildingListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/batiments/ — bâtiments (enveloppe maillée + assignation de parois)."""

    queryset         = Building.objects.all()
    serializer_class = BuildingSerializer


class BuildingDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/batiments/<id>/"""

    queryset         = Building.objects.all()
    serializer_class = BuildingSerializer


class SearchNearbyBuildingsView(APIView):
    """
    POST /api/batiments/rechercher/  {lat, lon, radius_m?}
    Lot T (mode simplifié) — cherche les bâtiments réels les plus proches
    (IGN BD TOPO / OpenStreetMap, api.geodata.search_nearby_buildings), chacun
    déjà extrudé en enveloppe groupée (mur_1..mur_N/toiture/sol) prête à être
    envoyée telle quelle à POST /api/batiments/ une fois choisie — synchrone
    (rayon volontairement petit, quelques bâtiments au plus, contrairement à
    la génération d'environnement qui passe par Celery).
    """

    def post(self, request):
        serializer = SearchNearbyBuildingsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            candidates, n_skipped_too_complex = geodata.search_nearby_buildings(
                serializer.validated_data['lat'], serializer.validated_data['lon'],
                serializer.validated_data['radius_m'],
            )
        except geodata.GeodataError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'candidates': candidates, 'n_skipped_too_complex': n_skipped_too_complex})


class GroundAltitudeView(APIView):
    """
    POST /api/altitude/  {lat, lon}
    Lot AA — altitude du terrain en ce point (IGN RGE ALTI en France, repli
    mondial Open-Meteo Elevation). Synchrone : un point, un appel. Sert à
    préremplir Building.georef_ground_z, dont l'absence place silencieusement
    tous les obstacles IGN à leur altitude NGF absolue au-dessus d'un bâtiment
    posé à z = 0.
    """

    def post(self, request):
        serializer = GroundAltitudeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            altitude_m, source = elevation.ground_altitude(
                serializer.validated_data['lat'], serializer.validated_data['lon'],
            )
        except elevation.ElevationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'altitude_m': round(altitude_m, 2), 'source': source})


class GenerateBuildingEnvironmentView(APIView):
    """
    POST /api/batiments/<id>/generer-environnement/  {radius_m}
    Génère en tâche de fond un environnement dans le repère local de ce bâtiment
    géoréférencé (api.geodata + Building.georef_*), crée l'Environment et le lie
    directement au bâtiment — voir tasks.generate_environment_for_building.
    """

    def post(self, request, pk):
        building = get_object_or_404(Building, pk=pk)
        if building.georef_lat is None or building.georef_lon is None:
            return Response(
                {'detail': "Ce bâtiment n'a pas de géoréférencement — renseignez latitude/longitude "
                           "avant de générer un environnement."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = GenerateBuildingEnvironmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        job = Job.objects.create(
            kind='generate_environment_for_building',
            params={'building_id': building.pk, **serializer.validated_data},
        )
        tasks.generate_environment_for_building.delay(
            job.id, building.pk, serializer.validated_data['radius_m'],
            serializer.validated_data.get('terrain_spacing_m'),
        )
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class EnvironmentListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/environnements/ — maillages d'environnement (obstacles)."""

    queryset         = Environment.objects.all()
    serializer_class = EnvironmentSerializer


class EnvironmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/environnements/<id>/"""

    queryset         = Environment.objects.all()
    serializer_class = EnvironmentSerializer


class GenerateEnvironmentView(APIView):
    """
    POST /api/environnements/generer/  {lat, lon, radius_m}
    Génère en tâche de fond un maillage d'obstacles depuis l'IGN (BD TOPO, France)
    avec repli OpenStreetMap (api.geodata), à réviser puis enregistrer via le flux
    d'upload existant — ne crée pas d'Environment directement.
    """

    def post(self, request):
        serializer = GenerateEnvironmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        job = Job.objects.create(kind='generate_environment', params=serializer.validated_data)
        tasks.generate_environment.delay(job.id, serializer.validated_data)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class FetchWeatherView(APIView):
    """
    POST /api/meteo/recuperer/  {lat, lon, start_date, end_date, north_offset_deg?}
    Lot L — récupère en tâche de fond une série météo horaire réelle (Open-Meteo
    Archive) + position solaire calculée (api.weather_source), directement dans la
    convention azimuth interne de l'app. Endpoint autonome (pas de <id> bâtiment) :
    la météo n'est jamais persistée côté serveur, seulement renvoyée via job.result
    pour que le frontend la reprenne (page Calcul 3D).
    """

    def post(self, request):
        serializer = WeatherFetchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # dates -> ISO string : JSONField (Job.params) et la sérialisation Celery
        # (JSON) ne savent pas encoder un datetime.date brut.
        params = dict(serializer.validated_data)
        params['start_date'] = params['start_date'].isoformat()
        params['end_date'] = params['end_date'].isoformat()

        job = Job.objects.create(kind='fetch_weather', params=params)
        tasks.fetch_weather.delay(job.id, params)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class JobDetailView(generics.RetrieveAPIView):
    """GET /api/jobs/<id>/ — état d'une tâche asynchrone (polling frontend)."""

    queryset         = Job.objects.all()
    serializer_class = JobSerializer


class PrecomputeShadowsView(APIView):
    """
    POST /api/batiments/<id>/precalcul-ombrage/
    Lance le précalcul de visibilité solaire (Lot C) pour ce bâtiment, en
    tâche de fond. Un seul calcul d'ombrage à la fois pour tout le lab
    (--concurrency=1 sur le worker) ; on renvoie 409 si un calcul est déjà
    en cours plutôt que de le mettre en file silencieusement.
    """

    def post(self, request, pk):
        building = get_object_or_404(Building, pk=pk)

        if not building.envelope.get('triangles'):
            return Response({'detail': "Ce bâtiment n'a pas encore de maillage importé."},
                             status=status.HTTP_400_BAD_REQUEST)

        if Job.objects.filter(kind='shadow_precompute', status__in=[Job.PENDING, Job.RUNNING]).exists():
            return Response({'detail': "Un précalcul d'ombrage est déjà en cours pour le lab — réessayez plus tard."},
                             status=status.HTTP_409_CONFLICT)

        job = Job.objects.create(kind='shadow_precompute', params={'building_id': building.pk})
        tasks.precompute_shadows.delay(job.id, building.pk)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class BuildingRefineView(APIView):
    """
    POST /api/batiments/<id>/affiner-maillage/  {max_edge_length}
    Subdivise les triangles dont un côté dépasse max_edge_length (api.geometry.
    refine_envelope), en conservant l'assignation de paroi de chaque triangle
    parent sur ses enfants. Marque l'ombrage précalculé comme périmé.
    """

    def post(self, request, pk):
        building = get_object_or_404(Building, pk=pk)
        serializer = RefineMeshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        envelope = building.envelope
        if not envelope.get('triangles'):
            return Response({'detail': "Ce bâtiment n'a pas encore de maillage importé."},
                             status=status.HTTP_400_BAD_REQUEST)

        try:
            new_vertices, new_triangles = geometry.refine_envelope(
                envelope['vertices'], envelope['triangles'], serializer.validated_data['max_edge_length'],
            )
            new_triangles = geometry.compute_envelope_geometry(new_vertices, new_triangles)
        except geometry.GeometryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        building.envelope = {'vertices': new_vertices, 'triangles': new_triangles}
        building.sun_visibility_stale = True
        building.save(update_fields=['envelope', 'sun_visibility_stale', 'updated_at'])

        return Response(BuildingSerializer(building).data)


class BuildingCalculView(APIView):
    """
    POST /api/batiments/<id>/calcul-3d/
    Lance en tâche de fond la simulation du bâtiment entier (Lot D) : un
    système EF par triangle, tous couplés à un même nœud d'air pondéré par
    aire (api/building_solver.py). Mesuré à ~28 s pour 338 triangles sur une
    année horaire complète — trop long pour une requête HTTP synchrone dès
    qu'un bâtiment réel dépasse quelques centaines de triangles, d'où le
    passage en Celery (même mutex lab-wide qu'au Lot C : un calcul lourd à
    la fois, --concurrency=1 sur le worker).
    """

    def post(self, request, pk):
        building = get_object_or_404(Building, pk=pk)
        serializer = BuildingCalculRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        triangles = building.envelope.get('triangles', [])
        if not triangles:
            return Response({'detail': "Ce bâtiment n'a pas encore de maillage importé."},
                             status=status.HTTP_400_BAD_REQUEST)

        unassigned = sum(1 for t in triangles if t.get('paroi_model_id') is None)
        if unassigned:
            return Response(
                {'detail': f"{unassigned} triangle(s) sans modèle de paroi assigné — complétez l'assignation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shadow_mode = serializer.validated_data.get('shadow_mode', 'precomputed')
        if shadow_mode == 'precomputed' and building.sun_visibility_stale:
            return Response(
                {'detail': "L'ombrage précalculé est périmé (enveloppe ou environnement modifié depuis) — "
                           "relancez le précalcul, ou choisissez le mode temps réel."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Job.objects.filter(status__in=[Job.PENDING, Job.RUNNING]).exists():
            return Response({'detail': "Un calcul est déjà en cours pour le lab — réessayez plus tard."},
                             status=status.HTTP_409_CONFLICT)

        job = Job.objects.create(kind='building_calcul', params={'building_id': building.pk})
        tasks.run_building_calcul.delay(job.id, building.pk, serializer.validated_data)
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class Calcul1DView(APIView):
    """
    POST /api/calcul-1d/
    Simule le régime transitoire 1D d'une paroi multicouche, heure par heure,
    à partir de sa définition, du maillage souhaité, des conditions aux
    limites intérieures et d'une série météo extérieure. Voir api/solver.py.
    """

    def post(self, request):
        serializer = CalculRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = solver.run_simulation(serializer.validated_data)
        except solver.SimulationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result)
