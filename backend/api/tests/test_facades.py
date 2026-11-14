"""Tests — facades.

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


class FacadeGeometryTest(SimpleTestCase):
    databases = []

    def test_planes_face_outward_with_right_handed_axes(self):
        from .. import facades as fc
        v, t = _box_building()
        planes = fc.facade_planes(v, t)
        self.assertEqual(len(planes), 4)
        for pl in planes.values():
            n, u = np.array(pl['n']), np.array(pl['u'])
            # u = « vers la droite » vu de l'extérieur : n × z = −u.
            self.assertTrue(np.allclose(np.cross(n, [0, 0, 1]), -u, atol=1e-9))
            self.assertAlmostEqual(pl['height'], 6.0, places=6)

    def test_projection_center_and_horizon(self):
        """Un point droit devant la caméra (au cap de l'image), à hauteur
        d'appareil, tombe au centre de la photo équirectangulaire ; un point à
        l'est d'une caméra regardant le nord tombe au 3/4 de la largeur."""
        from .. import facades as fc
        px, py = fc.project_to_pano([[0.0, 10.0, 2.0]], (0.0, 0.0, 2.0), 0.0, 0.0, 0.0, 4000, 2000)
        self.assertAlmostEqual(float(px[0]), 2000.0, places=3)
        self.assertAlmostEqual(float(py[0]), 1000.0, places=3)
        px, py = fc.project_to_pano([[10.0, 0.0, 2.0]], (0.0, 0.0, 2.0), 0.0, 0.0, 0.0, 4000, 2000)
        self.assertAlmostEqual(float(px[0]), 3000.0, places=3)
        # Cap du bâtiment tourné de 90° : son +Y local regarde l'est vrai.
        px, py = fc.project_to_pano([[0.0, 10.0, 2.0]], (0.0, 0.0, 2.0), 90.0, 0.0, 90.0, 4000, 2000)
        self.assertAlmostEqual(float(px[0]), 2000.0, places=3)

    def test_rectify_recovers_synthetic_facade(self):
        """Panorama synthétique : moitié gauche de la façade rouge, droite bleue.
        La texture redressée doit reproduire le partage au bon endroit."""
        from .. import facades as fc
        v, t = _box_building()
        planes = fc.facade_planes(v, t)
        pl = next(p for p in planes.values() if np.allclose(p['n'], [0, -1, 0]))   # façade sud (y = 0)
        W, H = 4096, 2048
        pano = np.zeros((H, W, 3), dtype=np.uint8)
        cam = (5.0, -12.0, 2.0)
        yy, xx = np.mgrid[0:H, 0:W]
        yaw = (xx + 0.5) / W * 2 * math.pi - math.pi          # cap 0 : centre = nord
        pitch = math.pi / 2 - (yy + 0.5) / H * math.pi
        # intersection du rayon avec le plan y = 0
        dy = np.cos(yaw) * np.cos(pitch)
        tpar = np.where(dy > 1e-6, 12.0 / np.maximum(dy, 1e-6), np.inf)
        hit_x = cam[0] + tpar * np.sin(yaw) * np.cos(pitch)
        pano[(hit_x < 5.0) & np.isfinite(tpar)] = (255, 0, 0)
        pano[(hit_x >= 5.0) & np.isfinite(tpar)] = (0, 0, 255)
        tex, scale = fc.rectify(pl, pano, cam, 0.0, 0.0, 0.0, m_per_px=0.05)
        mid = tex.shape[0] // 2
        # u vers la droite vu de l'extérieur (sud) = +x : s = x.
        self.assertGreater(tex[mid, int(2.5 / scale), 0], 200)
        self.assertGreater(tex[mid, int(7.5 / scale), 2], 200)

    def test_insert_openings_keeps_solid_closed(self):
        import trimesh
        from .. import facades as fc
        v, t = _box_building()
        planes = fc.facade_planes(v, t)
        g, pl = next((g, p) for g, p in planes.items() if np.allclose(p['n'], [0, -1, 0]))
        openings = [{'label': 'window', 's0': 1.0, 't0': 1.0, 's1': 2.2, 't1': 2.4},
                    {'label': 'window', 's0': 4.0, 't0': 1.0, 's1': 5.2, 't1': 2.4},
                    {'label': 'door', 's0': 7.0, 't0': 0.0, 's1': 8.0, 't1': 2.1}]   # porte : rognée au bas du mur
        nv, nt, n = fc.insert_openings(v, t, g, pl, openings, glazing_model_id=99)
        self.assertEqual(n, 3)
        m = trimesh.Trimesh(nv, [x['v'] for x in nt], process=False)
        self.assertTrue(m.is_watertight and m.is_winding_consistent)
        self.assertAlmostEqual(m.volume, 10 * 8 * 6, places=3)
        k = g.split('_')[1]
        glass = [x for x in nt if x['group'] == f'vitrage_{k}']
        self.assertEqual(len(glass), 4)
        self.assertTrue(all(x['paroi_model_id'] == 99 for x in glass))
        area = sum(0.5 * np.linalg.norm(np.cross(np.subtract(nv[x['v'][1]], nv[x['v'][0]]),
                                                 np.subtract(nv[x['v'][2]], nv[x['v'][0]]))) for x in glass)
        self.assertAlmostEqual(area, 2 * 1.2 * 1.4, places=3)
        self.assertEqual(len([x for x in nt if x['group'] == f'porte_{k}']), 2)

    def test_apply_with_fallback_ratio(self):
        from .. import facades as fc
        v, t = _box_building(w=12.0, d=8.0, h=6.0)
        vv, tt, report = fc.apply_openings(v, t, {}, glazing_model_id=5, fallback_ratio=0.15, use_detection={})
        self.assertTrue(all(r['source'] == 'proportion' for r in report.values()))
        self.assertTrue(any(tr['group'].startswith('vitrage_') for tr in tt))
        for r in report.values():
            self.assertLess(abs(r['glazed_ratio'] - 0.15), 0.08)

    def test_nms_keeps_best(self):
        from .. import facades as fc
        keep = fc._nms([(0, 0, 10, 10), (1, 1, 10, 10), (20, 20, 30, 30)], np.array([0.5, 0.9, 0.3]))
        self.assertEqual(sorted(keep), [1, 2])


class FacadeDetectionPostprocessTest(SimpleTestCase):
    databases = []

    def test_nested_and_split_boxes_merged(self):
        from .. import facades as fc
        boxes = [(10, 10, 100, 120), (30, 30, 60, 60), (40, 40, 50, 50),     # emboîtés
                 (200, 10, 240, 120), (243, 10, 285, 118),                    # deux vantaux
                 (400, 10, 440, 150)]                                         # porte
        b, sc, lab = fc._merge_boxes(boxes, [0.5, 0.3, 0.2, 0.6, 0.55, 0.7],
                                     ['window'] * 5 + ['door'], gap_px=5)
        self.assertEqual(len(b), 3)
        self.assertIn((10, 10, 100, 120), b)
        self.assertIn((200, 10, 285, 120), b)
        self.assertEqual(lab[b.index((400, 10, 440, 150))], 'door')

    def test_visibility_blocked_by_building(self):
        import shapely.geometry as sg
        from .. import facades as fc
        from .. import observed_env as oe
        v, t = _box_building()
        planes = fc.facade_planes(v, t)
        pl = next(p for p in planes.values() if np.allclose(p['n'], [0, -1, 0]))   # façade sud
        pl['outline'] = pl['outline']
        own = oe.make_object(1, 'building', 'x', 'x', v, t, sg.box(0, 0, 10, 8))
        occ = fc._occluder([own])
        self.assertGreater(fc.visible_fraction(pl, (5.0, -15.0, 2.0), occ), 0.95)
        wv, wt = oe.prism_solid(sg.box(-5, -8, 15, -6), 0.0, 10.0)            # immeuble devant
        wall = oe.make_object(2, 'building', 'x', 'x', wv, wt, sg.box(-5, -8, 15, -6))
        occ2 = fc._occluder([own, wall])
        self.assertLess(fc.visible_fraction(pl, (5.0, -15.0, 2.0), occ2), 0.1)
        # Caméra derrière le bâtiment : la façade sud est cachée par le bâtiment lui-même.
        self.assertLess(fc.visible_fraction(pl, (5.0, 20.0, 2.0), occ), 0.1)


class FacadeApplyDefaultsTest(SimpleTestCase):
    databases = []

    def test_seen_blind_facade_stays_blind(self):
        from .. import facades as fc
        v, t = _box_building(w=12.0, d=8.0, h=6.0)
        planes = fc.facade_planes(v, t)
        seen = {g: {'status': 'analysee', 'openings': []} for g in planes}
        vv, tt, report = fc.apply_openings(v, t, seen, glazing_model_id=5, fallback_ratio=0.2, use_detection={})
        self.assertFalse(any(x['group'].startswith('vitrage_') for x in tt))
        self.assertTrue(all(r['source'] == 'détection' for r in report.values()))


class FacadeEnvironmentTextureTest(SimpleTestCase):
    """Lot AL — façades traitées dans l'ensemble de leur environnement."""
    databases = []

    def _occupancy(self, exclude=None):
        from .. import facade_texture as ft
        # Sol plat à z = 0 sur 40 × 40 m ; une haie de 1,8 m (points sur son
        # dessus seulement, comme vus d'avion) le long de y = -3, de x = 0 à 10.
        gx, gy = np.meshgrid(np.arange(-20, 20, 0.5), np.arange(-20, 20, 0.5))
        hx, hy = np.meshgrid(np.arange(0, 10, 0.2), np.arange(-3.3, -2.7, 0.2))
        x = np.concatenate([gx.ravel(), hx.ravel()])
        y = np.concatenate([gy.ravel(), hy.ravel()])
        z = np.concatenate([np.zeros(gx.size), np.full(hx.size, 1.8)])
        cls = np.concatenate([np.full(gx.size, 2), np.full(hx.size, 5)])
        return ft.LidarOccupancy(x, y, z, cls, (-20, -20, 20, 20), exclude=exclude)

    def test_low_hedge_seen_from_the_air_blocks_rays_through_its_body(self):
        occ = self._occupancy()
        cam = [[5.0, -10.0, 2.2]] * 2
        pts = [[5.0, 0.0, 0.8],      # pied du mur, derrière la haie
               [5.0, 0.0, 5.0]]      # étage, au-dessus de la haie
        blocked = occ.blocked(cam, pts)
        self.assertTrue(blocked[0])
        self.assertFalse(blocked[1])

    def test_excluded_polygon_ignored(self):
        import shapely.geometry as sg
        occ = self._occupancy(exclude=sg.box(-1, -4, 11, -2))
        self.assertFalse(occ.blocked([[5.0, -10.0, 2.2]], [[5.0, 0.0, 0.8]])[0])

    def test_grid_completed_only_in_unseen_part(self):
        from .. import facade_texture as ft
        from .. import facades as fc
        v, t = _box_building(w=12.0, d=8.0, h=9.0)
        pl = next(p for p in fc.facade_planes(v, t).values() if np.allclose(p['n'], [0, -1, 0]))
        # Deux étages vus (fenêtres à 3,5 m et 6,3 m, deux travées), le bas
        # (< 3 m) caché par une haie ; la travée de droite de l'étage du haut
        # est vue et SANS fenêtre (mur aveugle) — elle doit le rester.
        win = lambda s0, t0: {'label': 'window', 'score': 0.5, 's0': s0, 't0': t0, 's1': s0 + 1.2, 't1': t0 + 1.4}
        found = [win(2.0, 3.5), win(8.0, 3.5), win(2.0, 6.3)]
        rows = []
        for j in range(18):                       # cellules de 0,5 m, ligne 0 = bas
            rows.append(('0' if j * 0.5 < 3.0 else '1') * 24)
        seen = {'cell_m': 0.5, 'rows': rows}
        openings, grid = ft.regularize_openings(found, pl, seen)
        added = [o for o in openings if o.get('source') == 'grille']
        self.assertIsNotNone(grid)
        self.assertEqual(len(added), 2)                               # rez caché, deux travées
        self.assertTrue(all(o['t1'] <= 3.0 + 1e-6 for o in added))
        self.assertFalse(any(o['s0'] > 7 and o['t0'] > 6 for o in openings))   # aveugle vu

    def test_equalize_ignores_unseen(self):
        from .. import facade_texture as ft
        tex = np.full((10, 10, 3), 200, dtype=np.uint8)
        tex[:, :5] = 210
        seen = np.zeros((10, 10), dtype=bool)
        seen[:, :8] = True
        eq = ft.equalize_seen(tex, seen)
        self.assertTrue((eq[:, 8:] == 128).all())
        self.assertLess(int(eq[0, 7, 0]), int(eq[0, 0, 0]))           # contraste étiré

    def test_door_rules(self):
        from .. import facades as fc
        m = 0.02
        h = 500      # 10 m
        px = lambda s0, t0, s1, t1: (s0 / m, h - t1 / m, s1 / m, h - t0 / m)
        cands = [(px(1.0, 4.0, 2.0, 6.0), 0.5, 'door'),               # en étage, dessous vu → fenêtre
                 (px(4.0, 0.0, 6.2, 2.2), 0.5, 'door'),               # 2,2 m de large → baie vitrée
                 (px(8.0, 0.0, 9.0, 2.2), 0.5, 'door'),               # vraie porte
                 (px(10.0, 1.3, 11.0, 3.3), 0.5, 'door')]             # bas caché par une clôture
        from .. import facade_texture as ft
        # Cellules de 0,5 m : x ≥ 10 m et t < 1,5 m cachés (clôture).
        rows = [r[:20] + ('0000' if j < 3 else '1111') for j, r in enumerate(['1' * 24] * 20)]
        out = ft.fix_door_labels(fc._postprocess(cands, h, 600, m, None), {'cell_m': 0.5, 'rows': rows})
        labels = sorted((o['s0'], o['label']) for o in out)
        self.assertEqual([lab for _s, lab in labels], ['window', 'window', 'door', 'door'])


class GlazingReferenceTest(SimpleTestCase):
    databases = []

    def test_cardinal(self):
        from .. import bdnb
        self.assertEqual([bdnb.cardinal_of(a) for a in (0, 44, 46, 180, 269, 316)], ['N', 'N', 'E', 'S', 'O', 'N'])

    def test_house_uses_surfaces_per_orientation(self):
        from .. import bdnb
        dpe = [{'type_batiment_dpe': 'maison', 'surface_vitree_nord': 4.0, 'surface_vitree_sud': 10.0,
                'surface_vitree_est': None, 'surface_vitree_ouest': 2.0, 'date_etablissement_dpe': '2024-01-01',
                'pourcentage_surface_baie_vitree_exterieur': 0.15}]
        info = bdnb.glazing_ratios(dpe, [{'nb_log': 1}], {'N': 40.0, 'S': 40.0, 'E': 20.0, 'O': 20.0})
        self.assertEqual(info['mode'], 'orientation')
        self.assertAlmostEqual(info['ratios']['S'], 0.25)
        self.assertAlmostEqual(info['ratios']['E'], 0.0)

    def test_apartment_uses_global_ratio_only(self):
        from .. import bdnb
        dpe = [{'type_batiment_dpe': 'appartement', 'surface_vitree_est': 9.0,
                'pourcentage_surface_baie_vitree_exterieur': 0.23}]
        info = bdnb.glazing_ratios(dpe, [{'nb_log': 26}], {'E': 100.0, 'O': 100.0})
        self.assertEqual(info['mode'], 'global')
        self.assertEqual(set(info['ratios'].values()), {0.23})

    def test_apply_uses_reference_for_unseen_and_fills_partial(self):
        from .. import facades as fc
        v, t = _box_building(w=12.0, d=8.0, h=6.0)
        planes = fc.facade_planes(v, t)
        south = next(g for g, p in planes.items() if np.allclose(p['n'], [0, -1, 0]))
        facades = {}
        for g, p in planes.items():
            facades[g] = {'status': 'sans_photo', 'openings': [], 'azimuth': fc.facade_azimuth(p, 0.0)}
        # Façade sud en partie vue : moitié haute vue, sans baie ; moitié basse cachée.
        rows = [('0' if j < 6 else '1') * 24 for j in range(12)]
        facades[south] = {'status': 'partielle', 'openings': [], 'grid': None, 'azimuth': 180.0,
                          'seen': {'cell_m': 0.5, 'rows': rows}}
        ref = {'ratios': {'N': 0.0, 'E': 0.3, 'S': 0.3, 'O': 0.3}}
        vv, tt, report = fc.apply_openings(v, t, facades, 5, 0.2, {}, reference=ref)
        north = next(g for g, p in planes.items() if np.allclose(p['n'], [0, 1, 0]))
        self.assertEqual(report[north]['n_openings'], 0)               # DPE : aucune baie au nord
        self.assertEqual(report[north]['source'], 'DPE')
        self.assertGreater(report[south]['n_openings'], 0)             # complété dans la partie cachée
        self.assertIn('non vu', report[south]['source'])
        V = np.asarray(vv)
        for x in tt:
            if x['group'] == 'vitrage_' + south.split('_')[1]:
                self.assertLessEqual(V[x['v']][:, 2].max(), 3.0 + 1e-6)


class SfmAlignmentTest(SimpleTestCase):
    """Lot AM — calage d'un modèle photogrammétrique dans le repère local."""
    databases = []

    @staticmethod
    def _rot(axis, deg):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        if axis == 'x':
            return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        if axis == 'y':
            return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    def test_recovers_positions_and_orientations_despite_gps_noise(self):
        from .. import facade_sfm as fs
        rng = np.random.default_rng(1)
        # Vraies poses : trajet en L, appareil à 2,2 m, cap variable ; caméra
        # COLMAP : x droite, y bas, z avant → monde ← caméra.
        true_C, true_R = [], []
        for k in range(20):
            x, y = (k * 1.5, 0.0) if k < 12 else (16.5, (k - 11) * 1.5)
            heading = 90.0 if k < 12 else 0.0
            Rwc = self._rot('z', -heading) @ np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
            true_C.append([x, y, 2.2])
            true_R.append(Rwc)
        true_C, true_R = np.array(true_C), np.array(true_R)
        # Repère arbitraire de la reconstruction : échelle 0,37, rotation quelconque.
        Q = self._rot('x', 25) @ self._rot('z', 70) @ self._rot('y', -40)
        sfm_C = 0.37 * (true_C @ Q.T) + np.array([3.0, -8.0, 1.0])
        sfm_R = np.array([Q @ R for R in true_R])
        gps = true_C[:, :2] + rng.normal(0, 1.5, (20, 2))
        gps[5] += [15.0, -9.0]                                        # une position aberrante
        Cw, Rw, res, stats = fs.align_poses(sfm_C, sfm_R, gps, ground_at=lambda x, y: 0.0)
        self.assertLess(np.abs(Cw - true_C).max(), 1.2)                # l'erreur GPS se moyenne
        ang = [math.degrees(math.acos(np.clip((np.trace(a.T @ b) - 1) / 2, -1, 1))) for a, b in zip(Rw, true_R)]
        self.assertLess(max(ang), 3.0)
        self.assertGreaterEqual(stats['n_inliers'], 17)
        self.assertGreater(res[5], 8.0)                                # l'aberrante est écartée

    def test_projection_matches_colmap_convention(self):
        from .. import facade_sfm as fs
        R = self._rot('z', 0) @ np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)  # regarde vers +y (nord)
        # Point droit devant, à hauteur d'appareil → centre de l'image.
        px, py = fs.project([[0.0, 10.0, 0.0]], [0.0, 0.0, 0.0], R, 4000, 2000)
        self.assertAlmostEqual(px[0], 2000.0, places=6)
        self.assertAlmostEqual(py[0], 1000.0, places=6)
        # Point à droite (est) → quart droit ; point plus haut → au-dessus du centre.
        px, py = fs.project([[10.0, 0.0, 0.0], [0.0, 10.0, 10.0]], [0, 0, 0], R, 4000, 2000)
        self.assertAlmostEqual(px[0], 3000.0, places=6)
        self.assertLess(py[1], 1000.0)
