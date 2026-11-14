"""Lancement exclusif des calculs lourds (Lot 2, refonte).

Avant : chaque vue faisait `Job.objects.filter(...).exists()` **puis** `Job.objects.create(...)`
— deux requêtes simultanées passaient toutes les deux le test et lançaient deux calculs sur un
worker en concurrence 1 (2 vCPU partagés par tout le lab).

Maintenant : le test et la création se font sous un verrou consultatif PostgreSQL de transaction
(`pg_advisory_xact_lock`), le même pour toute l'app — la seconde requête attend que la première
ait créé son job, puis le voit et rend 409. La tâche Celery n'est envoyée qu'après la validation
de la transaction (`on_commit`) : un worker ne peut pas chercher un job pas encore visible.

Un job resté `PENDING`/`RUNNING` plus de `STALE_AFTER` (worker redémarré au milieu d'un calcul)
est marqué en erreur au passage : sans cela il bloquerait l'app pour toujours.
"""

from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .models import Job

# Clé du verrou consultatif : une constante propre à l'app (le schéma est à elle seule, mais un
# verrou consultatif vaut pour toute la base — la valeur est choisie pour ne croiser aucune autre).
ADVISORY_LOCK_KEY = 0x6274_0002  # « bt », 2
STALE_AFTER = timedelta(hours=6)
ACTIVE = (Job.PENDING, Job.RUNNING)


def _lock():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)', [ADVISORY_LOCK_KEY])


def expire_stale_jobs():
    from . import calc_runs
    limit = timezone.now() - STALE_AFTER
    stale = list(Job.objects.filter(status__in=ACTIVE, updated_at__lt=limit).values_list('pk', flat=True))
    if stale:
        calc_runs.expire_for_jobs(stale)
    return Job.objects.filter(pk__in=stale).update(
        status=Job.ERROR, message="Calcul interrompu (worker redémarré ?) — relancez-le.")


def launch_exclusive(kind, params, enqueue, *, blocking_kinds=None, owner_sub='', on_created=None):
    """Crée et envoie un job si aucun job actif ne bloque, sinon rend None.

    blocking_kinds : None = tout job actif bloque (calculs lourds) ; sinon seuls ces types.
    enqueue(job) : appelé après validation de la transaction (envoi Celery).
    on_created(job) : appelé DANS la transaction (objets liés au job, ex. `CalcRun`).
    """
    with transaction.atomic():
        _lock()
        expire_stale_jobs()
        active = Job.objects.filter(status__in=ACTIVE)
        if blocking_kinds is not None:
            active = active.filter(kind__in=blocking_kinds)
        if active.exists():
            return None
        job = Job.objects.create(kind=kind, params=params, owner_sub=owner_sub or '')
        if on_created is not None:
            on_created(job)
        transaction.on_commit(lambda: enqueue(job))
    return job
