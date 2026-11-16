"""Choix des simulations affichées par un bloc de rendu (`niveaux`, lot 45) : chiffres clés, tableau et carte
montrent la ou les simulations choisies ; plusieurs ⇒ une colonne par simulation pour comparer une même grandeur."""

from django.test import TestCase

from .. import testing
from ..rendu import spec as spec_mod
from .test_rendu_fixtures import RenduMixin
from .test_rendu_niveaux import NiveauxRenduTest

_NO_AUTO_ENV = testing.without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


def doc(*blocs, portee='batiment'):
    return {'version': 1, 'portee': portee, 'sections': [{'blocs': list(blocs)}]}


class ValidationTest(TestCase):
    def test_niveaux_valides_ranges_et_facultatifs(self):
        v = spec_mod.valider(doc({'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel',
                                  'niveaux': ['N2', 'N0', 'N2']}))
        self.assertEqual(v['sections'][0]['blocs'][0]['niveaux'], ['N0', 'N2'])
        v = spec_mod.valider(doc({'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel', 'niveaux': []}))
        self.assertNotIn('niveaux', v['sections'][0]['blocs'][0])        # vide = la plus fine à jour, comme avant
        for mauvais in (['N3'], 'N1', [1]):
            with self.assertRaises(spec_mod.SpecError):
                spec_mod.valider(doc({'type': 'tableau', 'source': 'batiments', 'niveaux': mauvais}))
        v = spec_mod.valider(doc({'type': 'tableau', 'source': 'batiments', 'colonnes': []}))
        self.assertTrue(v['sections'][0]['blocs'][0]['colonnes'])          # tableau neuf : colonnes par défaut
        v = spec_mod.valider(doc({'type': 'tableau', 'source': 'batiments', 'colonnes': [], 'tri': ''},
                                 {'type': 'indicateurs', 'cles': [], 'series': 'reel'}))
        self.assertIsNone(v['sections'][0]['blocs'][0]['tri'])
        self.assertEqual(v['sections'][0]['blocs'][1]['cles'], [])
        with self.assertRaises(spec_mod.SpecError):
            spec_mod.valider(doc({'type': 'carte', 'indicateur': 'heating_kwh_m2', 'niveaux': ['N0', 'N1']}))


class SimulationsRenduTest(RenduMixin, TestCase):
    client_doc = NiveauxRenduTest.client_doc
    n2_a_jour = NiveauxRenduTest.n2_a_jour

    def test_chiffres_cles_une_colonne_par_simulation(self):
        self.n2_a_jour(self.b1, k=0.3)
        d = self.client_doc(doc({'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel_et_bouquets',
                                 'niveaux': ['N0', 'N1', 'N2']}))
        ind = d['sections'][0]['blocs'][0]
        cols = [(c['serie'], c['niveau']) for c in ind['colonnes']]
        self.assertEqual(cols[:3], [('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2')])
        self.assertEqual([c['libelle'] for c in ind['colonnes'][:3]], ['Réel (N0)', 'Réel (N1)', 'Réel (N2)'])
        v = ind['lignes'][0]['valeurs']
        self.assertAlmostEqual(v[0], 110.0)
        self.assertAlmostEqual(v[1], 100.0)
        self.assertAlmostEqual(v[2], 30.0)
        # Bouquet 1 en N1 (50) : écart au réel N1 (100), pas au réel d'une autre simulation.
        i = cols.index((self.bq1.pk, 'N1'))
        self.assertAlmostEqual(v[i], 50.0)
        self.assertAlmostEqual(ind['lignes'][0]['ecarts'][i], -50.0)
        self.assertIsNone(ind['lignes'][0]['ecarts'][0])

    def test_bloc_neuf_ne_bloque_pas_le_document(self):
        d = self.client_doc(doc({'type': 'indicateurs', 'cles': [], 'series': 'reel'},
                                {'type': 'tableau', 'source': 'batiments', 'colonnes': [], 'tri': ''}))
        ind, tab = d['sections'][0]['blocs']
        self.assertEqual(ind['indisponible'], 'Aucun chiffre choisi : bloc à régler.')
        self.assertTrue(tab['colonnes'])

    def test_chiffres_cles_une_seule_simulation_imposee(self):
        self.n2_a_jour(self.b1, k=0.3)
        ind = self.client_doc(doc({'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel',
                                   'niveaux': ['N1']}))['sections'][0]['blocs'][0]
        self.assertEqual(ind['colonnes'], [{'serie': 'reel', 'libelle': 'Réel (N1)', 'niveau': 'N1'}])
        self.assertAlmostEqual(ind['lignes'][0]['valeurs'][0], 100.0)   # N1, bien que le N2 soit à jour

    def test_tableau_batiments_eclate_par_simulation(self):
        self.n2_a_jour(self.b1, k=0.3)
        tab = self.client_doc(doc({'type': 'tableau', 'source': 'batiments', 'niveaux': ['N1', 'N2'], 'tri': 'heating_kwh_m2',
                                   'ordre': 'desc', 'colonnes': ['batiment', 'niveau_calcul', 'heating_kwh_m2', 'usage'],
                                   'limite': 10}, portee='parc'), {'parc': self.p.pk})['sections'][0]['blocs'][0]
        self.assertEqual([c['cle'] for c in tab['colonnes']],
                         ['batiment', 'niveau_calcul', 'heating_kwh_m2@N1', 'heating_kwh_m2@N2', 'usage'])
        self.assertEqual([c['libelle'] for c in tab['colonnes']][2:4],
                         ['Besoin de chauffage (N1)', 'Besoin de chauffage (N2)'])
        par_nom = {x[0]: x for x in tab['lignes']}
        self.assertEqual(par_nom['Mairie'][1:4], ['N1, N2', 100.0, 30.0])
        self.assertEqual(tab['lignes'][0][0], 'Mairie')                 # trié sur la plus fine (N2)

    def test_tableau_une_simulation_et_potentiels_filtres(self):
        self.n2_a_jour(self.b1, k=0.3)
        tab, pot, calc = self.client_doc(doc(
            {'type': 'tableau', 'source': 'batiments', 'niveaux': ['N0'], 'colonnes': ['batiment', 'niveau_calcul',
                                                                                     'heating_kwh_m2'], 'limite': 10},
            {'type': 'tableau', 'source': 'potentiels', 'niveaux': ['N2'], 'colonnes': ['bouquet', 'niveau', 'statut'],
             'limite': 50},
            {'type': 'tableau', 'source': 'calculs', 'niveaux': ['N0'], 'colonnes': ['niveau'], 'limite': 50},
        ))['sections'][0]['blocs']
        self.assertEqual(tab['lignes'][0][1:], ['N0', 110.0])
        self.assertEqual({tuple(x[1:]) for x in pot['lignes']}, {('N2', 'non calculé')})
        self.assertTrue(calc['lignes'])
        self.assertEqual({x[0] for x in calc['lignes']}, {'N0'})

    def test_carte_a_la_simulation_choisie(self):
        d = self.client_doc(doc({'type': 'carte', 'indicateur': 'heating_kwh_m2', 'series': 'reel', 'niveaux': ['N0']},
                                portee='parc'), {'parc': self.p.pk})
        fig = d['sections'][0]['blocs'][0]['figure']
        self.assertFalse(fig['vide'])
        self.assertEqual(fig['data'][0]['customdata'], [110.0])          # seul b1 a un N0
        self.assertIn('N0 — mensuel', fig['layout']['meta']['resume'])
