"""Secteur A (I7) : catalogue des actions, bouquets.

Chantier bt-potentiels — voir chantiers/bt-potentiels/CONTRAT.md. Propriété du secteur indiqué."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

A_VENIR = {'detail': 'Pas encore disponible.'}


class _AVenir(APIView):
    """Route déclarée par la fondation (chantier bt-potentiels, lot 35) : forme du contrat, réponse 501 fixe
    tant que le secteur propriétaire ne l'a pas réalisée."""

    def get(self, request, *a, **kw):
        return Response(A_VENIR, status=status.HTTP_501_NOT_IMPLEMENTED)

    post = put = patch = delete = get


class ActionsView(_AVenir):
    pass


class BouquetsParcView(_AVenir):
    pass


class BouquetDetailView(_AVenir):
    pass
