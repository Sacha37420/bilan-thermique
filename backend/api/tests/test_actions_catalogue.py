"""Chantier bt-potentiels, lot 37 (secteur A) — catalogue des actions (I7.a) et conditions d'application."""

from django.test import TestCase

from .. import facts, scenarios
from ..ameliorations import bouquets as bq_service, catalogue
from ..models import Bouquet, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, without_auto_environment
from .test_calage import seeded as _seeded


def seeded(**over):
    import uuid
    b = _seeded(**over)
    b.name = uuid.uuid4().hex[:12]
    b.save(update_fields=['name'])
    return b

_env = without_auto_environment()


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()


class CatalogueShapeTest(TestCase):
    def test_families_and_every_action_complete(self):
        cat = catalogue.catalogue_json()
        fams = [f['cle'] for f in cat['familles']]
        self.assertEqual(fams, ['isolation', 'ventilation', 'protections', 'vegetalisation', 'toiture_albedo', 'systemes'])
        for a in cat['actions']:
            self.assertIn(a['famille'], fams, a['cle'])
            for k in ('cle', 'libelle', 'description', 'indicateurs_pertinents', 'parametres', 'conditions', 'effets',
                      'cibles', 'application', 'vigilance', 'desactivee'):
                self.assertIn(k, a, (a['cle'], k))
            self.assertTrue(set(a['indicateurs_pertinents']) <= {'energie', 'confort'})
            self.assertEqual(set(a['application']), {'N0', 'N1', 'N2'})
            self.assertTrue(a['cibles'])
            for p in a['parametres']:
                self.assertEqual(set(p), {'cle', 'libelle', 'unite', 'type', 'min', 'max', 'pas', 'defaut', 'choix',
                                          'groupe', 'aide', 'niveaux'})
            for c in a['conditions']:
                self.assertEqual(set(c), {'code', 'libelle', 'bloquante'})
        # Chaque famille a au moins une action ; les anciens bouquets sont des actions, à l'identique.
        self.assertEqual({a['famille'] for a in cat['actions']}, set(fams))
        keys = {a['cle'] for a in cat['actions']}
        self.assertTrue(set(scenarios.BUNDLES) <= keys)
        self.assertEqual(next(a for a in cat['actions'] if a['cle'] == 'pac')['famille'], 'systemes')

    def test_parc_defaults_and_disabled(self):
        p = Portfolio.objects.create(name='P', owner_sub='g',
                                     parametrage={'bouquets.ite.epaisseur_cm': 20, 'bouquets.pac.actif': False})
        PortfolioMember.objects.create(portfolio=p, sub='g', role='gestionnaire_parc')
        with patch_jwks():
            r = client_for(sub='g').get(f'/api/ameliorations/actions/?parc={p.pk}')
            self.assertEqual(r.status_code, 200)
            acts = {a['cle']: a for a in r.json()['actions']}
            self.assertEqual(acts['ite']['parametres'][0]['defaut'], 20)
            self.assertIn('20 cm', acts['ite']['libelle'])
            self.assertTrue(acts['pac']['desactivee'])
            self.assertFalse(acts['ite']['desactivee'])
            self.assertEqual(client_for(sub='x').get(f'/api/ameliorations/actions/?parc={p.pk}').status_code, 404)
            self.assertEqual(client_for(sub='x').get('/api/ameliorations/actions/').status_code, 200)


class ConditionsTest(TestCase):
    def _report(self, b, elements):
        bq = Bouquet(nom='t', elements=bq_service.validate_elements(elements))
        return bq_service.plan_bouquet(b, bq)[1]

    def test_blocking_condition_discards_with_reason(self):
        b = seeded(**{'plancher.type': ('terre_plein', 'declare'), 'plancher.u': (0.8, 'defaut'),
                      'systeme.generateur': ('pac_air_eau', 'declare')})
        rep = self._report(b, [{'action': 'plancher_bas'}, {'action': 'pac'}, {'action': 'combles'}])
        by = {r['cle']: r for r in rep}
        self.assertFalse(by['plancher_bas']['appliquee'])
        self.assertIn('terre-plein', by['plancher_bas']['raison'])
        self.assertFalse(by['pac']['appliquee'])
        self.assertEqual(by['pac']['raison'], 'Déjà chauffé par une pompe à chaleur.')
        self.assertTrue(by['combles']['appliquee'])
        self.assertIsNone(by['combles']['raison'])
        self.assertTrue(any('U de la toiture' in e and '→' in e for e in by['combles']['effets']))

    def test_une_parmi_takes_first_applicable(self):
        b = seeded(**{'mur.isolation': ('ite', 'declare')})
        rep = self._report(b, [{'une_parmi': [{'action': 'ite'}, {'action': 'iti'}]}])
        self.assertEqual([(r['cle'], r['appliquee']) for r in rep], [('ite', False), ('iti', True)])
        self.assertEqual(rep[0]['raison'], 'Murs déjà isolés par l’extérieur.')
        b2 = seeded()
        rep2 = self._report(b2, [{'une_parmi': [{'action': 'ite'}, {'action': 'iti'}]}])
        self.assertEqual([(r['cle'], r['appliquee']) for r in rep2], [('ite', True), ('iti', False)])
        self.assertIn('retenue', rep2[1]['raison'])

    def test_non_blocking_gives_warning(self):
        b = seeded(**{'mur.materiau': ('pierre', 'declare')})
        rep = self._report(b, [{'action': 'iti'}])
        self.assertTrue(rep[0]['appliquee'])
        self.assertTrue(any('perspirants' in w for w in rep[0]['avertissements']))
        # Information manquante : jamais bloquante, signalée.
        rep = self._report(seeded(), [{'action': 'toiture_vegetalisee'}])
        self.assertTrue(rep[0]['appliquee'])
        self.assertTrue(rep[0]['avertissements'])

    def test_windows_condition_on_protections(self):
        b = seeded(**{f'baies.ratio_{o}': (0.0, 'declare') for o in 'NESO'})
        rep = self._report(b, [{'action': 'protection_mobile', 'parametres': {'orientation': 'S'}}])
        self.assertFalse(rep[0]['appliquee'])
        self.assertEqual(rep[0]['raison'], 'Pas de fenêtres sur les façades visées.')

    def test_old_bundle_identical_to_apply_bundles(self):
        """Les anciens bouquets donnent exactement les mêmes faits qu'avant (scenarios.apply_bundles)."""
        from ..models import Scenario
        for key in ('ite', 'iti', 'combles', 'fenetres', 'triple', 'vmc_hygro', 'vmc_df', 'pac'):
            b = seeded()
            old = Scenario.objects.create(building=b, name='old')
            scenarios.apply_bundles(b, old, [key])
            bq = Bouquet.objects.create(nom=f'b-{key}', elements=[{'action': key, 'parametres': {}}])
            new, rep = bq_service.apply_to_building(b, bq)
            self.assertTrue(rep[0]['appliquee'], (key, rep))
            vo = {k: v for k, v in facts.effective_values(b, old).items()}
            vn = {k: v for k, v in facts.effective_values(b, new).items()}
            self.assertEqual(vo, vn, key)
            self.assertEqual(new.weather['bundles'], [key])
        b = seeded()
        bq = Bouquet.objects.create(nom='s', elements=[{'action': 'store_sud'}, {'action': 'casquette_sud'}])
        s, _ = bq_service.apply_to_building(b, bq)
        self.assertEqual(sorted(d.kind for d in s.shading_devices.all()), ['casquette', 'store_ext'])

    def test_surcharges_written_in_contract_form(self):
        b = seeded()
        b.pipeline = {'footprint_xy': [[-5, -4], [5, -4], [5, 4], [-5, 4]]}
        b.save()
        bq = Bouquet.objects.create(nom='v', elements=bq_service.validate_elements([
            {'action': 'arbres', 'parametres': {'orientation': 'S', 'nombre': 2, 'distance_m': 6}},
            {'action': 'toiture_vegetalisee'}, {'action': 'murs_vegetalises', 'parametres': {'orientation': 'SO'}},
            {'action': 'rafraichissement_air'}, {'action': 'surventilation_nocturne'}, {'action': 'abords_clairs'}]))
        s, rep = bq_service.apply_to_building(b, bq)
        self.assertTrue(all(r['appliquee'] for r in rep), rep)
        sc = s.surcharges
        self.assertEqual(set(sc), {'arbres', 'toiture_vegetalisee', 'murs_vegetalises', 'rafraichissement_air'})
        self.assertEqual(len(sc['arbres']), 2)
        for t in sc['arbres']:
            self.assertEqual(set(t), {'x_m', 'y_m', 'hauteur_m', 'rayon_houppier_m', 'tronc_m', 'persistant',
                                      'transmission_ete', 'transmission_hiver'})
            self.assertAlmostEqual(t['y_m'], -10.0)   # 6 m devant la façade sud (y = -4)
        self.assertEqual(sorted(t['x_m'] for t in sc['arbres']), [-2.5, 2.5])
        self.assertEqual(set(sc['toiture_vegetalisee']), {'substrat_m', 'lai', 'part'})
        self.assertEqual(sc['murs_vegetalises']['orientations'], ['S', 'O'])
        self.assertEqual(set(sc['rafraichissement_air']), {'delta_c', 'mois', 'heures', 'hypothese'})
        self.assertIn('Hypothèse', sc['rafraichissement_air']['hypothese'])
        v = facts.effective_values(b, s)
        self.assertEqual(v['ventilation.nocturne_vol_h'], 3.0)
        self.assertEqual(v['abords.albedo'], 0.35)
        self.assertIs(v['toiture.vegetalisee'], True)
        src = s.facts.first().source
        self.assertEqual(src, f'amélioration « {s.name} »')
        self.assertEqual(s.facts.first().method, 'declare')
        # La base n'est jamais touchée.
        self.assertNotIn('ventilation.nocturne_vol_h', facts.effective_values(b))
