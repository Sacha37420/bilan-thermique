"""Lot 36 (chantier bt-potentiels, I3) — DH RE2020 à seuil adaptatif borné [26 ; 28] °C, vérifié sur des séries
construites à la main ; indicateurs et séries mensuelles des calculs ; liste canonique des indicateurs."""

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import calc_runs
from ..engines import comfort
from ..models import CalcRun
from ..testing import client_for, patch_jwks, without_auto_environment

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


class Re2020Test(SimpleTestCase):
    def test_constant_warm_series_hand_value(self):
        """Air extérieur 20 °C : θ_rm = 20 → 0,33·20 + 21,8 = 28,4 → borné à 28 °C le jour, 26 °C la nuit (logement).
        T_op = 29 °C pendant 48 h : 9 h de nuit × 3 + 15 h de jour × 1 = 42 °C·h par jour → 84 °C·h, 48 h."""
        dh, hours, _ = comfort.dh_re2020([29.0] * 48, [20.0] * 48, np.arange(48), residential=True)
        self.assertAlmostEqual(dh, 84.0, places=6)
        self.assertEqual(hours, 48)

    def test_cool_series_floor_26(self):
        """θ_rm = 10 °C → 0,33·10 + 21,8 = 25,1 < 26 → 26 °C toute la journée ; T_op 27 → 1 °C·h par heure."""
        dh, hours, _ = comfort.dh_re2020([27.0] * 24, [10.0] * 24, np.arange(24), residential=True)
        self.assertAlmostEqual(dh, 24.0)
        self.assertEqual(hours, 24)

    def test_intermediate_threshold_and_tertiary_hours(self):
        """θ_rm = 15 °C → T_conf jour = 26,75 °C ; T_op 27 : 0,25 °C·h par heure occupée. Tertiaire : lundi 8 h–19 h
        seulement (11 h) → 2,75 °C·h ; logement : 15 h de jour × 0,25 + 9 h de nuit × 1 = 12,75 °C·h."""
        dh_t, h_t, _ = comfort.dh_re2020([27.0] * 24, [15.0] * 24, np.arange(24), residential=False)
        self.assertAlmostEqual(dh_t, 11 * 0.25)
        self.assertEqual(h_t, 11)
        dh_r, _h, _ = comfort.dh_re2020([27.0] * 24, [15.0] * 24, np.arange(24), residential=True)
        self.assertAlmostEqual(dh_r, 15 * 0.25 + 9 * 1.0)

    def test_running_mean_recursion(self):
        """Sept jours à 10 °C puis un jour à 20 °C : amorce 10 ; le 9ᵉ jour θ_rm = 0,2·20 + 0,8·10 = 12 °C."""
        t = [10.0] * (7 * 24) + [20.0] * 24 + [20.0] * 24
        rm = comfort.running_mean(t, np.arange(len(t)))
        self.assertAlmostEqual(rm[7 * 24], 10.0)
        self.assertAlmostEqual(rm[8 * 24], 12.0)
        self.assertTrue(np.all((comfort.t_conf(t) >= 26.0) & (comfort.t_conf(t) <= 28.0)))

    def test_no_discomfort_below_threshold(self):
        dh, hours, _ = comfort.dh_re2020([25.9] * 100, [30.0] * 100)
        self.assertEqual((dh, hours), (0.0, 0))

    def test_monthly_series(self):
        ind, mens = comfort.indicators([29.0] * 8760, [20.0] * 8760, np.arange(8760), True, [100.0] * 8760)
        self.assertEqual(len(mens['dh_re2020']), 12)
        self.assertAlmostEqual(sum(mens['dh_re2020']), ind['dh_re2020'], delta=1.0)
        self.assertAlmostEqual(mens['heating_kwh'][0], 74.4)        # 744 h × 100 W
        self.assertEqual(mens['cooling_kwh'][0], 0.0)
        self.assertIn('RE2020', ind['t_conf_methode'])


class IndicatorsTest(SimpleTestCase):
    def test_n0_has_null_comfort_keys(self):
        ind = calc_runs.indicators_from({'heating_kwh': 1000.0, 'hours': 8760}, 100.0, CalcRun.N0)
        for k in ('dh_re2020', 'heures_inconfort', 't_conf_methode', 'dh_26'):
            self.assertIn(k, ind)
            self.assertIsNone(ind[k])
        self.assertEqual(ind['cooling_kwh_m2'], 0.0)

    def test_n1_alias_and_comfort(self):
        res = {'heating_kwh': 1.0, 'hours': 3, 't_op': [27.0, 25.0, 30.0],
               'confort': {'dh_re2020': 5.0, 'heures_inconfort': 2, 't_conf_methode': 'x'}}
        ind = calc_runs.indicators_from(res, 10.0, CalcRun.N1)
        self.assertEqual(ind['dh_26'], ind['degree_hours_over_26'])
        self.assertEqual(ind['dh_26'], 5.0)
        self.assertEqual(ind['dh_re2020'], 5.0)


@patch_jwks()
class IndicateursRouteTest(TestCase):
    def test_list(self):
        r = client_for('g').get('/api/indicateurs/')
        self.assertEqual(r.status_code, 200)
        cles = {i['cle']: i for i in r.json()}
        for k in ('dh_re2020', 'dh_26', 'heures_inconfort', 't_conf_methode', 'cooling_kwh_m2', 'heating_kwh_m2'):
            self.assertIn(k, cles)
        for i in r.json():
            self.assertEqual(set(i), {'cle', 'libelle', 'unite', 'niveaux', 'additif', 'sens', 'famille'})
            self.assertIn(i['sens'], ('bas_est_mieux', 'haut_est_mieux'))
            self.assertIn(i['famille'], ('energie', 'confort', 'donnees'))
        self.assertEqual(cles['dh_re2020']['niveaux'], ['N1', 'N2'])
        self.assertFalse(cles['heating_kwh_m2']['additif'])
        self.assertTrue(cles['heating_kwh']['additif'])
