"""Tests — ombrage.

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


class GroundReflectionFactorTest(SimpleTestCase):
    databases = []

    def test_uniform_ground_gives_analytic_value(self):
        """Sol plat uniforme d'albédo 0,3, aucun obstacle : exactement
        ρ(1 − cos β)/2 = 0,3 × 0,5 pour un mur vertical, sans bruit."""
        env = _vertical_wall_envelope()
        self.assertAlmostEqual(env['triangles'][0]['tilt_deg'], 90.0, places=1)
        self.assertLess(env['triangles'][0]['normal'][1], -0.99)
        f = shadow.compute_ground_reflection_factors(env, _terrain_env(), lambda x, y: np.full(np.shape(x), 0.3),
                                                     ground_obj_ids={7})
        self.assertAlmostEqual(f[0], 0.15, places=4)
        # Sans terrain dans l'environnement : repli sur le plan du pied, même valeur.
        f2 = shadow.compute_ground_reflection_factors(env, None, lambda x, y: np.full(np.shape(x), 0.3))
        self.assertAlmostEqual(f2[0], 0.15, places=4)

    def test_roof_sees_no_ground_and_obstacle_darkens(self):
        roof = _single_triangle_envelope(area=4.0)
        f = shadow.compute_ground_reflection_factors(roof, _terrain_env(), lambda x, y: np.full(np.shape(x), 0.3),
                                                     ground_obj_ids={7})
        self.assertEqual(f[0], 0.0)
        # Un mur noir de 10 m face à la paroi masque une partie du sol vu.
        wall = _vertical_wall_envelope()
        env = _terrain_env()
        base = len(env['vertices'])
        env['vertices'] += [[-20, -3, 0], [20, -3, 0], [20, -3, 10], [-20, -3, 10]]
        env['triangles'] += [{'v': [base, base + 1, base + 2], 'obj': 9}, {'v': [base, base + 2, base + 3], 'obj': 9}]
        f = shadow.compute_ground_reflection_factors(wall, env, lambda x, y: np.full(np.shape(x), 0.3),
                                                     ground_obj_ids={7})
        self.assertLess(f[0], 0.15 - 0.02)
        self.assertGreater(f[0], 0.0)

    def test_floor_on_ground_receives_nothing(self):
        """Un plancher sur terre-plein regarde le sol par sa face enterrée :
        aucune réflexion, même si géométriquement il « voit » tout le sol."""
        vertices = [[0.0, 0.0, 0.0], [0.0, 4.0, 0.0], [4.0, 0.0, 0.0]]
        tris = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'boundary': 'ground'}])
        self.assertAlmostEqual(tris[0]['tilt_deg'], 180.0, places=3)
        env = {'vertices': vertices, 'triangles': tris}
        f = shadow.compute_ground_reflection_factors(env, _terrain_env(), lambda x, y: np.full(np.shape(x), 0.3),
                                                     ground_obj_ids={7})
        self.assertEqual(f[0], 0.0)
        tris[0]['boundary'] = 'exterior_air'   # sous-face d'un porte-à-faux : elle, oui
        f = shadow.compute_ground_reflection_factors(env, _terrain_env(), lambda x, y: np.full(np.shape(x), 0.3),
                                                     ground_obj_ids={7})
        self.assertGreater(f[0], 0.25)

    def test_albedo_sampled_where_rays_land(self):
        """Sol sombre (0,05) partout sauf une bande claire (0,6) devant la paroi :
        le facteur doit refléter le sol effectivement vu, pas une moyenne de zone."""
        wall = _vertical_wall_envelope()
        near = lambda x, y: np.where((np.asarray(y) < 0) & (np.asarray(y) > -6), 0.6, 0.05)  # noqa: E731
        far = lambda x, y: np.where(np.asarray(y) < -30, 0.6, 0.05)  # noqa: E731
        f_near = shadow.compute_ground_reflection_factors(wall, _terrain_env(), near, ground_obj_ids={7})[0]
        f_far = shadow.compute_ground_reflection_factors(wall, _terrain_env(), far, ground_obj_ids={7})[0]
        self.assertGreater(f_near, 3 * f_far)


class GroundReflectionSolverTest(SimpleTestCase):
    """Le terme réfléchi est, pour le solveur, exactement un supplément de
    diffus : e_glo = e_dif·F_ciel + f·GHI. Oracle : un run avec facteur f et
    e_dif = D doit être IDENTIQUE à un run sans facteur et e_dif = D·(F_ciel + f)/F_ciel
    (soleil sous l'horizon : GHI = D)."""

    databases = []

    def _run(self, e_dif, ground_factor=None, alpha_ext=None, layers=None):
        env = _wall_envelope(4.0)
        if alpha_ext is not None:
            env['triangles'][0]['alpha_ext'] = alpha_ext
        weather = [{'t_ext': 5.0, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': e_dif}
                   for _ in range(12)]
        payload = {'dx_max': 0.02, 'h_e': 25.0, 'interior': {'mode': 'free', 'h_i': 8.0, 'c_air_int': 5e4},
                   't_init': 15.0, 'weather': weather}
        sv = {'ground_reflect_factor': [ground_factor]} if ground_factor is not None else None
        return building_solver.run_building_simulation(env, {1: layers or [_flat_wall_layer()]}, sv, payload)

    def test_reflection_equivalent_to_extra_diffuse(self):
        d, f = 120.0, 0.12
        with_reflection = self._run(d, ground_factor=f)
        equivalent = self._run(d * (0.5 + f) / 0.5)
        for a, b in zip(with_reflection['t_air'], equivalent['t_air']):
            self.assertAlmostEqual(a, b, places=9)

    def test_no_factor_is_historical_behaviour(self):
        a = self._run(120.0)
        b = self._run(120.0, ground_factor=0.0)
        self.assertEqual(a['t_air'], b['t_air'])

    def test_alpha_ext_equals_editing_the_layer(self):
        """alpha_ext sur le triangle ⇔ couche extérieure modifiée à la main."""
        edited = dict(_flat_wall_layer(), alpha=0.85, r=0.15)
        a = self._run(200.0, alpha_ext=0.85)
        b = self._run(200.0, layers=[edited])
        for x, y in zip(a['t_air'], b['t_air']):
            self.assertAlmostEqual(x, y, places=9)
        c = self._run(200.0)
        self.assertGreater(a['t_air'][-1], c['t_air'][-1])   # plus absorbant → plus chaud


class CrownSolidTest(SimpleTestCase):
    """Lot AJ — houppier reconstruit sur ses propres points LiDAR."""

    databases = []

    def _cone(self, n=4000, r0=3.0, z0=4.0, z1=14.0, seed=2):
        rng = np.random.default_rng(seed)
        z = rng.uniform(z0, z1, n)
        rmax = r0 * (z1 - z) / (z1 - z0)          # conifère : cône, pointe en haut
        a = rng.uniform(-math.pi, math.pi, n)
        rr = rmax * np.sqrt(rng.uniform(0, 1, n))
        return np.column_stack([10 + rr * np.cos(a), 5 + rr * np.sin(a), z])

    def test_cone_closed_and_tapered(self):
        import trimesh
        from .. import observed_env as oe
        pts = self._cone()
        verts, tris = oe.crown_solid(pts, 4.0, 14.0, (10.0, 5.0))
        m = trimesh.Trimesh(verts, [t['v'] for t in tris], process=False)
        self.assertTrue(m.is_watertight and m.is_winding_consistent and m.volume > 0)
        V = np.array(verts)
        self.assertAlmostEqual(V[:, 2].max(), 14.0, places=3)
        low = V[(V[:, 2] > 4.5) & (V[:, 2] < 6.5)]
        high = V[(V[:, 2] > 11.0) & (V[:, 2] < 13.9)]
        r_low = np.hypot(low[:, 0] - 10, low[:, 1] - 5).mean()
        r_high = np.hypot(high[:, 0] - 10, high[:, 1] - 5).mean()
        self.assertGreater(r_low, 2 * r_high)            # la forme suit les points
        # Volume du même ordre que le cône (π r² h / 3 ≈ 94 m³), bien loin du
        # prisme équivalent (π r² h ≈ 283 m³).
        self.assertLess(m.volume, 180.0)
        self.assertGreater(m.volume, 50.0)

    def test_too_few_points_falls_back(self):
        from .. import observed_env as oe
        self.assertIsNone(oe.crown_solid(self._cone(n=10), 4.0, 14.0, (10.0, 5.0)))
