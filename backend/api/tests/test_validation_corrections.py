"""Validation physique (chantier bt-validation-physique, phase 5) — écarts corrigés, chacun prouvé par un test qui
échouait avant la correction (`chantiers/bt-validation-physique/RAPPORT.md`, C1 et C2).

C1 — sans environnement relevé relié, le N2 de la fiche ne recevait **aucune** réflexion par le sol, alors que N0/N1
comptent toujours un albédo de 0,20 (`engines.solar.DEFAULT_ALBEDO`) ; `refonte.md` § 6.3, point 5, prévoyait déjà
d'appliquer ce défaut « pour que N1 et N2 restent comparables ».
C2 — un mur mitoyen (adiabatique, contre un local chauffé) recevait le rayonnement réfléchi par le sol dès qu'un
albédo était donné, alors qu'il ne voit jamais l'extérieur.
"""

import math
from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_service
from ..engines import solar
from ..testing import box_envelope, box_with_window, synthetic_year, without_auto_environment
from .test_n2_fiche import fiche, make_building
from .test_validation_analytique import TOL_SOLAR, WALL, flat_weather, n2_payload

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


GLASS = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.85, 'r': 0.08, 'alpha': 0.07}]


class GroundReflectionTest(SimpleTestCase):
    def test_glazing_receives_reflected_part(self):
        """Duffie & Beckman éq. 2.15.1 : I = I_b·cos θ + I_d·(1 + cos β)/2 + ρ_g·GHI·(1 − cos β)/2 ; β = 90°."""
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=1, floor_id=1, glass_id=2, win_ratio_south=0.3)
        a_glass = sum(t['area'] for t in env['triangles'] if t['paroi_model_id'] == 2)
        el, dni, dhi, rho = 30.0, 800.0, 100.0, 0.20
        w = flat_weather(1, el=el, az=180.0, e_dir=dni, e_dif=dhi, t_ground=0.0)
        res = building_solver.run_building_simulation(env, {1: WALL, 2: GLASS}, None,
                                                      n2_payload(w, t_init=0.0, t_min=0.0, t_max=0.001),
                                                      ground_albedo_constant=rho)
        ghi = dni * math.sin(math.radians(el)) + dhi
        expected = 0.85 * a_glass * (dni * math.cos(math.radians(el)) + dhi * 0.5 + rho * ghi * 0.5)
        self.assertAlmostEqual(res['balance']['solar_transmitted_kwh'] * 1000.0, expected, delta=TOL_SOLAR * expected)

    def test_party_wall_receives_nothing(self):
        """C2 : un mur adiabatique n'absorbe aucun rayonnement, ni direct, ni diffus, ni réfléchi par le sol."""
        env = box_envelope(10, 8, 3, paroi_id=1)
        party = [i for i, t in enumerate(env['triangles']) if t['group'] == 'mur_sud']
        for i in party:
            env['triangles'][i]['boundary'] = 'adiabatic'
        w = flat_weather(3, el=30.0, az=180.0, e_dir=800.0, e_dif=100.0, t_ground=0.0)
        for p in w:
            p['mois'] = 7
        res = building_solver.run_building_simulation(env, {1: WALL}, None, n2_payload(w), ground_albedo_constant=0.2)
        absorbed = res['par_triangle']['solaire_ete_kwh']
        for i in party:
            self.assertEqual(absorbed[i], 0.0)
        east = [i for i, t in enumerate(env['triangles']) if t['group'] == 'mur_est']
        self.assertGreater(absorbed[east[0]], 0.0)       # un mur exposé, lui, reçoit le réfléchi (soleil plein sud)


class FicheDefaultAlbedoTest(TestCase):
    """C1 : le N2 de la fiche d'un bâtiment sans environnement relevé passe l'albédo par défaut au solveur ; une valeur
    d'abords saisie (`abords.albedo`) l'emporte."""

    def _albedo_passed(self, vals, name):
        b = make_building(vals, name=name)
        seen = {}
        real = building_solver.run_building_simulation

        def spy(*a, **kw):
            seen.update(kw)
            return real(*a, **kw)
        weather = synthetic_year(days=7)
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': weather, 'source': 'test'}), \
                mock.patch('api.building_solver.run_building_simulation', side_effect=spy):
            calc_service.run_n2_now(b, force=True)
        return seen.get('ground_albedo_constant')

    def test_default_and_override(self):
        self.assertEqual(self._albedo_passed(fiche(), 'defaut'), solar.DEFAULT_ALBEDO)
        self.assertEqual(self._albedo_passed(fiche(**{'abords.albedo': 0.35}), 'abords'), 0.35)


class TertiarySetbackTest(SimpleTestCase):
    """C3 : le N2 reproduit la consigne réduite hors occupation du tertiaire (−3 °C, `engines.n1.schedules`), heure par
    heure, avec le même calendrier que N1 (indice horaire de la météo) ; un logement garde une consigne constante."""

    def test_hourly_setpoint_matches_n1(self):
        import numpy as np
        from ..engines import n1, n2_inputs
        weather = synthetic_year(days=14)
        hidx = np.array([p['hour_index'] for p in weather])
        _f, expected = n1.schedules(hidx, False, 20.0)
        w = n2_inputs.weather_for_n2(weather, 0.0, False, None, t_heat=20.0)
        self.assertEqual([p['t_min'] for p in w], [float(x) for x in expected])
        self.assertEqual(sorted({p['t_min'] for p in w}), [17.0, 20.0])
        w = n2_inputs.weather_for_n2(weather, 0.0, True, None, t_heat=20.0)
        self.assertTrue(all(p.get('t_min') is None for p in w))

    def test_occupied_flag_follows_hour_index(self):
        """Le drapeau « jour ouvré » suit l'indice horaire de la météo (comme N1), pas la position dans la liste."""
        from ..engines import n2_inputs
        weather = synthetic_year(days=8, start_day=2)          # premier point : indice horaire 48 (mercredi)
        w = n2_inputs.weather_for_n2(weather, 0.0, False, None, t_heat=20.0)
        for p in w:
            self.assertEqual(p['occupied'], (p['hour_index'] // 24) % 7 < 5)


class TertiarySetbackFicheTest(TestCase):
    def test_n2_heating_drops_with_setback(self):
        """Effet physique : sur un bureau, le N2 avec réduit chauffe moins qu'avec une consigne constante."""
        from ..engines import n2_inputs
        b = make_building(fiche(**{'identite.usage': 'bureau'}), name='bureau')
        weather = synthetic_year(days=21)
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': weather, 'source': 'test'}):
            with_setback, _ = calc_service.run_n2_now(b, force=True)
            real = n2_inputs.weather_for_n2

            def flat(series, north, residential, ground=None, t_heat=None):
                return real(series, north, residential, ground, None)
            with mock.patch('api.engines.n2_inputs.weather_for_n2', side_effect=flat):
                constant, _ = calc_service.run_n2_now(b, force=True)
        self.assertLess(with_setback.results['heating_kwh'], 0.97 * constant.results['heating_kwh'])
