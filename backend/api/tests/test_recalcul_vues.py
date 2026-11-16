"""Chantier bt-trois-niveaux, lot 42 (secteur C) — état des calculs (I2) : formes exactes, statuts calculés par
empreinte, « Recalculer » réservé aux gestionnaires (403/404 fixes), état d'un parc sans requête par bâtiment."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .. import calc_service
from ..ameliorations import bouquets as bq_service
from ..models import Bouquet, Building, BuildingAccess, Portfolio, PortfolioMember, Recalcul
from ..recalcul import etat, execution as ex, taches
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment
from .test_calage import seeded

YEAR = synthetic_year(days=28)
_env = without_auto_environment()
_tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})

ETAT_KEYS = {'statut', 'cause', 'prevu_pour', 'message', 'peut_recalculer', 'calculs', 'calculs_restants'}
CALC_KEYS = {'etat', 'libelle', 'niveau', 'statut', 'run_id', 'calcule_le'}
RESUME_KEYS = {'batiments', 'a_jour', 'planifies', 'en_cours', 'erreurs', 'calculs_restants'}


def setUpModule():
    _env.start()
    _tmy.start()


def tearDownModule():
    _tmy.stop()
    _env.stop()


def by(e):
    return {(c['etat'], c['niveau']): c for c in e['calculs']}


class EtatBatimentTest(TestCase):
    def setUp(self):
        bq_service.ensure_defaults()
        self.bqs = list(Bouquet.objects.filter(portfolio__isnull=True).order_by('ordre', 'pk'))
        self.b = seeded()

    def test_forme_et_statuts(self):
        e = etat.etat_batiment(self.b)
        self.assertEqual(set(e), ETAT_KEYS)
        self.assertEqual((e['statut'], e['cause'], e['prevu_pour'], e['message'], e['peut_recalculer']),
                         ('inactif', '', None, '', False))
        self.assertEqual(len(e['calculs']), 3 * (1 + len(self.bqs)))
        for c in e['calculs']:
            self.assertEqual(set(c), CALC_KEYS)
        self.assertEqual([(c['etat'], c['niveau']) for c in e['calculs'][:4]],
                         [('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2'), (self.bqs[0].pk, 'N0')])
        self.assertEqual(e['calculs'][0]['libelle'], 'État actuel')
        self.assertEqual(e['calculs'][3]['libelle'], self.bqs[0].nom)
        self.assertEqual({c['statut'] for c in e['calculs']}, {'jamais'})
        self.assertEqual(e['calculs_restants'], len(e['calculs']))
        run, _ = calc_service.run_level(self.b, 'N1')
        c = by(etat.etat_batiment(self.b))[('reel', 'N1')]
        self.assertEqual((c['statut'], c['run_id']), ('a_jour', run.pk))
        self.assertIsNotNone(c['calcule_le'])
        from .. import facts
        facts.record(self.b, 'mur.u', 0.5, 'declare', author_sub='u')
        self.assertEqual(by(etat.etat_batiment(self.b))[('reel', 'N1')]['statut'], 'a_refaire')

    def test_en_file_en_cours_erreur_desactive(self):
        r = Recalcul.objects.create(building=self.b, statut='planifie', cause='donnée mur.u',
                                    prevu_pour=timezone.now() + timedelta(seconds=30))
        e = etat.etat_batiment(self.b)
        self.assertEqual((e['statut'], e['cause']), ('planifie', 'donnée mur.u'))
        self.assertIsNotNone(e['prevu_pour'])
        self.assertEqual({c['statut'] for c in e['calculs']}, {'en_file'})
        r.statut, r.etapes = 'en_cours', [{'etat': 'reel', 'niveau': 'N0', 'resultat': 'calcule'}]
        r.save()
        d = by(etat.etat_batiment(self.b))
        self.assertEqual((d[('reel', 'N1')]['statut'], d[('reel', 'N2')]['statut']), ('en_cours', 'en_file'))
        r.statut, r.message = 'erreur', ex.MESSAGE_ERREUR
        r.etapes = [{'etat': 'reel', 'niveau': 'N2', 'resultat': 'erreur'}]
        r.save()
        e = etat.etat_batiment(self.b)
        self.assertEqual((e['statut'], e['message'], e['prevu_pour']), ('erreur', ex.MESSAGE_ERREUR, None))
        self.assertEqual(by(e)[('reel', 'N2')]['statut'], 'erreur')

    def test_reglages_coupes(self):
        p = Portfolio.objects.create(name='p', owner_sub='g', orchestration={'calcul.n2': False, 'calcul.potentiels': False})
        Building.objects.filter(pk=self.b.pk).update(portfolio=p)
        self.b.refresh_from_db()
        d = by(etat.etat_batiment(self.b))
        self.assertEqual(d[('reel', 'N2')]['statut'], 'desactive')
        self.assertEqual(d[('reel', 'N1')]['statut'], 'jamais')
        self.assertEqual(d[(self.bqs[0].pk, 'N1')]['statut'], 'desactive')


@patch_jwks()
class VuesTest(TestCase):
    def setUp(self):
        bq_service.ensure_defaults()
        self.p = Portfolio.objects.create(name='p', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role=PortfolioMember.GESTIONNAIRE_PARC)
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role=PortfolioMember.LECTEUR)
        self.b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69,
                                         pipeline_status='complet')
        BuildingAccess.objects.create(building=self.b, sub='o', role='occupant')
        self.u = f'/api/batiments/{self.b.pk}/calculs/'

    def test_lecture_et_droits(self):
        for sub, can in (('g', True), ('l', False), ('o', False)):
            r = client_for(sub=sub).get(self.u + 'etat/')
            self.assertEqual(r.status_code, 200, sub)
            self.assertEqual(set(r.json()), ETAT_KEYS)
            self.assertEqual(r.json()['peut_recalculer'], can, sub)
        r = client_for(sub='x').get(self.u + 'etat/')
        self.assertEqual((r.status_code, r.json()), (404, {'detail': 'Bâtiment introuvable.'}))

    def test_recalculer_reserve_aux_gestionnaires(self):
        for sub in ('l', 'o'):     # D2 : un humain non gestionnaire ne lance rien (le N2 en fait partie)
            r = client_for(sub=sub).post(self.u + 'recalculer/', {}, format='json')
            self.assertEqual((r.status_code, r.json()),
                             (403, {'detail': 'Action réservée aux gestionnaires de ce bâtiment.'}), sub)
        r = client_for(sub='x').post(self.u + 'recalculer/', {}, format='json')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Recalcul.objects.exists())

    def test_recalculer_sans_effet_en_tests_ordinaires(self):
        with mock.patch.object(taches.recalcul_batiment, 'apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                r = client_for(sub='g').post(self.u + 'recalculer/', {}, format='json')
        self.assertEqual(r.status_code, 202)
        self.assertEqual(set(r.json()), ETAT_KEYS)
        self.assertFalse(Recalcul.objects.exists())
        send.assert_not_called()

    @override_settings(RECALCUL_EN_TEST=True)
    def test_recalculer_replanifie_tout_de_suite(self):
        with mock.patch.object(taches.recalcul_batiment, 'apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                r = client_for(sub='g').post(self.u + 'recalculer/', {}, format='json')
        self.assertEqual(r.status_code, 202)
        j = r.json()
        self.assertEqual((j['statut'], j['cause'], j['peut_recalculer']), ('planifie', 'recalcul demandé', True))
        self.assertEqual(send.call_args.kwargs['countdown'], 0)

    def test_etat_du_parc(self):
        b2 = Building.objects.create(name='b2', portfolio=self.p, pipeline_status='complet')
        Recalcul.objects.create(building=self.b, statut='planifie')
        Recalcul.objects.create(building=b2, statut='inactif')
        r = client_for(sub='g').get(f'/api/parcs/{self.p.pk}/calculs/etat/')
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual(set(j), {'resume', 'batiments'})
        self.assertEqual(set(j['resume']), RESUME_KEYS)
        n = 3 * (1 + Bouquet.objects.filter(portfolio__isnull=True).count())
        self.assertEqual(j['resume'], {'batiments': 2, 'a_jour': 1, 'planifies': 1, 'en_cours': 0, 'erreurs': 0,
                                       'calculs_restants': n})
        self.assertEqual(j['batiments'], [{'id': self.b.pk, 'statut': 'planifie', 'calculs_restants': n},
                                          {'id': b2.pk, 'statut': 'inactif', 'calculs_restants': 0}])
        r = client_for(sub='x').get(f'/api/parcs/{self.p.pk}/calculs/etat/')
        self.assertEqual((r.status_code, r.json()), (404, {'detail': 'Parc introuvable.'}))
        # Un gestionnaire de bâtiment ne voit que les bâtiments qui lui sont confiés.
        PortfolioMember.objects.create(portfolio=self.p, sub='gb', role=PortfolioMember.GESTIONNAIRE_BAT)
        BuildingAccess.objects.create(building=b2, sub='gb', role='gestionnaire')
        j = client_for(sub='gb').get(f'/api/parcs/{self.p.pk}/calculs/etat/').json()
        self.assertEqual([x['id'] for x in j['batiments']], [b2.pk])
        self.assertEqual(j['resume']['batiments'], 1)


class ResumeParcTest(TestCase):
    def test_sans_requete_par_batiment(self):
        p = Portfolio.objects.create(name='p', owner_sub='g')
        for i in range(3):
            Building.objects.create(name=f'a{i}', portfolio=p)
        with self.assertNumQueries(3) as ctx:
            etat.resume_parc(p)
        for i in range(30):
            b = Building.objects.create(name=f'z{i}', portfolio=p)
            Recalcul.objects.create(building=b, statut=('erreur', 'en_cours', 'inactif')[i % 3],
                                    etapes=[{'etat': 'reel', 'niveau': 'N0', 'resultat': 'erreur'}])
        with self.assertNumQueries(len(ctx.captured_queries)):
            s = etat.resume_parc(p)
        self.assertEqual((s['batiments'], s['erreurs'], s['en_cours'], s['a_jour']), (33, 10, 10, 10))

    def test_demande_notee_sans_recalcul(self):
        p = Portfolio.objects.create(name='p', owner_sub='g')
        b = Building.objects.create(name='a', portfolio=p)
        now = timezone.now()
        Recalcul.objects.create(building=b, statut='inactif', termine_le=now - timedelta(minutes=1), demande_le=now)
        s = etat.resume_parc(p)
        self.assertEqual(s['a_jour'], 0)
        self.assertGreater(s['calculs_restants'], 0)
