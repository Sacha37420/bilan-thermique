"""Lot 24 (refonte) — page « Calculs » : liste avec état, détail avec entrées figées, comparaison de deux calculs."""

from unittest import mock

from django.test import TestCase

from .. import calc_service, facts
from ..models import Building, CalcRun, Job, Portfolio, PortfolioMember, Scenario
from ..testing import client_for, patch_jwks, synthetic_year

WEATHER = synthetic_year(days=60)


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER, 'source': 'test'})


def building(name='b', portfolio=None):
    b = Building.objects.create(name=name, georef_lat=47.39, georef_lon=0.69, portfolio=portfolio)
    for k, v in (('identite.surface_m2', 100.0), ('mur.u', 1.2), ('identite.usage', 'maison')):
        facts.record(b, k, v, 'infere')
    return b


@patch_jwks()
class CalcPageTest(TestCase):
    def test_list_reports_state_and_detail_carries_frozen_inputs(self):
        b = building()
        with tmy():
            r1, _ = calc_service.run_level(b, 'N1')
            facts.record(b, 'mur.u', 0.3, 'declare', author_sub='x')
            r2, _ = calc_service.run_level(b, 'N1')
            rows = client_for().get(f'/api/batiments/{b.pk}/calculs/').json()['results']
        state = {r['id']: r['up_to_date'] for r in rows}
        self.assertEqual(state, {r1.pk: False, r2.pk: True})
        self.assertTrue(all('facts' not in r['settings'] for r in rows))
        d = client_for().get(f'/api/calculs/{r1.pk}/').json()
        self.assertEqual(d['inputs']['facts']['mur.u']['value'], 1.2)        # figé : l'ancienne valeur
        self.assertEqual(d['inputs']['params']['murs']['S']['u'], 1.2)
        self.assertEqual(d['mode'], 'fiche')
        self.assertIn('t_op', d['results'])

    def test_scenario_filter(self):
        b = building()
        sc = Scenario.objects.create(building=b, name='Isolation')
        with tmy():
            calc_service.run_level(b, 'N1')
            calc_service.run_level(b, 'N1', scenario=sc)
        base = client_for().get(f'/api/batiments/{b.pk}/calculs/?scenario=').json()['results']
        var = client_for().get(f'/api/batiments/{b.pk}/calculs/?scenario={sc.pk}').json()['results']
        self.assertEqual([r['scenario'] for r in base], [None])
        self.assertEqual([r['scenario_name'] for r in var], ['Isolation'])

    def test_compare_two_runs(self):
        b = building()
        with tmy():
            r1, _ = calc_service.run_level(b, 'N1')
            facts.record(b, 'mur.u', 0.3, 'declare', author_sub='x')
            r2, _ = calc_service.run_level(b, 'N1')
        c = client_for().get(f'/api/calculs/{r1.pk}/comparer/{r2.pk}/').json()
        self.assertEqual([f['key'] for f in c['facts']], ['mur.u'])
        self.assertEqual((c['facts'][0]['a']['value'], c['facts'][0]['b']['value']), (1.2, 0.3))
        heat = next(i for i in c['indicators'] if i['key'] == 'heating_kwh')
        self.assertLess(heat['delta'], 0)
        self.assertTrue(any(x['key'] == 'params.murs.S.u' for x in c['inputs']))
        self.assertIsNone(c['weather'])

    def test_compare_refused_across_buildings_and_hidden_runs(self):
        b1, b2 = building('b1'), building('b2')
        with tmy():
            r1, _ = calc_service.run_level(b1, 'N1')
            r2, _ = calc_service.run_level(b2, 'N1')
        self.assertEqual(client_for().get(f'/api/calculs/{r1.pk}/comparer/{r2.pk}/').status_code, 400)
        p = Portfolio.objects.create(name='P')
        PortfolioMember.objects.create(portfolio=p, sub='gp', role=PortfolioMember.GESTIONNAIRE_PARC)
        hidden = building('caché', portfolio=p)
        run = CalcRun.objects.create(building=hidden, level='N1', status=Job.DONE, inputs={'facts': {}})
        self.assertEqual(client_for(sub='autre').get(f'/api/calculs/{run.pk}/').status_code, 404)
        self.assertEqual(client_for(sub='autre').get(f'/api/calculs/{run.pk}/comparer/{run.pk}/').status_code, 404)
        self.assertEqual(client_for(sub='gp').get(f'/api/calculs/{run.pk}/').status_code, 200)

    def test_calcul_3d_run_shown_with_its_form_settings(self):
        b = building()
        legacy = CalcRun.objects.create(building=b, level='N2', status=Job.DONE, settings={'dx_max': 0.05, 'h_e': 25},
                                        results={'hours': 2, 't_op': [19, 19.5, 20], 'heating_kwh': 1.0})
        d = client_for().get(f'/api/calculs/{legacy.pk}/').json()
        self.assertEqual(d['mode'], 'calcul_3d')
        self.assertIsNone(d['up_to_date'])
        self.assertEqual(d['settings']['dx_max'], 0.05)
