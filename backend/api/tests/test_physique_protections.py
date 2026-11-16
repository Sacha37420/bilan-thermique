"""Lot 36 (chantier bt-potentiels, I1) — protections paramétrables : modèle de lames (ISO 15099), débords inclinés et
latéraux par côté, joues partielles, ΔR des fermetures ; chaque paramètre du catalogue change le résultat dans les
niveaux qu'il annonce, et seulement là."""

import copy
import math

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import shading_service
from ..engines import inputs, n0, n1, shading, solar
from ..models import Building, ParoiModel, ShadingDevice
from ..testing import box_with_window, synthetic_year, without_auto_environment
from .test_moteurs import FLOOR, GLASS, ROOF, WALL

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


YEAR = synthetic_year()
R = math.radians


class SlatModelTest(SimpleTestCase):
    def test_hand_values(self):
        # Lames horizontales, soleil rasant, lames minces : tout passe.
        self.assertAlmostEqual(float(shading.slat_open_fraction(0.0, 0.0, 0.08, 0.07)), 1.0)
        # Lames verticales jointives (L = s) : fermé.
        self.assertAlmostEqual(float(shading.slat_open_fraction(0.0, R(90), 0.07, 0.07)), 0.0)
        # φ = 30°, ψ = 0, L = 0,08, s = 0,07 : 1 − 0,08·sin30 / (0,07·cos30) = 1 − 0,04/0,06062 = 0,3402.
        self.assertAlmostEqual(float(shading.slat_open_fraction(R(30), 0.0, 0.08, 0.07)), 0.3402, places=3)
        # Épaisseur : ψ = −φ (lames dans l'axe du rayon) → seule l'épaisseur arrête : 1 − 0,002/(0,07·cos30) = 0,967.
        self.assertAlmostEqual(float(shading.slat_open_fraction(R(30), R(-30), 0.08, 0.07, 0.002)), 0.967, places=3)
        # Transmission de la lame : la part arrêtée passe à τ.
        self.assertAlmostEqual(float(shading.slat_beam(R(30), 0.0, 0.08, 0.07, 0.0, 0.2)), 0.3402 + 0.6598 * 0.2, places=3)

    def test_tracking_cuts_beam(self):
        phi = np.radians([10.0, 30.0, 50.0])
        psi = shading.tracking_tilt(phi, 0.08, 0.07)
        self.assertTrue(np.allclose(shading.slat_open_fraction(phi, psi, 0.08, 0.07), 0.0, atol=1e-6))
        self.assertTrue(np.all(psi <= R(90)))

    def test_diffuse_integral(self):
        # Lames de largeur nulle : ∫ cos φ dφ / 2 = 1.
        self.assertAlmostEqual(shading.slat_diffuse(0.0, 1e-6, 0.07, 0.0, 0.0), 1.0, places=3)
        closed = shading.slat_diffuse(R(90), 0.07, 0.07, 0.0, 0.0)
        open_ = shading.slat_diffuse(0.0, 0.07, 0.07, 0.0, 0.0)
        self.assertLess(closed, 0.01)
        self.assertGreater(open_, 0.3)


class OverhangSidesTest(SimpleTestCase):
    AZ, EL = np.radians([230.0]), np.radians([40.0])   # soleil à l'ouest du sud : à gauche, face à la façade sud

    def f(self, **kw):
        base = dict(depth=1.0, gap=0.2, height=1.5, width=1.2, ext_left=0.0, ext_right=0.0)
        base.update(kw)
        return float(shading.overhang_fraction(self.AZ, self.EL, 180.0, **base)[0])

    def test_left_extension_matters_when_sun_on_left(self):
        self.assertLess(self.f(ext_left=2.0), self.f(ext_left=0.0) - 0.05)
        self.assertAlmostEqual(self.f(ext_right=2.0), self.f(ext_right=0.0), places=6)

    def test_tilt_lowers_edge(self):
        self.assertLess(self.f(tilt_deg=30.0), self.f(tilt_deg=0.0))

    def test_fin_height_and_side(self):
        az, el = np.radians([230.0]), np.radians([30.0])
        full = float(shading.fins_fraction(az, el, 180.0, 0.5, 1.2, None, 1.5)[0])
        low = float(shading.fins_fraction(az, el, 180.0, 0.5, 1.2, 0.5, 1.5)[0])
        right_only = float(shading.fins_fraction(az, el, 180.0, 0.5, 1.2, None, 1.5, 'droite')[0])
        self.assertLess(full, low)
        self.assertEqual(right_only, 1.0)     # la joue de droite est derrière, soleil à gauche


def _params(**kw):
    vals = {'identite.surface_m2': 100.0, 'identite.usage': 'bureau', 'mur.u': 0.5, 'baies.ratio_S': 0.4,
            'baies.ratio_O': 0.3, 'baies.ratio_E': 0.3, 'toiture.u': 0.3, 'usage.consigne_chauffage_c': 19.0}
    vals.update(kw)
    return inputs.from_fiche(vals)


ALT = {'bool': lambda s: not s['defaut'], 'enum': lambda s: [c['valeur'] for c in s['choix'] if c['valeur'] != s['defaut']][0]}


ALT_OVERRIDE = {'fin_height_m': 0.6}     # défaut 3 m ≥ baie de 1,5 m : il faut descendre sous la baie pour agir


def _alt_value(spec):
    if spec['cle'] in ALT_OVERRIDE:
        return ALT_OVERRIDE[spec['cle']]
    if spec['type'] in ALT:
        return ALT[spec['type']](spec)
    d = spec['defaut']
    if d is None:
        return spec['min'] + 0.3 * (spec['max'] - spec['min'])
    lo, hi = spec['min'], spec['max']
    v = d + 0.35 * (hi - lo) if d + 0.35 * (hi - lo) <= hi else d - 0.35 * (hi - lo)
    return int(round(v)) if spec['type'] == 'int' else v


# Pilotage adapté à chaque paramètre (un réglage de planning n'agit qu'en planning, etc.).
CONTROL_MODE = {'heure_debut': 'planning', 'heure_fin': 'planning', 'mois_debut': 'planning', 'mois_fin': 'planning',
                'irradiance_w_m2': 'auto', 't_ext_min_c': 'auto', 'close_when_absent': 'auto', 'position': 'fixe',
                'fermeture_nocturne': 'auto', 'lame_suivi_soleil': 'fixe'}


class EveryParameterHasItsEffectTest(SimpleTestCase):
    """Pour chaque modèle et chaque paramètre : varier le paramètre change les termes d'enveloppe de N0/N1 (apports
    des baies, apports opaques ou conductance des vitrages) et les résultats du N1 ; un paramètre annoncé sans effet
    en N2 (baie type) ne change pas les obstacles géométriques du N2 (vérifié dans GeometricN2Test)."""

    def _terms(self, p):
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        e = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        return e

    def test_each_parameter(self):
        base_p = _params()
        for m in shading.MODELS:
            specs = [(s, False) for s in m['parametres']] + [(s, True) for s in m['pilotage']['parametres']]
            for spec, is_ctl in specs:
                if not set(spec['niveaux']) & {'N0', 'N1'}:
                    continue
                with self.subTest(model=m['cle'], param=spec['cle']):
                    dev = {'kind': m['cle'], 'orientation': 'S', 'mode': 'parametrique',
                           'params': shading.validate_params(m['cle'], {}),
                           'control': shading.validate_control(m['cle'], {'mode': CONTROL_MODE.get(spec['cle'], 'auto')}
                                                               if m['pilotage']['modes'] else {})}
                    if is_ctl and spec['cle'] == 'position':
                        dev['control']['position'] = 0.6
                    if is_ctl and spec['cle'] == 'lame_suivi_soleil':
                        dev['control']['position'] = 1.0
                    if m['cle'] == 'bso' and not is_ctl and spec['cle'].startswith('lame_'):
                        dev['control'] = shading.validate_control('bso', {'mode': 'fixe'})
                    if m['cle'] in shading.MOBILE and not is_ctl and dev['control'].get('mode') == 'auto':
                        dev['control']['t_ext_min_c'] = 10.0
                    alt = copy.deepcopy(dev)
                    (alt['control'] if is_ctl else alt['params'])[spec['cle']] = _alt_value(spec)
                    if spec['cle'] == 'close_when_absent':
                        alt['control']['irradiance_w_m2'] = dev['control']['irradiance_w_m2'] = 800.0
                    p1, p2 = copy.deepcopy(base_p), copy.deepcopy(base_p)
                    p1.devices, p2.devices = [dev], [alt]
                    a, b = self._terms(p1), self._terms(p2)
                    diff = sum(float(np.abs(a[k] - b[k]).sum()) for k in ('win', 'opq', 'h_w'))
                    self.assertGreater(diff, 1e-3, msg=f'{m["cle"]}.{spec["cle"]} sans effet sur N0/N1')

    def test_n1_and_n0_results_change(self):
        """Effet bout à bout sur les résultats (pas seulement les termes) : lames, ΔR, inclinaison."""
        p = _params()
        bso = {'kind': 'bso', 'orientation': 'S', 'params': shading.validate_params('bso', {}),
               'control': shading.validate_control('bso', {'mode': 'fixe'})}
        bso_closed = copy.deepcopy(bso)
        bso_closed['params']['lame_inclinaison_deg'] = 85.0
        volet = {'kind': 'volet_roulant', 'orientation': 'all', 'params': shading.validate_params('volet_roulant', {}),
                 'control': shading.validate_control('volet_roulant', {'mode': 'planning', 'heure_debut': 0, 'heure_fin': 0})}
        volet_r0 = copy.deepcopy(volet)
        volet_r0['params']['delta_r_m2k_w'] = 0.0
        qs = []
        for devs in ([bso], [bso_closed], [volet], [volet_r0]):
            q = copy.deepcopy(p)
            q.devices = devs
            qs.append(q)
        r = n1.run(qs, YEAR)
        self.assertLess(r[1]['t_op_max'], r[0]['t_op_max'])
        self.assertLess(r[1]['confort']['dh_re2020'], r[0]['confort']['dh_re2020'] + 1e-9)
        # Volet fermé la nuit (fermeture nocturne par défaut), ΔR 0,20 : moins de chauffage qu'avec ΔR = 0.
        self.assertLess(r[2]['heating_kwh'], r[3]['heating_kwh'])
        h0 = [n0.run(q, YEAR)['heating_kwh'] for q in qs[2:]]
        self.assertLess(h0[0], h0[1])

    def test_delta_r_order_of_magnitude(self):
        """Vitrage Uw = 2,8 fermé la nuit (ΔR 0,20) : U nuit = 1/(1/2,8 + 0,2) = 1,79 W/m²·K, soit −36 %."""
        p = _params(**{'baies.uw': 2.8})
        p.devices = [{'kind': 'volet_roulant', 'orientation': 'all', 'params': shading.validate_params('volet_roulant', {}),
                      'control': shading.validate_control('volet_roulant', {'mode': 'planning', 'heure_debut': 0,
                                                                             'heure_fin': 0})}]
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        e = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        a_win = sum(w['window'] for w in p.walls.values())
        night = el <= 0
        self.assertAlmostEqual(float(e['h_w'][night].mean()), a_win / (1 / 2.8 + 0.20), places=3)
        self.assertAlmostEqual(float(e['h_w'][~night].mean()), a_win * 2.8, places=3)

    def test_legacy_devices_keep_their_behaviour(self):
        """Protection d'avant le lot 36 (clés absentes) : débord infini, profondeur 0 sans effet, BSO à fs fixes."""
        p = _params()
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        p.devices = [{'kind': 'casquette', 'orientation': 'S', 'params': {}}]
        e = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        p.devices = []
        e0 = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        self.assertTrue(np.allclose(e['win'], e0['win']))
        legacy_bso = {'kind': 'bso', 'orientation': 'S', 'control': {'mode': 'planning', 'hours': list(range(24))}}
        fb, fd = shading.factors([legacy_bso], 'S', 180.0, az, el, hidx, np.ones(len(az)), t_e, 0.6)
        self.assertTrue(np.allclose(fb, 0.05) and np.allclose(fd, 0.25))

    def test_overhang_shades_opaque_band(self):
        p = _params()
        t_e, az, el, dni, dhi, hidx = solar.arrays(YEAR)
        e0 = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        p.devices = [{'kind': 'balcon', 'orientation': 'S', 'params': shading.validate_params('balcon', {'depth_m': 1.5})}]
        e1 = n1.envelope_terms(p, az, el, dni, dhi, t_e, hidx)
        self.assertLess(e1['opq'].sum(), e0['opq'].sum())


class GeometricN2Test(TestCase):
    def setUp(self):
        ids = {k: ParoiModel.objects.create(name=k, layers=l, is_glazing=(k == 'verre')).pk
               for k, l in (('mur', WALL), ('toit', ROOF), ('sol', FLOOR), ('verre', GLASS))}
        env = box_with_window(w=10, d=8, h=3, wall_id=ids['mur'], roof_id=ids['toit'], floor_id=ids['sol'],
                              glass_id=ids['verre'])
        self.b = Building.objects.create(name='b', envelope=env)

    def occ(self, kind, **params):
        ShadingDevice.objects.all().delete()
        ShadingDevice.objects.create(building=self.b, kind=kind, orientation='S', mode='parametrique',
                                     params=shading.validate_params(kind, params))
        return shading_service.geometric_occluders(self.b, all_fixed=True)

    def test_n2_geometry_follows_parameters(self):
        flat = self.occ('casquette', depth_m=1.0)
        tilted = self.occ('casquette', depth_m=1.0, inclinaison_deg=30.0)
        z_flat = sorted({round(v[2], 3) for v in flat['vertices']})
        z_tilt = sorted({round(v[2], 3) for v in tilted['vertices']})
        self.assertEqual(len(z_flat), 1)
        self.assertAlmostEqual(z_tilt[1] - z_tilt[0], math.tan(R(30)), delta=0.002)   # bord abaissé de D·tan β
        left = self.occ('casquette', depth_m=1.0, extension_gauche_m=0.0, extension_droite_m=3.0)
        xs = [v[0] for v in left['vertices']]
        win_x = [v[0] for v in self.b.envelope['vertices'][8:12]]
        self.assertAlmostEqual(min(xs), min(win_x), places=3)          # face sud : gauche = ouest = x min
        self.assertAlmostEqual(max(xs), max(win_x) + 3.0, places=3)
        fins = self.occ('joues', fin_depth_m=0.5, fin_height_m=0.5, fin_cote='gauche')
        self.assertEqual(len(fins['triangles']), 2)
        zs = [v[2] for v in fins['vertices']]
        self.assertAlmostEqual(max(zs) - min(zs), 0.5, places=3)

    def test_window_type_parameters_do_not_change_n2(self):
        """`window_height_m`/`window_width_m` sont annoncés N0/N1 seulement : en N2, ce sont les baies réelles."""
        a = self.occ('casquette', depth_m=1.0, window_height_m=1.0, window_width_m=1.0)
        b = self.occ('casquette', depth_m=1.0, window_height_m=3.0, window_width_m=8.0)
        self.assertEqual(a, b)
        spec = {p['cle']: p for p in shading.KINDS['casquette']['parametres']}
        self.assertEqual(spec['window_height_m']['niveaux'], ['N0', 'N1'])

    def test_parametric_mode_ignored_by_calcul_3d_but_used_from_fiche(self):
        ShadingDevice.objects.create(building=self.b, kind='casquette', orientation='S', mode='parametrique',
                                     params=shading.validate_params('casquette', {}))
        self.assertIsNone(shading_service.geometric_occluders(self.b))
        self.assertIsNotNone(shading_service.geometric_occluders(self.b, all_fixed=True))
