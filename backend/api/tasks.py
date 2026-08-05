from celery import shared_task

from .models import Job, Building, ParoiModel
from . import shadow
from . import building_solver


@shared_task(bind=True)
def precompute_shadows(self, job_id: int, building_id: int):
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    try:
        job.set_state(status=Job.RUNNING, progress=0, message="Préparation de la géométrie…")
        building = Building.objects.get(pk=building_id)
        environment_envelope = building.environment.envelope if building.environment_id else None

        def progress_cb(done, total):
            # Grille de visibilité solaire : 0-79 % ; facteur de vue du ciel : 80-98 %.
            pct = int(1 + done * 79 / total)
            job.set_state(progress=pct, message=f"Test de visibilité solaire… {done}/{total} positions")

        result = shadow.compute_visibility_grid(
            building.envelope, environment_envelope, progress_cb=progress_cb,
        )

        job.set_state(progress=80, message="Facteur de vue du ciel (occlusion réelle)…")
        result['sky_view_factor'] = shadow.compute_sky_view_factors(building.envelope, environment_envelope)

        building.sun_visibility = result
        building.sun_visibility_stale = False
        building.save(update_fields=['sun_visibility', 'sun_visibility_stale'])

        job.result = {
            'n_triangles': len(building.envelope['triangles']),
            'n_azimuths': len(result['azimuths_deg']),
            'n_elevations': len(result['elevations_deg']),
        }
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100, message="Précalcul d'ombrage terminé.")
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))


@shared_task(bind=True)
def run_building_calcul(self, job_id: int, building_id: int, calcul_payload: dict):
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    try:
        job.set_state(status=Job.RUNNING, progress=0, message="Assemblage du système…")
        building = Building.objects.get(pk=building_id)
        triangles = building.envelope.get('triangles', [])
        paroi_ids = {t['paroi_model_id'] for t in triangles}
        paroi_layers = {p.pk: p.layers for p in ParoiModel.objects.filter(pk__in=paroi_ids)}
        sun_visibility = building.sun_visibility if building.sun_visibility.get('per_triangle') else None
        environment_envelope = building.environment.envelope if building.environment_id else None

        # DB write throttlée (~1 % du run, jamais moins de 5s d'intervalle) —
        # un job de plusieurs milliers d'heures ne doit pas faire une écriture
        # Job par heure simulée.
        import time
        last_write = [0.0]

        def progress_cb(done, total):
            pct = int(1 + done * 98 / total)
            now = time.monotonic()
            if pct == 100 or now - last_write[0] > 2.0:
                last_write[0] = now
                job.set_state(progress=pct, message=f"Résolution heure par heure… {done}/{total}")

        result = building_solver.run_building_simulation(
            building.envelope, paroi_layers, sun_visibility, calcul_payload,
            environment_envelope=environment_envelope, progress_cb=progress_cb,
        )

        job.result = {
            'hours': result['hours'],
            't_air_mean': result['t_air_mean'],
            'heating_kwh': result['heating_kwh'],
            'cooling_kwh': result['cooling_kwh'],
            'flux_positive_kwh': result['flux_positive_kwh'],
            'flux_negative_kwh': result['flux_negative_kwh'],
            't_air': result['t_air'],
            'envelope_flux_w': result['envelope_flux_w'],
            'final_exterior_surface_temp': result['final_exterior_surface_temp'],
            'final_interior_surface_temp': result['final_interior_surface_temp'],
        }
        job.save(update_fields=['result'])
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"Calcul terminé — {result['hours']}h, "
                    f"chauffage {result['heating_kwh']:.0f} kWh, clim {result['cooling_kwh']:.0f} kWh.",
        )
    except building_solver.BuildingSimulationError as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
