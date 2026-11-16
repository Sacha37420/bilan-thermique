"""Lot 25 (refonte) — préparer une simulation : entrées annoncées = entrées enregistrées, fiabilité, blocages."""

from unittest import mock

from django.test import TestCase

from .. import calc_service, facts
from ..models import Building, BuildingAccess, CalcRun, Job, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


WEATHER = synthetic_year()


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER, 'source': 'test'})


def building(**kw):
    b = Building.objects.create(name=kw.pop('name', 'b'), georef_lat=47.39, georef_lon=0.69,
                                pipeline={'footprint_xy': [[0, 0], [10, 0], [10, 8], [0, 8]]}, **kw)
    for k, v, m in (('identite.surface_m2', 80.0, 'infere'), ('mur.u', 1.2, 'defaut'), ('identite.usage', 'maison', 'infere'),
                    ('baies.uw', 2.8, 'declare'), ('identite.niveaux', 1, 'infere'), ('identite.hauteur_m', 3.0, 'infere')):
        facts.record(b, k, v, m, author_sub='x' if m == 'declare' else '')
    return b


@patch_jwks()
class SimulationTest(TestCase):
    def test_announced_inputs_are_exactly_the_recorded_ones(self):
        b = building()
        c = client_for()
        with tmy():
            prep1 = c.get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()
            prep2 = c.get(f'/api/batiments/{b.pk}/simulation/?niveau=N2').json()
            run1, _ = calc_service.run_level(b, 'N1')
            run2, _ = calc_service.start_n2(b)
        self.assertEqual(prep1['inputs'], run1.inputs['facts'])
        self.assertEqual(prep2['inputs'], run2.inputs['facts'])
        self.assertEqual(prep2['engine']['parois'], run2.inputs['parois'])
        self.assertEqual(prep1['engine']['params'], run1.inputs['params'])
        with tmy():
            again = c.get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()
        self.assertEqual(again['already'], run1.pk)       # déjà calculé avec ces entrées
        self.assertIsNotNone(again['range'])               # fourchette du N1 à jour
        self.assertLess(again['range']['heating_kwh_m2_p10'], again['range']['heating_kwh_m2_p90'])

    def test_reliability_names_decisive_uncertain_inputs(self):
        b = building()
        with tmy():
            rel = client_for().get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()['reliability']
        self.assertIn(rel['verdict'], ('fiable', 'à confirmer', 'indicatif'))
        keys = [d['key'] for d in rel['decisive']]
        self.assertIn('mur.u', keys[:3])                   # U des murs « par défaut » : parmi les plus décisives
        self.assertIn('identite.surface_m2', keys[:3])     # la surface met tout à l'échelle
        before = rel['spread_pct']
        facts.record(b, 'mur.u', 1.2, 'mesure', author_sub='x')
        with tmy():
            after = client_for().get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()['reliability']['spread_pct']
        self.assertLess(after, before)                     # mesurer une entrée resserre la dispersion

    def test_blockers(self):
        b = Building.objects.create(name='sans position')
        facts.record(b, 'identite.surface_m2', 80.0, 'declare', author_sub='x')
        prep = client_for().get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()
        self.assertFalse(prep['can_launch'])
        self.assertTrue(any('position' in m for m in prep['blockers']))
        self.assertEqual(client_for().get(f'/api/batiments/{b.pk}/simulation/?niveau=N9').status_code, 400)

    def test_occupant_cannot_launch_n2_reader_cannot_launch_anything(self):
        p = Portfolio.objects.create(name='P')
        PortfolioMember.objects.create(portfolio=p, sub='lect', role=PortfolioMember.LECTEUR)
        b = building(portfolio=p)
        BuildingAccess.objects.create(building=b, sub='occ', role=BuildingAccess.OCCUPANT)
        with tmy():
            occ_n1 = client_for(sub='occ').get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()
            occ_n2 = client_for(sub='occ').get(f'/api/batiments/{b.pk}/simulation/?niveau=N2').json()
            lect = client_for(sub='lect').get(f'/api/batiments/{b.pk}/simulation/?niveau=N1').json()
        self.assertTrue(occ_n1['can_launch'])
        self.assertFalse(occ_n2['can_launch'])
        self.assertFalse(lect['can_launch'])
        self.assertEqual(client_for(sub='inconnu').get(f'/api/batiments/{b.pk}/simulation/').status_code, 404)

    def test_busy_lab_reported_for_n2(self):
        b = building()
        Job.objects.create(kind='building_calcul', status=Job.RUNNING)
        with tmy():
            prep = client_for().get(f'/api/batiments/{b.pk}/simulation/?niveau=N2').json()
        self.assertTrue(prep['busy'])
        self.assertGreater(prep['estimate_s'], 2)
        self.assertFalse(CalcRun.objects.exists())          # préparer ne lance rien
