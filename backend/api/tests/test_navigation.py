"""Chantier bt-navigation — réglages de parc (et leur prise en compte réelle), tableau enrichi, création
dans un parc, catalogue des moteurs, documents d'un parc, fiche d'identité, parc d'un bâtiment.
Aucun appel réseau : sources, météo et chaîne sont simulées comme dans les autres tests."""

from unittest import mock

from django.test import TestCase

from .. import archetypes, facts, parc, pipeline, questionnaire, reglages, scenarios as sc_service
from ..calc_service import ENGINE_VERSION
from ..models import (AIExtractionRun, Building, BuildingDocument, CalcRun, FactProposal, Portfolio, PortfolioMember,
                      Scenario)
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment
from .test_fiche_auto import SOURCES, fake_ign
from .test_parc import WEATHER, fake_init


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


YEAR = synthetic_year()


def set_reglages(portfolio, section, surcharges):
    setattr(portfolio, section, reglages.validate(section, surcharges))
    portfolio.save(update_fields=[section])


class ParcCase(TestCase):
    """Un parc (gestionnaire `g`), un lecteur `l`, un étranger `o`, deux bâtiments du parc et un commun."""

    def setUp(self):
        jwks = patch_jwks()
        jwks.start()
        self.addCleanup(jwks.stop)
        self.p = Portfolio.objects.create(name='Parc', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role=PortfolioMember.GESTIONNAIRE_PARC)
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role=PortfolioMember.LECTEUR)
        self.g, self.l, self.o = client_for(sub='g'), client_for(sub='l'), client_for(sub='o')
        self.b1 = Building.objects.create(name='b1', portfolio=self.p, georef_lat=47.39, georef_lon=0.69)
        self.b2 = Building.objects.create(name='b2', portfolio=self.p)
        self.common = Building.objects.create(name='commun', georef_lat=47.39, georef_lon=0.69)


# ── § 2.3 : réglages ─────────────────────────────────────────────────────────

class ReglagesApiTest(ParcCase):
    def url(self, section):
        return f'/api/parcs/{self.p.pk}/reglages/{section}/'

    def test_get_shape_for_each_section(self):
        for section in reglages.SECTIONS:
            r = self.l.get(self.url(section))
            self.assertEqual(r.status_code, 200, r.content)
            d = r.json()
            self.assertEqual(set(d), {'section', 'peut_modifier', 'schema', 'defauts', 'surcharges', 'valeurs'})
            self.assertEqual(d['section'], section)
            self.assertFalse(d['peut_modifier'])
            self.assertEqual(d['surcharges'], {})
            self.assertEqual(d['valeurs'], d['defauts'])
            for e in d['schema']:
                self.assertTrue({'cle', 'libelle', 'aide', 'type', 'unite', 'min', 'max', 'choix', 'groupe', 'modeles',
                                 'applique'} <= set(e), e)
                self.assertIn(e['cle'], d['defauts'])
        self.assertTrue(self.g.get(self.url('rendu')).json()['peut_modifier'])

    def test_defaults_are_the_current_constants(self):
        d = reglages.defaults('parametrage')
        self.assertEqual(d['isolants.lambda_mur'], questionnaire.LAMBDA_INSULATION)
        self.assertEqual(d['bouquets.ite.epaisseur_cm'], sc_service.BUNDLES['ite']['answers']['murs_epaisseur'])
        self.assertEqual(d['menuiseries.double_recent.uw'], questionnaire.WINDOWS['double_recent'][1])
        self.assertEqual(d['archetypes.1990_2000.mur_u'], archetypes.PERIODS[6][2])
        self.assertEqual(d['ventilation.vmc_df.recup'], archetypes.VENTILATION_DEFAULT['vmc_df'][1])
        self.assertEqual(d['rendements.pac_air_eau'], archetypes.GENERATOR_EFFICIENCY['pac_air_eau'])
        o = reglages.defaults('orchestration')
        self.assertEqual(o['bdtopo.rayon_voisins_m'], pipeline.DEFAULT_RADIUS_M)
        self.assertTrue(o['bdnb.actif'] and o['calcul.n0'] and o['calcul.n1'])

    def test_unbranched_entries_say_so(self):
        # Depuis le 2026-11-16, tout l'orchestrateur est branché (environnement compris : test_environnement_orchestre).
        flags = {e['cle']: e['applique'] for e in reglages.schema('orchestration')}
        self.assertTrue(flags['environnement.mode'] and flags['bdnb.actif'])

    def test_put_by_manager_then_reset(self):
        r = self.g.put(self.url('parametrage'), {'surcharges': {'isolants.lambda_mur': 0.03}}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['valeurs']['isolants.lambda_mur'], 0.03)
        self.assertEqual(r.json()['surcharges'], {'isolants.lambda_mur': 0.03})
        self.assertEqual(self.l.get(self.url('parametrage')).json()['valeurs']['isolants.lambda_mur'], 0.03)
        r = self.g.put(self.url('parametrage'), {'surcharges': {}}, format='json')
        self.assertEqual(r.json()['valeurs']['isolants.lambda_mur'], questionnaire.LAMBDA_INSULATION)

    def test_rights(self):
        body = {'surcharges': {'parc.titre': 'Mon parc'}}
        self.assertEqual(self.l.put(self.url('rendu'), body, format='json').status_code, 403)
        self.assertEqual(self.o.get(self.url('rendu')).status_code, 404)
        self.assertEqual(self.o.put(self.url('rendu'), body, format='json').status_code, 404)
        self.assertEqual(self.g.get(f'/api/parcs/{self.p.pk}/reglages/inconnue/').status_code, 404)
        self.assertEqual(self.g.put(self.url('rendu'), body, format='json').status_code, 200)

    def test_validation_is_strict_with_fixed_messages(self):
        bad = [{'zz_evil<script>': 1}, {'isolants.lambda_mur': 5}, {'isolants.lambda_mur': True},
               {'isolants.lambda_mur': 'zz_evil'}, {'bdtopo.rayon_voisins_m': 60.5}, {'meteo.type': 'zz_evil'},
               {'parc.colonnes': ['zz_evil']}, {'parc.colonnes': ['usage', 'usage']}, {'bdnb.actif': 'non'},
               {'parc.titre': 'x' * 200}, {'meteo.annee': 2999}]
        sections = ['orchestration', 'parametrage', 'parametrage', 'parametrage', 'orchestration', 'orchestration',
                    'rendu', 'rendu', 'orchestration', 'rendu', 'orchestration']
        for s, sur in zip(sections, bad):
            r = self.g.put(self.url(s), {'surcharges': sur}, format='json')
            self.assertEqual(r.status_code, 400, (s, sur))
            self.assertNotIn('zz_evil', r.content.decode())
        self.assertEqual(self.g.put(self.url('rendu'), {'x': 1}, format='json').status_code, 400)
        self.assertEqual(self.g.put(self.url('rendu'), {'surcharges': [1]}, format='json').status_code, 400)
        self.p.refresh_from_db()
        self.assertEqual((self.p.orchestration, self.p.parametrage, self.p.rendu), ({}, {}, {}))

    def test_nullable_year_and_text_cleaned(self):
        r = self.g.put(self.url('orchestration'), {'surcharges': {'meteo.type': 'reel', 'meteo.annee': None}}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        r = self.g.put(self.url('rendu'), {'surcharges': {'parc.titre': ' Parc\u0007 A '}}, format='json')
        self.assertEqual(r.json()['valeurs']['parc.titre'], 'Parc A')

    def test_outside_a_parc_nothing_is_read(self):
        self.assertEqual(reglages.for_building(self.common, 'parametrage'), {})


# ── Prise en compte : paramétrage ────────────────────────────────────────────

class ParametrageEffectTest(ParcCase):
    def test_lambda_changes_wall_u(self):
        set_reglages(self.p, 'parametrage', {'isolants.lambda_mur': 0.022})
        cfg = reglages.for_building(self.b1, 'parametrage')
        tuned = {k: v for k, v, _ in questionnaire.answers_to_facts({'murs': 'ite', 'murs_epaisseur': 12}, {}, cfg)}
        default = {k: v for k, v, _ in questionnaire.answers_to_facts({'murs': 'ite', 'murs_epaisseur': 12}, {})}
        self.assertLess(tuned['mur.u'], default['mur.u'])

    def test_lambda_roof_iti_windows_ventilation_generator(self):
        set_reglages(self.p, 'parametrage', {
            'isolants.lambda_toiture': 0.03, 'isolants.majoration_iti_pct': 0, 'menuiseries.double_recent.uw': 1.1,
            'menuiseries.double_recent.g': 0.4, 'ventilation.vmc_df.recup': 0.9, 'rendements.pac_air_eau': 3.6})
        cfg = reglages.for_building(self.b1, 'parametrage')
        ans = {'toiture': 20, 'murs': 'iti', 'murs_epaisseur': 10, 'fenetres': 'double_recent', 'ventilation': 'vmc_df',
               'chauffage': 'pac_air_eau'}
        tuned = {k: v for k, v, _ in questionnaire.answers_to_facts(ans, {}, cfg)}
        default = {k: v for k, v, _ in questionnaire.answers_to_facts(ans, {})}
        self.assertLess(tuned['toiture.u'], default['toiture.u'])
        self.assertAlmostEqual(default['mur.u'] / tuned['mur.u'], 1.10, places=2)   # majoration ITI retirée
        self.assertEqual((tuned['baies.uw'], tuned['baies.g']), (1.1, 0.4))
        self.assertEqual(tuned['ventilation.rendement_recup'], 0.9)
        self.assertEqual(tuned['systeme.rendement_generation'], 3.6)
        self.assertEqual(default['systeme.rendement_generation'], archetypes.GENERATOR_EFFICIENCY['pac_air_eau'])

    def test_questionnaire_api_uses_parc_settings(self):
        set_reglages(self.p, 'parametrage', {'menuiseries.triple.uw': 0.6})
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'}):
            r = self.g.post(f'/api/batiments/{self.b1.pk}/questionnaire/', {'answers': {'fenetres': 'triple'}}, format='json')
            rc = client_for(sub='u').post(f'/api/batiments/{self.common.pk}/questionnaire/',
                                          {'answers': {'fenetres': 'triple'}}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(facts.effective_values(self.b1)['baies.uw'], 0.6)
        self.assertEqual(rc.status_code, 200, rc.content)
        self.assertEqual(facts.effective_values(self.common)['baies.uw'], questionnaire.WINDOWS['triple'][1])

    def _scenario(self, client, building, bundles):
        return client.post(f'/api/batiments/{building.pk}/scenarios/', {'name': 'v', 'bundles': bundles}, format='json')

    def test_inactive_bundle_refused_and_hidden(self):
        set_reglages(self.p, 'parametrage', {'bouquets.pac.actif': False})
        r = self._scenario(self.g, self.b1, ['pac'])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Scenario.objects.filter(building=self.b1).exists())
        self.assertEqual(self._scenario(client_for(sub='u'), self.common, ['pac']).status_code, 201)   # hors parc
        keys = [b['key'] for b in self.l.get(f'/api/scenarios/bouquets/?parc={self.p.pk}').json()['bundles']]
        self.assertNotIn('pac', keys)
        self.assertIn('pac', [b['key'] for b in self.l.get('/api/scenarios/bouquets/').json()['bundles']])
        self.assertEqual(self.o.get(f'/api/scenarios/bouquets/?parc={self.p.pk}').status_code, 404)

    def test_bundle_thickness_changes_label_and_u(self):
        set_reglages(self.p, 'parametrage', {'bouquets.ite.epaisseur_cm': 20})
        rows = {b['key']: b for b in self.g.get(f'/api/scenarios/bouquets/?parc={self.p.pk}').json()['bundles']}
        self.assertEqual(rows['ite']['label'], 'Isolation des murs par l’extérieur (20 cm)')
        self.assertEqual(rows['ite']['epaisseur_cm'], 20)
        sp = Scenario.objects.create(building=self.b1, name='p')
        sc = Scenario.objects.create(building=self.common, name='c')
        sc_service.apply_bundles(self.b1, sp, ['ite'])
        sc_service.apply_bundles(self.common, sc, ['ite'])
        up = facts.effective_values(self.b1, sp)['mur.u']
        uc = facts.effective_values(self.common, sc)['mur.u']
        self.assertLess(up, uc)
        self.assertEqual(facts.effective_values(self.b1, sp)['mur.isolation_epaisseur_m'], 0.2)


# ── Prise en compte : orchestration et archétypes (chaîne A1 → A8) ───────────

class OrchestrationEffectTest(ParcCase):
    def _init(self, building, extra=None):
        mocks = {k: mock.patch(k, return_value=v) for k, v in SOURCES.items()}
        mocks['ign'] = mock.patch('api.geodata.fetch_ign_buildings', side_effect=fake_ign)
        mocks.update(extra or {})
        started = {k: m.start() for k, m in mocks.items()}
        try:
            return pipeline.initialize(building), started
        finally:
            for m in mocks.values():
                m.stop()

    def _b(self):
        n = Building.objects.filter(portfolio=self.p).count()
        return Building.objects.create(name=f'init {n}', portfolio=self.p, address='1 rue Exemple Tours')

    def test_bdnb_off_skips_a2(self):
        set_reglages(self.p, 'orchestration', {'bdnb.actif': False})
        b = self._b()
        report, m = self._init(b)
        m['api.sources.bdnb_for_rnb'].assert_not_called()
        self.assertTrue(report['steps']['A2'].get('skipped'))
        b.refresh_from_db()
        self.assertEqual(b.pipeline_status, 'complet')

    def test_bdtopo_off_skips_a3(self):
        set_reglages(self.p, 'orchestration', {'bdtopo.actif': False})
        report, m = self._init(self._b())
        m['ign'].assert_not_called()
        self.assertTrue(report['steps']['A3'].get('skipped'))
        m['api.sources.bdnb_for_rnb'].assert_called_once()

    def test_radius_and_rnb_lookup(self):
        set_reglages(self.p, 'orchestration', {'bdtopo.rayon_voisins_m': 120})
        _r, m = self._init(self._b(), {'fp': mock.patch('api.pipeline.fetch_footprints', return_value=None)})
        self.assertEqual(m['fp'].call_args.args[2], 120)
        set_reglages(self.p, 'orchestration', {'identification.rnb_auto': False})
        report, m = self._init(self._b())
        m['api.sources.rnb_closest'].assert_not_called()
        self.assertFalse(report['steps']['A2']['ok'])   # sans RNB, pas de fiche nationale

    def test_outside_parc_chain_unchanged(self):
        b = Building.objects.create(name='hors', address='1 rue Exemple Tours')
        set_reglages(self.p, 'orchestration', {'bdnb.actif': False})
        report, m = self._init(b)
        m['api.sources.bdnb_for_rnb'].assert_called_once()
        self.assertNotIn('skipped', report['steps']['A2'])

    def test_archetype_ventilation_and_generator_values_at_a5(self):
        # 1992 (BDNB) → période 1990-2000 ; maison 1992 → VMC simple flux autoréglable.
        set_reglages(self.p, 'parametrage', {'archetypes.1990_2000.n50': 3.3, 'ventilation.vmc_sf_auto.debit': 0.9,
                                             'rendements.chaudiere_gaz': 0.9})
        b = self._b()
        self._init(b)
        defaut = {f.key: f.value for f in b.facts.filter(method='defaut', superseded_by__isnull=True)}
        self.assertEqual(defaut['ventilation.n50'], 3.3)
        self.assertEqual(defaut['ventilation.debit_vol_h'], 0.9)
        self.assertEqual(defaut['systeme.rendement_generation'], 0.9)
        hors = Building.objects.create(name='hors', address='1 rue Exemple Tours')
        self._init(hors)
        d2 = {f.key: f.value for f in hors.facts.filter(method='defaut', superseded_by__isnull=True)}
        self.assertEqual(d2['ventilation.n50'], archetypes.PERIODS[6][6])

    def _drain(self):
        with mock.patch('api.pipeline.initialize', side_effect=fake_init), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            Building.objects.filter(pk=self.b1.pk).update(pipeline_status='en_attente')
            b = parc.next_building(self.p.pk)
            parc.process_one(b, owner_sub='g')
        return Building.objects.get(pk=b.pk)

    def test_automatic_levels_follow_settings(self):
        set_reglages(self.p, 'orchestration', {'calcul.n0': False})
        b = self._drain()
        self.assertFalse(CalcRun.objects.filter(building=b, level='N0').exists())
        self.assertTrue(CalcRun.objects.filter(building=b, level='N1').exists())
        set_reglages(self.p, 'orchestration', {'calcul.n0': False, 'calcul.n1': False})
        CalcRun.objects.all().delete()
        b = self._drain()
        self.assertFalse(CalcRun.objects.filter(building=b).exists())
        self.assertTrue(b.pipeline['steps']['A8'].get('skipped'))

    def test_real_year_weather_by_default(self):
        set_reglages(self.p, 'orchestration', {'meteo.type': 'reel', 'meteo.annee': 2020})
        with mock.patch('api.weather_source.build_weather_series', return_value=(YEAR, 0)) as real:
            b = self._drain()
        self.assertEqual(real.call_args.args[2:], ('2020-01-01', '2020-12-31'))
        self.assertEqual(b.weather_dataset.kind, 'reel')
        run = CalcRun.objects.get(building=b, level='N1')
        self.assertEqual(run.weather.get('dataset_id'), b.weather_dataset_id)
        self.assertTrue(b.pipeline['steps']['A7']['ok'])

    def test_task_after_init_follows_settings(self):
        from .. import tasks
        from ..models import Job
        set_reglages(self.p, 'orchestration', {'calcul.n1': False})
        job = Job.objects.create(kind='init_building', owner_sub='g')
        with mock.patch('api.pipeline.initialize', side_effect=fake_init), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            tasks.init_building.apply(args=[job.pk, self.b1.pk])
        self.assertTrue(CalcRun.objects.filter(building=self.b1, level='N0').exists())
        self.assertFalse(CalcRun.objects.filter(building=self.b1, level='N1').exists())


# ── § 2.1 : tableau enrichi ──────────────────────────────────────────────────

class TableauTest(ParcCase):
    def test_rows_carry_depth_and_models(self):
        facts.record(self.b1, 'mur.u', 1.4, 'defaut', source='archétype')
        rows = {r['id']: r for r in self.l.get(f'/api/parcs/{self.p.pk}/tableau/').json()['rows']}
        self.assertEqual(rows[self.b1.pk]['approfondissement']['niveau'], 'automatique')
        self.assertEqual(rows[self.b1.pk]['modeles'], [])
        with mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            self.g.post(f'/api/batiments/{self.b1.pk}/calculer/', {'niveaux': ['N0', 'N1']}, format='json')
        row = {r['id']: r for r in self.l.get(f'/api/parcs/{self.p.pk}/tableau/').json()['rows']}[self.b1.pk]
        self.assertEqual([m['niveau'] for m in row['modeles']], ['N0', 'N1'])
        self.assertEqual(row['modeles'][1]['libelle'], 'Horaire simplifié')
        self.assertTrue(all(m['a_jour'] for m in row['modeles']))
        self.g.post(f'/api/batiments/{self.b1.pk}/faits/', {'key': 'mur.u', 'value': 0.4}, format='json')
        row = {r['id']: r for r in self.l.get(f'/api/parcs/{self.p.pk}/tableau/').json()['rows']}[self.b1.pk]
        self.assertFalse(any(m['a_jour'] for m in row['modeles']))
        self.assertEqual(row['approfondissement']['niveau'], 'complete')
        self.assertIn('1 valeur déclarée', row['approfondissement']['detail'])

    def test_depth_order(self):
        b = self.b2
        self.assertEqual(parc.approfondissement(b)['niveau'], 'automatique')
        facts.record(b, 'mur.u', 0.5, 'declare', author_sub='g')
        self.assertEqual(parc.approfondissement(b)['niveau'], 'complete')
        facts.record(b, 'toiture.u', 0.3, 'document', author_sub='g')
        self.assertEqual(parc.approfondissement(b)['niveau'], 'documente')
        facts.record(b, 'plancher.u', 0.6, 'infere', source='calage sur factures (CV(RMSE) 5 %, NMBE 1 %)')
        self.assertEqual(parc.approfondissement(b)['niveau'], 'mesure')
        b.envelope = {'triangles': [{'v': [0, 1, 2], 'paroi_model_id': 1}]}
        b.save()
        self.assertEqual(parc.approfondissement(b)['niveau'], 'expert')
        c = Building.objects.create(name='m', portfolio=self.p)
        facts.record(c, 'ventilation.n50', 3.0, 'mesure', author_sub='g')
        self.assertEqual(parc.approfondissement(c)['libelle'], 'Mesuré')


# ── § 2.2 : création dans un parc ────────────────────────────────────────────

class InitInParcTest(ParcCase):
    def test_only_a_parc_manager_creates_in_the_parc(self):
        body = {'address': '1 rue Exemple Tours', 'portfolio': self.p.pk}
        with mock.patch('api.tasks.init_building.delay'):
            r = self.g.post('/api/batiments/initialiser/', body, format='json')
            self.assertEqual(r.status_code, 202, r.content)
            self.assertEqual(set(r.json()), {'building_id', 'job'})
            self.assertEqual(Building.objects.get(pk=r.json()['building_id']).portfolio_id, self.p.pk)
            self.assertEqual(self.l.post('/api/batiments/initialiser/', body, format='json').status_code, 403)
            self.assertEqual(self.o.post('/api/batiments/initialiser/', body, format='json').status_code, 403)
            r = self.o.post('/api/batiments/initialiser/', {'address': '2 rue Exemple Tours'}, format='json')
            self.assertEqual(r.status_code, 202)
            self.assertIsNone(Building.objects.get(pk=r.json()['building_id']).portfolio_id)


# ── § 2.4 : moteurs ──────────────────────────────────────────────────────────

class MoteursTest(ParcCase):
    def test_catalogue(self):
        r = self.o.get('/api/calculs/moteurs/')
        self.assertEqual(r.status_code, 200)
        rows = {m['id']: m for m in r.json()['results']}
        self.assertEqual(list(rows), ['N0', 'N1', 'N2', '1D', '3D'])
        self.assertEqual((rows['1D']['expert_route'], rows['3D']['expert_route']), ('/calcul-1d', '/calcul-3d'))
        self.assertEqual(rows['N1']['version'], ENGINE_VERSION['N1'])
        self.assertEqual(rows['N2']['version'], ENGINE_VERSION['N2'])
        for m in rows.values():
            self.assertTrue({'libelle', 'resume', 'methode', 'hypotheses', 'entrees', 'sorties', 'duree', 'qui',
                             'limites', 'version', 'expert_route'} <= set(m))
            self.assertTrue(m['resume'] and m['methode'])


# ── § 2.5 : documents d'un parc ──────────────────────────────────────────────

class ParcDocumentsTest(ParcCase):
    def test_documents_filtered_like_the_building_route(self):
        d = BuildingDocument.objects.create(building=self.b1, owner_sub='occ', kind='texte', filename='dpe.txt',
                                            pages=['texte'])
        run = AIExtractionRun.objects.create(document=d, owner_sub='occ', provider='local', status='DONE')
        FactProposal.objects.create(run=run, building=self.b1, key='mur.u', value=0.3, control='ok')
        FactProposal.objects.create(run=run, building=self.b1, key='toiture.u', value=0.3, control='ok', decision='acceptee')
        r = self.g.get(f'/api/parcs/{self.p.pk}/documents/')
        self.assertEqual(r.status_code, 200)
        rows = {x['building_id']: x for x in r.json()['results']}
        self.assertEqual(set(rows), {self.b1.pk, self.b2.pk})
        self.assertEqual([x['filename'] for x in rows[self.b1.pk]['documents']], ['dpe.txt'])
        self.assertEqual(rows[self.b1.pk]['propositions_en_attente'], 1)
        self.assertEqual(rows[self.b1.pk]['building_name'], 'b1')
        same = self.g.get(f'/api/batiments/{self.b1.pk}/documents/').json()['results']
        self.assertEqual(rows[self.b1.pk]['documents'], same)
        lr = {x['building_id']: x for x in self.l.get(f'/api/parcs/{self.p.pk}/documents/').json()['results']}
        self.assertEqual((lr[self.b1.pk]['documents'], lr[self.b1.pk]['propositions_en_attente']), ([], 0))
        self.assertEqual(self.o.get(f'/api/parcs/{self.p.pk}/documents/').status_code, 404)


# ── § 2.6 : fiche d'identité ─────────────────────────────────────────────────

class IdentiteTest(ParcCase):
    def test_identity_aggregate(self):
        facts.record(self.b1, 'identite.usage', 'maison', 'declare', author_sub='g')
        facts.record(self.b1, 'mur.u', 1.4, 'defaut', source='archétype')
        facts.record(self.b1, 'systeme.generateur', 'chaudiere_gaz', 'infere')
        with mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            self.g.post(f'/api/batiments/{self.b1.pk}/calculer/', {'niveaux': ['N1']}, format='json')
        Scenario.objects.create(building=self.b1, name='Isolation', weather={'bundles': ['ite']})
        r = self.l.get(f'/api/batiments/{self.b1.pk}/identite/')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(set(d), {'batiment', 'identite', 'enveloppe', 'systemes', 'meteo', 'environnement', 'resultats',
                                  'ameliorations', 'confiance', 'completude', 'approfondissement', 'sources',
                                  'geometrie', 'plancher'})
        self.assertEqual(d['batiment']['portfolio'], self.p.pk)
        self.assertEqual(d['identite']['identite.usage'], {'valeur': 'maison', 'methode': 'declare', 'libelle': 'Usage',
                                                           'unite': '', 'texte': 'Maison individuelle', 'source': ''})
        # Lot 34 : valeurs en mots, sol vu par le plancher, origine de la géométrie.
        self.assertEqual(d['systemes']['systeme.generateur']['texte'], facts.BY_KEY['systeme.generateur'].choices['chaudiere_gaz'])
        self.assertIn('t_moyenne_c', d['plancher'])
        self.assertEqual(d['geometrie']['source'], 'bdtopo')
        self.assertEqual(d['resultats']['N1']['limites'], [])
        self.assertIn('mur.u', d['enveloppe'])
        self.assertIn('systeme.generateur', d['systemes'])
        self.assertIsNone(d['resultats']['N0'])
        self.assertTrue(d['resultats']['N1']['a_jour'])
        self.assertIn('heating_kwh_m2', d['resultats']['N1'])
        self.assertEqual(d['ameliorations'][0]['bouquets'], ['ite'])
        self.assertIsNone(d['ameliorations'][0]['dernier'])
        self.assertEqual(d['meteo']['type'], 'tmy')
        self.assertIsNone(d['environnement'])
        self.assertEqual(d['approfondissement']['niveau'], 'complete')
        self.assertEqual(self.o.get(f'/api/batiments/{self.b1.pk}/identite/').status_code, 404)


# ── Amendement v1.1 : parc d'un bâtiment dans BuildingSerializer ─────────────

class BuildingPortfolioFieldTest(ParcCase):
    def test_portfolio_in_list_and_detail(self):
        rows = {b['id']: b for b in self.g.get('/api/batiments/').json()}
        self.assertEqual(rows[self.b1.pk]['portfolio'], self.p.pk)
        self.assertIsNone(rows[self.common.pk]['portfolio'])
        self.assertEqual(self.g.get(f'/api/batiments/{self.b1.pk}/').json()['portfolio'], self.p.pk)
        self.assertIsNone(self.g.get(f'/api/batiments/{self.common.pk}/').json()['portfolio'])
        r = self.g.patch(f'/api/batiments/{self.common.pk}/', {'portfolio': self.p.pk}, format='json')
        self.assertIn(r.status_code, (200, 400))
        self.assertIsNone(Building.objects.get(pk=self.common.pk).portfolio_id)   # lecture seule
