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
# Lot 34 : nom de chaque étape (un tableau de sources lu par d'autres ne parle pas en codes), ordre de la chaîne.
STEP_NAMES = {'A1': 'Identification', 'A2': 'Fiche nationale et DPE existant', 'A3': 'Géométrie et voisins',
              'A5': 'Compositions par archétype', 'A6': 'Environnement observé et géométrie croisée',
              'A7': 'Météo par défaut', 'A8': 'Calculs automatiques'}
ENV_SOURCES = {'lidar': 'LiDAR HD × BD TOPO (IGN)', 'mns_lidar': 'MNS LiDAR HD × BD TOPO (IGN)',
               'mns_correl': 'MNS Correl × BD TOPO (IGN)', 'legacy': 'BD TOPO / OpenStreetMap (repli, sans relevé)'}


def _step_source(key, pipe, env):
    if key == 'A6' and env is not None:
        return ENV_SOURCES.get((env.generation or {}).get('source'), STEP_SOURCES['A6'])
    if key == 'A3' and (pipe.get('geometrie') or {}).get('source', 'bdtopo') != 'bdtopo':
        return f"BD TOPO, croisée : {pipe['geometrie'].get('libelle') or STEP_SOURCES['A6']}"
    return STEP_SOURCES.get(key, key)


def _block(eff, blocks):
    out = {}
    for spec in facts.CATALOGUE:
        f = eff.get(spec.key)
        if spec.block in blocks and f is not None:
            out[spec.key] = {'valeur': f.value, 'methode': f.method, 'libelle': spec.label, 'unite': spec.unit,
                             'texte': facts.display_value(spec.key, f.value), 'source': f.source}
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
            resultats[lvl] = {**(run.indicators or {}), 'computed_at': run.created_at, 'a_jour': a_jour,
                              'limites': list((run.inputs or {}).get('limites') or [])}
        meteo = None
        if b.weather_dataset_id:
            meteo = {'id': b.weather_dataset_id, 'jeu': b.weather_dataset.label, 'type': b.weather_dataset.kind}
        elif b.georef_lat is not None:
            meteo = {'id': None, 'jeu': 'Année type du site (PVGIS)', 'type': 'tmy'}
        env = None
        if b.environment_id:
            objs = b.environment.scene_objects or []
            gen = b.environment.generation or {}
            env = {'id': b.environment_id, 'objets': len(objs),
                   'source': ENV_SOURCES.get(gen.get('source'), gen.get('source') or ''),
                   'batiments': sum(1 for o in objs if o.get('kind') == 'building' and o.get('status') == 'active'),
                   'arbres': sum(1 for o in objs if o.get('kind') == 'vegetation' and o.get('status', 'active') == 'active'),
                   'rayon_m': gen.get('radius_m')}
        # Lot 34 : d'où vient la géométrie (BD TOPO seule ou croisée), horizon par façade, sol vu par le plancher.
        from . import geometry_service
        summary = geometry_service.effective_summary(b) or {}
        pipe0 = b.pipeline or {}
        geo = {**(pipe0.get('geometrie') or {'source': 'bdtopo'}),
               'horizon_deg': summary.get('horizon_deg'), 'horizon_source': summary.get('horizon_source') or 'voisins de la chaîne'}
        plancher = None
        for lvl in ('N1', 'N0'):
            if lvl in latest and (latest[lvl][0].results or {}).get('sol'):
                plancher = latest[lvl][0].results['sol']
                break
        ameliorations = []
        for s in b.scenarios.all():
            run = (b.calc_runs.filter(scenario=s, level='N1', status=Job.DONE).order_by('-created_at').first())
            ameliorations.append({'id': s.pk, 'name': s.name, 'bouquets': (s.weather or {}).get('bundles', []),
                                  'dernier': dict(run.indicators or {}) if run else None})
        pipe = b.pipeline or {}
        env_obj = b.environment if b.environment_id else None
        sources = [{'etape': k, 'libelle': STEP_NAMES.get(k, k), 'ok': bool(v.get('ok')),
                    'saute': bool(v.get('skipped')), 'source': _step_source(k, pipe, env_obj), 'date': pipe.get('date')}
                   for k, v in sorted((pipe.get('steps') or {}).items(), key=lambda kv: (kv[0][0], int(kv[0][1:] or 0)
                                                                                        if kv[0][1:].isdigit() else 99))]
        return Response({
            'batiment': {'id': b.pk, 'name': b.name, 'address': b.address, 'rnb_id': b.rnb_id, 'lat': b.georef_lat,
                         'lon': b.georef_lon, 'portfolio': b.portfolio_id},
            'identite': _block(eff, IDENTITY_BLOCKS),
            'enveloppe': _block(eff, ENVELOPE_BLOCKS),
            'systemes': _block(eff, SYSTEM_BLOCKS),
            'meteo': meteo, 'environnement': env, 'resultats': resultats, 'ameliorations': ameliorations,
            'geometrie': geo, 'plancher': plancher,
            'confiance': b.confidence, 'completude': b.completeness,
            'approfondissement': parc.approfondissement(b, eff),
            'sources': sources,
        })
