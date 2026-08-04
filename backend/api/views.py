from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, status
from rest_framework.response import Response
from .models import Department, UserRecord
from .serializers import DepartmentSerializer, UserRecordSerializer, CalculRequestSerializer
from . import solver


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
