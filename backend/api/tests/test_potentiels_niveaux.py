"""Chantier bt-trois-niveaux, lot 43 (secteur D) — potentiels sur trois niveaux (I3) : N0, `calculer`, `a_jour`,
`bouquets_visibles`, N2 synchrone (verrou des calculs lourds, `Busy`), potentiels de parc par niveau (agrégats
exacts, file N2)."""

import statistics
from unittest import mock

from django.test import TestCase

from .. import calc_service, facts
from ..ameliorations import bouquets as bq_service, potentiels as pot_service, taches
from ..models import Bouquet, CalcRun, Job, Portfolio, PortfolioMember, Potentiel, Scenario
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


def faux_n2(heating=42.0):
    """`calc_service.run_n2` simulé : termine le calcul préparé avec des indicateurs I3."""
    def run_n2(run, say=None):
        if say:
            say(50, 'Résolution…')
        run.status, run.results = Job.DONE, {'heating_kwh': heating * 120}
        run.indicators = {'heating_kwh_m2': heating, 'heating_kwh': heating * 120, 'dh_re2020': 100.0}
        run.save(update_fields=['status', 'results', 'indicators'])
        return run
    return run_n2


N2_BUILD = mock.patch('api.engines.n2_inputs.build', return_value={'snapshot': {'mode': 'simplifie'}})


@patch_jwks()
class N0Test(TestCase):
    def setUp(self):
        self.b = seeded()
        self.c = client_for(sub='u')
        self.url = f'/api/batiments/{self.b.pk}/potentiels/'

    def test_n0_par_http_et_amelioration_commune(self):
        e = lab('energie')
        r = self.c.post(self.url, {'bouquets': [e.pk], 'niveau': 'N0'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertTrue(j['reels']['N0']['a_jour'])
        n0 = next(x for x in j['bouquets'] if x['bouquet']['id'] == e.pk and x['niveau'] == 'N0')
        self.assertEqual((n0['statut'], n0['a_jour']), ('fait', True))
        self.assertLess(n0['ecarts']['heating_kwh_m2'], 0)
        self.assertAlmostEqual(n0['ecarts']['heating_kwh_m2'], n0['indicateurs']['heating_kwh_m2']
                               - j['reels']['N0']['indicateurs']['heating_kwh_m2'], places=2)
        self.assertEqual(CalcRun.objects.get(pk=n0['run_id']).level, 'N0')
        # N1 du même bouquet : même amélioration générée (une seule par bâtiment et bouquet).
        self.assertEqual(self.c.post(self.url, {'bouquets': [e.pk], 'niveau': 'N1'}, format='json').status_code, 200)
        self.assertEqual(Scenario.objects.filter(building=self.b, bouquet=e, genere=True).count(), 1)
        sids = set(Potentiel.objects.filter(building=self.b, bouquet=e).values_list('scenario_id', flat=True))
        self.assertEqual(len(sids), 1)
        # Les autres bouquets et le N2 restent « jamais calculés », à la forme v3.
        n2 = next(x for x in self.c.get(self.url).json()['bouquets'] if x['bouquet']['id'] == e.pk and x['niveau'] == 'N2')
        self.assertEqual((n2['statut'], n2['a_jour'], n2['indicateurs'], n2['ecarts'], n2['actions']),
                         (None, False, None, {}, []))

    def test_calculer_a_jour_et_bouquets_visibles(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        self.b.portfolio = p
        self.b.save()
        mine = Bouquet.objects.create(portfolio=p, nom='m', elements=[{'action': 'combles', 'parametres': {}}])
        other = Bouquet.objects.create(portfolio=Portfolio.objects.create(name='Q', owner_sub='h'), nom='o', elements=[])
        vis = pot_service.bouquets_visibles(self.b)
        self.assertEqual([x.pk for x in vis], [lab('energie').pk, lab('confort').pk, mine.pk])
        self.assertNotIn(other.pk, [x.pk for x in vis])
        e = lab('energie')
        for niv in ('N0', 'N1', 'N2'):
            self.assertFalse(pot_service.a_jour(self.b, e, niv))
        pot = pot_service.calculer(self.b, e, 'N0', owner_sub='sys')
        self.assertEqual((pot.niveau, pot.statut), ('N0', 'fait'))
        self.assertTrue(pot_service.a_jour(self.b, e, 'N0'))
        self.assertFalse(pot_service.a_jour(self.b, e, 'N1'))
        # À jour : rien n'est recalculé.
        with mock.patch('api.calc_service.run_level') as rl:
            again = pot_service.calculer(self.b, e, 'N0')
        rl.assert_not_called()
        self.assertEqual(again.run_id, pot.run_id)
        # Une correction de la fiche rend le potentiel « à refaire » ; `calculer` le refait.
        facts.record(self.b, 'mur.u', 1.1, 'declare', author_sub='u')
        self.assertFalse(pot_service.a_jour(self.b, e, 'N0'))
        pot = pot_service.calculer(self.b, e, 'N0')
        self.assertTrue(pot_service.a_jour(self.b, e, 'N0'))
        with self.assertRaises(pot_service.PotentielError):
            pot_service.calculer(self.b, e, 'N3')


@patch_jwks()
class N2SynchroneTest(TestCase):
    def setUp(self):
        self.b = seeded()
        self.e = lab('energie')

    def test_busy_quand_un_lourd_tourne(self):
        Job.objects.create(kind='n2_fiche', status=Job.RUNNING)
        with N2_BUILD, mock.patch('api.calc_service.run_n2') as run_n2, self.assertRaises(calc_service.Busy):
            pot_service.calculer(self.b, self.e, 'N2')
        run_n2.assert_not_called()
        self.assertFalse(CalcRun.objects.filter(level='N2').exists())
        self.assertFalse(Scenario.objects.filter(building=self.b, genere=True).exists())   # rien d'écrit
        self.assertFalse(pot_service.a_jour(self.b, self.e, 'N2'))

    def test_busy_pris_entre_le_test_et_le_verrou(self):
        # Le lourd démarre après le premier contrôle : le verrou (`launch_exclusive`) le voit.
        with N2_BUILD, mock.patch('api.ameliorations.potentiels.lourd_en_cours', side_effect=[False, False]), \
                mock.patch('api.joblock.launch_exclusive', return_value=None), self.assertRaises(calc_service.Busy):
            pot_service.calculer(self.b, self.e, 'N2')
        self.assertEqual(Potentiel.objects.get(niveau='N2').statut, 'en_attente')

    def test_n2_calcule_dans_le_processus_et_rend_le_verrou(self):
        with N2_BUILD, mock.patch('api.calc_service.run_n2', side_effect=faux_n2()) as run_n2, \
                mock.patch('api.tasks.run_n2_fiche.delay') as delay:
            pot = pot_service.calculer(self.b, self.e, 'N2', owner_sub='sys')
        delay.assert_not_called()                                   # pas de seconde tâche Celery
        run_n2.assert_called_once()
        self.assertEqual((pot.statut, pot.niveau), ('fait', 'N2'))
        self.assertEqual(pot.run.status, Job.DONE)
        self.assertIsNotNone(pot.run.scenario_id)
        self.assertEqual(Job.objects.get(kind='n2_fiche').status, Job.DONE)
        self.assertFalse(pot_service.lourd_en_cours())
        self.assertTrue(pot_service.a_jour(self.b, self.e, 'N2'))
        with N2_BUILD, mock.patch('api.calc_service.run_n2') as again:
            self.assertEqual(pot_service.calculer(self.b, self.e, 'N2').run_id, pot.run_id)
        again.assert_not_called()
        # Lu par la vue bâtiment comme un N2 terminé.
        j = client_for(sub='u').get(f'/api/batiments/{self.b.pk}/potentiels/').json()
        n2 = next(x for x in j['bouquets'] if x['bouquet']['id'] == self.e.pk and x['niveau'] == 'N2')
        self.assertEqual((n2['statut'], n2['a_jour'], n2['indicateurs']['heating_kwh_m2']), ('fait', True, 42.0))

    def test_n2_en_erreur(self):
        with N2_BUILD, mock.patch('api.calc_service.run_n2', side_effect=RuntimeError('panne')):
            pot = pot_service.calculer(self.b, self.e, 'N2')
        self.assertEqual(pot.statut, 'erreur')
        # Message fixe en tête (`net.public_message`) ; la cause entre parenthèses vient de `net.reason`.
        self.assertTrue(pot.message.startswith('Erreur inattendue pendant le calcul.'), pot.message)
        self.assertEqual(Job.objects.get(kind='n2_fiche').status, Job.ERROR)
        self.assertFalse(pot_service.lourd_en_cours())               # le verrou est rendu
        self.assertEqual(CalcRun.objects.get(level='N2').status, Job.ERROR)

    def test_n2_manuel_reste_reserve_au_gestionnaire(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        self.b.portfolio = p
        self.b.save()
        r = client_for(sub='l').post(f'/api/batiments/{self.b.pk}/potentiels/', {'niveau': 'N2'}, format='json')
        self.assertEqual(r.status_code, 403)


@patch_jwks()
class ParcNiveauxTest(TestCase):
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
        self.g = client_for(sub='g')

    def _run_queue(self, limite=50):
        out = []
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async') as again:
            for _ in range(limite):
                res = taches.potentiels_parc_suivant.run(self.p.pk, 'g')
                out.append(res)
                if res in ('fini', 'occupe'):
                    break
        return out, again

    def test_n0_agregats_exacts_et_niveau_en_tete(self):
        e = lab('energie')
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async'):
            r = self.g.post(self.url, {'niveau': 'N0', 'bouquets': [e.pk]}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(list(r.json())[0], 'niveau')
        self.assertEqual(r.json()['file'], {'en_attente': 3, 'en_cours': 0})
        self._run_queue()
        j = client_for(sub='l').get(self.url + '?niveau=N0').json()
        self.assertEqual((list(j)[0], j['niveau']), ('niveau', 'N0'))
        self.assertEqual(j['file'], {'en_attente': 0, 'en_cours': 0})
        key = str(e.pk)
        vals = [row['potentiels'][key]['indicateurs']['heating_kwh'] for row in j['batiments']]
        ag = j['agregats'][key]['heating_kwh']
        self.assertEqual(ag['n'], 3)
        self.assertAlmostEqual(ag['somme'], sum(vals), places=2)
        self.assertAlmostEqual(ag['moyenne'], sum(vals) / 3, places=2)
        self.assertAlmostEqual(ag['mediane'], statistics.median(vals), places=2)
        for row in j['batiments']:
            self.assertEqual(row['reel']['niveau'], 'N0')
            self.assertTrue(row['potentiels'][key]['a_jour'])
            self.assertEqual(CalcRun.objects.get(pk=row['reel']['run_id']).level, 'N0')
        reel = [row['reel']['indicateurs']['heating_kwh'] for row in j['batiments']]
        self.assertAlmostEqual(j['agregats']['reel']['heating_kwh']['somme'], sum(reel), places=2)
        # Le N1 du parc n'a rien : niveau par défaut N1, potentiels jamais calculés, agrégats vides du bouquet.
        j1 = client_for(sub='l').get(self.url).json()
        self.assertEqual(j1['niveau'], 'N1')
        self.assertTrue(all(row['potentiels'][key]['statut'] is None for row in j1['batiments']))
        self.assertEqual(j1['agregats'][key], {})
        self.assertEqual(client_for(sub='l').get(self.url + '?niveau=N5').status_code, 400)

    def test_file_n2_occupee_puis_calculee(self):
        e = lab('energie')
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async'):
            r = self.g.post(self.url, {'niveau': 'N2', 'bouquets': [e.pk]}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json()['file']['en_attente'], 3)
        lourd = Job.objects.create(kind='n2_fiche', status=Job.RUNNING)
        out, again = self._run_queue()
        self.assertEqual(out, ['occupe'])
        self.assertEqual(again.call_args.kwargs['countdown'], taches.ATTENTE_LOURD_S)
        self.assertEqual(again.call_args.kwargs['queue'], 'parc')
        self.assertEqual(Potentiel.objects.filter(niveau='N2', statut='en_attente').count(), 3)
        lourd.set_state(status=Job.DONE)
        with N2_BUILD, mock.patch('api.calc_service.run_n2', side_effect=faux_n2()):
            out, _ = self._run_queue()
        self.assertEqual(out[-1], 'fini')
        j = client_for(sub='l').get(self.url + '?niveau=N2').json()
        key = str(e.pk)
        self.assertEqual(j['agregats'][key]['heating_kwh_m2'], {'somme': None, 'moyenne': 42.0, 'mediane': 42.0, 'n': 3})
        self.assertTrue(all(row['potentiels'][key]['statut'] == 'fait' for row in j['batiments']))

    def test_n0_et_n1_passent_avant_n2(self):
        e = lab('energie')
        with mock.patch.object(taches.potentiels_parc_suivant, 'apply_async'):
            self.g.post(self.url, {'niveau': 'N2', 'bouquets': [e.pk]}, format='json')
            self.g.post(self.url, {'niveau': 'N0', 'bouquets': [e.pk]}, format='json')
        first = pot_service.next_pending(self.p.pk)
        self.assertEqual(first.niveau, 'N0')
