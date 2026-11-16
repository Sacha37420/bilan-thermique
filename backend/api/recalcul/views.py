"""Routes I2 (chantier bt-trois-niveaux, secteur C).

GET  /api/batiments/<id>/calculs/etat/         qui voit le bâtiment → ETAT
POST /api/batiments/<id>/calculs/recalculer/   gestionnaire du bâtiment → 202 ETAT (replanifie tout de suite ; un
                                               calcul à jour n'est pas refait)
GET  /api/parcs/<id>/calculs/etat/             qui voit le parc → {"resume", "batiments": [{id, statut, calculs_restants}]}
                                               (bâtiments visibles de l'appelant seulement)
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import access
from . import etat, planification

BATIMENT_INTROUVABLE = {'detail': 'Bâtiment introuvable.'}
PARC_INTROUVABLE = {'detail': 'Parc introuvable.'}
RESERVE = {'detail': 'Action réservée aux gestionnaires de ce bâtiment.'}


def _batiment(request, pk):
    return access.visible_buildings(request).filter(pk=pk).first()


class EtatBatimentView(APIView):
    def get(self, request, pk):
        b = _batiment(request, pk)
        if b is None:
            return Response(BATIMENT_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        return Response(etat.etat_batiment(b, peut_recalculer=access.can_manage(request, b)))


class RecalculerView(APIView):
    def post(self, request, pk):
        b = _batiment(request, pk)
        if b is None:
            return Response(BATIMENT_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        if not access.can_manage(request, b):
            return Response(RESERVE, status=status.HTTP_403_FORBIDDEN)
        planification.planifier(b, planification.MANUEL, immediat=True)
        return Response(etat.etat_batiment(b, peut_recalculer=True), status=status.HTTP_202_ACCEPTED)


class EtatParcView(APIView):
    def get(self, request, pk):
        p = access.visible_portfolios(request).filter(pk=pk).first()
        if p is None:
            return Response(PARC_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        resume, lignes = etat.lignes_parc(p, access.visible_buildings(request).filter(portfolio=p))
        return Response({'resume': resume, 'batiments': lignes})
