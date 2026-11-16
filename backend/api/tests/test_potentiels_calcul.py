"""Chantier bt-potentiels, lot 37 (secteur A) — potentiels d'un bâtiment et d'un parc (I5) : calcul N1, empreinte,
idempotence, droits, N2 jamais en lot, agrégats exacts."""

import statistics
from unittest import mock

from django.test import TestCase

from .. import facts, parc
from ..ameliorations import bouquets as bq_service, potentiels as pot_service, taches
from ..models import Bouquet, Building, CalcRun, Portfolio, PortfolioMember, Potentiel, Scenario
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment
from .test_calage import seeded

YEAR = synthetic_year()
_env = without_auto_environment()
_tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})


def setUpModule():
    _env.start()
    _tmy.start()


def tearDownModule():
    _tmy.stop()
    _env.stop()


def lab(objectif):
    bq_service.ensure_defaults()
    return Bouquet.objects.get(portfolio__isnull=True, objectif=objectif)


@patch_jwks()
class BatimentTest(TestCase):
    def setUp(self):
        self.b = seeded()
        self.c = client_for(sub='u')
        self.url = f'/api/batiments/{self.b.pk}/potentiels/'

    def test_get_before_any_calc(self):
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j['peut_calculer'] and j['peut_calculer_n2'])
        self.assertEqual(j['reel'], {'niveau': 'N1', 'a_jour': False, 'indicateurs': None, 'run_id': None, 'calcule_le': None})
        self.assertEqual(len(j['bouquets']), 2)
        e = j['bouquets'][0]
        self.assertEqual((e['statut'], e['a_jour'], e['indicateurs'], e['ecarts'], e['actions']), (None, False, None, {}, []))

    def test_n1_energy_bouquet_lowers_need_and_is_idempotent(self):
        e = lab('energie')
        r = self.c.post(self.url, {'bouquets': [e.pk], 'niveau': 'N1'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertTrue(j['reel']['a_jour'])
        entry = next(x for x in j['bouquets'] if x['bouquet']['id'] == e.pk)
        self.assertEqual(entry['statut'], 'fait')
        self.assertTrue(entry['a_jour'])
        self.assertLess(entry['ecarts']['heating_kwh_m2'], 0)
        self.assertLess(entry['ecarts_pct']['heating_kwh_m2'], 0)
        self.assertAlmostEqual(entry['ecarts']['heating_kwh_m2'],
                               entry['indicateurs']['heating_kwh_m2'] - j['reel']['indicateurs']['heating_kwh_m2'], places=2)
        cles = [a['cle'] for a in entry['actions']]
        self.assertIn('ite', cles)
        self.assertIn('pac', cles)
        for a in entry['actions']:
            self.assertEqual(set(a), {'cle', 'libelle', 'appliquee', 'raison', 'parametres', 'effets', 'avertissements'})
        # Appliqué deux fois : même amélioration (recréée, pas dupliquée), même calcul.
        sid, rid = entry['scenario_id'], entry['run_id']
        pot_service.compute_n1(self.b, e, 'u', force=True)
        self.assertEqual(Scenario.objects.filter(building=self.b, bouquet=e, genere=True).count(), 1)
        pot = Potentiel.objects.get(building=self.b, bouquet=e, niveau='N1')
        self.assertEqual((pot.scenario_id, pot.run_id), (sid, rid))
        self.assertEqual(Potentiel.objects.count(), 1)
        # La base n'a pas bougé ; une modification de la fiche rend le potentiel « à recalculer ».
        facts.record(self.b, 'mur.u', 1.2, 'declare', author_sub='u')
        entry = next(x for x in self.c.get(self.url).json()['bouquets'] if x['bouquet']['id'] == e.pk)
        self.assertFalse(entry['a_jour'])
        self.assertEqual(entry['statut'], 'fait')

    def test_bouquet_change_makes_stale(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='u', role='gestionnaire_parc')
        self.b.portfolio = p
        self.b.save()
        mine = Bouquet.objects.create(portfolio=p, nom='m', elements=[{'action': 'combles', 'parametres': {}}])
        self.assertEqual(self.c.post(self.url, {'bouquets': [mine.pk]}, format='json').status_code, 200)
        mine.elements = [{'action': 'combles', 'parametres': {'epaisseur_cm': 40}}]
        mine.save()
        entry = next(x for x in self.c.get(self.url).json()['bouquets'] if x['bouquet']['id'] == mine.pk)
        self.assertFalse(entry['a_jour'])

    def test_limits_and_n2(self):
        e, c_ = lab('energie'), lab('confort')
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='u', role='gestionnaire_parc')
        self.b.portfolio = p
        self.b.save()
        extra = [Bouquet.objects.create(portfolio=p, nom=f'b{i}', elements=[]).pk for i in range(3)]
        r = self.c.post(self.url, {'bouquets': [e.pk, c_.pk] + extra}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['detail'], '4 bouquets au plus par calcul.')
        r = self.c.post(self.url, {'bouquets': [e.pk, c_.pk], 'niveau': 'N2'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.c.post(self.url, {'bouquets': [999999]}, format='json')
        self.assertEqual(r.status_code, 400)
        other = Bouquet.objects.create(portfolio=Portfolio.objects.create(name='Q', owner_sub='h'), nom='o', elements=[])
        self.assertEqual(self.c.post(self.url, {'bouquets': [other.pk]}, format='json').status_code, 400)
        # N2 d'un seul bouquet : lancé en tâche de fond (202), le potentiel suit le calcul.
        fake = CalcRun.objects.create(building=self.b, level='N2', status='PENDING')
        with mock.patch('api.calc_service.start_n2', return_value=(fake, True)) as start:
            r = self.c.post(self.url, {'bouquets': [c_.pk], 'niveau': 'N2'}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        self.assertIsNotNone(start.call_args.kwargs['scenario'])
        n2 = [x for x in r.json()['bouquets'] if x['niveau'] == 'N2']
        self.assertEqual([(x['bouquet']['id'], x['statut']) for x in n2], [(c_.pk, 'en_cours')])
        fake.status, fake.indicators = 'DONE', {'heating_kwh_m2': 50.0}
        fake.save()
        n2 = [x for x in self.c.get(self.url).json()['bouquets'] if x['niveau'] == 'N2']
        self.assertEqual(n2[0]['statut'], 'fait')
        self.assertEqual(n2[0]['indicateurs'], {'heating_kwh_m2': 50.0})
        from .. import calc_service
        with mock.patch('api.calc_service.start_n2', side_effect=calc_service.Busy('Un calcul lourd est déjà en cours.')):
            r = self.c.post(self.url, {'bouquets': [e.pk], 'niveau': 'N2'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_rights(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        b = Building.objects.create(name='y', portfolio=p)
        url = f'/api/batiments/{b.pk}/potentiels/'
        lec = client_for(sub='l')
        r = lec.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['peut_calculer'])
        self.assertEqual(lec.post(url, {'niveau': 'N1'}, format='json').status_code, 403)
        self.assertEqual(lec.post(url, {'niveau': 'N2'}, format='json').status_code, 403)
        self.assertEqual(client_for(sub='x').get(url).status_code, 404)
        self.assertEqual(client_for(sub='x').post(url, {}, format='json').status_code, 404)
        self.assertEqual(Potentiel.objects.count(), 0)


@patch_jwks()
class ParcTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role='lecteur')
        self.bs = []
        for i, u in enumerate((1.6, 1.2, 0.9)):
            b = seeded(**{'mur.u': (u, 'defaut')})
            b.portfolio, b.name = self.p, f'b{i}'
            b.save()
            self.bs.append(b)
        self.url = f'/api/parcs/{self.p.pk}/potentiels/'

    def _run_queue(self):
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async') as again:
            while taches.potentiels_parc_suivant.run(self.p.pk, 'g') != 'fini':
                pass
        return again

    def test_three_buildings_two_bouquets_exact_aggregates(self):
        g = client_for(sub='g')
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async') as queued:
            r = g.post(self.url, {}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        queued.assert_called_once()
        self.assertEqual(queued.call_args.kwargs['queue'], 'parc')
        self.assertEqual(r.json()['file'], {'en_attente': 6, 'en_cours': 0})
        self._run_queue()
        j = client_for(sub='l').get(self.url).json()
        self.assertFalse(j['peut_calculer'])
        self.assertEqual(j['file'], {'en_attente': 0, 'en_cours': 0})
        self.assertEqual(len(j['batiments']), 3)
        self.assertEqual([b['nom'] for b in j['bouquets']], ['Économiser l’énergie', 'Minimiser les DH'])
        for bq in j['bouquets']:
            key = str(bq['id'])
            vals = [row['potentiels'][key]['indicateurs']['heating_kwh'] for row in j['batiments']]
            ag = j['agregats'][key]['heating_kwh']
            self.assertEqual(ag['n'], 3)
            self.assertAlmostEqual(ag['somme'], sum(vals), places=2)
            self.assertAlmostEqual(ag['moyenne'], sum(vals) / 3, places=2)
            self.assertAlmostEqual(ag['mediane'], statistics.median(vals), places=2)
            self.assertIsNone(j['agregats'][key]['heating_kwh_m2']['somme'])
            for row in j['batiments']:
                pot = row['potentiels'][key]
                self.assertEqual(pot['statut'], 'fait')
                self.assertTrue(pot['a_jour'])
                self.assertGreaterEqual(pot['actions_appliquees'], 1)
        reel = [row['reel']['indicateurs']['heating_kwh'] for row in j['batiments']]
        self.assertAlmostEqual(j['agregats']['reel']['heating_kwh']['somme'], sum(reel), places=2)
        self.assertEqual(j['agregats']['reel']['heating_kwh']['n'], 3)
        e = str(j['bouquets'][0]['id'])
        self.assertLess(j['agregats'][e]['heating_kwh']['somme'], j['agregats']['reel']['heating_kwh']['somme'])
        # Rien à refaire : un second « Calculer » ne met rien en file.
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async') as queued:
            self.assertEqual(g.post(self.url, {}, format='json').status_code, 202)
        queued.assert_not_called()

    def test_rights_and_n2_refused(self):
        self.assertEqual(client_for(sub='l').post(self.url, {}, format='json').status_code, 403)
        self.assertEqual(client_for(sub='x').get(self.url).status_code, 404)
        r = client_for(sub='g').post(self.url, {'niveau': 'N2'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('bâtiment par bâtiment', r.json()['detail'])
        self.assertEqual(Potentiel.objects.count(), 0)

    def test_error_does_not_stop_queue(self):
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async'):
            client_for(sub='g').post(self.url, {'bouquets': [lab('energie').pk]}, format='json')
        with mock.patch('api.ameliorations.potentiels.compute_n1', side_effect=[RuntimeError('x'), None, None]):
            self._run_queue()
        st = sorted(Potentiel.objects.values_list('statut', flat=True))
        self.assertEqual(st, ['en_cours', 'en_cours', 'erreur'])   # compute simulé : rien n'a terminé les deux autres


class AggregatesTest(TestCase):
    def test_agregats_potentiels(self):
        a = parc.agregats_potentiels([{'heating_kwh': 10.0, 'heating_kwh_m2': 1.0, 'x': True, 's': 'a'},
                                      {'heating_kwh': 30.0, 'heating_kwh_m2': 3.0}, {'heating_kwh': 20.0}])
        self.assertEqual(a['heating_kwh'], {'somme': 60.0, 'moyenne': 20.0, 'mediane': 20.0, 'n': 3})
        self.assertEqual(a['heating_kwh_m2'], {'somme': None, 'moyenne': 2.0, 'mediane': 2.0, 'n': 2})
        self.assertNotIn('x', a)
        self.assertNotIn('s', a)
