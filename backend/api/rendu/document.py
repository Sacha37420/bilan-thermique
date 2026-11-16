"""Document résolu (I6.c, forme I6.g) : la spécification, bloc par bloc, remplacée par ses données.

Un même document sert l'aperçu (Angular) et les trois exports. Un bloc qui ne peut être résolu (calcul absent,
route d'un autre secteur pas encore livrée, données manquantes) devient `{"type", "indisponible": "message fixe"}`
— jamais une erreur du document entier.
"""

import logging

from .. import calc_service
from ..models import CalcRun, Job
from . import figures, spec as spec_mod, tableaux, texte
from .donnees import filtrer

log = logging.getLogger(__name__)

LIBELLES_3D = {'pertes_hiver_kwh_m2': ('Pertes d’hiver par m² de paroi', 'kWh/m²'),
               'pertes_hiver_kwh': ('Pertes d’hiver par triangle', 'kWh'),
               'gains_ete_kwh_m2': ('Gains d’été par m² de paroi', 'kWh/m²'),
               'gains_ete_kwh': ('Gains d’été par triangle', 'kWh'),
               'solaire_ete_kwh_m2': ('Solaire absorbé l’été par m²', 'kWh/m²'),
               't_si_hiver_moy_c': ('Température de surface intérieure moyenne en hiver', '°C'),
               't_si_ete_max_c': ('Température de surface intérieure maximale en été', '°C')}
INDISPONIBLE = 'Ce bloc n’a pas pu être construit.'


def titre_par_defaut(ctx):
    if ctx.portee == 'parc':
        return f'Synthèse — {ctx.portfolio.name}'
    return ctx.batiments[0].name


def resoudre(ctx, spec):
    """`spec` déjà validée (`spec.valider`)."""
    sections = []
    for i, s in enumerate(spec['sections']):
        blocs = []
        for b in s['blocs']:
            try:
                blocs.append(bloc(ctx, b))
            except Exception:  # noqa: BLE001 — un bloc en défaut ne casse pas le document
                log.exception('bloc de rendu %s', b.get('type'))
                blocs.append({'type': b['type'], 'indisponible': INDISPONIBLE})
        sections.append({'titre': s['titre'] or f'Section {i + 1}', 'ancre': f's{i + 1}', 'blocs': blocs})
    return {'titre': spec['titre'] or titre_par_defaut(ctx), 'mise_en_page': dict(spec['mise_en_page']),
            'sections': sections}


def bloc(ctx, b):
    t = b['type']
    if t == 'texte':
        return {'type': t, 'html': texte.vers_html(b['contenu'])}
    if t == 'saut_de_page':
        return {'type': t}
    if t == 'graphe':
        g = spec_mod.valider_graphe(b['graphe'], ctx.mesures())
        return {'type': t, 'figure': figures.construire(ctx, g)}
    if t == 'carte':
        return {'type': t, 'figure': figures.carte(ctx, b['indicateur'], b['series'])}
    if t == 'tableau':
        return {'type': t, **tableaux.resoudre(ctx, b)}
    if t == 'indicateurs':
        return indicateurs(ctx, b)
    if t == 'vue3d':
        return vue3d(ctx, b)
    if t == 'fiche':
        from ..views_reglages import fiche_bloc
        return {'type': t, **fiche_bloc(ctx.request, ctx.batiments[0], b['sections'])}
    return {'type': t, 'indisponible': INDISPONIBLE}


def indicateurs(ctx, b):
    mes = ctx.mesures()
    series = ctx.series(b['series'])
    if not series:
        return {'type': 'indicateurs', 'indisponible': 'Aucun des bouquets demandés n’est visible ici.'}
    rows = ctx.lignes('batiment', b['series'], ['N1'], any(mes[k]['source'] == 'fait' for k in b['cles']))
    if not rows:
        return {'type': 'indicateurs', 'indisponible': 'Aucun calcul N1 terminé pour ces séries.'}
    parc = ctx.portee == 'parc'
    lignes = []
    for k in b['cles']:
        d = mes[k]
        agg = 'somme' if (parc and d['additif']) else 'moyenne'
        vals = [figures.agreger([r.get(k) for r in rows if r['_serie'] == s], agg) for s in series]
        ref = vals[0] if series[0] == 'reel' else None
        ecarts = [None if (ref is None or v is None or i == 0) else round(v - ref, 3) for i, v in enumerate(vals)]
        libelle = d['libelle'] + (' (somme)' if agg == 'somme' and parc else ' (moyenne)' if parc else '')
        lignes.append({'cle': k, 'libelle': libelle, 'unite': d['unite'],
                       'valeurs': [None if v is None else round(v, 3) for v in vals], 'ecarts': ecarts})
    return {'type': 'indicateurs',
            'colonnes': [{'serie': s, 'libelle': ctx.libelle_serie(s)} for s in series], 'lignes': lignes}


def vue3d(ctx, b):
    bat = ctx.batiments[0]
    if b['calcul'] == 'dernier_N2':
        run = calc_service.latest(bat, 'N2')
    else:
        run = CalcRun.objects.filter(pk=b['calcul'], building=bat, level='N2', status=Job.DONE).first()
    if run is None:
        return {'type': 'vue3d', 'indisponible': 'Aucun calcul détaillé (N2) terminé pour ce bâtiment.'}
    from . import sources
    scene = sources.triangles(ctx.request, run.pk)
    if scene is None or b['grandeur'] not in (scene.get('grandeurs') or {}):
        return {'type': 'vue3d', 'indisponible': 'Résultats par paroi indisponibles pour ce calcul.'}
    lib, unite = LIBELLES_3D[b['grandeur']]
    unite = (scene.get('unites') or {}).get(b['grandeur']) or unite
    return {'type': 'vue3d', 'calcul_id': run.pk, 'grandeur': b['grandeur'], 'libelle': lib, 'unite': unite,
            'camera': b['camera'], 'scene': scene}


__all__ = ['resoudre', 'bloc', 'filtrer']
