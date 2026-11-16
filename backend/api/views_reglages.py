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
    """Fiche d'identité (lot 34), réponse inchangée. Chantier bt-potentiels (lot 38) : le bloc « fiche » des rendus
    en est tiré (`fiche_bloc`), avec la section « potentiels par bouquet » (réponse I5 bâtiment)."""

    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request).select_related('weather_dataset', 'environment'), pk=pk)
        return Response(identite(b))


def identite(b):
    """Agrégat de la fiche d'identité d'un bâtiment déjà autorisé (lecture seule)."""
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
    return {
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
    }


# ── Chantier bt-potentiels (lot 38) : la fiche d'identité comme bloc de rendu (I6.g, bloc « fiche ») ──

FICHE_TITRES = {'identite': 'Identité', 'geometrie': 'Géométrie', 'enveloppe': 'Enveloppe', 'systemes': 'Systèmes',
                'meteo': 'Météo', 'environnement': 'Environnement', 'resultats': 'Résultats',
                'potentiels': 'Potentiels par bouquet', 'ameliorations': 'Améliorations', 'sources': 'Sources'}
_RESULTATS = [('heating_kwh_m2', 'Besoin de chauffage', 'kWh/m²·an'), ('dh_re2020', 'Degrés-heures d’inconfort (RE2020)', '°C·h'),
              ('hours_over_28', 'Heures au-dessus de 28 °C', 'h'), ('final_kwh_m2', 'Énergie finale', 'kWh/m²·an'),
              ('primary_kwh_m2', 'Énergie primaire', 'kWh/m²·an'), ('dpe_label_model', 'Étiquette du modèle', '')]
LEVEL_NAMES = {'N0': 'N0 — mensuel', 'N1': 'N1 — horaire simplifié', 'N2': 'N2 — détaillé par paroi'}


def _el(libelle, valeur, unite='', source=''):
    return {'libelle': str(libelle), 'valeur': '—' if valeur in (None, '') else str(valeur), 'unite': unite or '',
            'source': source or ''}


def _num_txt(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None if v is None else str(v)
    return facts._fr_number(round(float(v), 1) if abs(v) >= 10 else round(float(v), 2))


def _date_txt(d):
    if d is None:
        return ''
    if isinstance(d, str):
        return d[:10]
    return d.strftime('%d/%m/%Y')


def fiche_bloc(request, b, sections):
    """{sections: [{cle, titre, elements: [{libelle, valeur, unite, source}]}], potentiels: <I5 bâtiment>} —
    `potentiels` présent si la section est demandée (None si la route I5 ne répond pas)."""
    f = identite(b)
    out = []
    pot = None
    if 'potentiels' in sections:
        from .rendu import sources as rendu_sources
        pot = rendu_sources.potentiels_batiment(request, b.pk)
    for cle in sections:
        els = []
        if cle == 'identite':
            bt = f['batiment']
            els.append(_el('Nom', bt['name']))
            if bt['address']:
                els.append(_el('Adresse', bt['address']))
            if bt['rnb_id']:
                els.append(_el('Identifiant RNB', bt['rnb_id'], source='RNB'))
            for _k, v in f['identite'].items():
                els.append(_el(v['libelle'], v['texte'], source=v['source']))
            els.append(_el('Approfondissement de la fiche', f['approfondissement']['libelle'],
                           source=f['approfondissement']['detail']))
        elif cle in ('enveloppe', 'systemes'):
            for _k, v in f[cle].items():
                els.append(_el(v['libelle'], v['texte'], source=v['source']))
        elif cle == 'geometrie':
            g = f['geometrie'] or {}
            els.append(_el('Origine de la géométrie', g.get('libelle') or ('BD TOPO (IGN)' if g.get('source') == 'bdtopo'
                                                                           else g.get('source'))))
            if g.get('hauteur_egout_m') is not None:
                els.append(_el('Hauteur à l’égout', _num_txt(g['hauteur_egout_m']), 'm'))
            hz = g.get('horizon_deg')
            if isinstance(hz, dict):
                for o, v in hz.items():
                    els.append(_el(f'Horizon — façade {o}', _num_txt(v), '°', g.get('horizon_source')))
            elif hz is not None:
                els.append(_el('Horizon', _num_txt(hz), '°', g.get('horizon_source')))
            pl = f.get('plancher') or {}
            if pl:
                # Lot 40 : le sol simulé (lot 32) — modèle et température vue par le plancher, note éventuelle.
                txt = pl.get('modele') or pl.get('libelle') or '—'
                if pl.get('t_moyenne_c') is not None:
                    txt += (f" — {_num_txt(pl['t_moyenne_c'])} °C en moyenne ({_num_txt(pl.get('t_min_c'))} à "
                            f"{_num_txt(pl.get('t_max_c'))} °C)")
                els.append(_el('Plancher bas et sol', txt, source='sol simulé sur la météo du calcul'))
                if pl.get('note'):
                    els.append(_el('Note sur le plancher', pl['note']))
        elif cle == 'meteo':
            m = f['meteo']
            els.append(_el('Jeu météo', m['jeu'] if m else 'Aucun', source=(m or {}).get('type', '')))
        elif cle == 'environnement':
            e = f['environnement']
            if e is None:
                els.append(_el('Environnement relevé', 'Aucun'))
            else:
                els += [_el('Source', e['source']), _el('Bâtiments voisins', e['batiments']), _el('Arbres', e['arbres']),
                        _el('Rayon', _num_txt(e['rayon_m']), 'm')]
        elif cle == 'resultats':
            for lvl, r in f['resultats'].items():
                if r is None:
                    continue
                src = f"{LEVEL_NAMES[lvl]}, calculé le {_date_txt(r.get('computed_at'))}" + (
                    '' if r.get('a_jour') is not False else ' (plus à jour)')
                for k, lib, u in _RESULTATS:
                    if r.get(k) is not None:
                        els.append(_el(f'{lib} ({lvl})', _num_txt(r[k]) if u else r[k], u, src))
        elif cle == 'potentiels':
            for e in (pot or {}).get('bouquets') or []:
                if not isinstance(e, dict):
                    continue
                nom = (e.get('bouquet') or {}).get('nom') or 'Bouquet'
                niv = e.get('niveau') or 'N1'
                if e.get('statut') is None and niv != 'N1':
                    continue      # N0/N2 jamais calculés : une seule ligne « non calculé » par bouquet (celle du N1)
                if e.get('statut') != 'fait' or not e.get('indicateurs'):
                    els.append(_el(f'{nom} ({niv})', 'non calculé' if e.get('statut') is None else e.get('statut'),
                                   source=e.get('message') or ''))
                    continue
                src = f"potentiel {niv} du {_date_txt(e.get('calcule_le'))}" + ('' if e.get('a_jour') else ' (plus à jour)')
                ind, pct = e.get('indicateurs') or {}, e.get('ecarts_pct') or {}
                for k, lib, u in _RESULTATS[:2]:
                    if ind.get(k) is None:
                        continue
                    v = _num_txt(ind[k])
                    if isinstance(pct.get(k), (int, float)):
                        v += f" ({'+' if pct[k] > 0 else '−' if pct[k] < 0 else ''}{facts._fr_number(round(abs(pct[k]) * 100, 0))} %)"
                    els.append(_el(f'{nom} — {lib} ({niv})', v, u, src))
                acts = e.get('actions') or []
                n_ok = sum(1 for a in acts if isinstance(a, dict) and a.get('appliquee'))
                els.append(_el(f'{nom} — actions appliquées ({niv})', f'{n_ok} sur {len(acts)}', source=src))
        elif cle == 'ameliorations':
            for a in f['ameliorations']:
                d = a.get('dernier') or {}
                els.append(_el(a['name'], _num_txt(d.get('heating_kwh_m2')) if d else 'non calculée',
                               'kWh/m²·an' if d.get('heating_kwh_m2') is not None else '',
                               ', '.join(a['bouquets']) if a.get('bouquets') else ''))
        elif cle == 'sources':
            for s in f['sources']:
                els.append(_el(s['libelle'], 'sautée' if s['saute'] else ('faite' if s['ok'] else 'en échec'),
                               source=s['source']))
        out.append({'cle': cle, 'titre': FICHE_TITRES[cle], 'elements': els})
    res = {'sections': out}
    if 'potentiels' in sections:
        res['potentiels'] = pot
    return res
