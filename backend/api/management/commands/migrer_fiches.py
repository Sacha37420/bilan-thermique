"""Donner une fiche à tout bâtiment (Lot 27, refonte).

Un bâtiment créé avant la refonte (page Bâtiment, mode simplifié) n'a aucun fait : N0/N1 tournent sur des valeurs
par défaut invisibles et la fiche est vide. La commande lui donne une fiche :
- son enveloppe, si elle est entièrement assignée → faits « enveloppe experte » (Lot 26) ;
- sa surface de référence, si elle a été saisie → surface chauffée, déclarée (source « migration ») ;
- l'archétype par défaut (période inconnue, usage inconnu) pour tout le reste, en valeurs `defaut`.

Idempotente : un bâtiment qui a déjà au moins un fait n'est pas touché. `--check` ne fait rien et rend le code 1
s'il reste des bâtiments sans fiche.

    python manage.py migrer_fiches --check
    python manage.py migrer_fiches
"""

import sys

from django.core.management.base import BaseCommand

from api import archetypes, expert_facts, facts
from api.models import Building

SOURCE = 'migration (bâtiment d’avant la refonte)'


def without_fiche():
    return Building.objects.filter(facts__isnull=True).distinct().order_by('pk')


def give_fiche(b):
    added = expert_facts.sync_envelope(b, author_sub='', author_name='migration')['ajoutes']
    if b.surface_ref_m2:
        facts.record(b, 'identite.surface_m2', b.surface_ref_m2, 'declare', source=SOURCE, author_name='migration',
                     refresh=False)
        added.append('identite.surface_m2')
    defaults, period = archetypes.defaults()
    have = facts.effective_values(b)
    for k, v in defaults.items():
        if k not in have:
            facts.record(b, k, v, 'defaut', source=f'archétype {period} · usage inconnu', refresh=False)
            added.append(k)
    facts.refresh_scores(b)
    return added


class Command(BaseCommand):
    help = "Donne une fiche (faits) à tout bâtiment qui n'en a pas ; --check ne fait rien."

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')

    def handle(self, *args, **opts):
        rows = list(without_fiche())
        if opts['check']:
            for b in rows:
                self.stdout.write(f'  sans fiche : #{b.pk} {b.name}')
            self.stdout.write(f'{len(rows)} bâtiment(s) sans fiche.')
            if rows:
                sys.exit(1)
            return
        for b in rows:
            added = give_fiche(b)
            self.stdout.write(f'  #{b.pk} {b.name} : {len(added)} fait(s)')
        self.stdout.write(f'{len(rows)} bâtiment(s) migré(s).')
