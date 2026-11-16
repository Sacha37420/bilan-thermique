"""Navigation à six onglets (chantier bt-navigation, contrat §§ 2.3 à 2.6). Routes nouvelles uniquement.

GET|PUT /api/parcs/<id>/reglages/<section>/   réglages du parc (orchestration, parametrage, rendu) ; PUT : gestionnaire de parc
GET     /api/calculs/moteurs/                 catalogue des moteurs (statique)
GET     /api/parcs/<id>/documents/            documents des bâtiments du parc visibles par l'appelant
GET     /api/batiments/<id>/identite/         fiche d'identité (agrégat en lecture seule, onglet Rendu)
"""

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, facts, moteurs, parc, reglages
from .models import Job
from .views_ia import _doc
from .views_parc import _portfolio


class ReglagesView(APIView):
    def _section(self, section):
        if section not in reglages.SECTIONS:
            raise Http404
        return section

    def get(self, request, pk, section):
        section = self._section(section)
        p = _portfolio(request, pk)
        return Response(reglages.payload(p, section, access.is_portfolio_manager(request, p)))

    def put(self, request, pk, section):
        section = self._section(section)
        p = _portfolio(request, pk, manager=True)
        data = request.data if isinstance(request.data, dict) else {}
        if 'surcharges' not in data:
            return Response({'detail': 'Surcharges attendues (champ « surcharges »).'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            clean = reglages.validate(section, data['surcharges'])
        except reglages.ReglageError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        setattr(p, section, clean)
        p.save(update_fields=[section])
        return Response(reglages.payload(p, section, True))


class MoteursView(APIView):
    def get(self, request):
        return Response({'results': moteurs.CATALOGUE})


class ParcDocumentsView(APIView):
    def get(self, request, pk):
        p = _portfolio(request, pk)
        out = []
        for b in access.visible_buildings(request).filter(portfolio=p).order_by('name'):
            docs = [d for d in b.documents.all() if access.can_see_personal(request, b, d.owner_sub)]
            pending = sum(1 for pr in b.proposals.filter(decision='en_attente').select_related('run')
                          if access.can_see_personal(request, b, pr.run.owner_sub))
            out.append({'building_id': b.pk, 'building_name': b.name, 'documents': [_doc(d) for d in docs],
                        'propositions_en_attente': pending})
        return Response({'results': out})


IDENTITY_BLOCKS = ('identite',)
ENVELOPE_BLOCKS = ('enveloppe', 'menuiseries')
SYSTEM_BLOCKS = ('ventilation', 'systemes', 'usage')
STEP_SOURCES = {'A1': 'BAN, RNB', 'A2': 'BDNB (CSTB)', 'A3': 'BD TOPO (IGN)', 'A6': 'LiDAR HD × BD TOPO (IGN)', 'A5': 'Archétypes TABULA-FR',
                'A7': 'Météo (Open-Meteo)', 'A8': 'Calculs automatiques N0 / N1'}


def _block(eff, blocks):
    out = {}
    for spec in facts.CATALOGUE:
        f = eff.get(spec.key)
        if spec.block in blocks and f is not None:
            out[spec.key] = {'valeur': f.value, 'methode': f.method, 'libelle': spec.label, 'unite': spec.unit}
    return out


class IdentiteView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request).select_related('weather_dataset', 'environment'), pk=pk)
        eff = facts.effective(b)
        values = {k: f.value for k, f in eff.items()}
        latest = parc.latest_models(b, values)
        resultats = {}
        for lvl in ('N0', 'N1', 'N2'):
            if lvl not in latest:
                resultats[lvl] = None
                continue
            run, a_jour = latest[lvl]
            resultats[lvl] = {**(run.indicators or {}), 'computed_at': run.created_at, 'a_jour': a_jour}
        meteo = None
        if b.weather_dataset_id:
            meteo = {'id': b.weather_dataset_id, 'jeu': b.weather_dataset.label, 'type': b.weather_dataset.kind}
        elif b.georef_lat is not None:
            meteo = {'id': None, 'jeu': 'Année type du site (PVGIS)', 'type': 'tmy'}
        env = None
        if b.environment_id:
            env = {'id': b.environment_id, 'objets': len(b.environment.scene_objects or [])}
        ameliorations = []
        for s in b.scenarios.all():
            run = (b.calc_runs.filter(scenario=s, level='N1', status=Job.DONE).order_by('-created_at').first())
            ameliorations.append({'id': s.pk, 'name': s.name, 'bouquets': (s.weather or {}).get('bundles', []),
                                  'dernier': dict(run.indicators or {}) if run else None})
        pipe = b.pipeline or {}
        sources = [{'etape': k, 'ok': bool(v.get('ok')), 'source': STEP_SOURCES.get(k, k), 'date': pipe.get('date')}
                   for k, v in sorted((pipe.get('steps') or {}).items())]
        return Response({
            'batiment': {'id': b.pk, 'name': b.name, 'address': b.address, 'rnb_id': b.rnb_id, 'lat': b.georef_lat,
                         'lon': b.georef_lon, 'portfolio': b.portfolio_id},
            'identite': _block(eff, IDENTITY_BLOCKS),
            'enveloppe': _block(eff, ENVELOPE_BLOCKS),
            'systemes': _block(eff, SYSTEM_BLOCKS),
            'meteo': meteo, 'environnement': env, 'resultats': resultats, 'ameliorations': ameliorations,
            'confiance': b.confidence, 'completude': b.completeness,
            'approfondissement': parc.approfondissement(b, eff),
            'sources': sources,
        })
