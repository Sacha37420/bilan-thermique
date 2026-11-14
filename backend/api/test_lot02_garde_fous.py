"""Lot 2 (refonte) — garde-fous de calcul : passe végétation comptée, verrou atomique,
messages d'erreur sans URL, User-Agent sans donnée personnelle."""

import threading
from unittest import mock

import requests
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from . import building_solver, net, bdnb, weather_source, geodata
from .models import Building, Environment, Job, ParoiModel
from .testing import box_envelope, client_for, patch_jwks

WALL = [{'e': 0.2, 'lam': 1.0, 'rho': 2000.0, 'c': 1000.0, 'tau': 0.0, 'r': 0.0, 'alpha': 0.6}]


def calcul_payload(hours=3, shadow_mode='precomputed'):
    return {
        'dx_max': 0.1, 'h_e': 25.0, 't_init': 20.0, 'shadow_mode': shadow_mode,
        'interior': {'mode': 'free', 'h_i': 7.7, 'c_air_int': 50000.0},
        'weather': [{'t_ext': 5.0, 'sun_azimuth': 180.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}
                    for _ in range(hours)],
    }


def tree_envelope():
    """Un triangle translucide (végétation, Lot Z : champ `k`)."""
    return {'vertices': [[30, 0, 0], [31, 0, 0], [30, 1, 5]],
            'triangles': [{'v': [0, 1, 2], 'k': 0.3, 'normal': [0, -1, 0]}]}


class VegetationCostTest(SimpleTestCase):
    def test_vegetation_detected(self):
        self.assertTrue(building_solver.envelope_has_vegetation(None, tree_envelope()))
        self.assertFalse(building_solver.envelope_has_vegetation(box_envelope()))

    def test_vegetation_pass_counted_in_cost(self):
        base = building_solver.realtime_raycast_ops(100, 10, False)
        veg = building_solver.realtime_raycast_ops(100, 10, True)
        self.assertEqual(base, 1000)
        self.assertEqual(veg, int(1000 * (1 + building_solver.VEGETATION_PASS_WEIGHT)))

    def test_vegetation_over_threshold_refused(self):
        """12 triangles × N heures sous le seuil sans végétation, au-dessus avec."""
        env = box_envelope()
        n_hours = building_solver.MAX_REALTIME_RAYCAST_OPS // 12 // 2  # ×1 sous, ×3 au-dessus
        building_solver.check_realtime_cost(env, None, n_hours)  # sans végétation : accepté
        with self.assertRaises(building_solver.BuildingSimulationError) as ctx:
            building_solver.check_realtime_cost(env, tree_envelope(), n_hours)
        self.assertIn('végétation comprise', str(ctx.exception))


@patch_jwks()
class VegetationCostViewTest(TestCase):
    def setUp(self):
        p = ParoiModel.objects.create(name='mur', layers=WALL)
        envr = Environment.objects.create(name='arbres', envelope=tree_envelope())
        self.b = Building.objects.create(name='b', envelope=box_envelope(paroi_id=p.pk), environment=envr)

    def test_view_refuses_before_queueing(self):
        old = building_solver.MAX_REALTIME_RAYCAST_OPS
        building_solver.MAX_REALTIME_RAYCAST_OPS = 12 * 3 * 2  # 3 h passent sans végétation, pas avec
        try:
            with mock.patch('api.tasks.run_building_calcul.delay') as delay:
                r = client_for().post(f'/api/batiments/{self.b.pk}/calcul-3d/',
                                      calcul_payload(3, 'realtime'), format='json')
        finally:
            building_solver.MAX_REALTIME_RAYCAST_OPS = old
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['code'], 'cost')
        delay.assert_not_called()
        self.assertFalse(Job.objects.exists())


@patch_jwks()
class ConcurrentLaunchTest(TransactionTestCase):
    """Deux lancements simultanés : un 202, un 409 — jamais deux jobs."""

    def setUp(self):
        p = ParoiModel.objects.create(name='mur', layers=WALL)
        self.b = Building.objects.create(name='b', envelope=box_envelope(paroi_id=p.pk),
                                         sun_visibility_stale=False)

    def test_two_simultaneous_launches(self):
        barrier = threading.Barrier(2)
        codes = []

        def launch():
            try:
                barrier.wait()
                r = client_for().post(f'/api/batiments/{self.b.pk}/calcul-3d/',
                                      calcul_payload(2, 'realtime'), format='json')
                codes.append(r.status_code)
            finally:
                connection.close()

        with mock.patch('api.tasks.run_building_calcul.delay'):
            threads = [threading.Thread(target=launch) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(sorted(codes), [202, 409])
        self.assertEqual(Job.objects.count(), 1)

    def test_task_sent_after_commit_with_job_visible(self):
        seen = []
        with mock.patch('api.tasks.run_building_calcul.delay',
                        side_effect=lambda job_id, *a: seen.append(Job.objects.filter(pk=job_id).exists())):
            r = client_for().post(f'/api/batiments/{self.b.pk}/calcul-3d/', calcul_payload(2, 'realtime'),
                                  format='json')
        self.assertEqual(r.status_code, 202)
        self.assertEqual(seen, [True])

    def test_stale_job_does_not_block_forever(self):
        from datetime import timedelta
        from django.utils import timezone
        j = Job.objects.create(kind='building_calcul', status=Job.RUNNING)
        Job.objects.filter(pk=j.pk).update(updated_at=timezone.now() - timedelta(hours=7))
        with mock.patch('api.tasks.run_building_calcul.delay'):
            r = client_for().post(f'/api/batiments/{self.b.pk}/calcul-3d/', calcul_payload(2, 'realtime'),
                                  format='json')
        self.assertEqual(r.status_code, 202)
        j.refresh_from_db()
        self.assertEqual(j.status, Job.ERROR)

    def test_owner_sub_recorded(self):
        with mock.patch('api.tasks.run_building_calcul.delay'):
            client_for(sub='abc-123').post(f'/api/batiments/{self.b.pk}/calcul-3d/',
                                           calcul_payload(2, 'realtime'), format='json')
        self.assertEqual(Job.objects.get().owner_sub, 'abc-123')


class NoUrlInErrorsTest(SimpleTestCase):
    SECRET_URL = 'https://api.bdnb.io/v1/x?batiment_groupe_id=in.(secret)&cle=SECRET'

    def test_reason_never_contains_requests_text(self):
        for exc in (requests.ConnectionError(self.SECRET_URL), requests.Timeout(self.SECRET_URL),
                    requests.HTTPError(self.SECRET_URL), requests.RequestException(self.SECRET_URL),
                    ValueError(self.SECRET_URL), RuntimeError(self.SECRET_URL)):
            self.assertNotIn('SECRET', net.reason(exc))
            self.assertNotIn('http', net.reason(exc))

    def test_domain_errors_pass_through(self):
        self.assertEqual(net.reason(bdnb.BdnbError('BDNB : délai dépassé')), 'BDNB : délai dépassé')

    def test_bdnb_error_has_no_url(self):
        with mock.patch('requests.get', side_effect=requests.ConnectionError(self.SECRET_URL)):
            with self.assertRaises(bdnb.BdnbError) as ctx:
                bdnb.rnb_ids_at([(47.39, 0.68)])
        self.assertNotIn('SECRET', str(ctx.exception))
        self.assertIn('connexion impossible', str(ctx.exception))

    def test_job_message_is_fixed_for_unexpected_errors(self):
        msg = net.public_message(requests.ConnectionError(self.SECRET_URL))
        self.assertNotIn('SECRET', msg)
        self.assertEqual(net.public_message(building_solver.BuildingSimulationError('trop grand')), 'trop grand')

    def test_user_agents_carry_no_personal_data(self):
        for ua in (net.USER_AGENT, bdnb.USER_AGENT, weather_source.USER_AGENT, geodata.USER_AGENT):
            self.assertNotIn('@', ua)
            self.assertTrue(ua.startswith('bilan-thermique'))
