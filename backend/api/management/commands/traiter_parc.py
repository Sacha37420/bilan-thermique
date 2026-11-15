"""Importe un CSV dans un nouveau parc et le traite de bout en bout, sans worker (Lot 11 — recette).

    python manage.py traiter_parc parc.csv [--nom "Parc de recette"]

Affiche le tableau (besoin N1 et fourchette, DPE, confiance, complétude) et les durées.
"""

import time

from django.core.management.base import BaseCommand

from api import parc
from api.models import Building, Portfolio


class Command(BaseCommand):
    help = 'Importe et traite un parc depuis un CSV (synchrone).'

    def add_arguments(self, parser):
        parser.add_argument('fichier')
        parser.add_argument('--nom', default='Parc de recette')

    def handle(self, *args, **o):
        with open(o['fichier'], encoding='utf-8') as fh:
            rows, errors = parc.parse_csv(fh.read())
        p = Portfolio.objects.create(name=o['nom'], owner_sub='recette')
        parc.import_rows(p, rows, 'recette', 'recette')
        t0 = time.monotonic()
        durations = []
        while (b := parc.next_building(p.pk)) is not None:
            t = time.monotonic()
            parc.process_one(b)
            durations.append(time.monotonic() - t)
        total = time.monotonic() - t0
        for r in parc.table(p, Building.objects.filter(portfolio=p).order_by('name')):
            rng = f"{r['p10']:.0f}–{r['p90']:.0f}" if r['p10'] is not None else '—'
            self.stdout.write(
                f"{r['status']:9} | {r['name'][:34]:34} | {str(r['usage'] or '—'):18} | "
                f"{(r['surface_m2'] or 0):7.0f} m² | {('%.0f' % r['heating_kwh_m2']) if r['heating_kwh_m2'] is not None else '—':>5} "
                f"kWh/m² [{rng:>9}] | DPE {r['dpe'] or '—'} | conf {r['confidence']:.2f} | compl {r['completeness']:.2f}"
                + (f" | {r['errors']}" if r['errors'] else ''))
        c = parc.counts(p)
        self.stdout.write(f"{c['complet']}/{c['total']} complets, {c['incomplet']} incomplets ; "
                          f"{len(errors)} ligne(s) écartée(s) ; durée totale {total:.0f} s, "
                          f"{(sum(durations) / max(len(durations), 1)):.1f} s par bâtiment")
