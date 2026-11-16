"""Bloc « tableau » (I6.a / I6.g) : sources `batiments`, `potentiels`, `actions`, `faits`, `calculs`.

Résolu : `{"colonnes": [{"cle", "libelle", "unite"}], "lignes": [[valeur, …]], "total_lignes": n}` — les valeurs
sont des nombres ou du texte brut (l'échappement est fait par le rendu : gabarit Django, liaison Angular).
"""

from .. import facts
from ..models import CalcRun, Job
from . import catalogue

_COMMUNES = {'batiment': ('Bâtiment', ''), 'usage': ('Usage', ''), 'periode_construction': ('Période', ''),
             'annee': ('Année de construction', ''), 'classe_dpe': ('DPE affiché', ''),
             'approfondissement': ('Approfondissement', ''), 'adresse': ('Adresse', '')}
_PROPRES = {
    'batiments': {'niveau_calcul': ('Dernier calcul', ''), 'etiquette_modele': ('Étiquette du modèle', '')},
    'potentiels': {'bouquet': ('Bouquet', ''), 'statut': ('État', ''), 'actions_appliquees': ('Actions appliquées', ''),
                   'actions_ecartees': ('Actions écartées', ''), 'calcule_le': ('Calculé le', '')},
    'actions': {'bouquet': ('Bouquet', ''), 'action': ('Action', ''), 'famille_action': ('Famille', ''),
                'application': ('Appliquée', ''), 'raison': ('Raison', ''), 'effets': ('Effets', '')},
    'faits': {'fait': ('Donnée', ''), 'valeur': ('Valeur', ''), 'unite': ('Unité', ''), 'methode': ('Origine', ''),
              'source': ('Source', '')},
    'calculs': {'niveau': ('Niveau', ''), 'amelioration': ('Amélioration', ''), 'calcule_le': ('Calculé le', ''),
                'duree_s': ('Durée', 's')},
}
_DEFAUT = {
    'batiments': ['batiment', 'usage', 'annee', 'heating_kwh_m2', 'dh_re2020', 'classe_dpe', 'approfondissement'],
    'potentiels': ['batiment', 'bouquet', 'statut', 'heating_kwh_m2', 'ecart.heating_kwh_m2', 'dh_re2020',
                   'ecart.dh_re2020', 'actions_appliquees'],
    'actions': ['batiment', 'bouquet', 'action', 'famille_action', 'application', 'raison'],
    'faits': ['batiment', 'fait', 'valeur', 'methode', 'source'],
    'calculs': ['batiment', 'niveau', 'amelioration', 'calcule_le', 'heating_kwh_m2', 'dh_re2020'],
}
METHODES = {'defaut': 'Valeur par défaut', 'infere': 'Déduite', 'declare': 'Déclarée', 'document': 'Document',
            'mesure': 'Mesure'}


def _mesures_i3(mesures):
    return {k: d for k, d in mesures.items() if d['grain'] == 'batiment' and d['source'] in ('i3', 'fait')}


def colonnes(source, mesures):
    out = {k: v for k, v in _COMMUNES.items()}
    out.update(_PROPRES[source])
    if source in ('batiments', 'potentiels', 'calculs'):
        for k, d in _mesures_i3(mesures).items():
            if source != 'batiments' and d['source'] == 'fait':
                continue
            out[k] = (d['libelle'], d['unite'])
        if source == 'potentiels':
            for k, d in mesures.items():
                if d['grain'] == 'batiment' and d['source'] == 'i3':
                    out['ecart.' + k] = (f"Écart — {d['libelle']}", d['unite'])
    return out


def colonnes_defaut(source):
    return list(_DEFAUT[source])


def _date(d):
    return d.strftime('%d/%m/%Y %H:%M') if d else None


def resoudre(ctx, bloc):
    mes = ctx.mesures()
    cols = colonnes(bloc['source'], mes)
    lignes = []
    src = bloc['source']
    for b in ctx.batiments:
        c = ctx.commun(b)
        base = {**c['dims'], 'annee': c['raw']['_annee'], 'adresse': b.address or None}
        if src == 'batiments':
            run, niv = None, None
            for lv in ('N1', 'N0', 'N2'):
                run = ctx.run(b, 'reel', lv)
                if run is not None:
                    niv = lv
                    break
            ind = (run.indicators or {}) if run else {}
            row = {**base, 'niveau_calcul': niv, 'etiquette_modele': ind.get('dpe_label_model')}
            for k, d in _mesures_i3(mes).items():
                row[k] = c['vals'].get(k) if d['source'] == 'fait' else ind.get(k)
            lignes.append(row)
        elif src == 'potentiels':
            r0 = ctx.run(b, 'reel', 'N1')
            i0 = (r0.indicators or {}) if r0 else {}
            for bq in ctx.bouquets:
                p = ctx.potentiel(b, bq.pk, 'N1')
                ind = (p.run.indicators or {}) if p is not None and p.run_id else {}
                acts = (p.actions or []) if p else []
                row = {**base, 'bouquet': bq.nom, 'statut': 'fait' if p else 'non calculé',
                       'actions_appliquees': sum(1 for a in acts if isinstance(a, dict) and a.get('appliquee')),
                       'actions_ecartees': sum(1 for a in acts if isinstance(a, dict) and not a.get('appliquee')),
                       'calcule_le': _date(p.calcule_le) if p else None}
                for k, d in mes.items():
                    if d['grain'] == 'batiment' and d['source'] == 'i3':
                        row[k] = ind.get(k)
                        a, z = i0.get(k), ind.get(k)
                        row['ecart.' + k] = (round(z - a, 3) if isinstance(a, (int, float)) and isinstance(z, (int, float))
                                             and not isinstance(a, bool) else None)
                lignes.append(row)
        elif src == 'actions':
            fam_lib, actions = ctx.familles()
            for bq in ctx.bouquets:
                p = ctx.potentiel(b, bq.pk, 'N1')
                for a in (p.actions or []) if p else []:
                    if not isinstance(a, dict):
                        continue
                    fam = (actions.get(a.get('cle')) or {}).get('famille')
                    lignes.append({**base, 'bouquet': bq.nom, 'action': a.get('libelle') or a.get('cle'),
                                   'famille_action': fam_lib.get(fam, fam), 'application': 'oui' if a.get('appliquee') else 'non',
                                   'raison': a.get('raison'), 'effets': '; '.join(str(e) for e in a.get('effets') or [])})
        elif src == 'faits':
            for spec in facts.CATALOGUE:
                f = c['eff'].get(spec.key)
                if f is None:
                    continue
                lignes.append({**base, 'fait': spec.label, 'valeur': facts.display_value(spec.key, f.value),
                               'unite': spec.unit or None, 'methode': METHODES.get(f.method, f.method),
                               'source': f.source or None})
        elif src == 'calculs':
            for run in (CalcRun.objects.filter(building=b, status=Job.DONE).select_related('scenario')
                        .order_by('-created_at')[:50]):
                ind = run.indicators or {}
                row = {**base, 'niveau': run.level, 'amelioration': run.scenario.name if run.scenario_id else 'État actuel',
                       'calcule_le': _date(run.created_at), 'duree_s': run.duration_s}
                for k, d in mes.items():
                    if d['grain'] == 'batiment' and d['source'] == 'i3':
                        row[k] = ind.get(k)
                lignes.append(row)
    total = len(lignes)
    if bloc.get('tri'):
        k = bloc['tri']
        present = [r for r in lignes if r.get(k) is not None]
        absent = [r for r in lignes if r.get(k) is None]
        present.sort(key=lambda r: (isinstance(r[k], str), r[k] if not isinstance(r[k], str) else r[k].lower()),
                     reverse=bloc['ordre'] == 'desc')
        lignes = present + absent
    lignes = lignes[:bloc['limite']]
    return {'colonnes': [{'cle': k, 'libelle': cols[k][0], 'unite': cols[k][1]} for k in bloc['colonnes']],
            'lignes': [[_cell(r.get(k)) for k in bloc['colonnes']] for r in lignes], 'total_lignes': total}


def _cell(v):
    if isinstance(v, bool):
        return 'oui' if v else 'non'
    if isinstance(v, float):
        return round(v, 3)
    if v is None or isinstance(v, (int, str)):
        return v
    return str(v)


__all__ = ['colonnes', 'colonnes_defaut', 'resoudre', 'catalogue']
