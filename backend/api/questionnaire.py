"""Questionnaire guidé (Lot 12, refonte — § B1).

Des questions en langage courant, chacune traduite en un ou plusieurs **faits déclarés** (par
exemple « 12 cm d'isolant par l'extérieur » → isolation, épaisseur **et** U des murs déduit).
Les questions sont ordonnées par **influence × incertitude** : on calcule, avec le N1 vectorisé,
de combien le besoin varie quand chaque valeur incertaine bouge d'un écart type de sa méthode ;
on pose d'abord les trois à cinq questions qui réduiraient le plus la fourchette.
Les incohérences de la fiche sont signalées.
"""

import numpy as np

from . import archetypes, facts, uncertainty

LAMBDA_INSULATION = 0.035  # W/m·K, laine minérale / polystyrène courants

QUESTIONS = [
    {'id': 'usage', 'label': 'Quel est l’usage du bâtiment ?', 'block': 'identite', 'kind': 'enum',
     'choices': facts.USAGES, 'keys': ['identite.usage', 'usage.apports_w_m2', 'usage.consigne_chauffage_c',
                                       'baies.ratio_N', 'baies.ratio_E', 'baies.ratio_S', 'baies.ratio_O']},
    {'id': 'annee', 'label': 'Année de construction', 'block': 'identite', 'kind': 'int', 'min': 1500, 'max': 2030,
     'keys': ['identite.annee_construction', 'mur.u', 'toiture.u', 'plancher.u', 'baies.uw', 'ventilation.n50',
              'ventilation.debit_vol_h']},
    {'id': 'surface', 'label': 'Surface chauffée (m²)', 'block': 'identite', 'kind': 'number', 'min': 5, 'max': 200000,
     'keys': ['identite.surface_m2']},
    {'id': 'niveaux', 'label': 'Nombre de niveaux chauffés', 'block': 'identite', 'kind': 'int', 'min': 1, 'max': 80,
     'keys': ['identite.niveaux']},
    {'id': 'mitoyennete', 'label': 'Le bâtiment est-il accolé à des voisins chauffés ?', 'block': 'identite', 'kind': 'enum',
     'choices': {'0': 'Non, isolé', '0.25': 'D’un côté', '0.45': 'De deux côtés', '0.6': 'De trois côtés ou plus'},
     'keys': ['identite.mitoyennete']},
    {'id': 'murs', 'label': 'Les murs sont-ils isolés ?', 'block': 'enveloppe', 'kind': 'enum',
     'choices': {'aucune': 'Non', 'iti': 'Oui, par l’intérieur', 'ite': 'Oui, par l’extérieur', 'inconnue': 'Je ne sais pas'},
     'keys': ['mur.u', 'mur.isolation']},
    {'id': 'murs_epaisseur', 'label': 'Épaisseur d’isolant des murs (cm)', 'block': 'enveloppe', 'kind': 'number',
     'min': 0, 'max': 40, 'keys': ['mur.u'], 'depends': {'murs': ['iti', 'ite']}},
    {'id': 'toiture', 'label': 'Épaisseur d’isolant en combles ou toiture (cm, 0 si aucun)', 'block': 'enveloppe',
     'kind': 'number', 'min': 0, 'max': 60, 'keys': ['toiture.u']},
    {'id': 'fenetres', 'label': 'Quelles fenêtres ?', 'block': 'menuiseries', 'kind': 'enum',
     'choices': {'simple': 'Simple vitrage', 'double_ancien': 'Double vitrage d’avant 2000',
                 'double_recent': 'Double vitrage récent', 'triple': 'Triple vitrage'},
     'keys': ['baies.uw', 'baies.g']},
    {'id': 'vitrage', 'label': 'Part de la façade en fenêtres (%)', 'block': 'menuiseries', 'kind': 'number',
     'min': 0, 'max': 80, 'keys': ['baies.ratio_N', 'baies.ratio_E', 'baies.ratio_S', 'baies.ratio_O']},
    {'id': 'ventilation', 'label': 'Comment le bâtiment est-il ventilé ?', 'block': 'ventilation', 'kind': 'enum',
     'choices': facts.VENTILATION, 'keys': ['ventilation.debit_vol_h', 'ventilation.rendement_recup']},
    {'id': 'etancheite', 'label': 'Ressent-on des courants d’air ?', 'block': 'ventilation', 'kind': 'enum',
     'choices': {'beaucoup': 'Beaucoup', 'un_peu': 'Un peu', 'non': 'Non, c’est étanche', 'mesure': 'Test d’étanchéité fait'},
     'keys': ['ventilation.n50']},
    {'id': 'n50', 'label': 'Résultat du test d’étanchéité n50 (vol/h)', 'block': 'ventilation', 'kind': 'number',
     'min': 0.3, 'max': 20, 'keys': ['ventilation.n50'], 'depends': {'etancheite': ['mesure']}},
    {'id': 'consigne', 'label': 'Température de chauffage habituelle (°C)', 'block': 'usage', 'kind': 'number',
     'min': 12, 'max': 25, 'keys': ['usage.consigne_chauffage_c']},
    {'id': 'chauffage', 'label': 'Quel chauffage ?', 'block': 'systemes', 'kind': 'enum', 'choices': facts.GENERATORS,
     'keys': ['systeme.generateur']},
]
BY_ID = {q['id']: q for q in QUESTIONS}

WINDOWS = {'simple': ('simple', 5.0, 0.80), 'double_ancien': ('double', 2.9, 0.68),
           'double_recent': ('double_fe', 1.5, 0.58), 'triple': ('triple', 0.9, 0.50)}
AIRTIGHT = {'beaucoup': 12.0, 'un_peu': 6.0, 'non': 2.5}


class QuestionError(ValueError):
    pass


def _uninsulated_wall_u(values):
    year = values.get('identite.annee_construction')
    u = archetypes.period_for(year)[2]
    return max(u, 1.2) if (year or 1970) < 1975 else max(u, 1.0)


def answers_to_facts(answers, values):
    """{id: valeur} → [(clé, valeur, source)]. `values` = fiche effective actuelle (pour déduire les U)."""
    out = []
    a = dict(answers)

    def num(qid):
        q = BY_ID[qid]
        try:
            v = float(a[qid])
        except (TypeError, ValueError):
            raise QuestionError(f'« {q["label"]} » : nombre attendu.') from None
        if not (q['min'] <= v <= q['max']):
            raise QuestionError(f'« {q["label"]} » : entre {q["min"]} et {q["max"]}.')
        return v

    for qid in a:
        if qid not in BY_ID:
            raise QuestionError('Question inconnue.')
        q = BY_ID[qid]
        if q['kind'] == 'enum' and str(a[qid]) not in q['choices']:
            raise QuestionError(f'« {q["label"]} » : choix inconnu.')
    src = 'questionnaire'
    if 'usage' in a:
        out.append(('identite.usage', a['usage'], src))
    if 'annee' in a:
        out.append(('identite.annee_construction', int(num('annee')), src))
    if 'surface' in a:
        out.append(('identite.surface_m2', num('surface'), src))
    if 'niveaux' in a:
        out.append(('identite.niveaux', int(num('niveaux')), src))
    if 'mitoyennete' in a:
        out.append(('identite.mitoyennete', float(a['mitoyennete']), src))
    merged = {**values, **{k: v for k, v, _ in out}}
    if 'murs' in a:
        out.append(('mur.isolation', a['murs'], src))
        if a['murs'] == 'aucune':
            out.append(('mur.u', round(_uninsulated_wall_u(merged), 2), 'questionnaire : murs non isolés'))
        if a['murs'] in ('iti', 'ite') and 'murs_epaisseur' in a:
            e = num('murs_epaisseur') / 100.0
            u0 = _uninsulated_wall_u(merged)
            u = 1.0 / (1.0 / u0 + e / LAMBDA_INSULATION)
            # Pont thermique de plancher et de refend d'une isolation intérieure : +10 %.
            if a['murs'] == 'iti':
                u *= 1.10
            out += [('mur.isolation_epaisseur_m', round(e, 3), src),
                    ('mur.u', round(u, 3), f'questionnaire : {e * 100:.0f} cm d’isolant ({a["murs"].upper()})')]
    if 'toiture' in a:
        e = num('toiture') / 100.0
        out.append(('toiture.u', round(1.0 / (1.0 / 2.5 + e / 0.04), 3), f'questionnaire : {e * 100:.0f} cm en toiture'))
    if 'fenetres' in a:
        vit, uw, g = WINDOWS[a['fenetres']]
        out += [('baies.vitrage', vit, src), ('baies.uw', uw, f'questionnaire : {a["fenetres"]}'), ('baies.g', g, src)]
    if 'vitrage' in a:
        r = num('vitrage') / 100.0
        out += [(f'baies.ratio_{o}', round(r, 3), src) for o in 'NESO']
    if 'ventilation' in a:
        debit, recup = archetypes.VENTILATION_DEFAULT[a['ventilation']]
        out += [('ventilation.type', a['ventilation'], src), ('ventilation.debit_vol_h', debit, src),
                ('ventilation.rendement_recup', recup, src)]
    if 'etancheite' in a:
        if a['etancheite'] == 'mesure':
            if 'n50' in a:
                out.append(('ventilation.n50', num('n50'), 'test d’étanchéité'))
        else:
            out.append(('ventilation.n50', AIRTIGHT[a['etancheite']], f'questionnaire : courants d’air « {a["etancheite"]} »'))
    if 'consigne' in a:
        out.append(('usage.consigne_chauffage_c', num('consigne'), src))
    if 'chauffage' in a:
        out += [('systeme.generateur', a['chauffage'], src),
                ('systeme.rendement_generation', archetypes.GENERATOR_EFFICIENCY[a['chauffage']], 'questionnaire (valeur type)')]
    return out


# ── Incohérences ────────────────────────────────────────────────────────────

def inconsistencies(values):
    v = values
    out = []

    def add(msg, keys):
        out.append({'message': msg, 'keys': keys})
    year = v.get('identite.annee_construction')
    if v.get('baies.vitrage') in ('triple', 'double_fe') and (v.get('baies.uw') or 0) > 2.5:
        add('Vitrage performant déclaré mais Uw élevé : quelle est l’année des fenêtres ?', ['baies.vitrage', 'baies.uw'])
    if v.get('baies.vitrage') == 'simple' and (v.get('baies.uw') or 9) < 3.5:
        add('Simple vitrage déclaré mais Uw faible.', ['baies.vitrage', 'baies.uw'])
    if v.get('mur.isolation') == 'aucune' and (v.get('mur.u') or 9) < 0.8:
        add('Murs « non isolés » mais U des murs faible.', ['mur.isolation', 'mur.u'])
    if v.get('mur.isolation') in ('iti', 'ite') and (v.get('mur.u') or 0) > 1.5:
        add('Murs isolés déclarés mais U des murs élevé : quelle épaisseur d’isolant ?', ['mur.isolation', 'mur.u'])
    if v.get('ventilation.type') == 'vmc_df' and not v.get('ventilation.rendement_recup'):
        add('Double flux sans rendement de récupération.', ['ventilation.type', 'ventilation.rendement_recup'])
    if year and year < 1950 and v.get('baies.vitrage') == 'triple' and (v.get('mur.u') or 9) > 1.5:
        add('Triple vitrage sur un bâtiment ancien non rénové : les menuiseries ont-elles été changées ?',
            ['baies.vitrage', 'identite.annee_construction'])
    if v.get('identite.usage') == 'maison' and (v.get('identite.logements') or 1) > 2:
        add('Maison individuelle déclarée avec plusieurs logements.', ['identite.usage', 'identite.logements'])
    emp, niv, surf = v.get('identite.emprise_m2'), v.get('identite.niveaux'), v.get('identite.surface_m2')
    if emp and niv and surf and surf > emp * niv * 1.2:
        add('Surface chauffée supérieure à emprise × niveaux.', ['identite.surface_m2', 'identite.niveaux'])
    if year and year >= 2006 and v.get('ventilation.type') == 'naturelle':
        add('Bâtiment récent en ventilation naturelle : est-ce bien le cas ?', ['ventilation.type'])
    return out


# ── Classement par influence × incertitude ──────────────────────────────────

def key_influence(building, eff, weather):
    """{clé: Δbesoin (kWh) pour ±1 écart type de la méthode actuelle} — un seul passage N1 vectorisé."""
    from .engines import inputs as engine_inputs
    from .engines import n1
    from . import shading_service
    base = {k: f.value for k, f in eff.items()}
    from . import geometry_service
    geom = geometry_service.effective_summary(building)
    variants, labels = [base], []
    for k in uncertainty.UNCERTAIN_KEYS:
        f = eff.get(k)
        if f is None or not isinstance(f.value, (int, float)) or isinstance(f.value, bool):
            continue
        spec = facts.BY_KEY[k]
        for sign in (-1, 1):
            v = dict(base)
            if k in uncertainty.ABSOLUTE:
                x = f.value + sign * uncertainty.ABSOLUTE[k][f.method]
            else:
                x = f.value * float(np.exp(sign * uncertainty.REL_SPREAD[f.method]))
            v[k] = min(max(x, spec.min if spec.min is not None else -1e18), spec.max if spec.max is not None else 1e18)
            variants.append(v)
        labels.append(k)
    params = []
    for v in variants:
        p = engine_inputs.from_fiche(v, geom)
        shading_service.apply(p, building)
        params.append(p)
    res = n1.run(params, weather, keep_series=False)
    heat = [r['heating_kwh'] for r in res]
    return {k: abs(heat[2 + 2 * i] - heat[1 + 2 * i]) for i, k in enumerate(labels)}, heat[0]


def ranked_questions(building, weather=None):
    eff = facts.effective(building)
    values = {k: f.value for k, f in eff.items()}
    influence, nominal = ({}, None)
    if weather:
        influence, nominal = key_influence(building, eff, weather)
    out = []
    for q in QUESTIONS:
        if q.get('depends'):
            continue  # sous-question : affichée avec sa question
        keys = q['keys']
        if q['id'] in ('annee', 'usage'):
            # Répondre change l'archétype : seules les valeurs encore « par défaut » en dépendent.
            keys = [k for k in keys if k == q['keys'][0] or getattr(eff.get(k), 'method', 'defaut') == 'defaut']
        score = sum(influence.get(k, 0.0) for k in keys)
        if not influence:  # sans météo : poids du catalogue × incertitude de la méthode
            score = sum(facts.BY_KEY[k].weight * uncertainty.REL_SPREAD.get(getattr(eff.get(k), 'method', 'defaut'), 0.3)
                        for k in q['keys'] if k in facts.BY_KEY)
        methods = [eff[k].method for k in q['keys'] if k in eff]
        out.append({'id': q['id'], 'label': q['label'], 'block': q['block'], 'kind': q['kind'],
                    'choices': q.get('choices'), 'min': q.get('min'), 'max': q.get('max'),
                    'influence_kwh': round(score, 1) if influence else None, 'score': score,
                    'answered': bool(methods) and all(m in facts.USER_METHODS for m in methods),
                    'current': {k: values.get(k) for k in q['keys']},
                    'subquestions': [dict(s, current={k: values.get(k) for k in s['keys']})
                                     for s in QUESTIONS if s.get('depends', {}).get(q['id'])]})
    out.sort(key=lambda x: (x['answered'], -x['score']))
    for i, q in enumerate(out):
        q['priority'] = i < 5 and not q['answered']
        q.pop('score')
    return {'questions': out, 'nominal_kwh': nominal, 'inconsistencies': inconsistencies(values)}


def refresh_archetype(building, values, record):
    """Après une réponse sur l'année ou l'usage : valeurs par défaut de l'archétype recalculées
    (méthode `defaut`, automatiques) — elles ne priment sur rien d'autre que d'anciens défauts."""
    defaults, period = archetypes.defaults(values.get('identite.annee_construction'), values.get('identite.usage'),
                                           values.get('mur.materiau'))
    src = f'archétype {period} · {values.get("identite.usage") or "usage inconnu"}'
    for k, v in defaults.items():
        record(k, v, src)
