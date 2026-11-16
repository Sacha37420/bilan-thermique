"""Lot 11 (refonte) — parcs : import, traitement automatique, tableau, export, cloisonnement, invitations."""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from .. import facts, parc, tasks
from ..models import Building, BuildingAccess, CalcRun, Invitation, Job, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


WEATHER = {'series': synthetic_year(days=60), 'source': 'synthétique'}
CSV = ('﻿Nom;Adresse;Usage;Surface;Année;Énergie\n'
       'Mairie;1 place Jean Jaurès 37000 Tours;bureau;1200;1880;gaz\n'
       'École;3 rue Émile Zola 37000 Tours;école;800;1965;fioul\n'
       ';;;;;\n'
       'Sans lieu;;maison;;;\n')


def fake_init(building, progress=None):
    """Chaîne simulée : position, quelques faits automatiques, statut complet."""
    building.georef_lat, building.georef_lon = 47.39, 0.69
    building.pipeline_status = 'complet'
    building.pipeline = {**(building.pipeline or {}), 'steps': {'A1': {'ok': True}}}
    building.save()
    facts.record(building, 'mur.u', 1.4, 'defaut', source='archétype')
    return {'steps': {'A1': {'ok': True}}}


class CsvImportTest(TestCase):
    def test_parse_french_headers_and_errors(self):
        rows, errors = parc.parse_csv(CSV)
        self.assertEqual([r['name'] for r in rows], ['Mairie', 'École'])
        self.assertEqual(errors, [{'line': 5, 'error': 'Ni adresse, ni RNB, ni position.'}])
        self.assertEqual(rows[0]['surface'], '1200')

    def test_requires_a_location_column(self):
        with self.assertRaises(parc.ImportError_):
            parc.parse_csv('nom;surface\nA;100\n')

    def test_comma_separator_lat_lon_and_rnb(self):
        rows, errors = parc.parse_csv('name,lat,lon,rnb\nA,47.39,0.69,abcd-1234-efgh\nB,60,0,\n')
        self.assertEqual(rows[0]['rnb_id'], 'ABCD1234EFGH')
        self.assertEqual(len(errors), 1)  # B hors de France

    def test_import_creates_buildings_with_declared_facts(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        rows, _ = parc.parse_csv(CSV)
        created = parc.import_rows(p, rows, 'g', 'gestion')
        self.assertEqual(len(created), 2)
        eff = facts.effective(created[1])
        self.assertEqual((eff['identite.usage'].value, eff['identite.usage'].method), ('enseignement', 'declare'))
        self.assertEqual(eff['systeme.generateur'].value, 'chaudiere_fioul')
        self.assertEqual(eff['identite.annee_construction'].value, 1965)
        self.assertEqual(created[0].pipeline_status, 'en_attente')


class ParcProcessingTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P', owner_sub='g')
        rows, _ = parc.parse_csv(CSV)
        self.bs = parc.import_rows(self.p, rows, 'g', 'gestion')

    def _drain(self):
        with mock.patch('api.pipeline.initialize', side_effect=fake_init), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            while True:
                b = parc.next_building(self.p.pk)
                if b is None:
                    break
                parc.process_one(b, owner_sub='g')

    def test_whole_parc_processed_without_intervention(self):
        self._drain()
        self.assertEqual(parc.counts(self.p)['complet'], 2)
        run = CalcRun.objects.get(building=self.bs[0], level='N1')
        ind = run.indicators
        self.assertLessEqual(ind['heating_kwh_m2_p10'], ind['heating_kwh_m2'])
        self.assertGreaterEqual(ind['heating_kwh_m2_p90'], ind['heating_kwh_m2'])
        self.assertTrue(CalcRun.objects.filter(building=self.bs[0], level='N0').exists())
        rows = parc.table(self.p, Building.objects.filter(portfolio=self.p))
        self.assertIsNotNone(rows[0]['heating_kwh_m2'])
        self.assertIsNotNone(rows[0]['p90'])
        self.assertGreater(rows[0]['confidence'], 0)

    def test_one_failure_does_not_stop_the_parc(self):
        def flaky(building, progress=None):
            if building.name == 'Mairie':
                raise RuntimeError('panne')
            return fake_init(building)
        with mock.patch('api.pipeline.initialize', side_effect=flaky), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            while (b := parc.next_building(self.p.pk)) is not None:
                parc.process_one(b)
        c = parc.counts(self.p)
        self.assertEqual((c['incomplet'], c['complet']), (1, 1))

    def test_stale_in_progress_building_is_resumed(self):
        Building.objects.filter(pk=self.bs[0].pk).update(pipeline_status='en_cours',
                                                         updated_at=timezone.now() - timedelta(hours=1))
        Building.objects.filter(pk=self.bs[1].pk).update(pipeline_status='complet')
        self.assertEqual(parc.next_building(self.p.pk).pk, self.bs[0].pk)

    def test_task_requeues_itself_on_parc_queue(self):
        with mock.patch('api.parc.process_one'), mock.patch('api.tasks.process_parc_next.apply_async') as again:
            tasks.process_parc_next.apply(args=[self.p.pk])
        self.assertEqual(again.call_args.kwargs['queue'], 'parc')

    def test_export_csv_escapes_formulas(self):
        Building.objects.filter(pk=self.bs[0].pk).update(name='=HYPERLINK("x")')
        text = parc.to_csv(parc.table(self.p, Building.objects.filter(portfolio=self.p)))
        self.assertIn("'=HYPERLINK", text)
        self.assertTrue(text.startswith('﻿id;nom;'))


class CeleryQueuesTest(TestCase):
    def test_interactive_queue_checked_first(self):
        self.assertEqual(settings.CELERY_BROKER_TRANSPORT_OPTIONS['queue_order_strategy'], 'priority')
        self.assertEqual(settings.CELERY_TASK_ROUTES['api.tasks.process_parc_next']['queue'], 'parc')
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)


@patch_jwks()
class ParcAccessTest(TestCase):
    """Cloisonnement entre utilisateurs (deux verrous de l'app : visibilité et droits par rôle)."""

    def setUp(self):
        jwks = patch_jwks()  # le décorateur de classe ne couvre pas setUp
        jwks.start()
        self.addCleanup(jwks.stop)
        self.g = client_for(sub='g', preferred_username='gestion')
        r = self.g.post('/api/parcs/', {'name': 'Parc A'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.pid = r.json()['id']
        self.p = Portfolio.objects.get(pk=self.pid)
        with mock.patch('api.tasks.process_parc_next.apply_async'):
            r = self.g.post(f'/api/parcs/{self.pid}/importer/', {'csv': CSV}, format='json')
            assert r.status_code == 201, r.content
        self.b1, self.b2 = Building.objects.filter(portfolio=self.p).order_by('name')
        self.common = Building.objects.create(name='commun')

    def _invite(self, role, building=None):
        r = self.g.post(f'/api/parcs/{self.pid}/invitations/', {'role': role, 'building_id': building}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()['code']

    def test_outsider_sees_nothing_of_the_parc(self):
        o = client_for(sub='o')
        self.assertEqual(o.get(f'/api/parcs/{self.pid}/').status_code, 404)
        self.assertEqual(o.get(f'/api/batiments/{self.b1.pk}/').status_code, 404)        # ancienne route
        self.assertEqual(o.get(f'/api/batiments/{self.b1.pk}/fiche/').status_code, 404)
        self.assertEqual(o.post(f'/api/batiments/{self.b1.pk}/faits/', {'key': 'mur.u', 'value': 1}, format='json').status_code, 404)
        ids = {b['id'] for b in o.get('/api/batiments/').json()}
        self.assertIn(self.common.pk, ids)        # bâtiment commun : inchangé (D4)
        self.assertNotIn(self.b1.pk, ids)
        self.assertEqual(o.get('/api/parcs/').json()['results'], [])

    def test_reader_reads_but_cannot_write(self):
        lecteur = client_for(sub='l', preferred_username='lecteur')
        lecteur.post('/api/invitations/accepter/', {'code': self._invite('lecteur')}, format='json')
        self.assertEqual(lecteur.get(f'/api/batiments/{self.b1.pk}/fiche/').status_code, 200)
        self.assertEqual(lecteur.post(f'/api/batiments/{self.b1.pk}/faits/', {'key': 'mur.u', 'value': 1}, format='json').status_code, 403)
        self.assertEqual(lecteur.post(f'/api/batiments/{self.b1.pk}/calculer/', {}, format='json').status_code, 403)
        self.assertEqual(lecteur.patch(f'/api/batiments/{self.b1.pk}/', {'description': 'x'}, format='json').status_code, 403)
        self.assertEqual(lecteur.post(f'/api/parcs/{self.pid}/importer/', {'csv': CSV}, format='json').status_code, 403)
        self.assertEqual(len(lecteur.get(f'/api/parcs/{self.pid}/tableau/').json()['rows']), 2)

    def test_building_manager_sees_only_entrusted_building(self):
        gb = client_for(sub='gb')
        gb.post('/api/invitations/accepter/', {'code': self._invite('gestionnaire_bat')}, format='json')
        self.assertEqual(gb.get(f'/api/parcs/{self.pid}/tableau/').json()['rows'], [])
        gb.post('/api/invitations/accepter/', {'code': self._invite('gestionnaire', self.b1.pk)}, format='json')
        rows = gb.get(f'/api/parcs/{self.pid}/tableau/').json()['rows']
        self.assertEqual([r['id'] for r in rows], [self.b1.pk])
        self.assertEqual(gb.get(f'/api/batiments/{self.b2.pk}/').status_code, 404)

    def test_occupant_declares_but_cannot_manage(self):
        occ = client_for(sub='occ', preferred_username='occupant')
        occ.post('/api/invitations/accepter/', {'code': self._invite('occupant', self.b1.pk)}, format='json')
        self.assertEqual(occ.post(f'/api/batiments/{self.b1.pk}/faits/', {'key': 'usage.consigne_chauffage_c', 'value': 20}, format='json').status_code, 201)
        self.assertEqual(occ.delete(f'/api/batiments/{self.b1.pk}/').status_code, 403)
        self.assertEqual(occ.post(f'/api/batiments/{self.b1.pk}/initialiser/').status_code, 403)
        self.assertEqual(occ.post(f'/api/batiments/{self.b1.pk}/calcul-3d/', {}, format='json').status_code, 403)
        self.assertEqual(occ.get(f'/api/parcs/{self.pid}/membres/').status_code, 403)

    def test_manager_can_retire_someone_elses_override_in_parc(self):
        occ = client_for(sub='occ')
        occ.post('/api/invitations/accepter/', {'code': self._invite('occupant', self.b1.pk)}, format='json')
        fid = occ.post(f'/api/batiments/{self.b1.pk}/faits/', {'key': 'mur.u', 'value': 0.3}, format='json').json()['fact']['id']
        self.assertEqual(self.g.delete(f'/api/faits/{fid}/').status_code, 200)

    def test_invitation_single_use_expiry_and_hash_only(self):
        code = self._invite('lecteur')
        self.assertFalse(Invitation.objects.filter(token_hash=code).exists())
        self.assertFalse(Invitation.objects.filter(prefix=code).exists())
        self.assertEqual(client_for(sub='a').post('/api/invitations/accepter/', {'code': code}, format='json').status_code, 200)
        self.assertEqual(client_for(sub='b').post('/api/invitations/accepter/', {'code': code}, format='json').status_code, 400)
        code2 = self._invite('lecteur')
        Invitation.objects.filter(uses=0).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(client_for(sub='c').post('/api/invitations/accepter/', {'code': code2}, format='json').status_code, 400)
        self.assertEqual(client_for(sub='d').post('/api/invitations/accepter/', {'code': 'btinv_faux'}, format='json').status_code, 400)

    def test_invitation_never_demotes_a_manager(self):
        self.g.post('/api/invitations/accepter/', {'code': self._invite('lecteur')}, format='json')
        self.assertEqual(PortfolioMember.objects.get(portfolio=self.p, sub='g').role, 'gestionnaire_parc')

    def test_last_manager_cannot_be_removed(self):
        mid = PortfolioMember.objects.get(portfolio=self.p, sub='g').pk
        self.assertEqual(self.g.delete(f'/api/parcs/{self.pid}/membres/{mid}/').status_code, 409)

    def test_members_list_exposes_names_not_subs(self):
        client_for(sub='zz-secret-sub', preferred_username='lea').post(
            '/api/invitations/accepter/', {'code': self._invite('lecteur')}, format='json')
        body = self.g.get(f'/api/parcs/{self.pid}/membres/').content.decode()
        self.assertIn('lea', body)
        self.assertNotIn('zz-secret-sub', body)

    def test_jobs_visible_only_to_their_author(self):
        mine = Job.objects.create(kind='init_building', owner_sub='g')
        legacy = Job.objects.create(kind='building_calcul')
        self.assertEqual(client_for(sub='o').get(f'/api/jobs/{mine.pk}/').status_code, 404)
        self.assertEqual(self.g.get(f'/api/jobs/{mine.pk}/').status_code, 200)
        self.assertEqual(client_for(sub='o').get(f'/api/jobs/{legacy.pk}/').status_code, 200)

    def test_attach_common_building(self):
        r = self.g.post(f'/api/parcs/{self.pid}/rattacher/', {'building_id': self.common.pk}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(client_for(sub='o').get(f'/api/batiments/{self.common.pk}/').status_code, 404)

    def test_export_endpoint(self):
        r = self.g.get(f'/api/parcs/{self.pid}/export.csv')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
        self.assertEqual(r.content.decode('utf-8-sig').count('\n'), 3)
