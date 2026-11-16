"""Tâches Celery du secteur A (chantier bt-potentiels). Importées par `api/tasks.py` pour être enregistrées.

Potentiels d'un parc (N1) : file `parc`, **un potentiel à la fois**, puis la tâche se replanifie (même stratégie que
`tasks.process_parc_next`, D13) — un job interactif en attente sur la file `celery` passe donc avant le suivant.
Un potentiel resté « en cours » (worker arrêté) est remis en attente par le prochain « Calculer » du parc."""

from celery import shared_task


@shared_task(bind=True, acks_late=True)
def potentiels_parc_suivant(self, portfolio_id, owner_sub=''):
    from . import potentiels
    pot = potentiels.next_pending(portfolio_id)
    if pot is None:
        return 'fini'
    potentiels.process_one(pot, owner_sub=owner_sub)
    potentiels_parc_suivant.apply_async(args=[portfolio_id, owner_sub], queue='parc')
    return pot.pk


def enqueue(portfolio_id, owner_sub=''):
    potentiels_parc_suivant.apply_async(args=[portfolio_id, owner_sub], queue='parc')
