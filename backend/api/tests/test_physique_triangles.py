"""Lot 36 (chantier bt-potentiels, I4) — cumuls N2 par triangle : leur somme égale le flux d'enveloppe du bilan ;
forme de `GET /api/calculs/<id>/triangles/`, droits et 404 fixe."""

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_runs
from ..models import Building, CalcRun, Job, Portfolio, PortfolioMember
from ..testing import box_with_window, client_for, patch_jwks, synthetic_year, without_auto_environment
from .test_moteurs import FLOOR, GLASS, ROOF, WALL

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


LAYERS = {1: WALL, 2: ROOF, 3: FLOOR, 4: GLASS}
# Mars à août (jours 60 à 240) : couvre une partie de l'hiver (mars, avril) et tout l'été (juin–août).
WEATHER = synthetic_year(days=180, start_day=60)


def run(weather=WEATHER):
    env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4, win_ratio_south=0.3)
    payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 19.0, 'shadow_mode': 'precomputed', 't_ground': 12.0,
               'weather': weather, 'interior': {'mode': 'thermostat', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 200 * 5,
                                                't_min': 19.0, 't_max': 27.0, 'debit_vent_m3h': 60.0,
                                                'apports_internes_w': 300.0}}
    return building_solver.run_building_simulation(env, LAYERS, None, payload), env


class CumulsTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.res, cls.env = run()

    def test_sum_equals_envelope_flux(self):
        pt, flux = self.res['par_triangle'], np.asarray(self.res['envelope_flux_w'])
        kwh = 3600.0 / 3.6e6
        self.assertAlmostEqual(sum(pt['net_kwh']), flux.sum() * kwh, delta=1e-6 * abs(flux).sum() * kwh + 1e-9)
        self.assertAlmostEqual(sum(pt['net_kwh']), self.res['balance']['envelope_kwh'], delta=1e-6 + 1e-6 *
                               abs(self.res['balance']['envelope_kwh']))
        months = [building_solver._month_of_point(p, i) for i, p in enumerate(WEATHER)]
        for period, sel in building_solver.PERIODS.items():
            mask = np.isin(months, sel)
            expected = flux[mask].sum() * kwh
            got = sum(pt[f'gain_{period}_kwh']) - sum(pt[f'loss_{period}_kwh'])
            with self.subTest(period=period):
                self.assertAlmostEqual(got, expected, delta=1e-6 * abs(flux[mask]).sum() * kwh + 1e-9)
                self.assertEqual(pt['heures'][period], int(mask.sum()))

    def test_signs_and_magnitudes(self):
        pt = self.res['par_triangle']
        glass = [i for i, t in enumerate(self.env['triangles']) if t['paroi_model_id'] == 4]
        wall = [i for i, t in enumerate(self.env['triangles']) if t['paroi_model_id'] == 1]
        # Le vitrage perd plus par m² que les murs isolés l'hiver (U ≈ 5,6 contre 0,37 W/m²·K).
        a = [t['area'] for t in self.env['triangles']]
        per_m2 = lambda idx: sum(pt['loss_hiver_kwh'][i] for i in idx) / sum(a[i] for i in idx)
        self.assertGreater(per_m2(glass), 5 * per_m2(wall))
        self.assertTrue(all(v >= 0 for v in pt['solaire_ete_kwh']))
        self.assertGreater(max(pt['solaire_ete_kwh']), 0)
        self.assertTrue(all(v is not None for v in pt['t_si_ete_max_c']))

    def test_i4_format(self):
        out = calc_runs.triangle_results(self.env, LAYERS, self.res['par_triangle'])
        self.assertEqual(set(out), {'repere', 'vertices', 'triangles', 'groupes', 'type', 'periodes', 'grandeurs', 'unites'})
        self.assertEqual(set(out['grandeurs']), {'pertes_hiver_kwh_m2', 'pertes_hiver_kwh', 'gains_ete_kwh_m2',
                                                 'gains_ete_kwh', 'solaire_ete_kwh_m2', 't_si_hiver_moy_c',
                                                 't_si_ete_max_c'})
        n = len(self.env['triangles'])
        self.assertTrue(all(len(v) == n for v in out['grandeurs'].values()))
        self.assertEqual(out['type'].count('vitrage'), 2)
        self.assertEqual(out['periodes'], {'hiver': [10, 11, 12, 1, 2, 3, 4], 'ete': [6, 7, 8]})
        v = out['grandeurs']['pertes_hiver_kwh'][0]
        self.assertEqual(v, float(f'{v:.3g}'))
        i = 18   # vitrage
        self.assertAlmostEqual(out['grandeurs']['pertes_hiver_kwh_m2'][i],
                               self.res['par_triangle']['loss_hiver_kwh'][i] / self.env['triangles'][i]['area'], delta=0.01 *
                               out['grandeurs']['pertes_hiver_kwh_m2'][i])


@patch_jwks()
class RouteTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='p', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role='lecteur')
        self.b = Building.objects.create(name='b', portfolio=self.p)
        res, env = run(synthetic_year(days=3, start_day=0))
        self.n2 = CalcRun.objects.create(building=self.b, level='N2', status=Job.DONE, results={
            'par_triangle': calc_runs.triangle_results(env, LAYERS, res['par_triangle'])})
        self.n1 = CalcRun.objects.create(building=self.b, level='N1', status=Job.DONE, results={})

    def test_reader_reads_outsider_404(self):
        r = client_for('l').get(f'/api/calculs/{self.n2.pk}/triangles/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['repere'], 'batiment')
        self.assertEqual(client_for('x').get(f'/api/calculs/{self.n2.pk}/triangles/').status_code, 404)

    def test_not_n2_or_missing(self):
        for pk in (self.n1.pk, 999999):
            r = client_for('g').get(f'/api/calculs/{pk}/triangles/')
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r.json(), {'detail': 'Pas de résultats par triangle pour ce calcul.'})
