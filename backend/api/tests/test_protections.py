"""Lot 14 (refonte) — protections solaires : paramétrique (N0/N1) et géométrique (N2), cohérence des deux."""

import math
from unittest import mock

import numpy as np
import trimesh
from django.test import SimpleTestCase, TestCase

from .. import building_solver, calc_service, facts, shading_service
from ..engines import inputs, n1, shading
from ..models import Building, ParoiModel, ShadingDevice
from ..testing import box_with_window, client_for, patch_jwks, synthetic_year
from .test_moteurs import FLOOR, GLASS, ROOF, WALL

SUMMER = synthetic_year(days=45, start_day=180, t_mean=25.0, t_amp=0.0)
CASQUETTE = {'depth_m': 1.0, 'gap_m': 0.2, 'window_height_m': 1.64, 'window_width_m': 5.48, 'extension_m': 2.0}


class OverhangGeometryTest(SimpleTestCase):
    def test_high_sun_fully_shaded_low_sun_free(self):
        az = np.radians(np.array([180.0, 180.0, 0.0]))
        el = np.radians(np.array([80.0, 5.0, 30.0]))
        f = shading.overhang_fraction(az, el, 180.0, depth=1.0, gap=0.0, height=1.0)
        self.assertAlmostEqual(f[0], 0.0)
        self.assertGreater(f[1], 0.9)
        self.assertEqual(f[2], 0.0)  # soleil derrière la façade : pas de direct

    def test_fins_shade_oblique_sun(self):
        az = np.radians(np.array([180.0, 240.0]))
        el = np.radians(np.array([30.0, 30.0]))
        f = shading.fins_fraction(az, el, 180.0, fin_depth=0.5, width=1.0)
        self.assertAlmostEqual(f[0], 1.0)
        self.assertLess(f[1], 0.3)

    def test_mobile_auto_closes_on_strong_sun(self):
        dev = {'kind': 'store_ext', 'orientation': 'S', 'control': {'mode': 'auto', 'irradiance_w_m2': 200, 't_ext_min_c': 20}}
        pos = shading.mobile_position(dev, np.arange(3), np.array([500.0, 500.0, 100.0]), np.array([25.0, 15.0, 25.0]))
        self.assertEqual(list(pos), [1.0, 0.0, 0.0])

    def test_planning_and_months(self):
        dev = {'kind': 'volet_roulant', 'control': {'mode': 'planning', 'hours': [13, 14], 'months': [7]}}
        hidx = np.array([13, 14, 15, 24 * 190 + 13])  # janvier 13 h, 14 h, 15 h ; juillet 13 h
        self.assertEqual(list(shading.mobile_position(dev, hidx, None, None)), [0.0, 0.0, 0.0, 1.0])

    def test_vegetation_and_film(self):
        hidx = np.array([24 * 10 + 12, 24 * 190 + 12])  # janvier, juillet
        az, el = np.radians(np.array([180.0, 180.0])), np.radians(np.array([20.0, 60.0]))
        fb, _ = shading.factors([{'kind': 'arbre_caduc', 'orientation': 'S'}], 'S', 180.0, az, el, hidx,
                                np.ones(2), np.ones(2), 0.6)
        self.assertGreater(fb[0], fb[1])  # plus de soleil l'hiver (sans feuilles)
        fb, fd = shading.factors([{'kind': 'film', 'orientation': 'all', 'params': {'g': 0.3}}], 'E', 90.0, az, el, hidx,
                                 np.ones(2), np.ones(2), 0.6)
        self.assertAlmostEqual(fb[0], 0.5)


def ray_sunlit_fraction(window, occluder, az_deg, el_deg, n=12):
    """Fraction de points de la baie qui voient le soleil malgré les obstacles (lancer de rayons)."""
    mesh = trimesh.Trimesh(vertices=occluder['vertices'], faces=[t['v'] for t in occluder['triangles']], process=False)
    xs = np.linspace(window['x0'] + 1e-3, window['x1'] - 1e-3, n)
    zs = np.linspace(window['z0'] + 1e-3, window['z1'] - 1e-3, n)
    origins = np.array([[x, -0.02, z] for x in xs for z in zs])
    a, e = math.radians(az_deg), math.radians(el_deg)
    d = np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)])
    hit = mesh.ray.intersects_any(origins, np.tile(d, (len(origins), 1)))
    return 1.0 - hit.mean()


class ParametricVersusGeometricTest(TestCase):
    """Les deux méthodes donnent le même ordre de grandeur (critère de recette du § B4)."""

    def setUp(self):
        self.ids = {k: ParoiModel.objects.create(name=k, layers=l, is_glazing=(k == 'verre')).pk
                    for k, l in (('mur', WALL), ('toit', ROOF), ('sol', FLOOR), ('verre', GLASS))}
        self.env = box_with_window(w=10, d=8, h=3, win_ratio_south=0.3, wall_id=self.ids['mur'], roof_id=self.ids['toit'],
                                   floor_id=self.ids['sol'], glass_id=self.ids['verre'])
        self.b = Building.objects.create(name='b', envelope=self.env)
        ShadingDevice.objects.create(building=self.b, kind='casquette', orientation='S', mode='geometrique', params=CASQUETTE)

    def test_window_sunlit_fraction_matches_ray_casting(self):
        occ = shading_service.geometric_occluders(self.b)
        self.assertEqual(len(occ['triangles']), 2)
        xs = [v[0] for v in self.env['vertices'][8:12]]
        zs = [v[2] for v in self.env['vertices'][8:12]]
        win = {'x0': min(xs), 'x1': max(xs), 'z0': min(zs), 'z1': max(zs)}
        for az, el in ((180, 20), (180, 45), (180, 60), (150, 40), (210, 55), (130, 25)):
            ray = ray_sunlit_fraction(win, occ, az, el)
            par = float(shading.overhang_fraction(np.radians([az]), np.radians([el]), 180.0, CASQUETTE['depth_m'],
                                                  CASQUETTE['gap_m'], win['z1'] - win['z0'], CASQUETTE['extension_m'],
                                                  win['x1'] - win['x0'])[0])
            with self.subTest(az=az, el=el):
                self.assertAlmostEqual(par, ray, delta=0.1)

    def test_south_overhang_reduces_overheating_consistently(self):
        """Casquette sud : réduction des degrés-heures > 26 °C en N1 (paramétrique) et en N2 (géométrique)."""
        layers = {self.ids['mur']: WALL, self.ids['toit']: ROOF, self.ids['sol']: FLOOR, self.ids['verre']: GLASS}
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 25.0, 'shadow_mode': 'realtime', 't_ground': 14.0, 'weather': SUMMER,
                   'interior': {'mode': 'free', 'h_i': 7.7, 'c_air_int': 1.2 * 1006 * 200 * 5, 'debit_vent_m3h': 60.0,
                                'eta_recup_vent': 0.0, 'apports_internes_w': 320.0}}

        def dh(series):
            return sum(max(0.0, t - 26.0) for t in series[1:])
        n2_free = dh(building_solver.run_building_simulation(self.env, layers, None, payload)['t_air'])
        occ = shading_service.geometric_occluders(self.b)
        n2_shaded = dh(building_solver.run_building_simulation(self.env, layers, None, payload, environment_envelope=occ)['t_air'])
        p = inputs.from_envelope(self.env, layers, n_vent=0.3, gains_w=320.0, a_floor=80.0)
        p.t_heat, p.t_ground = -50.0, 14.0  # évolution libre, comme le N2
        r_free = n1.run_one(p, SUMMER, t_init=25.0)
        p.devices = [{'kind': 'casquette', 'orientation': 'S', 'mode': 'parametrique', 'params': CASQUETTE}]
        r_shaded = n1.run_one(p, SUMMER, t_init=25.0)
        n1_free, n1_shaded = dh(r_free['t_air']), dh(r_shaded['t_air'])
        red_n2 = 1 - n2_shaded / n2_free
        red_n1 = 1 - n1_shaded / n1_free
        self.assertGreater(red_n2, 0.05)
        self.assertGreater(red_n1, 0.05)
        self.assertLess(max(red_n1, red_n2) / min(red_n1, red_n2), 2.5, msg=(red_n1, red_n2))


@patch_jwks()
class ProtectionsApiTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69, sun_visibility_stale=False)
        for k, v in (('identite.surface_m2', 100.0), ('baies.ratio_S', 0.4), ('identite.usage', 'maison')):
            facts.record(self.b, k, v, 'infere')

    def test_create_validate_and_effect(self):
        c = client_for(sub='u')
        bad = c.post(f'/api/batiments/{self.b.pk}/protections/', {'kind': 'casquette', 'orientation': 'S', 'params': {'depth_m': 9}}, format='json')
        self.assertEqual(bad.status_code, 400)
        r = c.post(f'/api/batiments/{self.b.pk}/protections/', {'kind': 'casquette', 'orientation': 'S', 'params': CASQUETTE}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        c.post(f'/api/batiments/{self.b.pk}/protections/', {'kind': 'store_ext', 'orientation': 'S',
                                                            'control': {'mode': 'auto', 'irradiance_w_m2': 250}}, format='json')
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': synthetic_year(), 'source': 't'}):
            eff = c.post(f'/api/batiments/{self.b.pk}/protections/effet/', {}, format='json').json()
        rows = {x['label']: x for x in eff['rows']}
        self.assertLess(rows['toutes']['degree_hours_26'], rows['sans']['degree_hours_26'])
        self.assertGreater(rows['toutes']['heating_kwh'], rows['sans']['heating_kwh'])  # perte d'apports l'hiver
        self.assertLess(rows['toutes']['solar_by_orientation_kwh']['S'], rows['sans']['solar_by_orientation_kwh']['S'])
        self.assertEqual(len(eff['rows']), 4)

    def test_device_changes_calc_hash(self):
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': synthetic_year(days=60), 'source': 't'}):
            r1, _ = calc_service.run_level(self.b, 'N1', uncertainty=False)
            ShadingDevice.objects.create(building=self.b, kind='volet_roulant', orientation='S',
                                         control={'mode': 'planning', 'hours': list(range(10, 18))})
            self.assertFalse(calc_service.is_up_to_date(r1))
            r2, fresh = calc_service.run_level(self.b, 'N1', uncertainty=False)
        self.assertTrue(fresh)
        self.assertLess(r2.results['balance']['solar_windows_kwh'], r1.results['balance']['solar_windows_kwh'])

    def test_geometric_mode_marks_shadow_stale_and_only_for_fixed(self):
        c = client_for()
        self.assertEqual(c.post(f'/api/batiments/{self.b.pk}/protections/', {'kind': 'store_ext', 'orientation': 'S',
                                                                             'mode': 'geometrique'}, format='json').status_code, 400)
        c.post(f'/api/batiments/{self.b.pk}/protections/', {'kind': 'casquette', 'orientation': 'S', 'mode': 'geometrique',
                                                            'params': CASQUETTE}, format='json')
        self.b.refresh_from_db()
        self.assertTrue(self.b.sun_visibility_stale)

    def test_reader_cannot_add(self):
        from ..models import Portfolio, PortfolioMember
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        b = Building.objects.create(name='x', portfolio=p)
        r = client_for(sub='l').post(f'/api/batiments/{b.pk}/protections/', {'kind': 'film', 'orientation': 'S'}, format='json')
        self.assertEqual(r.status_code, 403)
