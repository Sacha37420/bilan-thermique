"""Tests — usage.

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


class OccupancyDrivenPlanningTest(SimpleTestCase):
    """Lot AG — le planning horaire suit le calendrier d'occupation.

    Le Lot V n'a fait varier que les CONSIGNES de thermostat selon le calendrier.
    Ventilation, apports internes et volets restaient sur un unique « jour type » :
    un bureau dont le thermostat passait en hors gel le dimanche continuait d'être
    ventilé au débit d'occupation et de recevoir ses apports internes ce jour-là.
    """

    databases = []

    LAYERS = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 900.0, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}]

    def _envelope(self):
        vertices = [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 3.0]]
        return {'vertices': vertices,
                'triangles': geometry.compute_envelope_geometry(
                    vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])}

    @staticmethod
    def _weather(occupied_flags):
        """Une heure par entrée ; `occupied_flags[i] is None` = clé absente."""
        out = []
        for i, occ in enumerate(occupied_flags):
            p = {'t_ext': -5.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0,
                 'e_dir': 0.0, 'e_dif': 0.0, 'hour_index': i}
            if occ is not None:
                p['occupied'] = occ
            out.append(p)
        return out

    @staticmethod
    def _planning(debit, apports=0.0, volets=False):
        return [{'debit_vent_m3h': debit, 'eta_recup_vent': 0.0,
                 'apports_internes_w': apports, 'volets_fermes': volets} for _ in range(24)]

    def _run(self, weather, planning, planning_ferme=None):
        payload = {
            'dx_max': 0.05, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 20_000.0},
            't_init': 20.0, 'weather': weather, 'planning': planning, 'heure_debut': 0,
        }
        if planning_ferme is not None:
            payload['planning_ferme'] = planning_ferme
        return building_solver.run_building_simulation(
            self._envelope(), {1: self.LAYERS}, None, payload)

    def test_closed_hours_use_the_closed_planning(self):
        """Le cœur : à -5 °C dehors, une journée fermée doit moins ventiler donc
        moins refroidir qu'une journée occupée."""
        occupied = self._run(self._weather([True] * 24), self._planning(200.0),
                             self._planning(20.0))
        closed = self._run(self._weather([False] * 24), self._planning(200.0),
                           self._planning(20.0))
        self.assertGreater(closed['t_air'][-1], occupied['t_air'][-1])

    def test_without_closed_planning_nothing_changes(self):
        """Non-régression du Lot Q : sans planning_ferme, `occupied` n'a aucun
        effet — un payload existant se comporte exactement comme avant."""
        a = self._run(self._weather([True] * 24), self._planning(200.0))
        b = self._run(self._weather([False] * 24), self._planning(200.0))
        self.assertEqual(a['t_air'], b['t_air'])

    def test_absent_occupied_key_falls_back_to_the_open_planning(self):
        """Série sans `occupied` (météo collée à la main) : le planning normal
        s'applique, jamais celui de fermeture."""
        none_flags = self._run(self._weather([None] * 24), self._planning(200.0),
                               self._planning(20.0))
        all_open = self._run(self._weather([True] * 24), self._planning(200.0),
                             self._planning(20.0))
        self.assertEqual(none_flags['t_air'], all_open['t_air'])

    def test_internal_gains_follow_occupancy_too(self):
        """Les apports internes vivent dans le même planning : ils doivent
        disparaître un jour de fermeture, sinon on chaufferait un bâtiment vide
        avec des occupants absents."""
        with_gains = self._run(self._weather([True] * 24),
                               self._planning(0.0, apports=2000.0), self._planning(0.0, apports=0.0))
        without = self._run(self._weather([False] * 24),
                            self._planning(0.0, apports=2000.0), self._planning(0.0, apports=0.0))
        self.assertGreater(with_gains['t_air'][-1], without['t_air'][-1])

    def test_mixed_week_switches_hour_by_hour(self):
        """Une semaine réelle mélange les deux : le basculement doit se faire
        heure par heure, pas une fois pour tout le run."""
        flags = [(i // 24) < 5 for i in range(7 * 24)]  # 5 jours ouvrés puis 2 fermés
        mixed = self._run(self._weather(flags), self._planning(200.0), self._planning(20.0))
        all_open = self._run(self._weather([True] * (7 * 24)), self._planning(200.0),
                             self._planning(20.0))
        all_closed = self._run(self._weather([False] * (7 * 24)), self._planning(200.0),
                               self._planning(20.0))
        final = mixed['t_air'][-1]
        self.assertGreater(final, all_open['t_air'][-1])
        self.assertLess(final, all_closed['t_air'][-1])

    def test_shutters_can_differ_when_closed(self):
        """Les volets aussi : un bâtiment fermé peut les garder baissés en
        permanence. Vérifié en mode 'imposed', où seul le canal K agit."""
        envelope = self._envelope()
        envelope['triangles'][0]['shading_profile_id'] = 'volet-roulant'
        payload = {
            'dx_max': 0.05, 'h_e': 25.0,
            'interior': {'mode': 'imposed', 'h_i': 8.0, 't_int': 20.0},
            't_init': 20.0, 'planning': self._planning(0.0, volets=False),
            'planning_ferme': self._planning(0.0, volets=True), 'heure_debut': 0,
        }

        def run(flags):
            return building_solver.run_building_simulation(
                envelope, {1: self.LAYERS}, None,
                {**payload, 'weather': self._weather(flags)})['envelope_flux_w']

        self.assertNotEqual(run([True] * 24), run([False] * 24))


class ClosedPlanningSerializerTest(SimpleTestCase):
    """Lot AG — validation de planning_ferme."""

    databases = []

    @staticmethod
    def _payload(**extra):
        return {
            'dx_max': 0.02, 'h_e': 25.0,
            'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 200000.0},
            't_init': 15.0,
            'weather': [{'t_ext': 5.0, 'sun_azimuth': 0.0, 'sun_elevation': 10.0,
                         'e_dir': 0.0, 'e_dif': 0.0}],
            **extra,
        }

    ENTRY = {'debit_vent_m3h': 30.0, 'eta_recup_vent': 0.5, 'apports_internes_w': 100.0}

    def test_requires_exactly_24_entries(self):
        s = serializers.BuildingCalculRequestSerializer(
            data=self._payload(planning=[self.ENTRY] * 24, planning_ferme=[self.ENTRY] * 12))
        self.assertFalse(s.is_valid())
        self.assertIn('planning_ferme', s.errors)

    def test_rejected_without_planning(self):
        """Un planning de fermeture seul n'a pas de sens : il ne remplace que
        certaines heures, il ne peut pas porter tout le run."""
        s = serializers.BuildingCalculRequestSerializer(
            data=self._payload(planning_ferme=[self.ENTRY] * 24))
        self.assertFalse(s.is_valid())
        self.assertIn('planning_ferme', s.errors)

    def test_accepted_with_planning(self):
        s = serializers.BuildingCalculRequestSerializer(
            data=self._payload(planning=[self.ENTRY] * 24, planning_ferme=[self.ENTRY] * 24))
        self.assertTrue(s.is_valid(), s.errors)

    def test_occupied_defaults_to_none_on_each_point(self):
        s = serializers.BuildingCalculRequestSerializer(data=self._payload())
        self.assertTrue(s.is_valid(), s.errors)
        self.assertIsNone(s.validated_data['weather'][0]['occupied'])
