"""Lot 20 (refonte, D9) — température radiante moyenne et opérative dans le N2, comparables au N1."""

from django.test import SimpleTestCase

from .. import building_solver, calc_runs
from ..testing import box_with_window, synthetic_year
from .test_moteurs import LAYERS


class OperativeTemperatureN2Test(SimpleTestCase):
    def _run(self, weather, mode='free'):
        env = box_with_window(w=10, d=8, h=3, win_ratio_south=0.35, wall_id=1, roof_id=2, floor_id=3, glass_id=4)
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 22.0, 'shadow_mode': 'precomputed', 't_ground': 14.0,
                   'weather': weather, 'interior': {'mode': mode, 'h_i': 7.7, 'c_air_int': 1e6, 'debit_vent_m3h': 60.0,
                                                    'apports_internes_w': 300.0}}
        return building_solver.run_building_simulation(env, LAYERS, None, payload)

    def test_series_lengths_and_definition(self):
        r = self._run(synthetic_year(days=5, start_day=180, t_mean=24, t_amp=0))
        self.assertEqual(len(r['t_op']), len(r['t_air']))
        self.assertEqual(len(r['t_mrt']), len(r['t_air']))
        for a, m, o in zip(r['t_air'], r['t_mrt'], r['t_op']):
            self.assertAlmostEqual(o, 0.5 * (a + m), places=9)

    def test_steady_state_mrt_between_air_and_outside_in_winter(self):
        cold = [{'t_ext': 0.0, 'sun_azimuth': 0, 'sun_elevation': -10, 'e_dir': 0, 'e_dif': 0}] * 300
        env = box_with_window(w=10, d=8, h=3, win_ratio_south=0.35, wall_id=1, roof_id=2, floor_id=3, glass_id=4)
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 20.0, 'shadow_mode': 'precomputed', 't_ground': 0.0, 'weather': cold,
                   'interior': {'mode': 'imposed', 'h_i': 7.7, 't_int': 20.0}}
        r = building_solver.run_building_simulation(env, LAYERS, None, payload)
        self.assertLess(r['t_mrt'][-1], 20.0)       # parois plus froides que l'air l'hiver
        self.assertGreater(r['t_mrt'][-1], 15.0)    # mais pas loin, l'enveloppe est isolée
        self.assertLess(r['t_op'][-1], 20.0)

    def test_comfort_indicators_use_operative_temperature(self):
        r = self._run(synthetic_year(days=10, start_day=185, t_mean=27, t_amp=0))
        ind_air = calc_runs.comfort_hours(r['t_air'][1:])
        ind = calc_runs.indicators_from({**r, 'heating_kwh': 0, 'cooling_kwh': 0, 't_air_mean': 25}, 80)
        self.assertEqual(ind['hours_over_28'], calc_runs.comfort_hours(r['t_op'][1:])['hours_over_28'])
        self.assertIsInstance(ind_air['hours_over_28'], int)
