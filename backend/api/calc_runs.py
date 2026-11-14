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
    t = result.get('t_op') or result.get('t_air')
    if t and result.get('hours') and len(t) == result['hours'] + 1:
        t = t[1:]  # N2 : la série commence par l'état initial
    if t:
        ind.update(comfort_hours(t))
    for k in ('heating_kw_max', 'final_energy_kwh', 'primary_energy_kwh', 'co2_kg', 'cost_eur',
              'heating_kwh_p10', 'heating_kwh_p90'):
        if result.get(k) is not None:
            ind[k] = result[k]
    return ind


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
