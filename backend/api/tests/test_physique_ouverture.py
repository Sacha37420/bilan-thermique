"""Lot 40 (I9, contrat v4) — ouverture des fenêtres en été, réglage du parc désactivé par défaut : sans effet tant
qu'elle n'est pas activée ; activée, DH nettement plus bas et besoin d'hiver inchangé ; empreinte des entrées."""

import copy
from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_service, facts
from ..engines import inputs, n1, surcharges
from ..models import Building, Portfolio
from ..testing import box_with_window, synthetic_year, without_auto_environment
from .test_moteurs import FLOOR, GLASS, ROOF, WALL
from .test_physique_bypass import HOT, OFFICE

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


OPEN = {'seuil_c': 25.0, 'ecart_c': 1.0, 'vol_h': 3.0, 'occupation': True}


class N1Test(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        closed = inputs.from_fiche(OFFICE)
        opened = copy.deepcopy(closed)
        opened.window_opening = dict(OPEN)
        anytime = copy.deepcopy(closed)
        anytime.window_opening = {**OPEN, 'occupation': False}
        cls.r = dict(zip(('ferme', 'ouvert', 'toujours'), n1.run([closed, opened, anytime], HOT)))

    def test_disabled_is_unchanged(self):
        self.assertNotIn('ouverture_fenetres_h', self.r['ferme'])
        again = n1.run_one(inputs.from_fiche(OFFICE), HOT)
        self.assertEqual(again['confort'], self.r['ferme']['confort'])
        self.assertEqual(again['heating_kwh'], self.r['ferme']['heating_kwh'])

    def test_enabled_lowers_dh_keeps_winter(self):
        f, o, t = self.r['ferme'], self.r['ouvert'], self.r['toujours']
        self.assertLess(o['confort']['dh_re2020'], 0.5 * f['confort']['dh_re2020'])
        self.assertLess(o['t_op_max'], f['t_op_max'])
        # Ouverture de mai à septembre seulement : besoin d'hiver inchangé, mois par mois d'octobre à avril.
        for m in (0, 1, 2, 3, 9, 10, 11):
            self.assertEqual(o['mensuel']['heating_kwh'][m], f['mensuel']['heating_kwh'][m])
        self.assertAlmostEqual(o['heating_kwh'], f['heating_kwh'], delta=0.02 * f['heating_kwh'] + 1.0)
        self.assertGreater(o['ouverture_fenetres_h'], 100)
        # Hors occupation aussi (soirées) : plus d'heures d'ouverture, DH au plus égaux.
        self.assertGreater(t['ouverture_fenetres_h'], o['ouverture_fenetres_h'])
        self.assertLessEqual(t['confort']['dh_re2020'], o['confort']['dh_re2020'] + 1e-6)
        # Baies ouvertes : échangeur by-passé (au moins autant d'heures sans récupération).
        self.assertGreaterEqual(o['bypass_double_flux_h'], f['bypass_double_flux_h'])


class N2Test(SimpleTestCase):
    def test_solver(self):
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4, win_ratio_south=0.3)
        summer = synthetic_year(days=15, start_day=190, t_mean=22.0, t_amp=0.0)
        base = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 24.0, 'shadow_mode': 'precomputed', 't_ground': 14.0,
                'weather': summer, 'interior': {'mode': 'free', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 240 * 5,
                                                'debit_vent_m3h': 60.0, 'apports_internes_w': 1500.0}}
        layers = {1: WALL, 2: ROOF, 3: FLOOR, 4: GLASS}
        r0 = building_solver.run_building_simulation(env, layers, None, base)
        r1 = building_solver.run_building_simulation(env, layers, None, {
            **base, 'ouverture': {'g_w_k': 0.34 * 3.0 * 240.0, 'seuil_c': 25.0, 'ecart_c': 1.0}})
        self.assertIsNone(r0['ouverture_fenetres_h'])
        self.assertGreater(r1['ouverture_fenetres_h'], 0)
        self.assertLess(max(r1['t_op']), max(r0['t_op']))
        self.assertLess(abs(r1['balance']['closure_error_kwh']), 0.05)


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': HOT, 'source': 'test'})


class ParcSettingTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='p', owner_sub='g')
        self.b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69)
        self.alone = Building.objects.create(name='seul', georef_lat=47.39, georef_lon=0.69)
        for b in (self.b, self.alone):
            for k, v in {**OFFICE, 'plancher.type': 'terre_plein'}.items():
                facts.record(b, k, v, 'infere', refresh=False)
            facts.refresh_scores(b)

    def _hash(self, level='N1'):
        return calc_service.inputs_hash_for(self.b, level, None, 'w')

    def test_default_off_and_outside_parc(self):
        self.assertIsNone(surcharges.window_opening(self.b))
        self.assertIsNone(surcharges.window_opening(self.alone))
        self.assertIsNone(calc_service.params_for(self.b)[0].window_opening)

    def test_hash_changes_only_when_enabled(self):
        h0, h0_n0 = self._hash(), self._hash('N0')
        self.p.parametrage = {'confort.ouverture_fenetres': False}
        self.p.save()
        self.assertEqual(self._hash(), h0)
        self.p.parametrage = {'confort.ouverture_fenetres': True}
        self.p.save()
        h1 = self._hash()
        self.assertNotEqual(h1, h0)
        self.assertEqual(self._hash('N0'), h0_n0)                       # N0 : non appliquée
        self.p.parametrage = {'confort.ouverture_fenetres': True, 'confort.ouverture_seuil_c': 27.0}
        self.p.save()
        self.assertNotEqual(self._hash(), h1)

    def test_enabled_run_records_hypothesis(self):
        self.p.parametrage = {'confort.ouverture_fenetres': True}
        self.p.save()
        with tmy():
            run, _ = calc_service.run_level(self.b, 'N1', uncertainty=False)
            alone, _ = calc_service.run_level(self.alone, 'N1', uncertainty=False)
        o = run.inputs['params']['ouverture_fenetres']
        self.assertEqual((o['seuil_c'], o['vol_h']), (25.0, 3.0))
        self.assertIn('25 °C', o['hypothese'])
        self.assertGreater(run.results['ouverture_fenetres_h'], 0)
        self.assertLess(run.indicators['dh_re2020'], alone.indicators['dh_re2020'])
        self.assertIsNone(alone.inputs['params']['ouverture_fenetres'])
        self.assertNotIn('ouverture_fenetres_h', alone.results)
