"""Lot 40 — by-pass d'été de la double flux : la récupération ne réchauffe plus, l'été, un bâtiment qui n'a pas besoin
de chaleur (cas constaté : bureaux très isolés, double flux 0,8, 14 908 °C·h de DH RE2020 et 46 °C de T_op max)."""

import copy

from django.test import SimpleTestCase

from .. import building_solver
from ..engines import inputs, n1, n2_inputs
from ..testing import box_with_window, synthetic_year, without_auto_environment
from .test_moteurs import FLOOR, GLASS, ROOF, WALL

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


HOT = synthetic_year(t_mean=15.0, t_amp=10.0)
OFFICE = {'identite.surface_m2': 600.0, 'identite.usage': 'bureau', 'identite.niveaux': 2, 'identite.hauteur_m': 6.0,
          'mur.u': 0.2, 'toiture.u': 0.127, 'plancher.u': 0.298, 'plancher.type': 'sous_sol', 'baies.uw': 0.9,
          'baies.g': 0.5, 'ventilation.type': 'vmc_df', 'ventilation.rendement_recup': 0.8,
          'ventilation.debit_vol_h': 0.5, 'ventilation.n50': 2.5, 'usage.apports_w_m2': 9.0,
          'usage.consigne_chauffage_c': 19.0, **{f'baies.ratio_{o}': 0.25 for o in 'NESO'}}


class BypassN1Test(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        df = inputs.from_fiche(OFFICE)
        no_bypass = copy.deepcopy(df)
        no_bypass.bypass = False
        no_recovery = inputs.from_fiche({**OFFICE, 'ventilation.rendement_recup': 0.0})
        cls.r = dict(zip(('df', 'sans_bypass', 'sans_recup'), n1.run([df, no_bypass, no_recovery], HOT)))

    def test_split(self):
        p = inputs.from_fiche(OFFICE)
        self.assertEqual((p.n_recup, p.eta_recup), (0.5, 0.8))
        # Renouvellement effectif « échangeur actif » : 0,5·(1 − 0,8) + 2,5/20 = 0,225 vol/h (inchangé).
        self.assertAlmostEqual(p.n_vent, 0.225)
        self.assertEqual(inputs.from_fiche({**OFFICE, 'ventilation.type': 'vmc_sf_auto'}).eta_recup, 0.0)

    def test_summer_like_without_recovery_winter_unchanged(self):
        df, nb, nr = self.r['df'], self.r['sans_bypass'], self.r['sans_recup']
        # Sans by-pass : surchauffe aggravée (le défaut constaté : ≈ 2 fois les DH sans récupération) ; avec : du même
        # ordre que sans récupération.
        self.assertGreater(nb['confort']['dh_re2020'], 1.5 * nr['confort']['dh_re2020'])
        self.assertLess(df['confort']['dh_re2020'], 1.25 * nr['confort']['dh_re2020'] + 50)
        self.assertGreater(df['confort']['dh_re2020'], 0.5 * nr['confort']['dh_re2020'] - 50)
        self.assertLess(df['t_op_max'], nb['t_op_max'])
        # Hiver : la récupération reste active quand on chauffe → besoin inchangé (±2 %) et bien sous « sans récup ».
        self.assertAlmostEqual(df['heating_kwh'], nb['heating_kwh'], delta=0.02 * nb['heating_kwh'] + 1.0)
        self.assertLess(df['heating_kwh'], 0.9 * nr['heating_kwh'])
        self.assertGreater(df['bypass_double_flux_h'], 500)

    def test_rule(self):
        self.assertTrue(bool(inputs.recovery_active(0.0, 19.0, 19.0)))     # on chauffe : échangeur actif
        self.assertFalse(bool(inputs.recovery_active(18.0, 24.0, 19.0)))   # dehors plus frais, dedans chaud : by-pass
        self.assertTrue(bool(inputs.recovery_active(8.0, 24.0, 19.0)))     # sous 12 °C dehors : on garde la chaleur
        self.assertTrue(bool(inputs.recovery_active(32.0, 26.0, 19.0)))    # dehors plus chaud : l'échangeur rafraîchit


class BypassN2Test(SimpleTestCase):
    def test_payload_and_solver(self):
        payload, _res = n2_inputs.payload_from_fiche(OFFICE, 600.0)
        vol = 600.0 * inputs.CEILING_H
        self.assertAlmostEqual(payload['interior']['debit_vent_m3h'], (0.225 + 0.4) * vol, places=1)
        self.assertAlmostEqual(payload['bypass']['g_recup_w_k'], 0.34 * 0.4 * vol, places=2)
        self.assertEqual(payload['bypass']['seuil_c'], 21.0)
        self.assertNotIn('bypass', n2_inputs.payload_from_fiche({**OFFICE, 'ventilation.type': 'vmc_sf_auto'}, 600.0)[0])
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4, win_ratio_south=0.3)
        summer = synthetic_year(days=15, start_day=190, t_mean=22.0, t_amp=0.0)   # nuits ≈ 18,5 °C (> 12)
        base = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 24.0, 'shadow_mode': 'precomputed', 't_ground': 14.0,
                'weather': summer, 'interior': {'mode': 'free', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 240 * 5,
                                                'debit_vent_m3h': 200.0, 'apports_internes_w': 1500.0}}
        layers = {1: WALL, 2: ROOF, 3: FLOOR, 4: GLASS}
        always = {**base, 'interior': {**base['interior'], 'debit_vent_m3h': 200.0 * 0.2}}   # récupération 0,8 toujours
        bypass = {**base, 'bypass': {'g_recup_w_k': 0.34 * 200.0 * 0.8, 'seuil_c': 21.0}}
        r_always = building_solver.run_building_simulation(env, layers, None, always)
        r_bypass = building_solver.run_building_simulation(env, layers, None, bypass)
        self.assertLess(max(r_bypass['t_op']), max(r_always['t_op']))
        self.assertGreater(r_bypass['bypass_double_flux_h'], 0)
        self.assertLess(abs(r_bypass['balance']['closure_error_kwh']), 0.05)
