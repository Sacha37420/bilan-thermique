"""Tests physiques des solveurs (Lot F du to_do.md racine).

Aucun de ces tests ne touche la base de données — solver.py, building_solver.py,
shadow.py et geometry.py sont volontairement indépendants de Django (voir leurs
docstrings). SimpleTestCase le reflète : accès DB interdit, tests plus rapides.

Chaque test compare le résultat du solveur à un oracle INDÉPENDANT de son code
interne (formule physique manuelle ou identité algébrique dérivée à la main,
jamais un appel aux fonctions privées `_assemble_*`/`_build_mesh` du module
testé — sinon un bug dans ces fonctions se reproduirait à l'identique côté
« attendu » et le test ne verrait rien).
"""

import math
from unittest import mock

from django.test import SimpleTestCase, TestCase

from . import building_solver, elevation, geodata, geometry, serializers, shadow, solver, weather_source
from .models import Building, Environment

DT_SECONDS = 3600.0


def _flat_wall_layer(e=0.1, lam=1.0, rho=1400.0, c=1000.0):
    return {'e': e, 'lam': lam, 'rho': rho, 'c': c, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}


def _no_sun_weather_1d(t_ext_series):
    return [{'t_ext': t, 'h_s': -10.0, 'theta_i': 0.0, 'e_dir': 0.0, 'e_dif': 0.0} for t in t_ext_series]


def _no_sun_weather_3d(t_ext_series):
    return [{'t_ext': t, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}
            for t in t_ext_series]


def _single_triangle_envelope(area):
    """Un unique triangle rectangle isocèle d'aire `area`, normale +Z (tilt 0°)."""
    side = math.sqrt(2.0 * area)
    vertices = [[0.0, 0.0, 0.0], [side, 0.0, 0.0], [0.0, side, 0.0]]
    triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])
    return {'vertices': vertices, 'triangles': triangles}


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


class BuildingAirNodeEnergyBalanceTest(SimpleTestCase):
    """Lot F, étape 2 (volet 3D) + étape 4 (mode thermostat) — identité exacte sur
    le nœud d'air global, dérivée de la même façon qu'en 1D : sa variation
    d'énergie stockée égale exactement le flux net reçu des parois
    (`envelope_flux_w`, déjà retourné par l'API) plus l'éventuelle puissance HVAC.
    N'utilise que des champs publics de run_building_simulation, jamais K_global/
    C_global directement."""

    databases = []

    def _envelope_and_weather(self, hours, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        return envelope, {1: layers}, _no_sun_weather_3d(t_ext_series)

    def test_free_mode_air_node_conserves_energy(self):
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=25)
        c_air_int = 300_000.0
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']
        self.assertEqual(len(t_air), len(weather) + 1)
        self.assertEqual(len(flux), len(weather))

        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls = sum(f * DT_SECONDS for f in flux)
        self.assertAlmostEqual(energy_stored, energy_from_walls, delta=abs(energy_from_walls) * 1e-6 + 1e-3)

    def test_free_mode_air_node_conserves_energy_with_ventilation(self):
        # Même identité que test_free_mode_air_node_conserves_energy, avec un
        # terme de ventilation en plus (Lot G) : C_air*dT_air = Sigma(flux +
        # g_vent*(t_ext-T_air))*dt. g_vent recalculé ici via la formule
        # documentée (publique) de run_building_simulation, jamais en import ant
        # une variable interne du module.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=25)
        c_air_int = 300_000.0
        debit_vent_m3h, eta_recup_vent = 80.0, 0.6
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int,
                         'debit_vent_m3h': debit_vent_m3h, 'eta_recup_vent': eta_recup_vent},
            't_init': 15.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']
        g_vent = 0.34 * debit_vent_m3h * (1.0 - eta_recup_vent)

        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls_and_vent = sum(
            (f + g_vent * (point['t_ext'] - t_air_next)) * DT_SECONDS
            for f, point, t_air_next in zip(flux, weather, t_air[1:])
        )
        self.assertAlmostEqual(
            energy_stored, energy_from_walls_and_vent,
            delta=abs(energy_from_walls_and_vent) * 1e-6 + 1e-3,
        )
        # Non-régression : plus de ventilation doit refroidir davantage un
        # bâtiment plus chaud que l'extérieur (mêmes conditions, sans vent).
        payload_no_vent = dict(payload)
        payload_no_vent['interior'] = {**payload['interior'], 'debit_vent_m3h': 0.0, 'eta_recup_vent': 0.0}
        result_no_vent = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_no_vent)
        self.assertLess(result['t_air'][-1], result_no_vent['t_air'][-1])

    def test_free_mode_air_node_conserves_energy_with_apports_internes(self):
        # Meme identite que test_free_mode_air_node_conserves_energy, avec un
        # terme d'apports internes constant en plus (Lot H) : C_air*dT_air =
        # Sigma(flux + apports_internes_w)*dt — pas de dependance a T_ext/T_air
        # contrairement a g_vent, donc pas de zip avec t_air necessaire ici.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=25)
        c_air_int = 300_000.0
        apports_internes_w = 250.0
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int,
                         'apports_internes_w': apports_internes_w},
            't_init': 15.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']

        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls_and_gains = sum((f + apports_internes_w) * DT_SECONDS for f in flux)
        self.assertAlmostEqual(
            energy_stored, energy_from_walls_and_gains,
            delta=abs(energy_from_walls_and_gains) * 1e-6 + 1e-3,
        )
        # Non-regression : des apports internes doivent laisser le batiment
        # plus chaud, toutes choses egales par ailleurs (memes murs/meteo).
        payload_no_gains = dict(payload)
        payload_no_gains['interior'] = {**payload['interior'], 'apports_internes_w': 0.0}
        result_no_gains = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_no_gains)
        self.assertGreater(result['t_air'][-1], result_no_gains['t_air'][-1])

    def test_imposed_mode_ignores_apports_internes(self):
        # Meme raisonnement que test_imposed_mode_ignores_ventilation : sans
        # noeud d'air libre, Dirichlet ecrase la ligne du noeud d'air, donc
        # apports_internes_w ne doit avoir strictement aucun effet.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=10)

        def run(apports):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0,
                             'apports_internes_w': apports},
                't_init': 19.0, 'weather': weather,
            }
            return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        r0 = run(0.0)
        r1 = run(5000.0)
        self.assertEqual(r0['flux_positive_kwh'], r1['flux_positive_kwh'])
        self.assertEqual(r0['flux_negative_kwh'], r1['flux_negative_kwh'])

    def test_thermostat_mode_hvac_matches_residual_energy_balance(self):
        # Amplitude volontairement large (-5°C a +35°C) et t_min/t_max serrés :
        # force le thermostat a chauffer ET climatiser dans la meme serie, pas
        # juste un seul cas — un helper partage n'irait pas assez haut/bas.
        hours = 40
        t_ext_series = [15.0 + 20.0 * math.sin(h / 6.0) for h in range(hours)]
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=2.5)
        paroi_layers = {1: layers}
        weather = _no_sun_weather_3d(t_ext_series)
        c_air_int = 300_000.0
        t_min, t_max = 19.0, 21.0
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'thermostat', 'h_i': 8.0, 'c_air_int': c_air_int, 't_min': t_min, 't_max': t_max},
            't_init': 20.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        # Vérifie que le scénario exerce bien les deux régimes (sinon le test ne
        # dirait rien sur le signe du résidu HVAC).
        self.assertGreater(result['heating_kwh'], 0.0)
        self.assertGreater(result['cooling_kwh'], 0.0)

        t_air = result['t_air']
        flux = result['envelope_flux_w']
        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls = sum(f * DT_SECONDS for f in flux)
        hvac_energy = (result['heating_kwh'] - result['cooling_kwh']) * 3.6e6  # kWh -> J

        self.assertAlmostEqual(
            energy_stored, energy_from_walls + hvac_energy,
            delta=abs(energy_from_walls + hvac_energy) * 1e-6 + 1e-3,
        )

    def test_imposed_mode_ignores_ventilation(self):
        # Sans nœud d'air libre, g_vent ne doit avoir strictement aucun effet
        # (voir le commentaire dans run_building_simulation, mode 'imposed').
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=10)

        def run(debit):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0,
                             'debit_vent_m3h': debit, 'eta_recup_vent': 0.0},
                't_init': 19.0, 'weather': weather,
            }
            return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        r0 = run(0.0)
        r1 = run(500.0)
        self.assertEqual(r0['flux_positive_kwh'], r1['flux_positive_kwh'])
        self.assertEqual(r0['flux_negative_kwh'], r1['flux_negative_kwh'])


class GroundBoundaryTest(SimpleTestCase):
    """Lot K — un triangle marqué boundary='ground' doit échanger avec
    payload['t_ground'] (constant), jamais avec weather[h]['t_ext'] — vérifié
    par une identité EXACTE (pas une convergence de régime permanent) : le
    triangle 'ground' voit une météo t_ext très différente et variable, mais
    doit reproduire au chiffre près un mur 1D alimenté par une météo
    CONSTANTE égale à t_ground (même dx_max/h_i/t_int/t_init/heures).

    Mis à jour au Lot AB3 : la conductance côté sol vaut désormais 1/r_ground et
    non plus h_e. Le `h_e` du payload 3D est laissé volontairement à une valeur
    TRÈS différente (25 contre 1/r_ground = 2) — le test vérifie donc du même
    coup qu'il est bien ignoré pour ce triangle."""

    databases = []

    def test_ground_triangle_uses_t_ground_not_weather_t_ext(self):
        layers = [_flat_wall_layer(e=0.1, lam=1.0), _flat_wall_layer(e=0.08, lam=0.035, rho=25.0, c=1030.0)]
        h_e, h_i, t_int, dx_max = 25.0, 8.0, 19.0, 0.02
        hours = 20
        t_ground = 13.0
        r_ground = 0.5

        vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        triangles = geometry.compute_envelope_geometry(
            vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1, 'boundary': 'ground'}],
        )
        envelope_ground = {'vertices': vertices, 'triangles': triangles}
        # t_ext délibérément très différent de t_ground et variable dans le
        # temps : ne doit avoir strictement aucune influence sur ce triangle.
        t_ext_series = [-10.0 + 40.0 * math.sin(h / 3.0) for h in range(hours)]
        payload_ground = {
            'dx_max': dx_max, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 15.0, 'weather': _no_sun_weather_3d(t_ext_series), 't_ground': t_ground,
            'r_ground': r_ground,
        }
        result_ground = building_solver.run_building_simulation(envelope_ground, {1: layers}, None, payload_ground)

        payload_ref = {
            # h_e = 1/r_ground côté référence (Lot AB3) : c'est la résistance du
            # sol, et non un film convectif, qui ferme ce bord.
            'layers': layers, 'dx_max': dx_max, 'h_e': 1.0 / r_ground,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 15.0, 'weather': _no_sun_weather_1d([t_ground] * hours),
        }
        result_ref = solver.run_simulation(payload_ref)

        self.assertAlmostEqual(
            result_ground['final_exterior_surface_temp'][0], result_ref['temperatures'][-1][0], places=6,
        )
        self.assertAlmostEqual(
            result_ground['final_interior_surface_temp'][0], result_ref['temperatures'][-1][-1], places=6,
        )

    def test_exterior_air_boundary_unaffected_by_t_ground(self):
        # Non-régression : un triangle par défaut ('exterior_air', comportement
        # historique) ne doit jamais être influencé par t_ground.
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=2.0)
        weather = _no_sun_weather_3d([5.0] * 20)

        def run(t_ground):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0},
                't_init': 10.0, 'weather': weather, 't_ground': t_ground,
            }
            return building_solver.run_building_simulation(envelope, {1: layers}, None, payload)

        r0 = run(12.0)
        r1 = run(-50.0)
        self.assertEqual(r0['final_exterior_surface_temp'], r1['final_exterior_surface_temp'])
        self.assertEqual(r0['final_interior_surface_temp'], r1['final_interior_surface_temp'])


class ShadowSkyViewFactorTest(SimpleTestCase):
    """Lot F, étape 6 (volet facteur de vue du ciel) — sans obstacle, aucun rayon
    n'est jamais bloqué, donc le facteur retourné doit retomber EXACTEMENT sur la
    formule analytique (1+cos(tilt))/2 (propriété garantie par construction,
    documentée dans shadow.compute_sky_view_factors — ce test la fige)."""

    databases = []

    def test_no_obstacle_matches_analytic_formula(self):
        cases = [
            ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], 0.0),   # toiture plate, normale +Z
            ([[0, 0, 0], [1, 0, 0], [0, 0, 1]], 90.0),  # mur vertical
        ]
        for vertices, expected_tilt in cases:
            triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2]}])
            self.assertAlmostEqual(triangles[0]['tilt_deg'], expected_tilt, places=6)
            envelope = {'vertices': vertices, 'triangles': triangles}
            factors = shadow.compute_sky_view_factors(envelope, environment_envelope=None, n_samples=256)
            expected = (1.0 + math.cos(math.radians(expected_tilt))) / 2.0
            self.assertAlmostEqual(factors[0], expected, delta=1e-9)


class ShadowOcclusionTest(SimpleTestCase):
    """Lot F, étape 6 (volet occlusion) — un obstacle plat directement au-dessus
    d'un triangle tourné vers le ciel doit le masquer au zénith (élévation 90°,
    présente dans la grille par défaut), sans obstacle il doit y rester visible."""

    databases = []

    def _flat_roof_envelope(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2]}])
        return {'vertices': vertices, 'triangles': triangles}

    def _overhead_panel(self):
        # Grand panneau horizontal à 5 m d'altitude, largement plus grand que le
        # triangle du bâtiment : bloque tout rayon vertical vers le haut.
        vertices = [[-5, -5, 5], [5, -5, 5], [5, 5, 5], [-5, 5, 5]]
        triangles = [{'v': [0, 1, 2]}, {'v': [0, 2, 3]}]
        return {'vertices': vertices, 'triangles': triangles}

    def test_visible_without_obstacle(self):
        grid = shadow.compute_visibility_grid(self._flat_roof_envelope(), environment_envelope=None)
        zenith_index = grid['elevations_deg'].index(90.0)
        self.assertTrue(all(grid['per_triangle'][0][ai][zenith_index] == 1
                             for ai in range(len(grid['azimuths_deg']))))

    def test_blocked_with_overhead_obstacle(self):
        grid = shadow.compute_visibility_grid(
            self._flat_roof_envelope(), environment_envelope=self._overhead_panel(),
        )
        zenith_index = grid['elevations_deg'].index(90.0)
        self.assertTrue(all(grid['per_triangle'][0][ai][zenith_index] == 0
                             for ai in range(len(grid['azimuths_deg']))))


def _angle_diff_deg(a, b):
    """Écart angulaire signé minimal a-b, dans [-180, 180] — évite les faux
    échecs 0 vs 360 quand on compare des azimuths modulo 360."""
    return (a - b + 180.0) % 360.0 - 180.0


class SolarEphemerisTest(SimpleTestCase):
    """Lot L — déclinaison solaire et équation du temps (weather_source._declination_deg/
    _equation_of_time_min, formules de Spencer 1971). Oracle : bornes et faits
    astronomiques indépendants de la formule elle-même (inclinaison de l'axe terrestre
    ≈23,44°, bien établie), PAS des valeurs de référence recopiées d'une autre
    calculatrice solaire — pour ne pas simplement retester la même formule sous un
    autre nom."""

    databases = []

    def test_declination_never_exceeds_axial_tilt(self):
        for n in range(1, 366):
            gamma = 2.0 * math.pi * (n - 1) / 365.0
            self.assertLessEqual(abs(weather_source._declination_deg(gamma)), 23.6)

    def test_declination_sign_matches_hemisphere_season(self):
        # ~21 juin (jour 172) : été hémisphère nord, déclinaison nettement positive.
        gamma_june = 2.0 * math.pi * (172 - 1) / 365.0
        self.assertGreater(weather_source._declination_deg(gamma_june), 20.0)
        # ~21 décembre (jour 355) : hiver hémisphère nord, déclinaison nettement négative.
        gamma_dec = 2.0 * math.pi * (355 - 1) / 365.0
        self.assertLess(weather_source._declination_deg(gamma_dec), -20.0)

    def test_equation_of_time_bounded(self):
        # Écart connu entre temps solaire vrai et temps solaire moyen : quelques
        # dizaines de minutes au plus sur toute l'année (borne large et sûre,
        # pas une valeur de référence précise à une date donnée).
        for n in range(1, 366):
            gamma = 2.0 * math.pi * (n - 1) / 365.0
            self.assertLess(abs(weather_source._equation_of_time_min(gamma)), 20.0)


class SolarElevationAzimuthTest(SimpleTestCase):
    """Lot L — weather_source._elevation_azimuth (cœur géométrique, sans notion de
    date/heure). Oracle : identités algébriques dérivées à la main à des angles
    horaires remarquables (voir docstring de chaque test), jamais un second appel à
    la fonction testée."""

    databases = []

    def test_solar_noon_azimuth_and_elevation_hand_derived(self):
        # A l'angle horaire 0 (midi solaire vrai), la composante Est du vecteur
        # soleil est nulle par construction (-cos(decl)*sin(0)=0) : l'azimuth ne
        # peut valoir que 0 ou 180° selon le signe de (declinaison-latitude), et
        # l'elevation vaut exactement 90-|latitude-declinaison| — deux identités
        # dérivées à la main (voir weather_source._elevation_azimuth, pas une
        # resolution numérique).
        cases = [
            (48.8566, 10.0),   # Paris, decl < lat -> soleil au sud
            (10.0, 20.0),      # decl > lat -> soleil au nord
            (-33.8688, -10.0),  # hémisphère sud, decl > lat -> soleil au nord
        ]
        for lat, decl in cases:
            azimuth, elevation = weather_source._elevation_azimuth(lat, decl, 0.0)
            expected_elevation = 90.0 - abs(lat - decl)
            expected_azimuth = 180.0 if decl < lat else 0.0
            self.assertAlmostEqual(elevation, expected_elevation, places=6)
            self.assertAlmostEqual(azimuth, expected_azimuth, places=6)

    def test_equinox_sunrise_sunset_due_east_west(self):
        # A declinaison nulle (equinoxe), le lever (HA=-90) et le coucher (HA=+90)
        # se produisent exactement a l'horizon plein est / plein ouest, a N'IMPORTE
        # QUELLE latitude — fait astronomique elementaire independant de toute
        # formule de position solaire.
        for lat in (-60.0, -23.5, 0.0, 23.5, 60.0, 80.0):
            az_rise, el_rise = weather_source._elevation_azimuth(lat, 0.0, -90.0)
            az_set, el_set = weather_source._elevation_azimuth(lat, 0.0, 90.0)
            self.assertAlmostEqual(el_rise, 0.0, places=6)
            self.assertAlmostEqual(el_set, 0.0, places=6)
            self.assertAlmostEqual(az_rise, 90.0, places=6)
            self.assertAlmostEqual(az_set, 270.0, places=6)

    def test_time_symmetry_around_solar_noon(self):
        # L'elevation ne depend de H qu'au travers de cos(H) (fonction paire) :
        # elevation(H) == elevation(-H) exactement. L'azimuth, lui, doit etre le
        # miroir : azimuth(-H) == 360 - azimuth(H). Ces deux identites decoulent de
        # la structure algebrique de _elevation_azimuth (Est est impaire en H, Nord
        # est paire), independamment des valeurs numeriques choisies ici.
        cases = [(48.8566, 15.0, 37.0), (-10.0, -5.0, 82.0), (60.0, -20.0, 150.0)]
        for lat, decl, ha in cases:
            az_pos, el_pos = weather_source._elevation_azimuth(lat, decl, ha)
            az_neg, el_neg = weather_source._elevation_azimuth(lat, decl, -ha)
            self.assertAlmostEqual(el_pos, el_neg, places=9)
            self.assertAlmostEqual(_angle_diff_deg(az_neg, 360.0 - az_pos), 0.0, places=6)


class LocalAzimuthRotationTest(SimpleTestCase):
    """Lot L, point d'attention critique du to_do.md — weather_source.to_local_azimuth
    doit appliquer EXACTEMENT la même rotation que geodata._rotate_xy (même
    convention Building.georef_north_offset_deg), pour que météo et géométrie restent
    dans le même repère. Oracle : géométrie vectorielle dérivée à la main, pas un
    second appel à _rotate_xy (qui pourrait partager le même bug de signe)."""

    databases = []

    def test_zero_offset_is_identity(self):
        for az in (0.0, 45.0, 90.0, 180.0, 270.0, 359.9):
            self.assertAlmostEqual(
                _angle_diff_deg(weather_source.to_local_azimuth(az, 0.0), az), 0.0, places=6,
            )

    def test_ninety_degree_offset_rotates_south_to_local_east_axis(self):
        # Si l'axe +Y local pointe le Est reel (offset=90), alors l'axe +X local
        # (90° horaire apres +Y, geometry.py) pointe le Sud reel : un soleil reel
        # plein sud (azimuth reel 180°) doit donc avoir un azimuth LOCAL de 90°.
        self.assertAlmostEqual(weather_source.to_local_azimuth(180.0, 90.0), 90.0, places=6)

    def test_ninety_degree_offset_rotates_north_to_local_negative_x(self):
        # Meme configuration : le Nord reel (azimuth reel 0°) devient l'axe -X
        # local, soit un azimuth local de 270°.
        self.assertAlmostEqual(weather_source.to_local_azimuth(0.0, 90.0), 270.0, places=6)


class WeatherSeriesAssemblyTest(SimpleTestCase):
    """Lot L — weather_source._assemble_weather_series, partie pure (pas de réseau :
    prend directement une réponse Open-Meteo Archive synthétique, même format que la
    vraie API vérifiée en réel — voir project_bilan_thermique.md)."""

    databases = []

    def test_assembles_series_with_correct_shape_and_clamping(self):
        data = {
            'hourly': {
                'time': ['2023-06-21T10:00', '2023-06-21T11:00', '2023-06-21T12:00'],
                'temperature_2m': [20.0, 500.0, -500.0],           # hors bornes exprès
                'direct_normal_irradiance': [600.0, 5000.0, -10.0],  # hors bornes exprès
                'diffuse_radiation': [100.0, 5000.0, -10.0],         # hors bornes exprès
            },
        }
        series, n_missing = weather_source._assemble_weather_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 3)
        self.assertEqual(n_missing, 0)
        self.assertEqual(series[1]['t_ext'], weather_source.T_EXT_MAX)
        self.assertEqual(series[2]['t_ext'], weather_source.T_EXT_MIN)
        self.assertEqual(series[1]['e_dir'], weather_source.E_DIR_MAX)
        self.assertEqual(series[2]['e_dir'], 0.0)
        self.assertEqual(series[1]['e_dif'], weather_source.E_DIF_MAX)
        self.assertEqual(series[2]['e_dif'], 0.0)
        for point in series:
            self.assertTrue(0.0 <= point['sun_azimuth'] < 360.0)
            self.assertTrue(-90.0 <= point['sun_elevation'] <= 90.0)

    def test_skips_hours_with_missing_data(self):
        data = {
            'hourly': {
                'time': ['2023-06-21T10:00', '2023-06-21T11:00'],
                'temperature_2m': [20.0, None],
                'direct_normal_irradiance': [600.0, 500.0],
                'diffuse_radiation': [100.0, 90.0],
            },
        }
        series, n_missing = weather_source._assemble_weather_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 1)
        self.assertEqual(n_missing, 1)

    def test_no_hourly_data_raises(self):
        with self.assertRaises(weather_source.WeatherSourceError):
            weather_source._assemble_weather_series(48.8566, 2.3522, {'hourly': {'time': []}})

    def test_series_points_validate_against_public_serializer(self):
        # Integration avec le contrat public de l'app : chaque point assemblé doit
        # etre accepté par BuildingWeatherPointSerializer (le meme serializer que
        # celui utilisé au moment du calcul), pas seulement "dans les bornes"
        # vérifiées à la main ci-dessus.
        data = {
            'hourly': {
                'time': [f'2023-06-21T{h:02d}:00' for h in range(24)],
                'temperature_2m': [15.0 + h for h in range(24)],
                'direct_normal_irradiance': [0.0 if h < 6 or h > 20 else 500.0 for h in range(24)],
                'diffuse_radiation': [0.0 if h < 6 or h > 20 else 80.0 for h in range(24)],
            },
        }
        series, _ = weather_source._assemble_weather_series(48.8566, 2.3522, data, north_offset_deg=37.0)
        for point in series:
            s = serializers.BuildingWeatherPointSerializer(data=point)
            self.assertTrue(s.is_valid(), s.errors)


class PvgisTimestampTest(SimpleTestCase):
    """Lot S — weather_source._parse_pvgis_timestamp ('YYYYMMDD:HHMM', format PVGIS,
    différent du format ISO d'Open-Meteo)."""

    databases = []

    def test_parses_midnight(self):
        dt = weather_source._parse_pvgis_timestamp('20050101:0000')
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2005, 1, 1, 0, 0))

    def test_parses_non_midnight_time(self):
        dt = weather_source._parse_pvgis_timestamp('20081215:1430')
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2008, 12, 15, 14, 30))


class TmySeriesAssemblyTest(SimpleTestCase):
    """Lot S — weather_source._assemble_tmy_series, partie pure (pas de réseau :
    prend directement une réponse PVGIS TMY synthétique, même format que la vraie
    API vérifiée en réel — voir project_bilan_thermique.md). Gb(n)/Gd(h) sont les
    mêmes grandeurs physiques que direct_normal_irradiance/diffuse_radiation
    d'Open-Meteo (irradiance directe normale au rayon / diffuse horizontale), donc
    les mêmes tests de clamping/heures manquantes que WeatherSeriesAssemblyTest
    s'appliquent, juste avec le format brut PVGIS en entrée."""

    databases = []

    def test_assembles_tmy_series_with_correct_shape_and_clamping(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': '20050621:1000', 'T2m': 20.0, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                    {'time(UTC)': '20060215:1200', 'T2m': 500.0, 'Gb(n)': 5000.0, 'Gd(h)': 5000.0},
                    {'time(UTC)': '20081201:0000', 'T2m': -500.0, 'Gb(n)': -10.0, 'Gd(h)': -10.0},
                ],
            },
        }
        series, n_missing = weather_source._assemble_tmy_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 3)
        self.assertEqual(n_missing, 0)
        self.assertEqual(series[1]['t_ext'], weather_source.T_EXT_MAX)
        self.assertEqual(series[2]['t_ext'], weather_source.T_EXT_MIN)
        self.assertEqual(series[1]['e_dir'], weather_source.E_DIR_MAX)
        self.assertEqual(series[2]['e_dir'], 0.0)
        for point in series:
            self.assertTrue(0.0 <= point['sun_azimuth'] < 360.0)
            self.assertTrue(-90.0 <= point['sun_elevation'] <= 90.0)

    def test_skips_hours_with_missing_pvgis_data(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': '20050621:1000', 'T2m': 20.0, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                    {'time(UTC)': '20050621:1100', 'T2m': None, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                ],
            },
        }
        series, n_missing = weather_source._assemble_tmy_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 1)
        self.assertEqual(n_missing, 1)

    def test_no_hourly_data_raises(self):
        with self.assertRaises(weather_source.WeatherSourceError):
            weather_source._assemble_tmy_series(48.8566, 2.3522, {'outputs': {'tmy_hourly': []}})

    def test_series_points_validate_against_public_serializer(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': f'20050621:{h:02d}00', 'T2m': 15.0 + h,
                     'Gb(n)': 0.0 if h < 6 or h > 20 else 500.0, 'Gd(h)': 0.0 if h < 6 or h > 20 else 80.0}
                    for h in range(24)
                ],
            },
        }
        series, _ = weather_source._assemble_tmy_series(48.8566, 2.3522, data, north_offset_deg=37.0)
        for point in series:
            s = serializers.BuildingWeatherPointSerializer(data=point)
            self.assertTrue(s.is_valid(), s.errors)


class TmyFallbackTest(SimpleTestCase):
    """Lot S — weather_source.build_tmy_or_fallback_series doit replier sur
    Open-Meteo Archive quand PVGIS échoue (zone non couverte ou autre erreur), et
    retourner explicitement quelle source a produit le résultat (to_do.md, étape 3 :
    ne jamais laisser la source ambiguë). build_tmy_series/build_weather_series
    mockées : ce test vérifie le BRANCHEMENT, pas le réseau (déjà couvert par les
    tests d'assemblage ci-dessus + vérification en réel, voir mémoire projet)."""

    databases = []

    def test_uses_pvgis_when_available(self):
        fake_series = [{'t_ext': 10.0}]
        with mock.patch.object(weather_source, 'build_tmy_series', return_value=(fake_series, 0)) as m_tmy, \
                mock.patch.object(weather_source, 'build_weather_series') as m_archive:
            series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
                48.8566, 2.3522, '2023-01-01', '2023-01-02',
            )
        self.assertEqual(series, fake_series)
        self.assertEqual(n_missing, 0)
        self.assertEqual(source, 'pvgis-tmy')
        self.assertIsNone(warning)
        m_tmy.assert_called_once()
        m_archive.assert_not_called()

    def test_falls_back_to_archive_when_pvgis_unavailable(self):
        fake_series = [{'t_ext': 5.0}]
        with mock.patch.object(
            weather_source, 'build_tmy_series',
            side_effect=weather_source.WeatherSourceError("PVGIS TMY : Location over the sea."),
        ), mock.patch.object(weather_source, 'build_weather_series', return_value=(fake_series, 0)) as m_archive:
            series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
                0.0, -160.0, '2023-01-01', '2023-01-02', north_offset_deg=15.0,
                utc_offset_seconds=-11 * 3600,
            )
        self.assertEqual(series, fake_series)
        self.assertEqual(source, 'open-meteo-archive')
        self.assertIsNotNone(warning)
        self.assertIn('over the sea', warning)
        # Assertion volontairement stricte sur la signature complète : le repli
        # doit transmettre TOUS les paramètres, pas seulement ceux qu'il
        # connaissait au Lot S. C'est ce qui a fait remonter, au Lot AB4, qu'un
        # nouveau paramètre devait bien traverser ce chemin-là aussi.
        m_archive.assert_called_once_with(
            0.0, -160.0, '2023-01-01', '2023-01-02',
            north_offset_deg=15.0, utc_offset_seconds=-11 * 3600,
        )


class HourlyPlanningTest(SimpleTestCase):
    """Lot Q — plannings horaires (ventilation + apports internes). Point dur
    vérifié : g_vent variable par heure change K_global (pas seulement F), donc
    la factorisation LU doit être refaite par valeur DISTINCTE de g_vent
    rencontrée (voir building_solver._factorize_for_g_vent) — ces tests portent
    sur le résultat physique produit, jamais sur l'implémentation de la
    factorisation elle-même."""

    databases = []

    def _envelope_and_weather(self, hours, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        return envelope, {1: layers}, _no_sun_weather_3d(t_ext_series)

    def test_constant_planning_matches_constant_interior_all_modes(self):
        # Un planning dont les 24 entrées sont toutes identiques aux constantes
        # équivalentes de `interior` doit produire un résultat identique au
        # chemin sans planning, dans les trois modes — non-régression sur le
        # nouveau mécanisme de bundles factorisés par g_vent.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=50)
        debit, eta, apports = 80.0, 0.6, 250.0
        base_variants = [
            {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0},
            {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            {'mode': 'thermostat', 'h_i': 8.0, 'c_air_int': 300_000.0, 't_min': 18.0, 't_max': 21.0},
        ]
        planning = [{'debit_vent_m3h': debit, 'eta_recup_vent': eta, 'apports_internes_w': apports}] * 24

        for base in base_variants:
            with self.subTest(mode=base['mode']):
                payload_constant = {
                    'dx_max': 0.02, 'h_e': 25.0,
                    'interior': {**base, 'debit_vent_m3h': debit, 'eta_recup_vent': eta, 'apports_internes_w': apports},
                    't_init': 15.0, 'weather': weather,
                }
                result_constant = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_constant)

                payload_planning = {
                    'dx_max': 0.02, 'h_e': 25.0,
                    'interior': dict(base),
                    't_init': 15.0, 'weather': weather, 'planning': planning,
                }
                result_planning = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_planning)

                self.assertEqual(result_constant['t_air'], result_planning['t_air'])
                self.assertEqual(result_constant['heating_kwh'], result_planning['heating_kwh'])
                self.assertEqual(result_constant['cooling_kwh'], result_planning['cooling_kwh'])

    def test_heure_debut_selects_correct_planning_slot(self):
        # Un planning avec une forte ventilation au seul index 5 : heure_debut=5
        # doit faire utiliser CET index dès hour_idx=0.
        envelope, paroi_layers, _ = self._envelope_and_weather(hours=1)
        weather_one_hour = _no_sun_weather_3d([5.0])
        planning = [{'debit_vent_m3h': 0.0, 'eta_recup_vent': 0.0, 'apports_internes_w': 0.0} for _ in range(24)]
        planning[5] = {'debit_vent_m3h': 500.0, 'eta_recup_vent': 0.0, 'apports_internes_w': 0.0}

        def run(heure_debut):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
                't_init': 19.0, 'weather': weather_one_hour, 'planning': planning, 'heure_debut': heure_debut,
            }
            return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        result_slot5 = run(heure_debut=5)  # hour_idx=0 -> slot (5+0)%24=5 -> forte ventilation
        result_slot0 = run(heure_debut=0)  # hour_idx=0 -> slot 0 -> ventilation nulle

        # t_init=19°C > t_ext=5°C, sans soleil : plus de ventilation doit
        # refroidir davantage (même raisonnement que le test de non-régression
        # du Lot G).
        self.assertLess(result_slot5['t_air'][-1], result_slot0['t_air'][-1])

    def test_free_mode_conserves_energy_with_varying_planning(self):
        # Identité de conservation (même famille que Lot F/G), généralisée à un
        # g_vent/apports_internes_w VARIABLE heure par heure : le planning ci-
        # dessous a jusqu'à 12 combinaisons (débit, rendement) distinctes sur 24
        # heures (lcm(3,4)), exerçant réellement plusieurs bundles factorisés.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=50)
        c_air_int = 300_000.0
        planning = [
            {'debit_vent_m3h': 20.0 + 10.0 * (h % 3), 'eta_recup_vent': 0.1 * (h % 4),
             'apports_internes_w': 50.0 * (h % 5)}
            for h in range(24)
        ]
        heure_debut = 7
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather, 'planning': planning, 'heure_debut': heure_debut,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']
        energy_stored = c_air_int * (t_air[-1] - t_air[0])

        energy_from_walls_vent_gains = 0.0
        for h, (f, point, t_air_next) in enumerate(zip(flux, weather, t_air[1:])):
            entry = planning[(heure_debut + h) % 24]
            g_vent = 0.34 * entry['debit_vent_m3h'] * (1.0 - entry['eta_recup_vent'])
            energy_from_walls_vent_gains += (
                f + g_vent * (point['t_ext'] - t_air_next) + entry['apports_internes_w']
            ) * DT_SECONDS

        self.assertAlmostEqual(
            energy_stored, energy_from_walls_vent_gains,
            delta=abs(energy_from_walls_vent_gains) * 1e-6 + 1e-3,
        )

    def test_imposed_mode_ignores_planning(self):
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=10)
        planning_high = [
            {'debit_vent_m3h': 5000.0, 'eta_recup_vent': 0.0, 'apports_internes_w': 100_000.0} for _ in range(24)
        ]

        def run(planning):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0},
                't_init': 19.0, 'weather': weather,
            }
            if planning is not None:
                payload['planning'] = planning
            return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        r0 = run(None)
        r1 = run(planning_high)
        self.assertEqual(r0['flux_positive_kwh'], r1['flux_positive_kwh'])
        self.assertEqual(r0['flux_negative_kwh'], r1['flux_negative_kwh'])


class PlanningSerializerTest(SimpleTestCase):
    """Lot Q — validation de BuildingCalculRequestSerializer.planning/heure_debut."""

    databases = []

    def _base_payload(self, **overrides):
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0,
            'weather': [{'t_ext': 5.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}],
        }
        payload.update(overrides)
        return payload

    def test_planning_must_have_exactly_24_entries(self):
        entry = {'debit_vent_m3h': 10.0, 'eta_recup_vent': 0.5, 'apports_internes_w': 100.0}
        s = serializers.BuildingCalculRequestSerializer(data=self._base_payload(planning=[entry] * 23))
        self.assertFalse(s.is_valid())
        self.assertIn('planning', s.errors)

    def test_planning_with_24_entries_and_heure_debut_valid(self):
        entry = {'debit_vent_m3h': 10.0, 'eta_recup_vent': 0.5, 'apports_internes_w': 100.0}
        s = serializers.BuildingCalculRequestSerializer(
            data=self._base_payload(planning=[entry] * 24, heure_debut=5),
        )
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(len(s.validated_data['planning']), 24)
        self.assertEqual(s.validated_data['heure_debut'], 5)

    def test_planning_omitted_defaults_absent_and_heure_debut_to_zero(self):
        s = serializers.BuildingCalculRequestSerializer(data=self._base_payload())
        self.assertTrue(s.is_valid(), s.errors)
        self.assertNotIn('planning', s.validated_data)
        self.assertEqual(s.validated_data['heure_debut'], 0)


class WindowFrameTest(SimpleTestCase):
    """Lot I — cadre de fenêtre. Le maillage vitrage existant (solaire, capacité)
    garde sa physique complète mais sur une aire réduite à (1-frame_fraction)*aire ;
    le cadre lui-même est traité comme une résistance directe extérieur->nœud
    d'air, comme g_vent (Lot G) mais par triangle. Voir
    building_solver.run_building_simulation, docstring du paramètre paroi_frame_by_id."""

    databases = []

    def _envelope_and_weather(self, hours, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        return envelope, {1: layers}, _no_sun_weather_3d(t_ext_series)

    def test_frame_fraction_zero_matches_no_frame_info_at_all(self):
        # frame_fraction=0 (cadre nul) doit être un no-op EXACT : aucune aire
        # réduite (1-0=1), aucune conductance (0*frame_u*aire=0).
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=30)
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        result_no_frame = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)
        result_zero_frame = building_solver.run_building_simulation(
            envelope, paroi_layers, None, payload, paroi_frame_by_id={1: (2.0, 0.0)},
        )
        self.assertEqual(result_no_frame['t_air'], result_zero_frame['t_air'])
        self.assertEqual(result_no_frame['envelope_flux_w'], result_zero_frame['envelope_flux_w'])

    def test_free_mode_conserves_energy_with_frame(self):
        # Identité de conservation (même famille que Lot F/G/Q) : le flux mural
        # (envelope_flux_w) ne porte plus QUE la contribution vitrage (aire
        # réduite) — le cadre est un terme séparé, comme g_vent.
        for frame_fraction in (0.3, 0.9):
            with self.subTest(frame_fraction=frame_fraction):
                envelope, paroi_layers, weather = self._envelope_and_weather(hours=25)
                c_air_int = 300_000.0
                frame_u = 2.0
                payload = {
                    'dx_max': 0.02, 'h_e': 25.0,
                    'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
                    't_init': 15.0, 'weather': weather,
                }
                result = building_solver.run_building_simulation(
                    envelope, paroi_layers, None, payload,
                    paroi_frame_by_id={1: (frame_u, frame_fraction)},
                )

                t_air = result['t_air']
                flux = result['envelope_flux_w']
                area = envelope['triangles'][0]['area']
                frame_g = frame_fraction * frame_u * area

                energy_stored = c_air_int * (t_air[-1] - t_air[0])
                energy_from_walls_and_frame = sum(
                    (f + frame_g * (point['t_ext'] - t_air_next)) * DT_SECONDS
                    for f, point, t_air_next in zip(flux, weather, t_air[1:])
                )
                self.assertAlmostEqual(
                    energy_stored, energy_from_walls_and_frame,
                    delta=abs(energy_from_walls_and_frame) * 1e-6 + 1e-3,
                )

    def test_glazing_area_reduction_matches_smaller_triangle_flux(self):
        # Piège évité : en mode 'imposed', la température de surface est
        # INVARIANTE par aire (déjà établi par Building1D3DConsistencyTest —
        # l'aire cancelle algébriquement dans le système local d'un triangle
        # découplé, puisque K/C/F sont TOUS scalés par la même aire). Comparer
        # des températures de surface entre deux aires différentes ne peut donc
        # RIEN prouver sur la réduction d'aire elle-même — seule la PUISSANCE
        # ABSOLUE (envelope_flux_w, en W) en dépend. Mode 'imposed' choisi
        # exprès : frame_g y est sans effet (Dirichlet écrase la ligne du nœud
        # d'air, comme g_vent/apports_internes_w), donc la comparaison isole
        # PUREMENT la réduction d'aire du vitrage, sans contamination par la
        # conductance propre du cadre.
        layers = [_flat_wall_layer(e=0.1, lam=1.0), _flat_wall_layer(e=0.08, lam=0.035, rho=25.0, c=1030.0)]
        h_e, h_i, t_int, dx_max = 25.0, 8.0, 19.0, 0.02
        hours = 20
        weather = [
            {'t_ext': 5.0 + 3.0 * math.sin(h / 5.0), 'sun_azimuth': 180.0,
             'sun_elevation': 30.0 if 6 <= h % 24 <= 18 else -10.0,
             'e_dir': 500.0 if 6 <= h % 24 <= 18 else 0.0, 'e_dif': 80.0 if 6 <= h % 24 <= 18 else 0.0}
            for h in range(hours)
        ]
        area_full = 4.0
        frame_fraction = 0.4

        envelope_reduced = _single_triangle_envelope(area=area_full * (1.0 - frame_fraction))
        payload_reduced = {
            'dx_max': dx_max, 'h_e': h_e,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': 12.0, 'weather': weather,
        }
        result_reduced = building_solver.run_building_simulation(
            envelope_reduced, {1: layers}, None, payload_reduced,
        )

        envelope_full = _single_triangle_envelope(area=area_full)
        payload_full = dict(payload_reduced)
        result_full = building_solver.run_building_simulation(
            envelope_full, {1: layers}, None, payload_full,
            paroi_frame_by_id={1: (3.0, frame_fraction)},
        )

        for f_reduced, f_full in zip(result_reduced['envelope_flux_w'], result_full['envelope_flux_w']):
            self.assertAlmostEqual(f_reduced, f_full, places=6)

    def test_frame_only_affects_triangles_using_that_paroi_model(self):
        # Bâtiment à deux triangles, modèles de paroi différents. Comparaison
        # différentielle plutôt qu'une égalité stricte sur le triangle 2 : les
        # deux triangles partagent le MÊME nœud d'air, donc changer le triangle 1
        # perturbe forcément (légèrement) la dynamique du triangle 2 aussi — ce
        # n'est pas un bug, c'est le couplage voulu par le nœud d'air partagé.
        # Le vrai test : {1: cadre} et {1: cadre, 2: MÊME cadre} doivent donner
        # des résultats DIFFÉRENTS — un bug qui appliquerait le cadre à tous les
        # triangles indépendamment de paroi_model_id rendrait ces deux scénarios
        # indiscernables (le triangle 2 aurait déjà "secrètement" le cadre dans
        # le premier cas).
        area1, area2 = 2.5, 3.0
        side1, side2 = math.sqrt(2.0 * area1), math.sqrt(2.0 * area2)
        vertices = [
            [0.0, 0.0, 0.0], [side1, 0.0, 0.0], [0.0, side1, 0.0],
            [10.0, 0.0, 0.0], [10.0 + side2, 0.0, 0.0], [10.0, side2, 0.0],
        ]
        triangles = geometry.compute_envelope_geometry(vertices, [
            {'v': [0, 1, 2], 'paroi_model_id': 1},
            {'v': [3, 4, 5], 'paroi_model_id': 2},
        ])
        envelope = {'vertices': vertices, 'triangles': triangles}
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        paroi_layers = {1: layers, 2: layers}
        weather = _no_sun_weather_3d([2.0 + 10.0 * math.sin(h / 6.0) for h in range(20)])

        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }

        def run(paroi_frame_by_id):
            return building_solver.run_building_simulation(
                envelope, paroi_layers, None, payload, paroi_frame_by_id=paroi_frame_by_id,
            )

        result_only_1 = run({1: (2.0, 0.3)})
        result_both = run({1: (2.0, 0.3), 2: (2.0, 0.3)})
        self.assertNotEqual(result_only_1['t_air'], result_both['t_air'])

        # Et sans aucun cadre, un résultat encore différent des deux précédents
        # (confirme que le cadre a bien un effet du tout, pas seulement qu'il
        # distingue 1 de 2).
        result_none = run(None)
        self.assertNotEqual(result_none['t_air'], result_only_1['t_air'])

    def test_invalid_frame_fraction_raises(self):
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=5)
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        with self.assertRaises(building_solver.BuildingSimulationError):
            building_solver.run_building_simulation(
                envelope, paroi_layers, None, payload, paroi_frame_by_id={1: (2.0, 1.0)},
            )


def _wall_envelope(area):
    """Un unique triangle vertical (tilt_deg=90°, normale horizontale) — pour
    distinguer de _single_triangle_envelope (normale +Z, tilt=0°, toiture)."""
    side = math.sqrt(2.0 * area)
    vertices = [[0.0, 0.0, 0.0], [side, 0.0, 0.0], [0.0, 0.0, side]]
    triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])
    return {'vertices': vertices, 'triangles': triangles}


def _no_sun_weather_3d_wind(t_ext_series, wind_series):
    return [
        {'t_ext': t, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0, 'wind_m_s': w}
        for t, w in zip(t_ext_series, wind_series)
    ]


class DynamicConvectionTest(SimpleTestCase):
    """Lot R — h_e dynamique (vent, corrélation de Jürges) et h_i par orientation
    (ISO 6946). Comme pour le Lot Q (g_vent variable par heure), h_e variable
    par heure change K_global (pas seulement F) : la factorisation LU doit être
    refaite par COMBINAISON DISTINCTE de (g_vent, h_e) rencontrée (voir
    building_solver._factorize_for) — ces tests portent sur le résultat
    physique produit, jamais sur l'implémentation de la factorisation
    elle-même."""

    databases = []

    def _envelope_and_weather(self, hours, wind=5.0, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        wind_series = [wind] * hours
        return envelope, {1: layers}, _no_sun_weather_3d_wind(t_ext_series, wind_series)

    # ── Formules pures (oracle indépendant) ──────────────────────────────
    def test_h_e_from_wind_matches_jurges_formula(self):
        self.assertAlmostEqual(building_solver.h_e_from_wind(0.0), 5.8)
        self.assertAlmostEqual(building_solver.h_e_from_wind(5.0), 5.8 + 3.94 * 5.0)
        self.assertAlmostEqual(building_solver.h_e_from_wind(10.0), 5.8 + 3.94 * 10.0)
        # Vent négatif (ne devrait jamais arriver en pratique) clampé à 0 plutôt
        # que de réduire h_e sous sa valeur à vent nul.
        self.assertAlmostEqual(building_solver.h_e_from_wind(-3.0), 5.8)

    def test_h_i_from_tilt_buckets(self):
        # Plafond/toiture (flux montant) : tilt proche de 0°, jusqu'au seuil inclus.
        self.assertEqual(building_solver.h_i_from_tilt(0.0), building_solver.H_I_CEILING)
        self.assertEqual(building_solver.h_i_from_tilt(60.0), building_solver.H_I_CEILING)
        # Mur (flux horizontal) : entre les deux seuils.
        self.assertEqual(building_solver.h_i_from_tilt(60.001), building_solver.H_I_WALL)
        self.assertEqual(building_solver.h_i_from_tilt(90.0), building_solver.H_I_WALL)
        self.assertEqual(building_solver.h_i_from_tilt(119.999), building_solver.H_I_WALL)
        # Plancher/sol (flux descendant) : tilt proche de 180°, à partir du seuil inclus.
        self.assertEqual(building_solver.h_i_from_tilt(120.0), building_solver.H_I_FLOOR)
        self.assertEqual(building_solver.h_i_from_tilt(180.0), building_solver.H_I_FLOOR)

    # ── h_e dynamique — intégration via run_building_simulation ──────────
    def test_h_e_dynamic_with_constant_wind_matches_equivalent_constant_h_e(self):
        # Vent constant à 5 m/s sur tout le run : h_e_dynamic doit produire un
        # résultat EXACTEMENT identique à h_e_dynamic=False avec la constante
        # h_e_from_wind(5.0) posée à la main — même physique, deux chemins de
        # code différents.
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=40, wind=5.0)
        h_e_equiv = building_solver.h_e_from_wind(5.0)

        payload_dynamic = {
            'dx_max': 0.02, 'h_e': 999.0, 'h_e_dynamic': True,  # h_e ignorée en mode dynamique
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        payload_constant = {
            'dx_max': 0.02, 'h_e': h_e_equiv,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        result_dynamic = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_dynamic)
        result_constant = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_constant)

        self.assertEqual(result_dynamic['t_air'], result_constant['t_air'])
        self.assertEqual(result_dynamic['envelope_flux_w'], result_constant['envelope_flux_w'])

    def test_h_e_dynamic_rounds_wind_to_nearest_integer(self):
        # 5,4 m/s et 5,0 m/s doivent produire le MÊME h_e (round(5.4)=5) — la
        # discrétisation au m/s près est ce qui borne le nombre de
        # factorisations distinctes sur un run réel (to_do.md, Lot R).
        envelope, paroi_layers, weather_54 = self._envelope_and_weather(hours=30, wind=5.4)
        _, _, weather_50 = self._envelope_and_weather(hours=30, wind=5.0)

        def run(weather):
            payload = {
                'dx_max': 0.02, 'h_e': 999.0, 'h_e_dynamic': True,
                'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
                't_init': 15.0, 'weather': weather,
            }
            return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

        self.assertEqual(run(weather_54)['t_air'], run(weather_50)['t_air'])

    def test_h_e_dynamic_missing_wind_raises(self):
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=5, wind=5.0)
        del weather[2]['wind_m_s']  # une seule heure sans vent suffit
        payload = {
            'dx_max': 0.02, 'h_e': 25.0, 'h_e_dynamic': True,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        with self.assertRaises(building_solver.BuildingSimulationError):
            building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

    # ── h_i par orientation — intégration via run_building_simulation ────
    def test_h_i_auto_matches_equivalent_constant_for_wall_triangle(self):
        # Un unique triangle vertical (tilt=90°) : h_i_auto doit choisir
        # H_I_WALL, donc produire un résultat identique à h_i_auto=False avec
        # h_i=H_I_WALL posé à la main.
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _wall_envelope(area=2.5)
        weather = _no_sun_weather_3d([2.0 + 10.0 * math.sin(h / 6.0) for h in range(40)])

        payload_auto = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i_auto': True, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        payload_manual = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': building_solver.H_I_WALL, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        result_auto = building_solver.run_building_simulation(envelope, {1: layers}, None, payload_auto)
        result_manual = building_solver.run_building_simulation(envelope, {1: layers}, None, payload_manual)

        self.assertEqual(result_auto['t_air'], result_manual['t_air'])

    def test_h_i_auto_differs_from_default_for_roof_triangle(self):
        # _single_triangle_envelope a une normale +Z (tilt=0°, toiture) :
        # h_i_auto doit choisir H_I_CEILING (10.0) — différent de H_I_WALL
        # (7.7), donc un résultat différent d'un h_i constant à 7.7. Confirme
        # que h_i_auto a bien un effet réel, pas un no-op silencieux.
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=2.5)
        weather = _no_sun_weather_3d([2.0 + 10.0 * math.sin(h / 6.0) for h in range(40)])

        def run(interior_extra):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'free', 'c_air_int': 300_000.0, **interior_extra},
                't_init': 15.0, 'weather': weather,
            }
            return building_solver.run_building_simulation(envelope, {1: layers}, None, payload)

        result_auto = run({'h_i_auto': True})
        result_wall_constant = run({'h_i': building_solver.H_I_WALL})
        self.assertNotEqual(result_auto['t_air'], result_wall_constant['t_air'])

    # ── Non-régression : comportement par défaut inchangé ────────────────
    def test_defaults_unchanged_without_h_e_dynamic_or_h_i_auto(self):
        # Ni h_e_dynamic ni h_i_auto dans le payload : comportement strictement
        # identique à avant ce lot (h_e/h_i constants classiques).
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=20)
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, paroi_layers, None, payload)
        self.assertEqual(result['hours'], 20)
        self.assertTrue(all(math.isfinite(t) for t in result['t_air']))

    # ── Conservation d'énergie avec g_vent ET h_e variables simultanément ─
    def test_free_mode_conserves_energy_with_varying_wind_and_planning(self):
        # Identité de conservation (même famille que Lot F/Q), généralisée à
        # g_vent ET h_e VARIABLES heure par heure en même temps — le test le
        # plus sévère pour le cache de bundles factorisés par (g_vent, h_e) :
        # une factorisation réutilisée à tort pour la mauvaise combinaison
        # casserait cette identité de façon quasi certaine sur autant de
        # combinaisons distinctes.
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=2.5)
        hours = 60
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        wind_series = [1.0 + (h % 7) for h in range(hours)]  # 7 valeurs de vent distinctes
        weather = _no_sun_weather_3d_wind(t_ext_series, wind_series)

        c_air_int = 300_000.0
        planning = [
            {'debit_vent_m3h': 20.0 + 10.0 * (h % 3), 'eta_recup_vent': 0.1 * (h % 4),
             'apports_internes_w': 50.0 * (h % 5)}
            for h in range(24)
        ]
        payload = {
            'dx_max': 0.02, 'h_e': 999.0, 'h_e_dynamic': True,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather, 'planning': planning,
        }
        result = building_solver.run_building_simulation(envelope, {1: layers}, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']
        energy_stored = c_air_int * (t_air[-1] - t_air[0])

        energy_from_walls_vent_gains = 0.0
        for h, (f, point, t_air_next) in enumerate(zip(flux, weather, t_air[1:])):
            entry = planning[h % 24]
            g_vent = 0.34 * entry['debit_vent_m3h'] * (1.0 - entry['eta_recup_vent'])
            energy_from_walls_vent_gains += (
                f + g_vent * (point['t_ext'] - t_air_next) + entry['apports_internes_w']
            ) * DT_SECONDS

        self.assertAlmostEqual(
            energy_stored, energy_from_walls_vent_gains,
            delta=abs(energy_from_walls_vent_gains) * 1e-6 + 1e-3,
        )

    def test_h_e_dynamic_switch_converges_to_new_steady_state(self):
        # Piège identifié en écrivant les tests (avant tout bug réel) : une
        # identité de conservation d'énergie comme celle du test précédent est
        # TAUTOLOGIQUE vis-à-vis de K (elle re-dérive la ligne du nœud d'air du
        # système linéaire RÉELLEMENT résolu, quel que soit K) — elle ne peut
        # donc PAS détecter un bundle réutilisé à tort pour le mauvais h_e
        # (vérifié par mutation : "key = (g_vent, h_e)" réduit à "(g_vent,)"
        # laisse ce test-ci intact). Ce test-ci compare au contraire à un
        # oracle INDÉPENDANT du système résolu (formule U(h_e) en régime
        # permanent, même méthode que WallSteadyStateTest) : vent constant
        # (donc h_e constant) sur une longue première phase, puis vent
        # CONSTANT différent sur une seconde phase assez longue pour reconverger
        # — le flux final doit correspondre au NOUVEAU h_e, pas être resté
        # bloqué sur l'ancien (mode 'imposed' : g_vent toujours nul, donc la clé
        # de cache ne varie qu'avec h_e — piège direct pour _factorize_for).
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        # Aire 1 m² : envelope_flux_w est en watts ABSOLUS (aire x W/m², voir
        # docstring du module), alors que l'oracle U(h_e) ci-dessous est en
        # W/m² — aire=1 les rend directement comparables sans facteur d'échelle.
        envelope = _single_triangle_envelope(area=1.0)
        h_i = 8.0
        t_ext, t_int = 5.0, 20.0
        wind_phase1, wind_phase2 = 2.0, 15.0
        hours_per_phase = 400
        wind_series = [wind_phase1] * hours_per_phase + [wind_phase2] * hours_per_phase
        t_ext_series = [t_ext] * (2 * hours_per_phase)
        weather = _no_sun_weather_3d_wind(t_ext_series, wind_series)

        payload = {
            'dx_max': 0.02, 'h_e': 999.0, 'h_e_dynamic': True,
            'interior': {'mode': 'imposed', 'h_i': h_i, 't_int': t_int},
            't_init': (t_ext + t_int) / 2.0, 'weather': weather,
        }
        result = building_solver.run_building_simulation(envelope, {1: layers}, None, payload)

        h_e_phase2 = building_solver.h_e_from_wind(wind_phase2)
        u_value = 1.0 / (1.0 / h_e_phase2 + layers[0]['e'] / layers[0]['lam'] + 1.0 / h_i)
        q_expected = u_value * (t_ext - t_int)
        q_actual = result['envelope_flux_w'][-1]

        self.assertAlmostEqual(q_actual, q_expected, delta=abs(q_expected) * 1e-2)


class BuildingCalculRequestSerializerWindTest(SimpleTestCase):
    """Lot R — validation de BuildingCalculRequestSerializer.h_e_dynamic et
    BuildingInteriorSerializer.h_i_auto."""

    databases = []

    def _base_payload(self, **overrides):
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0,
            'weather': [{'t_ext': 10.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}],
        }
        payload.update(overrides)
        return payload

    def test_h_e_dynamic_requires_wind_on_every_point(self):
        payload = self._base_payload(h_e_dynamic=True)  # weather sans wind_m_s
        s = serializers.BuildingCalculRequestSerializer(data=payload)
        self.assertFalse(s.is_valid())
        self.assertIn('weather', s.errors)

    def test_h_e_dynamic_valid_with_wind_on_every_point(self):
        payload = self._base_payload(
            h_e_dynamic=True,
            weather=[{'t_ext': 10.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0,
                      'e_dir': 0.0, 'e_dif': 0.0, 'wind_m_s': 4.0}],
        )
        s = serializers.BuildingCalculRequestSerializer(data=payload)
        self.assertTrue(s.is_valid(), s.errors)

    def test_h_i_required_unless_auto(self):
        payload = self._base_payload(interior={'mode': 'free', 'c_air_int': 300_000.0})
        s = serializers.BuildingCalculRequestSerializer(data=payload)
        self.assertFalse(s.is_valid())
        self.assertIn('interior', s.errors)

    def test_h_i_optional_when_auto(self):
        payload = self._base_payload(interior={'mode': 'free', 'h_i_auto': True, 'c_air_int': 300_000.0})
        s = serializers.BuildingCalculRequestSerializer(data=payload)
        self.assertTrue(s.is_valid(), s.errors)


class MovableShadingTest(SimpleTestCase):
    """Lot J — occultations mobiles (volets, stores), toujours ENTIÈREMENT
    fermées quand actives (pas de position intermédiaire). Généralise le
    mécanisme du Lot R (h_e par combinaison distincte factorisée
    paresseusement) à un h_e PAR TRIANGLE quand un dispositif est fermé
    (_h_e_diagonal) — même famille de piège que le Lot R si la clé de cache
    omettait volets_fermes."""

    databases = []

    def _envelope_and_weather(self, hours, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        t_ext_series = [2.0 + 10.0 * math.sin(h / 6.0) for h in range(hours)]
        return envelope, {1: layers}, _no_sun_weather_3d(t_ext_series)

    def _sun_weather_3d(self, hours, e_dir=500.0, e_dif=80.0, t_ext=None):
        return [
            {'t_ext': t_ext if t_ext is not None else 5.0 + 3.0 * math.sin(h / 5.0),
             'sun_azimuth': 180.0, 'sun_elevation': 30.0, 'e_dir': e_dir, 'e_dif': e_dif}
            for h in range(hours)
        ]

    def _shaded_envelope(self, profile_id, area=2.5):
        # Normale +Z (tilt=0°) : voit le soleil dès elevation>0, quel que
        # soit l'azimuth — même triangle que _single_triangle_envelope, mais
        # avec un shading_profile_id sur son unique triangle.
        side = math.sqrt(2.0 * area)
        vertices = [[0.0, 0.0, 0.0], [side, 0.0, 0.0], [0.0, side, 0.0]]
        triangles = geometry.compute_envelope_geometry(
            vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1, 'shading_profile_id': profile_id}],
        )
        return {'vertices': vertices, 'triangles': triangles}

    # ── Catalogue + formule (oracle direct, aucun appel au solveur) ───────
    def test_shading_profiles_catalogue_sane(self):
        for pid, profile in building_solver.SHADING_PROFILES.items():
            with self.subTest(profile=pid):
                self.assertGreater(profile['delta_r'], 0.0)
                self.assertTrue(0.0 <= profile['fs_dir'] <= 1.0)
                self.assertTrue(0.0 <= profile['fs_dif'] <= 1.0)

    def test_h_e_diagonal_matches_series_resistance_formula(self):
        h_e_vec = [1.0 / (1.0 / 25.0 + 0.20), 25.0]  # triangle 0 fermé, triangle 1 sans dispositif
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        systems = building_solver._build_triangle_systems(
            [{'paroi_model_id': 1}, {'paroi_model_id': 1}], {1: layers}, dx_max=0.05,
        )
        offsets = [0, systems[0][0]['n_wall_nodes']]
        n_dof = offsets[1] + systems[1][0]['n_wall_nodes'] + 1
        areas = [2.0, 3.0]
        addition = building_solver._h_e_diagonal(h_e_vec, systems, offsets, n_dof, areas)
        # Oracle indépendant : h_e_vec[i] * area_i sur la diagonale du premier
        # nœud de chaque triangle — formule à la main, rien de recalculé via
        # _propagate_solar/_assemble_F_hour.
        self.assertAlmostEqual(addition[0, 0], h_e_vec[0] * areas[0], places=8)
        self.assertAlmostEqual(addition[offsets[1], offsets[1]], h_e_vec[1] * areas[1], places=8)

    # ── Volet roulant fermé ≡ h_e réduit (série) + rayonnement nul ────────
    def test_volet_roulant_closed_matches_equivalent_manual_h_e_and_zero_sun(self):
        area = 2.5
        envelope_shaded = self._shaded_envelope('volet-roulant', area=area)
        envelope_plain = _single_triangle_envelope(area=area)
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        weather_sun = self._sun_weather_3d(hours=30)
        planning_always_closed = [{'volets_fermes': True}] * 24

        payload_shaded = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather_sun, 'planning': planning_always_closed,
        }
        result_shaded = building_solver.run_building_simulation(envelope_shaded, {1: layers}, None, payload_shaded)

        delta_r = building_solver.SHADING_PROFILES['volet-roulant']['delta_r']
        h_e_equiv = 1.0 / (1.0 / 25.0 + delta_r)
        weather_equiv = self._sun_weather_3d(hours=30, e_dir=0.0, e_dif=0.0)  # volet opaque : E=0
        payload_manual = {
            'dx_max': 0.02, 'h_e': h_e_equiv,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather_equiv,
        }
        result_manual = building_solver.run_building_simulation(envelope_plain, {1: layers}, None, payload_manual)

        self.assertEqual(result_shaded['t_air'], result_manual['t_air'])

    # ── Store extérieur : transmission réduite mais non nulle ────────────
    def test_store_exterieur_energy_conservation_with_reduced_transmission(self):
        # Même méthode que TransmittedSolarGainTest (Lot U) : identité de
        # conservation étendue à un terme calculé À LA MAIN (indépendant de
        # _propagate_solar/_assemble_F_hour), ici avec les fs_dir/fs_dif du
        # store — vérifie que la réduction appliquée est la BONNE, pas juste
        # qu'une réduction quelconque a lieu.
        tau, alpha = 0.87, 0.06
        window = {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': tau, 'r': 1 - tau - alpha, 'alpha': alpha}
        area = 2.5
        envelope = self._shaded_envelope('store-exterieur', area=area)
        hours = 20
        weather = self._sun_weather_3d(hours=hours)
        planning_closed = [{'volets_fermes': True}] * 24
        c_air_int = 500.0
        payload = {
            'dx_max': 0.01, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': c_air_int},
            't_init': 15.0, 'weather': weather, 'planning': planning_closed,
        }
        result = building_solver.run_building_simulation(envelope, {1: [window]}, None, payload)

        t_air = result['t_air']
        flux = result['envelope_flux_w']

        direction = shadow.sun_direction(180.0, 30.0)
        cos_ti = max(float(direction[2]), 0.0)
        f_ciel = 1.0
        profile = building_solver.SHADING_PROFILES['store-exterieur']
        e_glo_closed = 500.0 * profile['fs_dir'] * cos_ti + 80.0 * profile['fs_dif'] * f_ciel
        e_interior_expected = tau * e_glo_closed * area

        energy_stored = c_air_int * (t_air[-1] - t_air[0])
        energy_from_walls_and_solar = sum((f + e_interior_expected) * DT_SECONDS for f in flux)

        self.assertAlmostEqual(
            energy_stored, energy_from_walls_and_solar,
            delta=abs(energy_from_walls_and_solar) * 1e-6 + 1e-3,
        )

    def test_volet_transition_within_run_updates_h_e_not_just_optics(self):
        # Piège identifié EMPIRIQUEMENT (avant tout bug réel), même famille que
        # le Lot R : un premier essai avec du soleil montrait une différence
        # entre "ouvert->fermé" et "toujours ouvert" MÊME sous une mutation qui
        # retire volets_fermes de la clé de cache (key = (g_vent, h_e)) — parce
        # que la réduction optique (F, fs_dir/fs_dif) reste correcte heure par
        # heure indépendamment du cache, et suffit à elle seule à créer un
        # écart. Sans soleil, cette confusion disparaît : le SEUL effet
        # possible d'un volet fermé est la résistance ajoutée à h_e (K) — un
        # bundle réutilisé à tort pour la mauvaise heure ferait alors
        # RIGOUREUSEMENT disparaître tout écart avec "toujours ouvert" (vérifié
        # par mutation : les deux scénarios deviennent bit-à-bit identiques).
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = self._shaded_envelope('volet-roulant', area=2.5)
        weather = _no_sun_weather_3d([5.0, 5.0])
        # Heure 0 (slot 0) ouvert, heure 1 (slot 1) fermé.
        planning_mixed = [{'volets_fermes': False}, {'volets_fermes': True}] + [{'volets_fermes': False}] * 22

        def run(planning):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
                't_init': 15.0, 'weather': weather,
            }
            if planning is not None:
                payload['planning'] = planning
            return building_solver.run_building_simulation(envelope, {1: layers}, None, payload)

        result_mixed = run(planning_mixed)
        result_always_open = run(None)
        self.assertNotEqual(
            result_mixed['final_exterior_surface_temp'], result_always_open['final_exterior_surface_temp'],
        )

    # ── Deux triangles, deux dispositifs différents, même heure ───────────
    def test_two_triangles_different_profiles_behave_independently(self):
        area1, area2 = 2.5, 2.5
        side1 = math.sqrt(2.0 * area1)
        side2 = math.sqrt(2.0 * area2)
        vertices = [
            [0.0, 0.0, 0.0], [side1, 0.0, 0.0], [0.0, side1, 0.0],
            [10.0, 0.0, 0.0], [10.0 + side2, 0.0, 0.0], [10.0, side2, 0.0],
        ]
        triangles = geometry.compute_envelope_geometry(vertices, [
            {'v': [0, 1, 2], 'paroi_model_id': 1, 'shading_profile_id': 'volet-roulant'},
            {'v': [3, 4, 5], 'paroi_model_id': 1, 'shading_profile_id': 'store-exterieur'},
        ])
        envelope = {'vertices': vertices, 'triangles': triangles}
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        weather = self._sun_weather_3d(hours=20)
        planning_closed = [{'volets_fermes': True}] * 24
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather, 'planning': planning_closed,
        }
        result = building_solver.run_building_simulation(envelope, {1: layers}, None, payload)
        # Le volet (opaque, deltaR plus grand) doit finir plus proche de
        # l'extérieur froid que le store (transmission partielle, deltaR plus
        # petit) : sa surface extérieure encaisse moins de gain solaire ET
        # est mieux isolée -- deux effets qui vont dans le même sens.
        t_ext_volet = result['final_exterior_surface_temp'][0]
        t_ext_store = result['final_exterior_surface_temp'][1]
        self.assertNotEqual(t_ext_volet, t_ext_store)

    # ── h_e réduit s'applique aussi en mode 'imposed' (contrairement à g_vent) ──
    def test_volet_affects_imposed_mode_too(self):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope_shaded = self._shaded_envelope('volet-roulant', area=1.0)
        weather = _no_sun_weather_3d([5.0] * 10)
        planning_closed = [{'volets_fermes': True}] * 24

        def run(planning):
            payload = {
                'dx_max': 0.02, 'h_e': 25.0,
                'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 19.0},
                't_init': 19.0, 'weather': weather,
            }
            if planning is not None:
                payload['planning'] = planning
            return building_solver.run_building_simulation(envelope_shaded, {1: layers}, None, payload)

        result_open = run(None)
        result_closed = run(planning_closed)
        self.assertNotEqual(
            result_open['final_exterior_surface_temp'], result_closed['final_exterior_surface_temp'],
        )

    # ── Non-régression : triangle sans dispositif jamais affecté ──────────
    def test_triangle_without_shading_profile_unaffected_by_planning(self):
        envelope, paroi_layers, weather = self._envelope_and_weather(hours=20)
        planning_closed = [{'volets_fermes': True}] * 24
        payload_base = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 300_000.0},
            't_init': 15.0, 'weather': weather,
        }
        result_no_planning = building_solver.run_building_simulation(envelope, paroi_layers, None, payload_base)
        result_with_planning = building_solver.run_building_simulation(
            envelope, paroi_layers, None, {**payload_base, 'planning': planning_closed},
        )
        self.assertEqual(result_no_planning['t_air'], result_with_planning['t_air'])

    # ── Whitelist de reconstruction (piège du Lot K, généralisé) ─────────
    def test_build_envelope_preserves_shading_profile_id_on_partial_patch(self):
        # Building() ici n'est JAMAIS sauvegardé (pas de .save()) — construire
        # une instance en mémoire ne touche pas la DB, seul .update() (non
        # appelé ici) le ferait. _build_envelope reste donc testable dans ce
        # fichier SimpleTestCase (voir son docstring en tête de module).
        existing_envelope = {
            'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            'triangles': [{'v': [0, 1, 2], 'group': 'g1', 'paroi_model_id': 1,
                            'boundary': 'exterior_air', 'shading_profile_id': 'volet-roulant'}],
        }
        instance = Building(envelope=existing_envelope)
        s = serializers.BuildingSerializer(instance=instance)
        # 'triangles' ABSENT de validated_data (PATCH vertices seul) :
        # déclenche la reconstruction depuis l'enveloppe existante.
        validated_data = {'vertices': existing_envelope['vertices']}
        envelope = s._build_envelope(validated_data)
        self.assertEqual(envelope['triangles'][0]['shading_profile_id'], 'volet-roulant')


class TriangleShadingSerializerTest(SimpleTestCase):
    """Lot J — validation de TriangleInputSerializer.shading_profile_id et
    PlanningEntrySerializer.volets_fermes."""

    databases = []

    def test_valid_shading_profile_id_accepted(self):
        s = serializers.TriangleInputSerializer(data={'v': [0, 1, 2], 'shading_profile_id': 'volet-roulant'})
        self.assertTrue(s.is_valid(), s.errors)

    def test_unknown_shading_profile_id_rejected(self):
        s = serializers.TriangleInputSerializer(data={'v': [0, 1, 2], 'shading_profile_id': 'inexistant'})
        self.assertFalse(s.is_valid())

    def test_shading_profile_id_omitted_defaults_to_none(self):
        s = serializers.TriangleInputSerializer(data={'v': [0, 1, 2]})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertIsNone(s.validated_data['shading_profile_id'])

    def test_volets_fermes_omitted_defaults_false(self):
        s = serializers.PlanningEntrySerializer(data={})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertFalse(s.validated_data['volets_fermes'])


class ThermostatCalendarTest(SimpleTestCase):
    """Lot V — consignes t_min/t_max variables par heure (calendrier
    d'occupation résolu côté client à partir d'un profil d'usage : scolaire,
    tertiaire, habitation, climatisés ou non). Contrairement à g_vent/h_e/
    volets_fermes, t_min/t_max n'entrent JAMAIS dans K (seulement dans le
    choix du second membre à chaque heure) — aucun impact sur le cache de
    bundles, donc aucun risque de la famille des pièges Lots R/J."""

    databases = []

    def _envelope_and_layers(self, area=2.5):
        layers = [_flat_wall_layer(e=0.1, lam=1.0)]
        envelope = _single_triangle_envelope(area=area)
        return envelope, {1: layers}

    def _run(self, envelope, paroi_layers, weather, c_air_int=500.0):
        payload = {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'thermostat', 'h_i': 8.0, 'c_air_int': c_air_int, 't_min': 19.0, 't_max': 21.0},
            't_init': 19.0, 'weather': weather,
        }
        return building_solver.run_building_simulation(envelope, paroi_layers, None, payload)

    def test_defaults_unchanged_without_per_hour_override(self):
        # Non-régression : sans aucune surcharge, le comportement doit rester
        # celui des constantes interior.t_min/t_max — la clim doit s'engager
        # dès que l'air chaud dépasse t_max=21.
        envelope, paroi_layers = self._envelope_and_layers()
        weather = _no_sun_weather_3d([25.0] * 5)
        result = self._run(envelope, paroi_layers, weather)
        self.assertGreater(result['cooling_kwh'], 0.0)

    def test_explicit_none_in_weather_point_falls_back_to_constant(self):
        # Régression DIRECTE du piège trouvé en écrivant ce lot, AVANT tout
        # bug réel : BuildingWeatherPointSerializer (default=None) rend
        # 't_min'/'t_max' TOUJOURS présents dans le dict validé, avec la
        # valeur None si le client n'a rien fourni — point.get('t_min', repli)
        # ne retombe alors JAMAIS sur le repli (vérifié en réel avant
        # correction, voir _run_building_simulation). Reproduit ici le dict
        # EXACT que produirait le serializer, pas juste l'absence de la clé.
        envelope, paroi_layers = self._envelope_and_layers()
        weather = [
            {'t_ext': 25.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0,
             'wind_m_s': None, 't_min': None, 't_max': None}
            for _ in range(5)
        ]
        result = self._run(envelope, paroi_layers, weather)
        self.assertGreater(result['cooling_kwh'], 0.0)

    def test_per_hour_override_suspends_cooling_for_that_hour_only(self):
        envelope, paroi_layers = self._envelope_and_layers()
        hours = 10
        weather_const = _no_sun_weather_3d([25.0] * hours)
        weather_override = [dict(w) for w in weather_const]
        weather_override[5]['t_min'] = 19.0
        weather_override[5]['t_max'] = 100.0  # "hors gel" cette heure précise

        result_const = self._run(envelope, paroi_layers, weather_const)
        result_override = self._run(envelope, paroi_layers, weather_override)

        # Moins de refroidissement au total (une heure sans clim) et l'air
        # dépasse librement t_max=21 PENDANT cette heure précise seulement.
        self.assertLess(result_override['cooling_kwh'], result_const['cooling_kwh'])
        self.assertGreater(result_override['t_air'][6], 21.0)
        self.assertAlmostEqual(result_override['t_air'][7], 21.0, places=6)  # reclampé dès l'heure suivante

    def test_invalid_per_hour_bounds_raise(self):
        envelope, paroi_layers = self._envelope_and_layers()
        weather = [{'t_ext': 20.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0,
                    't_min': 22.0, 't_max': 20.0}]
        with self.assertRaises(building_solver.BuildingSimulationError):
            self._run(envelope, paroi_layers, weather)


class ExtrudeFootprintGroupedTest(SimpleTestCase):
    """Lot T (mode simplifié) — geodata.extrude_footprint_grouped. Partie pure
    (pas de réseau, contrairement à search_nearby_buildings) : la structure de
    faces de trimesh.creation.extrude_polygon a été vérifiée empiriquement (voir
    sa docstring), ces tests la figent pour détecter un changement de
    comportement de trimesh plutôt que de le découvrir en production."""

    databases = []

    def test_rectangle_four_walls(self):
        footprint = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
        vertices, triangles = geodata.extrude_footprint_grouped(footprint, height_m=2.5)
        groups = [t['group'] for t in triangles]
        self.assertEqual(groups.count('sol'), 2)
        self.assertEqual(groups.count('toiture'), 2)
        for i in range(1, 5):
            self.assertEqual(groups.count(f'mur_{i}'), 2)
        self.assertEqual(len(triangles), 12)

        boundaries = {t['group']: t['boundary'] for t in triangles}
        self.assertEqual(boundaries['sol'], 'ground')
        self.assertEqual(boundaries['toiture'], 'exterior_air')
        self.assertEqual(boundaries['mur_1'], 'exterior_air')

    def test_l_shape_six_walls(self):
        # Polygone non convexe (en L) -- plus représentatif d'une empreinte
        # IGN/OSM réelle qu'un simple rectangle.
        footprint = [(0, 0), (6, 0), (6, 4), (3, 4), (3, 7), (0, 7)]
        vertices, triangles = geodata.extrude_footprint_grouped(footprint, height_m=2.5)
        groups = [t['group'] for t in triangles]
        self.assertEqual(groups.count('sol'), 4)
        self.assertEqual(groups.count('toiture'), 4)
        for i in range(1, 7):
            self.assertEqual(groups.count(f'mur_{i}'), 2)
        self.assertEqual(len(triangles), 20)

    def test_sol_and_toiture_at_expected_elevation(self):
        # Oracle indépendant du comptage de groupes : le sol doit être
        # exactement au niveau z=0, la toiture exactement à z=height_m (les
        # deux capuchons de l'extrusion), peu importe comment trimesh a
        # numéroté ses faces en interne.
        footprint = [(0.0, 0.0), (5.0, 0.0), (5.0, 4.0), (0.0, 4.0)]
        height = 3.0
        vertices, triangles = geodata.extrude_footprint_grouped(footprint, height_m=height)
        for t in triangles:
            zs = [round(vertices[i][2], 9) for i in t['v']]
            if t['group'] == 'sol':
                self.assertTrue(all(z == 0.0 for z in zs))
            elif t['group'] == 'toiture':
                self.assertTrue(all(z == round(height, 9) for z in zs))
            else:
                # Un mur touche à la fois z=0 et z=height, jamais uniquement l'un des deux.
                self.assertIn(0.0, zs)
                self.assertIn(round(height, 9), zs)

    def test_resulting_triangles_pass_geometry_validation(self):
        # Intégration : le format produit doit être directement consommable
        # par compute_envelope_geometry (donc par BuildingSerializer), sans
        # transformation supplémentaire.
        footprint = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
        vertices, triangles = geodata.extrude_footprint_grouped(footprint, height_m=2.5)
        computed = geometry.compute_envelope_geometry(vertices, [dict(t) for t in triangles])
        self.assertEqual(len(computed), len(triangles))
        for c in computed:
            self.assertGreater(c['area'], 0.0)


class SearchNearbyBuildingsSerializerTest(SimpleTestCase):
    """Lot T — validation de SearchNearbyBuildingsRequestSerializer (pas de
    réseau, contrairement à geodata.search_nearby_buildings lui-même)."""

    databases = []

    def test_valid_with_explicit_radius(self):
        s = serializers.SearchNearbyBuildingsRequestSerializer(data={'lat': 48.85, 'lon': 2.35, 'radius_m': 40.0})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['radius_m'], 40.0)

    def test_radius_defaults_to_50(self):
        s = serializers.SearchNearbyBuildingsRequestSerializer(data={'lat': 48.85, 'lon': 2.35})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['radius_m'], 50.0)

    def test_radius_above_150_rejected(self):
        s = serializers.SearchNearbyBuildingsRequestSerializer(data={'lat': 48.85, 'lon': 2.35, 'radius_m': 200.0})
        self.assertFalse(s.is_valid())
        self.assertIn('radius_m', s.errors)

    def test_out_of_range_coordinates_rejected(self):
        s = serializers.SearchNearbyBuildingsRequestSerializer(data={'lat': 200.0, 'lon': 2.35})
        self.assertFalse(s.is_valid())
        self.assertIn('lat', s.errors)


class SelfBuildingFilterTest(SimpleTestCase):
    """Lot X — écarter le bâtiment étudié de son propre maillage d'environnement.

    Partie pure (pas de réseau) : `envelope_footprint_polygon` et
    `_is_studied_building` portent toute la décision, `generate_environment_mesh`
    ne fait que les câbler. Le bâtiment étudié étant un bâtiment réel de BD
    TOPO/OSM, il ressortait jusqu'ici comme n'importe quel voisin et
    api.shadow fusionnant enveloppe + environnement, le solveur voyait deux
    exemplaires superposés du même bâtiment.
    """

    databases = []

    @staticmethod
    def _box_envelope(x0, y0, x1, y1, height=6.0):
        """Enveloppe fermée d'une boîte rectangulaire, dans le repère LOCAL du
        bâtiment (mêmes conventions que Building.envelope)."""
        v = [
            [x0, y0, 0.0], [x1, y0, 0.0], [x1, y1, 0.0], [x0, y1, 0.0],
            [x0, y0, height], [x1, y0, height], [x1, y1, height], [x0, y1, height],
        ]
        t = [
            # sol / toiture : porteurs de l'empreinte
            {'v': [0, 2, 1], 'boundary': 'ground'}, {'v': [0, 3, 2], 'boundary': 'ground'},
            {'v': [4, 5, 6], 'boundary': 'exterior_air'}, {'v': [4, 6, 7], 'boundary': 'exterior_air'},
            # murs : projection XY d'aire nulle
            {'v': [0, 1, 5], 'boundary': 'exterior_air'}, {'v': [0, 5, 4], 'boundary': 'exterior_air'},
            {'v': [1, 2, 6], 'boundary': 'exterior_air'}, {'v': [1, 6, 5], 'boundary': 'exterior_air'},
            {'v': [2, 3, 7], 'boundary': 'exterior_air'}, {'v': [2, 7, 6], 'boundary': 'exterior_air'},
            {'v': [3, 0, 4], 'boundary': 'exterior_air'}, {'v': [3, 4, 7], 'boundary': 'exterior_air'},
        ]
        return {'vertices': v, 'triangles': t}

    @staticmethod
    def _rect(x0, y0, x1, y1):
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    # ── Empreinte du bâtiment étudié ────────────────────────────────────────

    def test_footprint_area_ignores_vertical_walls(self):
        """Les murs se projettent en segments : l'aire doit être celle de
        l'empreinte seule (8 x 6 = 48), pas une somme incluant les murs."""
        polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertAlmostEqual(polygon.area, 48.0, places=6)

    def test_footprint_without_ground_boundary(self):
        """Un OBJ importé n'a aucun triangle marqué 'ground' — l'empreinte doit
        quand même sortir (via la toiture), sans quoi le filtrage ne
        s'appliquerait qu'aux bâtiments du mode simplifié."""
        envelope = self._box_envelope(0, 0, 8, 6)
        for tri in envelope['triangles']:
            tri['boundary'] = 'exterior_air'
        polygon = geodata.envelope_footprint_polygon(envelope)
        self.assertAlmostEqual(polygon.area, 48.0, places=6)

    def test_footprint_none_when_envelope_empty(self):
        for envelope in (None, {}, {'vertices': [], 'triangles': []}):
            self.assertIsNone(geodata.envelope_footprint_polygon(envelope))

    def test_footprint_preserves_concavity(self):
        """Empreinte en L : l'aire doit être celle du L (pas de son enveloppe
        convexe), sinon un voisin logé dans le creux serait écarté à tort."""
        v = [[0, 0, 0], [4, 0, 0], [4, 2, 0], [2, 2, 0], [2, 4, 0], [0, 4, 0]]
        t = [{'v': [0, 1, 2]}, {'v': [0, 2, 3]}, {'v': [0, 3, 4]}, {'v': [0, 4, 5]}]
        polygon = geodata.envelope_footprint_polygon({'vertices': v, 'triangles': t})
        self.assertAlmostEqual(polygon.area, 12.0, places=6)  # 16 - 4 (convexe = 16)

    # ── Décision « c'est le bâtiment étudié » ───────────────────────────────

    def test_exact_duplicate_is_filtered(self):
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertTrue(geodata._is_studied_building(self._rect(0, 0, 8, 6), self_polygon))

    def test_party_wall_neighbour_is_kept(self):
        """Mitoyen partageant EXACTEMENT une arête : intersection d'aire nulle.
        C'est le cas que `intersects` (vrai au moindre contact) supprimerait à
        tort — un mitoyen est un obstacle légitime."""
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertFalse(geodata._is_studied_building(self._rect(8, 0, 16, 6), self_polygon))

    def test_disjoint_neighbour_is_kept(self):
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertFalse(geodata._is_studied_building(self._rect(20, 20, 28, 26), self_polygon))

    def test_partial_overlap_above_threshold_is_filtered(self):
        """Modélisation approximative (OBJ/boîte) décalée de 2 m sur 8 :
        recouvrement 6/8 = 75 % > seuil, donc écarté."""
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertTrue(geodata._is_studied_building(self._rect(2, 0, 10, 6), self_polygon))

    def test_slight_digitisation_overlap_is_kept(self):
        """Voisin qui déborde de 10 cm sur 8 m (imprécision de digitalisation) :
        1,25 % de recouvrement, très en dessous du seuil — conservé sans avoir
        besoin d'un buffer négatif."""
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertFalse(geodata._is_studied_building(self._rect(7.9, 0, 15.9, 6), self_polygon))

    def test_smaller_candidate_fully_inside_is_filtered(self):
        """Donnée IGN plus petite que la modélisation de l'utilisateur : le
        rapport est pris sur le MINIMUM des deux aires, donc détecté quand même
        (sur l'aire du bâtiment étudié seul, 24/48 = 50 %, ce serait limite)."""
        self_polygon = geodata.envelope_footprint_polygon(self._box_envelope(0, 0, 8, 6))
        self.assertTrue(geodata._is_studied_building(self._rect(1, 1, 5, 4), self_polygon))

    def test_no_self_envelope_filters_nothing(self):
        """Génération autonome (page Environnement) : aucun bâtiment de
        référence, donc aucun filtrage — comportement d'origine préservé."""
        self.assertFalse(geodata._is_studied_building(self._rect(0, 0, 8, 6), None))


class SelfBuildingFilterFrameTest(SimpleTestCase):
    """Lot X — le piège de repère : les empreintes candidates sont converties
    en repère LOCAL du bâtiment (local_xy puis _rotate_xy(north_offset_deg)),
    alors que Building.envelope y est DÉJÀ. Appliquer une seconde rotation à
    l'empreinte du bâtiment étudié ferait rater le doublon dès que
    georef_north_offset_deg n'est pas nul. On rejoue donc ici la chaîne réelle
    de generate_environment_mesh, sans réseau."""

    databases = []

    LAT, LON = 47.90123, 1.68345

    def _candidate_footprint_local(self, footprint_latlon, north_offset_deg):
        """Exactement la transformation appliquée par generate_environment_mesh."""
        return [
            geodata._rotate_xy(*geodata.local_xy(plat, plon, self.LAT, self.LON), north_offset_deg)
            for plat, plon in footprint_latlon
        ]

    def _self_envelope_from(self, footprint_latlon, north_offset_deg):
        """Le bâtiment tel que l'utilisateur l'a modélisé : même empreinte
        réelle, déjà exprimée dans son repère local."""
        xy = self._candidate_footprint_local(footprint_latlon, north_offset_deg)
        vertices = [[x, y, 0.0] for x, y in xy] + [[x, y, 6.0] for x, y in xy]
        n = len(xy)
        triangles = [{'v': [0, i, i + 1], 'boundary': 'ground'} for i in range(1, n - 1)]
        triangles += [{'v': [n, n + i, n + i + 1], 'boundary': 'exterior_air'} for i in range(1, n - 1)]
        return {'vertices': vertices, 'triangles': triangles}

    @staticmethod
    def _latlon_rect(lat0, lon0, dlat, dlon):
        return [
            [lat0, lon0], [lat0, lon0 + dlon],
            [lat0 + dlat, lon0 + dlon], [lat0 + dlat, lon0],
        ]

    def test_duplicate_detected_at_every_north_offset(self):
        footprint = self._latlon_rect(self.LAT, self.LON, 0.00006, 0.00010)
        for offset in (0.0, 37.0, 90.0, 180.0, 270.0):
            with self.subTest(north_offset_deg=offset):
                self_polygon = geodata.envelope_footprint_polygon(
                    self._self_envelope_from(footprint, offset),
                )
                candidate = self._candidate_footprint_local(footprint, offset)
                self.assertTrue(
                    geodata._is_studied_building(candidate, self_polygon),
                    f"doublon non détecté à north_offset_deg={offset}",
                )

    def test_real_neighbour_kept_at_every_north_offset(self):
        own = self._latlon_rect(self.LAT, self.LON, 0.00006, 0.00010)
        # Voisin nettement décalé vers l'est (~30 m), jamais recouvrant.
        neighbour = self._latlon_rect(self.LAT, self.LON + 0.0004, 0.00006, 0.00010)
        for offset in (0.0, 37.0, 90.0, 180.0, 270.0):
            with self.subTest(north_offset_deg=offset):
                self_polygon = geodata.envelope_footprint_polygon(
                    self._self_envelope_from(own, offset),
                )
                candidate = self._candidate_footprint_local(neighbour, offset)
                self.assertFalse(
                    geodata._is_studied_building(candidate, self_polygon),
                    f"voisin écarté à tort à north_offset_deg={offset}",
                )


class WeatherHourIndexTest(SimpleTestCase):
    """Lot AB1 — `hour_index` porté par chaque point météo.

    weather_source SAUTE les heures dont une donnée manque (décision du Lot L :
    ne rien inventer). Tout ce qui indexait le temps par la POSITION dans la
    liste — planning de ventilation/apports/volets (Lots Q et J) côté serveur,
    calendrier d'occupation (Lot V) côté client — dérivait donc définitivement
    d'autant d'heures qu'il en manquait, sans aucun signe (`n_missing`
    n'apparaît que dans le message du Job).
    """

    databases = []

    @staticmethod
    def _archive_payload(times, drop_temp_at=()):
        n = len(times)
        return {'hourly': {
            'time': times,
            'temperature_2m': [None if i in drop_temp_at else 10.0 for i in range(n)],
            'direct_normal_irradiance': [0.0] * n,
            'diffuse_radiation': [0.0] * n,
            'wind_speed_10m': [3.0] * n,
        }}

    def test_hour_index_matches_position_when_nothing_missing(self):
        times = [f'2024-03-05T{h:02d}:00' for h in range(24)]
        series, n_missing = weather_source._assemble_weather_series(48.85, 2.35, self._archive_payload(times))
        self.assertEqual(n_missing, 0)
        self.assertEqual([p['hour_index'] for p in series], list(range(24)))

    def test_hour_index_survives_missing_hours(self):
        """LE cas du bug : 3 heures sautées au milieu. Les points suivants
        doivent garder leur heure RÉELLE, pas leur rang dans la liste."""
        times = [f'2024-03-05T{h:02d}:00' for h in range(24)]
        series, n_missing = weather_source._assemble_weather_series(
            48.85, 2.35, self._archive_payload(times, drop_temp_at={8, 9, 10}),
        )
        self.assertEqual(n_missing, 3)
        self.assertEqual(len(series), 21)
        # Position 8 dans la liste = 11 h réelles (8, 9, 10 sautées).
        self.assertEqual(series[8]['hour_index'], 11)
        self.assertEqual(series[-1]['hour_index'], 23)
        # Le bug d'origine : l'ancien calcul (heure_debut=0 + position) aurait
        # donné 20 pour la dernière heure au lieu de 23 — 3 h de décalage.
        self.assertNotEqual(series[-1]['hour_index'], len(series) - 1)

    def test_hour_index_spans_days(self):
        times = [f'2024-03-{d:02d}T{h:02d}:00' for d in (5, 6, 7) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(48.85, 2.35, self._archive_payload(times))
        self.assertEqual(series[0]['hour_index'], 0)
        self.assertEqual(series[24]['hour_index'], 24)     # jour 1, minuit
        self.assertEqual(series[47]['hour_index'], 47)     # jour 1, 23 h
        self.assertEqual(series[-1]['hour_index'], 71)     # jour 2, 23 h

    def test_hour_index_survives_a_whole_missing_day(self):
        """Une JOURNÉE entière absente : le décompte des jours doit rester juste
        (sinon week-ends et vacances du calendrier d'occupation glissent d'un
        jour) — c'est pourquoi hour_index est dérivé de la DATE et non d'un
        compteur incrémenté ligne à ligne."""
        times = [f'2024-03-{d:02d}T{h:02d}:00' for d in (5, 6, 7) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._archive_payload(times, drop_temp_at=set(range(24, 48))),
        )
        self.assertEqual(len(series), 48)
        self.assertEqual(series[24]['hour_index'], 48)  # 7 mars 00 h, pas 24
        self.assertEqual(series[24]['hour_index'] // 24, 2)

    def test_tmy_hour_index_ignores_source_years(self):
        """Une TMY assemble des mois issus d'ANNÉES DIFFÉRENTES : compter par
        différence de dates sauterait des années entières d'un mois à l'autre.
        Ici, 31 déc 2011 puis 1er jan 2007 doivent rester deux jours
        CONSÉCUTIFS de l'année type."""
        rows = (
            [{'time(UTC)': f'20111231:{h:02d}00', 'T2m': 5.0, 'Gb(n)': 0.0, 'Gd(h)': 0.0, 'WS10m': 2.0}
             for h in range(24)]
            + [{'time(UTC)': f'20070101:{h:02d}00', 'T2m': 4.0, 'Gb(n)': 0.0, 'Gd(h)': 0.0, 'WS10m': 2.0}
               for h in range(24)]
        )
        series, _ = weather_source._assemble_tmy_series(48.85, 2.35, {'outputs': {'tmy_hourly': rows}})
        self.assertEqual(series[0]['hour_index'], 0)
        self.assertEqual(series[23]['hour_index'], 23)
        self.assertEqual(series[24]['hour_index'], 24)
        self.assertEqual(series[-1]['hour_index'], 47)


class PlanningHourIndexTest(SimpleTestCase):
    """Lot AB1 — le solveur suit `hour_index` plutôt que la position, quand il
    est fourni, pour le planning (Lot Q) ET les volets (Lot J)."""

    databases = []

    LAYERS = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 900.0, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}]

    def _envelope(self):
        vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 1.0]]
        triangles = geometry.compute_envelope_geometry(
            vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}],
        )
        return {'vertices': vertices, 'triangles': triangles}

    @staticmethod
    def _point(hour_index=None):
        p = {'t_ext': 0.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}
        if hour_index is not None:
            p['hour_index'] = hour_index
        return p

    def _run(self, weather, planning, heure_debut=0):
        payload = {
            'dx_max': 0.1, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 5000.0},
            't_init': 20.0, 'weather': weather, 'planning': planning, 'heure_debut': heure_debut,
        }
        return building_solver.run_building_simulation(
            self._envelope(), {1: self.LAYERS}, None, payload,
        )

    @staticmethod
    def _planning_with_gain_at(slot):
        """Planning neutre sauf un gros apport interne à UNE heure donnée : la
        température finale trahit alors sans ambiguïté quel créneau a été
        appliqué."""
        return [
            {'debit_vent_m3h': 0.0, 'eta_recup_vent': 0.0,
             'apports_internes_w': 5000.0 if h == slot else 0.0}
            for h in range(24)
        ]

    def test_missing_hours_do_not_shift_the_planning(self):
        """Série amputée de 3 heures avant le créneau porteur de l'apport.
        Avec hour_index, l'apport tombe sur la bonne heure ; sans lui (repli sur
        la position), il tomberait 3 heures trop tôt — c'est le bug."""
        kept_hours = [h for h in range(24) if h not in (2, 3, 4)]
        with_index = [self._point(hour_index=h) for h in kept_hours]
        without_index = [self._point() for _ in kept_hours]
        planning = self._planning_with_gain_at(20)

        t_correct = self._run(with_index, planning)['t_air_mean']
        t_shifted = self._run(without_index, planning)['t_air_mean']
        # Référence : la même série SANS trou, où position et heure coïncident.
        t_reference = self._run([self._point(hour_index=h) for h in range(24)], planning)['t_air_mean']

        # L'apport est bien appliqué dans les deux cas (même énergie totale),
        # mais pas à la même heure : les moyennes diffèrent.
        self.assertNotAlmostEqual(t_correct, t_shifted, places=6)
        # Et la série trouée correctement indexée reste proche de la référence.
        self.assertLess(abs(t_correct - t_reference), abs(t_shifted - t_reference))

    def test_hour_index_beats_heure_debut(self):
        """hour_index fourni ET heure_debut incohérent : hour_index gagne."""
        weather = [self._point(hour_index=h) for h in range(24)]
        planning = self._planning_with_gain_at(9)
        self.assertAlmostEqual(
            self._run(weather, planning, heure_debut=0)['t_air_mean'],
            self._run(weather, planning, heure_debut=17)['t_air_mean'],
            places=9,
        )

    def test_heure_debut_still_used_without_hour_index(self):
        """Série collée à la main (aucun hour_index) : heure_debut continue de
        piloter l'alignement, comportement du Lot Q inchangé."""
        weather = [self._point() for _ in range(24)]
        planning = self._planning_with_gain_at(9)
        self.assertNotAlmostEqual(
            self._run(weather, planning, heure_debut=0)['t_air_mean'],
            self._run(weather, planning, heure_debut=17)['t_air_mean'],
            places=6,
        )

    def test_shutter_schedule_follows_hour_index_too(self):
        """Les volets (Lot J) partagent le même créneau que le planning : ils
        doivent suivre hour_index de la même façon (ils entrent dans K, pas
        seulement dans F — d'où un test séparé)."""
        envelope = self._envelope()
        envelope['triangles'][0]['shading_profile_id'] = 'volet-roulant'
        planning = [
            {'debit_vent_m3h': 0.0, 'eta_recup_vent': 0.0, 'apports_internes_w': 0.0,
             'volets_fermes': h >= 18}
            for h in range(24)
        ]
        payload_base = {
            'dx_max': 0.1, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 5000.0},
            't_init': 20.0, 'planning': planning, 'heure_debut': 0,
        }
        kept_hours = [h for h in range(24) if h not in (2, 3, 4)]

        def run(weather):
            return building_solver.run_building_simulation(
                envelope, {1: self.LAYERS}, None, {**payload_base, 'weather': weather},
            )['t_air_mean']

        t_correct = run([self._point(hour_index=h) for h in kept_hours])
        t_shifted = run([self._point() for _ in kept_hours])
        self.assertNotAlmostEqual(t_correct, t_shifted, places=6)


class EnergyBalanceBreakdownTest(SimpleTestCase):
    """Lot AB2 — bilan par poste au nœud d'air.

    Le dashboard n'affichait que le canal « surfaces d'enveloppe » sous les
    libellés « gains »/« pertes », alors que le solaire transmis par les
    vitrages (Lot U), les apports internes (Lots H/Q), le renouvellement d'air
    (Lots G/Q) et les cadres de fenêtre (Lot I) alimentent le MÊME nœud d'air.

    Ces tests vérifient l'identité discrète exacte du nœud d'air : la somme des
    postes vaut la variation de stockage, au bit près. Ce n'est PAS tautologique
    (piège du Lot R) : les postes sont reconstruits depuis les températures
    résolues et les paramètres physiques, jamais relus depuis le vecteur F.
    """

    databases = []

    LAYERS = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 900.0, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}]
    GLAZING = [{'e': 0.004, 'lam': 1.0, 'rho': 2500.0, 'c': 750.0, 'tau': 0.87, 'r': 0.07, 'alpha': 0.06}]

    def _envelope(self, glazing=False, frame=False):
        vertices = [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 3.0], [0.0, 0.0, 3.0]]
        triangles = [
            {'v': [0, 1, 2], 'paroi_model_id': 2 if glazing else 1},
            {'v': [0, 2, 3], 'paroi_model_id': 1},
        ]
        return {'vertices': vertices,
                'triangles': geometry.compute_envelope_geometry(vertices, triangles)}

    def _run(self, *, mode='free', glazing=False, frame=False, sun=False,
             debit=0.0, apports=0.0, t_ext=0.0, hours=12, **interior_extra):
        interior = {'mode': mode, 'h_i': 8.0, 'c_air_int': 50_000.0,
                    'debit_vent_m3h': debit, 'eta_recup_vent': 0.0,
                    'apports_internes_w': apports, **interior_extra}
        weather = [{
            't_ext': t_ext, 'sun_azimuth': 180.0,
            'sun_elevation': 40.0 if sun else -10.0,
            'e_dir': 800.0 if sun else 0.0, 'e_dif': 120.0 if sun else 0.0,
        } for _ in range(hours)]
        payload = {'dx_max': 0.05, 'h_e': 25.0, 'interior': interior,
                   't_init': 20.0, 'weather': weather}
        layers = {1: self.LAYERS, 2: self.GLAZING}
        frames = {2: (2.0, 0.25)} if frame else None
        return building_solver.run_building_simulation(
            self._envelope(glazing=glazing), layers, None, payload,
            paroi_frame_by_id=frames,
        )

    def _assert_closes(self, result, tol_kwh=1e-9):
        b = result['balance']
        self.assertIsNotNone(b)
        total = sum(b[k] for k in ('envelope_kwh', 'solar_transmitted_kwh', 'internal_gains_kwh',
                                    'ventilation_kwh', 'frames_kwh', 'hvac_kwh'))
        self.assertAlmostEqual(total, b['storage_kwh'], delta=tol_kwh)
        self.assertAlmostEqual(b['closure_error_kwh'], 0.0, delta=tol_kwh)
        return b

    def test_closes_with_envelope_only(self):
        b = self._assert_closes(self._run(t_ext=-5.0))
        self.assertLess(b['envelope_kwh'], 0.0)  # il fait froid dehors : pertes
        for k in ('solar_transmitted_kwh', 'internal_gains_kwh', 'ventilation_kwh', 'frames_kwh'):
            self.assertEqual(b[k], 0.0)

    def test_closes_with_every_channel_active(self):
        b = self._assert_closes(self._run(
            glazing=True, frame=True, sun=True, debit=60.0, apports=300.0, t_ext=-5.0,
        ))
        for k in ('solar_transmitted_kwh', 'internal_gains_kwh', 'frames_kwh'):
            self.assertNotEqual(b[k], 0.0, f"{k} devrait être non nul")
        self.assertLess(b['ventilation_kwh'], 0.0)  # air neuf plus froid que l'intérieur

    def test_closes_in_thermostat_mode_heating(self):
        """Nuit d'hiver, sans soleil : le thermostat chauffe (hvac > 0)."""
        b = self._assert_closes(self._run(
            mode='thermostat', debit=60.0, t_ext=-5.0, t_min=19.0, t_max=26.0,
        ))
        self.assertGreater(b['hvac_kwh'], 0.0)
        self.assertLess(b['envelope_kwh'], 0.0)

    def test_closes_in_thermostat_mode_cooling(self):
        """Même bâtiment en plein soleil derrière un vitrage : le solaire
        transmis dépasse largement les déperditions malgré -5 °C dehors, et le
        thermostat doit REFROIDIR (hvac < 0). Le bilan doit boucler dans ce sens
        aussi — c'est le cas où le poste solaire, invisible avant ce lot,
        explique à lui seul le signe du résultat."""
        b = self._assert_closes(self._run(
            mode='thermostat', glazing=True, sun=True, debit=60.0, apports=300.0,
            t_ext=-5.0, t_min=19.0, t_max=26.0,
        ))
        self.assertLess(b['hvac_kwh'], 0.0)
        self.assertGreater(b['solar_transmitted_kwh'], abs(b['hvac_kwh']))

    def test_solar_transmitted_is_the_dominant_gain_through_glazing(self):
        """Le poste que le dashboard laissait invisible : sur une paroi vitrée
        ensoleillée, il doit dépasser le canal « surfaces d'enveloppe » — c'est
        bien pour ça que l'afficher change la lecture du bilan."""
        b = self._assert_closes(self._run(glazing=True, sun=True, t_ext=0.0))
        self.assertGreater(b['solar_transmitted_kwh'], abs(b['envelope_kwh']))

    def test_internal_gains_exactly_match_their_power(self):
        """Oracle indépendant du solveur : des apports constants de 300 W
        pendant 12 h valent exactement 3,6 kWh."""
        b = self._assert_closes(self._run(apports=300.0, hours=12))
        self.assertAlmostEqual(b['internal_gains_kwh'], 300.0 * 12 / 1000.0, places=9)

    def test_envelope_channel_matches_legacy_flux_series(self):
        """Non-régression : le poste « enveloppe » est exactement l'intégrale de
        envelope_flux_w, la grandeur historiquement affichée."""
        result = self._run(t_ext=-5.0, hours=8)
        legacy = sum(result['envelope_flux_w']) * 3600.0 / 3.6e6
        self.assertAlmostEqual(result['balance']['envelope_kwh'], legacy, places=12)
        self.assertAlmostEqual(
            result['flux_positive_kwh'] + result['flux_negative_kwh'], legacy, places=12,
        )

    def test_no_balance_in_imposed_mode(self):
        """Mode 'imposed' : la ligne du nœud d'air est écrasée par Dirichlet,
        aucun de ces postes n'agit — mieux vaut ne rien annoncer que d'annoncer
        des valeurs sans effet sur la solution."""
        result = self._run(mode='imposed', t_int=20.0, glazing=True, sun=True, apports=300.0)
        self.assertIsNone(result['balance'])


class GroundResistanceTest(SimpleTestCase):
    """Lot AB3 — un plancher au contact du sol échange à travers la résistance
    de la TERRE, pas à travers h_e.

    Deux défauts corrigés ici. (1) Physique : avec h_e = 25, la résistance côté
    extérieur d'un dallage valait 0,04 m²·K/W — un contact quasi parfait avec
    une source à `t_ground`, d'où une surestimation des déperditions par le bas
    d'autant plus forte que le plancher est isolé. (2) Cohérence : depuis le Lot
    R, ce couplage à la terre se mettait à osciller avec la vitesse du vent à
    10 m.
    """

    databases = []

    LAYERS = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 900.0, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}]

    def _envelope(self, boundary):
        vertices = [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0]]
        triangles = geometry.compute_envelope_geometry(
            vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1, 'boundary': boundary}],
        )
        return {'vertices': vertices, 'triangles': triangles}

    @staticmethod
    def _weather(winds, t_ext=-5.0):
        return [{'t_ext': t_ext, 'sun_azimuth': 0.0, 'sun_elevation': -10.0,
                 'e_dir': 0.0, 'e_dif': 0.0, 'wind_m_s': w} for w in winds]

    def _run(self, boundary, weather, *, h_e_dynamic=False, h_e=25.0, **extra):
        payload = {
            'dx_max': 0.05, 'h_e': h_e, 'h_e_dynamic': h_e_dynamic,
            'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 20.0},
            't_init': 20.0, 'weather': weather, 't_ground': 12.0, **extra,
        }
        return building_solver.run_building_simulation(
            self._envelope(boundary), {1: self.LAYERS}, None, payload,
        )

    def test_ground_triangle_ignores_wind(self):
        """LE correctif : un dallage ne doit plus rien devoir au vent."""
        calm = self._run('ground', self._weather([0.0] * 24), h_e_dynamic=True)
        gale = self._run('ground', self._weather([25.0] * 24), h_e_dynamic=True)
        self.assertEqual(calm['envelope_flux_w'], gale['envelope_flux_w'])

    def test_exterior_air_triangle_still_follows_wind(self):
        """Non-régression du Lot R : une paroi exposée, elle, doit toujours
        réagir au vent — sinon le test ci-dessus passerait pour une raison
        triviale (h_e_dynamic cassé partout)."""
        calm = self._run('exterior_air', self._weather([0.0] * 24), h_e_dynamic=True)
        gale = self._run('exterior_air', self._weather([25.0] * 24), h_e_dynamic=True)
        self.assertNotEqual(calm['envelope_flux_w'], gale['envelope_flux_w'])

    def test_ground_triangle_ignores_h_e_constant_too(self):
        """Même en h_e constant : la valeur de h_e ne doit plus entrer dans
        l'équation d'un triangle au sol."""
        low = self._run('ground', self._weather([3.0] * 12), h_e=5.0)
        high = self._run('ground', self._weather([3.0] * 12), h_e=90.0)
        self.assertEqual(low['envelope_flux_w'], high['envelope_flux_w'])

    def test_r_ground_is_exactly_a_series_resistance(self):
        """Identité EXACTE (pas une convergence) : un triangle 'ground' avec
        r_ground doit reproduire au chiffre près un triangle 'exterior_air'
        soumis à une météo constante à t_ground et à h_e = 1/r_ground. C'est la
        définition même d'une résistance en série, vérifiée hors du code qui la
        pose."""
        r_ground = 0.8
        ground = self._run('ground', self._weather([7.0] * 24), r_ground=r_ground)
        equivalent = self._run(
            'exterior_air', self._weather([7.0] * 24, t_ext=12.0), h_e=1.0 / r_ground,
        )
        for a, b in zip(ground['envelope_flux_w'], equivalent['envelope_flux_w']):
            self.assertAlmostEqual(a, b, places=9)

    def test_more_ground_resistance_means_fewer_losses(self):
        """Sens physique : plus la terre résiste, moins le plancher déperd."""
        thin = self._run('ground', self._weather([3.0] * 24), r_ground=0.1)
        thick = self._run('ground', self._weather([3.0] * 24), r_ground=3.0)
        # t_ground (12 °C) < t_int (20 °C) : le flux est négatif (pertes).
        self.assertLess(sum(thin['envelope_flux_w']), sum(thick['envelope_flux_w']))

    def test_previous_behaviour_overestimated_losses(self):
        """Chiffre l'écart corrigé : l'ancien couplage (h_e = 25, soit
        R = 0,04) contre le défaut actuel (0,5). Les déperditions par le sol
        étaient nettement surestimées."""
        old = self._run('ground', self._weather([3.0] * 24), r_ground=0.04)
        new = self._run('ground', self._weather([3.0] * 24))  # défaut 0,5
        self.assertLess(sum(old['envelope_flux_w']), sum(new['envelope_flux_w']))
        self.assertGreater(abs(sum(old['envelope_flux_w'])), 1.2 * abs(sum(new['envelope_flux_w'])))

    def test_shutter_never_applies_to_a_ground_triangle(self):
        """Un volet sur un plancher n'a aucun sens : même avec un dispositif
        assigné et un planning qui le ferme, le triangle 'ground' ne doit pas
        bouger."""
        envelope = self._envelope('ground')
        envelope['triangles'][0]['shading_profile_id'] = 'volet-roulant'
        planning = [{'debit_vent_m3h': 0.0, 'eta_recup_vent': 0.0,
                     'apports_internes_w': 0.0, 'volets_fermes': h >= 12} for h in range(24)]
        payload = {
            'dx_max': 0.05, 'h_e': 25.0,
            'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 20.0},
            't_init': 20.0, 'weather': self._weather([3.0] * 24), 't_ground': 12.0,
        }
        without = building_solver.run_building_simulation(
            envelope, {1: self.LAYERS}, None, payload,
        )
        with_planning = building_solver.run_building_simulation(
            envelope, {1: self.LAYERS}, None, {**payload, 'planning': planning, 'heure_debut': 0},
        )
        self.assertEqual(without['envelope_flux_w'], with_planning['envelope_flux_w'])


class GroundSlabCatalogueTest(SimpleTestCase):
    """Lot AB3 — le catalogue doit proposer un plancher bas : le mode simplifié
    impose de choisir un modèle opaque pour le groupe `sol`, et la liste
    n'offrait que des murs et des toitures."""

    databases = []

    def test_catalogue_has_slab_models(self):
        from api.management.commands.seed_paroi_catalogue import CATALOGUE
        slabs = [e for e in CATALOGUE if 'terre-plein' in e['name']]
        self.assertEqual(len(slabs), 3)
        for entry in slabs:
            with self.subTest(name=entry['name']):
                self.assertFalse(entry.get('is_glazing', False))
                # Couches de l'extérieur (côté terre) vers l'intérieur : la
                # première doit être la dalle, jamais l'isolant.
                self.assertGreater(entry['layers'][0]['lam'], 1.0)
                for layer in entry['layers']:
                    self.assertEqual(layer['tau'], 0)
                    self.assertAlmostEqual(
                        layer['tau'] + layer['r'] + layer['alpha'], 1.0, places=6,
                    )

    def test_slab_models_pass_the_serializer(self):
        from api.management.commands.seed_paroi_catalogue import CATALOGUE
        for entry in [e for e in CATALOGUE if 'terre-plein' in e['name']]:
            with self.subTest(name=entry['name']):
                s = serializers.LayerSerializer(data=entry['layers'], many=True)
                self.assertTrue(s.is_valid(), s.errors)


class LocalTimeOffsetTest(SimpleTestCase):
    """Lot AB4 — `hour_index` en heure LOCALE, pour qu'un profil d'occupation
    « 7 h - 19 h » veuille dire 7 h - 19 h à l'horloge de l'utilisateur.

    La météo est demandée en UTC (indispensable à la position solaire, qui est
    un instant physique) ; seul l'étiquetage horaire est décalé. Décalage FIXE,
    sans changement d'heure : vérifié en réel que Open-Meteo renvoie le même
    `utc_offset_seconds` en janvier et en juillet, et une année continue de
    8784 heures — arbitrage tranché avec l'utilisateur (to_do.md, Lot AB4).
    """

    databases = []

    @staticmethod
    def _payload(times):
        n = len(times)
        return {'hourly': {
            'time': times, 'temperature_2m': [10.0] * n,
            'direct_normal_irradiance': [0.0] * n, 'diffuse_radiation': [0.0] * n,
            'wind_speed_10m': [3.0] * n,
        }}

    def test_offset_shifts_hour_index(self):
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=3600,
        )
        # 00:00 UTC = 01:00 locale le même jour -> hour_index part à 1.
        self.assertEqual(series[0]['hour_index'], 1)
        # 23:00 UTC = 00:00 locale le LENDEMAIN -> jour 1, heure 0.
        self.assertEqual(series[-1]['hour_index'], 24)
        self.assertEqual(series[-1]['hour_index'] % 24, 0)

    def test_zero_offset_is_the_previous_behaviour(self):
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(48.85, 2.35, self._payload(times))
        self.assertEqual([p['hour_index'] for p in series], list(range(24)))

    def test_negative_offset_never_produces_a_negative_index(self):
        """Fuseau très à l'ouest : la première heure locale tombe la veille. Le
        jour 0 est celui du PREMIER point, donc hour_index reste positif — sans
        quoi le serializer (min_value=0) rejetterait la série."""
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=-8 * 3600,
        )
        self.assertTrue(all(p['hour_index'] >= 0 for p in series))
        self.assertEqual(series[0]['hour_index'], 16)  # 00:00 UTC -> 16:00 la veille

    def test_solar_position_is_untouched_by_the_offset(self):
        """Le point clé : décaler l'étiquette horaire ne doit RIEN changer à la
        position du soleil, qui est un instant physique. Sans quoi on
        corrigerait un confort d'usage en cassant la physique."""
        times = [f'2024-06-21T{h:02d}:00' for h in range(24)]
        utc, local = (weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=off,
        )[0] for off in (0, 2 * 3600))
        for a, b in zip(utc, local):
            self.assertAlmostEqual(a['sun_elevation'], b['sun_elevation'], places=12)
            self.assertAlmostEqual(a['sun_azimuth'], b['sun_azimuth'], places=12)

    def test_full_year_stays_within_serializer_bounds(self):
        """Un décalage positif reporte les dernières heures sur un jour de plus :
        la borne haute du serializer doit l'accepter (8807 et non 8783)."""
        times = [f'2024-{m:02d}-{d:02d}T{h:02d}:00'
                 for m, d in ((1, 1), (12, 31)) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=2 * 3600,
        )
        field = serializers.BuildingWeatherPointSerializer().fields['hour_index']
        biggest = max(p['hour_index'] for p in series)
        self.assertLessEqual(biggest, field.max_value)

    def test_tmy_offset_applies_too(self):
        rows = [{'time(UTC)': f'20110101:{h:02d}00', 'T2m': 5.0, 'Gb(n)': 0.0,
                 'Gd(h)': 0.0, 'WS10m': 2.0} for h in range(24)]
        series, _ = weather_source._assemble_tmy_series(
            48.85, 2.35, {'outputs': {'tmy_hourly': rows}}, utc_offset_seconds=3600,
        )
        self.assertEqual(series[0]['hour_index'], 1)
        self.assertEqual(series[-1]['hour_index'], 24)

    def test_fetch_request_accepts_and_defaults_the_offset(self):
        base = {'lat': 48.85, 'lon': 2.35, 'start_date': '2024-01-01', 'end_date': '2024-01-02'}
        s = serializers.WeatherFetchRequestSerializer(data=base)
        self.assertTrue(s.is_valid(), s.errors)
        self.assertIsNone(s.validated_data['utc_offset_h'])  # None = détection auto

        s = serializers.WeatherFetchRequestSerializer(data={**base, 'utc_offset_h': 5.5})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['utc_offset_h'], 5.5)

        s = serializers.WeatherFetchRequestSerializer(data={**base, 'utc_offset_h': 20})
        self.assertFalse(s.is_valid())


class ElevationGridTest(SimpleTestCase):
    """Lot AA — partie pure du maillage de terrain (grille, triangulation,
    aplanissement sous le bâtiment). Le réseau est isolé dans fetch_* comme
    partout ailleurs dans ce module."""

    databases = []

    def test_grid_is_square_and_centred(self):
        coords, n = elevation.terrain_grid_local(radius_m=20.0, spacing_m=10.0)
        self.assertEqual(n, 5)                       # -20 .. +20 au pas de 10
        self.assertEqual(len(coords), 25)
        self.assertEqual(coords[0], (-20.0, -20.0))
        self.assertEqual(coords[-1], (20.0, 20.0))
        # Balayage ligne par ligne : build_terrain_mesh en dépend pour trianguler.
        self.assertEqual(coords[1], (-10.0, -20.0))
        self.assertEqual(coords[n], (-20.0, -10.0))

    def test_grid_refuses_to_explode(self):
        with self.assertRaises(elevation.ElevationError):
            elevation.terrain_grid_local(radius_m=400.0, spacing_m=2.0)
        with self.assertRaises(elevation.ElevationError):
            elevation.terrain_grid_local(radius_m=10.0, spacing_m=0.0)

    def test_mesh_has_two_triangles_per_cell(self):
        coords, n = elevation.terrain_grid_local(radius_m=20.0, spacing_m=10.0)
        mesh = elevation.build_terrain_mesh(coords, n, [100.0] * len(coords), ground_z_ref=100.0)
        self.assertEqual(len(mesh['vertices']), 25)
        self.assertEqual(len(mesh['triangles']), 2 * (n - 1) * (n - 1))
        # ground_z_ref soustrait : un terrain plat à l'altitude du bâtiment est à z = 0.
        self.assertTrue(all(abs(v[2]) < 1e-12 for v in mesh['vertices']))

    def test_mesh_indices_are_all_valid(self):
        coords, n = elevation.terrain_grid_local(radius_m=30.0, spacing_m=10.0)
        mesh = elevation.build_terrain_mesh(coords, n, list(range(len(coords))), ground_z_ref=0.0)
        for tri in mesh['triangles']:
            for i in tri['v']:
                self.assertTrue(0 <= i < len(mesh['vertices']))

    def test_relative_altitude_uses_ground_z_ref(self):
        coords, n = elevation.terrain_grid_local(radius_m=10.0, spacing_m=10.0)
        mesh = elevation.build_terrain_mesh(coords, n, [112.0] * len(coords), ground_z_ref=106.9)
        for v in mesh['vertices']:
            self.assertAlmostEqual(v[2], 5.1, places=6)

    def test_terrain_is_flattened_under_the_building(self):
        """Sans aplanissement, un terrain en pente traverse les murs et bloque
        des rayons DEPUIS L'INTÉRIEUR de l'enveloppe — l'ombrage serait faussé
        au lieu d'être amélioré."""
        import shapely.geometry
        coords, n = elevation.terrain_grid_local(radius_m=20.0, spacing_m=10.0)
        # Pente régulière : altitude croissante avec x.
        altitudes = [100.0 + x for x, _ in coords]
        footprint = shapely.geometry.Polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)])
        mesh = elevation.build_terrain_mesh(coords, n, altitudes, ground_z_ref=100.0,
                                             footprint_polygon=footprint)
        inside = [v for v in mesh['vertices'] if -5 < v[0] < 5 and -5 < v[1] < 5]
        self.assertTrue(inside)
        self.assertTrue(all(abs(v[2]) < 1e-12 for v in inside), "sommets sous le bâtiment non aplanis")
        outside = [v for v in mesh['vertices'] if v[0] > 15]
        self.assertTrue(any(abs(v[2]) > 1.0 for v in outside), "le relief hors emprise doit subsister")

    def test_mismatched_altitudes_are_rejected(self):
        coords, n = elevation.terrain_grid_local(radius_m=10.0, spacing_m=10.0)
        with self.assertRaises(elevation.ElevationError):
            elevation.build_terrain_mesh(coords, n, [100.0], ground_z_ref=0.0)


class LocalXyRoundTripTest(SimpleTestCase):
    """Lot AA — latlon_from_local_xy doit être l'inverse EXACT de la chaîne
    local_xy + _rotate_xy utilisée pour placer les obstacles. Une erreur de
    signe ici demanderait l'altitude au mauvais endroit, en silence : le terrain
    aurait l'air plausible tout en étant celui d'à côté."""

    databases = []

    LAT, LON = 47.90123, 1.68345

    def test_round_trip_at_every_north_offset(self):
        for offset in (0.0, 37.0, 90.0, 180.0, 270.0):
            for x, y in ((0.0, 0.0), (150.0, -80.0), (-42.5, 63.25)):
                with self.subTest(offset=offset, x=x, y=y):
                    lat, lon = geodata.latlon_from_local_xy(x, y, self.LAT, self.LON, offset)
                    back = geodata._rotate_xy(
                        *geodata.local_xy(lat, lon, self.LAT, self.LON), offset,
                    )
                    self.assertAlmostEqual(back[0], x, places=6)
                    self.assertAlmostEqual(back[1], y, places=6)

    def test_origin_maps_to_the_reference_point(self):
        lat, lon = geodata.latlon_from_local_xy(0.0, 0.0, self.LAT, self.LON, 42.0)
        self.assertAlmostEqual(lat, self.LAT, places=9)
        self.assertAlmostEqual(lon, self.LON, places=9)


def _veg_box(x0, y0, x1, y1, z0, z1, obj, k, base_vertex=0):
    """Boîte translucide (végétation) — 8 sommets, 12 triangles portant k/obj."""
    v = [
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ]
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    t = []
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            t.append({'v': [base_vertex + i for i in tri], 'k': k, 'obj': obj})
    return v, t


class VegetationOccluderTest(SimpleTestCase):
    """Lot Z — la végétation atténue le rayonnement au lieu de le bloquer.

    Le piège central : un rayon qui traverse un volume fermé touche AU MOINS
    DEUX faces (entrée et sortie). Multiplier la transmittance à chaque face
    donnerait k² au lieu de k — d'où le regroupement par objet (`obj`) dans
    api.shadow.VegetationScene, que ces tests vérifient directement.
    """

    databases = []

    def _ground_triangle(self):
        """Un triangle horizontal à l'origine, tourné vers le ciel."""
        vertices = [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, 1.0, 0.0]]
        triangles = geometry.compute_envelope_geometry(
            vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}],
        )
        return {'vertices': vertices, 'triangles': triangles}

    @staticmethod
    def _canopy(k, obj=0):
        """Couvert translucide au-dessus de l'origine."""
        v, t = _veg_box(-5.0, -5.0, 5.0, 5.0, 5.0, 7.0, obj=obj, k=k)
        return {'vertices': v, 'triangles': t}

    def test_transmittance_applied_once_per_object(self):
        """Le test décisif : le couvert est traversé de part en part (deux faces
        au moins), la fraction transmise doit valoir k et non k²."""
        k = 0.3
        grid = shadow.compute_visibility_grid(
            self._ground_triangle(), self._canopy(k),
            azimuth_step_deg=90.0, elevation_step_deg=90.0,
        )
        # Soleil au zénith : le rayon part vers +Z, droit à travers le couvert.
        zenith = grid['per_triangle'][0][0][-1]
        self.assertAlmostEqual(zenith, k, places=3)
        self.assertNotAlmostEqual(zenith, k * k, places=3)

    def test_two_stacked_canopies_multiply(self):
        """Deux objets distincts sur le trajet : les transmittances se
        multiplient (0,5 x 0,4 = 0,2). C'est le pendant du test précédent —
        regrouper par objet ne doit pas fusionner des objets différents."""
        v1, t1 = _veg_box(-5, -5, 5, 5, 5, 6, obj=0, k=0.5)
        v2, t2 = _veg_box(-5, -5, 5, 5, 8, 9, obj=1, k=0.4, base_vertex=len(v1))
        env = {'vertices': v1 + v2, 'triangles': t1 + t2}
        grid = shadow.compute_visibility_grid(
            self._ground_triangle(), env, azimuth_step_deg=90.0, elevation_step_deg=90.0,
        )
        self.assertAlmostEqual(grid['per_triangle'][0][0][-1], 0.2, places=3)

    def test_opaque_environment_is_unchanged(self):
        """Non-régression : sans triangle translucide, la grille reste
        strictement binaire — c'est le comportement du Lot C, inchangé."""
        v, t = _veg_box(-5, -5, 5, 5, 5, 7, obj=0, k=0.0)
        opaque = {'vertices': v, 'triangles': [{'v': tri['v']} for tri in t]}
        grid = shadow.compute_visibility_grid(
            self._ground_triangle(), opaque, azimuth_step_deg=90.0, elevation_step_deg=90.0,
        )
        values = {v for row in grid['per_triangle'][0] for v in row}
        self.assertTrue(values <= {0, 1}, f"valeurs non binaires : {values}")
        self.assertEqual(grid['per_triangle'][0][0][-1], 0)

    def test_k_zero_behaves_as_opaque(self):
        v, t = _veg_box(-5, -5, 5, 5, 5, 7, obj=0, k=0.0)
        grid = shadow.compute_visibility_grid(
            self._ground_triangle(), {'vertices': v, 'triangles': t},
            azimuth_step_deg=90.0, elevation_step_deg=90.0,
        )
        self.assertEqual(grid['per_triangle'][0][0][-1], 0)

    def test_sky_view_factor_is_attenuated_not_cancelled(self):
        """Le facteur de vue du ciel doit se situer STRICTEMENT entre le cas
        dégagé et le cas opaque : la végétation laisse passer du diffus."""
        building = self._ground_triangle()
        free = shadow.compute_sky_view_factors(building, None)[0]
        veg = shadow.compute_sky_view_factors(building, self._canopy(0.3))[0]
        v, t = _veg_box(-5, -5, 5, 5, 5, 7, obj=0, k=0.0)
        opaque = shadow.compute_sky_view_factors(
            building, {'vertices': v, 'triangles': [{'v': tri['v']} for tri in t]},
        )[0]
        self.assertLess(veg, free)
        self.assertGreater(veg, opaque)

    def test_lookup_returns_a_fraction(self):
        grid = shadow.compute_visibility_grid(
            self._ground_triangle(), self._canopy(0.25),
            azimuth_step_deg=90.0, elevation_step_deg=90.0,
        )
        value = shadow.lookup_visibility(grid, 0, 0.0, 90.0)
        self.assertIsInstance(value, float)
        self.assertAlmostEqual(value, 0.25, places=3)

    def test_solver_receives_attenuated_direct_radiation(self):
        """Bout en bout : le même bâtiment sous un couvert à k = 0,25 doit
        recevoir exactement le quart du rayonnement direct — l'atténuation doit
        traverser tout le chemin jusqu'au flux calculé."""
        layers = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 900.0,
                   'tau': 0.0, 'r': 0.4, 'alpha': 0.6}]
        building = self._ground_triangle()
        weather = [{'t_ext': 20.0, 'sun_azimuth': 0.0, 'sun_elevation': 90.0,
                    'e_dir': 800.0, 'e_dif': 0.0}]

        def run(environment):
            grid = shadow.compute_visibility_grid(
                building, environment, azimuth_step_deg=90.0, elevation_step_deg=90.0,
            )
            payload = {'dx_max': 0.05, 'h_e': 25.0,
                       'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 20.0},
                       't_init': 20.0, 'weather': weather}
            return building_solver.run_building_simulation(
                building, {1: layers}, grid, payload,
                environment_envelope=environment,
            )['final_exterior_surface_temp'][0]

        t_free = run(None)
        t_veg = run(self._canopy(0.25))
        # L'échauffement de surface est proportionnel au rayonnement absorbé.
        rise_free = t_free - 20.0
        rise_veg = t_veg - 20.0
        self.assertGreater(rise_free, 0.0)
        self.assertAlmostEqual(rise_veg / rise_free, 0.25, places=2)


class BaseAltitudeResolutionTest(SimpleTestCase):
    """Correctif du 2026-08-09 — tous les obstacles doivent partager la MÊME
    référence d'altitude que le bâtiment étudié.

    Seule la BD TOPO porte une altitude par bâtiment (`altitude_minimale_sol`) :
    la végétation (IGN comme OSM) et les bâtiments OpenStreetMap n'en ont
    aucune, et se retrouvaient donc posés au niveau 0 du bâtiment étudié pendant
    que les bâtiments IGN suivaient le relief réel. Sur un site en pente, les uns
    flottaient ou s'enterraient par rapport aux autres — signalé à l'usage.
    """

    databases = []

    def test_uses_the_altitude_carried_by_the_data(self):
        items = [{'base_z': 112.0, 'point_latlon': (47.9, 1.68)}]
        base = geodata.resolve_base_z(items, ground_z_ref=106.9, elevation_lookup=None)
        self.assertAlmostEqual(base[0], 5.1, places=6)

    def test_falls_back_to_elevation_lookup(self):
        """Sans altitude propre, on interroge l'altimétrie — et on la ramène au
        repère local du bâtiment."""
        called = []

        def lookup(points):
            called.append(list(points))
            return [120.0, 130.0]

        items = [{'point_latlon': (47.9, 1.68)}, {'point_latlon': (47.91, 1.69)}]
        base = geodata.resolve_base_z(items, ground_z_ref=110.0, elevation_lookup=lookup)
        self.assertEqual(called, [[(47.9, 1.68), (47.91, 1.69)]])
        self.assertAlmostEqual(base[0], 10.0, places=6)
        self.assertAlmostEqual(base[1], 20.0, places=6)

    def test_lookup_is_only_asked_for_what_it_needs(self):
        """Un mélange BD TOPO / OSM ne doit interroger l'altimétrie que pour les
        éléments qui n'ont pas d'altitude — pas pour toute la liste."""
        asked = []

        def lookup(points):
            asked.extend(points)
            return [200.0] * len(points)

        items = [
            {'base_z': 100.0, 'point_latlon': (1.0, 1.0)},
            {'point_latlon': (2.0, 2.0)},
            {'base_z': 105.0, 'point_latlon': (3.0, 3.0)},
        ]
        base = geodata.resolve_base_z(items, ground_z_ref=100.0, elevation_lookup=lookup)
        self.assertEqual(asked, [(2.0, 2.0)])
        self.assertAlmostEqual(base[0], 0.0, places=6)
        self.assertAlmostEqual(base[1], 100.0, places=6)
        self.assertAlmostEqual(base[2], 5.0, places=6)

    def test_lookup_failure_falls_back_without_losing_obstacles(self):
        """Best-effort : une panne d'altimétrie ne doit pas faire perdre les
        obstacles, seulement les poser au niveau du bâtiment avec un
        avertissement."""
        def boom(_points):
            raise RuntimeError("altimétrie indisponible")

        warnings = []
        items = [{'point_latlon': (47.9, 1.68)}]
        base = geodata.resolve_base_z(items, 100.0, boom, warnings, label="arbres")
        self.assertEqual(base, [0.0])
        self.assertTrue(any('altimétrie indisponible' in w for w in warnings))

    def test_no_lookup_available_warns(self):
        warnings = []
        geodata.resolve_base_z([{'point_latlon': (1.0, 1.0)}], 0.0, None, warnings, label="arbres")
        self.assertTrue(any('sans altitude propre' in w for w in warnings))

    def test_vegetation_and_buildings_share_the_same_reference(self):
        """Le cœur du correctif : sur un terrain à 150 m, un arbre et un bâtiment
        situés au même endroit doivent recevoir la MÊME altitude de base."""
        ground_z_ref = 145.0
        terrain_altitude = 150.0
        building = [{'base_z': terrain_altitude, 'point_latlon': (45.9, 6.13)}]
        tree = [{'point_latlon': (45.9, 6.13)}]
        b = geodata.resolve_base_z(building, ground_z_ref, None)
        t = geodata.resolve_base_z(tree, ground_z_ref, lambda pts: [terrain_altitude] * len(pts))
        self.assertAlmostEqual(b[0], t[0], places=6)
        self.assertAlmostEqual(t[0], 5.0, places=6)


class ClipAgainstStudiedBuildingTest(SimpleTestCase):
    """Lot AD — un obstacle qui empiète sur le bâtiment étudié est ROGNÉ plutôt
    qu'écarté (choix de l'utilisateur, 2026-08-09).

    Le Lot X ne savait que garder ou jeter. Un voisin qui interpénètre
    l'enveloppe modélisée — cas courant quand celle-ci est approximative (import
    OBJ, boîte du générateur) — était donc soit conservé en s'enfonçant dans le
    bâtiment, soit perdu en entier alors qu'il masque réellement le soleil.
    """

    databases = []

    SELF = [(0.0, 0.0), (8.0, 0.0), (8.0, 6.0), (0.0, 6.0)]  # 48 m²

    def _self_polygon(self):
        import shapely.geometry
        return shapely.geometry.Polygon(self.SELF)

    @staticmethod
    def _rect(x0, y0, x1, y1):
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    def test_exact_duplicate_still_dropped(self):
        parts, status = geodata.resolve_against_self(self.SELF, self._self_polygon())
        self.assertEqual(status, 'self')
        self.assertEqual(parts, [])

    def test_disjoint_and_party_wall_untouched(self):
        for name, fp in (('disjoint', self._rect(20, 20, 28, 26)),
                         ('mitoyen', self._rect(8, 0, 16, 6))):
            with self.subTest(name):
                parts, status = geodata.resolve_against_self(fp, self._self_polygon())
                self.assertEqual(status, 'kept')
                self.assertAlmostEqual(parts[0].area, 48.0, places=6)

    def test_partial_overlap_is_clipped_not_dropped(self):
        """Voisin décalé de 1 m sur l'enveloppe : on retire les 6 m² d'empiétement
        et on garde les 42 m² restants, au lieu de perdre l'obstacle entier."""
        parts, status = geodata.resolve_against_self(self._rect(7, 0, 15, 6), self._self_polygon())
        self.assertEqual(status, 'clipped')
        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0].area, 42.0, places=6)
        self.assertAlmostEqual(parts[0].intersection(self._self_polygon()).area, 0.0, places=9)

    def test_engulfing_block_is_clipped_with_a_hole(self):
        """Cas que le critère d'origine ratait : un pâté de maisons digitalisé
        d'un seul tenant CONTIENT le logement étudié sans être lui. L'écarter
        supprimait tout le masque solaire du pâté ; il est désormais rogné, avec
        un trou à l'emplacement du bâtiment."""
        parts, status = geodata.resolve_against_self(self._rect(-5, -5, 20, 15), self._self_polygon())
        self.assertEqual(status, 'clipped')
        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0].area, 25 * 20 - 48, places=6)
        self.assertEqual(len(parts[0].interiors), 1, "le bâtiment étudié doit laisser un trou")

    def test_clipping_can_split_an_obstacle_in_two(self):
        """Un obstacle traversé de part en part par le bâtiment donne deux
        morceaux — d'où une LISTE de polygones en retour."""
        parts, status = geodata.resolve_against_self(self._rect(-5, 1, 20, 4), self._self_polygon())
        self.assertEqual(status, 'clipped')
        self.assertEqual(len(parts), 2)

    def test_sliver_remainder_is_dropped(self):
        """Reste inférieur à MIN_CLIPPED_AREA_M2 : un copeau extrudé sur toute la
        hauteur coûte des triangles sans rien masquer."""
        parts, status = geodata.resolve_against_self(self._rect(0, 0, 8.05, 6), self._self_polygon())
        self.assertEqual(status, 'self')
        self.assertEqual(parts, [])

    def test_clipped_pieces_are_extrudable(self):
        """Le rognage doit produire une géométrie que trimesh sait extruder,
        trous compris — sans quoi l'obstacle serait perdu à l'étape suivante."""
        parts, _ = geodata.resolve_against_self(self._rect(-5, -5, 20, 15), self._self_polygon())
        vertices, faces = geodata.extrude_polygon_mesh(parts[0], 9.0)
        self.assertGreater(len(vertices), 8)
        self.assertGreater(len(faces), 12)
        self.assertTrue(all(len(f) == 3 for f in faces))

    def test_no_self_polygon_keeps_everything(self):
        parts, status = geodata.resolve_against_self(self.SELF, None)
        self.assertEqual(status, 'kept')
        self.assertAlmostEqual(parts[0].area, 48.0, places=6)


class EnvironmentInvalidatesShadowTest(TestCase):
    """Lot AD (point L1) — modifier un environnement doit périmer l'ombrage de
    TOUS les bâtiments qui l'utilisent.

    Le mécanisme existait pour les deux autres causes (enveloppe du bâtiment
    modifiée, lien d'environnement changé) mais pas pour celle-ci : un bâtiment
    lié gardait une grille calculée contre l'ANCIENNE géométrie, et le calcul
    suivant rendait un résultat faux sans aucun signe. Test avec DB (TestCase et
    non SimpleTestCase) : c'est une requête de mise à jour groupée qui est en jeu.
    """

    def _building(self, name, environment=None):
        b = Building.objects.create(name=name, envelope={'vertices': [], 'triangles': []},
                                     environment=environment)
        Building.objects.filter(pk=b.pk).update(sun_visibility_stale=False)
        b.refresh_from_db()
        return b

    def test_changing_the_envelope_stales_every_linked_building(self):
        env = Environment.objects.create(name='env', envelope={'vertices': [], 'triangles': []})
        a, b = self._building('A', env), self._building('B', env)
        other = self._building('C')
        self.assertFalse(a.sun_visibility_stale)

        s = serializers.EnvironmentSerializer(
            env,
            data={'name': 'env',
                  'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                  'triangles': [{'v': [0, 1, 2]}]},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        s.save()

        a.refresh_from_db(); b.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(a.sun_visibility_stale)
        self.assertTrue(b.sun_visibility_stale)
        # Un bâtiment qui n'utilise PAS cet environnement ne doit pas être touché.
        self.assertFalse(other.sun_visibility_stale)

    def test_renaming_only_does_not_stale(self):
        """Renommer un environnement ne change pas sa géométrie : périmer
        l'ombrage forcerait un recalcul de plusieurs minutes pour rien."""
        env = Environment.objects.create(name='env2', envelope={'vertices': [], 'triangles': []})
        a = self._building('D', env)
        s = serializers.EnvironmentSerializer(env, data={'name': 'renommé'}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        a.refresh_from_db()
        self.assertFalse(a.sun_visibility_stale)
