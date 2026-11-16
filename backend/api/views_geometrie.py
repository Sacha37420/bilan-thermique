"""Géométrie corrigée (Lot 15, refonte). Routes nouvelles uniquement.

GET    /api/batiments/<id>/geometrie/     emprise (automatique et corrigée), hauteurs, toiture, écarts, voisins, origine
POST   /api/batiments/<id>/geometrie/     {footprint_xy, eave_height_m, levels, roof_type, roof_pitch_deg, regenerate_envelope?}
DELETE /api/batiments/<id>/geometrie/     revenir à la géométrie automatique
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, facts, geometry_service as gs


def payload(b):
    vals = facts.effective_values(b)
    e = gs.active_edit(b)
    auto = (b.pipeline or {}).get('geometry') or {}
    return {
        'auto_footprint_xy': gs.auto_footprint(b), 'auto': gs.without_profile(auto),
        # Lot 30 : d'où vient la géométrie automatique (BD TOPO seule, ou croisée avec le LiDAR HD) et écarts.
        'origine': (b.pipeline or {}).get('geometrie') or {'source': 'bdtopo'},
        'edit': ({'footprint_xy': e.footprint_xy, 'eave_height_m': e.eave_height_m, 'levels': e.levels,
                  'roof_type': e.roof_type, 'roof_pitch_deg': e.roof_pitch_deg, 'summary': e.summary} if e else None),
        'diff': gs.diff(b),
        'current': {'eave_height_m': vals.get('identite.hauteur_m'), 'levels': vals.get('identite.niveaux'),
                    'roof_type': vals.get('toiture.type') or 'terrasse', 'roof_pitch_deg': vals.get('toiture.pente_deg') or 0,
                    'ratios': {o: vals.get(f'baies.ratio_{o}') for o in 'NESO'}},
        'neighbours': [n['xy'] for n in (b.pipeline or {}).get('neighbours') or []][:40],
        'has_manual_parois': any(t.get('paroi_model_id') is not None for t in (b.envelope or {}).get('triangles') or []),
    }


class GeometryView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        return Response(payload(b))

    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_manage(request, b):
            return Response({'detail': 'Action réservée aux gestionnaires de ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            gs.apply_edit(b, request.data, access.sub_of(request), access.name_of(request),
                          regenerate_envelope=request.data.get('regenerate_envelope'))
        except gs.GeometryEditError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        b.refresh_from_db()
        return Response(payload(b), status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_manage(request, b):
            return Response({'detail': 'Action réservée aux gestionnaires de ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        gs.retire_edit(b, access.sub_of(request))
        return Response(payload(b))
