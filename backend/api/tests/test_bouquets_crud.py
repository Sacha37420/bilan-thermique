"""Chantier bt-potentiels, lot 37 (secteur A) — bouquets du lab et d'un parc (I7.b, I7.c) : droits, validation."""

from django.test import TestCase

from ..ameliorations import bouquets as bq_service
from ..models import Bouquet, Building, BuildingAccess, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, without_auto_environment

_env = without_auto_environment()


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()


@patch_jwks()
class BouquetsTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role='lecteur')
        self.g, self.l, self.x = client_for(sub='g'), client_for(sub='l'), client_for(sub='x')
        self.url = f'/api/parcs/{self.p.pk}/bouquets/'

    def test_lab_defaults_idempotent(self):
        bq_service.ensure_defaults()
        bq_service.ensure_defaults()
        lab = Bouquet.objects.filter(portfolio__isnull=True)
        self.assertEqual(sorted(lab.values_list('objectif', flat=True)), ['confort', 'energie'])
        self.assertEqual(set(lab.values_list('nom', flat=True)), {'Économiser l’énergie', 'Minimiser les DH'})
        for b in lab:   # les bouquets du lab passent leur propre validation
            self.assertEqual(bq_service.validate_elements(b.elements), b.elements)

    def test_list_and_rights(self):
        r = self.l.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['peut_ecrire'])
        self.assertEqual([b['portfolio'] for b in r.json()['bouquets']], [None, None])
        self.assertTrue(all(not b['modifiable'] for b in r.json()['bouquets']))
        self.assertTrue(self.g.get(self.url).json()['peut_ecrire'])
        self.assertEqual(self.x.get(self.url).status_code, 404)
        body = {'nom': 'Mon bouquet', 'objectif': 'libre', 'elements': [{'action': 'ite', 'parametres': {'epaisseur_cm': 16}}]}
        self.assertEqual(self.l.post(self.url, body, format='json').status_code, 403)
        self.assertEqual(self.x.post(self.url, body, format='json').status_code, 404)
        r = self.g.post(self.url, body, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual(set(b), {'id', 'nom', 'description', 'objectif', 'elements', 'ordre', 'par_defaut', 'portfolio',
                                  'modifiable', 'modifie_le'})
        self.assertTrue(b['modifiable'])
        d = f'/api/bouquets/{b["id"]}/'
        self.assertEqual(self.l.get(d).status_code, 200)
        self.assertFalse(self.l.get(d).json()['modifiable'])
        self.assertEqual(self.x.get(d).status_code, 404)
        self.assertEqual(self.l.patch(d, {'nom': 'z'}, format='json').status_code, 403)
        self.assertEqual(self.x.delete(d).status_code, 404)
        r = self.g.patch(d, {'nom': 'Renommé', 'elements': [{'action': 'combles'}]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['elements'], [{'action': 'combles', 'parametres': {}}])
        self.assertEqual(self.g.delete(d).status_code, 204)
        self.assertFalse(Bouquet.objects.filter(pk=b['id']).exists())

    def test_lab_bouquet_read_only_but_copyable(self):
        bq_service.ensure_defaults()
        lab = Bouquet.objects.get(portfolio__isnull=True, objectif='energie')
        d = f'/api/bouquets/{lab.pk}/'
        self.assertEqual(self.x.get(d).status_code, 200)   # lisible par tout membre
        r = self.g.patch(d, {'nom': 'x'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['detail'], 'Bouquet du lab : copiez-le dans un parc pour le modifier.')
        self.assertEqual(self.g.delete(d).status_code, 403)
        self.assertEqual(self.l.post(self.url, {'copie_de': lab.pk}, format='json').status_code, 403)
        r = self.g.post(self.url, {'copie_de': lab.pk}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['elements'], lab.elements)
        self.assertEqual(r.json()['portfolio'], self.p.pk)
        self.assertEqual(r.json()['nom'], 'Économiser l’énergie (copie)')
        # Un bouquet d'un autre parc ne se copie pas.
        other = Portfolio.objects.create(name='Q', owner_sub='h')
        ob = Bouquet.objects.create(portfolio=other, nom='o', elements=[])
        self.assertEqual(self.g.post(self.url, {'copie_de': ob.pk}, format='json').status_code, 404)

    def test_validation(self):
        cases = [
            ({'nom': '', 'objectif': 'libre', 'elements': []}, 'Nom du bouquet attendu.'),
            ({'nom': 'a', 'objectif': 'x', 'elements': []}, 'Objectif : energie, confort ou libre.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'action': 'inconnue'}]}, 'Action inconnue.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'action': 'ite', 'parametres': {'zz': 1}}]}, 'Paramètre inconnu.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'action': 'ite', 'parametres': {'epaisseur_cm': 99}}]}, None),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'action': 'ite'}, {'action': 'iti'}]},
             'Deux éléments du bouquet visent la même cible.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'action': 'vmc_df'}, {'action': 'etancheite'}]},
             'Deux éléments du bouquet visent la même cible.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'une_parmi': [{'action': 'ite'}, {'action': 'iti'}]},
                                                            {'action': 'iti'}]},
             'Deux éléments du bouquet visent la même cible.'),
            ({'nom': 'a', 'objectif': 'libre', 'elements': [{'une_parmi': []}]}, None),
        ]
        for body, msg in cases:
            r = self.g.post(self.url, body, format='json')
            self.assertEqual(r.status_code, 400, body)
            if msg:
                self.assertEqual(r.json()['detail'], msg)
        # Une même cible à l'intérieur d'un « une parmi » est permise.
        ok = {'nom': 'a', 'objectif': 'libre', 'elements': [{'une_parmi': [{'action': 'ite'}, {'action': 'iti'}]}]}
        self.assertEqual(self.g.post(self.url, ok, format='json').status_code, 201)

    def test_bouquets_visible_for_building(self):
        bq_service.ensure_defaults()
        mine = Bouquet.objects.create(portfolio=self.p, nom='m', elements=[])
        Bouquet.objects.create(portfolio=Portfolio.objects.create(name='Q', owner_sub='h'), nom='o', elements=[])
        b = Building.objects.create(name='b', portfolio=self.p)
        BuildingAccess.objects.create(building=b, sub='o', role='occupant')
        names = [x.nom for x in bq_service.visible_for_building(b)]
        self.assertEqual(names, ['Économiser l’énergie', 'Minimiser les DH', mine.nom])
