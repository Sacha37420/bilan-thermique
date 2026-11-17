"""Validation physique (chantier bt-validation-physique, phase 2) — cas analytiques, un canal à la fois.

Chaque attendu est calculé **à la main dans le test**, sans appeler la fonction testée, et la tolérance est fixée avant
le calcul (constantes `TOL_*`). Les canaux sont isolés : conduction et ventilation sans soleil, soleil sans écart de
température, apports dans une boîte adiabatique. Sources :
- conductances : ISO 13790:2008 § 8 (H_tr = Σ U·A, H_ve = ρ_a·c_a·q, ρ_a·c_a = 1 200 J/m³K ⇒ 0,34 W·h/m³K) ;
- 5R1C : ISO 13790:2008 annexe C (éq. C.1 à C.10, schéma de Crank-Nicolson) ;
- éclairement d'un plan incliné, ciel isotrope : Duffie & Beckman, *Solar Engineering of Thermal Processes*, 4ᵉ éd.,
  éq. 1.6.2 (angle d'incidence) et 2.15.1 (modèle isotrope de Liu-Jordan, réflexion du sol ρ_g·(1 − cos β)/2) ;
- apport d'une paroi opaque ensoleillée : ISO 13790:2008 éq. 45 (Φ = α·R_se·U·A·I, R_se = 1/h_e) ;
- DH RE2020 : arrêté du 4 août 2021 (méthode Th-BCE 2020), T_conf adaptative EN 16798-1:2019 éq. B.1.
"""

import math

import numpy as np
from django.test import SimpleTestCase

from .. import building_solver
from ..engines import comfort, n1, solar
from ..engines.inputs import Params
from ..testing import box_envelope, box_with_window

TOL_STEADY = 0.005       # 0,5 % : régime établi, seule l'erreur de convergence temporelle reste
TOL_RATE = 0.01          # 1 % : pente de montée en température d'une boîte adiabatique
TOL_SOLAR = 0.002        # 0,2 % : un éclairement, aucune dynamique
TOL_TAU = 0.02           # 2 % : constante de temps (schéma au pas horaire contre l'exponentielle exacte)


def flat_weather(hours, t_ext=0.0, el=-30.0, az=180.0, e_dir=0.0, e_dif=0.0, t_ground=None):
    out = []
    for h in range(hours):
        p = {'t_ext': t_ext, 'sun_azimuth': az, 'sun_elevation': el, 'e_dir': e_dir, 'e_dif': e_dif,
             'wind_m_s': 3.0, 'hour_index': h}
        if t_ground is not None:
            p['t_ground'] = t_ground
        out.append(p)
    return out


def params(**over):
    walls = {o: {'opaque': 20.0, 'window': 4.0, 'u': 0.5, 'uw': 2.0} for o in 'NESO'}
    kw = dict(a_floor=80.0, volume=200.0, walls=walls, roof_area=80.0, u_roof=0.3, floor_area=80.0, u_floor=0.4,
              g_window=0.6, gains_w_m2=0.0, t_heat=20.0, residential=True, n_vent=0.5, t_ground=0.0)
    kw.update(over)
    return Params(**kw)


# ── N1 ──────────────────────────────────────────────────────────────────────

class N1SteadyStateTest(SimpleTestCase):
    """Sans soleil ni apports, en régime établi. Le 5R1C (ISO 13790 annexe C) place H_tr,is = h_is·A_t **en série**
    avec les transmissions alors que les U comptent déjà R_si : la consigne étant portée par l'air, la perte vaut
    H_ve·ΔT + [H_tr,is ∥ série (H_tr,w + H_tr,op)]·ΔT, et non Σ U·A·ΔT. Le réseau est résolu ici à la main (deux nœuds,
    θ_s et θ_m), sans `n1.step`. Écart de modèle documenté (RAPPORT.md, E1)."""

    def test_conduction_and_ventilation(self):
        a_f = 80.0
        h_ve, h_w = 0.34 * 0.5 * 200, 4 * 4 * 2.0
        h_op, h_fl = 4 * 20 * 0.5 + 80 * 0.3, 80 * 0.4
        h_is, h_ms = n1.H_IS * 4.5 * a_f, n1.H_MS * 2.5 * a_f
        h_em = 1.0 / (1.0 / (h_op + h_fl) - 1.0 / h_ms)
        share = h_fl / (h_op + h_fl)
        for t_e, t_g in ((0.0, 0.0), (0.0, 10.0), (-5.0, 8.0)):
            with self.subTest(t_e=t_e, t_g=t_g):
                t_em = t_e + share * (t_g - t_e)
                # θ_a = 20 ; inconnues θ_s, θ_m : bilans des nœuds de surface et de masse.
                a = np.array([[h_is + h_w + h_ms, -h_ms], [-h_ms, h_ms + h_em]])
                b = np.array([h_is * 20.0 + h_w * t_e, h_em * t_em])
                t_s, _t_m = np.linalg.solve(a, b)
                expected = h_ve * (20 - t_e) + h_is * (20 - t_s)
                r = n1.run_one(params(t_ground=t_g), flat_weather(24 * 30, t_ext=t_e))
                self.assertAlmostEqual(r['phi_hc_w'][-1], expected, delta=TOL_STEADY * expected)
                # Ordre de grandeur de l'écart au statique Σ U·A·ΔT (sans le sol : ici 7 % de perte en moins).
                if t_g == t_e:
                    static = (h_ve + h_w + h_op + h_fl) * (20 - t_e)
                    self.assertLess(expected, static)
                    self.assertGreater(expected, 0.9 * static)


class N1AdiabaticTest(SimpleTestCase):
    """Boîte sans aucune perte, apports constants Q : la masse monte de Q·t/C (C = c_m·A_f)."""

    def test_heating_rate(self):
        walls = {o: {'opaque': 20.0, 'window': 0.0, 'u': 0.0, 'uw': 0.0} for o in 'NESO'}
        p = params(walls=walls, u_roof=0.0, u_floor=0.0, n_vent=0.0, gains_w_m2=10.0, t_heat=-100.0)
        r = n1.run_one(p, flat_weather(24 * 10))
        q, c = 10.0 * 80.0, 165_000.0 * 80.0
        t = np.asarray(r['t_air'])
        slope = (t[-1] - t[-25]) / (24 * 3600.0)
        self.assertAlmostEqual(slope, q / c, delta=TOL_RATE * q / c)


class N1FreeDecayTest(SimpleTestCase):
    """Évolution libre après un échelon de T_ext : décroissance exponentielle de la masse, constante de temps du
    modèle RC équivalent τ = C_m / (H_tr,em + H_3) (ISO 13790 annexe C : la masse voit H_tr,em vers dehors et, en série,
    H_tr,ms puis H_tr,w + H_1 vers dehors). Vérifie l'intégration temporelle, pas la norme elle-même."""

    def test_time_constant(self):
        p = params(t_heat=-100.0, c_m=1.0e6)
        r = n1.run_one(p, flat_weather(24 * 20, t_ext=0.0), t_init=20.0)
        a_m, a_t = 2.5 * 80, 4.5 * 80
        h_ve, h_is, h_ms = 0.34 * 0.5 * 200, n1.H_IS * a_t, n1.H_MS * a_m
        h_w, h_op = 4 * 4 * 2.0, 4 * 20 * 0.5 + 80 * 0.3 + 80 * 0.4
        h_em = 1.0 / (1.0 / h_op - 1.0 / h_ms)
        h1 = 1.0 / (1.0 / h_ve + 1.0 / h_is)
        h3 = 1.0 / (1.0 / (h1 + h_w) + 1.0 / h_ms)
        tau_h = 1.0e6 * 80 / (h_em + h3) / 3600.0
        t = np.asarray(r['t_air'])
        # t_air(k) ∝ t_m : la pente de ln(t) sur la seconde moitié donne −1/τ.
        k0, k1 = 24, 24 * 8
        tau_obs = (k1 - k0) / math.log(t[k0] / t[k1])
        self.assertAlmostEqual(tau_obs, tau_h, delta=TOL_TAU * tau_h)


class N1SolarOnlyTest(SimpleTestCase):
    """Soleil seul : gains d'une baie = g·(1 − part de cadre)·A·I, d'une paroi opaque = α·R_se·U·A·I ; I calculé à la
    main (soleil plein sud, 30° de hauteur)."""

    def test_window_and_opaque_gains(self):
        el, dni, dhi, rho = 30.0, 800.0, 100.0, 0.20
        i_south = dni * math.cos(math.radians(el)) + dhi * 0.5 + rho * (dni * math.sin(math.radians(el)) + dhi) * 0.5
        i_roof = dni * math.sin(math.radians(el)) + dhi
        walls = {o: {'opaque': 0.0, 'window': 0.0, 'u': 0.5, 'uw': 2.0} for o in 'NESO'}
        walls['S'] = {'opaque': 20.0, 'window': 4.0, 'u': 0.5, 'uw': 2.0}
        p = params(walls=walls, absorptance=0.6, albedo=rho)
        e = n1.envelope_terms(p, np.radians([180.0]), np.radians([el]), np.array([dni]), np.array([dhi]))
        self.assertAlmostEqual(e['win'][0], 4.0 * 0.6 * 0.8 * i_south, delta=TOL_SOLAR * 4.0 * 0.6 * 0.8 * i_south)
        opq = 0.6 * 0.04 * 0.5 * 20.0 * i_south + 0.6 * 0.04 * 0.3 * 80.0 * i_roof
        self.assertAlmostEqual(e['opq'][0], opq, delta=TOL_SOLAR * opq)

    def test_irradiance_reference_values(self):
        """Duffie & Beckman ex. 1.6.1 (Madison, 13 février 10 h 30, β = 45°, γ = 15°) : θ = 35,0°."""
        lat, decl, omega, beta, gamma = 43.0, -14.0, -22.5, 45.0, 15.0
        r = math.radians
        cos_t = (math.sin(r(decl)) * math.sin(r(lat)) * math.cos(r(beta))
                 - math.sin(r(decl)) * math.cos(r(lat)) * math.sin(r(beta)) * math.cos(r(gamma))
                 + math.cos(r(decl)) * math.cos(r(lat)) * math.cos(r(beta)) * math.cos(r(omega))
                 + math.cos(r(decl)) * math.sin(r(lat)) * math.sin(r(beta)) * math.cos(r(gamma)) * math.cos(r(omega))
                 + math.cos(r(decl)) * math.sin(r(beta)) * math.sin(r(gamma)) * math.sin(r(omega)))
        self.assertAlmostEqual(cos_t, 0.817, delta=0.001)             # D&B : cos θ = 0,817, θ = 35°
        # Même géométrie par les angles solaires (hauteur, azimut) que l'app utilise : cos θ identique.
        cos_z = (math.cos(r(lat)) * math.cos(r(decl)) * math.cos(r(omega)) + math.sin(r(lat)) * math.sin(r(decl)))
        el = math.degrees(math.asin(cos_z))
        az_s = math.degrees(math.copysign(abs(math.acos((cos_z * math.sin(r(lat)) - math.sin(r(decl)))
                                                        / (math.sin(math.acos(cos_z)) * math.cos(r(lat))))), omega))
        total, beam = solar.surface_irradiance(np.radians([180.0 + az_s]), np.radians([el]), np.array([1000.0]),
                                               np.array([0.0]), 180.0 + gamma, beta, albedo=0.0)
        self.assertAlmostEqual(float(beam[0]) / 1000.0, cos_t, delta=2e-3)


# ── N2 ──────────────────────────────────────────────────────────────────────

WALL = [{'e': 0.10, 'lam': 0.04, 'rho': 30, 'c': 1400, 'tau': 0, 'r': 0.4, 'alpha': 0.6},
        {'e': 0.20, 'lam': 1.75, 'rho': 2300, 'c': 1000, 'tau': 0, 'r': 0.4, 'alpha': 0.6}]
H_I, H_E, R_G = 7.7, 25.0, 0.5


def u_of(layers, h_e=H_E):
    return 1.0 / (1.0 / H_I + 1.0 / h_e + sum(l['e'] / l['lam'] for l in layers))


def n2_payload(weather, t_init=20.0, **interior):
    it = {'mode': 'thermostat', 'h_i': H_I, 'c_air_int': 1.2 * 1006 * 240 * 5, 't_min': 20.0, 't_max': 60.0,
          'debit_vent_m3h': 0.0, 'apports_internes_w': 0.0}
    it.update(interior)
    return {'dx_max': 0.05, 'h_e': H_E, 't_init': t_init, 'shadow_mode': 'precomputed', 't_ground': 0.0,
            'r_ground': R_G, 'weather': weather, 'interior': it}


class N2SteadyStateTest(SimpleTestCase):
    """Boîte 10 × 8 × 3 m, sans soleil : Φ = Σ U·A·ΔT + 0,34·q·ΔT, plancher vers le sol à travers R_sol."""

    def test_conduction_ventilation_ground(self):
        env = box_envelope(10, 8, 3, paroi_id=1)
        a_walls_roof = 2 * (10 * 3 + 8 * 3) + 80.0
        u_floor = 1.0 / (1.0 / H_I + sum(l['e'] / l['lam'] for l in WALL) + R_G)
        for t_e, t_g, q in ((0.0, 0.0, 0.0), (0.0, 10.0, 120.0)):
            with self.subTest(t_e=t_e, t_g=t_g, q=q):
                w = flat_weather(24 * 40, t_ext=t_e, t_ground=t_g)
                res = building_solver.run_building_simulation(env, {1: WALL}, None, n2_payload(w, debit_vent_m3h=q))
                expected = u_of(WALL) * a_walls_roof * (20 - t_e) + u_floor * 80 * (20 - t_g) + 0.34 * q * (20 - t_e)
                self.assertAlmostEqual(res['phi_hc_w'][-1], expected, delta=TOL_STEADY * expected)


class N2AdiabaticTest(SimpleTestCase):
    """Boîte entièrement adiabatique, apports Q : l'air et toute la masse montent ensemble de Q/C par seconde,
    C = C_air + Σ A·ρ·c·e."""

    def test_heating_rate(self):
        env = box_envelope(10, 8, 3, paroi_id=1)
        for t in env['triangles']:
            t['boundary'] = 'adiabatic'
        q = 2000.0
        c_air = 1.2 * 1006 * 240 * 5
        area = 2 * (10 * 3 + 8 * 3) + 2 * 80.0
        c = c_air + area * sum(l['rho'] * l['c'] * l['e'] for l in WALL)
        w = flat_weather(24 * 20)
        res = building_solver.run_building_simulation(env, {1: WALL}, None,
                                                      n2_payload(w, t_min=-100.0, apports_internes_w=q))
        t = np.asarray(res['t_air'])
        slope = (t[-1] - t[-25]) / (24 * 3600.0)
        self.assertAlmostEqual(slope, q / c, delta=TOL_RATE * q / c)


class N2SolarOnlyTest(SimpleTestCase):
    """Soleil seul, l'air tenu à la température extérieure (consignes = T_ext)."""

    def test_glazing_transmission(self):
        glass = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.85, 'r': 0.08, 'alpha': 0.07}]
        env = box_with_window(w=10, d=8, h=3, wall_id=1, roof_id=1, floor_id=1, glass_id=2, win_ratio_south=0.3)
        a_glass = sum(t['area'] for t in env['triangles'] if t['paroi_model_id'] == 2)
        el, dni, dhi = 30.0, 800.0, 100.0
        w = flat_weather(1, el=el, az=180.0, e_dir=dni, e_dif=dhi, t_ground=0.0)
        res = building_solver.run_building_simulation(env, {1: WALL, 2: glass}, None,
                                                      n2_payload(w, t_init=0.0, t_min=0.0, t_max=0.001))
        i_south = dni * math.cos(math.radians(el)) + dhi * 0.5          # sans réflexion du sol (aucun albédo passé)
        expected_wh = 0.85 * a_glass * i_south
        self.assertAlmostEqual(res['balance']['solar_transmitted_kwh'] * 1000.0, expected_wh,
                               delta=TOL_SOLAR * expected_wh)

    def test_opaque_roof_steady(self):
        """Toit seul ensoleillé (soleil au zénith, aucun diffus), intérieur tenu à T_ext : en régime établi le flux
        entrant vaut α·I·U/h_e·A (ISO 13790 éq. 45)."""
        env = box_envelope(10, 8, 3, paroi_id=1)
        w = flat_weather(24 * 30, el=89.999, az=180.0, e_dir=600.0, t_ground=0.0)
        res = building_solver.run_building_simulation(env, {1: WALL}, None, n2_payload(w, t_init=0.0, t_min=0.0, t_max=0.001))
        expected = 0.6 * 600.0 * u_of(WALL) / H_E * 80.0
        self.assertAlmostEqual(-res['phi_hc_w'][-1], expected, delta=TOL_STEADY * expected)


# ── Indicateurs ─────────────────────────────────────────────────────────────

def dh_by_hand(t_op, t_ext, residential):
    """DH RE2020 réécrit sans `engines.comfort` : boucle jour par jour, θ_rm,j = 0,2·θ̄_e,j−1 + 0,8·θ_rm,j−1."""
    n_days = len(t_ext) // 24
    means = [sum(t_ext[24 * d:24 * d + 24]) / 24.0 for d in range(n_days)]
    rm = sum(means[:7]) / min(7, n_days)
    dh, hours = 0.0, 0
    for d in range(n_days):
        if d > 0:
            rm = 0.2 * means[d - 1] + 0.8 * rm
        for h in range(24):
            k = 24 * d + h
            if not residential and not (8 <= h < 19 and d % 7 < 5):
                continue
            night = residential and (h >= 22 or h < 7)
            tc = 26.0 if night else min(28.0, max(26.0, 0.33 * rm + 21.8))
            if t_op[k] > tc:
                dh += t_op[k] - tc
                hours += 1
    return dh, hours


class IndicatorsFromSeriesTest(SimpleTestCase):
    """L'indicateur reflète la série : DH et heures > 28 °C recalculés à la main sur la série d'un vrai calcul N1 d'été."""

    def test_n1_summer_series(self):
        from ..testing import synthetic_year
        weather = synthetic_year(days=120, start_day=150)
        for residential in (True, False):
            with self.subTest(residential=residential):
                p = params(gains_w_m2=20.0, residential=residential, n_vent=0.2, g_window=0.7)
                r = n1.run_one(p, weather)
                t_ext = [w['t_ext'] for w in weather]
                dh, hours = dh_by_hand(r['t_op'], t_ext, residential)
                self.assertGreater(dh, 100.0)        # le cas surchauffe vraiment : le test n'est pas vide
                self.assertAlmostEqual(r['confort']['dh_re2020'], dh, delta=0.1 + 1e-3 * dh)
                self.assertEqual(r['confort']['heures_inconfort'], hours)
                from ..calc_runs import comfort_hours
                self.assertEqual(comfort_hours(r['t_op'])['hours_over_28'], sum(1 for x in r['t_op'] if x > 28))

    def test_definitions_identical_across_levels(self):
        """N1 et N2 passent par la même fonction, sur θ_op et le même masque d'occupation."""
        t = np.full(24 * 14, 30.0)
        a = comfort.indicators(t, t - 5, np.arange(len(t)), False)[0]
        b = comfort.dh_re2020(t, t - 5, np.arange(len(t)), False)[0]
        self.assertAlmostEqual(a['dh_re2020'], b, places=1)
