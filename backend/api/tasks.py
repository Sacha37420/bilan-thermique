from celery import shared_task

from .models import Job, Building
from . import shadow


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
            pct = int(1 + done * 98 / total)
            job.set_state(progress=pct, message=f"Test de visibilité solaire… {done}/{total} positions")

        result = shadow.compute_visibility_grid(
            building.envelope, environment_envelope, progress_cb=progress_cb,
        )

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
