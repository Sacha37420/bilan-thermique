"""Tâches Celery des potentiels (chantiers bt-potentiels, bt-trois-niveaux). Importées par `api/tasks.py`.

Potentiels d'un parc (N0, N1, N2) : file `parc`, **un potentiel à la fois**, puis la tâche se replanifie (même
stratégie que `tasks.process_parc_next`, D13) — un job interactif en attente sur la file `celery` passe donc avant
le suivant. N0 et N1 passent avant N2. Un N2 qui ne peut partir (calcul lourd en cours sur le lab) reste en attente
et la tâche se replanifie après `ATTENTE_LOURD_S`. Un potentiel resté « en cours » (worker arrêté) est remis en
attente par le prochain « Calculer » du parc."""

from celery import shared_task

ATTENTE_LOURD_S = 60


@shared_task(bind=True, acks_late=True)
def potentiels_parc_suivant(self, portfolio_id, owner_sub=''):
    from . import potentiels
    pot = potentiels.next_pending(portfolio_id)
    if pot is None:
        return 'fini'
    if potentiels.process_one(pot, owner_sub=owner_sub) == 'occupe':
        potentiels_parc_suivant.apply_async(args=[portfolio_id, owner_sub], queue='parc', countdown=ATTENTE_LOURD_S)
        return 'occupe'
    potentiels_parc_suivant.apply_async(args=[portfolio_id, owner_sub], queue='parc')
    return pot.pk


def enqueue(portfolio_id, owner_sub=''):
    potentiels_parc_suivant.apply_async(args=[portfolio_id, owner_sub], queue='parc')
