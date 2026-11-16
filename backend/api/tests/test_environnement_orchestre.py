"""Orchestrateur du parc — environnement observé (réglage `environnement.mode`) : généré pendant la chaîne
automatique (étape A6, avant N0/N1) ou au début d'un calcul détaillé (N2), relié au bâtiment, jamais
remplacé s'il existe ; un échec n'arrête rien. N2 depuis la fiche : réflexion du sol relevée.
Aucun appel réseau : la génération LiDAR est simulée (`environment_service.generate`)."""

from unittest import mock

import shapely.geometry as sg
from django.test import TestCase

from .. import calc_service, environment_service, geodata, observed_env as oe, parc, reglages, simulation, tasks
from ..models import Building, CalcRun, Environment, Job, Portfolio, PortfolioMember
from .test_n2_fiche import SQUARE, fiche, make_building, tmy
from .test_navigation import set_reglages
from .test_parc import WEATHER, fake_init

ALBEDO = {'half': 60.0, 'cell': 10.0, 'n': 12, 'values': [0.35] * 144}


def fake_generate(params, building=None, progress_cb=None, self_polygon=None):
    """Environnement relevé simulé : le bâtiment étudié et un voisin haut au sud, avec un albédo de sol."""
    if progress_cb:
        progress_cb('lidar-read', 20)
        progress_cb('buildings', 60)
    objs = []
    for k, (box, h) in enumerate([(sg.box(0, 0, 10, 8), 3.0), (sg.box(0, -20, 10, -12), 18.0)], start=1):
        v, t = oe.prism_solid(box, 0.0, h)
        objs.append(oe.make_object(k, 'building', 'bdtopo+lidar', f'B{k}', v, t, box, info={'base_z': 0.0}))
    objs[0]['status'] = oe.STATUS_STUDIED
    objs[0]['building_id'] = building.pk if building else None
    env = Environment.objects.create(
        name=f'obs {Environment.objects.count()}', georef_lat=building.georef_lat, georef_lon=building.georef_lon,
        georef_ground_z=90.0, scene_objects=objs, envelope=oe.compose_envelope(objs),
        generation={'source': 'lidar', 'radius_m': params['radius_m'], 'ground_albedo': ALBEDO, 'warnings': []})
    return env, {'environment_id': env.pk, 'source': 'lidar', 'stats': {}, 'warnings': ['essai'],
                 'replaced_environment_ids': [], 'n_objects': 2, 'n_triangles': len(env.envelope['triangles'])}


def generating(**kw):
    return mock.patch('api.environment_service.generate', side_effect=kw.pop('side_effect', fake_generate), **kw)


class Base(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='Parc', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role=PortfolioMember.GESTIONNAIRE_PARC)

    def mode(self, mode, **extra):
        set_reglages(self.p, 'orchestration', {'environnement.mode': mode, **extra})


class SchemaTest(Base):
    def test_every_orchestration_entry_is_applied(self):
        flags = {e['cle']: e for e in reglages.schema('orchestration')}
        self.assertTrue(all(e['applique'] for e in flags.values()), [k for k, e in flags.items() if not e['applique']])
        self.assertEqual(set(flags['environnement.mode']['choix']), {'manuel', 'avant_n2', 'chaine'})
        d = reglages.defaults('orchestration')
        self.assertEqual((d['environnement.mode'], d['environnement.rayon_m']), ('chaine', 150))   # Lot 30
        with self.assertRaises(reglages.ReglageError):
            reglages.validate('orchestration', {'environnement.rayon_m': 400})

    def test_wanted(self):
        b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69)
        hors = Building.objects.create(name='h', georef_lat=47.39, georef_lon=0.69)
        self.assertTrue(environment_service.wanted(b, 'chaine'))              # défaut (Lot 30) : dans la chaîne
        self.mode('manuel')
        self.assertFalse(environment_service.wanted(b, 'n2'))
        self.mode('avant_n2')
        self.assertTrue(environment_service.wanted(b, 'n2'))
        self.assertFalse(environment_service.wanted(b, 'chaine'))
        self.mode('chaine')
        self.assertTrue(environment_service.wanted(b, 'n2') and environment_service.wanted(b, 'chaine'))
        self.assertTrue(environment_service.wanted(hors, 'n2'))               # hors parc : le défaut (Lot 30)
        b.environment = Environment.objects.create(name='déjà', georef_lat=47.39, georef_lon=0.69)
        b.save()
        self.assertFalse(environment_service.wanted(b, 'chaine'))             # un environnement relié est gardé
        sans_position = Building.objects.create(name='s', portfolio=self.p)
        self.assertFalse(environment_service.wanted(sans_position, 'chaine'))


class ChainTest(Base):
    def setUp(self):
        super().setUp()
        self.b = Building.objects.create(name='b1', portfolio=self.p, georef_lat=47.39, georef_lon=0.69,
                                         pipeline={'footprint_xy': SQUARE}, pipeline_status='en_attente')

    def _drain(self, **gen):
        seen = []
        orig = calc_service.run_level

        def spy(building, level, **kw):
            seen.append((level, Building.objects.get(pk=building.pk).environment_id))
            return orig(building, level, **kw)
        with mock.patch('api.pipeline.initialize', side_effect=fake_init), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER), \
                mock.patch('api.calc_service.run_level', side_effect=spy), generating(**gen) as g:
            Building.objects.filter(pk=self.b.pk).update(pipeline_status='en_attente')
            parc.process_one(parc.next_building(self.p.pk), owner_sub='g')
        return Building.objects.get(pk=self.b.pk), g, seen

    def test_generated_before_n0_n1_with_parc_settings(self):
        self.mode('chaine', **{'environnement.rayon_m': 100, 'environnement.vegetation': False})
        b, g, seen = self._drain()
        g.assert_called_once()
        params = g.call_args.args[0]
        self.assertEqual((params['radius_m'], params['include_vegetation'], params['include_terrain']), (100.0, False, True))
        self.assertEqual(g.call_args.kwargs['building'].pk, b.pk)
        self.assertEqual(sorted(g.call_args.kwargs['self_polygon'].bounds), sorted((0.0, 0.0, 10.0, 8.0)))
        self.assertIsNotNone(b.environment_id)
        self.assertTrue(b.sun_visibility_stale)
        step = b.pipeline['steps']['A6']
        self.assertTrue(step['ok'])
        self.assertEqual((step['environment_id'], step['source'], step['rayon_m']), (b.environment_id, 'lidar', 100.0))
        # N0 et N1 sont calculés après : ils voient l'environnement (horizon relevé, Lot 26).
        self.assertEqual(seen, [('N0', b.environment_id), ('N1', b.environment_id)])
        self.assertEqual(b.pipeline_status, 'complet')

        # Une relance de la chaîne garde l'environnement relié (il a pu être retouché).
        b2, g2, _ = self._drain()
        g2.assert_not_called()
        self.assertEqual(b2.environment_id, b.environment_id)

    def test_failure_does_not_stop_the_chain(self):
        self.mode('chaine')
        b, g, seen = self._drain(side_effect=geodata.GeodataError('LiDAR HD indisponible'))
        g.assert_called_once()
        self.assertIsNone(b.environment_id)
        self.assertEqual(b.pipeline['steps']['A6'], {'ok': False, 'error': 'LiDAR HD indisponible'})
        self.assertEqual([lvl for lvl, _ in seen], ['N0', 'N1'])
        self.assertEqual(b.pipeline_status, 'complet')

    def test_unexpected_error_message_is_fixed(self):
        self.mode('chaine')
        b, _g, _ = self._drain(side_effect=RuntimeError('https://data.geopf.fr/wms?SECRET=1'))
        self.assertEqual(b.pipeline['steps']['A6'], {'ok': False, 'error': 'erreur RuntimeError'})

    def test_manual_and_before_n2_modes_leave_the_chain_alone(self):
        for mode in ('manuel', 'avant_n2'):
            self.mode(mode)
            b, g, _ = self._drain()
            g.assert_not_called()
            self.assertNotIn('A6', b.pipeline['steps'])
            self.assertIsNone(b.environment_id)

    def test_single_building_init_task(self):
        self.mode('chaine')
        job = Job.objects.create(kind='init_building', owner_sub='g')
        with mock.patch('api.pipeline.initialize', side_effect=fake_init), \
                mock.patch('api.calc_service.tmy_weather', return_value=WEATHER), generating() as g:
            tasks.init_building.apply(args=[job.pk, self.b.pk])
        g.assert_called_once()
        b = Building.objects.get(pk=self.b.pk)
        self.assertTrue(b.pipeline['steps']['A6']['ok'])
        self.assertIsNotNone(b.environment_id)
        self.assertTrue(CalcRun.objects.filter(building=b, level='N1').exists())


class N2Test(Base):
    def setUp(self):
        super().setUp()
        self.b = make_building(fiche())
        self.b.portfolio = self.p
        self.b.save()

    def test_before_n2_generates_links_and_stays_up_to_date(self):
        self.mode('avant_n2')
        with tmy():
            prep = simulation.prepare(self.b, 'N2', can_manage=True)
        self.assertEqual(prep['environment'], {'generer': True, 'rayon_m': 150,
                                               'estimate_s': environment_service.ENV_ESTIMATE_S})
        self.assertIn('généré avant le calcul', prep['engine']['obstacles'])
        self.assertIsNone(prep['already'])
        with tmy():
            run, fresh = calc_service.start_n2(self.b, owner_sub='g')
        self.assertTrue(fresh)
        self.assertTrue(run.settings['environnement']['generer'])
        steps = []
        with tmy(), generating() as g:
            run = calc_service.run_n2(run, lambda pct, msg: steps.append((pct, msg)))
        g.assert_called_once()
        self.b.refresh_from_db()
        self.assertEqual(run.status, Job.DONE)
        self.assertIsNotNone(self.b.environment_id)
        self.assertEqual(run.settings['environnement']['environment_id'], self.b.environment_id)
        self.assertEqual(run.inputs['obstacles'], 'environnement relié')
        self.assertTrue(any('Environnement observé' in m for _p, m in steps))
        pcts = [p for p, _m in steps]
        self.assertEqual(pcts, sorted(pcts))                          # l'avancement ne recule jamais
        with tmy():
            self.assertTrue(calc_service.is_up_to_date(run))          # empreinte refaite avec l'environnement
            again, fresh = calc_service.start_n2(self.b)
            prep = simulation.prepare(self.b, 'N2', can_manage=True)
        self.assertFalse(fresh)
        self.assertEqual(again.pk, run.pk)
        self.assertIsNone(prep['environment'])
        self.assertEqual(prep['already'], run.pk)

    def test_an_old_n2_without_environment_is_not_reused(self):
        self.mode('manuel')                                           # un N2 d'abord sans environnement
        with tmy():
            run, _ = calc_service.start_n2(self.b, owner_sub='g')
            run = calc_service.run_n2(run)
        Job.objects.update(status=Job.DONE)   # la tâche Celery l'aurait clôturé
        self.mode('avant_n2')
        with tmy():
            again, fresh = calc_service.start_n2(self.b, owner_sub='g')
        self.assertTrue(fresh)
        self.assertNotEqual(again.pk, run.pk)

    def test_failure_falls_back_to_bdtopo_neighbours(self):
        self.mode('chaine')
        with tmy():
            run, _ = calc_service.start_n2(self.b, owner_sub='g')
        with tmy(), generating(side_effect=geodata.GeodataError('zone pas encore couverte')):
            run = calc_service.run_n2(run)
        self.b.refresh_from_db()
        self.assertEqual(run.status, Job.DONE)
        self.assertIsNone(self.b.environment_id)
        self.assertFalse(run.settings['environnement']['ok'])
        self.assertTrue(any('zone pas encore couverte' in lim for lim in run.inputs['limites']))

    def test_ground_reflection_passed_to_the_solver(self):
        self.mode('manuel')                                           # un N2 d'abord sans environnement
        from .. import building_solver
        orig = building_solver.run_building_simulation
        seen = {}

        def spy(*a, **kw):
            seen['albedo'] = kw.get('ground_albedo')
            return orig(*a, **kw)
        with tmy():
            run0, _ = calc_service.start_n2(self.b, owner_sub='g')
            run0 = calc_service.run_n2(run0)
        Job.objects.update(status=Job.DONE)
        self.mode('avant_n2')
        with tmy():
            run, _ = calc_service.start_n2(self.b, owner_sub='g')
        with tmy(), generating(), mock.patch('api.building_solver.run_building_simulation', side_effect=spy):
            run = calc_service.run_n2(run)
        self.assertEqual(seen['albedo'], ALBEDO)
        self.assertEqual(run.inputs['engine'], 'n2-2')
        # Un N2 de l'ancienne version reste reconnu comme lancé depuis la fiche (simplement périmé).
        CalcRun.objects.filter(pk=run0.pk).update(settings={**run0.settings, 'engine': 'n2-1'})
        run0.refresh_from_db()
        self.assertTrue(calc_service.from_fiche(run0))

    def test_outside_a_parc_the_default_applies(self):
        """Lot 30 : hors parc, le réglage par défaut (« pendant la récupération automatique ») vaut aussi."""
        hors = make_building(fiche(), name='hors')
        with tmy():
            run, _ = calc_service.start_n2(hors, owner_sub='g')
        self.assertEqual(run.settings['environnement'], {'generer': True})
        with tmy(), generating() as g:
            calc_service.run_n2(run)
        g.assert_called_once()
        with mock.patch('api.environment_service.auto_mode', return_value='manuel'):
            other = make_building(fiche(), name='manuel')
            Job.objects.update(status=Job.DONE)
            with tmy():
                run2, _ = calc_service.start_n2(other, owner_sub='g')
            self.assertNotIn('environnement', run2.settings)
