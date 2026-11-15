"""Lot 3 (refonte) — historique des calculs (U3), environnements remplacés (L6), brouillons (L7),
dossier SfM nettoyé."""

import os
import tempfile
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .. import calc_runs, environment_service, facade_sfm, tasks
from ..models import Building, CalcRun, Environment, Job, ParoiModel
from .test_garde_fous import WALL, calcul_payload
from ..testing import box_envelope, client_for, patch_jwks


@patch_jwks()
class CalcHistoryTest(TestCase):
    def setUp(self):
        self.p = ParoiModel.objects.create(name='mur', layers=WALL)
        self.b = Building.objects.create(name='b', envelope=box_envelope(paroi_id=self.p.pk),
                                         sun_visibility_stale=False, surface_ref_m2=80.0)

    def _launch(self, hours=24):
        with mock.patch('api.tasks.run_building_calcul.delay') as delay, \
                self.captureOnCommitCallbacks(execute=True):
            r = client_for(sub='u1').post(f'/api/batiments/{self.b.pk}/calcul-3d/',
                                          calcul_payload(hours, 'realtime'), format='json')
        self.assertEqual(r.status_code, 202, r.content)
        return r.json()['id'], delay.call_args.args

    def test_launch_creates_pending_run(self):
        job_id, _ = self._launch()
        run = CalcRun.objects.get(job_id=job_id)
        self.assertEqual((run.level, run.status, run.owner_sub), ('N2', 'PENDING', 'u1'))
        self.assertEqual(run.weather['n_hours'], 24)
        self.assertNotIn('weather', run.settings)
        self.assertEqual(len(run.inputs_hash), 64)

    def test_result_reviewed_after_leaving_page_without_recalc(self):
        """Le calcul tourne (tâche jouée en direct), puis le résultat se relit par l'API seule."""
        job_id, args = self._launch()
        tasks.run_building_calcul.apply(args=args)  # exécution synchrone, sans Celery
        self.assertEqual(Job.objects.get(pk=job_id).status, Job.DONE)
        with mock.patch('api.building_solver.run_building_simulation') as solver:
            listing = client_for().get(f'/api/batiments/{self.b.pk}/calculs/').json()['results']
            self.assertEqual(len(listing), 1)
            self.assertNotIn('results', listing[0])
            run = client_for().get(f"/api/calculs/{listing[0]['id']}/").json()
            solver.assert_not_called()
        self.assertEqual(run['status'], 'DONE')
        self.assertEqual(run['results']['hours'], 24)
        self.assertEqual(len(run['results']['t_air']), 25)  # état initial + 24 h
        ind = run['indicators']
        self.assertGreater(ind['heating_kwh'], -1)
        self.assertIn('heating_kwh_m2', ind)
        self.assertIn('hours_over_28', ind)
        self.assertIsNotNone(run['duration_s'])

    def test_failure_marks_run_error(self):
        job_id, args = self._launch()
        with mock.patch('api.building_solver.run_building_simulation', side_effect=RuntimeError('boom')):
            tasks.run_building_calcul.apply(args=args)
        run = CalcRun.objects.get(job_id=job_id)
        self.assertEqual(run.status, 'ERROR')

    def test_one_active_run_per_building_and_level(self):
        from django.db import IntegrityError, transaction
        CalcRun.objects.create(building=self.b, level='N1', status='RUNNING')
        with self.assertRaises(IntegrityError), transaction.atomic():
            CalcRun.objects.create(building=self.b, level='N1', status='PENDING')
        CalcRun.objects.create(building=self.b, level='N1', status='DONE')  # terminé : permis

    def test_indicators_comfort_hours(self):
        ind = calc_runs.indicators_from({'heating_kwh': 100.0, 'cooling_kwh': 0.0, 't_air_mean': 22.0,
                                         'hours': 4, 't_air': [25.0, 27.0, 29.0, 30.0]}, 50.0)
        self.assertEqual((ind['hours_over_26'], ind['hours_over_28']), (3, 2))
        self.assertEqual(ind['heating_kwh_m2'], 2.0)
        self.assertEqual(ind['degree_hours_over_26'], 8.0)

    def test_unknown_run_404(self):
        self.assertEqual(client_for().get('/api/calculs/999999/').status_code, 404)


class EnvironmentReplacedTest(TestCase):
    def _generate(self, building):
        env = Environment.objects.create(name=f'env {Environment.objects.count()}',
                                         generation={'source': 'lidar'},
                                         scene_objects=[{'id': 1, 'status': 'studied', 'building_id': building.pk}])
        return env, environment_service.mark_previous_replaced(env, building)

    @patch_jwks()
    def test_regenerating_three_times_leaves_one(self):
        b = Building.objects.create(name='b', envelope=box_envelope(), georef_lat=47.0, georef_lon=0.7)
        for _ in range(3):
            env, _ = self._generate(b)
        self.assertEqual(Environment.objects.count(), 3)  # rien supprimé
        listed = client_for().get('/api/environnements/').json()
        self.assertEqual([e['id'] for e in listed], [env.pk])
        self.assertEqual(len(client_for().get('/api/environnements/?tous=1').json()), 3)

    @patch_jwks()
    def test_replaced_env_still_listed_while_linked(self):
        b = Building.objects.create(name='b', envelope=box_envelope())
        e1, _ = self._generate(b)
        b.environment = e1
        b.save()
        e2, replaced = self._generate(b)
        self.assertEqual(replaced, [e1.pk])
        ids = {e['id'] for e in client_for().get('/api/environnements/').json()}
        self.assertEqual(ids, {e1.pk, e2.pk})

    def test_imported_environment_never_marked(self):
        b = Building.objects.create(name='b', envelope=box_envelope())
        imported = Environment.objects.create(name='importé', envelope={'vertices': [], 'triangles': []})
        b.environment = imported
        b.save()
        _, replaced = self._generate(b)
        self.assertEqual(replaced, [])


class DraftCleanupTest(TestCase):
    def test_lists_without_deleting_then_applies(self):
        old = Building.objects.create(name='brouillon', envelope=box_envelope())
        Building.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=30))
        recent = Building.objects.create(name='récent', envelope=box_envelope())
        used = Building.objects.create(name='calculé', envelope=box_envelope())
        Building.objects.filter(pk=used.pk).update(updated_at=timezone.now() - timedelta(days=30))
        CalcRun.objects.create(building=used, level='N2', status='DONE')
        out = StringIO()
        call_command('nettoyer_brouillons', stdout=out)
        self.assertIn('brouillon', out.getvalue())
        self.assertIn('rien supprimé', out.getvalue())
        self.assertEqual(Building.objects.count(), 3)
        call_command('nettoyer_brouillons', '--appliquer', stdout=StringIO())
        self.assertEqual(set(Building.objects.values_list('name', flat=True)), {'récent', 'calculé'})
        self.assertTrue(Building.objects.filter(pk=recent.pk).exists())


class SfmWorkdirCleanupTest(TestCase):
    def test_cleanup_only_under_sfm_dir(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(facade_sfm, 'SFM_DIR', os.path.join(root, 'sfm')):
                work = os.path.join(root, 'sfm', '12')
                os.makedirs(os.path.join(work, 'images'))
                outside = os.path.join(root, 'autre')
                os.makedirs(outside)
                facade_sfm.cleanup_workdir(work)
                facade_sfm.cleanup_workdir(outside)
                facade_sfm.cleanup_workdir(os.path.join(root, 'sfm', '..', 'autre'))
                self.assertFalse(os.path.exists(work))
                self.assertTrue(os.path.exists(outside))
