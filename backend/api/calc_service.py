"""Calculs N0/N1 d'un bâtiment depuis sa fiche (Lot 10, refonte — étapes A7 à A9).

`run_level(building, 'N1')` : fiche effective → paramètres → moteur → `CalcRun` terminé, avec
indicateurs, et la liste des faits utilisés (traçabilité, § 9). Un calcul dont l'empreinte des
entrées (fiche + météo + version du moteur) est identique à un calcul terminé n'est pas refait.
"""

import time
from datetime import date

from django.db import IntegrityError, transaction

from . import calc_runs, facts, sources, weather_source
from .engines import inputs as engine_inputs
from .engines import n0, n1
from .fingerprint import stable_hash
from .models import CalcRun, Job

ENGINE_VERSION = {'N0': 'n0-2', 'N1': 'n1-2'}  # -2 : consommations (Lot 16)


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


def inputs_hash_for(building, level, scenario, weather_hash, values=None):
    from . import geometry_service, shading_service
    vals = values if values is not None else facts.effective_values(building, scenario)
    return stable_hash(level, ENGINE_VERSION[level], stable_hash(sorted(vals.items())), weather_hash,
                       shading_service.signature(building, scenario), geometry_service.effective_summary(building, scenario))


def is_up_to_date(run):
    return run.inputs_hash == inputs_hash_for(run.building, run.level, run.scenario, (run.weather or {}).get('hash'))


def run_level(building, level, *, scenario=None, weather_spec=None, owner_sub='', force=False, job=None,
              uncertainty=True):
    if level not in ('N0', 'N1'):
        raise CalcError('Niveau inconnu (N0 ou N1).')
    weather, wdesc = resolve_weather(building, weather_spec)
    p, vals = params_for(building, scenario)
    inputs_hash = inputs_hash_for(building, level, scenario, wdesc.get('hash'), vals)
    if not force:
        done = CalcRun.objects.filter(building=building, level=level, scenario=scenario, status=Job.DONE,
                                      inputs_hash=inputs_hash).first()
        if done is not None:
            return done, False
    eff = facts.effective(building, scenario)
    settings = {'engine': ENGINE_VERSION[level],
                'facts': {k: {'value': f.value, 'method': f.method, 'source': f.source, 'confidence': f.confidence}
                          for k, f in eff.items()},
                'confidence': facts.scores(eff)[0], 'completeness': facts.scores(eff)[1]}
    settings['uncertainty'] = bool(uncertainty and level == 'N1')
    try:
        with transaction.atomic():
            run = CalcRun.objects.create(building=building, level=level, scenario=scenario, status=Job.RUNNING,
                                         owner_sub=owner_sub or '', inputs_hash=inputs_hash, weather=wdesc,
                                         settings=settings, job=job)
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
    if last is None or level == 'N2' or is_up_to_date(last):
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
