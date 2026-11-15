"""Lot 27 (refonte) — tout bâtiment a une fiche : commande `migrer_fiches`."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .. import facts
from ..models import Building, ParoiModel
from ..testing import box_envelope

WALL = [{'e': 0.30, 'lam': 0.5, 'rho': 1800, 'c': 1000, 'tau': 0, 'r': 0.4, 'alpha': 0.6}]


class MigrerFichesTest(TestCase):
    def _run(self, *args):
        out = StringIO()
        try:
            call_command('migrer_fiches', *args, stdout=out)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue()

    def test_check_then_migrate_then_idempotent(self):
        p = ParoiModel.objects.create(name='Mur', layers=WALL)
        old = Building.objects.create(name='ancien', envelope=box_envelope(paroi_id=p.pk), surface_ref_m2=95.0)
        bare = Building.objects.create(name='nu')
        done = Building.objects.create(name='déjà')
        facts.record(done, 'mur.u', 0.5, 'infere')
        code, out = self._run('--check')
        self.assertEqual(code, 1)
        self.assertIn('2 bâtiment(s) sans fiche', out)
        self.assertEqual(Building.objects.get(pk=old.pk).facts.count(), 0)    # --check ne fait rien
        code, out = self._run()
        self.assertEqual(code, 0)
        eff = facts.effective(old)
        self.assertEqual(eff['identite.surface_m2'].value, 95.0)
        self.assertEqual(eff['identite.surface_m2'].method, 'declare')
        self.assertEqual(eff['mur.u'].source, 'enveloppe experte')            # l'enveloppe assignée prime
        self.assertEqual(facts.effective(bare)['mur.u'].method, 'defaut')
        self.assertEqual(set(facts.effective(done)), {'mur.u'})               # déjà doté : intact
        self.assertEqual(self._run('--check')[0], 0)
        n = old.facts.count()
        self._run()
        self.assertEqual(old.facts.count(), n)
        self.assertGreater(Building.objects.get(pk=old.pk).confidence, 0)
