"""Lot 36 (chantier bt-potentiels, I1) — catalogue v2 des protections et API : forme exacte, validation par le
catalogue (refus à message fixe), modification, rattachement à l'état actuel ou à une amélioration, droits."""

from unittest import mock

from django.test import TestCase

from .. import calc_service, facts
from ..engines import shading
from ..models import Building, Portfolio, PortfolioMember, Scenario, ShadingDevice
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment

_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


PARAM_KEYS = {'cle', 'libelle', 'unite', 'type', 'min', 'max', 'pas', 'defaut', 'choix', 'groupe', 'aide', 'niveaux'}


@patch_jwks()
class CatalogueTest(TestCase):
    def test_form(self):
        c = client_for('g').get('/api/protections/catalogue/').json()
        self.assertEqual(c['version'], 2)
        self.assertEqual({f['cle'] for f in c['familles']}, {'fixe', 'mobile', 'vitrage', 'vegetal'})
        keys = set()
        for m in c['modeles']:
            self.assertEqual(set(m), {'cle', 'famille', 'libelle', 'description', 'modes', 'parametres', 'pilotage',
                                      'application'})
            self.assertEqual(set(m['application']), {'N0', 'N1', 'N2'})
            for p in m['parametres'] + m['pilotage']['parametres']:
                self.assertEqual(set(p), PARAM_KEYS)
                self.assertIn(p['type'], ('number', 'int', 'enum', 'bool'))
                keys.add(p['cle'])
        # Minimum du contrat : épaisseur, débord, débords latéraux par côté, inclinaison (débords, lames),
        # largeur et entraxe des lames, transmission, position.
        for k in ('lame_epaisseur_m', 'depth_m', 'extension_gauche_m', 'extension_droite_m', 'inclinaison_deg',
                  'lame_inclinaison_deg', 'lame_largeur_m', 'lame_entraxe_m', 'lame_transmission', 'position'):
            self.assertIn(k, keys)
        # Les clés d'avant le lot 36 gardent leur nom.
        for k in ('depth_m', 'gap_m', 'window_height_m', 'window_width_m', 'extension_m', 'fin_depth_m', 'g',
                  'fs_dir', 'fs_dif', 't_summer', 't_winter'):
            self.assertIn(k, keys)


@patch_jwks()
class ApiTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='p', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role='lecteur')
        self.b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69)
        self.other = Building.objects.create(name='o', portfolio=self.p)
        for k, v in (('identite.surface_m2', 100.0), ('baies.ratio_S', 0.4), ('identite.usage', 'maison')):
            facts.record(self.b, k, v, 'infere')
        self.s = Scenario.objects.create(building=self.b, name='amélioration')
        self.url = f'/api/batiments/{self.b.pk}/protections/'
        self.g = client_for('g')

    def post(self, body, client=None):
        return (client or self.g).post(self.url, body, format='json')

    def test_create_complete_with_defaults(self):
        r = self.post({'kind': 'bso', 'orientation': 'S', 'params': {'lame_inclinaison_deg': 60},
                       'control': {'mode': 'auto', 'irradiance_w_m2': 250}})
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d['params']['lame_inclinaison_deg'], 60)
        self.assertEqual(d['params']['lame_entraxe_m'], shading.SLAT_DEFAULTS['bso']['lame_entraxe_m'])
        self.assertEqual(d['control']['irradiance_w_m2'], 250)
        self.assertEqual(d['control']['position'], 1.0)
        self.assertIsNone(d['scenario'])

    def test_refusals_fixed_messages(self):
        cases = [
            ({'kind': 'casquette', 'orientation': 'S', 'params': {'depth_m': 9}}, 'Profondeur du débord : entre 0 et 5.'),
            ({'kind': 'casquette', 'orientation': 'S', 'params': {'inconnu': 1}}, 'Paramètre inconnu.'),
            ({'kind': 'casquette', 'orientation': 'S', 'params': {'depth_m': 'x'}}, 'Nombre attendu.'),
            ({'kind': 'joues', 'orientation': 'S', 'params': {'fin_cote': 'haut'}}, 'Choix inconnu.'),
            ({'kind': 'store_ext', 'orientation': 'S', 'mode': 'geometrique'},
             'Seules les protections fixes à débord ou à joues ont un mode géométrique.'),
            ({'kind': 'store_ext', 'orientation': 'S', 'control': {'mode': 'jamais'}}, 'Pilotage : fixe, planning ou auto.'),
            ({'kind': 'store_ext', 'orientation': 'S', 'control': {'heure_debut': 24}}, 'Fermée à partir de : entre 0 et 23.'),
            ({'kind': 'film', 'orientation': 'S', 'control': {'mode': 'auto'}}, 'Cette protection n’a pas de pilotage.'),
            ({'kind': 'fusee', 'orientation': 'S'}, 'Type de protection inconnu.'),
            ({'kind': 'film', 'orientation': 'X'}, 'Orientation : N, E, S, O ou all.'),
            ({'kind': 'film', 'orientation': 'S', 'scenario': 0}, 'Amélioration d’un autre bâtiment.'),
        ]
        for body, msg in cases:
            with self.subTest(body=body):
                r = self.post(body)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json(), {'detail': msg})
        other = Scenario.objects.create(building=self.other, name='x')
        self.assertEqual(self.post({'kind': 'film', 'orientation': 'S', 'scenario': other.pk}).json(),
                         {'detail': 'Amélioration d’un autre bâtiment.'})
        gen = Scenario.objects.create(building=self.b, name='potentiel', genere=True)
        self.assertEqual(self.post({'kind': 'film', 'orientation': 'S', 'scenario': gen.pk}).json(),
                         {'detail': 'Amélioration calculée depuis un bouquet : modifiez le bouquet.'})
        self.assertEqual(ShadingDevice.objects.count(), 0)

    def test_edit_and_reattach(self):
        d = self.post({'kind': 'casquette', 'orientation': 'S', 'params': {'depth_m': 0.8}}).json()
        url = f'/api/protections/{d["id"]}/'
        r = self.g.patch(url, {'params': {'depth_m': 1.2, 'inclinaison_deg': 15}, 'scenario': self.s.pk}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['params']['depth_m'], 1.2)
        self.assertEqual(r.json()['params']['gap_m'], 0.2)               # complété par le défaut
        self.assertEqual(r.json()['scenario'], self.s.pk)
        # Protection de l'amélioration : absente de l'état actuel, présente dans l'amélioration.
        self.assertEqual(calc_service.params_for(self.b)[0].devices, [])
        self.assertEqual(len(calc_service.params_for(self.b, self.s)[0].devices), 1)
        r = self.g.patch(url, {'scenario': None}, format='json')
        self.assertIsNone(r.json()['scenario'])
        self.assertEqual(len(calc_service.params_for(self.b)[0].devices), 1)
        r = self.g.patch(url, {'kind': 'store_ext'}, format='json')          # autre modèle : paramètres refaits
        self.assertEqual(r.status_code, 200)
        self.assertIn('fs_dir', r.json()['params'])
        self.assertNotIn('depth_m', r.json()['params'])
        self.assertEqual(self.g.patch(url, {'params': {'depth_m': 1}}, format='json').status_code, 400)

    def test_reader_cannot_write(self):
        d = self.post({'kind': 'film', 'orientation': 'S'}).json()
        lec = client_for('l')
        self.assertEqual(lec.get(self.url).status_code, 200)
        self.assertEqual(self.post({'kind': 'film', 'orientation': 'S'}, lec).status_code, 403)
        self.assertEqual(lec.patch(f'/api/protections/{d["id"]}/', {'params': {'g': 0.3}}, format='json').status_code, 403)
        self.assertEqual(lec.delete(f'/api/protections/{d["id"]}/').status_code, 403)
        self.assertEqual(client_for('x').patch(f'/api/protections/{d["id"]}/', {}, format='json').status_code, 404)

    def test_effect_rows_carry_dh_re2020(self):
        self.post({'kind': 'store_ext', 'orientation': 'S', 'control': {'mode': 'auto'}})
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': synthetic_year(), 'source': 't'}):
            eff = self.g.post(f'{self.url}effet/', {}, format='json').json()
        rows = {x['label']: x for x in eff['rows']}
        self.assertIn('dh_re2020', rows['sans'])
        self.assertLessEqual(rows['toutes']['dh_re2020'], rows['sans']['dh_re2020'])
