"""Tests — batiment solveur.

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


class HourlySetpointNoCoolingTest(SimpleTestCase):
    """Lot AI — « pas de climatisation » est codé t_max = 100 par les profils
    d'usage : la consigne horaire doit l'accepter (refusée à 50 depuis le Lot V)."""

    databases = []

    def test_hourly_t_max_100_accepted(self):
        point = {'t_ext': 5.0, 'sun_azimuth': 0.0, 'sun_elevation': -5.0, 'e_dir': 0.0, 'e_dif': 0.0,
                 't_min': 7.0, 't_max': 100.0}
        s = serializers.BuildingWeatherPointSerializer(data=point)
        self.assertTrue(s.is_valid(), s.errors)


class RefineEnvelopeTest(SimpleTestCase):
    """Raffinement local (Lot AK) : conforme, orienté, et sans propagation à
    tout le maillage quand une petite zone est déjà finement triangulée."""

    @staticmethod
    def _box(lx, ly, lz):
        V = [[0, 0, 0], [lx, 0, 0], [lx, ly, 0], [0, ly, 0],
             [0, 0, lz], [lx, 0, lz], [lx, ly, lz], [0, ly, lz]]
        F = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
             [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
        return V, [{'v': f, 'group': 'g'} for f in F]

    @staticmethod
    def _signed_volume(V, T):
        V = np.asarray(V, float)
        return sum(np.dot(V[t['v'][0]], np.cross(V[t['v'][1]], V[t['v'][2]])) for t in T) / 6.0

    def _check_closed(self, T):
        count = {}
        for t in T:
            v = t['v']
            for i in range(3):
                e = (v[i], v[(i + 1) % 3])
                count[e] = count.get(e, 0) + 1
        for (a, b), n in count.items():
            self.assertEqual(n, 1)
            self.assertEqual(count.get((b, a)), 1, 'arête sans jumelle orientée : fissure')

    def test_refined_box_is_closed_oriented_and_below_threshold(self):
        V, T = self._box(30.0, 8.0, 6.0)
        V2, T2 = geometry.refine_envelope(V, T, 2.0)
        self._check_closed(T2)
        self.assertAlmostEqual(self._signed_volume(V2, T2), 30 * 8 * 6, places=6)
        V2a = np.asarray(V2)
        for t in T2:
            for i in range(3):
                self.assertLessEqual(np.linalg.norm(V2a[t['v'][i]] - V2a[t['v'][(i + 1) % 3]]), 2.0 + 1e-9)
            self.assertEqual(t['group'], 'g')

    def test_fine_patch_does_not_flood_mesh(self):
        # Boîte dont une face est déjà très finement subdivisée (comme autour
        # des baies) : l'ancien algorithme quadruplait tout le maillage à
        # chaque itération jusqu'à dépasser la limite.
        V, T = self._box(37.0, 4.0, 4.0)
        V = [list(map(float, v)) for v in V]
        # Zone fine conforme : insertion répétée de centroïdes dans une face
        # (bords extérieurs inchangés), triangles effilés comme autour des baies.
        fine = [T.pop(10)['v']]  # pignon x=0, 4 × 4 m : déjà sous le seuil
        for _ in range(5):
            nxt = []
            for a, b, c in fine:
                V.append([(V[a][k] + V[b][k] + V[c][k]) / 3 for k in range(3)])
                m = len(V) - 1
                nxt += [[a, b, m], [b, c, m], [c, a, m]]
            fine = nxt
        T += [{'v': f, 'group': 'g'} for f in fine]
        self._check_closed(T)
        n_fine = len(T)
        V2, T2 = geometry.refine_envelope(V, T, 6.8)
        self._check_closed(T2)
        # Les triangles fins, déjà sous le seuil, restent intacts ; seules les
        # longues faces sont subdivisées (ancien algorithme : > 20 000).
        kept = {tuple(t['v']) for t in T2}
        self.assertTrue(all(tuple(f) in kept for f in fine))
        self.assertLess(len(T2), 2 * n_fine)
