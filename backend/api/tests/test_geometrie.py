"""Lot 15 (refonte) — géométrie corrigée : validation, enveloppe régénérée et fermée, surcharge réversible."""

from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import calc_service, facts, geometry_service as gs
from ..models import Building
from ..testing import box_envelope, client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


RECT = [[-5, -4], [5, -4], [5, 4], [-5, 4]]
L_SHAPE = [[0, 0], [12, 0], [12, 5], [5, 5], [5, 10], [0, 10]]


class EnvelopeTest(SimpleTestCase):
    def _poly(self, xy):
        return gs.validate(xy, 6.0, 2, 'terrasse', 0)

    def test_flat_gable_and_hip_roofs_are_closed(self):
        for roof, pitch in (('terrasse', 0), ('2pans', 35), ('4pans', 30)):
            env, note = gs.envelope_for(self._poly(RECT), 6.0, roof, pitch)
            with self.subTest(roof=roof):
                vol = gs.check_closed(env['vertices'], env['triangles'])
                self.assertGreater(vol, 80 * 6 - 1)
                self.assertIsNone(note)
                roof_tris = [t for t in env['triangles'] if t['group'] == 'toiture']
                self.assertTrue(all(t['normal'][2] > 0 for t in roof_tris))
        env, _ = gs.envelope_for(self._poly(RECT), 6.0, '2pans', 35)
        self.assertEqual(sum(1 for t in env['triangles'] if t['group'].startswith('pignon')), 2)

    def test_non_rectangular_pitched_falls_back_to_flat(self):
        env, note = gs.envelope_for(self._poly(L_SHAPE), 6.0, '2pans', 35)
        self.assertIn('terrasse', note)
        gs.check_closed(env['vertices'], env['triangles'])

    def test_validation(self):
        for bad in (([[0, 0], [1, 0]], 6, 1, 'terrasse', 0),                       # 2 sommets
                    ([[0, 0], [10, 10], [10, 0], [0, 10]], 6, 1, 'terrasse', 0),  # papillon
                    ([[0, 0], [1, 0], [1, 1]], 6, 1, 'terrasse', 0),              # < 5 m²
                    (RECT, 1.0, 1, 'terrasse', 0), (RECT, 6, 1, 'dome', 0), (RECT, 6, 1, '2pans', 80)):
            with self.assertRaises(gs.GeometryEditError):
                gs.validate(*bad)


class GeometryEditTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69, pipeline={
            'footprint_xy': RECT, 'geometry': {'emprise_m2': 80.0, 'perimetre_m': 36.0, 'hauteur_m': 6.0, 'mitoyennete': 0.0,
                                               'murs_exposes_m2': {'N': 60, 'E': 48, 'S': 60, 'O': 48},
                                               'horizon_deg': {'N': 0, 'E': 0, 'S': 0, 'O': 0}},
            'neighbours': [{'xy': [[5, -4], [15, -4], [15, 4], [5, 4]], 'h': 6.0}]})
        facts.record(self.b, 'identite.surface_m2', 150.0, 'infere')
        facts.record(self.b, 'identite.hauteur_m', 6.0, 'infere')

    def test_edit_regenerates_valid_envelope_then_computes(self):
        """Recette du lot : édition → enveloppe valide → calcul."""
        w = {'points': synthetic_year(days=60), 'label': 'synthétique'}
        before, _ = calc_service.run_level(self.b, 'N1', weather_spec=w, uncertainty=False)
        e = gs.apply_edit(self.b, {'footprint_xy': [[-5, -4], [5, -4], [5, 6], [-5, 6]], 'eave_height_m': 9.0, 'levels': 3,
                                   'roof_type': '2pans', 'roof_pitch_deg': 40}, 'u', 'alice')
        self.b.refresh_from_db()
        gs.check_closed(self.b.envelope['vertices'], self.b.envelope['triangles'])
        self.assertTrue(e.envelope_regenerated)
        self.assertTrue(self.b.sun_visibility_stale)
        self.assertEqual(e.summary['emprise_m2'], 100.0)
        self.assertGreater(e.summary['mitoyennete'], 0.1)  # voisin gardé par la chaîne, accolé à l'est
        eff = facts.effective(self.b)
        self.assertEqual((eff['identite.hauteur_m'].value, eff['identite.hauteur_m'].method), (9.0, 'declare'))
        self.assertEqual(eff['toiture.type'].value, '2pans')
        after, fresh = calc_service.run_level(self.b, 'N1', weather_spec=w, uncertainty=False)
        self.assertTrue(fresh)
        self.assertNotEqual(after.indicators['heating_kwh'], before.indicators['heating_kwh'])
        d = gs.diff(self.b)
        self.assertEqual({c['label'] for c in d['changes']} >= {'emprise', 'hauteur'}, True)
        self.assertEqual(d['author'], 'alice')

    def test_retire_returns_to_automatic(self):
        gs.apply_edit(self.b, {'eave_height_m': 12.0, 'levels': 4, 'roof_type': 'terrasse'}, 'u')
        self.assertEqual(facts.effective_values(self.b)['identite.hauteur_m'], 12.0)
        gs.retire_edit(self.b, 'u')
        self.assertEqual(facts.effective_values(self.b)['identite.hauteur_m'], 6.0)
        self.assertEqual(gs.effective_summary(self.b)['emprise_m2'], 80.0)

    def test_manual_paroi_envelope_not_overwritten_by_default(self):
        self.b.envelope = box_envelope(paroi_id=None)
        self.b.envelope['triangles'][0]['paroi_model_id'] = 1
        self.b.save()
        e = gs.apply_edit(self.b, {'eave_height_m': 7.0, 'levels': 2}, 'u')
        self.assertFalse(e.envelope_regenerated)
        self.b.refresh_from_db()
        self.assertEqual(self.b.envelope['triangles'][0]['paroi_model_id'], 1)

    def test_pitched_roof_increases_roof_area_in_n1(self):
        from ..engines import inputs
        flat = inputs.from_fiche({'identite.emprise_m2': 100.0, 'toiture.type': 'terrasse'})
        pitched = inputs.from_fiche({'identite.emprise_m2': 100.0, 'toiture.type': '2pans', 'toiture.pente_deg': 45})
        self.assertAlmostEqual(pitched.roof_area, 100 / 0.7071, places=0)
        self.assertEqual(flat.roof_area, 100.0)


@patch_jwks()
class GeometryApiTest(TestCase):
    def test_api_edit_diff_and_permissions(self):
        b = Building.objects.create(name='b', pipeline={'footprint_xy': RECT, 'geometry': {'emprise_m2': 80.0, 'hauteur_m': 6.0}})
        c = client_for(sub='u', preferred_username='alice')
        bad = c.post(f'/api/batiments/{b.pk}/geometrie/', {'footprint_xy': [[0, 0], [10, 10], [10, 0], [0, 10]],
                                                           'eave_height_m': 6, 'levels': 2}, format='json')
        self.assertEqual(bad.status_code, 400)
        r = c.post(f'/api/batiments/{b.pk}/geometrie/', {'footprint_xy': RECT, 'eave_height_m': 11.5, 'levels': 4,
                                                         'roof_type': 'terrasse'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()['diff']
        self.assertEqual(next(x for x in d['changes'] if x['label'] == 'hauteur')['edit'], 11.5)
        self.assertEqual(c.delete(f'/api/batiments/{b.pk}/geometrie/').json()['edit'], None)
        from ..models import Portfolio, PortfolioMember, BuildingAccess
        p = Portfolio.objects.create(name='P', owner_sub='g')
        b2 = Building.objects.create(name='x', portfolio=p, pipeline={'footprint_xy': RECT})
        BuildingAccess.objects.create(building=b2, sub='occ', role='occupant')
        r = client_for(sub='occ').post(f'/api/batiments/{b2.pk}/geometrie/', {'eave_height_m': 6, 'levels': 2}, format='json')
        self.assertEqual(r.status_code, 403)
