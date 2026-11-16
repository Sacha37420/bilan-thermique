"""Lot 35 — fondation du chantier bt-potentiels : modèles, clés de faits, routes déclarées (forme du contrat).
Les routes rendent 501 tant que leur secteur ne les a pas réalisées ; ce test le remplace alors par les siens."""

from django.test import TestCase

from .. import facts
from ..models import Bouquet, Building, ExportRendu, Portfolio, Potentiel, RapportModele, Scenario


ROUTES = ['indicateurs/', 'calculs/1/triangles/', 'batiments/1/potentiels/', 'parcs/1/potentiels/',
          'ameliorations/actions/', 'parcs/1/bouquets/', 'bouquets/1/', 'rendu/schema/', 'rendu/dimensions/',
          'rendu/mesures/', 'rendu/figure/', 'rendu/document/', 'rendu/exports/', 'rendu/exports/1/',
          'rendu/exports/1/fichier', 'rendu/gabarits/', 'parcs/1/rapports/', 'rapports/1/']


class ModelsTest(TestCase):
    def test_models_and_scenario_fields(self):
        p = Portfolio.objects.create(name='p', owner_sub='g')
        b = Building.objects.create(name='b', portfolio=p)
        bq = Bouquet.objects.create(nom='Économiser l’énergie', objectif='energie',
                                    elements=[{'action': 'ite', 'parametres': {}}])
        s = Scenario.objects.create(building=b, name='potentiel', bouquet=bq, genere=True,
                                    surcharges={'arbres': []})
        Potentiel.objects.create(building=b, bouquet=bq, scenario=s)
        RapportModele.objects.create(nom='Fiche', portee='batiment', spec={'version': 1})
        ExportRendu.objects.create(owner_sub='g', format='pdf')
        self.assertEqual(list(bq.scenarios.all()), [s])
        self.assertIsNone(bq.portfolio)
        with self.assertRaises(Exception):
            Potentiel.objects.create(building=b, bouquet=bq, niveau='N1')


class FactKeysTest(TestCase):
    def test_new_keys_weightless(self):
        for k in ('toiture.absorptance', 'abords.albedo', 'ventilation.nocturne_vol_h', 'ventilation.nocturne_seuil_c',
                  'toiture.vegetalisee', 'murs.vegetalises'):
            self.assertEqual(facts.BY_KEY[k].weight, 0, k)


class RoutesTest(TestCase):
    """Lot 40 : toutes les routes de la fondation sont réalisées par leur secteur ; chacune refuse un appel sans jeton."""

    def test_declared_routes_require_authentication(self):
        from rest_framework.test import APIClient
        for r in ROUTES:
            self.assertIn(APIClient().get('/api/' + r).status_code, (401, 403), r)
