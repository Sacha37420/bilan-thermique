"""Validation physique (chantier bt-validation-physique, phase 3) — invariants : monotonie, symétries, conservation.

Chaque sens est joué sur chaque niveau qui le modélise (N0 : chauffage seul ; N1 et N2 : chauffage et confort d'été).
Météo synthétique sans réseau (`testing.synthetic_year`) ; le N2 tourne sur une boîte maillée courte (été ou hiver).
"""

import numpy as np
from django.test import SimpleTestCase

from .. import building_solver
from ..engines import n0, n1
from ..testing import box_with_window, synthetic_year
from .test_validation_analytique import WALL, n2_payload, params



def dated(start_day, days):
    """Année synthétique à partir de `start_day`, indice horaire recalé sur la date (piège : `synthetic_year` numérote
    ses heures depuis 0 quel que soit `start_day`, donc tout ce qui lit le mois y verrait janvier)."""
    w = synthetic_year(days=days, start_day=start_day)
    for p in w:
        p['hour_index'] += 24 * start_day
    return w


YEAR = synthetic_year()
SUMMER = dated(170, 60)
WINTER = synthetic_year(days=45, start_day=0)
GLASS = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.85, 'r': 0.08, 'alpha': 0.07}]


def p_ref(**over):
    kw = dict(gains_w_m2=6.0, n_vent=0.4, g_window=0.6, absorptance=0.6)
    kw.update(over)
    return params(**kw)


def heat(level, p, weather=YEAR):
    return (n0.run if level == 'N0' else n1.run_one)(p, weather)['heating_kwh']


def walls(u=0.5, uw=2.0, win=4.0):
    return {o: {'opaque': 20.0, 'window': win, 'u': u, 'uw': uw} for o in 'NESO'}


class MonotonyN0N1Test(SimpleTestCase):
    def test_more_insulation_less_heating(self):
        for level in ('N0', 'N1'):
            with self.subTest(level=level):
                hs = [heat(level, p_ref(walls=walls(u=u), u_roof=u * 0.6)) for u in (1.5, 0.8, 0.3)]
                self.assertTrue(hs[0] > hs[1] > hs[2], hs)

    def test_colder_climate_more_heating(self):
        cold = synthetic_year(t_mean=8.0)
        for level in ('N0', 'N1'):
            with self.subTest(level=level):
                self.assertGreater(heat(level, p_ref(), cold), heat(level, p_ref()))

    def test_higher_g_less_heating_more_discomfort(self):
        lo, hi = n1.run_one(p_ref(g_window=0.3), YEAR), n1.run_one(p_ref(g_window=0.7), YEAR)
        self.assertLess(hi['heating_kwh'], lo['heating_kwh'])
        self.assertGreater(hi['confort']['dh_re2020'], lo['confort']['dh_re2020'])
        self.assertLess(heat('N0', p_ref(g_window=0.7)), heat('N0', p_ref(g_window=0.3)))

    def test_night_ventilation_and_protection_reduce_discomfort(self):
        base = n1.run_one(p_ref(g_window=0.7, gains_w_m2=10.0), SUMMER)['confort']['dh_re2020']
        night = n1.run_one(p_ref(g_window=0.7, gains_w_m2=10.0, night_vent=3.0, night_vent_threshold=24.0),
                           SUMMER)['confort']['dh_re2020']
        from ..engines import shading
        shade = n1.run_one(p_ref(g_window=0.7, gains_w_m2=10.0, devices=[
            {'kind': 'store_ext', 'orientation': 'all', 'params': shading.validate_params('store_ext', {}),
             'control': {'mode': 'auto', 'irradiance_w_m2': 200, 't_ext_min_c': 20}}]), SUMMER)['confort']['dh_re2020']
        self.assertGreater(base, 50.0)
        self.assertLess(night, 0.9 * base)
        self.assertLess(shade, base)


class SymmetryN1Test(SimpleTestCase):
    def test_east_west_swap_swaps_profiles(self):
        """Une baie à l'est seule puis à l'ouest seule : gains journaliers égaux, profils horaires miroirs autour du midi
        solaire (la météo synthétique est symétrique autour de son midi solaire, à l'heure près)."""
        def only(o):
            w = {k: {'opaque': 20.0, 'window': 0.0, 'u': 0.5, 'uw': 2.0} for k in 'NESO'}
            w[o]['window'] = 6.0
            return p_ref(walls=w)
        from ..engines import solar
        t_e, az, el, dni, dhi, hidx = solar.arrays(SUMMER[:24 * 7])
        e = n1.envelope_terms(only('E'), az, el, dni, dhi)['win']
        o = n1.envelope_terms(only('O'), az, el, dni, dhi)['win']
        self.assertAlmostEqual(e.sum(), o.sum(), delta=0.02 * e.sum())
        self.assertLess(int(np.argmax(e[:24])), 12)
        self.assertGreater(int(np.argmax(o[:24])), 12)

    def test_symmetric_building_rotation_invariant(self):
        """Mêmes murs et baies sur les quatre façades : tourner de 180° (N↔S, E↔O) ne change rien."""
        w = walls()
        rot = {'N': w['S'], 'S': w['N'], 'E': w['O'], 'O': w['E']}
        a, b = n1.run_one(p_ref(walls=w), YEAR), n1.run_one(p_ref(walls=rot), YEAR)
        self.assertAlmostEqual(a['heating_kwh'], b['heating_kwh'], delta=1e-6 * a['heating_kwh'])


def n2_run(weather, wall=WALL, glass=GLASS, gains=1500.0, debit=60.0, **payload_extra):
    env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=1, floor_id=1, glass_id=2, win_ratio_south=0.3)
    payload = n2_payload(weather, t_min=19.0, apports_internes_w=gains, debit_vent_m3h=debit)
    payload['t_ground'] = 12.0
    payload.update(payload_extra)
    res = building_solver.run_building_simulation(env, {1: wall, 2: glass}, None, payload)
    t_op = np.asarray(res['t_op'][1:])
    return res, float(np.clip(t_op - 26.0, 0, None).sum())


class MonotonyN2Test(SimpleTestCase):
    def test_insulation_heating(self):
        thin = [dict(WALL[0], e=0.03), WALL[1]]
        h_thin, _ = n2_run(WINTER, wall=thin)
        h_ref, _ = n2_run(WINTER)
        self.assertGreater(h_thin['heating_kwh'], h_ref['heating_kwh'])

    def test_g_summer(self):
        lo = [dict(GLASS[0], tau=0.4, alpha=0.52)]
        _r, dh_lo = n2_run(SUMMER, glass=lo)
        _r, dh_hi = n2_run(SUMMER)
        self.assertGreater(dh_lo, 10.0)
        self.assertGreater(dh_hi, dh_lo)

    def test_ventilation_summer(self):
        _r, dh_lo = n2_run(SUMMER, debit=40.0)
        _r, dh_hi = n2_run(SUMMER, debit=400.0)
        self.assertGreater(dh_lo, 10.0)
        self.assertLess(dh_hi, dh_lo)


class SolarToFloorOptionTest(SimpleTestCase):
    """Option d'essai E3 (`solaire_transmis: 'plancher'`) : l'énergie transmise est conservée — même total entrant,
    bilan du nœud d'air toujours fermé, et la pointe d'air baisse (la masse du plancher l'absorbe d'abord)."""

    def test_conservation_and_peak(self):
        air, _ = n2_run(SUMMER[:24 * 10])
        floor, _ = n2_run(SUMMER[:24 * 10], solaire_transmis='plancher')
        b_air, b_floor = air['balance'], floor['balance']
        self.assertAlmostEqual(b_floor['solar_transmitted_floor_kwh'], b_air['solar_transmitted_kwh'],
                               delta=1e-6 * b_air['solar_transmitted_kwh'])
        self.assertEqual(b_floor['solar_transmitted_kwh'], 0.0)
        self.assertLess(abs(b_floor['closure_error_kwh']), 1e-6)
        self.assertLess(max(floor['t_air']), max(air['t_air']))
