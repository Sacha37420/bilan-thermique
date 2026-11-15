"""Préparer une simulation (Lot 25, refonte) : ce qui sera calculé, avec quelles entrées, quelle fiabilité.

`prepare(building, level, scenario, can_manage)` ne lance rien et n'écrit rien. Il rend :
- `inputs` : exactement les faits que le calcul enregistrera (`calc_service.facts_used`, même fonction) ;
- `engine` : ce que le moteur en tire (paramètres N0/N1, ou géométrie, parois et limites du N2) ;
- `reliability` : confiance et complétude de la fiche, entrées **décisives** (influence × incertitude, un passage
  N1 vectorisé — `questionnaire.key_influence`), dispersion attendue du besoin et un verdict ;
- `range` : la fourchette P10–P90 du dernier N1 s'il est à jour ;
- `estimate_s`, `blockers`, `can_launch`, `already` (un calcul terminé a déjà exactement ces entrées), `busy`.
"""

import math

from . import calc_service, facts
from .engines import n2_inputs
from .models import CalcRun, Job

VERDICTS = ((0.10, 'fiable', 'Les entrées incertaines bougent peu le résultat.'),
            (0.25, 'à confirmer', 'Quelques entrées incertaines pèsent sur le résultat : les préciser le resserre.'),
            (math.inf, 'indicatif', 'Le résultat dépend fortement d’entrées par défaut ou déduites : un ordre de grandeur.'))
N2_SECONDS = (1.0, 0.085, 3.0)   # par année simulée : constante + par triangle ; + ombrage (mesures du Lot 23)


def _estimate(level, n_hours, n_triangles):
    if level in ('N0', 'N1'):
        return 2
    a, b, shade = N2_SECONDS
    return int(round((a + b * (n_triangles or 100)) * (n_hours or 8760) / 8760 + shade))


def reliability(building, eff, weather):
    from . import questionnaire
    conf, compl = facts.scores(eff)
    out = {'confidence': conf, 'completeness': compl, 'nominal_kwh': None, 'spread_pct': None,
           'verdict': None, 'verdict_text': None, 'decisive': []}
    if weather is None:
        return out
    influence, nominal = questionnaire.key_influence(building, eff, weather)
    if not nominal:
        return out
    # ±1 écart type sur une entrée déplace le besoin de `influence / 2` ; effets supposés indépendants.
    spread = math.sqrt(sum((v / 2.0) ** 2 for v in influence.values())) / nominal
    for limit, label, text in VERDICTS:
        if spread < limit:
            out['verdict'], out['verdict_text'] = label, text
            break
    ranked = sorted(influence.items(), key=lambda kv: -kv[1])[:6]
    out.update({
        'nominal_kwh': round(nominal, 1), 'spread_pct': round(spread * 100, 1),
        'decisive': [{'key': k, 'influence_kwh': round(v, 1), 'share_pct': round(100 * v / nominal, 1),
                      'method': eff[k].method, 'confidence': eff[k].confidence, 'value': eff[k].value}
                     for k, v in ranked if v > 0.005 * nominal],
    })
    return out


def prepare(building, level, scenario=None, can_manage=False):
    eff = facts.effective(building, scenario)
    vals = {k: f.value for k, f in eff.items()}
    blockers = []
    weather = wdesc = None
    spec = {'dataset_id': scenario.weather['dataset_id']} if scenario is not None and \
        (scenario.weather or {}).get('dataset_id') else None
    try:
        weather, wdesc = calc_service.resolve_weather(building, spec)
    except calc_service.CalcError as exc:
        blockers.append(str(exc))
    engine, n_tri = None, None
    if level == 'N2':
        if not can_manage:
            blockers.append('Le N2 est réservé aux gestionnaires du bâtiment.')
        try:
            snap = n2_inputs.build(building, scenario, vals)['snapshot']
            engine = snap
            n_tri = (snap.get('geometry') or {}).get('n_triangles')
        except n2_inputs.N2InputError as exc:
            blockers.append(str(exc))
    else:
        p, _ = calc_service.params_for(building, scenario, vals)
        from . import geometry_service
        engine = {'params': calc_service.params_snapshot(p), 'geometry': geometry_service.effective_summary(building, scenario)}
    already = None
    if wdesc is not None and not blockers:
        h = calc_service.inputs_hash_for(building, level, scenario, wdesc.get('hash'), vals)
        done = CalcRun.objects.filter(building=building, level=level, scenario=scenario, status=Job.DONE,
                                      inputs_hash=h).order_by('-created_at').first()
        already = done.pk if done else None
    rng = None
    n1 = calc_service.current_or_latest(building, 'N1', scenario)
    if n1 is not None and calc_service.is_up_to_date(n1) and n1.indicators.get('heating_kwh_m2_p10') is not None:
        rng = {k: n1.indicators.get(k) for k in ('heating_kwh_m2', 'heating_kwh_m2_p10', 'heating_kwh_m2_p90')}
        rng['run'] = n1.pk
    busy = level == 'N2' and Job.objects.filter(status__in=[Job.PENDING, Job.RUNNING]).exists()
    return {
        'level': level, 'scenario': scenario.pk if scenario else None,
        'weather': wdesc, 'inputs': calc_service.facts_used(eff), 'engine': engine,
        'reliability': reliability(building, eff, weather),
        'range': rng, 'estimate_s': _estimate(level, (wdesc or {}).get('n_hours'), n_tri),
        'blockers': blockers, 'can_launch': not blockers, 'already': already, 'busy': busy,
    }
