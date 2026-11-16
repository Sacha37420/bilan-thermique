"""Lot 36 (chantier bt-potentiels, I2) — chaque élément d'une amélioration (faits I0, surcharges, protections) a
l'effet de signe et d'ordre de grandeur attendu dans chaque niveau où la table § 7 le déclare applicable, et il est
décrit dans les entrées figées du calcul (`inputs['ameliorations']`)."""

import copy
from unittest import mock

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_service, facts
from ..engines import inputs, n0, n1, shading, solar, surcharges, vegetation
from ..models import Building, Job, Scenario, ShadingDevice
from ..testing import box_with_window, synthetic_year, without_auto_environment
from .test_moteurs import FLOOR, GLASS, ROOF, WALL

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


YEAR = synthetic_year()
SUMMER = synthetic_year(days=20, start_day=190, t_mean=24.0, t_amp=0.0)
BASE = {'identite.surface_m2': 100.0, 'identite.usage': 'maison', 'identite.niveaux': 1, 'identite.hauteur_m': 3.0,
        'mur.u': 0.5, 'toiture.u': 0.3, 'plancher.u': 0.4, 'baies.uw': 1.8, 'baies.g': 0.6,
        'usage.consigne_chauffage_c': 19.0, **{f'baies.ratio_{o}': 0.2 for o in 'NESO'}}


def params(**kw):
    return inputs.from_fiche({**BASE, **kw})


def terms(p, weather=YEAR):
    t_e, az, el, dni, dhi, hidx = solar.arrays(weather)
    return n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)


class N01Test(SimpleTestCase):
    def test_albedo_hand_value(self):
        """Mur vertical : réfléchi = ρ·GHI·(1 − cos 90°)/2 ; ρ de 0,20 à 0,60 → +0,20·ΣGHI sur chaque façade."""
        e0, e1 = terms(params()), terms(params(**{'abords.albedo': 0.6}))
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        ghi = (dni * np.clip(np.sin(el), 0, None) + dhi).sum()
        self.assertAlmostEqual(float(e1['irr']['S'].sum() - e0['irr']['S'].sum()), 0.20 * ghi, delta=1e-6 * ghi)
        r = n1.run([params(), params(**{'abords.albedo': 0.6})], YEAR, keep_series=False)
        self.assertLess(r[1]['heating_kwh'], r[0]['heating_kwh'])            # plus d'apports l'hiver

    def test_roof_absorptance_separate(self):
        a, b = params(**{'enveloppe.couleur_absorptance': 0.7}), params(**{'enveloppe.couleur_absorptance': 0.7,
                                                                             'toiture.absorptance': 0.2})
        self.assertEqual(a.alpha_roof, 0.7)
        self.assertEqual(b.alpha_roof, 0.2)
        self.assertEqual(b.alpha_wall('S'), 0.7)
        ea, eb = terms(a, SUMMER), terms(b, SUMMER)
        t_e, az, el, dni, dhi, hidx = solar.arrays(SUMMER)
        roof_irr = solar.surface_irradiance(az, el, dni, dhi, 180.0, 0.0)[0]
        expected = 0.5 * n1.R_SE * a.u_roof * a.roof_area * roof_irr.sum()      # Δα = 0,5
        self.assertAlmostEqual(float(ea['opq'].sum() - eb['opq'].sum()), expected, delta=1e-6 * expected)

    def test_green_roof_u_alpha_evapo(self):
        p = params(**{'toiture.vegetalisee': True})
        p.green_roof = {'substrat_m': 0.10, 'lai': 3.0, 'part': 1.0}
        # 1/U = 1/0,3 + 0,10/0,40 → U = 0,2791 W/m²·K
        self.assertAlmostEqual(p.u_roof_eff, 1 / (1 / 0.3 + 0.25), places=4)
        self.assertEqual(p.alpha_roof, vegetation.ALPHA_FOLIAGE)
        # Ordre de grandeur : 600 W/m² absorbés à 25 °C, LAI 3 → LE ≈ 195 W/m² (≈ 0,29 mm/h).
        le = float(vegetation.latent_flux(600.0, 25.0, 3.0))
        self.assertAlmostEqual(le, 195.0, delta=5.0)
        e_n1 = terms(p, SUMMER)
        t_e, az, el, dni, dhi, hidx = solar.arrays(SUMMER)
        e_n0 = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx, evapo=False)
        self.assertLess(e_n1['opq'].sum(), e_n0['opq'].sum())                   # puits en N1, pas en N0
        r = n1.run([params(), p], SUMMER, t_init=24.0)
        self.assertLess(r[1]['confort']['dh_re2020'], r[0]['confort']['dh_re2020'])

    def test_green_walls_only_chosen_orientations(self):
        p = params()
        p.green_walls = {'orientations': ['S'], 'lai': 2.0, 'part': 0.5}
        self.assertAlmostEqual(p.u_wall_eff('S'), 0.5 / (1 / 0.5 + 0.09) + 0.5 * 0.5, places=4)
        self.assertEqual(p.u_wall_eff('N'), 0.5)
        self.assertLess(p.h_tr_op_air, params().h_tr_op_air)

    def test_trees_seasonal_beam_factor(self):
        """Arbre à 6 m au sud de la façade sud, transmission 0,2 l'été et 0,7 l'hiver : la façade sud reçoit moins,
        et la perte d'apports d'été dépasse, rapportée au direct, celle d'hiver ; le nord ne change pas."""
        p = params()
        tree = {'x_m': 5.0, 'y_m': -6.0, 'hauteur_m': 9.0, 'rayon_houppier_m': 3.0, 'tronc_m': 2.0,
                'persistant': False, 'transmission_ete': 0.2, 'transmission_hiver': 0.7}
        q = copy.deepcopy(p)
        surcharges.apply_to_params(q, {'arbres': [tree]}, BASE, {'footprint_xy': [[0, 0], [10, 0], [10, 10], [0, 10]]})
        self.assertTrue(q.facade_points['S'])
        e0, e1 = terms(p), terms(q)
        self.assertLess(e1['irr']['S'].sum(), e0['irr']['S'].sum())
        self.assertAlmostEqual(float(e1['irr']['N'].sum()), float(e0['irr']['N'].sum()), places=3)
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        months = shading.month_of(hidx)
        f = vegetation.tree_beam_factor(q.facade_points['S'], [tree], az, el, months)
        summer = np.isin(months, (6, 7, 8))
        winter = np.isin(months, (12, 1, 2))
        self.assertGreaterEqual(float(f.min()), 0.2 - 1e-9)
        self.assertLess(float(f[summer & (el > 0)].mean()), 1.0)
        self.assertGreater(float(f[winter & (f < 1)].min()), 0.69)              # hors feuilles : 0,7

    def test_air_cooling_hypothesis(self):
        s = surcharges.clean({'rafraichissement_air': {'delta_c': 1.5, 'mois': [7], 'heures': [14]}})[0]
        w = surcharges.corrected_weather(YEAR, s)
        july_14 = (181 + 3) * 24 + 14                                         # 4 juillet, 14 h
        self.assertAlmostEqual(w[july_14]['t_ext'], YEAR[july_14]['t_ext'] - 1.5)
        self.assertEqual(w[july_14 + 1]['t_ext'], YEAR[july_14 + 1]['t_ext'])
        self.assertEqual(w[14]['t_ext'], YEAR[14]['t_ext'])                    # janvier : inchangé
        self.assertIn('Hypothèse', s['rafraichissement_air']['hypothese'])
        full = surcharges.clean({'rafraichissement_air': {'delta_c': 2.0, 'mois': [6, 7, 8],
                                                          'heures': list(range(10, 20))}})[0]
        r1 = n1.run_one(params(), YEAR)
        r2 = n1.run_one(params(), surcharges.corrected_weather(YEAR, full))
        self.assertLess(r2['confort']['dh_re2020'], r1['confort']['dh_re2020'] + 1e-9)
        self.assertLess(r2['t_op_max'], r1['t_op_max'])

    def test_night_ventilation_n1_not_n0(self):
        base, nv = params(), params(**{'ventilation.nocturne_vol_h': 4.0, 'ventilation.nocturne_seuil_c': 22.0})
        r = n1.run([base, nv], YEAR)
        self.assertLess(r[1]['confort']['dh_re2020'], r[0]['confort']['dh_re2020'])
        self.assertGreater(r[1]['surventilation_nocturne_h'], 100)
        # Déclenchée seulement au-dessus de 24 °C dedans : le chauffage ne bouge presque pas.
        self.assertLess(abs(r[1]['heating_kwh'] - r[0]['heating_kwh']), 0.01 * r[0]['heating_kwh'] + 1.0)
        self.assertEqual(n0.run(base, YEAR)['heating_kwh'], n0.run(nv, YEAR)['heating_kwh'])

    def test_bad_surcharges_rejected_and_said(self):
        ok, rejected = surcharges.clean({'arbres': [{'x_m': 'a'}], 'toiture_vegetalisee': {'lai': 99},
                                         'murs_vegetalises': {'orientations': ['X']}, 'foo': 1,
                                         'rafraichissement_air': {'delta_c': 9}})
        self.assertEqual(ok, {})
        self.assertEqual({r['cle'] for r in rejected},
                         {'arbres', 'toiture_vegetalisee', 'murs_vegetalises', 'foo', 'rafraichissement_air'})


class N2Test(SimpleTestCase):
    LAYERS = {1: WALL, 2: ROOF, 3: FLOOR, 4: GLASS}

    def run_n2(self, env=None, weather=SUMMER, **kw):
        env = env or box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4, win_ratio_south=0.3)
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 24.0, 'shadow_mode': 'precomputed', 't_ground': 14.0,
                   'weather': weather, 'interior': {'mode': 'free', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 200 * 5,
                                                    'debit_vent_m3h': 60.0, 'apports_internes_w': 300.0}}
        payload.update(kw.pop('payload', {}))
        return building_solver.run_building_simulation(env, self.LAYERS, None, payload, **kw), env

    def test_albedo_constant_warms(self):
        r0, _ = self.run_n2()
        r1, _ = self.run_n2(ground_albedo_constant=0.6)
        self.assertGreater(np.mean(r1['t_air']), np.mean(r0['t_air']))

    def test_evapo_sink_cools_roof(self):
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4)
        r0, _ = self.run_n2(copy.deepcopy(env))
        for t in env['triangles']:
            if t.get('group') == 'toiture':
                t['evapo'] = vegetation.latent_coefficient(3.0, 1.0)
        r1, _ = self.run_n2(env)
        self.assertLess(np.mean(r1['t_air']), np.mean(r0['t_air']))
        self.assertLess(np.mean(r1['par_triangle']['solaire_ete_kwh']), np.mean(r0['par_triangle']['solaire_ete_kwh']) + 1e-9)

    def test_night_ventilation_n2(self):
        w = [dict(p, nocturne=(p['hour_index'] % 24 >= 22 or p['hour_index'] % 24 < 7)) for p in SUMMER]
        r0, _ = self.run_n2(weather=w)
        r1, _ = self.run_n2(weather=w, payload={'nocturne': {'debit_m3h': 1000.0}})
        self.assertLess(np.mean(r1['t_air']), np.mean(r0['t_air']))
        self.assertLess(r1['balance']['ventilation_kwh'], r0['balance']['ventilation_kwh'])
        self.assertLess(abs(r1['balance']['closure_error_kwh']), 0.05)

    def test_window_factors_and_delta_r(self):
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=2, floor_id=3, glass_id=4, win_ratio_south=0.3)
        glass = [i for i, t in enumerate(env['triangles']) if t['paroi_model_id'] == 4]
        T = len(SUMMER)
        r0, _ = self.run_n2(env)
        closed = {'baies_facteurs': [{'triangles': glass, 'beam': [0.1] * T, 'diffuse': [0.3] * T, 'r_add': [0.0] * T}]}
        r1, _ = self.run_n2(env, payload=closed)
        self.assertLess(np.mean(r1['t_air']), np.mean(r0['t_air']))
        cold = synthetic_year(days=10, start_day=10, t_mean=2.0, t_amp=0.0)
        Tc = len(cold)
        payload = {'interior': {'mode': 'thermostat', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 200 * 5, 't_min': 19.0,
                                't_max': 60.0, 'debit_vent_m3h': 60.0}}
        rw0, _ = self.run_n2(env, weather=cold, payload=payload)
        rw1, _ = self.run_n2(env, weather=cold, payload={**payload, 'baies_facteurs': [
            {'triangles': glass, 'beam': [1.0] * Tc, 'diffuse': [1.0] * Tc, 'r_add': [0.2] * Tc}]})
        self.assertLess(rw1['heating_kwh'], rw0['heating_kwh'])
        with self.assertRaises(building_solver.BuildingSimulationError):
            self.run_n2(env, payload={'baies_facteurs': [{'triangles': glass, 'beam': [1.0], 'diffuse': [1.0],
                                                          'r_add': [0.0]}]})

    def test_tree_meshes_translucent(self):
        m_e = vegetation.tree_meshes([{'x_m': 0, 'y_m': -8, 'hauteur_m': 9, 'rayon_houppier_m': 3, 'tronc_m': 2,
                                       'transmission_ete': 0.2, 'transmission_hiver': 0.7}], 'ete')
        m_h = vegetation.tree_meshes([{'x_m': 0, 'y_m': -8, 'hauteur_m': 9, 'rayon_houppier_m': 3, 'tronc_m': 2,
                                       'transmission_ete': 0.2, 'transmission_hiver': 0.7}], 'hiver')
        self.assertEqual({t['k'] for t in m_e['triangles']}, {0.2})
        self.assertEqual({t['k'] for t in m_h['triangles']}, {0.7})
        self.assertEqual(len({t['obj'] for t in m_e['triangles']}), 1)


def fiche(**over):
    vals = {**BASE, 'identite.emprise_m2': 100.0, 'toiture.type': 'terrasse', 'plancher.type': 'terre_plein',
            'ventilation.type': 'vmc_sf_auto', 'ventilation.debit_vol_h': 0.5, 'usage.apports_w_m2': 4.0}
    vals.update(over)
    return vals


HOT = synthetic_year(t_mean=15.0, t_amp=10.0)    # été chaud (≈ 25 °C en juillet) : de l'inconfort à réduire


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': HOT, 'source': 'test'})


class EndToEndTest(TestCase):
    """Une amélioration complète, calculée en N0, N1 et N2 depuis la fiche : effets et entrées figées."""

    def setUp(self):
        self.b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69,
                                         pipeline={'footprint_xy': [[0, 0], [10, 0], [10, 10], [0, 10]], 'neighbours': []})
        for k, v in fiche().items():
            facts.record(self.b, k, v, 'infere', refresh=False)
        facts.refresh_scores(self.b)
        self.s = Scenario.objects.create(building=self.b, name='été', surcharges={
            'arbres': [{'x_m': 5.0, 'y_m': -7.0, 'hauteur_m': 10.0, 'rayon_houppier_m': 3.5, 'tronc_m': 2.0,
                        'persistant': False, 'transmission_ete': 0.2, 'transmission_hiver': 0.7}],
            'toiture_vegetalisee': {'substrat_m': 0.12, 'lai': 3.0, 'part': 1.0},
            'rafraichissement_air': {'delta_c': 1.0, 'mois': [6, 7, 8], 'heures': list(range(12, 19)),
                                     'hypothese': 'Hypothèse : −1 °C l’après-midi par la végétalisation.'}})
        for k, v in (('ventilation.nocturne_vol_h', 3.0), ('toiture.absorptance', 0.3), ('toiture.vegetalisee', True)):
            facts.record(self.b, k, v, 'declare', scenario=self.s, refresh=False)
        ShadingDevice.objects.create(building=self.b, scenario=self.s, kind='store_ext', orientation='S',
                                     params={}, control={'mode': 'auto', 'irradiance_w_m2': 200, 't_ext_min_c': 20})

    def test_n0_n1_effects_and_inputs(self):
        with tmy():
            base1, _ = calc_service.run_level(self.b, 'N1', uncertainty=False)
            imp1, _ = calc_service.run_level(self.b, 'N1', scenario=self.s, uncertainty=False)
            imp0, _ = calc_service.run_level(self.b, 'N0', scenario=self.s)
        self.assertGreater(base1.indicators['dh_re2020'], 0)
        self.assertLess(imp1.indicators['dh_re2020'], base1.indicators['dh_re2020'])
        self.assertEqual(len(imp1.results['mensuel']['dh_re2020']), 12)
        self.assertEqual(base1.inputs['ameliorations'], [])
        am = {(e['type'], e['cle']): e for e in imp1.inputs['ameliorations']}
        self.assertTrue(am[('fait', 'ventilation.nocturne_vol_h')]['appliquee'])
        self.assertTrue(am[('surcharge', 'arbres')]['appliquee'])
        self.assertEqual(am[('surcharge', 'rafraichissement_air')]['hypothese'],
                         'Hypothèse : −1 °C l’après-midi par la végétalisation.')
        self.assertTrue(am[('protection', 'store_ext')]['appliquee'])
        am0 = {(e['type'], e['cle']): e for e in imp0.inputs['ameliorations']}
        self.assertFalse(am0[('fait', 'ventilation.nocturne_vol_h')]['appliquee'])
        self.assertIsNone(imp0.indicators['dh_re2020'])
        self.assertEqual(imp1.inputs['params']['absorptance_toiture'], vegetation.ALPHA_FOLIAGE)
        self.assertEqual(imp1.inputs['params']['arbres_plantes'], 1)
        # La protection de l'amélioration n'est pas dans l'état actuel.
        self.assertEqual(base1.inputs['params']['protections'], [])
        self.assertEqual(len(imp1.inputs['params']['protections']), 1)

    def test_n2_effects_inputs_and_triangles(self):
        with tmy():
            base, _ = calc_service.start_n2(self.b, owner_sub='g')
            base = calc_service.run_n2(base)
            Job.objects.filter(pk=base.job_id).update(status=Job.DONE)      # libère le verrou du lab
            imp, _ = calc_service.start_n2(self.b, scenario=self.s, owner_sub='g')
            imp = calc_service.run_n2(imp)
        self.assertGreater(base.indicators['dh_re2020'], 0)
        self.assertLess(imp.indicators['dh_re2020'], base.indicators['dh_re2020'])
        self.assertLess(imp.results['t_op_max'], base.results['t_op_max'])
        am = {(e['type'], e['cle']): e for e in imp.inputs['ameliorations']}
        self.assertTrue(am[('surcharge', 'toiture_vegetalisee')]['appliquee'])
        self.assertEqual(imp.inputs['arbres_plantes'], 1)
        self.assertEqual(imp.inputs['protections']['par_triangle'], [ShadingDevice.objects.get(scenario=self.s).pk])
        self.assertEqual(imp.inputs['parois']['toiture']['layers'][0]['lam'], vegetation.SUBSTRATE_LAMBDA)
        pt = imp.results['par_triangle']
        self.assertEqual(len(pt['grandeurs']['pertes_hiver_kwh']), len(pt['triangles']))
        self.assertIn('vitrage', pt['type'])
