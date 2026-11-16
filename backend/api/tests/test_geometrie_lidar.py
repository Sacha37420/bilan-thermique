"""Lot 30 — géométrie croisée BD TOPO × LiDAR HD et horizon des façades par profil.

- `api.horizon` : profil d'obstruction par tranche d'azimut, mesuré depuis les façades ; un mitoyen n'est
  plus un masque, un immeuble de côté ne masque que les azimuts où il se trouve ;
- `api.croisement` : emprise recalée, hauteur à l'égout mesurée, toiture et voisins réels tirés de
  l'environnement relié ; la BD TOPO reste en repli et revient quand on détache l'environnement ;
  une correction de l'utilisateur prime toujours.
Aucun appel réseau."""

import math
from unittest import mock

import numpy as np
import shapely.geometry as sg
from django.test import SimpleTestCase, TestCase

from .. import (calc_service, croisement, facts, geometry_service, horizon, observed_env as oe, pipeline,
                reglages)
from ..engines import solar
from ..models import Building, BuildingFact, Environment
from ..testing import client_for, patch_jwks
from .test_fiche_auto import SOURCES, fake_ign

BOX = sg.box(0, 0, 10, 8)


def rows_of(prof):
    return np.asarray(prof, dtype=float)


class ProfileTest(SimpleTestCase):
    def test_open_site_has_no_mask(self):
        degs, prof = horizon.profile(BOX, [], 9.0)
        self.assertEqual(degs, {'N': 0.0, 'E': 0.0, 'S': 0.0, 'O': 0.0})
        self.assertTrue(all(rows_of(prof[o]).max() == 0 for o in 'NESO'))
        self.assertEqual(len(prof['S']), 3 * 3)                  # 10 m → 3 points × 3 hauteurs

    def test_wall_in_front_masks_by_distance_and_height(self):
        wall = (sg.box(-20, -12, 30, -10), 20.0)                  # 10 m au sud, 20 m de haut, très large
        degs, prof = horizon.profile(BOX, [wall], 9.0)
        S = rows_of(prof['S'])
        due_south = S[:, 18]                                      # tranche 180–190°
        # Point au milieu de la hauteur (4,5 m) : atan((20 − 4,5) / 9,7) ≈ 58°.
        self.assertAlmostEqual(float(due_south[1]), math.degrees(math.atan2(15.5, 9.7)), delta=1.0)
        self.assertGreater(due_south[0], due_south[2])            # le bas de la façade est plus masqué que le haut
        self.assertEqual(degs['N'], 0.0)
        self.assertGreater(degs['S'], 40.0)

    def test_adjoining_neighbour_is_not_a_mask(self):
        """Ancienne règle : voisin accolé à l'est → centre dans le cône est, distance ramenée à 1 m → 85°."""
        neighbour = (sg.box(10, 0, 20, 8), 15.0)
        degs, prof = horizon.profile(BOX, [neighbour], 6.0, adjacency=[neighbour[0]])
        self.assertEqual(prof['E'], [])                           # mur mitoyen : aucun point exposé
        self.assertEqual(degs['E'], 0.0)
        self.assertLess(degs['S'], 5.0)                          # alignés : il ne masque pas la façade sud
        old = pipeline.geometry_from_footprints(BOX, [neighbour], 6.0)
        self.assertGreater(old['mitoyennete'], 0.2)
        self.assertEqual(old['horizon_deg']['E'], 0.0)

    def test_side_building_masks_only_its_azimuths(self):
        tower = (sg.box(14, -30, 24, -20), 30.0)                  # au sud-est
        _degs, prof = horizon.profile(BOX, [tower], 9.0)
        S = rows_of(prof['S'])
        self.assertTrue((S[:, 18] == 0).all())                    # plein sud : dégagé
        self.assertTrue((S[:, 15] > 20).all())                    # vers 150° (direction de la tour) : masqué
        az = np.radians(np.array([150.0, 182.0]))
        el = np.radians(np.array([30.0, 30.0]))
        vis = horizon.beam_visible(prof['S'], az, el)
        self.assertLess(vis[0], 0.5)
        self.assertEqual(vis[1], 1.0)

    def test_own_wing_masks(self):
        ell = sg.Polygon([(0, 0), (20, 0), (20, 20), (10, 20), (10, 8), (0, 8)])
        _degs, prof = horizon.profile(ell, [], 12.0)
        # La façade est de la branche basse (x = 10, au-dessus de y = 8) voit l'aile… la façade ouest de l'aile.
        self.assertTrue(rows_of(prof['O']).max() > 30)

    def test_solar_uses_profile(self):
        az, el = np.radians(np.array([180.0, 90.0])), np.radians(np.array([30.0, 30.0]))
        dni, dhi = np.array([800.0, 800.0]), np.array([100.0, 100.0])
        free, beam_free = solar.surface_irradiance(az, el, dni, dhi, 180.0, 90.0)
        half = [[90] * 36, [0] * 36]                              # un point sur deux à l'ombre
        _t, beam = solar.surface_irradiance(az, el, dni, dhi, 180.0, 90.0, horizon_deg=80.0, horizon_profile=half)
        self.assertAlmostEqual(float(beam[0]), float(beam_free[0]) / 2, places=6)
        _t, beam_old = solar.surface_irradiance(az, el, dni, dhi, 180.0, 90.0, horizon_deg=40.0)
        self.assertEqual(float(beam_old[0]), 0.0)                 # résumé d'avant le lot : règle d'avant


def lidar_env(building, studied_poly, studied_h=None, roof='2pans', neighbours=(), trees=(), shift=(1.2, 0.0),
              lat=None, lon=None, source='lidar'):
    """Environnement relevé simulé : bâtiment étudié (toit relevé), voisins et arbres."""
    objs = []
    if roof == 'terrasse':
        v, t = oe.prism_solid(studied_poly, 0.0, studied_h)
    else:
        env3d, _n = geometry_service.envelope_for(studied_poly, studied_h, roof, 35.0)
        v, t = env3d['vertices'], [{'v': x['v'], 'group': x['group'], 'boundary': x['boundary']} for x in env3d['triangles']]
    objs.append(oe.make_object(1, 'building', 'bdtopo+lidar', 'Étudié', v, t, studied_poly,
                               info={'base_z': 0.0, 'roof': 'relevé LiDAR', 'shift_m': list(shift), 'lidar_cover': 0.97,
                                     'hauteur_bdtopo': 4.0},
                               status=oe.STATUS_STUDIED))
    k = 2
    for poly, h in neighbours:
        v, t = oe.prism_solid(poly, 0.0, h)
        objs.append(oe.make_object(k, 'building', 'bdtopo+lidar', f'V{k}', v, t, poly, info={'base_z': 0.0}))
        k += 1
    for poly, h in trees:
        v, t = oe.prism_solid(poly, 2.0, h)
        objs.append(oe.make_object(k, 'tree', 'lidar', 'Arbre', v, t, poly, info={}))
        k += 1
    return Environment.objects.create(
        name=f'env {Environment.objects.count()}', georef_lat=lat or building.georef_lat,
        georef_lon=lon or building.georef_lon, georef_ground_z=92.0, scene_objects=objs,
        envelope=oe.compose_envelope(objs), generation={'source': source, 'warnings': []})


class MeasureTest(TestCase):
    def test_gable_roof(self):
        b = Building.objects.create(name='m', georef_lat=47.39, georef_lon=0.68)
        env = lidar_env(b, BOX, 9.0, '2pans')
        m = croisement.measure(croisement.studied(b, env))
        self.assertAlmostEqual(m['egout_m'], 9.0, places=2)
        self.assertAlmostEqual(m['faitage_m'], 9.0 + 4.0 * math.tan(math.radians(35)), places=1)
        self.assertEqual((m['toiture'], m['pente_deg']), ('2pans', 35.0))

    def test_hip_and_flat_roofs(self):
        b = Building.objects.create(name='m', georef_lat=47.39, georef_lon=0.68)
        m4 = croisement.measure(croisement.studied(b, lidar_env(b, sg.box(0, 0, 16, 10), 7.0, '4pans')))
        self.assertEqual(m4['toiture'], '4pans')
        mt = croisement.measure(croisement.studied(b, lidar_env(b, BOX, 12.0, 'terrasse')))
        self.assertEqual((mt['toiture'], mt['pente_deg'], mt['egout_m']), ('terrasse', 0.0, 12.0))


class CrossTest(TestCase):
    """Bâtiment initialisé par la chaîne (BD TOPO : 4 m de haut, emprise décalée), puis environnement relié."""

    def setUp(self):
        self.b = Building.objects.create(name='c', georef_lat=47.39, georef_lon=0.68, rnb_id='ABCD1234EFGH')
        self._chain()

    def _chain(self):
        patches = [mock.patch(k, return_value=v) for k, v in SOURCES.items()]
        patches.append(mock.patch('api.geodata.fetch_ign_buildings', side_effect=fake_ign))
        patches.append(mock.patch('api.environment_service.auto_mode', return_value='manuel'))
        for p in patches:
            p.start()
        try:
            pipeline.initialize(self.b)
        finally:
            for p in patches:
                p.stop()
        self.b.refresh_from_db()

    def _link(self, env):
        self.b.environment = env
        self.b.save(update_fields=['environment'])
        return croisement.apply(self.b)

    def _studied_poly(self):
        return sg.Polygon(self.b.pipeline['footprint_xy'])

    def test_lidar_geometry_replaces_bdtopo(self):
        bd = facts.effective(self.b)
        self.assertEqual(bd['identite.hauteur_m'].source, 'BD TOPO (IGN)')
        self.assertEqual(self.b.pipeline['geometrie'], {'source': 'bdtopo'})
        poly = shapely_translate(self._studied_poly(), 1.2, 0.0)  # emprise recalée de 1,2 m
        step = self._link(lidar_env(self.b, poly, 12.5, '2pans', shift=(1.2, 0.0)))
        self.assertTrue(step['ok'], step)
        self.b.refresh_from_db()
        eff = facts.effective(self.b)
        self.assertEqual(eff['identite.hauteur_m'].value, 12.5)
        self.assertEqual(eff['identite.hauteur_m'].source, f'{croisement.SOURCE}, hauteur à l’égout mesurée')
        self.assertEqual(eff['toiture.type'].value, '2pans')
        self.assertEqual(eff['toiture.pente_deg'].value, 35.0)
        g = self.b.pipeline['geometrie']
        self.assertEqual((g['source'], g['decalage_m'], g['hauteur_egout_m']), ('lidar', 1.2, 12.5))
        self.assertEqual(g['bdtopo']['hauteur_m'], bd['identite.hauteur_m'].value)
        fp = sg.Polygon(self.b.pipeline['footprint_xy'])
        self.assertAlmostEqual(fp.centroid.x, poly.centroid.x, delta=0.05)
        self.assertEqual(self.b.pipeline['geometry']['hauteur_m'], 12.5)
        self.assertIn('horizon_profil', self.b.pipeline['geometry'])
        self.assertIn('bdtopo', self.b.pipeline)
        # N0/N1 : la géométrie effective est la géométrie croisée.
        self.assertEqual(geometry_service.effective_summary(self.b)['hauteur_m'], 12.5)

    def test_neighbours_and_trees_from_environment(self):
        poly = self._studied_poly()
        tower = (sg.box(poly.bounds[0] - 10, poly.bounds[1] - 20, poly.bounds[2] + 10, poly.bounds[1] - 12), 25.0)
        tree = (sg.box(poly.bounds[2] + 3, poly.bounds[3] + 3, poly.bounds[2] + 7, poly.bounds[3] + 7), 14.0)
        step = self._link(lidar_env(self.b, poly, 7.0, 'terrasse', neighbours=[tower], trees=[tree]))
        self.assertTrue(step['ok'], step)
        self.b.refresh_from_db()
        h = self.b.pipeline['geometry']['horizon_deg']
        self.assertGreater(h['S'], 30.0)                          # l'immeuble relevé au sud
        prof = self.b.pipeline['geometry']['horizon_profil']
        self.assertGreater(max(max(r[4] for r in prof['N']), max(r[4] for r in prof['E'])), 0)  # l'arbre au NE (45°)
        self.assertEqual(self.b.pipeline['neighbours'][0]['h'], 25.0)

    def test_user_correction_wins(self):
        facts.record(self.b, 'identite.hauteur_m', 8.0, 'declare', source='visite', author_sub='u1')
        self._link(lidar_env(self.b, self._studied_poly(), 12.5, '2pans'))
        self.assertEqual(facts.effective_values(self.b)['identite.hauteur_m'], 8.0)

    def test_unlink_restores_bdtopo(self):
        before = facts.effective_values(self.b)['identite.hauteur_m']
        self._link(lidar_env(self.b, self._studied_poly(), 12.5, '2pans'))
        self.b.refresh_from_db()
        self.b.environment = None
        self.b.save(update_fields=['environment'])
        self.assertTrue(croisement.restore(self.b))
        self.b.refresh_from_db()
        eff = facts.effective(self.b)
        self.assertEqual(eff['identite.hauteur_m'].value, before)
        self.assertEqual(eff['identite.hauteur_m'].source, 'BD TOPO (IGN)')
        self.assertNotEqual(getattr(eff.get('toiture.type'), 'source', ''), f'{croisement.SOURCE}, pans relevés')
        self.assertEqual(self.b.pipeline['geometrie']['source'], 'bdtopo')

    def test_rerun_of_chain_keeps_crossed_geometry(self):
        self._link(lidar_env(self.b, self._studied_poly(), 12.5, '2pans'))
        n = BuildingFact.objects.filter(building=self.b).count()
        self._chain()
        self.assertEqual(facts.effective_values(self.b)['identite.hauteur_m'], 12.5)
        self.assertEqual(self.b.pipeline['geometrie']['source'], 'lidar')
        self.assertEqual(self.b.pipeline['steps']['A3']['geometrie'], 'lidar')
        n2 = BuildingFact.objects.filter(building=self.b).count()
        self._chain()                                             # rejouée encore : aucun fait en plus
        self.assertEqual(BuildingFact.objects.filter(building=self.b).count(), n2)
        self.assertLessEqual(n2 - n, 3)
        self.assertEqual(self.b.pipeline['bdtopo']['geometry']['hauteur_m'], self.b.pipeline['geometrie']['bdtopo']['hauteur_m'])

    def test_envelope_replaced_only_if_automatic(self):
        lod1 = self.b.envelope
        self.assertTrue(lod1.get('triangles'))
        self._link(lidar_env(self.b, self._studied_poly(), 12.5, '2pans'))
        self.b.refresh_from_db()
        zs = [v[2] for v in self.b.envelope['vertices']]
        self.assertGreater(max(zs), 12.5)                         # enveloppe relevée (faîtage)
        # Une paroi choisie à la main : l'enveloppe n'est plus touchée.
        env = dict(self.b.envelope)
        env['triangles'] = [dict(t) for t in env['triangles']]
        env['triangles'][0]['paroi_model_id'] = 1
        self.b.envelope = env
        self.b.save(update_fields=['envelope'])
        self._link(lidar_env(self.b, self._studied_poly(), 20.0, 'terrasse'))
        self.b.refresh_from_db()
        self.assertEqual(self.b.envelope['triangles'][0]['paroi_model_id'], 1)

    def test_other_frame_or_no_lidar_is_not_crossed(self):
        far = lidar_env(self.b, self._studied_poly(), 12.5, lat=47.40)
        self.assertEqual(self._link(far), {'ok': False, 'error': 'L’environnement relié n’est pas dans le repère du bâtiment.'})
        legacy = lidar_env(self.b, self._studied_poly(), 12.5, source='bdtopo')
        self.assertFalse(self._link(legacy)['ok'])
        self.assertEqual(facts.effective(self.b)['identite.hauteur_m'].source, 'BD TOPO (IGN)')

    def test_studied_found_by_footprint_when_not_marked(self):
        env = lidar_env(self.b, self._studied_poly(), 12.5)
        objs = env.scene_objects
        objs[0]['status'] = oe.STATUS_ACTIVE
        env.scene_objects = objs
        env.save()
        self.assertTrue(self._link(env)['ok'])
        self.b.refresh_from_db()
        self.assertEqual(self.b.pipeline['geometrie']['objets'], [1])
        # Il n'est pas compté comme son propre obstacle.
        self.assertLess(max(self.b.pipeline['geometry']['horizon_deg'].values()), 1.0)


def shapely_translate(p, dx, dy):
    import shapely.affinity
    return shapely.affinity.translate(p, dx, dy)


@patch_jwks()
class LinkViewTest(TestCase):
    def test_patch_link_and_unlink(self):
        c = client_for('gest')
        b = Building.objects.create(name='v', georef_lat=47.39, georef_lon=0.68,
                                    pipeline={'footprint_xy': [list(p) for p in BOX.exterior.coords[:-1]],
                                              'geometry': {'hauteur_m': 4.0, 'emprise_m2': 80.0, 'mitoyennete': 0.0}})
        env = lidar_env(b, BOX, 11.0)
        r = c.patch(f'/api/batiments/{b.pk}/', {'environment_id': env.pk}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        b.refresh_from_db()
        self.assertEqual(facts.effective_values(b)['identite.hauteur_m'], 11.0)
        self.assertEqual(r.json()['pipeline']['geometrie']['source'], 'lidar')
        self.assertNotIn('horizon_profil', r.json()['pipeline']['geometry'])
        g = c.get(f'/api/batiments/{b.pk}/geometrie/').json()
        self.assertEqual(g['origine']['source'], 'lidar')
        self.assertNotIn('horizon_profil', g['auto'])
        c.patch(f'/api/batiments/{b.pk}/', {'environment_id': None}, format='json')
        b.refresh_from_db()
        self.assertEqual(facts.effective_values(b)['identite.hauteur_m'], 4.0)


class RederiveTest(TestCase):
    def test_levels_and_surface_follow_measured_height(self):
        b = Building.objects.create(name='r', georef_lat=47.39, georef_lon=0.68,
                                    pipeline={'footprint_xy': [list(p) for p in BOX.exterior.coords[:-1]],
                                              'geometry': {'hauteur_m': 3.0, 'emprise_m2': 80.0, 'mitoyennete': 0.0}})
        pipeline._record_all(b, [('identite.hauteur_m', 3.0, 'infere', 0.65, 'BD TOPO (IGN)'),
                                 ('identite.emprise_m2', 80.0, 'infere', 0.65, 'BD TOPO (IGN)'),
                                 ('identite.niveaux', 1, 'infere', 0.35, croisement.DERIVED_LEVELS),
                                 ('identite.surface_m2', 64.0, 'infere', 0.35, croisement.DERIVED_SURFACE)])
        b.environment = lidar_env(b, BOX, 12.2, 'terrasse')
        b.save(update_fields=['environment'])
        self.assertTrue(croisement.apply(b)['ok'])
        v = facts.effective_values(b)
        self.assertEqual((v['identite.hauteur_m'], v['identite.niveaux'], v['identite.surface_m2']), (12.2, 4, 256.0))


class IncoherentLevelsTest(TestCase):
    def test_source_levels_replaced_when_storey_height_is_absurd(self):
        b = Building.objects.create(name='hdv', georef_lat=47.39, georef_lon=0.68,
                                    pipeline={'footprint_xy': [list(p) for p in BOX.exterior.coords[:-1]],
                                              'geometry': {'hauteur_m': 10.0, 'emprise_m2': 80.0, 'mitoyennete': 0.0}})
        pipeline._record_all(b, [('identite.niveaux', 1, 'infere', 0.5, 'BDNB bdnb-bg-x'),
                                 ('identite.surface_m2', 64.0, 'infere', 0.35, croisement.DERIVED_SURFACE)])
        b.environment = lidar_env(b, BOX, 14.8, 'terrasse')
        b.save(update_fields=['environment'])
        croisement.apply(b)
        eff = facts.effective(b)
        self.assertEqual((eff['identite.niveaux'].value, eff['identite.niveaux'].source), (5, croisement.INCOHERENT_LEVELS))
        self.assertEqual(eff['identite.surface_m2'].value, 320.0)          # 80 × 5 × 0,8

    def test_coherent_source_levels_kept(self):
        b = Building.objects.create(name='ok', georef_lat=47.39, georef_lon=0.68,
                                    pipeline={'footprint_xy': [list(p) for p in BOX.exterior.coords[:-1]]})
        pipeline._record_all(b, [('identite.niveaux', 4, 'infere', 0.5, 'BDNB bdnb-bg-x')])
        b.environment = lidar_env(b, BOX, 12.6, 'terrasse')
        b.save(update_fields=['environment'])
        croisement.apply(b)
        self.assertEqual(facts.effective(b)['identite.niveaux'].source, 'BDNB bdnb-bg-x')


class DefaultModeTest(TestCase):
    def test_chain_mode_is_the_default_everywhere(self):
        from .. import environment_service
        self.assertEqual(reglages.defaults('orchestration')['environnement.mode'], 'chaine')
        hors = Building.objects.create(name='h', georef_lat=47.39, georef_lon=0.68)
        self.assertEqual(environment_service.auto_mode(hors), 'chaine')
        self.assertTrue(environment_service.wanted(hors, 'chaine'))

    def test_engine_versions_bumped(self):
        self.assertEqual((calc_service.ENGINE_VERSION['N0'], calc_service.ENGINE_VERSION['N1']), ('n0-3', 'n1-3'))
