"""Initialise des bâtiments depuis des adresses, sans saisie (Lot 9, refonte — recette).

    python manage.py initialiser_adresses "1 place Jean Jaurès 37000 Tours" "…"
    python manage.py initialiser_adresses --fichier adresses.txt   (une adresse par ligne)

Joue la chaîne A1→A6 de façon synchrone et affiche, par bâtiment : statut, RNB, année, usage,
U des murs (et sa provenance), mitoyenneté, confiance, complétude, durée.
"""

import time

from django.core.management.base import BaseCommand

from api import facts, pipeline
from api.environment_service import unique_name
from api.models import Building


class Command(BaseCommand):
    help = 'Initialise des bâtiments depuis des adresses (chaîne automatique, sans saisie).'

    def add_arguments(self, parser):
        parser.add_argument('adresses', nargs='*')
        parser.add_argument('--fichier')

    def handle(self, *args, **o):
        addrs = list(o['adresses'])
        if o['fichier']:
            with open(o['fichier'], encoding='utf-8') as fh:
                addrs += [line.strip() for line in fh if line.strip() and not line.startswith('#')]
        ok = 0
        for a in addrs:
            t0 = time.monotonic()
            b = Building.objects.create(name=unique_name(Building, a[:140]), address=a)
            rep = pipeline.initialize(b)
            eff = facts.effective(b)
            mu = eff.get('mur.u')
            errs = {k: v.get('error') for k, v in rep['steps'].items() if not v.get('ok')}
            ok += b.pipeline_status == 'complet'
            self.stdout.write(
                f"{b.pipeline_status:9} | {b.rnb_id or '—':12} | {a[:45]:45} | "
                f"{getattr(eff.get('identite.annee_construction'), 'value', '?')} | "
                f"{getattr(eff.get('identite.usage'), 'value', '?'):18} | "
                f"U mur {mu.value if mu else '?'} ({mu.method if mu else '-'}) | "
                f"mitoy. {getattr(eff.get('identite.mitoyennete'), 'value', '?')} | "
                f"conf {b.confidence:.2f} | compl {b.completeness:.2f} | {time.monotonic() - t0:.1f} s"
                + (f" | {errs}" if errs else ''))
        self.stdout.write(f'{ok}/{len(addrs)} bâtiment(s) initialisé(s) complètement.')
