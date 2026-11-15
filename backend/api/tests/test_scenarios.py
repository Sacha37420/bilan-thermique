"""Lot 18 (refonte) — scénarios (bouquets de travaux) et comparateur base / variantes / météos."""

from unittest import mock

from django.test import TestCase

from .. import facts, scenarios as sc_service, weather_service as ws
from ..models import Building, Scenario
from ..testing import client_for, patch_jwks, synthetic_year
from .test_calage import seeded

YEAR = synthetic_year()


@patch_jwks()
class ScenarioTest(TestCase):
    def setUp(self):
        self.b = seeded()
        self.tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})
        self.tmy.start()
        self.addCleanup(self.tmy.stop)

    def test_base_and_two_scenarios_compared(self):
        """Recette du lot : base et 2 scénarios comparés."""
        c = client_for(sub='u')
        s1 = c.post(f'/api/batiments/{self.b.pk}/scenarios/', {'name': 'Isolation', 'bundles': ['ite', 'combles']}, format='json')
        s2 = c.post(f'/api/batiments/{self.b.pk}/scenarios/', {'name': 'Rénovation globale',
                                                              'bundles': ['ite', 'combles', 'fenetres', 'vmc_df', 'pac']}, format='json')
        self.assertEqual(s1.status_code, 201, s1.content)
        r = c.post(f'/api/batiments/{self.b.pk}/comparer/', {'scenarios': [None, s1.json()['id'], s2.json()['id']]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()['rows']
        heat = [x['indicators']['heating_kwh'] for x in rows]
        ep = [x['indicators']['primary_kwh_m2'] for x in rows]
        self.assertGreater(heat[0], heat[1])
        self.assertGreater(heat[1], heat[2])
        self.assertGreater(ep[0], ep[2])
        self.assertTrue(any('architecte des bâtiments de France' in v['message'] for v in r.json()['vigilance']))

    def test_scenario_never_changes_base(self):
        base_u = facts.effective_values(self.b)['mur.u']
        s = Scenario.objects.create(building=self.b, name='x')
        sc_service.apply_bundles(self.b, s, ['ite'])
        self.assertEqual(facts.effective_values(self.b)['mur.u'], base_u)
        self.assertLess(facts.effective_values(self.b, s)['mur.u'], base_u)
        self.assertTrue(any(d['key'] == 'mur.u' for d in sc_service.differences(self.b, s)))

    def test_future_climate_and_shading_bundle(self):
        hot = ws.save('tracc', 'TRACC +4', synthetic_year(t_mean=16.0, t_amp=9.0))
        s = Scenario.objects.create(building=self.b, name='Protections')
        sc_service.apply_bundles(self.b, s, ['store_sud'])
        rows = sc_service.compare(self.b, [None, s], [None, hot.pk])
        self.assertEqual(len(rows), 4)
        by = {(r['scenario'], r['weather']): r['indicators'] for r in rows}
        self.assertLess(by[(None, hot.pk)]['heating_kwh'], by[(None, None)]['heating_kwh'])
        self.assertLessEqual(by[(s.pk, hot.pk)]['hours_over_28'], by[(None, hot.pk)]['hours_over_28'])

    def test_validation_and_permissions(self):
        c = client_for(sub='u')
        self.assertEqual(c.post(f'/api/batiments/{self.b.pk}/scenarios/', {'name': 'x', 'bundles': ['inconnu']}, format='json').status_code, 400)
        self.assertEqual(Scenario.objects.count(), 0)
        from ..models import Portfolio, PortfolioMember
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        b2 = Building.objects.create(name='y', portfolio=p)
        self.assertEqual(client_for(sub='l').post(f'/api/batiments/{b2.pk}/scenarios/', {'name': 'z'}, format='json').status_code, 403)
