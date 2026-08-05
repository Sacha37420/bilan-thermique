from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, status
from rest_framework.response import Response
from .models import Department, UserRecord, ParoiModel, Building, Environment, Job
from .serializers import (
    DepartmentSerializer, UserRecordSerializer, CalculRequestSerializer, ParoiModelSerializer,
    BuildingSerializer, EnvironmentSerializer, JobSerializer, BuildingCalculRequestSerializer,
)
from . import solver
from . import tasks
from . import building_solver


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


class EnvironmentListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/environnements/ — maillages d'environnement (obstacles)."""

    queryset         = Environment.objects.all()
    serializer_class = EnvironmentSerializer


class EnvironmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PUT/PATCH/DELETE /api/environnements/<id>/"""

    queryset         = Environment.objects.all()
    serializer_class = EnvironmentSerializer


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
