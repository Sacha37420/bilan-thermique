"""Lot 17 (refonte) — mesures, sensibilité de Morris, calage mensuel (critères ASHRAE 14)."""

from datetime import date
from unittest import mock

import numpy as np
from django.test import SimpleTestCase, TestCase

from .. import calibration, facts
from ..engines import inputs as engine_inputs
from ..engines import n1
from ..models import Building, Job, Measurement
from ..testing import client_for, patch_jwks, synthetic_year

YEAR = synthetic_year()
MONTHS = [(date(2023, m, 1), date(2023 + (m == 12), 1 if m == 12 else m + 1, 1)) for m in range(1, 13)]


def seeded(**over):
    b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
    base = {'identite.usage': ('maison', 'infere'), 'identite.surface_m2': (120.0, 'infere'), 'mur.u': (1.6, 'defaut'),
            'toiture.u': (0.8, 'defaut'), 'ventilation.n50': (9.0, 'defaut'), 'ventilation.type': ('naturelle', 'defaut'),
            'ventilation.debit_vol_h': (0.6, 'defaut'), 'baies.uw': (2.9, 'defaut'), 'usage.consigne_chauffage_c': (19.0, 'defaut'),
            'systeme.generateur': ('chaudiere_gaz', 'infere'), 'systeme.ecs': ('ballon_elec', 'infere'),
            'usage.apports_w_m2': (4.0, 'defaut')}
    base.update(over)
    for k, (v, m) in base.items():
        facts.record(b, k, v, m, author_sub='u' if m in facts.USER_METHODS else '')
    return b


def twin_bills(truth, noise=0.02, seed=3):
    """Factures mensuelles de gaz produites par le modèle avec des valeurs « vraies » cachées."""
    p = engine_inputs.from_fiche(truth)
    r = n1.run([p], YEAR, keep_series=True)
    bills = [{'start': s.isoformat(), 'end': e.isoformat(), 'kwh': 1} for s, e in MONTHS]
    kwh = calibration.consumption_by_bill(truth, r, YEAR, bills, 'gaz', p.a_floor, p.volume)[0]
    rng = np.random.default_rng(seed)
    return [{**b, 'kwh': round(float(k * (1 + rng.normal(0, noise))), 1)} for b, k in zip(bills, kwh)]


class ParsingTest(SimpleTestCase):
    def test_bills_and_curve(self):
        bills = calibration.parse_bills([{'start': '01/01/2024', 'end': '2024-02-01', 'kwh': '1 234,5'.replace(' ', '')}])
        self.assertEqual(bills[0]['kwh'], 1234.5)
        with self.assertRaises(calibration.MeasureError):
            calibration.parse_bills([{'start': '2024-02-01', 'end': '2024-01-01', 'kwh': 10}])
        curve = 'date;kWh\n' + ''.join(f'2024-01-{d:02d};{10 + d}\n' for d in range(1, 32)) + '2024-02-01;5\n'
        m = calibration.parse_curve_csv(curve)
        self.assertEqual(len(m), 2)
        self.assertEqual(m[0]['kwh'], sum(10 + d for d in range(1, 32)))

    def test_ashrae_criteria(self):
        c = calibration.criteria([100, 200, 300], [102, 195, 305])
        self.assertTrue(c['ok'])
        self.assertFalse(calibration.criteria([100, 200, 300], [150, 250, 400])['ok'])


class MorrisAndCalibrationTest(TestCase):
    def test_morris_ranks_influential_parameters(self):
        b = seeded()
        rank = calibration.morris(b, facts.effective(b), YEAR[:24 * 120], None, r=5)
        keys = [x['key'] for x in rank[:4]]
        self.assertIn('mur.u', keys)
        self.assertGreater(rank[0]['mu_star'], rank[-1]['mu_star'])

    def test_twin_calibration_meets_ashrae_criteria(self):
        """Recette du lot (cas jumeau) : calage mensuel conforme aux critères § B6."""
        b = seeded()
        truth = {**facts.effective_values(b), 'mur.u': 0.6, 'ventilation.n50': 4.0}
        bills = twin_bills(truth)
        report = calibration.calibrate(b, bills, 'gaz', YEAR)
        import json
        json.dumps(report)  # le compte rendu est enregistré dans Job.result (JSON) : aucun type numpy
        self.assertFalse(report['before']['ok'])
        self.assertTrue(report['after']['ok'], report['after'])
        moved = {p['key']: p for p in report['parameters']}
        self.assertTrue(any(k in moved for k in ('mur.u', 'ventilation.n50', 'ventilation.debit_vol_h')))
        calibration.record_calibration(b, report)
        f = facts.effective(b)[report['parameters'][0]['key']]
        self.assertEqual(f.method, 'infere')
        self.assertIn('calage', f.source)

    def test_declared_values_are_never_calibrated(self):
        b = seeded(**{'mur.u': (1.6, 'declare')})
        bills = twin_bills({**facts.effective_values(b), 'mur.u': 0.6})
        report = calibration.calibrate(b, bills, 'gaz', YEAR, max_params=3)
        self.assertNotIn('mur.u', [p['key'] for p in report.get('parameters', [])])


@patch_jwks()
class MeasurementApiTest(TestCase):
    def test_privacy_test_fact_and_apply_by_owner_only(self):
        b = seeded()
        me, other = client_for(sub='me'), client_for(sub='other')
        r = me.post(f'/api/batiments/{b.pk}/mesures/', {'kind': 'facture', 'energy': 'gaz',
                                                        'rows': [{'start': s.isoformat(), 'end': e.isoformat(), 'kwh': 900} for s, e in MONTHS]},
                    format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(me.get(f'/api/batiments/{b.pk}/mesures/').json()['results']), 1)
        self.assertEqual(other.get(f'/api/batiments/{b.pk}/mesures/').json()['results'], [])  # donnée personnelle
        self.assertEqual(other.delete(f"/api/mesures/{r.json()['id']}/").status_code, 404)
        t = me.post(f'/api/batiments/{b.pk}/mesures/', {'kind': 'test', 'n50': 3.2}, format='json')
        self.assertEqual(t.json()['fact']['method'], 'mesure')
        with mock.patch('api.tasks.calibrate_building.delay') as delay:
            j = me.post(f'/api/batiments/{b.pk}/calage/', {'measurement_id': r.json()['id']}, format='json')
            self.assertEqual(other.post(f'/api/batiments/{b.pk}/calage/', {'measurement_id': r.json()['id']}, format='json').status_code, 400)
        self.assertEqual(j.status_code, 202)
        delay.assert_called_once()
        job = Job.objects.get(pk=j.json()['id'])
        job.status, job.result = Job.DONE, {'parameters': [{'key': 'mur.u', 'to': 0.7}], 'after': {'cv_rmse_pct': 8, 'nmbe_pct': 1}}
        job.save()
        self.assertEqual(other.post(f'/api/batiments/{b.pk}/calage/appliquer/', {'job_id': job.pk}, format='json').status_code, 400)
        self.assertEqual(me.post(f'/api/batiments/{b.pk}/calage/appliquer/', {'job_id': job.pk}, format='json').status_code, 200)
        self.assertEqual(facts.effective_values(b)['mur.u'], 0.7)

    def test_sensitivity_endpoint(self):
        b = seeded()
        with mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR[:24 * 60], 'source': 't'}):
            r = client_for().post(f'/api/batiments/{b.pk}/sensibilite/', {}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()['ranking']), 5)
