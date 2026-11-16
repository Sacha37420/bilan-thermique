"""Tâches Celery du secteur R (chantier bt-potentiels). Importées par `api/tasks.py` pour être
enregistrées : déclarer ici les `@shared_task` du secteur."""

from celery import shared_task


@shared_task
def rendu_export(export_id: int):
    """Met en forme un export (HTML, DOCX, PDF) déjà résolu ; purge au passage les exports expirés."""
    from . import exports
    exports.purger()
    exports.executer(export_id)


@shared_task
def rendu_purger():
    """Supprime les exports expirés (24 h). Appelée aussi à chaque demande et à chaque liste d'exports ; peut être
    planifiée (Celery beat) si l'app en a un un jour."""
    from . import exports
    return exports.purger()
