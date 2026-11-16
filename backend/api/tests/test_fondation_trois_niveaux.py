"""Chantier bt-trois-niveaux — fondation : modèle Recalcul, réglages, routes déclarées (refusées sans jeton)."""

from django.test import TestCase

from .. import reglages
from ..models import Building, Recalcul

ROUTES = ['batiments/1/calculs/etat/', 'batiments/1/calculs/recalculer/', 'parcs/1/calculs/etat/']


class FondationTest(TestCase):
    def test_modele_et_reglages(self):
        b = Building.objects.create(name='b')
        r = Recalcul.objects.create(building=b)
        self.assertEqual((r.statut, b.recalcul.pk), ('inactif', r.pk))
        d = reglages.defaults('orchestration')
        self.assertEqual((d['calcul.n2'], d['calcul.potentiels'], d['calcul.recalcul_auto'], d['calcul.recalcul_delai_s']),
                         (True, True, True, 30))

    def test_routes_exigent_un_jeton(self):
        from rest_framework.test import APIClient
        for r in ROUTES:
            self.assertIn(APIClient().get('/api/' + r).status_code, (401, 403), r)
