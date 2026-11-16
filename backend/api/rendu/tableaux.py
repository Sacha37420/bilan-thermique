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
    'batiments': {'niveau_calcul': ('Niveau de calcul', ''), 'etiquette_modele': ('Étiquette du modèle', '')},
    'potentiels': {'bouquet': ('Bouquet', ''), 'niveau': ('Niveau', ''), 'statut': ('État', ''), 'actions_appliquees': ('Actions appliquées', ''),
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
    'potentiels': ['batiment', 'bouquet', 'niveau', 'statut', 'heating_kwh_m2', 'ecart.heating_kwh_m2', 'dh_re2020',
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
    """`niveaux` (facultatif) : simulations choisies. Bâtiments : une seule ⇒ lue à ce niveau ; plusieurs ⇒ chaque
    colonne de résultat de calcul devient une colonne par simulation (« Besoin de chauffage (N1) », « … (N2) »).
    Potentiels et calculs : seules les lignes de ces simulations."""
    mes = ctx.mesures()
    cols = dict(colonnes(bloc['source'], mes))
    lignes = []
    src = bloc['source']
    choisis = list(bloc.get('niveaux') or [])
    calculees = {k for k, d in _mesures_i3(mes).items() if d['source'] == 'i3'}
    eclater = src == 'batiments' and len(choisis) > 1
    sortie = list(bloc['colonnes'])
    if eclater:
        sortie = [x for k in bloc['colonnes'] for x in ([f'{k}@{n}' for n in choisis] if k in calculees else [k])]
        for k in calculees:
            for n in choisis:
                cols[f'{k}@{n}'] = (f'{cols[k][0]} ({n})', cols[k][1])
    for b in ctx.batiments:
        c = ctx.commun(b)
        base = {**c['dims'], 'annee': c['raw']['_annee'], 'adresse': b.address or None}
        if src == 'batiments':
            if eclater:
                runs = {n: ctx.run(b, 'reel', n) for n in choisis}
                faits = [n for n in choisis if runs[n] is not None]
                fin = next((runs[n] for n in reversed(choisis) if runs[n] is not None), None)
                row = {**base, 'niveau_calcul': ', '.join(faits) or None,
                       'etiquette_modele': ((fin.indicators or {}) if fin else {}).get('dpe_label_model')}
                for k, d in _mesures_i3(mes).items():
                    if d['source'] == 'fait':
                        row[k] = c['vals'].get(k)
                        continue
                    for n in choisis:
                        row[f'{k}@{n}'] = ((runs[n].indicators or {}) if runs[n] else {}).get(k)
                lignes.append(row)
                continue
            # Simulation imposée, sinon la plus fine disponible et à jour (I5) ; sinon le dernier calcul (N1 d'abord).
            if choisis:
                niv = choisis[0]
            else:
                choix = ctx.niveau_auto(b, 'reel')
                niv = choix[0] if choix else None
            run = ctx.run(b, 'reel', niv) if niv else None
            ind = (run.indicators or {}) if run else {}
            row = {**base, 'niveau_calcul': niv if run else None, 'etiquette_modele': ind.get('dpe_label_model')}
            for k, d in _mesures_i3(mes).items():
                row[k] = c['vals'].get(k) if d['source'] == 'fait' else ind.get(k)
            lignes.append(row)
        elif src == 'potentiels':
            # Une ligne N1 par bouquet (calculé ou non, comme avant), plus une ligne N0 / N2 là où elle est calculée.
            for bq, niv in [(bq, niv) for bq in ctx.bouquets for niv in (choisis or ('N0', 'N1', 'N2'))]:
                p = ctx.potentiel(b, bq.pk, niv)
                if p is None and niv != 'N1' and not choisis:
                    continue
                r0 = ctx.run(b, 'reel', niv)
                i0 = (r0.indicators or {}) if r0 else {}
                ind = (p.run.indicators or {}) if p is not None and p.run_id else {}
                acts = (p.actions or []) if p else []
                row = {**base, 'bouquet': bq.nom, 'niveau': niv, 'statut': 'fait' if p else 'non calculé',
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
                # Les actions d'un bouquet sont celles de son amélioration générée, commune aux trois niveaux.
                p = next((x for x in (ctx.potentiel(b, bq.pk, n) for n in ('N1', 'N2', 'N0')) if x is not None), None)
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
            runs = CalcRun.objects.filter(building=b, status=Job.DONE)
            if choisis:
                runs = runs.filter(level__in=choisis)
            for run in runs.select_related('scenario').order_by('-created_at')[:50]:
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
        if eclater and k in calculees:
            k = f'{k}@{choisis[-1]}'  # trié sur la plus fine des simulations choisies
        present = [r for r in lignes if r.get(k) is not None]
        absent = [r for r in lignes if r.get(k) is None]
        present.sort(key=lambda r: (isinstance(r[k], str), r[k] if not isinstance(r[k], str) else r[k].lower()),
                     reverse=bloc['ordre'] == 'desc')
        lignes = present + absent
    lignes = lignes[:bloc['limite']]
    return {'colonnes': [{'cle': k, 'libelle': cols[k][0], 'unite': cols[k][1]} for k in sortie],
            'lignes': [[_cell(r.get(k)) for k in sortie] for r in lignes], 'total_lignes': total}


def _cell(v):
    if isinstance(v, bool):
        return 'oui' if v else 'non'
    if isinstance(v, float):
        return round(v, 3)
    if v is None or isinstance(v, (int, str)):
        return v
    return str(v)


__all__ = ['colonnes', 'colonnes_defaut', 'resoudre', 'catalogue']
