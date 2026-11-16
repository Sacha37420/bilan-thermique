"""Tâches Celery du recalcul (secteur C). Importé par `api/tasks.py`.

`recalcul_batiment(id, file)` : réclame et exécute (`execution.executer`) ; `recalcul_fond(id, cause)` : la phase de
fond (réel N2, potentiels), toujours en file `parc`. Au démarrage du worker, les recalculs interrompus ou dont la
tâche a été perdue (file Redis recréée à chaque déploiement) sont renvoyés.
"""

from celery import shared_task
from celery.signals import worker_ready


@shared_task(bind=True, acks_late=True)
def recalcul_batiment(self, building_id, file='celery'):
    from . import execution
    return execution.executer(building_id, file)


@shared_task(bind=True, acks_late=True)
def recalcul_fond(self, building_id, cause=''):
    from . import execution
    return execution.executer_fond(building_id, cause)


def reprendre():
    """Recalculs « en cours » (worker redémarré au milieu) → « planifié » ; tous les « planifié » renvoyés en file de
    fond. Rend le nombre renvoyé."""
    from django.utils import timezone
    from ..models import Recalcul
    from . import planification as plan
    if not plan.actif():
        return 0
    Recalcul.objects.filter(statut='en_cours').update(statut='planifie', prevu_pour=timezone.now())
    ids = list(Recalcul.objects.filter(statut='planifie').values_list('building_id', flat=True))
    for i in ids:
        try:
            recalcul_batiment.apply_async(args=[i, plan.FILE_FOND], countdown=5, queue=plan.FILE_FOND)
        except Exception:  # noqa: BLE001
            break
    return len(ids)


@worker_ready.connect
def _reprendre_recalculs(**_kwargs):
    try:
        reprendre()
    except Exception:  # noqa: BLE001 — le démarrage du worker ne dépend pas du recalcul
        pass
