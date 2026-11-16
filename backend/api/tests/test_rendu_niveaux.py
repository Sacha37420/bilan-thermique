"""Chantier bt-trois-niveaux, lot 43 (secteur D) — rendu sur trois niveaux (I5) : niveau par défaut = le plus fin
disponible et à jour (dit dans `layout.meta.resume`), filtre et dimension « niveau » inchangés, gabarits et
questions par niveau, indicateurs et tableaux par niveau."""

from django.test import TestCase

from .. import calc_service, testing
from ..ameliorations import potentiels as pot_service
from ..models import CalcRun, Job, Potentiel, Scenario
from ..rendu import gabarits
from .test_rendu_fixtures import RenduMixin, indicateurs, resultats

_NO_AUTO_ENV = testing.without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


BESOIN_REEL = {'forme': 'barres', 'x': 'bouquet', 'y': ['heating_kwh_m2'], 'series': 'reel', 'agregation': 'moyenne'}


class NiveauxRenduTest(RenduMixin, TestCase):
    def n2_a_jour(self, b, k=0.3, scenario=None):
        """Un N2 depuis la fiche, aux entrées d'aujourd'hui (empreinte recalculée comme `calc_service`)."""
        h = calc_service.inputs_hash_for(b, 'N2', scenario, None)
        return CalcRun.objects.create(building=b, scenario=scenario, level='N2', status=Job.DONE, inputs_hash=h,
                                      settings={'engine': 'n2-5'}, weather={}, indicators=indicateurs(k),
                                      results=resultats(k), duration_s=30)

    def valeur(self, fig, nom):
        tr = fig['data'][0]
        return dict(zip(tr['x'], tr['y']))[nom]

    def test_defaut_sans_calcul_a_jour_reste_n1_et_le_dit(self):
        f = self.figure(self.g, BESOIN_REEL).json()
        self.assertAlmostEqual(self.valeur(f, 'Réel'), 90.0)                 # N1 de b1 et b2, comme avant
        self.assertIn('Niveau de calcul : le plus fin disponible et à jour', f['layout']['meta']['resume'])
        self.assertIn('N1 pas à jour pour 2 séries', f['layout']['meta']['resume'])

    def test_defaut_le_plus_fin_a_jour(self):
        self.n2_a_jour(self.b1, k=0.3)
        self.assertTrue(calc_service.is_up_to_date(calc_service.latest(self.b1, 'N2')))
        f = self.figure(self.g, BESOIN_REEL, 'batiment').json()
        self.assertAlmostEqual(self.valeur(f, 'Réel'), 30.0)                 # le N2 à jour l'emporte sur le N1
        self.assertIn('N2 pour 1 série', f['layout']['meta']['resume'])
        # Parc : b1 en N2 (à jour), b2 en N1 (pas à jour) — moyenne des deux, et le résumé le dit.
        f = self.figure(self.g, BESOIN_REEL).json()
        self.assertAlmostEqual(self.valeur(f, 'Réel'), (30.0 + 80.0) / 2)
        self.assertIn('N2 pour 1 série, N1 pas à jour pour 1 série', f['layout']['meta']['resume'])
        # Un filtre de niveau impose le niveau ; rien n'est dit sur le choix automatique.
        f = self.figure(self.g, {**BESOIN_REEL, 'filtre': {'niveau': ['N1']}}, 'batiment').json()
        self.assertAlmostEqual(self.valeur(f, 'Réel'), 100.0)
        self.assertNotIn('Niveau de calcul', f['layout']['meta']['resume'])
        # La dimension « niveau » montre les trois.
        f = self.figure(self.g, {'forme': 'barres', 'x': 'niveau', 'y': ['heating_kwh_m2'], 'series': 'reel'},
                        'batiment').json()
        self.assertEqual(f['data'][0]['x'], ['N0 — mensuel', 'N1 — horaire simplifié', 'N2 — détaillé par paroi'])

    def test_potentiel_a_jour_choisi_par_bouquet(self):
        p = Potentiel.objects.get(building=self.b1, bouquet=self.bq1, niveau='N1')
        p.empreinte = pot_service.empreinte(self.b1, self.bq1, 'N1')
        p.save()
        s = Scenario.objects.create(building=self.b1, name='n0', bouquet=self.bq1, genere=False)
        r0 = CalcRun.objects.create(building=self.b1, scenario=s, level='N0', status=Job.DONE,
                                    indicators=indicateurs(0.2), results=resultats(0.2))
        Potentiel.objects.create(building=self.b1, bouquet=self.bq1, scenario=s, niveau='N0', statut='fait', run=r0,
                                 empreinte='ancienne')
        f = self.figure(self.g, {**BESOIN_REEL, 'series': [self.bq1.pk]}, 'batiment').json()
        self.assertAlmostEqual(self.valeur(f, 'Économiser l’énergie'), 50.0)    # N1 à jour > N0 pas à jour
        self.assertIn('N1 pour 1 série', f['layout']['meta']['resume'])
        # Tableau des potentiels : une ligne N1 par bouquet, plus la ligne N0 calculée, colonne « niveau ».
        spec = {'version': 1, 'portee': 'batiment', 'sections': [{'blocs': [
            {'type': 'tableau', 'source': 'potentiels', 'colonnes': ['bouquet', 'niveau', 'heating_kwh_m2',
                                                                    'ecart.heating_kwh_m2'], 'limite': 50},
            {'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel_et_bouquets'}]}]}
        d = self.client_doc(spec)
        tab, ind = d['sections'][0]['blocs']
        lignes = [tuple(x[:2]) for x in tab['lignes']]
        self.assertEqual(sorted(lignes), sorted([('Économiser l’énergie', 'N0'), ('Économiser l’énergie', 'N1'),
                                                 (self.bq2.nom, 'N1')]))
        n0 = next(x for x in tab['lignes'] if x[1] == 'N0')
        self.assertAlmostEqual(n0[2], 20.0)
        self.assertAlmostEqual(n0[3], 20.0 - 110.0)                          # écart au réel N0 de b1 (k = 1,1)
        self.assertEqual([c.get('niveau') for c in ind['colonnes']], ['N1', 'N1', 'N1'])
        self.assertEqual(ind['colonnes'][0]['libelle'], 'Réel (N1)')

    def client_doc(self, spec, cible=None):
        r = self.g.post('/api/rendu/document/', {'spec': spec, 'cible': cible or {'batiment': self.b1.pk}},
                        format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_indicateurs_et_tableau_batiments_au_niveau_le_plus_fin(self):
        self.n2_a_jour(self.b1, k=0.3)
        spec = {'version': 1, 'portee': 'parc', 'sections': [{'blocs': [
            {'type': 'indicateurs', 'cles': ['heating_kwh_m2'], 'series': 'reel'},
            {'type': 'tableau', 'source': 'batiments', 'colonnes': ['batiment', 'niveau_calcul', 'heating_kwh_m2'],
             'tri': 'batiment', 'limite': 10}]}]}
        ind, tab = self.client_doc(spec, {'parc': self.p.pk})['sections'][0]['blocs']
        self.assertEqual(ind['colonnes'][0]['niveau'], 'N2, N1')
        self.assertEqual(ind['colonnes'][0]['libelle'], 'Réel (N2, N1)')
        self.assertAlmostEqual(ind['lignes'][0]['valeurs'][0], (30.0 + 80.0) / 2)
        par_nom = {x[0]: x for x in tab['lignes']}
        self.assertEqual(par_nom['Mairie'][1:], ['N2', 30.0])
        self.assertEqual(par_nom['Hangar'][1:], [None, None])

    def test_cascade_au_meme_niveau(self):
        g = {'forme': 'cascade', 'x': 'poste_bilan', 'y': ['heating_kwh'], 'series': [self.bq1.pk], 'agregation': 'somme'}
        f = self.figure(self.g, g).json()
        self.assertIn('N1 pour 2 bâtiments', f['layout']['meta']['resume'])
        f = self.figure(self.g, {**g, 'filtre': {'niveau': ['N2']}}).json()
        self.assertTrue(f['vide'])
        self.assertIn('au même niveau', f['layout']['meta']['resume'])

    def test_gabarits_trois_niveaux(self):
        cles = [c for c, _q, _p, _g in gabarits.QUESTIONS]
        for c in ('niveaux_reel', 'niveaux_bouquets', 'niveaux_dh'):
            self.assertIn(c, cles)
        r = self.l.get('/api/rendu/gabarits/').json()
        q = {x['cle']: x for x in r['questions']}
        self.assertEqual(q['niveaux_bouquets']['graphe']['couleur'], 'niveau')
        self.assertEqual(q['niveaux_reel']['graphe']['x'], 'niveau')
        for m in r['modeles']:
            titres = [s['titre'] for s in m['spec']['sections']]
            self.assertIn('Trois niveaux de calcul', titres, m['nom'])
        # Sur le parc de test : réel N0 + N1 de b1, N1 de b2 ; potentiels N1 — chaque niveau présent a sa trace.
        f = self.figure(self.l, q['niveaux_bouquets']['graphe']).json()
        self.assertFalse(f['vide'])
        noms = {tr['name'] for tr in f['data']}
        self.assertEqual(noms, {'N0 — mensuel', 'N1 — horaire simplifié'})
        # Les documents des deux gabarits se construisent.
        for m in gabarits.assurer():
            cible = {'parc': self.p.pk} if m.portee == 'parc' else {'batiment': self.b1.pk}
            r = self.g.post('/api/rendu/document/', {'modele_id': m.pk, 'cible': cible}, format='json')
            self.assertEqual(r.status_code, 200, m.nom)
