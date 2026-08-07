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

from django.test import SimpleTestCase

from . import building_solver, geometry, shadow, solver

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
    CONSTANTE égale à t_ground (même dx_max/h_e/h_i/t_int/t_init/heures)."""

    databases = []

    def test_ground_triangle_uses_t_ground_not_weather_t_ext(self):
        layers = [_flat_wall_layer(e=0.1, lam=1.0), _flat_wall_layer(e=0.08, lam=0.035, rho=25.0, c=1030.0)]
        h_e, h_i, t_int, dx_max = 25.0, 8.0, 19.0, 0.02
        hours = 20
        t_ground = 13.0

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
        }
        result_ground = building_solver.run_building_simulation(envelope_ground, {1: layers}, None, payload_ground)

        payload_ref = {
            'layers': layers, 'dx_max': dx_max, 'h_e': h_e,
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
