"""Routes I2 (secteur C) — attente : 501 fixe."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class _AVenir(APIView):
    def get(self, request, *a, **kw):
        return Response({'detail': 'Pas encore disponible.'}, status=status.HTTP_501_NOT_IMPLEMENTED)

    post = get


class EtatBatimentView(_AVenir):
    pass


class RecalculerView(_AVenir):
    pass


class EtatParcView(_AVenir):
    pass
