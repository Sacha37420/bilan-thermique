"""Lot 38 (secteur R) — document résolu (I6.c / I6.g), gabarits du lab, bloc « fiche » (fiche d'identité du lot 34
avec ses potentiels par bouquet), modèles de rendu d'un parc (I6.f), consommation réelle des routes des autres
secteurs par leur classe de vue."""

import json
import os
from unittest import mock

from django.test import TestCase

from .. import testing
from ..models import RapportModele
from ..rendu import gabarits
from .test_rendu_fixtures import HOSTILE, RenduMixin

_NO_AUTO_ENV = testing.without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


FORMES_BLOCS = {'texte': {'html'}, 'indicateurs': {'colonnes', 'lignes'}, 'tableau': {'colonnes', 'lignes', 'total_lignes'},
                'graphe': {'figure'}, 'carte': {'figure'}, 'saut_de_page': set(),
                'vue3d': {'calcul_id', 'grandeur', 'libelle', 'unite', 'camera', 'scene'}, 'fiche': {'sections', 'potentiels'}}


def gabarit(nom):
    return next(m for m in gabarits.assurer() if m.nom == nom)


class DocumentTest(RenduMixin, TestCase):
    def doc(self, client, cible, spec=None, modele_id=None):
        body = {'cible': cible, **({'spec': spec} if spec is not None else {'modele_id': modele_id})}
        return client.post('/api/rendu/document/', body, format='json')

    def verifier_formes(self, doc):
        self.assertEqual(set(doc), {'titre', 'mise_en_page', 'sections'})
        for i, s in enumerate(doc['sections']):
            self.assertEqual(set(s), {'titre', 'ancre', 'blocs'})
            self.assertEqual(s['ancre'], f's{i + 1}')
            for b in s['blocs']:
                if 'indisponible' in b:
                    self.assertEqual(set(b), {'type', 'indisponible'})
                    continue
                attendu = FORMES_BLOCS[b['type']]
                if b['type'] == 'fiche' and 'potentiels' not in b:
                    attendu = {'sections'}
                self.assertEqual(set(b) - {'type'}, attendu, b['type'])

    def test_fiche_d_identite_avec_potentiels(self):
        r = self.doc(self.l, {'batiment': self.b1.pk}, modele_id=gabarit('Fiche d’identité').pk)
        self.assertEqual(r.status_code, 200, r.content[:400])
        d = r.json()
        self.verifier_formes(d)
        types = [b['type'] for s in d['sections'] for b in s['blocs']]
        for t in ('fiche', 'indicateurs', 'graphe', 'tableau', 'vue3d', 'texte'):
            self.assertIn(t, types)
        fiches = [b for s in d['sections'] for b in s['blocs'] if b['type'] == 'fiche']
        cles = [fs['cle'] for f in fiches for fs in f['sections']]
        self.assertEqual(cles, ['identite', 'geometrie', 'enveloppe', 'systemes', 'meteo', 'environnement', 'resultats',
                                'potentiels', 'ameliorations', 'sources'])
        pot_bloc = next(f for f in fiches if 'potentiels' in f)
        self.assertEqual(pot_bloc['potentiels']['reel']['run_id'], self.r1.pk)           # réponse I5 telle quelle
        pot = next(fs for f in fiches for fs in f['sections'] if fs['cle'] == 'potentiels')
        libs = [e['libelle'] for e in pot['elements']]
        self.assertIn('Économiser l’énergie — Besoin de chauffage (N1)', libs)
        e = next(e for e in pot['elements'] if e['libelle'] == 'Économiser l’énergie — Besoin de chauffage (N1)')
        self.assertEqual((e['valeur'], e['unite']), ('50 (−50 %)', 'kWh/m²·an'))
        for f in fiches:
            for fs in f['sections']:
                for el in fs['elements']:
                    self.assertEqual(set(el), {'libelle', 'valeur', 'unite', 'source'})
        ind = next(b for s in d['sections'] for b in s['blocs'] if b['type'] == 'indicateurs')
        self.assertEqual([c['serie'] for c in ind['colonnes']], ['reel', self.bq1.pk, self.bq2.pk])
        l0 = ind['lignes'][0]
        self.assertEqual(l0['cle'], 'heating_kwh_m2')
        self.assertEqual(l0['valeurs'], [100.0, 50.0, 90.0])
        self.assertEqual(l0['ecarts'], [None, -50.0, -10.0])
        if os.environ.get('RENDU_DUMP'):
            print('\n@@DOC_BATIMENT@@' + json.dumps(d) + '@@FIN@@')

    def test_vue3d_avec_et_sans_calcul_n2(self):
        spec = {'version': 1, 'portee': 'batiment', 'sections': [{'titre': '3D', 'blocs': [
            {'type': 'vue3d', 'calcul': 'dernier_N2', 'grandeur': 'pertes_hiver_kwh_m2', 'camera': 'iso'}]}]}
        b = self.doc(self.g, {'batiment': self.b1.pk}, spec).json()['sections'][0]['blocs'][0]
        self.assertEqual(b, {'type': 'vue3d', 'indisponible': 'Aucun calcul détaillé (N2) terminé pour ce bâtiment.'})
        from .test_rendu_fixtures import run
        n2 = run(self.b1, 'N2')
        b = self.doc(self.g, {'batiment': self.b1.pk}, spec).json()['sections'][0]['blocs'][0]
        self.assertEqual(b['calcul_id'], n2.pk)
        self.assertEqual(len(b['scene']['triangles']), 12)
        self.assertEqual(b['unite'], 'kWh/m²')
        # un calcul d'un autre bâtiment n'est pas lisible par cette voie
        spec['sections'][0]['blocs'][0]['calcul'] = run(self.b2, 'N2').pk
        b = self.doc(self.g, {'batiment': self.b1.pk}, spec).json()['sections'][0]['blocs'][0]
        self.assertIn('indisponible', b)

    def test_synthese_de_parc(self):
        r = self.doc(self.l, {'parc': self.p.pk}, modele_id=gabarit('Synthèse de parc').pk)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.verifier_formes(d)
        self.assertEqual(d['titre'], 'Synthèse de parc')
        tabs = [b for s in d['sections'] for b in s['blocs'] if b['type'] == 'tableau']
        bats = tabs[0]
        self.assertEqual(bats['total_lignes'], 3)                                 # b3 sans calcul y figure aussi
        self.assertEqual(bats['lignes'][0][0], 'Mairie')                         # tri par besoin décroissant
        pots = tabs[1]
        self.assertEqual(pots['total_lignes'], 6)                                 # 3 bâtiments × 2 bouquets
        carte = next(b for s in d['sections'] for b in s['blocs'] if b['type'] == 'carte')
        self.assertFalse(carte['figure']['vide'])
        ind = next(b for s in d['sections'] for b in s['blocs'] if b['type'] == 'indicateurs')
        self.assertEqual(ind['lignes'][0]['libelle'], 'Besoin de chauffage (somme)')
        self.assertEqual(ind['lignes'][0]['valeurs'][0], 21600.0)
        if os.environ.get('RENDU_DUMP'):
            print('\n@@DOC_PARC@@' + json.dumps(d) + '@@FIN@@')

    def test_texte_assaini_et_indisponibles(self):
        spec = {'version': 1, 'portee': 'batiment', 'titre': HOSTILE, 'sections': [{'titre': HOSTILE, 'blocs': [
            {'type': 'texte', 'contenu': '### Titre\n**gras** et *ital* <script>alert(1)</script>\n\n- un\n- <b>deux</b>'},
            {'type': 'graphe', 'graphe': {'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2'], 'series': [99999]}},
            {'type': 'saut_de_page'}]}]}
        d = self.doc(self.g, {'batiment': self.b2.pk}, spec).json()
        html = d['sections'][0]['blocs'][0]['html']
        self.assertIn('<h4>Titre</h4>', html)
        self.assertIn('<strong>gras</strong>', html)
        self.assertIn('<em>ital</em>', html)
        self.assertNotIn('<script', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<li>&lt;b&gt;deux&lt;/b&gt;</li>', html)
        g = d['sections'][0]['blocs'][1]
        self.assertTrue(g['figure']['vide'])
        self.assertEqual(d['sections'][0]['blocs'][2], {'type': 'saut_de_page'})

    def test_i5_indisponible(self):
        with mock.patch('api.rendu.sources.potentiels_batiment', return_value=None):
            spec = {'version': 1, 'portee': 'batiment', 'sections': [{'titre': 'f', 'blocs': [
                {'type': 'fiche', 'sections': ['potentiels']}]}]}
            b = self.doc(self.g, {'batiment': self.b1.pk}, spec).json()['sections'][0]['blocs'][0]
        self.assertIsNone(b['potentiels'])
        self.assertEqual(b['sections'][0]['elements'], [])

    def test_refus(self):
        spec = {'version': 1, 'portee': 'parc', 'sections': []}
        self.assertEqual(self.doc(self.o, {'parc': self.p.pk}, spec).status_code, 404)
        self.assertEqual(self.doc(self.g, {'batiment': self.b1.pk}, spec).status_code, 400)       # portée ≠ cible
        r = self.doc(self.g, {'parc': self.p.pk}, {'version': 1, 'portee': 'parc', 'sections': [
            {'titre': 't', 'blocs': [{'type': 'fiche'}]}]})
        self.assertEqual(r.json(), {'detail': 'La fiche d’identité appartient à un rendu de bâtiment.'})
        r = self.doc(self.g, {'parc': self.p.pk}, {'version': 2, 'portee': 'parc', 'sections': []})
        self.assertEqual(r.json(), {'detail': 'Version de spécification inconnue.'})
        self.assertEqual(self.doc(self.g, {'parc': self.p.pk}, modele_id=987654).status_code, 404)
        r = self.g.post('/api/rendu/document/', {'cible': {'parc': self.p.pk}}, format='json')
        self.assertEqual(r.status_code, 400)
        # modèle d'un autre parc : invisible
        from ..models import Portfolio
        autre = Portfolio.objects.create(name='autre', owner_sub='x')
        m = RapportModele.objects.create(portfolio=autre, nom='m', portee='parc', spec=spec)
        self.assertEqual(self.doc(self.g, {'parc': self.p.pk}, modele_id=m.pk).status_code, 404)


class IdentiteEtSourcesReellesTest(RenduMixin, TestCase):
    def test_identite_garde_sa_forme(self):
        r = self.l.get(f'/api/batiments/{self.b1.pk}/identite/')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for k in ('batiment', 'identite', 'enveloppe', 'systemes', 'meteo', 'environnement', 'resultats', 'ameliorations',
                  'geometrie', 'plancher', 'confiance', 'completude', 'approfondissement', 'sources'):
            self.assertIn(k, d)
        self.assertNotIn('potentiels', d)                   # la fiche du lot 34 garde sa forme ; potentiels : bloc « fiche »
        self.assertEqual(self.o.get(f'/api/batiments/{self.b1.pk}/identite/').status_code, 404)

    def test_routes_reelles_par_leur_classe_de_vue(self):
        """Sans simulation : les routes des autres secteurs sont appelées par leur vue, au nom de l'appelant."""
        for p in self.sources:
            p.stop()
        from types import SimpleNamespace
        from ..rendu import sources
        req = SimpleNamespace(user=SimpleNamespace(sub='g', is_authenticated=True, username='g'), auth=None, META={})
        i5 = sources.potentiels_batiment(req, self.b1.pk)
        if i5 is not None:                                   # A a livré : forme I5.b
            self.assertIn('bouquets', i5)
            self.assertIn('reel', i5)
        outsider = SimpleNamespace(user=SimpleNamespace(sub='o', is_authenticated=True, username='o'), auth=None, META={})
        self.assertIsNone(sources.potentiels_batiment(outsider, self.b1.pk))          # 404 → None
        i3 = sources.indicateurs(req)
        self.assertTrue(i3 is None or isinstance(i3, list))
        i7 = sources.actions(req, self.p.pk)
        self.assertTrue(i7 is None or 'familles' in i7)
        self.assertIsNone(sources.triangles(req, 999999))
        for p in self.sources:
            p.start()


class RapportsParcTest(RenduMixin, TestCase):
    SPEC = {'version': 1, 'portee': 'parc', 'titre': 'Mon rendu', 'sections': [{'titre': 's', 'blocs': [
        {'type': 'graphe', 'graphe': {'forme': 'barres', 'x': 'usage', 'y': ['heating_kwh_m2']}}]}]}

    def test_crud_et_droits(self):
        url = f'/api/parcs/{self.p.pk}/rapports/'
        self.assertEqual(self.l.get(url).json(), {'peut_ecrire': False, 'modeles': []})
        self.assertEqual(self.o.get(url).status_code, 404)
        self.assertEqual(self.l.post(url, {'nom': 'x', 'spec': self.SPEC}, format='json').status_code, 403)
        r = self.g.post(url, {'nom': 'Rendu ' + HOSTILE, 'spec': self.SPEC, 'par_defaut': True}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        m = r.json()
        self.assertEqual(set(m), {'id', 'nom', 'portee', 'spec', 'par_defaut', 'portfolio', 'modifiable'})
        self.assertEqual((m['portee'], m['portfolio'], m['modifiable']), ('parc', self.p.pk, True))
        self.assertEqual(m['spec']['sections'][0]['blocs'][0]['graphe']['agregation'], 'moyenne')    # normalisée
        r2 = self.g.post(url, {'nom': 'second', 'spec': self.SPEC, 'par_defaut': True}, format='json').json()
        self.assertFalse(RapportModele.objects.get(pk=m['id']).par_defaut)                          # un seul défaut
        self.assertEqual(self.g.get(url).json()['peut_ecrire'], True)
        d = f'/api/rapports/{m["id"]}/'
        self.assertEqual(self.l.get(d).json()['modifiable'], False)
        self.assertEqual(self.o.get(d).status_code, 404)
        self.assertEqual(self.l.patch(d, {'nom': 'y'}, format='json').status_code, 403)
        self.assertEqual(self.g.patch(d, {'nom': 'renommé'}, format='json').json()['nom'], 'renommé')
        self.assertEqual(self.g.patch(d, {'spec': {'version': 1}}, format='json').status_code, 400)
        self.assertEqual(self.g.post(url, {'nom': '', 'spec': self.SPEC}, format='json').status_code, 400)
        # le modèle sert au document
        r = self.g.post('/api/rendu/document/', {'modele_id': r2['id'], 'cible': {'parc': self.p.pk}}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.l.delete(d).status_code, 403)
        self.assertEqual(self.g.delete(d).status_code, 204)
        lab = gabarits.assurer()[0]
        r = self.g.patch(f'/api/rapports/{lab.pk}/', {'nom': 'x'}, format='json')
        self.assertEqual((r.status_code, r.json()), (403, {'detail': 'Gabarit du lab : copiez-le dans un parc pour le modifier.'}))
        self.assertEqual(self.o.get(f'/api/rapports/{lab.pk}/').status_code, 200)       # gabarit du lab : lisible
