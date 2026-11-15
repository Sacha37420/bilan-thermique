"""Scénarios et comparateur (Lot 18, refonte — § B7, principe 4).

Une variante (`Scenario`) ne stocke que ses différences : des faits rattachés à elle, des protections
solaires, une géométrie, une météo. Les bouquets de travaux ci-dessous en sont des raccourcis. Le
comparateur calcule (N1 avec fourchette, consommations) la base et chaque variante, sous chaque météo
demandée (actuelle, climat futur…), et rend les points de vigilance non thermiques des travaux choisis.
"""

from . import archetypes, calc_service, facts, questionnaire

BUNDLES = {
    'ite': {'label': 'Isolation des murs par l’extérieur (14 cm)', 'answers': {'murs': 'ite', 'murs_epaisseur': 14},
            'vigilance': ['Modifie l’aspect des façades : déclaration préalable, avis de l’architecte des bâtiments de France '
                          'près d’un monument historique.', 'Traiter les appuis, tableaux et débords de toiture.']},
    'iti': {'label': 'Isolation des murs par l’intérieur (10 cm)', 'answers': {'murs': 'iti', 'murs_epaisseur': 10},
            'vigilance': ['Risque hygrothermique sur les murs anciens (pierre, pan de bois) : pare-vapeur adapté, '
                          'matériaux perspirants.', 'Perte de surface habitable et ponts thermiques aux planchers.']},
    'combles': {'label': 'Isolation des combles ou de la toiture (30 cm)', 'answers': {'toiture': 30},
                'vigilance': ['Maintenir la ventilation de la sous-toiture ; traiter les trappes et conduits.']},
    'fenetres': {'label': 'Menuiseries double vitrage récent', 'answers': {'fenetres': 'double_recent'},
                 'vigilance': ['Des fenêtres plus étanches demandent une ventilation suffisante (entrées d’air).']},
    'triple': {'label': 'Menuiseries triple vitrage', 'answers': {'fenetres': 'triple'},
               'vigilance': ['Moins d’apports solaires l’hiver ; poids des ouvrants.']},
    'vmc_hygro': {'label': 'VMC simple flux hygroréglable', 'answers': {'ventilation': 'vmc_sf_hygro'},
                  'vigilance': ['Entrées d’air hygroréglables à poser dans les menuiseries.']},
    'vmc_df': {'label': 'VMC double flux', 'answers': {'ventilation': 'vmc_df', 'etancheite': 'non'},
               'vigilance': ['N’a d’intérêt qu’avec une bonne étanchéité à l’air ; entretien des filtres ; place des gaines.']},
    'pac': {'label': 'Pompe à chaleur air/eau', 'answers': {'chauffage': 'pac_air_eau'},
            'vigilance': ['Bruit de l’unité extérieure (voisinage) ; dimensionnement après isolation ; émetteurs basse température.']},
    'store_sud': {'label': 'Stores extérieurs automatiques au sud', 'device': {'kind': 'store_ext', 'orientation': 'S',
                                                                                 'control': {'mode': 'auto', 'irradiance_w_m2': 200,
                                                                                             't_ext_min_c': 20}},
                  'vigilance': ['Résistance au vent ; motorisation et pilotage à prévoir.']},
    'casquette_sud': {'label': 'Casquettes au sud (80 cm)', 'device': {'kind': 'casquette', 'orientation': 'S',
                                                                        'params': {'depth_m': 0.8, 'gap_m': 0.2, 'window_height_m': 1.5}},
                      'vigilance': ['Autorisation d’urbanisme (modification de façade).']},
}


class ScenarioError(ValueError):
    pass


def apply_bundles(building, scenario, keys, author_sub='', author_name=''):
    from .models import ShadingDevice
    values = facts.effective_values(building, scenario)
    done = []
    for key in keys:
        bundle = BUNDLES.get(key)
        if bundle is None:
            raise ScenarioError('Bouquet de travaux inconnu.')
        if 'answers' in bundle:
            for k, v, src in questionnaire.answers_to_facts(bundle['answers'], values):
                facts.record(building, k, v, 'declare', source=f'scénario : {bundle["label"]}', author_sub=author_sub,
                             author_name=author_name, scenario=scenario, refresh=False)
                values[k] = v
        if 'device' in bundle:
            d = bundle['device']
            ShadingDevice.objects.create(building=building, scenario=scenario, kind=d['kind'], orientation=d['orientation'],
                                         params=d.get('params', {}), control=d.get('control', {}), label=bundle['label'],
                                         author_sub=author_sub)
        done.append(key)
    meta = dict(scenario.weather or {})
    meta['bundles'] = sorted(set(meta.get('bundles', []) + done))
    scenario.weather = meta
    scenario.save(update_fields=['weather'])
    return done


def vigilance(scenario):
    out = []
    for key in (scenario.weather or {}).get('bundles', []):
        for v in BUNDLES.get(key, {}).get('vigilance', []):
            out.append({'bundle': BUNDLES[key]['label'], 'message': v})
    return out


def differences(building, scenario):
    rows = []
    for f in building.facts.filter(scenario=scenario, retired_at__isnull=True, superseded_by__isnull=True):
        spec = facts.BY_KEY.get(f.key)
        rows.append({'key': f.key, 'label': spec.label if spec else f.key, 'value': f.value, 'unit': f.unit, 'source': f.source})
    for d in building.shading_devices.filter(scenario=scenario):
        rows.append({'key': 'protection', 'label': d.label or d.kind, 'value': d.orientation, 'unit': '', 'source': 'protection solaire'})
    return rows


def compare(building, scenarios, weather_ids, owner_sub=''):
    """Lignes [{scenario, weather, indicators}] pour chaque (variante × météo). `scenarios` contient
    `None` pour la base."""
    rows = []
    weathers = weather_ids or [None]
    for sc in scenarios:
        for wid in weathers:
            spec = {'dataset_id': wid} if wid else None
            try:
                run, _ = calc_service.run_level(building, 'N1', scenario=sc, weather_spec=spec, owner_sub=owner_sub)
            except calc_service.CalcError as exc:
                rows.append({'scenario': sc.pk if sc else None, 'weather': wid, 'error': str(exc)})
                continue
            rows.append({'scenario': sc.pk if sc else None, 'scenario_name': sc.name if sc else 'État actuel',
                         'weather': wid, 'weather_label': run.weather.get('source'), 'run_id': run.pk,
                         'indicators': run.indicators})
    return rows
