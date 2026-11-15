"""Lot 16 (refonte) — systèmes : énergie finale, primaire, CO₂, coût, étiquette du modèle et écart au DPE."""

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import calc_service, facts
from ..engines import systems
from ..models import Building
from ..testing import synthetic_year

BASE = {'identite.usage': 'maison', 'ventilation.type': 'vmc_sf_auto', 'ventilation.debit_vol_h': 0.5}


class SystemsTest(SimpleTestCase):
    def ev(self, **over):
        return systems.evaluate({**BASE, **over}, 10000.0, 0.0, 100.0, 250.0)

    def test_condensing_boiler_and_electric_primary_factor(self):
        std, cond = self.ev(**{'systeme.generateur': 'chaudiere_gaz'}), self.ev(**{'systeme.generateur': 'chaudiere_gaz_cond',
                                                                                   'systeme.rendement_generation': 0.95})
        self.assertLess(cond['by_usage_kwh']['chauffage'], std['by_usage_kwh']['chauffage'])
        joule = self.ev(**{'systeme.generateur': 'joule', 'systeme.rendement_generation': 1.0})
        self.assertGreater(joule['primary_kwh'], joule['final_kwh'] * 2)  # facteur 2,3 sur l'électricité
        self.assertEqual(joule['energy'], 'electricite')

    def test_heat_pump_hourly_cop_depends_on_outdoor_temperature(self):
        cold = systems.heat_pump_cop(np.array([-7.0]), 55.0)[0]
        mild = systems.heat_pump_cop(np.array([10.0]), 55.0)[0]
        self.assertLess(cold, mild)
        low_t = systems.heat_pump_cop(np.array([0.0]), 32.0)[0]
        self.assertGreater(low_t, systems.heat_pump_cop(np.array([0.0]), 55.0)[0])  # plancher chauffant meilleur
        heat = np.full(48, 2000.0)
        r = systems.evaluate({**BASE, 'systeme.generateur': 'pac_air_eau', 'systeme.emetteurs': 'plancher'}, 96.0, 0.0, 100.0, 250.0,
                             heat, np.full(48, 5.0))
        self.assertGreater(r['cop_seasonal'], 2.5)
        self.assertLess(r['by_usage_kwh']['chauffage'], 96.0)

    def test_dpe_label_double_threshold(self):
        self.assertEqual(systems.dpe_label(60, 5), 'A')
        self.assertEqual(systems.dpe_label(60, 40), 'D')  # le GES déclasse
        self.assertEqual(systems.dpe_label(500, 5), 'G')

    def test_gap_to_declared_dpe(self):
        r = self.ev(**{'dpe.conso_ep_kwh_m2': 200.0, 'dpe.etiquette': 'D'})
        self.assertIn('dpe_gap_pct', r)
        self.assertEqual(r['dpe_label_declared'], 'D')
        self.assertEqual(set(r['by_usage_kwh']), {'chauffage', 'ecs', 'refroidissement', 'eclairage', 'auxiliaires'})


class CalcWithSystemsTest(TestCase):
    def test_run_indicators_show_gap_to_dpe(self):
        """Recette du lot : l'écart à l'étiquette DPE est affiché."""
        b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        for k, v, m in (('identite.surface_m2', 100.0, 'infere'), ('identite.usage', 'maison', 'infere'),
                        ('systeme.generateur', 'chaudiere_gaz', 'infere'), ('dpe.etiquette', 'E', 'document'),
                        ('dpe.conso_ep_kwh_m2', 290.0, 'document')):
            facts.record(b, k, v, m)
        run, _ = calc_service.run_level(b, 'N1', weather_spec={'points': synthetic_year()}, uncertainty=False)
        ind = run.indicators
        for k in ('final_kwh_m2', 'primary_kwh_m2', 'co2_kg_m2', 'cost_eur', 'dpe_label_model', 'dpe_gap_pct'):
            self.assertIn(k, ind)
        self.assertIn(ind['dpe_label_model'], 'ABCDEFG')
        self.assertGreater(ind['primary_kwh_m2'], ind['heating_kwh_m2'])
        facts.record(b, 'systeme.generateur', 'pac_air_eau', 'declare', author_sub='u')
        run2, _ = calc_service.run_level(b, 'N1', weather_spec={'points': synthetic_year()}, uncertainty=False)
        self.assertLess(run2.indicators['co2_kg_m2'], ind['co2_kg_m2'])
        self.assertIn('cop_seasonal', run2.results['energy'])
