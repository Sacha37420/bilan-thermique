"""Historique des calculs (Lot 3, refonte) : création, fin, indicateurs.

Un `CalcRun` naît dans la même transaction que le job qui le calcule (verrou de `joblock`), et la
tâche Celery le termine. Les indicateurs sont les mêmes pour les trois niveaux (N0, N1, N2), pour
qu'un tableau de parc puisse les mettre côte à côte.
"""

import time

from django.utils import timezone

from .fingerprint import stable_hash
from .models import CalcRun, Job, ParoiModel

COMFORT_THRESHOLDS_C = (26.0, 28.0)
I3_COMFORT_KEYS = ('dh_re2020', 'heures_inconfort', 't_conf_methode')


def n2_inputs_hash(building, payload):
    tris = building.envelope.get('triangles', [])
    ids = sorted({t.get('paroi_model_id') for t in tris if t.get('paroi_model_id') is not None})
    layers = {p.pk: [p.layers, p.frame_u, p.frame_fraction]
              for p in ParoiModel.objects.filter(pk__in=ids)}
    env = building.environment
    return stable_hash('N2', building.envelope, layers, payload,
                       env.pk if env else None, env.updated_at.isoformat() if env else None,
                       building.sun_visibility_stale)


def weather_descriptor(weather, source=None):
    w = list(weather or [])
    return {'n_hours': len(w), 'source': source or 'fourni par le client',
            'hash': stable_hash(w)[:16],
            't_ext_mean': round(sum(p.get('t_ext', 0.0) for p in w) / len(w), 2) if w else None}


def create_n2_run(building, job, payload, owner_sub):
    settings = {k: v for k, v in payload.items() if k != 'weather'}
    return CalcRun.objects.create(
        building=building, level=CalcRun.N2, job=job, owner_sub=owner_sub or '',
        inputs_hash=n2_inputs_hash(building, payload), settings=settings,
        weather=weather_descriptor(payload.get('weather')),
    )


def comfort_hours(t_series, thresholds=COMFORT_THRESHOLDS_C):
    out = {}
    for th in thresholds:
        out[f'hours_over_{int(th)}'] = int(sum(1 for t in t_series or [] if t is not None and t > th))
    out['degree_hours_over_26'] = round(sum(max(0.0, t - 26.0) for t in t_series or [] if t is not None), 1)
    return out


def indicators_from(result, surface_m2=None, level=CalcRun.N2):
    heating = float(result.get('heating_kwh') or 0.0)
    cooling = float(result.get('cooling_kwh') or 0.0)
    ind = {
        'level': level,
        'hours': result.get('hours'),
        'heating_kwh': round(heating, 1),
        'cooling_kwh': round(cooling, 1),
        't_air_mean': round(result['t_air_mean'], 2) if result.get('t_air_mean') is not None else None,
    }
    if surface_m2:
        ind['surface_m2'] = round(surface_m2, 1)
        ind['heating_kwh_m2'] = round(heating / surface_m2, 1)
        ind['cooling_kwh_m2'] = round(cooling / surface_m2, 1)
        for q in ('p10', 'p90'):
            if result.get(f'heating_kwh_{q}') is not None:
                ind[f'heating_kwh_m2_{q}'] = round(result[f'heating_kwh_{q}'] / surface_m2, 1)
    t = result.get('t_op') or result.get('t_air')
    if t and result.get('hours') and len(t) == result['hours'] + 1:
        t = t[1:]  # N2 : la série commence par l'état initial
    if t:
        ind.update(comfort_hours(t))
    for k in ('heating_kw_max', 'final_energy_kwh', 'primary_energy_kwh', 'co2_kg', 'cost_eur',
              'heating_kwh_p10', 'heating_kwh_p90'):
        if result.get(k) is not None:
            ind[k] = result[k]
    # Lot 36 (I3) : DH RE2020 à seuil adaptatif (`engines.comfort`), DH base 26 gardé sous le nom `dh_26`.
    # N0 (mensuel, sans série horaire) : clés présentes, valeur nulle.
    conf = result.get('confort') or {}
    for k in I3_COMFORT_KEYS:
        ind[k] = conf.get(k) if level != CalcRun.N0 else None
    ind['dh_26'] = ind.get('degree_hours_over_26') if level != CalcRun.N0 else None
    if surface_m2 is None and 'cooling_kwh_m2' not in ind:
        ind['cooling_kwh_m2'] = None
    return ind


def _sig3(x):
    if x is None:
        return None
    x = float(x)
    return float(f'{x:.3g}') if x == x and abs(x) != float('inf') else None


def triangle_results(envelope, layers_by_id, raw):
    """Résultats N2 par triangle au format I4 (`GET /api/calculs/<id>/triangles/`), depuis les cumuls du solveur
    (`building_solver`, sortie `par_triangle`) et la géométrie réellement calculée. 3 chiffres significatifs."""
    from .building_solver import PERIODS
    tris = envelope['triangles']
    out_type = []
    for i, t in enumerate(tris):
        ls = layers_by_id.get(t.get('paroi_model_id')) or []
        if raw['hors_volume'][i]:
            out_type.append('hors_volume')
        elif ls and all((l.get('tau') or 0) > 0 for l in ls):
            out_type.append('vitrage')
        else:
            out_type.append('paroi')
    area = [max(float(t.get('area') or 0.0), 1e-9) for t in tris]

    def per_m2(vals):
        return [_sig3(v / a) for v, a in zip(vals, area)]
    g = {
        'pertes_hiver_kwh_m2': per_m2(raw['loss_hiver_kwh']), 'pertes_hiver_kwh': [_sig3(v) for v in raw['loss_hiver_kwh']],
        'gains_ete_kwh_m2': per_m2(raw['gain_ete_kwh']), 'gains_ete_kwh': [_sig3(v) for v in raw['gain_ete_kwh']],
        'solaire_ete_kwh_m2': per_m2(raw['solaire_ete_kwh']),
        't_si_hiver_moy_c': [_sig3(v) for v in raw['t_si_hiver_moy_c']],
        't_si_ete_max_c': [_sig3(v) for v in raw['t_si_ete_max_c']],
    }
    units = {'pertes_hiver_kwh_m2': 'kWh/m²', 'pertes_hiver_kwh': 'kWh', 'gains_ete_kwh_m2': 'kWh/m²',
             'gains_ete_kwh': 'kWh', 'solaire_ete_kwh_m2': 'kWh/m²', 't_si_hiver_moy_c': '°C', 't_si_ete_max_c': '°C'}
    return {'repere': 'batiment', 'vertices': [[_sig_coord(c) for c in v] for v in envelope['vertices']],
            'triangles': [list(t['v']) for t in tris], 'groupes': [t.get('group') or '' for t in tris],
            'type': out_type, 'periodes': {k: list(v) for k, v in PERIODS.items()}, 'grandeurs': g, 'unites': units}


def _sig_coord(c):
    """Coordonnées au millimètre (3 chiffres significatifs déformeraient le maillage d'un bâtiment de 30 m)."""
    return round(float(c), 3)


def for_job(job_id):
    return CalcRun.objects.filter(job_id=job_id).first()


def mark_running(job_id):
    CalcRun.objects.filter(job_id=job_id).update(status=Job.RUNNING)


def finish(job_id, result, started_at, surface_m2=None):
    run = for_job(job_id)
    if run is None:
        return None
    run.status = Job.DONE
    run.results = result
    run.indicators = indicators_from(result, surface_m2, run.level)
    run.duration_s = round(time.monotonic() - started_at, 2)
    run.save(update_fields=['status', 'results', 'indicators', 'duration_s'])
    return run


def fail(job_id, message):
    CalcRun.objects.filter(job_id=job_id).update(status=Job.ERROR, message=(message or '')[:500])


def expire_for_jobs(job_ids):
    CalcRun.objects.filter(job_id__in=list(job_ids), status__in=[Job.PENDING, Job.RUNNING]).update(
        status=Job.ERROR, message="Calcul interrompu (worker redémarré ?) — relancez-le.")
