"""Tests — paroi 1d.

Découpé de l'ancien `api/tests.py` (Lot 5, refonte) — contenu des classes inchangé.
"""

import math
from unittest import mock
import numpy as np
from django.test import SimpleTestCase, TestCase
from .. import building_solver, elevation, geodata, geometry, serializers, shadow, solver, weather_source
from ..models import Building, Environment
from .helpers import (  # noqa: F401
    DT_SECONDS,
    _flat_wall_layer,
    _no_sun_weather_1d,
    _no_sun_weather_3d,
    _single_triangle_envelope,
    _angle_diff_deg,
    _wall_envelope,
    _no_sun_weather_3d_wind,
    _veg_box,
    SCENE_LAT,
    SCENE_LON,
    SCENE_GROUND_Z,
    SCENE_SLOPE_X,
    BDTOPO_OFFSET,
    HOUSE,
    FLAT,
    SHED,
    GONE,
    TREE,
    _scene_ground,
    _scene_points,
    _scene_bdtopo,
    _closed_and_outward,
    _terrain_env,
    _vertical_wall_envelope,
    _box_building,
)


class WallSteadyStateTest(SimpleTestCase):
    """Lot F, étape 1 — en régime permanent, le flux doit retomber sur la formule
    manuelle U = 1 / (1/h_e + Sigma(e/lam) + 1/h_i), indépendamment de tout ce que
    fait le solveur en interne."""

    databases = []

    def _run_to_steady_state(self, layers, dx_max, h_e, h_i, t_ext, t_int, hours=400):
        weather = _no_sun_weather_1d([t_ext] * hours)
        payload = {
            'layers': layers, 'dx_max': dx_max, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': (t_ext + t_int) / 2.0, 'weather': weather,
        }
        result = solver.run_simulation(payload)
        last, prev = result['temperatures'][-1], result['temperatures'][-2]
        # Régime établi : le dernier pas ne doit (quasiment) plus bouger.
        for a, b in zip(last, prev):
            self.assertAlmostEqual(a, b, delta=1e-6)
        return last[0], last[-1]  # T de surface extérieure, T de surface intérieure

    def test_single_layer(self):
        layer = _flat_wall_layer(e=0.1, lam=1.0)
        h_e, h_i, t_ext, t_int = 25.0, 8.0, 0.0, 20.0
        t0, t_last = self._run_to_steady_state([layer], dx_max=0.2, h_e=h_e, h_i=h_i, t_ext=t_ext, t_int=t_int)

        u_value = 1.0 / (1.0 / h_e + layer['e'] / layer['lam'] + 1.0 / h_i)
        q_expected = u_value * (t_ext - t_int)

        q_ext = h_e * (t_ext - t0)
        q_int = h_i * (t_last - t_int)
        self.assertAlmostEqual(q_ext, q_expected, delta=abs(q_expected) * 1e-3)
        self.assertAlmostEqual(q_int, q_expected, delta=abs(q_expected) * 1e-3)

    def test_multi_layer_finely_meshed(self):
        # Deux couches, maillées en plusieurs éléments chacune (dx_max << e) —
        # contrairement à test_single_layer, exerce vraiment la subdivision de
        # _build_mesh, pas seulement le cas à un unique élément par couche.
        layers = [_flat_wall_layer(e=0.10, lam=1.0), _flat_wall_layer(e=0.08, lam=0.035, rho=25.0, c=1030.0)]
        h_e, h_i, t_ext, t_int = 25.0, 8.0, -5.0, 19.0
        t0, t_last = self._run_to_steady_state(layers, dx_max=0.01, h_e=h_e, h_i=h_i, t_ext=t_ext, t_int=t_int)

        u_value = 1.0 / (1.0 / h_e + sum(l['e'] / l['lam'] for l in layers) + 1.0 / h_i)
        q_expected = u_value * (t_ext - t_int)

        q_ext = h_e * (t_ext - t0)
        q_int = h_i * (t_last - t_int)
        self.assertAlmostEqual(q_ext, q_expected, delta=abs(q_expected) * 1e-3)
        self.assertAlmostEqual(q_int, q_expected, delta=abs(q_expected) * 1e-3)


class WallEnergyConservationTest(SimpleTestCase):
    """Lot F, étape 2 (volet 1D) — identité exacte (pas une approximation) issue
    d'Euler implicite sur un mur à un seul élément : la variation d'énergie
    stockée doit égaler exactement le flux entrant moins le flux sortant, intégré
    sur toute la durée. Capacités nodales (rho*c*e/2) prises depuis la convention
    documentée dans solver._assemble_kc (« capacité concentrée »), pas recalculées
    en appelant la fonction elle-même — sinon un bug s'y reproduirait côté oracle."""

    databases = []

    def test_transient_run_conserves_energy(self):
        layer = _flat_wall_layer(e=0.1, lam=1.0, rho=1400.0, c=1000.0)
        h_e, h_i, t_int = 25.0, 8.0, 19.0
        hours = 30
        # Météo variable (pas une constante) : une erreur qui s'annulerait sur un
        # régime constant serait aussi détectée ici.
        t_ext_series = [5.0 + 8.0 * math.sin(h / 5.0) for h in range(hours)]
        weather = _no_sun_weather_1d(t_ext_series)
        payload = {
            'layers': [layer], 'dx_max': 0.2, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 10.0, 'weather': weather,
        }
        result = solver.run_simulation(payload)
        temps = result['temperatures']
        self.assertEqual(result['n_wall_nodes'], 2)  # un seul élément : 2 nœuds, pas de nœud d'air

        c_node = layer['rho'] * layer['c'] * layer['e'] / 2.0  # capacité concentrée, un seul élément
        t0_init, t_last_init = temps[0]
        t0_final, t_last_final = temps[-1]
        energy_stored = c_node * (t0_final - t0_init) + c_node * (t_last_final - t_last_init)

        energy_in = 0.0
        for point, T in zip(weather, temps[1:]):
            t0n, t_lastn = T
            q_ext_in = h_e * (point['t_ext'] - t0n)
            q_int_out = h_i * (t_lastn - t_int)
            energy_in += (q_ext_in - q_int_out) * DT_SECONDS

        self.assertAlmostEqual(energy_stored, energy_in, delta=abs(energy_in) * 1e-6 + 1e-3)


class Building1D3DConsistencyTest(SimpleTestCase):
    """Lot F, étape 3 — un bâtiment réduit à un unique triangle doit reproduire
    (à l'arrondi de maillage près, ici identique puisque même dx_max) le résultat
    de solver.run_simulation avec la même paroi/météo/conditions. Aire du
    triangle volontairement != 1 m² pour vérifier que la pondération par aire
    (building_solver.py:94-119) ne change rien à la température, seulement à la
    puissance absolue (non testée ici, cf. WallEnergyConservationTest côté 1D)."""

    databases = []

    def test_single_triangle_matches_1d_wall(self):
        layers = [_flat_wall_layer(e=0.1, lam=1.0), _flat_wall_layer(e=0.08, lam=0.035, rho=25.0, c=1030.0)]
        h_e, h_i, t_int, dx_max = 25.0, 8.0, 19.0, 0.02
        hours = 15
        t_ext_series = [3.0 + 6.0 * math.sin(h / 4.0) for h in range(hours)]

        payload_1d = {
            'layers': layers, 'dx_max': dx_max, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 12.0, 'weather': _no_sun_weather_1d(t_ext_series),
        }
        result_1d = solver.run_simulation(payload_1d)

        envelope = _single_triangle_envelope(area=3.7)  # aire arbitraire, != 1 m²
        payload_3d = {
            'dx_max': dx_max, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 12.0, 'weather': _no_sun_weather_3d(t_ext_series),
        }
        result_3d = building_solver.run_building_simulation(envelope, {1: layers}, None, payload_3d)

        self.assertAlmostEqual(
            result_3d['final_exterior_surface_temp'][0], result_1d['temperatures'][-1][0], places=6,
        )
        self.assertAlmostEqual(
            result_3d['final_interior_surface_temp'][0], result_1d['temperatures'][-1][-1], places=6,
        )


class TransmittedSolarGainTest(SimpleTestCase):
    """Rayonnement solaire qui traverse INTÉGRALEMENT une paroi sans jamais
    rencontrer de couche opaque (typiquement un vitrage sans encadrement
    opaque en fond, contrairement au mur Trombe déjà géré par le mécanisme
    existant) — avant ce correctif (2026-08-08), cette énergie disparaissait
    purement et simplement du bilan au lieu de chauffer l'air intérieur (voir
    to_do.md). Partagé 1D/3D via solver._propagate_solar : un seul endroit
    source de vérité, testé ici pour les deux solveurs."""

    databases = []

    # ── Formule pure (oracle indépendant, aucun appel au solveur complet) ──
    def test_single_opaque_layer_unaffected_no_interior_gain(self):
        # Non-régression : le cas historique (mur opaque) ne change pas.
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        mesh = solver._build_mesh(layers, dx_max=0.05)
        sources = solver._propagate_solar(layers, e_glo=500.0, mesh=mesh)
        self.assertNotIn('interior', [s[0] for s in sources])
        self.assertIn('surface', [s[0] for s in sources])

    def test_trombe_wall_translucent_then_opaque_unaffected(self):
        # Cas documenté (page Théorie, section 05) : couche translucide SUIVIE
        # d'une couche opaque — toute l'énergie s'arrête à l'opaque, non-
        # régression du comportement déjà géré (rien ne doit fuiter en
        # "interior").
        translucent = {'e': 0.01, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.9, 'r': 0.0, 'alpha': 0.1}
        opaque = _flat_wall_layer(e=0.1, lam=1.0)
        layers = [translucent, opaque]
        mesh = solver._build_mesh(layers, dx_max=0.02)
        sources = solver._propagate_solar(layers, e_glo=500.0, mesh=mesh)
        self.assertNotIn('interior', [s[0] for s in sources])

    def test_single_translucent_layer_matches_hand_derived_transmission(self):
        tau, alpha = 0.87, 0.06
        layers = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': tau, 'r': 1 - tau - alpha, 'alpha': alpha}]
        mesh = solver._build_mesh(layers, dx_max=0.001)
        e_glo = 500.0
        sources = solver._propagate_solar(layers, e_glo, mesh)
        interior = [v for k, r, v in sources if k == 'interior']
        self.assertEqual(len(interior), 1)
        self.assertAlmostEqual(interior[0], tau * e_glo, places=6)

    def test_double_glazing_matches_hand_derived_product_of_transmittances(self):
        # 3 couches translucides successives (double vitrage réel du
        # catalogue) : le reliquat intérieur doit être le PRODUIT des tau,
        # preuve que chaque couche réduit bien e_inc pour la suivante avant
        # d'atteindre l'intérieur — pas seulement le tau de la dernière.
        layers = [
            {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06},
            {'e': 0.016, 'lam': 0.094, 'rho': 1.2, 'c': 1000, 'tau': 0.97, 'r': 0.01, 'alpha': 0.02},
            {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06},
        ]
        mesh = solver._build_mesh(layers, dx_max=0.001)
        e_glo = 500.0
        sources = solver._propagate_solar(layers, e_glo, mesh)
        interior = [v for k, r, v in sources if k == 'interior']
        self.assertEqual(len(interior), 1)
        self.assertAlmostEqual(interior[0], e_glo * 0.88 * 0.97 * 0.88, places=6)

    def test_zero_incident_radiation_gives_no_sources(self):
        layers = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.87, 'r': 0.07, 'alpha': 0.06}]
        mesh = solver._build_mesh(layers, dx_max=0.001)
        self.assertEqual(solver._propagate_solar(layers, e_glo=0.0, mesh=mesh), [])

    # ── Intégration 1D — conservation d'énergie étendue au nouveau terme ───
    def test_1d_free_mode_conserves_energy_with_transmitted_solar_gain(self):
        tau, alpha = 0.87, 0.06
        window = {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': tau, 'r': 1 - tau - alpha, 'alpha': alpha}
        h_e, h_i, c_air_int = 25.0, 8.0, 500.0  # C_air FAIBLE, comme le cas rapporté par l'utilisateur
        hours = 20
        weather = [
            {'t_ext': 5.0 + 3.0 * math.sin(h / 5.0), 'h_s': 30.0, 'theta_i': 0.0,
             'e_dir': 500.0, 'e_dif': 80.0}
            for h in range(hours)
        ]
        payload = {
            'layers': [window], 'dx_max': 0.01, 'h_e': h_e,
            'interior': {'mode': 'free', 'h_i': h_i, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather,
        }
        result = solver.run_simulation(payload)
        temps = result['temperatures']
        n_wall = result['n_wall_nodes']
        air_idx = n_wall  # mode 'free' : dernier DOF = nœud d'air

        t_air = [T[air_idx] for T in temps]
        t_last = [T[n_wall - 1] for T in temps]  # dernier nœud du mur (interface air)

        # Oracle indépendant de _propagate_solar : f_ciel par défaut
        # (wall_tilt_deg=90°) = (1+cos(90°))/2 = 0,5 ; theta_i=0° -> cos_ti=1.
        f_ciel = 0.5
        e_glo = 500.0 * 1.0 + 80.0 * f_ciel
        e_interior_expected = tau * e_glo

        energy_stored_air = c_air_int * (t_air[-1] - t_air[0])
        energy_from_wall_and_solar = sum(
            (h_i * (t_last_next - t_air_next) + e_interior_expected) * DT_SECONDS
            for t_last_next, t_air_next in zip(t_last[1:], t_air[1:])
        )

        self.assertAlmostEqual(
            energy_stored_air, energy_from_wall_and_solar,
            delta=abs(energy_from_wall_and_solar) * 1e-6 + 1e-3,
        )

    # ── Intégration 3D — conservation d'énergie étendue au nouveau terme ───
    def test_3d_free_mode_conserves_energy_with_transmitted_solar_gain(self):
        tau, alpha = 0.87, 0.06
        window = {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': tau, 'r': 1 - tau - alpha, 'alpha': alpha}
        area = 2.5
        # Normale +Z (tilt=0°, toiture) : voit le soleil dès que elevation>0,
        # quel que soit l'azimuth.
        envelope = _single_triangle_envelope(area=area)
        hours = 20
        weather = [
            {'t_ext': 5.0 + 3.0 * math.sin(h / 5.0), 'sun_azimuth': 180.0, 'sun_elevation': 30.0,
             'e_dir': 500.0, 'e_dif': 80.0}
            for h in range(hours)
        ]
        c_air_int = 500.0  # C_air FAIBLE, comme le cas rapporté par l'utilisateur
        payload = {
            'dx_max': 0.01, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, {1: [window]}, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']

        # Oracle indépendant de _propagate_solar/_assemble_F_hour : cos(theta_i)
        # = normale · direction (normale +Z, direction issue de
        # shadow.sun_direction — déjà testée indépendamment ailleurs) ; f_ciel
        # (toiture plate, tilt=0°) = (1+cos(0°))/2 = 1.
        direction = shadow.sun_direction(180.0, 30.0)
        cos_ti = max(float(direction[2]), 0.0)
        f_ciel = 1.0
        e_glo = 500.0 * cos_ti + 80.0 * f_ciel
        e_interior_expected = tau * e_glo * area

        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls_and_solar = sum((f + e_interior_expected) * DT_SECONDS for f in flux)

        self.assertAlmostEqual(
            energy_stored, energy_from_walls_and_solar,
            delta=abs(energy_from_walls_and_solar) * 1e-6 + 1e-3,
        )

    def test_low_c_air_int_heats_air_faster_than_glazing_nodes(self):
        # Reproduit directement le symptôme rapporté par l'utilisateur : avec
        # un C_air FAIBLE, l'air doit désormais chauffer plus vite que les
        # nœuds du vitrage (le rayonnement transmis les contourne pour aller
        # droit sur l'air) — avant ce correctif, c'était l'inverse quel que
        # soit C_air, faute de tout gain solaire direct sur l'air.
        tau, alpha = 0.87, 0.06
        window = {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': tau, 'r': 1 - tau - alpha, 'alpha': alpha}
        weather = [{'t_ext': 10.0, 'h_s': 45.0, 'theta_i': 0.0, 'e_dir': 800.0, 'e_dif': 100.0}] * 5
        payload = {
            'layers': [window], 'dx_max': 0.001, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 500.0},
            't_init': 10.0, 'weather': weather,
        }
        result = solver.run_simulation(payload)
        last = result['temperatures'][-1]
        t_air, t_glazing_nodes = last[-1], last[:-1]
        self.assertGreater(t_air, max(t_glazing_nodes))
