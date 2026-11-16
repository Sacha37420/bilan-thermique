"""Lot 32 — sol simulé en 1D (conduction transitoire d'après la météo) et couplage du plancher bas : terre-plein,
vide sanitaire, sous-sol non chauffé, local chauffé dessous ; branché dans N0, N1 et N2."""

import math

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import calc_service, facts
from ..engines import inputs as engine_inputs
from ..engines import n0, n1, sol
from ..models import CalcRun, Job
from ..testing import without_auto_environment
from .test_n2_fiche import fiche, make_building, tmy

HOURS = 8760


def annual_wave(mean=11.0, amp=8.0):
    t = np.arange(HOURS)
    return mean + amp * np.sin(2 * math.pi * t / HOURS)


class SoilColumnTest(SimpleTestCase):
    def test_matches_the_analytic_annual_wave(self):
        """Onde annuelle : amplitude × e^(−z/d), retard z/d (d = √(2a/ω) ≈ 2,74 m), à la surface près."""
        t_ext = annual_wave()
        prof = sol.soil_profile(t_ext)
        a = sol.SOIL_LAMBDA / sol.SOIL_RHO_C
        omega = 2 * math.pi / (HOURS * 3600.0)
        d = math.sqrt(2 * a / omega)
        self.assertAlmostEqual(d, 2.74, delta=0.02)
        # Atténuation par l'échange de surface (h fini) : |1 / (1 + (1 + i) λ / (h d))|.
        surf = 1.0 / abs(1 + (1 + 1j) * sol.SOIL_LAMBDA / (sol.H_SURFACE * d))
        for z in (1.0, d, 5.0):
            s = sol.at_depth(prof, z)
            amp = (s.max() - s.min()) / 2
            self.assertAlmostEqual(amp, 8.0 * surf * math.exp(-z / d), delta=0.06 * 8.0 * math.exp(-z / d) + 0.05, msg=z)
            lag_h = (int(np.argmax(s)) - int(np.argmax(t_ext))) % HOURS
            self.assertAlmostEqual(lag_h / 24.0, (z / d) / omega / 86400.0, delta=6.0, msg=z)  # jours
            self.assertAlmostEqual(float(s.mean()), 11.0, delta=0.15)

    def test_deep_soil_is_stable(self):
        prof = sol.soil_profile(annual_wave())
        deep = sol.at_depth(prof, 12.0)
        self.assertLess(deep.max() - deep.min(), 0.3)


class FloorCouplingTest(SimpleTestCase):
    def setUp(self):
        self.t_ext = annual_wave()
        self.t_in = np.full(HOURS, 19.0)

    def seen(self, kind, **kw):
        t, _u, desc = sol.floor_boundary(kind, 100.0, 40.0, 0.8, self.t_ext, self.t_in, **kw)
        return t, desc

    def test_slab_on_ground_sees_damped_soil(self):
        t, desc = self.seen('terre_plein')
        self.assertEqual(desc['b_prime_m'], 5.0)                 # 100 / (½ × 40)
        self.assertEqual(desc['profondeur_m'], 2.5)
        self.assertLess(t.max() - t.min(), 0.5 * (self.t_ext.max() - self.t_ext.min()))
        small, _u, d2 = sol.floor_boundary('terre_plein', 9.0, 40.0, 0.8, self.t_ext, self.t_in)   # B′ = 0,45 m
        self.assertEqual(d2['profondeur_m'], sol.Z_MIN_M)           # petit plancher : sol peu profond, plus variable
        self.assertGreater(small.max() - small.min(), t.max() - t.min())

    def test_crawl_space_and_basement_lie_between_outside_and_inside(self):
        crawl, _ = self.seen('vide_sanitaire')
        base, desc = self.seen('sous_sol', basement_depth_m=3.0)
        self.assertEqual(desc['profondeur_m'], 3.0)
        cold = int(0.75 * HOURS)                                  # creux de l'onde sinusoïdale
        winter = slice(cold - 24 * 15, cold + 24 * 15)
        for t in (crawl, base):
            self.assertTrue((t >= self.t_ext.min() - 0.5).all() and (t <= 19.0).all())
        # Le sous-sol, enterré et peu ventilé, varie moins que le vide sanitaire ventilé.
        self.assertLess(base.max() - base.min(), crawl.max() - crawl.min())
        self.assertGreater(base[winter].min(), self.t_ext[winter].min())

    def test_equivalent_u_is_kept_on_average(self):
        """`plancher.u` = U équivalent vers l'extérieur (« final » du DPE) : en moyenne annuelle, le flux du modèle
        (U propre × (T_int − T_nœud)) vaut Ue × (T_int − T_ext moyen) — le local tampon n'est pas compté deux fois."""
        for kind in ('vide_sanitaire', 'sous_sol'):
            t, u_node, desc = sol.floor_boundary(kind, 100.0, 40.0, 0.8, self.t_ext, self.t_in)
            self.assertGreater(u_node, 0.8)
            flux = u_node * (19.0 - t).mean()
            self.assertAlmostEqual(flux, 0.8 * (19.0 - self.t_ext.mean()), delta=0.06 * 0.8 * 8.0, msg=kind)
            self.assertEqual(desc['u_equivalent'], 0.8)

    def test_impossible_equivalent_u_is_capped(self):
        u, note = sol.floor_u_node('vide_sanitaire', 100.0, 40.0, 3.0)
        self.assertEqual(u, sol.U_NODE_MAX)
        self.assertIn('plafonné', note)
        self.assertEqual(sol.floor_u_node('terre_plein', 100.0, 40.0, 3.0), (3.0, None))

    def test_heated_space_below(self):
        t, _ = self.seen('local_chauffe')
        self.assertTrue((t == 19.0).all())


class EnginesTest(TestCase):
    def _params(self, floor_type, **extra):
        vals = {'identite.surface_m2': 100.0, 'identite.emprise_m2': 100.0, 'identite.niveaux': 1,
                'plancher.type': floor_type, 'plancher.u': 1.0, **extra}
        return engine_inputs.from_fiche(vals, None)

    def test_floor_type_changes_the_need(self):
        from ..testing import synthetic_year
        weather = synthetic_year(days=365)
        res = {k: n1.run_one(self._params(k), weather)['heating_kwh'] for k in ('terre_plein', 'vide_sanitaire', 'sous_sol')}
        # Même U équivalent : à moyenne annuelle égale, les trois besoins restent proches (dynamique seule).
        self.assertLess(max(res.values()) / min(res.values()), 1.1, res)
        one = n1.run_one(self._params('sous_sol'), weather)
        self.assertIn('sous-sol', one['sol']['modele'])
        m = n0.run(self._params('terre_plein'), weather)
        self.assertIn('terre-plein', m['sol']['modele'])
        const = self._params('terre_plein')
        const.ground_model = False
        t, u, _d = engine_inputs.ground_series(const, np.zeros(24), 19.0)
        self.assertEqual((t.tolist(), u), ([12.0] * 24, 1.0))

    def test_basement_depth_fact(self):
        b = make_building(fiche())
        facts.record(b, 'sous_sol.profondeur_m', 3.2, 'declare', author_sub='u')
        p, _vals = calc_service.params_for(b, None)
        self.assertEqual(p.basement_depth, 3.2)


class N2GroundTest(TestCase):
    def test_hourly_ground_in_the_n2(self):
        from ..engines import n2_inputs
        b = make_building(fiche())
        facts.record(b, 'plancher.type', 'sous_sol', 'declare', author_sub='u')
        seen = {}
        from .. import building_solver
        orig = building_solver.run_building_simulation

        def spy(envelope, layers, sunvis, payload, *a, **kw):
            seen['t_ground'] = [w.get('t_ground') for w in payload['weather']]
            return orig(envelope, layers, sunvis, payload, *a, **kw)
        with tmy():
            run, _ = calc_service.start_n2(b, owner_sub='u')
            from unittest import mock
            with mock.patch('api.building_solver.run_building_simulation', side_effect=spy):
                run = calc_service.run_n2(run)
        self.assertEqual(run.status, Job.DONE)
        self.assertTrue(all(t is not None for t in seen['t_ground']))
        self.assertGreater(len(set(seen['t_ground'])), 10)        # varie dans le temps
        run = CalcRun.objects.get(pk=run.pk)
        self.assertIn('sous-sol', run.inputs['sol']['modele'])
        self.assertEqual(run.inputs['engine'], n2_inputs.ENGINE_VERSION)


_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()
