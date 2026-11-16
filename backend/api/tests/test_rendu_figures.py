"""Lot 38 (secteur R) — figures Plotly construites par le serveur : toutes les formes I6, séries, facettes,
filtres, agrégations, refus, droits, échappement. Sources des autres secteurs simulées (forme du contrat)."""

import json
import os
import re

from django.test import TestCase

from .. import testing
from ..rendu import catalogue, gabarits
from .test_rendu_fixtures import HOSTILE, RenduMixin

_NO_AUTO_ENV = testing.without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


DH_ECHAPPE = 'Minimiser les DH &lt;img src=x onerror=alert(1)&gt;"\'&amp;&lt;/script&gt;'
TYPES = {'barres': 'bar', 'barres_empilees': 'bar', 'barres_groupees': 'bar', 'courbes': 'scatter', 'aires': 'scatter',
         'nuage': 'scatter', 'bulles': 'scatter', 'boite': 'box', 'violon': 'violin', 'histogramme': 'histogram',
         'camembert': 'pie', 'radar': 'scatterpolar', 'carte_chaleur': 'heatmap', 'cascade': 'waterfall',
         'parallele': 'parcoords'}
COULEUR = re.compile(r'#[0-9a-fA-F]{3,8}\b|rgba?\(')
CLES_COULEUR = {'color', 'colors', 'colorscale', 'fillcolor', 'bgcolor', 'paper_bgcolor', 'plot_bgcolor', 'colorway'}


def _sans_couleur(test, obj, chemin=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CLES_COULEUR and isinstance(v, str):
                test.fail(f'couleur en dur : {chemin}.{k}')
            if k == 'colorscale':
                test.fail(f'échelle en dur : {chemin}.{k}')
            _sans_couleur(test, v, f'{chemin}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _sans_couleur(test, v, f'{chemin}[{i}]')


class FiguresTest(RenduMixin, TestCase):
    def verifier(self, fig, forme):
        self.assertFalse(fig['vide'], fig['layout']['meta'])
        self.assertTrue(fig['data'])
        self.assertEqual({tr['type'] for tr in fig['data']}, {TYPES[forme]}, forme)
        meta = fig['layout']['meta']
        self.assertIn(meta['palette'], ('categorielle', 'sequentielle', 'divergente'))
        self.assertTrue(meta['resume'].strip())
        _sans_couleur(self, fig)
        self.assertIsNone(COULEUR.search(json.dumps(fig)), forme)
        if forme in ('carte_chaleur', 'parallele'):
            self.assertEqual(meta['palette'], 'sequentielle')
        if forme == 'cascade':
            self.assertEqual(meta['palette'], 'divergente')

    def test_toutes_les_formes_sur_un_parc_et_un_batiment(self):
        vues = set()
        dump = {}
        for cle, _q, portees, g in gabarits.QUESTIONS:
            for portee in portees:
                r = self.figure(self.l, g, portee)
                self.assertEqual(r.status_code, 200, (cle, portee, r.content[:300]))
                fig = r.json()
                self.verifier(fig, g['forme'])
                vues.add(g['forme'])
                dump[f'{cle}-{portee}'] = fig
        self.assertEqual(vues, set(catalogue.FORMES))
        extras = {
            'facettes-barres': {'forme': 'barres', 'x': 'bouquet', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets',
                                'facettes': 'usage', 'axe_y_log': True},
            'facettes-camembert': {'forme': 'camembert', 'x': 'poste_energie', 'y': ['energie.poste_kwh'],
                                   'series': 'reel_et_bouquets'},
            'facettes-chaleur': {'forme': 'carte_chaleur', 'x': 'mois', 'couleur': 'bouquet', 'y': ['mensuel.dh_re2020'],
                                 'series': 'reel_et_bouquets', 'facettes': 'usage'},
            'nuage-categories': {'forme': 'nuage', 'x': 'usage', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets',
                                 'agregation': 'moyenne'},
            'courbes-niveaux': {'forme': 'courbes', 'x': 'mois', 'y': ['mensuel.heating_kwh'], 'couleur': 'niveau',
                                'series': 'reel', 'agregation': 'somme'},
        }
        for k, g in extras.items():
            r = self.figure(self.g, g)
            self.assertEqual(r.status_code, 200, (k, r.content[:300]))
            self.verifier(r.json(), g['forme'])
            dump[k] = r.json()
        from types import SimpleNamespace
        from ..rendu import figures
        from ..rendu.donnees import Contexte
        req = SimpleNamespace(user=SimpleNamespace(sub='g', is_authenticated=True), auth=None, META={})
        dump['carte'] = figures.carte(Contexte(req, 'parc', {'parc': self.p.pk}), 'heating_kwh_m2', 'reel')
        if os.environ.get('RENDU_DUMP'):
            print('\n@@FIGURES@@' + json.dumps(dump) + '@@FIN@@')

    def test_formes_bâtiment_pour_celles_du_parc(self):
        # boîte, violon, histogramme, bulles, parallèle n'ont de sens que sur plusieurs bâtiments, mais marchent seuls.
        for forme, g in (('boite', {'forme': 'boite', 'x': 'bouquet', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets'}),
                         ('parallele', {'forme': 'parallele', 'x': 'bouquet', 'y': ['heating_kwh_m2', 'dh_re2020'],
                                        'series': 'reel_et_bouquets'}),
                         ('histogramme', {'forme': 'histogramme', 'x': 'heating_kwh_m2', 'y': [], 'series': 'reel_et_bouquets'})):
            r = self.figure(self.g, g, 'batiment')
            self.assertEqual(r.status_code, 200, r.content)
            self.verifier(r.json(), forme)

    def test_series_et_valeurs(self):
        r = self.figure(self.g, {'forme': 'barres', 'x': 'bouquet', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets',
                                 'agregation': 'moyenne'}).json()
        tr = r['data'][0]
        vals = dict(zip(tr['x'], tr['y']))
        self.assertAlmostEqual(vals['Réel'], 90.0)                         # (100 + 80) / 2
        self.assertAlmostEqual(vals['Économiser l’énergie'], 47.5)         # (50 + 45) / 2
        self.assertAlmostEqual(vals[DH_ECHAPPE], 90.0)
        somme = self.figure(self.g, {'forme': 'barres', 'x': 'bouquet', 'y': ['heating_kwh'], 'series': 'reel',
                                     'agregation': 'somme'}).json()
        self.assertAlmostEqual(somme['data'][0]['y'][0], 12000 + 9600)
        med = self.figure(self.g, {'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2'], 'series': [self.bq1.pk],
                                   'agregation': 'mediane'}).json()
        self.assertEqual(dict(zip(med['data'][0]['x'], med['data'][0]['y'])), {'Bureaux': 50.0, 'Maison individuelle': 45.0})

    def test_mensuel_poste_orientation_action(self):
        f = self.figure(self.g, {'forme': 'courbes', 'x': 'mois', 'y': ['mensuel.heating_kwh'], 'series': 'reel',
                                 'agregation': 'somme'}).json()
        self.assertEqual(f['data'][0]['x'][0], 'janv.')
        self.assertEqual(len(f['data'][0]['x']), 12)
        self.assertAlmostEqual(sum(f['data'][0]['y']), 12000 + 9600, delta=1)
        f = self.figure(self.g, {'forme': 'barres', 'x': 'orientation', 'y': ['solaire.baies_kwh'], 'series': 'reel',
                                 'agregation': 'somme'}).json()
        self.assertEqual(f['data'][0]['x'], ['N', 'E', 'S', 'O'])
        f = self.figure(self.g, {'forme': 'barres_empilees', 'x': 'bouquet', 'y': ['actions.nombre'], 'series': 'bouquets',
                                 'couleur': 'famille_action', 'agregation': 'somme'}).json()
        noms = {tr['name'] for tr in f['data']}
        self.assertEqual(noms, {'Isolation', 'Protections solaires', 'Chauffage et systèmes'})
        self.assertEqual(f['layout']['barmode'], 'stack')
        f = self.figure(self.g, {'forme': 'barres', 'x': 'action', 'y': ['actions.nombre'], 'series': 'bouquets',
                                 'couleur': 'application', 'filtre': {'application': ['ecartee']}, 'agregation': 'somme'}).json()
        self.assertEqual([tr['name'] for tr in f['data']], ['Écartée · Économiser l’énergie',
                                                            'Écartée · ' + DH_ECHAPPE])

    def test_cascade_ferme_exactement(self):
        f = self.figure(self.g, {'forme': 'cascade', 'x': 'poste_bilan', 'y': ['heating_kwh'], 'series': [self.bq1.pk],
                                 'agregation': 'somme'}).json()
        tr = f['data'][0]
        self.assertEqual(tr['measure'][0], 'absolute')
        self.assertEqual(tr['measure'][-1], 'total')
        self.assertAlmostEqual(tr['y'][0], 12000 + 9600)
        self.assertAlmostEqual(tr['y'][0] + sum(tr['y'][1:-1]), tr['y'][-1], places=1)
        self.assertAlmostEqual(tr['y'][-1], 6000 + 0.45 * 12000, places=1)
        # parois : (0,5 − 1) × 15000 + (0,45 − 0,8) × 15000
        self.assertAlmostEqual(tr['y'][1], -7500 - 5250, places=1)
        self.assertIn('Économiser', f['layout']['meta']['resume'])

    def test_facettes_filtres_niveaux_log(self):
        f = self.figure(self.g, {'forme': 'barres', 'x': 'bouquet', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets',
                                 'facettes': 'usage', 'axe_y_log': True}).json()
        self.assertEqual(f['layout']['grid']['rows'] * f['layout']['grid']['columns'] >= 2, True)
        self.assertIn('xaxis2', f['layout'])
        self.assertEqual(f['layout']['yaxis']['type'], 'log')
        self.assertEqual({tr['xaxis'] for tr in f['data']}, {'x', 'x2'})
        f = self.figure(self.g, {'forme': 'barres', 'x': 'batiment', 'y': ['heating_kwh_m2'], 'series': 'reel',
                                 'filtre': {'usage': ['bureau'], 'periode': [1960, 1980]}}).json()
        self.assertEqual(f['data'][0]['x'], ['Mairie'])
        f = self.figure(self.g, {'forme': 'barres', 'x': 'niveau', 'y': ['heating_kwh_m2'], 'series': 'reel',
                                 'filtre': {'batiment': [self.b1.pk]}}).json()
        self.assertEqual(f['data'][0]['x'], ['N0 — mensuel', 'N1 — horaire simplifié'])
        f = self.figure(self.g, {'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2'], 'series': 'reel',
                                 'filtre': {'usage': ['enseignement']}}).json()
        self.assertTrue(f['vide'])
        self.assertTrue(f['layout']['meta']['resume'].startswith('Aucune donnée'))
        self.assertEqual(f['data'], [])

    def test_faits_par_serie_et_correlation(self):
        f = self.figure(self.g, {'forme': 'nuage', 'x': 'heating_kwh_m2', 'y': ['dh_re2020'], 'series': 'reel_et_bouquets',
                                 'couleur': 'bouquet'}).json()
        self.assertIn('Corrélation', f['layout']['meta']['resume'])
        f = self.figure(self.g, {'forme': 'barres', 'x': 'batiment', 'y': ['identite.surface_m2'], 'series': 'reel'}).json()
        self.assertEqual(f['data'][0]['y'], [120.0, 120.0])

    def test_refus_messages_fixes(self):
        cas = [({'forme': 'inconnue', 'x': 'usage', 'y': ['heating_kwh_m2']}, 'Forme de graphe inconnue.'),
               ({'forme': 'barres', 'x': '<b>', 'y': ['heating_kwh_m2']}, 'Dimension inconnue en abscisse.'),
               ({'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2', 'mensuel.heating_kwh']},
                'Ces mesures ne se comptent pas à la même échelle : un graphe par échelle.'),
               ({'forme': 'barres', 'x': 'mois', 'y': ['heating_kwh_m2']}, 'Cette dimension n’existe pas pour ces mesures.'),
               ({'forme': 'cascade', 'x': 'poste_bilan', 'y': ['heating_kwh'], 'series': 'reel'},
                'Cascade : un bouquet est attendu dans les séries.'),
               ({'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2'], 'filtre': {'evil': 1}}, 'Filtre inconnu.'),
               ({'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2'], 'titre': 'x' * 500},
                'Titre de graphe trop long (200 caractères au plus).')]
        for g, msg in cas:
            r = self.figure(self.g, g)
            self.assertEqual(r.status_code, 400, g)
            self.assertEqual(r.json(), {'detail': msg})
        r = self.g.post('/api/rendu/figure/', {'portee': 'parc', 'cible': {'batiment': self.b1.pk}, 'graphe': {}},
                        format='json')
        self.assertEqual(r.status_code, 400)

    def test_droits(self):
        g = {'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2']}
        self.assertEqual(self.figure(self.o, g).status_code, 404)
        self.assertEqual(self.figure(self.o, g, 'batiment').status_code, 404)
        self.assertEqual(self.figure(self.l, g).status_code, 200)
        from rest_framework.test import APIClient
        self.assertIn(APIClient().post('/api/rendu/figure/', {}, format='json').status_code, (401, 403))

    def test_noms_hostiles_echappes(self):
        f = self.figure(self.g, {'forme': 'barres', 'x': 'batiment', 'y': ['heating_kwh_m2'], 'series': 'reel_et_bouquets'}).json()
        texte = json.dumps(f, ensure_ascii=False)
        self.assertNotIn('<img', texte)
        self.assertNotIn('</script', texte)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', texte)

    def test_carte_sans_fond(self):
        from ..rendu import figures
        from ..rendu.donnees import Contexte
        ctx = Contexte(self._req('g'), 'parc', {'parc': self.p.pk})
        f = figures.carte(ctx, 'heating_kwh_m2', 'reel')
        self.assertFalse(f['vide'])
        self.assertEqual(f['data'][0]['type'], 'scatter')          # ni scattergeo (topologie en ligne) ni tuiles
        self.assertNotIn('mapbox', json.dumps(f))
        self.assertNotIn('geo', f['layout'])
        self.assertEqual(f['layout']['meta']['palette'], 'sequentielle')

    def _req(self, sub):
        from types import SimpleNamespace
        return SimpleNamespace(user=SimpleNamespace(sub=sub, is_authenticated=True), auth=None, META={})


class CataloguesTest(RenduMixin, TestCase):
    def test_dimensions_mesures_schema(self):
        d = self.l.get('/api/rendu/dimensions/').json()
        cles = {x['cle'] for x in d}
        for k in ('batiment', 'usage', 'periode_construction', 'classe_dpe', 'approfondissement', 'mois', 'heure',
                  'orientation', 'bouquet', 'action', 'famille_action', 'niveau', 'poste_bilan'):
            self.assertIn(k, cles)
        for x in d:
            self.assertEqual(set(x) - {'formes'}, {'cle', 'libelle', 'unite', 'portees', 'type'})
            self.assertIn(x['type'], ('categorie', 'ordonnee', 'temps'))
        m = self.l.get('/api/rendu/mesures/').json()
        by = {x['cle']: x for x in m}
        for k in ('dh_re2020', 'dh_26', 'heures_inconfort', 'cooling_kwh_m2', 'mensuel.heating_kwh', 'mensuel.dh_re2020',
                  'bilan.kwh', 'identite.surface_m2'):
            self.assertIn(k, by)
        self.assertNotIn('t_conf_methode', by)
        self.assertEqual(by['dh_re2020']['libelle'], 'DH RE2020')          # libellé repris d'I3
        for x in m:
            self.assertEqual(set(x) - {'formes'}, {'cle', 'libelle', 'unite', 'portees', 'famille'})
            self.assertIsInstance(x['unite'], str)
            self.assertIn(x['famille'], ('energie', 'confort', 'donnees', 'bilan'))
        s = self.l.get('/api/rendu/schema/').json()
        self.assertEqual(s['properties']['version'], {'const': 1})
        self.assertEqual(s['$defs']['graphe']['properties']['forme']['enum'], catalogue.FORMES)

    def test_gabarits_questions(self):
        r = self.l.get('/api/rendu/gabarits/').json()
        self.assertEqual(set(r), {'modeles', 'questions'})
        self.assertEqual([m['nom'] for m in r['modeles']], ['Fiche d’identité', 'Synthèse de parc'])
        for m in r['modeles']:
            self.assertEqual(set(m), {'id', 'nom', 'portee', 'spec', 'par_defaut', 'portfolio', 'modifiable'})
            self.assertTrue(m['par_defaut'])
            self.assertFalse(m['modifiable'])
            self.assertIsNone(m['portfolio'])
        self.assertEqual({q['graphe']['forme'] for q in r['questions']}, set(catalogue.FORMES))
        for q in r['questions']:
            self.assertEqual(set(q), {'cle', 'question', 'portees', 'graphe'})
        # Idempotent : un second appel ne crée rien.
        from ..models import RapportModele
        n = RapportModele.objects.count()
        self.l.get('/api/rendu/gabarits/')
        self.assertEqual(RapportModele.objects.count(), n)
