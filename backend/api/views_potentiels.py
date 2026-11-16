"""Potentiels d'un bâtiment et d'un parc (bt-potentiels v3, I5 et I5.b ; bt-trois-niveaux v1, I3).

GET, POST /api/batiments/<id>/potentiels/   POST {bouquets?: [id], niveau?: N0|N1|N2} — gestionnaire (D2) ;
                                            N0/N1 : ≤ 4 bouquets, synchrone ; N2 : un seul bouquet, en tâche de fond
GET, POST /api/parcs/<id>/potentiels/       GET ?niveau=N0|N1|N2 (défaut N1) ; POST {bouquets?: [id],
                                            niveau?: N0|N1|N2} — gestionnaire du parc ; file « parc » (D37 levée)
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service
from .ameliorations import bouquets as bq_service
from .ameliorations import potentiels as pot_service

NIVEAU_INCONNU = 'Niveau : N0, N1 ou N2.'


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


class PotentielsBatimentView(APIView):
    def _building(self, request, pk):
        return get_object_or_404(access.visible_buildings(request), pk=pk)

    def get(self, request, pk):
        b = self._building(request, pk)
        return Response(pot_service.building_payload(b, access.can_manage(request, b)))

    def post(self, request, pk):
        b = self._building(request, pk)
        if not access.can_manage(request, b):
            return Response({'detail': 'Seul un gestionnaire du bâtiment peut calculer ses potentiels.'},
                            status=status.HTTP_403_FORBIDDEN)
        data = _body(request)
        niveau = data.get('niveau') or 'N1'
        if niveau not in pot_service.NIVEAUX:
            return Response({'detail': NIVEAU_INCONNU}, status=status.HTTP_400_BAD_REQUEST)
        bq_service.ensure_defaults()
        try:
            chosen = pot_service.resolve_bouquets(bq_service.visible_for_building(b), data.get('bouquets'))
        except pot_service.PotentielError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not chosen:
            return Response({'detail': 'Aucun bouquet à calculer.'}, status=status.HTTP_400_BAD_REQUEST)
        sub, name = access.sub_of(request), access.name_of(request)
        if niveau == 'N2':
            if len(chosen) != 1:
                return Response({'detail': 'Le calcul détaillé (N2) se lance pour un seul bouquet à la fois.'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                pot_service.start_n2(b, chosen[0], sub, name)
            except calc_service.Busy as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
            except calc_service.CalcError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(pot_service.building_payload(b, True), status=status.HTTP_202_ACCEPTED)
        if len(chosen) > pot_service.MAX_N1_PAR_APPEL:
            return Response({'detail': f'{pot_service.MAX_N1_PAR_APPEL} bouquets au plus par calcul.'},
                            status=status.HTTP_400_BAD_REQUEST)
        for bq in chosen:
            pot_service.compute_level(b, bq, niveau, sub, name)
        return Response(pot_service.building_payload(b, True))


class PotentielsParcView(APIView):
    def _portfolio(self, request, pk):
        return get_object_or_404(access.visible_portfolios(request), pk=pk)

    def _buildings(self, request, p):
        return list(access.visible_buildings(request).filter(portfolio=p).order_by('pk'))

    def get(self, request, pk):
        p = self._portfolio(request, pk)
        niveau = request.query_params.get('niveau') or 'N1'
        if niveau not in pot_service.NIVEAUX:
            return Response({'detail': NIVEAU_INCONNU}, status=status.HTTP_400_BAD_REQUEST)
        return Response(pot_service.parc_payload(p, self._buildings(request, p), access.is_portfolio_manager(request, p),
                                                 niveau))

    def post(self, request, pk):
        p = self._portfolio(request, pk)
        if not access.is_portfolio_manager(request, p):
            return Response({'detail': 'Seul un gestionnaire du parc peut calculer les potentiels du parc.'},
                            status=status.HTTP_403_FORBIDDEN)
        data = _body(request)
        niveau = data.get('niveau') or 'N1'
        if niveau not in pot_service.NIVEAUX:
            return Response({'detail': NIVEAU_INCONNU}, status=status.HTTP_400_BAD_REQUEST)
        bq_service.ensure_defaults()
        try:
            chosen = pot_service.resolve_bouquets(pot_service.visible_for_portfolio(p), data.get('bouquets'))
        except pot_service.PotentielError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        buildings = self._buildings(request, p)
        if pot_service.enqueue_parc(p, buildings, chosen, niveau):
            from .ameliorations import taches
            taches.enqueue(p.pk, access.sub_of(request))
        return Response(pot_service.parc_payload(p, buildings, True, niveau), status=status.HTTP_202_ACCEPTED)
