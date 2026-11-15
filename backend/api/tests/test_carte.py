"""Lot 19 (refonte) — carte : emprises d'une zone, ajout de bâtiments au parc depuis la carte."""

from unittest import mock

from django.test import TestCase

from ..models import Building, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks
from .test_fiche_auto import fake_ign

ZONE = {'lat_min': 47.3900, 'lon_min': 0.6880, 'lat_max': 47.3920, 'lon_max': 0.6910}


@patch_jwks()
class MapTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role='lecteur')

    def test_area_footprints_and_limits(self):
        with mock.patch('api.geodata.fetch_ign_buildings', side_effect=fake_ign) as f:
            r = client_for(sub='g').post('/api/carte/batiments/', ZONE, format='json')
            client_for(sub='g').post('/api/carte/batiments/', ZONE, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()['results']), 2)
        self.assertGreater(r.json()['results'][0]['area_m2'], 50)
        self.assertEqual(f.call_count, 1)  # mis en cache
        big = client_for().post('/api/carte/batiments/', {**ZONE, 'lat_max': 47.45}, format='json')
        self.assertEqual((big.status_code, big.json()['code']), (400, 'zoom'))
        self.assertEqual(client_for().post('/api/carte/batiments/', {'lat_min': 'x'}, format='json').status_code, 400)

    def test_select_buildings_on_map_adds_them_to_parc(self):
        """Recette du lot : sélection de bâtiments à la carte."""
        with mock.patch('api.tasks.process_parc_next.apply_async') as q:
            r = client_for(sub='g').post(f'/api/parcs/{self.p.pk}/ajouter/',
                                         {'points': [{'lat': 47.391, 'lon': 0.689, 'name': 'A'}, {'lat': 47.3912, 'lon': 0.6895}]},
                                         format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Building.objects.filter(portfolio=self.p, pipeline_status='en_attente').count(), 2)
        self.assertEqual(q.call_args.kwargs['queue'], 'parc')
        self.assertEqual(client_for(sub='l').post(f'/api/parcs/{self.p.pk}/ajouter/', {'points': [{'lat': 47.39, 'lon': 0.69}]},
                                                  format='json').status_code, 403)
        self.assertEqual(client_for(sub='g').post(f'/api/parcs/{self.p.pk}/ajouter/', {'points': [{'lat': 60, 'lon': 0}]},
                                                  format='json').status_code, 400)

    def test_move_building_position_via_existing_route(self):
        b = Building.objects.create(name='commun', georef_lat=47.0, georef_lon=0.5)
        r = client_for().patch(f'/api/batiments/{b.pk}/', {'georef_lat': 47.3911, 'georef_lon': 0.6893}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        b.refresh_from_db()
        self.assertAlmostEqual(b.georef_lat, 47.3911)
