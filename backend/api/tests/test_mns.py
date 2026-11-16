"""Lot 31 — repli sur un modèle numérique de surface (MNS) quand le nuage LiDAR HD manque : la BD TOPO ne sert
jamais seule. Classification sans la BD TOPO, ordre des sources (MNS LiDAR HD, puis MNS Correl), chaîne complète
de l'environnement sur un MNS synthétique, géométrie croisée qui en sort. Aucun appel réseau."""

from unittest import mock

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import croisement, environment_service, facts, geodata, lidar_source, mns_source, observed_env as oe
from ..lidar_source import CLASS_BUILDING, CLASS_GROUND, CLASS_VEG_HIGH
from ..models import Building
from ..testing import without_auto_environment

LAT, LON = 47.3941, 0.6848


def scene(bbox, cell=0.5, ground=50.0, building=((0.0, 0.0, 10.0, 8.0), 9.0), tree=((20.0, 20.0), 4.0, 12.0),
          frame=None, shift=(0.0, 0.0)):
    """(MNS, MNT) synthétiques sur l'emprise : sol plat, un bloc à toit plat, un arbre rugueux."""
    xmin, ymin, xmax, ymax = bbox
    w, h = int(round((xmax - xmin) / cell)), int(round((ymax - ymin) / cell))
    ce = xmin + (np.arange(w) + 0.5) * cell
    cn = ymax - (np.arange(h) + 0.5) * cell
    E, N = np.meshgrid(ce, cn)
    x, y = frame.to_local(E, N) if frame is not None else (E - E.mean(), N - N.mean())
    mnt = np.full((h, w), ground)
    mns = mnt.copy()
    (x0, y0, x1, y1), hb = building
    inb = (x >= x0 + shift[0]) & (x <= x1 + shift[0]) & (y >= y0 + shift[1]) & (y <= y1 + shift[1])
    mns[inb] = ground + hb
    (cx, cy), r, ht = tree
    d = np.hypot(x - cx, y - cy)
    rng = np.random.default_rng(0)
    crown = d < r
    mns[crown] = ground + ht * (1 - (d[crown] / r) ** 2) + rng.normal(0, 0.8, crown.sum())
    return mns, mnt


class ClassifyTest(SimpleTestCase):
    def test_roof_tree_and_ground_are_told_apart(self):
        mns, mnt = scene((0, 0, 60, 60))
        cls = mns_source.classify(mns, mnt)
        h = mns - mnt
        roof = h > 8.9
        self.assertGreater((cls[roof] == CLASS_BUILDING).mean(), 0.8)
        crown = (h > 3) & ~roof
        self.assertGreater((cls[crown] == CLASS_VEG_HIGH).mean(), 0.7)
        self.assertTrue((cls[h < 0.1] == CLASS_GROUND).all())

    def test_ndvi_marks_a_smooth_canopy_as_vegetation(self):
        mns, mnt = scene((0, 0, 60, 60))
        ndvi = np.where((mns - mnt) > 2.0, 0.6, 0.05)       # le « bloc » est en fait une haie taillée (et l'arbre, un arbre)
        cls = mns_source.classify(mns, mnt, ndvi)
        self.assertFalse((cls[(mns - mnt) > 8.9] == CLASS_BUILDING).any())
        self.assertFalse((cls == CLASS_BUILDING).any())


class SourceOrderTest(SimpleTestCase):
    def test_lidar_mns_first_then_correl(self):
        calls = []

        def fetch(layer, bbox, cell=0.5):
            calls.append(layer)
            mns, mnt = scene(bbox)
            if 'LIDAR' in layer:
                return np.full(mns.shape, np.nan)          # zone non publiée
            return mns if layer.endswith('MNS') else mnt
        pts, desc = mns_source.synthetic_points((0, 0, 60, 60), fetch=fetch, ndvi=lambda b, s: None)
        self.assertEqual(desc['source'], 'mns_correl')
        self.assertEqual(calls[0], mns_source.SOURCES[0][1])
        self.assertEqual(len(pts), 4)
        self.assertEqual(len(pts[0]), 120 * 120)

    def test_nothing_available(self):
        def fetch(layer, bbox, cell=0.5):
            raise geodata.GeodataError('Modèle numérique de surface injoignable (erreur ConnectionError).')
        with self.assertRaises(lidar_source.LidarUnavailable):
            mns_source.synthetic_points((0, 0, 60, 60), fetch=fetch, ndvi=lambda b, s: None)


class GenerateFromMnsTest(TestCase):
    """Génération d'environnement sans dalle LiDAR : la chaîne observée tourne sur le MNS, la géométrie croisée en sort."""

    def test_environment_and_crossed_geometry_from_mns(self):
        b = Building.objects.create(name='m', georef_lat=LAT, georef_lon=LON, georef_ground_z=50.0,
                                    pipeline={'footprint_xy': [[0, 0], [10, 0], [10, 8], [0, 8]],
                                              'geometry': {'hauteur_m': 3.0, 'emprise_m2': 80.0, 'mitoyennete': 0.0}})
        frame = oe.LocalFrame(LAT, LON, 0.0, 50.0)

        def fetch(layer, bbox, cell=0.5):
            mns, mnt = scene(bbox, frame=frame, shift=(1.5, 0.0))       # le vrai bâtiment est 1,5 m plus à l'est
            if 'LIDAR' in layer:
                return np.full(mns.shape, np.nan)
            return mns if layer.endswith('MNS') else mnt
        rings = [[frame.to_l93(x, y) for x, y in [(0, 0), (10, 0), (10, 8), (0, 8), (0, 0)]]]
        bdtopo = [{'id': 'BATIMENT1', 'rings': [[(float(e), float(n)) for e, n in rings[0]]], 'hauteur': 3.0,
                   'etages': None, 'z_sol': 50.0, 'z_toit_max': None, 'nature': 'Indifférenciée', 'usage': 'Résidentiel'}]
        down = geodata.GeodataError('hors ligne')
        with mock.patch('api.lidar_source.fetch_lidar_tiles', return_value=[]), \
                mock.patch('api.mns_source.fetch_grid', side_effect=fetch), \
                mock.patch('api.mns_source._ndvi_grid', return_value=None), \
                mock.patch('api.lidar_source.fetch_bdtopo_buildings_l93', return_value=bdtopo), \
                mock.patch('api.lidar_source.fetch_cosia', side_effect=down), \
                mock.patch('api.lidar_source.fetch_orthophoto', side_effect=down):
            env, summary = environment_service.generate(
                {'radius_m': 40.0, 'include_vegetation': True, 'include_terrain': True, 'name': 'mns'}, building=b,
                self_polygon=environment_service._self_polygon(b))
        self.assertEqual(summary['source'], 'mns_correl')
        self.assertTrue(any('MNS Correl' in w for w in summary['warnings']))
        self.assertFalse(any(w.startswith('LiDAR acquis hors saison') for w in summary['warnings']))
        studied = [o for o in env.scene_objects if o['status'] == oe.STATUS_STUDIED]
        self.assertEqual(len(studied), 1)
        b.environment = env
        b.save(update_fields=['environment'])
        step = croisement.apply(b)
        self.assertTrue(step['ok'], step)
        b.refresh_from_db()
        g = b.pipeline['geometrie']
        self.assertEqual((g['source'], g['libelle']), ('mns_correl', 'MNS Correl × BD TOPO (IGN)'))
        self.assertAlmostEqual(g['hauteur_egout_m'], 9.0, delta=0.6)
        self.assertGreater(g['decalage_m'], 1.0)                        # recalé sur le MNS, pas sur la BD TOPO
        f = facts.effective(b)['identite.hauteur_m']
        self.assertEqual(f.source, 'MNS Correl × BD TOPO (IGN), hauteur à l’égout mesurée')
        self.assertAlmostEqual(f.confidence, croisement.CONFIDENCE['mns_correl'])


_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()
