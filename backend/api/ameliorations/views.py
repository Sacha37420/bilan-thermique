"""Secteur A (I7) : catalogue des actions, bouquets (contrat bt-potentiels v3, I7.a et I7.c).

GET          /api/ameliorations/actions/[?parc=<id>]
GET, POST    /api/parcs/<id>/bouquets/        POST : {nom, description?, objectif, elements} | {copie_de, nom?}
GET, PATCH, DELETE /api/bouquets/<id>/
"""

from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import access, reglages
from ..models import Bouquet
from . import bouquets as bq_service
from . import catalogue

LAB_READ_ONLY = 'Bouquet du lab : copiez-le dans un parc pour le modifier.'
MAX_PAR_PARC = 50


def _portfolio(request, pk):
    return get_object_or_404(access.visible_portfolios(request), pk=pk)


class ActionsView(APIView):
    def get(self, request):
        pid = request.query_params.get('parc')
        cfg = None
        if pid not in (None, ''):
            p = _portfolio(request, int(pid) if str(pid).isdigit() else -1)
            cfg = reglages.effective(p, 'parametrage')
        return Response(catalogue.catalogue_json(cfg))


class BouquetsParcView(APIView):
    def get(self, request, pk):
        p = _portfolio(request, pk)
        bq_service.ensure_defaults()
        can = access.is_portfolio_manager(request, p)
        qs = Bouquet.objects.filter(Q(portfolio__isnull=True) | Q(portfolio=p)).order_by(F('portfolio_id').asc(nulls_first=True), 'ordre', 'pk')
        return Response({'peut_ecrire': can, 'bouquets': [bq_service.serialize(b, can) for b in qs]})

    def post(self, request, pk):
        p = _portfolio(request, pk)
        if not access.is_portfolio_manager(request, p):
            return Response({'detail': 'Seul un gestionnaire du parc peut créer un bouquet.'}, status=status.HTTP_403_FORBIDDEN)
        if Bouquet.objects.filter(portfolio=p).count() >= MAX_PAR_PARC:
            return Response({'detail': f'{MAX_PAR_PARC} bouquets au plus par parc.'}, status=status.HTTP_400_BAD_REQUEST)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            if 'copie_de' in data:
                cid = data.get('copie_de')
                src = (Bouquet.objects.filter(Q(portfolio__isnull=True) | Q(portfolio=p), pk=cid).first()
                       if isinstance(cid, int) and not isinstance(cid, bool) else None)
                if src is None:
                    return Response({'detail': 'Bouquet à copier introuvable.'}, status=status.HTTP_404_NOT_FOUND)
                fields = bq_service.validate({'nom': data.get('nom') or f'{src.nom} (copie)', 'description': src.description,
                                              'objectif': src.objectif, 'elements': src.elements})
            else:
                fields = bq_service.validate(data)
        except bq_service.BouquetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        fields.setdefault('ordre', Bouquet.objects.filter(portfolio=p).count() + 10)
        b = Bouquet.objects.create(portfolio=p, auteur_sub=access.sub_of(request), **fields)
        return Response(bq_service.serialize(b, True), status=status.HTTP_201_CREATED)


class BouquetDetailView(APIView):
    def _get(self, request, pk):
        b = get_object_or_404(Bouquet, pk=pk)
        if b.portfolio_id is not None and not access.visible_portfolios(request).filter(pk=b.portfolio_id).exists():
            raise Http404
        can = b.portfolio_id is not None and access.is_portfolio_manager(request, b.portfolio)
        return b, can

    def _writable(self, request, pk):
        b, can = self._get(request, pk)
        if b.portfolio_id is None:
            return b, Response({'detail': LAB_READ_ONLY}, status=status.HTTP_403_FORBIDDEN)
        if not can:
            return b, Response({'detail': 'Seul un gestionnaire du parc peut modifier ce bouquet.'},
                               status=status.HTTP_403_FORBIDDEN)
        return b, None

    def get(self, request, pk):
        b, can = self._get(request, pk)
        return Response(bq_service.serialize(b, can))

    def patch(self, request, pk):
        b, refus = self._writable(request, pk)
        if refus:
            return refus
        try:
            fields = bq_service.validate(request.data if isinstance(request.data, dict) else {}, partial=True)
        except bq_service.BouquetError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        for k, v in fields.items():
            setattr(b, k, v)
        b.save()
        return Response(bq_service.serialize(b, True))

    def delete(self, request, pk):
        b, refus = self._writable(request, pk)
        if refus:
            return refus
        b.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
