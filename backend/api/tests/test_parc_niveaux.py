"""Chantier bt-trois-niveaux, lot 43 (secteur D) — tableau du parc sur trois niveaux (I4) : `niveaux` N0/N1/N2,
`calculs` (état du recalcul, I1 du secteur C), export CSV par niveau ; champs historiques inchangés."""

import csv
import io
from unittest import mock

from django.test import TestCase

from .. import calc_service, parc
from ..models import Building, CalcRun, Job, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment
from .test_calage import seeded

YEAR = synthetic_year()
_env = without_auto_environment()
_tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})

CHAMPS_HISTORIQUES = {'id', 'name', 'address', 'rnb_id', 'status', 'lat', 'lon', 'usage', 'surface_m2', 'annee',
                      'heating_kwh_m2', 'p10', 'p90', 'heating_kwh', 'heating_kw_max', 'hours_over_28', 'dpe',
                      'dpe_ep_kwh_m2', 'final_kwh_m2', 'primary_kwh_m2', 'model_label', 'confidence', 'completeness',
                      'level', 'computed_at', 'priority', 'errors', 'approfondissement', 'modeles'}


def setUpModule():
    _env.start()
    _tmy.start()


def tearDownModule():
    _tmy.stop()
    _env.stop()


@patch_jwks()
class TableauNiveauxTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role='gestionnaire_parc')
        self.b = seeded()
        self.b.portfolio, self.b.name = self.p, 'calculé'
        self.b.save()
        self.vide = Building.objects.create(name='sans calcul', portfolio=self.p)
        self.r0 = calc_service.run_level(self.b, 'N0')[0]
        self.r1 = calc_service.run_level(self.b, 'N1')[0]
        # Un N2 « ancien » : empreinte qui ne correspond plus à la fiche.
        self.r2 = CalcRun.objects.create(building=self.b, level='N2', status=Job.DONE, inputs_hash='ancien',
                                         settings={'engine': 'n2-5'},
                                         indicators={'heating_kwh_m2': 77.5, 'primary_kwh_m2': 120.25,
                                                     'dh_re2020': 310.0, 'hours_over_28': 12})

    def rows(self):
        return {r['id']: r for r in parc.table(self.p, Building.objects.filter(portfolio=self.p).order_by('pk'))}

    def test_niveaux_et_champs_historiques(self):
        rows = self.rows()
        r = rows[self.b.pk]
        self.assertTrue(CHAMPS_HISTORIQUES <= set(r))
        self.assertEqual(r['heating_kwh_m2'], self.r1.indicators['heating_kwh_m2'])          # toujours lus en N1
        self.assertEqual(r['level'], 'N1')
        self.assertEqual(set(r['niveaux']), {'N0', 'N1', 'N2'})
        n0, n1, n2 = r['niveaux']['N0'], r['niveaux']['N1'], r['niveaux']['N2']
        self.assertEqual(set(n0), {'heating_kwh_m2', 'primary_kwh_m2', 'dh_re2020', 'a_jour', 'run_id'})
        self.assertEqual(set(n1), {'heating_kwh_m2', 'heating_kwh_m2_p10', 'heating_kwh_m2_p90', 'primary_kwh_m2',
                                   'dh_re2020', 'hours_over_28', 'a_jour', 'run_id'})
        self.assertEqual(set(n2), {'heating_kwh_m2', 'primary_kwh_m2', 'dh_re2020', 'hours_over_28', 'a_jour', 'run_id'})
        self.assertEqual((n0['run_id'], n0['a_jour'], n0['dh_re2020']), (self.r0.pk, True, None))
        self.assertEqual(n0['heating_kwh_m2'], self.r0.indicators['heating_kwh_m2'])
        self.assertEqual((n1['run_id'], n1['a_jour']), (self.r1.pk, True))
        self.assertEqual(n1['heating_kwh_m2_p90'], self.r1.indicators['heating_kwh_m2_p90'])
        self.assertEqual(n1['dh_re2020'], self.r1.indicators['dh_re2020'])
        self.assertEqual((n2['run_id'], n2['a_jour'], n2['heating_kwh_m2'], n2['hours_over_28']),
                         (self.r2.pk, False, 77.5, 12))
        # Sans calcul : tout à None, a_jour compris (« jamais »).
        for lvl, d in rows[self.vide.pk]['niveaux'].items():
            self.assertTrue(all(v is None for v in d.values()), lvl)
        # État des calculs : module du secteur C (I1), réduit à {statut, restants}.
        self.assertEqual(set(r['calculs']), {'statut', 'restants'})
        self.assertIn(r['calculs']['statut'], ('inactif', 'planifie', 'en_cours', 'erreur'))
        self.assertIsInstance(r['calculs']['restants'], int)

    def test_calculs_suivent_le_recalcul(self):
        # Règle du secteur C (I1) : N0 et N1 seuls ⇒ il reste le N2 et les potentiels à faire.
        r = self.rows()[self.b.pk]
        self.assertGreater(r['calculs']['restants'], 0)
        lignes = [{'id': self.b.pk, 'statut': 'planifie', 'calculs_restants': 4},
                  {'id': self.vide.pk, 'statut': 'en_cours', 'calculs_restants': 2}]
        with mock.patch('api.recalcul.lignes_parc', return_value=({}, lignes)) as lp, \
                mock.patch('api.recalcul.etat_batiment') as eb:
            rows = self.rows()
        lp.assert_called_once()                                  # un seul appel pour tout le parc
        eb.assert_not_called()
        self.assertEqual(rows[self.b.pk]['calculs'], {'statut': 'planifie', 'restants': 4})
        self.assertEqual(rows[self.vide.pk]['calculs'], {'statut': 'en_cours', 'restants': 2})
        with mock.patch('api.recalcul.lignes_parc', side_effect=RuntimeError('x')):
            self.assertEqual(self.rows()[self.b.pk]['calculs'], {'statut': 'inactif', 'restants': 0})

    def test_http_et_csv_par_niveau(self):
        g = client_for(sub='g')
        j = g.get(f'/api/parcs/{self.p.pk}/tableau/').json()
        row = next(x for x in j['rows'] if x['id'] == self.b.pk)
        self.assertEqual(row['niveaux']['N2']['heating_kwh_m2'], 77.5)
        self.assertIn('calculs', row)
        r = g.get(f'/api/parcs/{self.p.pk}/export.csv')
        self.assertEqual(r.status_code, 200)
        lines = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig')), delimiter=';'))
        head = lines[0]
        self.assertEqual(head[:3], ['id', 'nom', 'adresse'])                                 # colonnes historiques d'abord
        for col in ('besoin_n0_kwh_m2', 'energie_primaire_n0_kwh_m2', 'dh_re2020_n0', 'besoin_n2_kwh_m2',
                    'energie_primaire_n2_kwh_m2', 'dh_re2020_n2', 'dh_re2020_n1', 'a_jour_n2', 'calculs',
                    'calculs_restants'):
            self.assertIn(col, head)
        data = {int(x[0]): dict(zip(head, x)) for x in lines[1:]}
        mine = data[self.b.pk]
        self.assertEqual(mine['besoin_n2_kwh_m2'], '77,5')
        self.assertEqual(mine['energie_primaire_n2_kwh_m2'], '120,25')
        self.assertEqual((mine['a_jour_n2'], mine['a_jour_n0'], mine['dh_re2020_n0']), ('non', 'oui', ''))
        self.assertEqual(mine['besoin_kwh_m2'], mine['besoin_n1_kwh_m2'])
        self.assertEqual((mine['calculs'], mine['calculs_restants']), (row['calculs']['statut'],
                                                                       str(row['calculs']['restants'])))
        self.assertEqual(data[self.vide.pk]['besoin_n0_kwh_m2'], '')
