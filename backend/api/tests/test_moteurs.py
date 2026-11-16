"""Lot 10 (refonte) — moteurs N0 (mensuel) et N1 (5R1C horaire), comparaison à N2 (FEM)."""

import copy

from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_service, facts
from ..engines import inputs, n0, n1
from ..models import Building, CalcRun
from ..testing import box_with_window, client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


# Seuil d'écart N1/N2 sur le besoin annuel de chauffage (décision D12 de refonte.md).
N1_N2_TOLERANCE = 0.20
N0_N2_TOLERANCE = 0.40

WALL = [{'e': 0.10, 'lam': 0.04, 'rho': 30, 'c': 1400, 'tau': 0, 'r': 0, 'alpha': 0.6},
        {'e': 0.20, 'lam': 1.75, 'rho': 2300, 'c': 1000, 'tau': 0, 'r': 0, 'alpha': 0.6}]
WALL_ITI = list(reversed(WALL))
ROOF = [{'e': 0.20, 'lam': 0.04, 'rho': 30, 'c': 1400, 'tau': 0, 'r': 0, 'alpha': 0.7},
        {'e': 0.18, 'lam': 1.75, 'rho': 2300, 'c': 1000, 'tau': 0, 'r': 0, 'alpha': 0.7}]
FLOOR = [{'e': 0.08, 'lam': 0.04, 'rho': 30, 'c': 1400, 'tau': 0, 'r': 0, 'alpha': 0.5},
         {'e': 0.15, 'lam': 1.75, 'rho': 2300, 'c': 1000, 'tau': 0, 'r': 0, 'alpha': 0.5}]
GLASS = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06},
         {'e': 0.016, 'lam': 0.094, 'rho': 1.2, 'c': 1000, 'tau': 0.97, 'r': 0.01, 'alpha': 0.02},
         {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06}]
LAYERS = {1: WALL, 2: ROOF, 3: FLOOR, 4: GLASS, 5: WALL_ITI}
CASES = {  # les trois cas de recette (§ 6.2) : boîte, maison vitrée au sud, petit collectif
    'boite': (dict(w=10, d=8, h=3, win_ratio_south=0.0001, wall_id=1), 80, 165_000),
    'maison': (dict(w=10, d=8, h=5.5, win_ratio_south=0.30, wall_id=1), 160, 165_000),
    'collectif': (dict(w=20, d=12, h=9, win_ratio_south=0.35, wall_id=5), 720, 260_000),
}
WEATHER = synthetic_year()


def n2_heating(env, floor, n, gains, weather):
    vol = floor * 2.5
    payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 19.0, 'shadow_mode': 'precomputed', 't_ground': 12.0,
               'r_ground': 0.5, 'weather': weather,
               'interior': {'mode': 'thermostat', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * vol * 5, 't_min': 19.0,
                            't_max': 60.0, 'debit_vent_m3h': n * vol, 'eta_recup_vent': 0.0, 'apports_internes_w': gains}}
    return building_solver.run_building_simulation(env, LAYERS, None, payload)['heating_kwh']


class N1VersusN2Test(SimpleTestCase):
    """Recette du lot : écart N1/N2 sous le seuil sur les trois cas (~10 s)."""

    def test_three_reference_cases(self):
        for name, (kw, floor, cm) in CASES.items():
            env = box_with_window(roof_id=2, floor_id=3, glass_id=4, **kw)
            p = inputs.from_envelope(env, LAYERS, n_vent=0.6, gains_w=4.0 * floor, a_floor=floor, c_m_per_m2=cm)
            h1 = n1.run_one(p, WEATHER, keep_series=False)['heating_kwh']
            h0 = n0.run(p, WEATHER)['heating_kwh']
            h2 = n2_heating(env, floor, 0.6, 4.0 * floor, WEATHER)
            with self.subTest(cas=name, n1=round(h1), n2=round(h2)):
                self.assertLess(abs(h1 / h2 - 1), N1_N2_TOLERANCE)
                self.assertLess(abs(h0 / h2 - 1), N0_N2_TOLERANCE)


def simple_params(**over):
    vals = {'identite.surface_m2': 100.0, 'identite.niveaux': 1, 'identite.hauteur_m': 3.0,
            'identite.emprise_m2': 100.0, 'mur.u': 0.5, 'toiture.u': 0.3, 'plancher.u': 0.4,
            'baies.uw': 1.8, 'baies.g': 0.6, 'ventilation.type': 'vmc_sf_auto', 'ventilation.debit_vol_h': 0.5,
            'ventilation.n50': 4.0, 'usage.consigne_chauffage_c': 19.0, 'usage.apports_w_m2': 4.0,
            'identite.usage': 'maison', **{f'baies.ratio_{o}': 0.15 for o in 'NESO'}}
    vals.update(over)
    return inputs.from_fiche(vals)


class N1PhysicsTest(SimpleTestCase):
    def test_steady_state_matches_conductances(self):
        p = simple_params(**{'usage.apports_w_m2': 0.0})
        p.t_ground = 0.0
        w = [{'t_ext': 0.0, 'sun_azimuth': 0, 'sun_elevation': -20, 'e_dir': 0, 'e_dif': 0}] * 400
        r = n1.run_one(p, w)
        # Oracle du 5R1C en régime permanent, dérivé à la main : la transmission (baies + opaque, la
        # chaîne H_em–H_ms valant H_op) passe par le nœud de surface, donc EN SÉRIE avec H_tr_is ;
        # la ventilation va directement de l'air à l'extérieur. (Propriété connue du modèle de la
        # norme : H_tr_is compte une seconde fois une résistance déjà dans les U.)
        h_is = n1.H_IS * n1.A_T_FACTOR * p.a_floor
        h_trans = p.h_tr_w + p.h_tr_op_air + p.h_floor
        expected = (p.h_ve + 1.0 / (1.0 / h_is + 1.0 / h_trans)) * 19.0
        self.assertAlmostEqual(r['phi_hc_w'][-1], expected, delta=0.01 * expected)

    def test_directional_effects(self):
        base = n1.run_one(simple_params(), WEATHER, keep_series=False)['heating_kwh']
        better = n1.run_one(simple_params(**{'mur.u': 0.2}), WEATHER, keep_series=False)['heating_kwh']
        terraced = n1.run_one(simple_params(**{'identite.mitoyennete': 0.5}), WEATHER, keep_series=False)['heating_kwh']
        warmer = n1.run_one(simple_params(**{'usage.consigne_chauffage_c': 21.0}), WEATHER, keep_series=False)['heating_kwh']
        self.assertLess(better, base)
        self.assertLess(terraced, base)
        self.assertGreater(warmer, base)

    def test_horizon_cuts_south_gains(self):
        p = simple_params()
        free = n1.run_one(p, WEATHER, keep_series=False)
        p2 = copy.deepcopy(p)
        p2.horizon = {'N': 0, 'E': 0, 'S': 60, 'O': 0}
        masked = n1.run_one(p2, WEATHER, keep_series=False)
        self.assertLess(masked['balance']['solar_windows_kwh'], free['balance']['solar_windows_kwh'])
        self.assertGreater(masked['heating_kwh'], free['heating_kwh'])

    def test_vectorised_equals_individual(self):
        a, b = simple_params(), simple_params(**{'mur.u': 1.5})
        both = n1.run([a, b], WEATHER[:24 * 30], keep_series=False)
        self.assertAlmostEqual(both[1]['heating_kwh'], n1.run_one(b, WEATHER[:24 * 30], keep_series=False)['heating_kwh'], places=6)

    def test_operative_temperature_and_comfort(self):
        p = simple_params(**{'baies.ratio_S': 0.6, 'baies.g': 0.8})
        r = n1.run_one(p, synthetic_year(days=60, start_day=180, t_mean=22, t_amp=0))
        self.assertEqual(len(r['t_op']), 60 * 24)
        self.assertGreater(r['t_op_max'], 26)

    def test_cooling_when_setpoint_given(self):
        hot = synthetic_year(days=30, start_day=190, t_mean=27, t_amp=0)
        r = n1.run_one(simple_params(**{'usage.consigne_froid_c': 26.0}), hot, keep_series=False)
        self.assertGreater(r['cooling_kwh'], 0)

    def test_office_schedule_reduces_need(self):
        res = n1.run_one(simple_params(), WEATHER, keep_series=False)['heating_kwh']
        office = n1.run_one(simple_params(**{'identite.usage': 'bureau'}), WEATHER, keep_series=False)['heating_kwh']
        self.assertNotAlmostEqual(res, office, delta=1)


class CalcServiceTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        for k, v in (('identite.surface_m2', 120.0), ('mur.u', 1.2), ('identite.usage', 'maison')):
            facts.record(self.b, k, v, 'infere')
        self.w = {'points': WEATHER, 'label': 'année synthétique'}

    def test_n1_and_n0_create_runs_with_indicators_and_traceability(self):
        r1, fresh = calc_service.run_level(self.b, 'N1', weather_spec=self.w)
        self.assertTrue(fresh)
        self.assertEqual(r1.status, 'DONE')
        self.assertIn('heating_kwh_m2', r1.indicators)
        self.assertIn('hours_over_28', r1.indicators)
        self.assertEqual(r1.settings['facts']['mur.u']['method'], 'infere')
        r0, _ = calc_service.run_level(self.b, 'N0', weather_spec=self.w)
        self.assertEqual(len(r0.results['monthly_heating_kwh']), 12)

    def test_identical_inputs_not_recomputed_but_change_is(self):
        r1, _ = calc_service.run_level(self.b, 'N1', weather_spec=self.w)
        again, fresh = calc_service.run_level(self.b, 'N1', weather_spec=self.w)
        self.assertFalse(fresh)
        self.assertEqual(again.pk, r1.pk)
        facts.record(self.b, 'mur.u', 0.3, 'declare', author_sub='u')
        r2, fresh = calc_service.run_level(self.b, 'N1', weather_spec=self.w)
        self.assertTrue(fresh)
        self.assertLess(r2.indicators['heating_kwh'], r1.indicators['heating_kwh'])
        self.assertGreater(r2.indicators['confidence'], r1.indicators['confidence'])


@patch_jwks()
class CalcApiTest(TestCase):
    def test_compute_and_read_latest(self):
        b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        facts.record(b, 'identite.surface_m2', 90.0, 'infere')
        from unittest import mock
        with self.settings():
            with mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER[:24 * 60], 'source': 'test'}):
                r = client_for().post(f'/api/batiments/{b.pk}/calculer/', {'niveaux': ['N0', 'N1']}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual({x['level'] for x in r.json()['runs']}, {'N0', 'N1'})
        latest = client_for().get(f'/api/batiments/{b.pk}/resultat/').json()
        self.assertTrue(latest['N1']['up_to_date'])
        self.assertNotIn('t_air', latest['N1'])
        facts.record(b, 'mur.u', 0.2, 'declare', author_sub='x')
        override = facts.effective(b)['mur.u']
        self.assertFalse(client_for().get(f'/api/batiments/{b.pk}/resultat/').json()['N1']['up_to_date'])
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER[:24 * 60], 'source': 'test'}):
            client_for().post(f'/api/batiments/{b.pk}/calculer/', {'niveaux': ['N1']}, format='json')
        facts.retire(override, 'x')  # revenir à l'automatique : le premier calcul redevient « à jour »
        again = client_for().get(f'/api/batiments/{b.pk}/resultat/').json()['N1']
        self.assertTrue(again['up_to_date'])
        self.assertEqual(again['id'], latest['N1']['id'])

    def test_unknown_level_refused(self):
        b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        r = client_for().post(f'/api/batiments/{b.pk}/calculer/', {'niveaux': ['N9']}, format='json')
        self.assertEqual(r.status_code, 400)
