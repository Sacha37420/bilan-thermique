"""Lot 26 (refonte) — gestes experts → faits : enveloppe assignée, environnement relevé, façades analysées."""

from unittest import mock

from django.test import TestCase

from .. import calc_service, expert_facts, facts, geometry_service
from ..engines.inputs import u_from_layers
from ..models import Building, BuildingDocument, Environment, FactProposal, ParoiModel
from ..testing import box_envelope, client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


WALL = [{'e': 0.20, 'lam': 1.0, 'rho': 2000, 'c': 1000, 'tau': 0, 'r': 0.4, 'alpha': 0.6},
        {'e': 0.10, 'lam': 0.04, 'rho': 30, 'c': 1400, 'tau': 0, 'r': 0.4, 'alpha': 0.6}]
GLASS = [{'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.8, 'r': 0.08, 'alpha': 0.12},
         {'e': 0.016, 'lam': 0.03, 'rho': 1.2, 'c': 1000, 'tau': 0.97, 'r': 0.01, 'alpha': 0.02},
         {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.8, 'r': 0.08, 'alpha': 0.12}]
WEATHER = synthetic_year(days=60)


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER, 'source': 'test'})


def building():
    b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69,
                                pipeline={'footprint_xy': [[0, 0], [10, 0], [10, 8], [0, 8]],
                                          'geometry': {'hauteur_m': 6.0, 'horizon_deg': {o: 0.0 for o in 'NESO'}}})
    for k, v in (('identite.surface_m2', 80.0), ('mur.u', 1.9), ('identite.usage', 'maison')):
        facts.record(b, k, v, 'infere')
    return b


@patch_jwks()
class ExpertEnvelopeTest(TestCase):
    def setUp(self):
        self.wall = ParoiModel.objects.create(name='Mur ITE', layers=WALL)
        self.glass = ParoiModel.objects.create(name='Double vitrage', layers=GLASS)
        self.b = building()

    def _envelope(self, glazed=True):
        env = box_envelope(paroi_id=self.wall.pk)
        if glazed:
            env['triangles'][4]['paroi_model_id'] = self.glass.pk   # moitié du mur sud vitrée
        return env

    @staticmethod
    def _body(env):
        """Corps d'écriture de l'API : sommets et triangles bruts (la géométrie est recalculée côté serveur)."""
        return {'vertices': env['vertices'], 'triangles': [
            {k: t[k] for k in ('v', 'group', 'paroi_model_id', 'boundary') if k in t} for t in env['triangles']]}

    def test_assigned_envelope_feeds_the_fiche_and_unassigning_retires(self):
        c = client_for(sub='expert')
        with tmy():
            run, _ = calc_service.run_level(self.b, 'N1')
        r = c.patch(f'/api/batiments/{self.b.pk}/', self._body(self._envelope()), format='json')
        self.assertEqual(r.status_code, 200, r.content)
        eff = facts.effective(self.b)
        self.assertEqual(eff['mur.u'].source, expert_facts.SOURCE_ENVELOPE)
        self.assertEqual(eff['mur.u'].method, 'declare')
        self.assertEqual(eff['mur.u'].author_sub, 'expert')
        self.assertAlmostEqual(eff['mur.u'].value, u_from_layers(WALL, 7.7, 25.0), places=3)
        self.assertAlmostEqual(eff['baies.uw'].value, u_from_layers(GLASS, 7.7, 25.0), places=3)
        self.assertAlmostEqual(eff['baies.g'].value, 0.8 * 0.97 * 0.8, places=3)
        self.assertAlmostEqual(eff['baies.ratio_S'].value, 0.5, places=3)
        self.assertEqual(eff['baies.ratio_N'].value, 0.0)
        self.assertAlmostEqual(eff['identite.emprise_m2'].value, 80.0, places=1)
        with tmy():
            self.assertFalse(calc_service.is_up_to_date(run))      # N0/N1 voient le geste
        # Réenregistrer la même enveloppe ne multiplie rien.
        n = self.b.facts.count()
        c.patch(f'/api/batiments/{self.b.pk}/', self._body(self._envelope()), format='json')
        self.assertEqual(self.b.facts.count(), n)
        # Une enveloppe qui n'est plus entièrement assignée retire les faits : la fiche revient à l'automatique.
        env = self._envelope()
        env['triangles'][6]['paroi_model_id'] = None
        c.patch(f'/api/batiments/{self.b.pk}/', self._body(env), format='json')
        eff = facts.effective(self.b)
        self.assertEqual(eff['mur.u'].method, 'infere')
        self.assertNotIn('baies.ratio_S', {k for k, f in eff.items() if f.source == expert_facts.SOURCE_ENVELOPE})

    def test_changed_assignment_updates_values(self):
        c = client_for(sub='expert')
        c.patch(f'/api/batiments/{self.b.pk}/', self._body(self._envelope(glazed=True)), format='json')
        c.patch(f'/api/batiments/{self.b.pk}/', self._body(self._envelope(glazed=False)), format='json')
        eff = facts.effective(self.b)
        self.assertEqual(eff['baies.ratio_S'].value, 0.0)
        self.assertNotIn('baies.uw', {k for k, f in eff.items() if f.source == expert_facts.SOURCE_ENVELOPE})

    def test_party_wall_share(self):
        env = self._envelope(glazed=False)
        for t in env['triangles']:
            if t['group'] == 'mur_est':
                t['boundary'] = 'adiabatic'
        self.b.envelope = env
        self.b.save()
        expert_facts.sync_envelope(self.b, 'expert')
        self.assertAlmostEqual(facts.effective(self.b)['identite.mitoyennete'].value, 48 / 216, places=3)

    def test_geometry_edit_regenerating_envelope_retires_expert_facts(self):
        self.b.envelope = self._envelope()
        self.b.save()
        expert_facts.sync_envelope(self.b, 'expert')
        geometry_service.apply_edit(self.b, {'footprint_xy': [[0, 0], [12, 0], [12, 8], [0, 8]], 'eave_height_m': 3,
                                             'levels': 1, 'roof_type': 'terrasse'}, author_sub='expert',
                                    regenerate_envelope=True)
        self.assertEqual(facts.effective(self.b)['mur.u'].method, 'infere')


class EnvironmentHorizonTest(TestCase):
    def test_linked_environment_gives_horizon_and_outdates_n1(self):
        b = building()
        with tmy():
            run, _ = calc_service.run_level(b, 'N1')
        tower = {'id': 1, 'kind': 'building', 'status': 'active', 'footprint': None,
                 'vertices': [[0, -20, 0], [10, -20, 0], [10, -14, 0], [0, -14, 0],
                              [0, -20, 24], [10, -20, 24], [10, -14, 24], [0, -14, 24]], 'triangles': []}
        studied = {**tower, 'id': 2, 'status': 'studied', 'vertices': [[0, 20, 0], [0, 20, 90], [5, 25, 90]]}
        env = Environment.objects.create(name='env', scene_objects=[tower, studied])
        b.environment = env
        b.save()
        s = geometry_service.effective_summary(b)
        self.assertEqual(s['horizon_source'], 'environnement relevé')
        self.assertGreater(s['horizon_deg']['S'], 40)        # tour de 24 m à 14 m au sud
        self.assertEqual(s['horizon_deg']['N'], 0.0)          # le bâtiment étudié n'est pas un obstacle
        with tmy():
            self.assertFalse(calc_service.is_up_to_date(run))
            masked, _ = calc_service.run_level(b, 'N1')
        self.assertGreater(masked.indicators['heating_kwh'], run.indicators['heating_kwh'])
        self.assertEqual(masked.inputs['params']['horizon_deg']['S'], s['horizon_deg']['S'])


@patch_jwks()
class FacadeProposalsTest(TestCase):
    def _analysed(self, b, windows):
        b.facades = {'facades': {
            'mur_sud': {'status': 'analysee', 'area': 60.0, 'azimuth': 180.0,
                        'openings': [{'label': 'window', 's0': 1, 't0': 1, 's1': 1 + w, 't1': 2.5} for w in windows]
                        + [{'label': 'door', 's0': 8, 't0': 0, 's1': 9, 't1': 2.1}]},
            'mur_nord': {'status': 'sans_photo', 'area': 60.0, 'azimuth': 0.0, 'openings': []},
        }}
        b.save()

    def test_analysis_yields_validated_proposals(self):
        b = building()
        self._analysed(b, [2.0, 2.0])                    # 2 × 3 m² sur 60 m² vus
        doc = expert_facts.facade_proposals(b, 'expert')
        self.assertEqual(doc.kind, 'facades')
        props = list(FactProposal.objects.filter(building=b))
        self.assertEqual([(p.key, p.value, p.control, p.decision) for p in props],
                         [('baies.ratio_S', 0.1, 'ok', 'en_attente')])
        listed = client_for(sub='expert').get(f'/api/batiments/{b.pk}/propositions/').json()
        self.assertEqual(len(listed['results'] if isinstance(listed, dict) else listed), 1)
        r = client_for(sub='expert').post(f'/api/propositions/{props[0].pk}/decision/', {'decision': 'acceptee'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        f = facts.effective(b)['baies.ratio_S']
        self.assertEqual((f.value, f.method), (0.1, 'document'))
        self.assertIn('façades', f.source)
        # Une nouvelle analyse ne touche pas au document dont une proposition a été acceptée.
        self._analysed(b, [4.0])
        expert_facts.facade_proposals(b, 'expert')
        self.assertEqual(BuildingDocument.objects.filter(building=b, kind='facades').count(), 2)

    def test_new_analysis_replaces_pending_proposals_and_nothing_without_photos(self):
        b = building()
        self._analysed(b, [2.0])
        expert_facts.facade_proposals(b, 'expert')
        self._analysed(b, [3.0])
        expert_facts.facade_proposals(b, 'expert')
        self.assertEqual(BuildingDocument.objects.filter(building=b, kind='facades').count(), 1)
        self.assertEqual(FactProposal.objects.get(building=b).value, 0.075)
        b.facades = {'facades': {'mur_nord': {'status': 'sans_photo', 'area': 60.0, 'azimuth': 0.0, 'openings': []}}}
        self.assertIsNone(expert_facts.facade_proposals(b, 'expert'))
