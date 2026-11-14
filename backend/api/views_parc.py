"""Parcs, membres, invitations (Lot 11, refonte). Routes nouvelles uniquement.

GET/POST          /api/parcs/                         mes parcs / créer (le créateur est gestionnaire de parc)
GET/PATCH/DELETE  /api/parcs/<id>/                    détail (+ compteurs, mon rôle) / gestionnaire
POST              /api/parcs/<id>/importer/           {csv} → bâtiments créés (gestionnaire)
POST              /api/parcs/<id>/lancer/             traitement automatique, file `parc` (gestionnaire)
POST              /api/parcs/<id>/rattacher/          {building_id} : un bâtiment commun entre au parc
GET               /api/parcs/<id>/tableau/            lignes du tableau de parc (visibles par l'appelant)
GET               /api/parcs/<id>/export.csv          export CSV (`;`)
GET               /api/parcs/<id>/membres/            membres et accès (gestionnaire)
DELETE            /api/parcs/<id>/membres/<mid>/      retirer un membre (gestionnaire)
POST              /api/parcs/<id>/invitations/        {role, building_id?, days?, max_uses?} → code (rendu une fois)
POST              /api/invitations/accepter/          {code}
"""

import hashlib
import secrets
from datetime import timedelta

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, parc, tasks
from .models import Building, BuildingAccess, Invitation, Portfolio, PortfolioMember

INVITE_PREFIX = 'btinv_'


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


class PortfolioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Portfolio
        fields = ['id', 'name', 'description', 'created_at']


def _portfolio(request, pk, manager=False):
    p = get_object_or_404(access.visible_portfolios(request), pk=pk)
    if manager and not access.is_portfolio_manager(request, p):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Action réservée aux gestionnaires du parc.')
    return p


def _detail(request, p):
    return {**PortfolioSerializer(p).data, 'role': access.portfolio_role(p.pk, access.sub_of(request)),
            'counts': parc.counts(p)}


class PortfoliosView(APIView):
    def get(self, request):
        return Response({'results': [_detail(request, p) for p in access.visible_portfolios(request)]})

    def post(self, request):
        sub = access.sub_of(request)
        if not sub:
            return Response({'detail': 'Jeton sans identifiant (sub).'}, status=status.HTTP_403_FORBIDDEN)
        ser = PortfolioSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.save(owner_sub=sub)
        PortfolioMember.objects.create(portfolio=p, sub=sub, display_name=access.name_of(request),
                                       role=PortfolioMember.GESTIONNAIRE_PARC, added_by=sub)
        return Response(_detail(request, p), status=status.HTTP_201_CREATED)


class PortfolioDetailView(APIView):
    def get(self, request, pk):
        return Response(_detail(request, _portfolio(request, pk)))

    def patch(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        ser = PortfolioSerializer(p, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(_detail(request, p))

    def delete(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        if Building.objects.filter(portfolio=p).exists() and request.query_params.get('confirmer') != '1':
            return Response({'detail': 'Ce parc contient des bâtiments : ajoutez ?confirmer=1 pour tout supprimer.'},
                            status=status.HTTP_409_CONFLICT)
        p.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ImportView(APIView):
    def post(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        text = request.data.get('csv')
        if not isinstance(text, str) or not text.strip():
            return Response({'detail': 'Contenu CSV attendu (champ « csv »).'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows, errors = parc.parse_csv(text)
        except parc.ImportError_ as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        created = parc.import_rows(p, rows, access.sub_of(request), access.name_of(request))
        launched = False
        if created and request.data.get('lancer', True):
            tasks.process_parc_next.apply_async(args=[p.pk, access.sub_of(request)], queue='parc')
            launched = True
        return Response({'created': len(created), 'errors': errors[:200], 'launched': launched,
                         'counts': parc.counts(p)}, status=status.HTTP_201_CREATED)


class LaunchView(APIView):
    def post(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        qs = Building.objects.filter(portfolio=p)
        if request.data.get('tout'):
            qs.exclude(pipeline_status='en_cours').update(pipeline_status='en_attente')
        else:
            qs.filter(pipeline_status__in=['', 'incomplet']).update(pipeline_status='en_attente')
        tasks.process_parc_next.apply_async(args=[p.pk, access.sub_of(request)], queue='parc')
        return Response({'counts': parc.counts(p)}, status=status.HTTP_202_ACCEPTED)


class AttachView(APIView):
    def post(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        b = get_object_or_404(Building.objects.filter(portfolio__isnull=True), pk=request.data.get('building_id'))
        if Building.objects.filter(portfolio=p, name=b.name).exists():
            return Response({'detail': 'Un bâtiment de ce parc porte déjà ce nom.'}, status=status.HTTP_409_CONFLICT)
        b.portfolio = p
        b.save(update_fields=['portfolio'])
        return Response({'building_id': b.pk, 'counts': parc.counts(p)})


class TableView(APIView):
    def get(self, request, pk):
        p = _portfolio(request, pk)
        bs = access.visible_buildings(request).filter(portfolio=p).order_by('name')
        return Response({'portfolio': _detail(request, p), 'rows': parc.table(p, bs)})


class ExportView(APIView):
    def get(self, request, pk):
        p = _portfolio(request, pk)
        bs = access.visible_buildings(request).filter(portfolio=p).order_by('name')
        resp = HttpResponse(parc.to_csv(parc.table(p, bs)), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="parc-{p.pk}.csv"'
        return resp


class MembersView(APIView):
    def get(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        me = access.sub_of(request)
        members = [{'id': m.pk, 'name': m.display_name or '(sans nom)', 'role': m.role, 'me': m.sub == me}
                   for m in p.members.order_by('created_at')]
        accesses = [{'id': a.pk, 'name': a.display_name or '(sans nom)', 'role': a.role, 'building_id': a.building_id,
                     'building': a.building.name}
                    for a in BuildingAccess.objects.filter(building__portfolio=p).select_related('building')]
        invites = [{'id': i.pk, 'prefix': i.prefix, 'role': i.role, 'building_id': i.building_id,
                    'expires_at': i.expires_at, 'uses': i.uses, 'max_uses': i.max_uses,
                    'active': i.revoked_at is None and i.expires_at > timezone.now() and i.uses < i.max_uses}
                   for i in p.invitations.order_by('-created_at')[:50]]
        return Response({'members': members, 'accesses': accesses, 'invitations': invites})


class MemberDetailView(APIView):
    def delete(self, request, pk, mid):
        p = _portfolio(request, pk, manager=True)
        kind = request.query_params.get('type', 'membre')
        if kind == 'acces':
            get_object_or_404(BuildingAccess, pk=mid, building__portfolio=p).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        if kind == 'invitation':
            inv = get_object_or_404(Invitation, pk=mid, portfolio=p)
            inv.revoked_at = timezone.now()
            inv.save(update_fields=['revoked_at'])
            return Response(status=status.HTTP_204_NO_CONTENT)
        m = get_object_or_404(PortfolioMember, pk=mid, portfolio=p)
        if m.role == PortfolioMember.GESTIONNAIRE_PARC and \
                p.members.filter(role=PortfolioMember.GESTIONNAIRE_PARC).count() == 1:
            return Response({'detail': 'Le parc garde au moins un gestionnaire.'}, status=status.HTTP_409_CONFLICT)
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=['gestionnaire_parc', 'gestionnaire_bat', 'lecteur', 'gestionnaire', 'occupant'])
    building_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    days = serializers.IntegerField(min_value=1, max_value=30, default=7)
    max_uses = serializers.IntegerField(min_value=1, max_value=50, default=1)

    def validate(self, d):
        if d['role'] in ('gestionnaire', 'occupant') and not d['building_id']:
            raise serializers.ValidationError('Ce rôle se donne sur un bâtiment (building_id).')
        if d['role'] not in ('gestionnaire', 'occupant') and d['building_id']:
            raise serializers.ValidationError('Ce rôle se donne sur le parc entier.')
        return d


class InvitationsView(APIView):
    def post(self, request, pk):
        p = _portfolio(request, pk, manager=True)
        ser = InviteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        building = None
        if d['building_id']:
            building = get_object_or_404(Building, pk=d['building_id'], portfolio=p)
        code = INVITE_PREFIX + secrets.token_urlsafe(24)
        Invitation.objects.create(portfolio=p, building=building, role=d['role'], token_hash=_hash(code),
                                  prefix=code[:12], created_by=access.sub_of(request),
                                  expires_at=timezone.now() + timedelta(days=d['days']), max_uses=d['max_uses'])
        return Response({'code': code, 'role': d['role'], 'building_id': d['building_id'],
                         'expires_in_days': d['days'], 'max_uses': d['max_uses']}, status=status.HTTP_201_CREATED)


class AcceptInvitationView(APIView):
    def post(self, request):
        sub = access.sub_of(request)
        code = str(request.data.get('code') or '').strip()
        inv = Invitation.objects.filter(token_hash=_hash(code)).select_related('portfolio', 'building').first() \
            if code.startswith(INVITE_PREFIX) else None
        if inv is None or inv.revoked_at or inv.expires_at <= timezone.now() or inv.uses >= inv.max_uses or not sub:
            return Response({'detail': 'Invitation invalide, expirée ou déjà utilisée.'}, status=status.HTTP_400_BAD_REQUEST)
        name = access.name_of(request)
        if inv.building_id:
            BuildingAccess.objects.update_or_create(building=inv.building, sub=sub,
                                                    defaults={'role': inv.role, 'display_name': name, 'added_by': inv.created_by})
        else:
            m = PortfolioMember.objects.filter(portfolio=inv.portfolio, sub=sub).first()
            if m is None:
                PortfolioMember.objects.create(portfolio=inv.portfolio, sub=sub, display_name=name, role=inv.role,
                                               added_by=inv.created_by)
            elif m.role != PortfolioMember.GESTIONNAIRE_PARC:  # une invitation ne rétrograde personne
                m.role, m.display_name = inv.role, name
                m.save(update_fields=['role', 'display_name'])
        Invitation.objects.filter(pk=inv.pk).update(uses=inv.uses + 1)
        return Response({'portfolio_id': inv.portfolio_id, 'building_id': inv.building_id, 'role': inv.role})
