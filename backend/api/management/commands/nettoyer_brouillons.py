"""Bâtiments brouillons laissés par un mode simplifié abandonné (Lot 3, refonte — point L7).

Brouillon = jamais calculé (aucun `CalcRun`), jamais d'ombrage précalculé, pas rattaché à un
parc, sans fait déclaré, et non modifié depuis `--jours` jours (défaut 14).

Par défaut la commande **ne supprime rien** : elle liste. `--appliquer` supprime — c'est une
décision humaine, jamais une tâche planifiée.

    python manage.py nettoyer_brouillons
    python manage.py nettoyer_brouillons --jours 30 --appliquer
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import Building


def draft_buildings(days=14):
    limit = timezone.now() - timedelta(days=days)
    qs = Building.objects.filter(updated_at__lt=limit, sun_visibility={}, calc_runs__isnull=True)
    if hasattr(Building, 'portfolio'):
        qs = qs.filter(portfolio__isnull=True)
    if hasattr(Building, 'facts'):
        qs = qs.filter(facts__isnull=True)
    return qs.distinct()


class Command(BaseCommand):
    help = "Liste (ou supprime avec --appliquer) les bâtiments brouillons d'un mode simplifié abandonné."

    def add_arguments(self, parser):
        parser.add_argument('--jours', type=int, default=14)
        parser.add_argument('--appliquer', action='store_true')

    def handle(self, *args, **opts):
        qs = draft_buildings(opts['jours'])
        rows = list(qs.values_list('pk', 'name', 'updated_at'))
        for pk, name, upd in rows:
            self.stdout.write(f"  #{pk}  {name}  (modifié le {upd:%Y-%m-%d})")
        if not opts['appliquer']:
            self.stdout.write(f"{len(rows)} brouillon(s) — rien supprimé (--appliquer pour supprimer).")
            return
        n, _ = Building.objects.filter(pk__in=[r[0] for r in rows]).delete()
        self.stdout.write(f"{len(rows)} brouillon(s) supprimé(s).")
