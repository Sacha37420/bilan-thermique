"""Tests — geodata.

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
