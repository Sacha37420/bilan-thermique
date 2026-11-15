"""Tests — environnement.

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


class LocalFrameTest(SimpleTestCase):
    databases = []

    def test_round_trip_and_no_convergence_on_central_meridian(self):
        from .. import observed_env as oe
        frame = oe.LocalFrame(46.5, 3.0)
        self.assertAlmostEqual(math.degrees(frame.convergence_rad), 0.0, places=3)
        f2 = oe.LocalFrame(48.11, -1.68, north_offset_deg=17.0)
        e, n = f2.to_l93([12.3, -80.0], [45.6, 3.0])
        x, y = f2.to_local(e, n)
        self.assertAlmostEqual(float(x[0]), 12.3, places=6)
        self.assertAlmostEqual(float(y[1]), 3.0, places=6)

    def test_true_north_maps_to_plus_y(self):
        """Un point plein nord (même longitude) doit tomber sur l'axe +Y local —
        oracle indépendant de la formule de convergence : pyproj direct."""
        from pyproj import Transformer
        from .. import observed_env as oe
        frame = oe.LocalFrame(48.11, -1.68)       # convergence ≈ 3,4° en Bretagne
        e, n = Transformer.from_crs('EPSG:4326', 'EPSG:2154', always_xy=True).transform(-1.68, 48.112)
        x, y = frame.to_local(e, n)
        self.assertLess(abs(float(x)), 0.02)
        self.assertAlmostEqual(float(y), 222.4, delta=0.5)   # 0,002° de latitude ≈ 222,4 m


class ObservedEnvironmentSceneTest(SimpleTestCase):
    databases = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from .. import observed_env as oe
        frame, points = _scene_points()
        cls.frame = frame
        cls.objects, cls.ground_z, cls.stats, cls.warnings = oe.build_objects(
            frame, 40.0, points, _scene_bdtopo(frame), acquisition_months=[2],
        )

    def _by(self, **kw):
        return [o for o in self.objects if all(o.get(k) == v for k, v in kw.items())]

    def test_ground_reference_is_terrain_at_origin(self):
        self.assertAlmostEqual(self.ground_z, SCENE_GROUND_Z, delta=0.1)

    def test_global_offset_recovered(self):
        dx, dy = self.stats['global_shift_m']
        self.assertAlmostEqual(dx, -BDTOPO_OFFSET[0], delta=0.3)
        self.assertAlmostEqual(dy, -BDTOPO_OFFSET[1], delta=0.3)

    def test_gable_house_heights(self):
        from .. import observed_env as oe
        house = next(o for o in self._by(kind='building')
                     if o['info'].get('bdtopo_id') == 'A')
        self.assertEqual(house['origin'], 'bdtopo+lidar')
        self.assertTrue(_closed_and_outward(house))
        top = max(v[2] for v in house['vertices'])
        self.assertAlmostEqual(top, HOUSE['ridge'], delta=0.4)
        # Égout : les sommets de mur aux coins, sous la toiture, pas les points de façade.
        poly = oe.rings_to_poly(house['footprint'])
        corners = [v for v in house['vertices'] if v[2] > 1.0
                   and min(math.dist(v[:2], c) for c in poly.exterior.coords) < 0.3]
        self.assertTrue(corners)
        for v in corners:
            self.assertAlmostEqual(v[2], HOUSE['eave'], delta=0.6)

    def test_flat_building_not_dragged_down_by_facade_points(self):
        flat = next(o for o in self._by(kind='building') if o['info'].get('bdtopo_id') == 'B')
        roof = [v[2] for v in flat['vertices'] if v[2] > 2.0]
        expected = FLAT['h'] + SCENE_SLOPE_X * 20.0
        self.assertTrue(all(abs(z - expected) < 0.4 for z in roof), roof)

    def test_demolished_building_removed_by_default(self):
        gone = next(o for o in self.objects if o['info'].get('bdtopo_id') == 'D')
        self.assertEqual(gone['status'], 'removed')
        self.assertIn('LiDAR', gone['reason'])

    def test_building_missing_from_bdtopo_found(self):
        from .. import observed_env as oe
        shed = self._by(kind='building', origin='lidar')
        self.assertEqual(len(shed), 1)
        c = oe.rings_to_poly(shed[0]['footprint']).centroid
        self.assertAlmostEqual(c.x, -20.0, delta=0.6)
        self.assertAlmostEqual(c.y, -18.0, delta=0.6)

    def test_tree_height_and_measured_gap(self):
        trees = self._by(kind='vegetation')
        self.assertGreaterEqual(len(trees), 1)
        tree = max(trees, key=lambda o: o['info']['height_m'])
        self.assertAlmostEqual(tree['info']['height_m'], TREE['top'], delta=1.0)
        # Vol en février : la trouée mesurée (≈ 25 %) est conservée, mais la
        # valeur « en feuilles » par défaut est appliquée.
        self.assertAlmostEqual(tree['info']['k_measured'], 0.25, delta=0.08)
        self.assertEqual(tree['k'], 0.20)
        self.assertTrue(_closed_and_outward(tree))

    def test_terrain_follows_slope_with_few_triangles(self):
        terrain = self._by(kind='terrain')[0]
        self.assertLess(len(terrain['triangles']), 200)
        for x, _y, z in terrain['vertices']:
            self.assertAlmostEqual(z, SCENE_SLOPE_X * x, delta=0.35)

    def test_composed_envelope_only_active_objects(self):
        from .. import observed_env as oe
        env = oe.compose_envelope(self.objects)
        active = {o['id'] for o in self.objects if o['status'] == 'active'}
        self.assertEqual({t['obj'] for t in env['triangles']}, active)
        veg_ids = {o['id'] for o in self._by(kind='vegetation')}
        self.assertTrue(all(('k' in t) == (t['obj'] in veg_ids) for t in env['triangles']))


class RegistrationTest(SimpleTestCase):
    databases = []

    def test_footprint_centered_in_wider_roof(self):
        """Un toit plus large que l'emprise d'un seul côté (emprise décalée,
        entièrement contenue dans le toit) : le recouvrement seul ne voit rien
        à corriger, la pondération par profondeur doit recentrer."""
        import numpy as np
        from .. import observed_env as oe
        roof = np.zeros((80, 80), dtype=bool)
        roof[20:60, 20:44] = True          # toit de 24 mailles de large
        fp = np.zeros_like(roof)
        fp[22:58, 20:40] = True            # emprise collée au bord gauche
        dx, dy, _b, _z = oe.best_offset(fp, oe.depth_weights(roof, 6), 8)
        self.assertAlmostEqual(dx, 2.0, delta=0.6)
        self.assertAlmostEqual(dy, 0.0, delta=0.6)


class ObjectToBuildingTest(SimpleTestCase):
    databases = []

    def test_groups_and_party_wall(self):
        import shapely.geometry as sg
        from .. import observed_env as oe
        a = sg.box(0, 0, 10, 8)
        b = sg.box(10, 0, 18, 8)          # mitoyen sur l'arête x = 10
        va, ta = oe.prism_solid(a, 0.0, 6.0)
        vb, tb = oe.prism_solid(b, 0.0, 6.0)
        oa = oe.make_object(1, 'building', 'bdtopo+lidar', 'A', va, ta, a)
        ob = oe.make_object(2, 'building', 'bdtopo+lidar', 'B', vb, tb, b)
        vertices, triangles = oe.building_envelope_from_object(oa, [oa, ob])
        groups = {t['group'] for t in triangles}
        self.assertIn('toiture_plate', groups)
        self.assertIn('sol', groups)
        party = [g for g in groups if g.endswith('_mitoyen')]
        self.assertEqual(len(party), 1)
        # Le mur mitoyen est bien celui de l'arête x = 10.
        xs = {round(vertices[i][0], 3) for t in triangles if t['group'] == party[0] for i in t['v']}
        self.assertEqual(xs, {10.0})
        self.assertTrue(all(t['boundary'] == 'ground' for t in triangles if t['group'] == 'sol'))


class FitImportTest(SimpleTestCase):
    databases = []

    def test_y_up_millimetre_model_rotated(self):
        """Boîte de 10 × 6 × 5 m exportée en Y-up et en millimètres (cas Blender →
        STL typique), cible tournée de 30° : axe, unité, rotation et position
        doivent être retrouvés."""
        import shapely.affinity
        import shapely.geometry as sg
        import trimesh
        from .. import observed_env as oe
        box = trimesh.creation.box(extents=[10.0, 5.0, 6.0])     # x, y(hauteur), z
        V = box.vertices * 1000.0
        V[:, 1] -= V[:, 1].min()
        triangles = [{'v': [int(i) for i in f]} for f in box.faces]
        target = shapely.affinity.rotate(sg.box(40, 20, 50, 26), 30, origin='centroid')
        verts, kept, report = oe.fit_import(V.tolist(), triangles, target, base_z=1.5)
        self.assertEqual(report['up_axis'], 'y')
        self.assertEqual(report['scale'], 0.001)
        self.assertGreater(report['iou'], 0.95)
        self.assertAlmostEqual(min(v[2] for v in verts), 1.5, places=3)
        self.assertAlmostEqual(max(v[2] for v in verts), 6.5, places=2)
        self.assertEqual(len(kept), len(triangles))


class EnvironmentServiceTest(TestCase):
    """Actions sur les objets d'un environnement : statut, bâtiment étudié,
    remplacement par un modèle importé — avec la base de données."""

    def _env(self):
        import shapely.geometry as sg
        from .. import observed_env as oe
        objs = []
        for k, box in enumerate([sg.box(0, 0, 10, 8), sg.box(20, 0, 28, 8)], start=1):
            v, t = oe.prism_solid(box, 0.0, 6.0)
            objs.append(oe.make_object(k, 'building', 'bdtopo+lidar', f'B{k}', v, t, box,
                                       info={'base_z': 0.0}))
        tv, tt = oe.prism_solid(sg.box(-5, -5, -3, -3), 2.0, 8.0)
        objs.append(oe.make_object(3, 'vegetation', 'lidar', 'Arbre', tv, [{'v': x['v']} for x in tt],
                                   sg.box(-5, -5, -3, -3), k=0.2))
        return Environment.objects.create(
            name='obs', georef_lat=47.0, georef_lon=0.7, georef_ground_z=90.0,
            scene_objects=objs, envelope=oe.compose_envelope(objs),
        )

    def test_remove_restore_recomposes_and_stales(self):
        from .. import environment_service as svc
        env = self._env()
        linked = Building.objects.create(name='lié', environment=env, sun_visibility_stale=False)
        n0 = len(env.envelope['triangles'])
        svc.set_status(env, [3], 'removed')
        env.refresh_from_db()
        linked.refresh_from_db()
        self.assertTrue(linked.sun_visibility_stale)
        self.assertNotIn(3, {t['obj'] for t in env.envelope['triangles']})
        self.assertLess(len(env.envelope['triangles']), n0)
        svc.set_status(env, [3], 'active')
        env.refresh_from_db()
        self.assertEqual(len(env.envelope['triangles']), n0)

    def test_study_object_creates_aligned_building(self):
        from .. import environment_service as svc
        env = self._env()
        building, env = svc.study_object(env, 1, 'Ma maison')
        self.assertEqual(building.name, 'Ma maison')
        self.assertEqual(building.environment_id, env.pk)
        self.assertEqual((building.georef_lat, building.georef_lon, building.georef_ground_z), (47.0, 0.7, 90.0))
        obj = next(o for o in env.scene_objects if o['id'] == 1)
        self.assertEqual((obj['status'], obj['building_id']), ('studied', building.pk))
        self.assertNotIn(1, {t['obj'] for t in env.envelope['triangles']})
        # Enveloppe prête pour le solveur : géométrie calculée, sol marqué.
        tris = building.envelope['triangles']
        self.assertTrue(all('area' in t for t in tris))
        self.assertTrue(any(t['boundary'] == 'ground' for t in tris))
        with self.assertRaises(svc.EnvironmentServiceError):
            svc.study_object(env, 1)
        with self.assertRaises(svc.EnvironmentServiceError):
            svc.study_object(env, 3)      # un arbre

    def test_replace_object_with_import(self):
        import trimesh
        from .. import environment_service as svc
        env = self._env()
        box = trimesh.creation.box(extents=[8.0, 8.0, 6.0])      # 8 × 8 au lieu de 8 × 8 : même emprise
        box.apply_translation([100.0, 100.0, 50.0])              # repère quelconque
        building, env, fit = svc.replace_object(
            env, 2, box.vertices.tolist(), [{'v': [int(i) for i in f]} for f in box.faces],
        )
        self.assertGreater(fit['iou'], 0.95)
        xs = [v[0] for v in building.envelope['vertices']]
        self.assertAlmostEqual(min(xs), 20.0, delta=0.3)
        self.assertAlmostEqual(min(v[2] for v in building.envelope['vertices']), 0.0, places=3)
        obj = next(o for o in env.scene_objects if o['id'] == 2)
        self.assertEqual(obj['status'], 'studied')

    def test_objects_view(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from ..views import EnvironmentObjectsView
        env = self._env()
        req = APIRequestFactory().patch(f'/api/environnements/{env.pk}/objets/',
                                        {'ids': [1, 2], 'status': 'removed'}, format='json')
        force_authenticate(req, user=mock.Mock(is_authenticated=True))
        resp = EnvironmentObjectsView.as_view()(req, pk=env.pk)
        self.assertEqual(resp.status_code, 200)
        statuses = {o['id']: o['status'] for o in resp.data['objects']}
        self.assertEqual(statuses, {1: 'removed', 2: 'removed', 3: 'active'})

    def test_generate_with_mocked_network(self):
        """Chaîne complète jusqu'à l'Environment enregistré, réseau remplacé
        par la scène synthétique."""
        from .. import environment_service as svc
        from .. import lidar_source
        frame, points = _scene_points(half=40.0)
        tiles = [{'url': 'x', 'name': 't.copc.laz', 'acq_start': '2023-02-08', 'acq_end': '2023-02-08',
                  'classification': 'IGN_AUTO_V5'}]
        with mock.patch.object(lidar_source, 'fetch_lidar_tiles', return_value=tiles), \
                mock.patch.object(lidar_source, 'read_points',
                                  return_value=(points, {'requests': 1, 'bytes': 10, 'points_read': 1})), \
                mock.patch.object(lidar_source, 'fetch_bdtopo_buildings_l93', return_value=_scene_bdtopo(frame)):
            env, summary = svc.generate({'lat': SCENE_LAT, 'lon': SCENE_LON, 'radius_m': 40.0})
        self.assertEqual(summary['source'], 'lidar')
        self.assertEqual(env.generation['lidar']['acquisition'], ['2023-02-08'])
        self.assertAlmostEqual(env.georef_ground_z, SCENE_GROUND_Z, delta=0.1)
        kinds = {o['kind'] for o in env.scene_objects}
        self.assertEqual(kinds, {'building', 'vegetation', 'terrain'})
        self.assertEqual(len(env.envelope['triangles']), summary['n_triangles'])

    def test_generate_falls_back_outside_lidar_coverage(self):
        from .. import environment_service as svc
        from .. import lidar_source
        footprint = [[47.00000, 0.70000], [47.00000, 0.70010], [47.00007, 0.70010], [47.00007, 0.70000]]
        with mock.patch.object(lidar_source, 'fetch_lidar_tiles', return_value=[]), \
                mock.patch.object(elevation, 'ground_altitude', return_value=(90.0, 'ign')), \
                mock.patch.object(geodata, 'fetch_ign_buildings', return_value=[
                    {'footprint_latlon': footprint, 'height_m': 9.0, 'approx_height': False,
                     'base_z': 91.0, 'source': 'ign'}]):
            env, summary = svc.generate({'lat': 47.0, 'lon': 0.7, 'radius_m': 50.0,
                                         'include_vegetation': False, 'include_terrain': False})
        self.assertEqual(summary['source'], 'legacy')
        self.assertIn('LiDAR HD indisponible', summary['warnings'][0])
        b = env.scene_objects[0]
        # z relatif au sol d'origine (90 m), pas l'altitude NGF absolue.
        self.assertAlmostEqual(min(v[2] for v in b['vertices']), 1.0, places=2)
        self.assertAlmostEqual(max(v[2] for v in b['vertices']), 10.0, places=2)


# ── Lot AI — sol (albédo), matériaux, fusion de bâtiments ─────────────────────

class CatalogueExistingBuildingsTest(SimpleTestCase):
    databases = []

    def test_every_suggested_model_exists_and_is_valid(self):
        """Toute paroi que la pré-assignation peut suggérer doit exister au
        catalogue sous CE nom exact — sinon la famille reste silencieusement
        non assignée."""
        from api.management.commands.seed_paroi_catalogue import CATALOGUE, EXISTANT
        from .. import observed_env as oe
        names = {e['name'] for e in CATALOGUE + EXISTANT}
        suggested = set(oe.WALL_BY_ERA.values()) | set(oe.ROOF_BY_ERA.values()) | set(oe.FLOOR_BY_ERA.values())
        suggested |= {'Mur pierre 50 cm (avant 1948)', 'Mur brique pleine 34 cm (avant 1948)',
                      'Mur pan de bois / torchis (avant 1948)', 'Mur béton banché 16 cm non isolé (1948–1974)',
                      'Mur parpaing 20 cm non isolé (1948–1974)', 'Mur ossature bois (isolant 100 mm)'}
        self.assertEqual(suggested - names, set())
        for entry in EXISTANT:
            with self.subTest(name=entry['name']):
                s = serializers.LayerSerializer(data=entry['layers'], many=True)
                self.assertTrue(s.is_valid(), s.errors)

    def test_indicative_u_values(self):
        """U recalculé à la main depuis les couches (Rsi 0,13 / Rse 0,04) :
        l'ordre de grandeur annoncé dans la description doit être tenu."""
        from api.management.commands.seed_paroi_catalogue import EXISTANT
        by_name = {e['name']: e['layers'] for e in EXISTANT}

        def u(name):
            return 1.0 / (0.17 + sum(l['e'] / l['lam'] for l in by_name[name]))
        self.assertAlmostEqual(u('Mur pierre 50 cm (avant 1948)'), 1.89, delta=0.05)
        self.assertAlmostEqual(u('Mur béton banché 16 cm non isolé (1948–1974)'), 3.17, delta=0.05)
        self.assertAlmostEqual(u('Mur maçonné ITI — 1975–1981 (isolant 40 mm)'), 0.71, delta=0.03)


class MaterialsSuggestionTest(SimpleTestCase):
    databases = []

    def test_decode_two_digit_codes(self):
        from .. import observed_env as oe
        self.assertEqual(oe.decode_materials('35', oe.WALL_MATERIALS), ['béton', 'aggloméré'])
        self.assertEqual(oe.decode_materials('10', oe.WALL_MATERIALS), ['pierre'])
        self.assertEqual(oe.decode_materials('00', oe.WALL_MATERIALS), [])
        self.assertEqual(oe.decode_materials('03', oe.ROOF_MATERIALS), ['zinc aluminium'])

    def test_era_and_material_choices(self):
        from .. import observed_env as oe
        s = oe.suggest_materials({'mat_murs': '10', 'mat_toit': '20', 'annee': 1930})
        self.assertEqual(s['wall'], 'Mur pierre 50 cm (avant 1948)')
        self.assertEqual(s['roof'], 'Toiture non isolée (avant 1975)')
        self.assertEqual(s['roof_alpha'], 0.90)          # ardoise, sans mesure
        s = oe.suggest_materials({'mat_murs': '30', 'annee': 1965})
        self.assertEqual(s['wall'], 'Mur béton banché 16 cm non isolé (1948–1974)')
        s = oe.suggest_materials({'mat_murs': '50', 'annee': 1995, 'roof_albedo': 0.25})
        self.assertEqual(s['wall'], 'Mur maçonné ITI — 1989–2000 (isolant 80 mm)')
        self.assertEqual(s['roof_alpha'], 0.75)          # mesurée : 1 − 0,25
        self.assertEqual(s['wall_alpha'], 0.50)          # aggloméré enduit
        # Ni année ni matériau : aucune isolation inventée.
        s = oe.suggest_materials({})
        self.assertIsNone(s['wall'])
        self.assertIsNone(s['roof'])


class OrthoAlbedoTest(SimpleTestCase):
    databases = []

    def test_broadband_albedo_from_rgb_and_nir(self):
        """Gris 50 % dans le visible ET l'infrarouge : albédo = 0,5^2,2.
        Même visible mais infrarouge très clair (végétation) : plus élevé."""
        import numpy as np
        from .. import observed_env as oe
        rgb = np.full((10, 10, 3), 128, dtype=np.uint8)
        irc = np.full((10, 10, 3), 128, dtype=np.uint8)
        ortho = oe.OrthoImage(rgb, irc, (0.0, 0.0, 10.0, 10.0))
        self.assertAlmostEqual(float(ortho.albedo_at(5.0, 5.0)), (128 / 255) ** 2.2, places=4)
        irc[..., 0] = 230
        veg = oe.OrthoImage(rgb, irc, (0.0, 0.0, 10.0, 10.0))
        self.assertGreater(float(veg.albedo_at(5.0, 5.0)), float(ortho.albedo_at(5.0, 5.0)) + 0.1)
        self.assertGreater(float(veg.ndvi_at(5.0, 5.0)), 0.3)


class MergeObjectsTest(SimpleTestCase):
    databases = []

    def test_two_parts_one_envelope_without_inner_wall(self):
        """Corps à 6 m (10 × 8) accolé à une aile à 9 m (8 × 8) : UN volume
        fermé, volume ≈ somme des parties, et aucun mur sur l'arête commune
        sous le toit le plus bas (ce serait un mur intérieur compté extérieur)."""
        import shapely.geometry as sg
        import trimesh
        from .. import observed_env as oe
        a, b = sg.box(0, 0, 10, 8), sg.box(10, 0, 18, 8)
        va, ta = oe.prism_solid(a, 0.0, 6.0)
        vb, tb = oe.prism_solid(b, 0.0, 9.0)
        objs = [oe.make_object(1, 'building', 'bdtopo+lidar', 'A', va, ta, a, {'annee': 1930, 'mat_murs': '10'}),
                oe.make_object(2, 'building', 'bdtopo+lidar', 'B', vb, tb, b, {'annee': 1990})]
        verts, tris, poly, info = oe.merge_objects(objs)
        m = trimesh.Trimesh(verts, [t['v'] for t in tris], process=False)
        self.assertTrue(m.is_watertight and m.is_winding_consistent)
        self.assertAlmostEqual(m.volume, 10 * 8 * 6 + 8 * 8 * 9, delta=0.04 * 1056)
        self.assertAlmostEqual(poly.area, 18 * 8, delta=0.5)
        inner_low = [t for t in tris if t['group'].startswith('mur')
                     and all(abs(verts[i][0] - 10.0) < 0.05 for i in t['v'])
                     and max(verts[i][2] for i in t['v']) < 5.5]
        self.assertEqual(inner_low, [])
        self.assertEqual(info['mat_murs'], '10')          # attributs de la plus grande partie
        self.assertEqual(info['merged_ids'], [1, 2])

    def test_disjoint_selection_refused(self):
        import shapely.geometry as sg
        from .. import observed_env as oe
        a, b = sg.box(0, 0, 5, 5), sg.box(20, 0, 25, 5)
        objs = [oe.make_object(k, 'building', 'lidar', 'X', *oe.prism_solid(p, 0, 5), p)
                for k, p in ((1, a), (2, b))]
        with self.assertRaises(oe.ObservedEnvError):
            oe.merge_objects(objs)


class StudyObjectsMaterialsTest(TestCase):
    def test_merge_and_preassign(self):
        import shapely.geometry as sg
        from django.core.management import call_command
        from .. import environment_service as svc
        from .. import observed_env as oe
        from ..models import ParoiModel
        call_command('seed_paroi_catalogue', stdout=mock.MagicMock())
        a, b = sg.box(0, 0, 10, 8), sg.box(10, 0, 18, 8)
        objs = [oe.make_object(1, 'building', 'bdtopo+lidar', 'A', *oe.prism_solid(a, 0.0, 6.0), a,
                               {'annee': 1965, 'mat_murs': '30', 'mat_toit': '10', 'roof_albedo': 0.2}),
                oe.make_object(2, 'building', 'bdtopo+lidar', 'B', *oe.prism_solid(b, 0.0, 6.0), b, {})]
        env = Environment.objects.create(name='mat', georef_lat=47.0, georef_lon=0.7, georef_ground_z=90.0,
                                         scene_objects=objs, envelope=oe.compose_envelope(objs))
        building, env, report = svc.study_objects(env, [1, 2], 'Maison réunie')
        tris = building.envelope['triangles']
        wall_id = ParoiModel.objects.get(name='Mur béton banché 16 cm non isolé (1948–1974)').pk
        roof_id = ParoiModel.objects.get(name='Toiture non isolée (avant 1975)').pk
        self.assertTrue(all(t['paroi_model_id'] == wall_id for t in tris if t['group'].startswith('mur')))
        self.assertTrue(all(t['paroi_model_id'] == roof_id and t['alpha_ext'] == 0.8
                            for t in tris if t['group'].startswith('toiture')))
        self.assertEqual({o['status'] for o in env.scene_objects}, {'studied'})
        self.assertIn('1965', report['basis'])
        # Un PATCH qui ne renvoie que les sommets reconstruit les triangles depuis
        # l'enveloppe existante : alpha_ext ne doit pas s'y perdre (piège du Lot K).
        s = serializers.BuildingSerializer(building, data={'vertices': building.envelope['vertices']}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        building.refresh_from_db()
        self.assertTrue(any(t.get('alpha_ext') == 0.8 for t in building.envelope['triangles']))


class CosiaEssenceTest(SimpleTestCase):
    databases = []

    def test_decode_palette_and_essence(self):
        from .. import observed_env as oe
        img = np.zeros((10, 10, 4), dtype=np.uint8)
        img[..., 3] = 255
        img[:, :5, :3] = oe.COSIA_PALETTE['conifère']
        img[:, 5:, :3] = (77, 144, 40)                       # feuillu, à l'antialiasing près
        c = oe.CosiaImage(img, (0.0, 0.0, 10.0, 10.0))
        self.assertEqual(oe.COSIA_CLASSES[c.classes_at(2.0, 5.0)], 'conifère')
        self.assertEqual(oe.COSIA_CLASSES[c.classes_at(8.0, 5.0)], 'feuillu')

        class IdFrame:
            def to_l93(self, x, y):
                return np.asarray(x), np.asarray(y)
        self.assertEqual(oe._essence(c, IdFrame(), [1, 2, 3], [5, 5, 5]), 'conifère')
        self.assertEqual(oe._essence(c, IdFrame(), [6, 7, 8, 1], [5, 5, 5, 5]), 'feuillu')
