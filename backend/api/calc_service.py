"""Calculs N0/N1/N2 d'un bâtiment depuis sa fiche (Lot 10, refonte — étapes A7 à A9 ; N2 : Lot 23).

`run_level(building, 'N1')` : fiche effective → paramètres → moteur → `CalcRun` terminé, avec
indicateurs, et la liste des faits utilisés (traçabilité, § 9). Un calcul dont l'empreinte des
entrées (fiche + météo + version du moteur) est identique à un calcul terminé n'est pas refait.
"""

import time
from datetime import date

from django.db import IntegrityError, transaction

from . import calc_runs, facts, sources, weather_source
from .engines import inputs as engine_inputs
from .engines import n0, n1, n2_inputs
from .fingerprint import stable_hash
from .models import CalcRun, Job

ENGINE_VERSION = {'N0': 'n0-2', 'N1': 'n1-2', 'N2': n2_inputs.ENGINE_VERSION}  # -2 : consommations (Lot 16)


class CalcError(ValueError):
    pass


class Busy(CalcError):
    pass


def tmy_weather(lat, lon):
    """Année type PVGIS du site (repli : dernière année réelle Open-Meteo), en cache par 0,05°."""
    key = f'{round(lat * 20) / 20:.2f},{round(lon * 20) / 20:.2f}'

    def fetch():
        y = date.today().year - 1
        series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
            lat, lon, f'{y}-01-01', f'{y}-12-31')
        return {'series': series, 'source': source, 'warning': warning, 'n_missing': n_missing}
    try:
        return sources.cached('pvgis_tmy', key, fetch)
    except weather_source.WeatherSourceError as exc:
        raise CalcError(f'Météo indisponible : {exc}') from exc


def resolve_weather(building, spec=None):
    """(série, descripteur) selon `spec` : {'points': [...]} fournis, sinon l'année type du site.
    Étendu au Lot 13 (stations, TRACC, périodes, imports)."""
    spec = spec or {}
    if spec.get('points'):
        pts = spec['points']
        return pts, {'source': spec.get('label') or 'série fournie', 'n_hours': len(pts), 'hash': stable_hash(pts)[:16]}
    if not spec.get('dataset_id') and building.weather_dataset_id:
        spec = {**spec, 'dataset_id': building.weather_dataset_id}
    if spec.get('dataset_id'):
        from . import weather_service
        return weather_service.series_for(spec, building)
    if building.georef_lat is None:
        raise CalcError('Bâtiment sans position : impossible de choisir une météo.')
    w = tmy_weather(building.georef_lat, building.georef_lon)
    desc = {'source': w['source'], 'warning': w.get('warning'), 'n_hours': len(w['series']),
            'lat': round(building.georef_lat, 3), 'lon': round(building.georef_lon, 3),
            'hash': stable_hash(w['series'])[:16]}
    return w['series'], desc


def params_for(building, scenario=None, values=None):
    vals = values if values is not None else facts.effective_values(building, scenario)
    from . import geometry_service
    p = engine_inputs.from_fiche(vals, geometry_service.effective_summary(building, scenario))
    from . import shading_service
    shading_service.apply(p, building, scenario)
    return p, vals


def n2_context_hash(building, scenario):
    """Ce dont dépend le N2 en plus de la fiche : géométrie, voisins, environnement, enveloppe experte."""
    from . import geometry_service
    from .models import ParoiModel
    edit = geometry_service.active_edit(building, scenario) or (
        geometry_service.active_edit(building) if scenario is not None else None)
    env = building.environment
    pipeline = building.pipeline or {}
    parts = [[edit.pk, edit.created_at.isoformat()] if edit else None, pipeline.get('footprint_xy'),
             pipeline.get('neighbours'), building.georef_north_offset_deg,
             [env.pk, env.updated_at.isoformat()] if env else None]
    if n2_inputs._expert_envelope(building) or not (edit or pipeline.get('footprint_xy')):
        ids = sorted({t.get('paroi_model_id') for t in (building.envelope or {}).get('triangles') or []
                      if isinstance(t.get('paroi_model_id'), int)})
        parts += [building.envelope, [[p.pk, p.layers, p.frame_u, p.frame_fraction]
                                      for p in ParoiModel.objects.filter(pk__in=ids).order_by('pk')]]
    return stable_hash(*parts)


def inputs_hash_for(building, level, scenario, weather_hash, values=None):
    from . import geometry_service, shading_service
    vals = values if values is not None else facts.effective_values(building, scenario)
    extra = n2_context_hash(building, scenario) if level == 'N2' else None
    return stable_hash(level, ENGINE_VERSION[level], stable_hash(sorted(vals.items())), weather_hash,
                       shading_service.signature(building, scenario), geometry_service.effective_summary(building, scenario),
                       *([extra] if extra else []))


def from_fiche(run):
    """Vrai pour un calcul lancé depuis la fiche (tous les N0/N1 ; un N2 du Lot 23). Faux pour un N2 de
    la page Calcul 3D, dont les réglages viennent du formulaire et dont on ne sait pas dire s'il est à jour."""
    return run.level != 'N2' or (run.settings or {}).get('engine') in n2_inputs.FICHE_ENGINES


def is_up_to_date(run):
    if not from_fiche(run):
        return None
    return run.inputs_hash == inputs_hash_for(run.building, run.level, run.scenario, (run.weather or {}).get('hash'))


def facts_used(eff):
    return {k: {'value': f.value, 'method': f.method, 'source': f.source, 'confidence': f.confidence}
            for k, f in eff.items()}


def params_snapshot(p):
    """Paramètres N0/N1 lisibles (entrées figées d'un calcul)."""
    return {'surface_m2': round(p.a_floor, 1), 'volume_m3': round(p.volume, 1),
            'murs': {o: {k: round(v, 3) for k, v in w.items()} for o, w in p.walls.items()},
            'toiture': {'surface_m2': round(p.roof_area, 1), 'u': p.u_roof},
            'plancher': {'surface_m2': round(p.floor_area, 1), 'u': p.u_floor},
            'baies': {'g': p.g_window, 'part_vitree': p.frame_factor}, 'absorptance': p.absorptance,
            'inertie': {'c_m_j_k_m2': p.c_m, 'a_m': p.a_m_factor}, 'renouvellement_vol_h': round(p.n_vent, 3),
            'apports_w_m2': p.gains_w_m2, 'consigne_chauffage_c': p.t_heat, 'consigne_froid_c': p.t_cool,
            'occupation': 'logement (constante)' if p.residential else 'tertiaire : 8 h–19 h, lundi–vendredi',
            'horizon_deg': p.horizon, 'protections': [{k: d[k] for k in ('kind', 'orientation', 'mode')} for d in p.devices]}


def run_level(building, level, *, scenario=None, weather_spec=None, owner_sub='', force=False, job=None,
              uncertainty=True):
    if level not in ('N0', 'N1'):
        raise CalcError('Niveau inconnu (N0 ou N1) — le N2 passe par start_n2.')
    if not weather_spec and scenario is not None and (scenario.weather or {}).get('dataset_id'):
        weather_spec = {'dataset_id': scenario.weather['dataset_id']}  # météo propre à la variante (Lot 18)
    weather, wdesc = resolve_weather(building, weather_spec)
    p, vals = params_for(building, scenario)
    inputs_hash = inputs_hash_for(building, level, scenario, wdesc.get('hash'), vals)
    if not force:
        done = CalcRun.objects.filter(building=building, level=level, scenario=scenario, status=Job.DONE,
                                      inputs_hash=inputs_hash).first()
        if done is not None:
            return done, False
    eff = facts.effective(building, scenario)
    settings = {'engine': ENGINE_VERSION[level], 'facts': facts_used(eff),
                'confidence': facts.scores(eff)[0], 'completeness': facts.scores(eff)[1]}
    from . import geometry_service
    inputs = {'engine': ENGINE_VERSION[level], 'facts': settings['facts'], 'params': params_snapshot(p),
              'geometry': geometry_service.effective_summary(building, scenario)}
    settings['uncertainty'] = bool(uncertainty and level == 'N1')
    try:
        with transaction.atomic():
            run = CalcRun.objects.create(building=building, level=level, scenario=scenario, status=Job.RUNNING,
                                         owner_sub=owner_sub or '', inputs_hash=inputs_hash, weather=wdesc,
                                         settings=settings, inputs=inputs, job=job)
    except IntegrityError as exc:
        raise Busy('Un calcul de ce niveau est déjà en cours pour ce bâtiment.') from exc
    t0 = time.monotonic()
    try:
        if level == 'N1' and uncertainty:
            res = run_n1_with_range(building, scenario, eff, weather, p)
        else:
            res = n1.run_one(p, weather) if level == 'N1' else n0.run(p, weather)
    except Exception as exc:
        from . import net
        run.status, run.message = Job.ERROR, net.public_message(exc)[:500]
        run.save(update_fields=['status', 'message'])
        raise CalcError(run.message) from exc
    # Lot 16 : du besoin aux consommations (énergie finale, primaire, CO₂, coût, étiquette du modèle).
    from .engines import systems
    t_ext = [w['t_ext'] for w in weather] if res.get('phi_hc_w') else None
    res['energy'] = systems.evaluate(vals, res['heating_kwh'], res.get('cooling_kwh') or 0.0, p.a_floor, p.volume,
                                     res.get('phi_hc_w'), t_ext)
    res['inputs_summary'] = {'h_tr_w_k': round(p.h_tr_w + p.h_tr_op_air + p.h_floor, 1),
                             'h_ve_w_k': round(p.h_ve, 1), 'a_floor_m2': round(p.a_floor, 1),
                             'n_vent_vol_h': round(p.n_vent, 3)}
    run.status = Job.DONE
    run.results = res
    run.indicators = calc_runs.indicators_from(res, p.a_floor, level)
    e = res['energy']
    run.indicators.update({k: e[k] for k in ('final_kwh_m2', 'primary_kwh_m2', 'co2_kg_m2', 'cost_eur', 'final_kwh',
                                             'primary_kwh', 'co2_kg') if k in e})
    run.indicators['dpe_label_model'] = e['dpe_label_model']
    if 'dpe_gap_pct' in e:
        run.indicators['dpe_gap_pct'] = e['dpe_gap_pct']
    run.indicators['confidence'] = settings['confidence']
    run.indicators['completeness'] = settings['completeness']
    run.duration_s = round(time.monotonic() - t0, 3)
    run.save(update_fields=['status', 'results', 'indicators', 'duration_s'])
    return run, True


def latest(building, level, scenario=None):
    return (CalcRun.objects.filter(building=building, level=level, scenario=scenario, status=Job.DONE)
            .order_by('-created_at').first())


def current_or_latest(building, level, scenario=None):
    """Le calcul terminé qui correspond à la fiche actuelle s'il existe (revenir à l'automatique
    rend le résultat précédent sans recalcul), sinon le plus récent."""
    last = latest(building, level, scenario)
    if last is None or not from_fiche(last) or is_up_to_date(last):
        return last
    wh = (last.weather or {}).get('hash')
    h = inputs_hash_for(building, level, scenario, wh)
    match = (CalcRun.objects.filter(building=building, level=level, scenario=scenario, status=Job.DONE, inputs_hash=h)
             .order_by('-created_at').first())
    return match or last


def run_n1_with_range(building, scenario, eff, weather, nominal_params):
    """N1 nominal + fourchette P10–P90 par tirages (api.uncertainty), en un seul passage vectorisé."""
    import copy
    from . import uncertainty
    from .engines import inputs as engine_inputs
    seed = int(stable_hash(sorted((k, f.value) for k, f in eff.items()))[:8], 16)
    from . import geometry_service
    geom = geometry_service.effective_summary(building, scenario)
    samples = uncertainty.sample_values(eff, seed=seed)
    params = [nominal_params]
    from . import shading_service
    for vals in samples[1:]:
        q = engine_inputs.from_fiche(vals, geom)
        shading_service.apply(q, building, scenario)
        params.append(q)
    results = n1.run(params, weather, keep_series=False)
    nominal = n1.run_one(nominal_params, weather)
    heat = [r['heating_kwh'] for r in results]
    pc = uncertainty.percentiles(heat)
    nominal['heating_kwh_p10'] = round(pc[10], 1)
    nominal['heating_kwh_p90'] = round(pc[90], 1)
    nominal['n_samples'] = len(results)
    return nominal


# ── N2 depuis la fiche (Lot 23) ─────────────────────────────────────────────

def start_n2(building, *, scenario=None, weather_spec=None, owner_sub='', force=False):
    """Lance le N2 en tâche de fond (verrou du lab : un calcul lourd à la fois).

    Rend `(run, False)` si un N2 terminé a exactement les mêmes entrées (rien n'est relancé), sinon
    `(run, True)` avec un calcul en attente. Lève `Busy` si un calcul lourd tourne déjà."""
    if not weather_spec and scenario is not None and (scenario.weather or {}).get('dataset_id'):
        weather_spec = {'dataset_id': scenario.weather['dataset_id']}
    _weather, wdesc = resolve_weather(building, weather_spec)
    vals = facts.effective_values(building, scenario)
    inputs_hash = inputs_hash_for(building, 'N2', scenario, wdesc.get('hash'), vals)
    from . import environment_service
    env_to_generate = environment_service.wanted(building, 'n2')
    # Un environnement à générer changera les obstacles : aucun calcul terminé n'a ces entrées-là.
    if not force and not env_to_generate:
        done = CalcRun.objects.filter(building=building, level='N2', scenario=scenario, status=Job.DONE,
                                      inputs_hash=inputs_hash).first()
        if done is not None:
            return done, False
    try:
        built = n2_inputs.build(building, scenario, vals)   # erreurs de géométrie avant de prendre le verrou
    except n2_inputs.N2InputError as exc:
        raise CalcError(str(exc)) from exc
    eff = facts.effective(building, scenario)
    used = facts_used(eff)
    conf, compl = facts.scores(eff)
    settings = {'engine': n2_inputs.ENGINE_VERSION, 'mode': built['snapshot']['mode'], 'facts': used,
                'confidence': conf, 'completeness': compl,
                'weather_spec': {k: v for k, v in (weather_spec or {}).items() if k in ('dataset_id', 'start', 'end')}}
    inputs = {**built['snapshot'], 'facts': used}
    if env_to_generate:
        # Orchestrateur du parc : environnement observé généré au début du calcul (run_n2), qui
        # recalcule alors l'empreinte des entrées.
        settings['environnement'] = {'generer': True}
        inputs['obstacles'] = 'environnement observé, généré avant le calcul (réglage du parc)'
    holder = {}

    def created(job):
        try:
            with transaction.atomic():
                holder['run'] = CalcRun.objects.create(
                    building=building, level='N2', scenario=scenario, status=Job.PENDING, job=job,
                    owner_sub=owner_sub or '', inputs_hash=inputs_hash, weather=wdesc, settings=settings, inputs=inputs)
        except IntegrityError as exc:
            raise Busy('Un N2 est déjà en cours pour ce bâtiment.') from exc

    from . import joblock, tasks
    job = joblock.launch_exclusive(
        'n2_fiche', {'building_id': building.pk, 'scenario_id': scenario.pk if scenario else None},
        lambda j: tasks.run_n2_fiche.delay(j.id), owner_sub=owner_sub, on_created=created)
    if job is None:
        raise Busy('Un calcul lourd est déjà en cours sur le lab — réessayez dans quelques minutes.')
    return holder['run'], True


def run_n2(run, progress_cb=None):
    """Exécute un N2 préparé par `start_n2` (appelé par la tâche Celery). Termine le `CalcRun`."""
    from . import shadow
    from .engines import systems
    from .building_solver import run_building_simulation
    from . import environment_service, pipeline
    from .tasks import _environment_ground
    t0 = time.monotonic()
    building, scenario = run.building, run.scenario
    say = progress_cb or (lambda pct, msg: None)
    base = 0   # avancement déjà consommé par la génération de l'environnement
    if (run.settings or {}).get('environnement', {}).get('generer'):
        if environment_service.wanted(building, 'n2'):
            def env_cb(stage, pct):
                say(1 + int(pct * 0.39), f'Environnement observé (LiDAR HD) : {pipeline.ENV_STAGES.get(stage, "préparation")}…')
            say(1, 'Environnement observé (LiDAR HD × BD TOPO)…')
            report = environment_service.ensure_for_building(building, progress_cb=env_cb)
        else:
            report = {'ok': True, 'deja_relie': True, 'environment_id': building.environment_id}
        run.settings = {**run.settings, 'environnement': {'generer': True, **report}}
        base = 40
    vals = facts.effective_values(building, scenario)
    built = n2_inputs.build(building, scenario, vals)
    weather, wdesc = resolve_weather(building, (run.settings or {}).get('weather_spec') or None)
    if base:
        # L'environnement a changé les obstacles : empreinte et description refaites sur ce qui est calculé.
        run.inputs_hash = inputs_hash_for(building, 'N2', scenario, wdesc.get('hash'), vals)
        run.inputs = {**built['snapshot'], 'facts': (run.inputs or {}).get('facts')}
        if not run.settings['environnement'].get('ok'):
            run.inputs['limites'] = run.inputs.get('limites', []) + [
                f"Environnement observé non généré ({run.settings['environnement'].get('error') or 'cause inconnue'}) : "
                'obstacles réduits aux voisins BD TOPO.']
        run.save(update_fields=['inputs_hash', 'inputs', 'settings'])
    w = n2_inputs.weather_for_n2(weather, building.georef_north_offset_deg or 0.0, built['residential'])
    envelope, env = built['envelope'], built['environment']
    # Réflexion par le sol (Lot AI) : seulement avec un environnement relevé relié, qui porte l'albédo.
    albedo, ground_ids = _environment_ground(building.environment) if building.environment_id else (None, set())
    span = 100 - base
    if built['snapshot']['mode'] == 'experte' and not building.sun_visibility_stale and \
            building.sun_visibility.get('per_triangle'):
        sunvis = building.sun_visibility
    else:
        say(base + 2 * span // 100, 'Ombrage : visibilité du soleil…')
        sunvis = shadow.compute_visibility_grid(envelope, env)
        say(base + 12 * span // 100, 'Ombrage : vue du ciel…')
        sunvis['sky_view_factor'] = shadow.compute_sky_view_factors(envelope, env)
    payload = {**built['payload'], 'weather': w}

    def cb(done, total):
        say(base + (15 + int(done * 84 / total)) * span // 100, f'Résolution heure par heure… {done}/{total}')
    res = run_building_simulation(envelope, built['layers'], sunvis, payload, environment_envelope=env,
                                  progress_cb=cb, paroi_frame_by_id=built['frames'],
                                  ground_albedo=albedo, ground_obj_ids=ground_ids)
    a_floor = built['a_floor']
    phi = res.get('phi_hc_w') or []
    heat = [max(x, 0.0) for x in phi]
    months = n1.MONTH_HOURS[n1.MONTH_HOURS <= len(heat)].tolist()
    if months[-1] != len(heat):
        months.append(len(heat))
    keep = ('hours', 'heating_kwh', 'cooling_kwh', 't_air_mean', 'balance', 'flux_positive_kwh', 'flux_negative_kwh')
    out = {k: res[k] for k in keep}
    out.update({
        'level': 'N2',
        't_air': [round(x, 2) for x in res['t_air'][1:]], 't_op': [round(x, 2) for x in res['t_op'][1:]],
        'phi_hc_w': [round(x, 1) for x in phi],
        'heating_kw_max': round(max(heat) / 1000.0, 2) if heat else 0.0,
        't_op_max': round(max(res['t_op'][1:]), 2) if len(res['t_op']) > 1 else None,
        'monthly_heating_kwh': [round(sum(heat[a:b]) / 1000.0, 1) for a, b in zip(months[:-1], months[1:])],
    })
    t_ext = [p['t_ext'] for p in w]
    out['energy'] = systems.evaluate(vals, out['heating_kwh'], out['cooling_kwh'] or 0.0, a_floor,
                                     a_floor * engine_inputs.CEILING_H, phi, t_ext)
    ind = calc_runs.indicators_from(out, a_floor, 'N2')
    e = out['energy']
    ind.update({k: e[k] for k in ('final_kwh_m2', 'primary_kwh_m2', 'co2_kg_m2', 'cost_eur', 'final_kwh',
                                  'primary_kwh', 'co2_kg') if k in e})
    ind['dpe_label_model'] = e.get('dpe_label_model')
    if 'dpe_gap_pct' in e:
        ind['dpe_gap_pct'] = e['dpe_gap_pct']
    ind['confidence'] = run.settings.get('confidence')
    ind['completeness'] = run.settings.get('completeness')
    run.status, run.results, run.indicators = Job.DONE, out, ind
    run.duration_s = round(time.monotonic() - t0, 2)
    run.save(update_fields=['status', 'results', 'indicators', 'duration_s'])
    return run
