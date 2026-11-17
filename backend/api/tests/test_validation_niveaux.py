"""Validation physique (chantier bt-validation-physique, phase 3) — N0, N1 et N2 d'une même fiche sur trois bâtiments
canoniques (maison, petit collectif, bureaux), bilan terme à terme. Cas lourd (trois N2 à l'année, ~1 min) : joué
seulement avec `BT_VALIDATION_LOURDE=1` :

    ESSAI_CMD='env BT_VALIDATION_LOURDE=1 python manage.py test' ESSAI_SRC=~/lab-work/host/bilan-thermique \\
      bash chantiers/tests/essai.sh bilan-thermique api.tests.test_validation_niveaux

Bandes écrites avant le calcul (RAPPORT.md § bandes) :
- chauffage : |N2/N1 − 1| ≤ 15 % et |N0/N1 − 1| ≤ 20 % — l'écart restant est expliqué par E1 (5R1C : H_tr,is en
  série, N1 sous-estime d'environ 7 % le statique) et E4 (inerties différentes) ;
- apports solaires par les baies : |N2/N1 − 1| ≤ 3 % — même soleil, même albédo (C1), même part de cadre ;
- confort d'été : aucune bande tant que E3 et E4 ne sont pas tranchés par le propriétaire ; valeurs affichées.
"""

import os
import unittest
from unittest import mock

from django.test import TestCase

from .. import calc_service, facts
from ..models import Building
from ..testing import synthetic_year, without_auto_environment

HEAVY = os.environ.get('BT_VALIDATION_LOURDE') == '1'
WEATHER = synthetic_year()
_NO_AUTO_ENV = without_auto_environment()

BASE = {'identite.niveaux': 1, 'identite.mitoyennete': 0.0, 'mur.isolation': 'iti', 'baies.vitrage': 'double',
        'enveloppe.couleur_absorptance': 0.6, 'ventilation.type': 'vmc_sf_auto', 'ventilation.debit_vol_h': 0.5,
        'ventilation.n50': 6.0, 'ventilation.rendement_recup': 0.0, 'toiture.type': 'terrasse',
        'plancher.type': 'terre_plein', 'usage.consigne_chauffage_c': 19.0}
CASES = {
    'maison': dict(BASE, **{'identite.surface_m2': 100.0, 'identite.hauteur_m': 3.0, 'identite.emprise_m2': 100.0,
                            'identite.usage': 'maison', 'mur.u': 0.4, 'toiture.u': 0.25, 'plancher.u': 0.4,
                            'baies.uw': 1.8, 'baies.g': 0.6, 'enveloppe.inertie': 'moyenne', 'usage.apports_w_m2': 4.0,
                            **{f'baies.ratio_{o}': 0.15 for o in 'NESO'}}),
    'collectif': dict(BASE, **{'identite.surface_m2': 600.0, 'identite.niveaux': 4, 'identite.hauteur_m': 12.0,
                               'identite.emprise_m2': 150.0, 'identite.usage': 'logement_collectif', 'mur.u': 0.8,
                               'toiture.u': 0.4, 'plancher.u': 0.6, 'baies.uw': 2.6, 'baies.g': 0.6,
                               'enveloppe.inertie': 'lourde', 'usage.apports_w_m2': 4.0,
                               **{f'baies.ratio_{o}': 0.2 for o in 'NESO'}}),
    'bureaux': dict(BASE, **{'identite.surface_m2': 165.0, 'identite.niveaux': 2, 'identite.hauteur_m': 6.0,
                             'identite.emprise_m2': 82.5, 'identite.usage': 'bureau', 'mur.u': 0.5, 'toiture.u': 0.35,
                             'plancher.u': 0.5, 'baies.uw': 2.9, 'baies.g': 0.62, 'enveloppe.inertie': 'moyenne',
                             'usage.apports_w_m2': 9.0, 'usage.consigne_chauffage_c': 20.0,
                             **{f'baies.ratio_{o}': 0.2 for o in 'NESO'}}),
}


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


def run_three(names):
    report = {}
    with mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER, 'source': 'test'}):
        for name in names:
            vals = CASES[name]
            side = vals['identite.emprise_m2'] ** 0.5
            b = Building.objects.create(name=name, georef_lat=47.33, georef_lon=0.71, pipeline={
                'footprint_xy': [[0, 0], [side, 0], [side, side], [0, side]], 'neighbours': []})
            for k, v in vals.items():
                facts.record(b, k, v, 'declare', refresh=False)
            facts.refresh_scores(b)
            r0, _ = calc_service.run_level(b, 'N0', uncertainty=False, force=True)
            r1, _ = calc_service.run_level(b, 'N1', uncertainty=False, force=True)
            r2, _ = calc_service.run_n2_now(b, force=True)
            h0, h1, h2 = (r.indicators['heating_kwh_m2'] for r in (r0, r1, r2))
            s1 = r1.results['balance']['solar_windows_kwh']
            s2 = r2.results['balance']['solar_transmitted_kwh']
            report[name] = {'chauffage': (h0, h1, h2), 'solaire_baies': (s1, round(s2, 1)),
                            'dh_re2020': (r1.indicators['dh_re2020'], r2.indicators['dh_re2020'])}
    print('VALIDATION_NIVEAUX', report)
    return report


@unittest.skipUnless(HEAVY, 'cas lourd : BT_VALIDATION_LOURDE=1')
class ThreeLevelsTest(TestCase):
    def test_bands(self):
        report = run_three(list(CASES))
        for name, r in report.items():
            h0, h1, h2 = r['chauffage']
            s1, s2 = r['solaire_baies']
            with self.subTest(case=name):
                self.assertLessEqual(abs(h2 / h1 - 1.0), 0.15, r)
                self.assertLessEqual(abs(s2 / s1 - 1.0), 0.03, r)
                if name != 'bureaux':
                    self.assertLessEqual(abs(h0 / h1 - 1.0), 0.20, r)

    @unittest.expectedFailure
    def test_n0_tertiary_band(self):
        """E6 (RAPPORT.md) : le N0 d'un bureau dépasse le N1 de 24 % (bande ±20 % écrite avant le calcul) — écart non
        entièrement expliqué (E1 en compte ~7 %). Retirer le décorateur avec le correctif."""
        h0, h1, _h2 = run_three(['bureaux'])['bureaux']['chauffage']
        self.assertLessEqual(abs(h0 / h1 - 1.0), 0.20)
