"""Lot 8 (refonte) — provenance par valeur, fiche effective, surcharges réversibles."""

from django.test import TestCase

from .. import facts
from ..models import Building, BuildingFact, Scenario
from ..testing import box_envelope, client_for, patch_jwks


class FactLogicTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', envelope=box_envelope())

    def test_higher_method_wins_without_erasing(self):
        facts.record(self.b, 'mur.u', 1.8, 'infere', source='archétype 1948-1974')
        facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u1')
        eff = facts.effective(self.b)
        self.assertEqual((eff['mur.u'].value, eff['mur.u'].method), (0.4, 'declare'))
        self.assertEqual(BuildingFact.objects.filter(key='mur.u').count(), 2)

    def test_retired_override_restores_automatic_value(self):
        """Recette du lot : une surcharge retirée rend la valeur automatique."""
        facts.record(self.b, 'mur.u', 1.8, 'infere')
        user = facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u1')
        facts.retire(user, 'u1')
        eff = facts.effective(self.b)
        self.assertEqual((eff['mur.u'].value, eff['mur.u'].method), (1.8, 'infere'))
        user.refresh_from_db()
        self.assertIsNotNone(user.retired_at)  # gardée dans l'historique

    def test_reimport_never_overwrites_override(self):
        facts.record(self.b, 'mur.u', 1.8, 'infere', source='BDNB 2024')
        facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u1')
        facts.record(self.b, 'mur.u', 1.6, 'infere', source='BDNB 2025')  # nouveau millésime
        eff = facts.effective(self.b)
        self.assertEqual(eff['mur.u'].value, 0.4)
        olds = BuildingFact.objects.filter(key='mur.u', method='infere').order_by('created_at')
        self.assertEqual(olds[0].superseded_by_id, olds[1].pk)  # l'automatique est remplacé, pas l'autre

    def test_same_rank_latest_wins_and_document_beats_declared(self):
        facts.record(self.b, 'baies.uw', 2.8, 'declare', author_sub='a')
        facts.record(self.b, 'baies.uw', 2.5, 'declare', author_sub='b')
        self.assertEqual(facts.effective(self.b)['baies.uw'].value, 2.5)
        facts.record(self.b, 'baies.uw', 1.4, 'document', author_sub='a')
        self.assertEqual(facts.effective(self.b)['baies.uw'].value, 1.4)
        facts.record(self.b, 'baies.uw', 1.3, 'mesure', author_sub='a')
        self.assertEqual(facts.effective(self.b)['baies.uw'].method, 'mesure')

    def test_override_beats_imported_document(self):
        facts.record(self.b, 'mur.u', 0.35, 'document', source='DPE (BDNB)')  # automatique
        facts.record(self.b, 'mur.u', 0.2, 'declare', author_sub='u1')
        self.assertEqual(facts.effective(self.b)['mur.u'].value, 0.2)

    def test_automatic_value_cannot_be_retired(self):
        f = facts.record(self.b, 'mur.u', 1.8, 'infere')
        with self.assertRaises(facts.FactError):
            facts.retire(f)

    def test_validation_ranges_and_types(self):
        for key, bad in (('mur.u', 99), ('mur.u', 'abc'), ('identite.usage', 'chateau'),
                         ('identite.annee_construction', 1990.5), ('baies.occultations', 'oui'),
                         ('inconnue.cle', 1)):
            with self.assertRaises(facts.FactError, msg=f'{key}={bad!r}'):
                facts.validate_value(key, bad)
        self.assertEqual(facts.validate_value('identite.annee_construction', '1975'), 1975)

    def test_scores_rise_with_corrections(self):
        facts.record(self.b, 'mur.u', 1.8, 'defaut')
        c0, k0 = facts.refresh_scores(self.b)
        facts.record(self.b, 'mur.u', 1.8, 'infere')
        c1, k1 = facts.refresh_scores(self.b)
        facts.record(self.b, 'mur.u', 0.5, 'mesure', author_sub='u')
        c2, k2 = facts.refresh_scores(self.b)
        self.assertLess(c0, c1)
        self.assertLess(c1, c2)
        self.assertEqual(k0, 0.0)
        self.assertGreater(k1, 0.0)
        self.b.refresh_from_db()
        self.assertEqual(self.b.confidence, c2)

    def test_scenario_override_does_not_touch_base(self):
        facts.record(self.b, 'mur.u', 1.8, 'infere')
        sc = Scenario.objects.create(building=self.b, name='ITE')
        facts.record(self.b, 'mur.u', 0.25, 'declare', author_sub='u', scenario=sc)
        self.assertEqual(facts.effective(self.b)['mur.u'].value, 1.8)
        self.assertEqual(facts.effective(self.b, sc)['mur.u'].value, 0.25)
        self.assertNotEqual(facts.fiche_hash(self.b), facts.fiche_hash(self.b, sc))


@patch_jwks()
class FicheApiTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', envelope=box_envelope())
        facts.record(self.b, 'mur.u', 1.8, 'infere', source='archétype')

    def test_override_then_back_to_automatic(self):
        c = client_for(sub='u1')
        r = c.post(f'/api/batiments/{self.b.pk}/faits/', {'key': 'mur.u', 'value': 0.35}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        fiche = r.json()['facts']['mur.u']
        self.assertEqual((fiche['value'], fiche['method'], fiche['author'], fiche['mine']), (0.35, 'declare', 'vous', True))
        self.assertEqual(fiche['automatic']['value'], 1.8)
        fid = r.json()['fact']['id']
        r = c.delete(f'/api/faits/{fid}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['facts']['mur.u']['method'], 'infere')

    def test_someone_else_cannot_retire_my_override(self):
        r = client_for(sub='u1').post(f'/api/batiments/{self.b.pk}/faits/', {'key': 'mur.u', 'value': 0.35}, format='json')
        fid = r.json()['fact']['id']
        self.assertEqual(client_for(sub='u2').delete(f'/api/faits/{fid}/').status_code, 403)

    def test_system_methods_refused_from_api(self):
        r = client_for().post(f'/api/batiments/{self.b.pk}/faits/', {'key': 'mur.u', 'value': 1, 'method': 'infere'},
                              format='json')
        self.assertEqual(r.status_code, 400)

    def test_invalid_value_message_is_fixed(self):
        r = client_for().post(f'/api/batiments/{self.b.pk}/faits/',
                              {'key': 'mur.u', 'value': '<script>x</script>'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('<script>', r.json()['detail'])

    def test_history_and_catalogue(self):
        c = client_for(sub='u1')
        c.post(f'/api/batiments/{self.b.pk}/faits/', {'key': 'mur.u', 'value': 0.35}, format='json')
        hist = c.get(f'/api/batiments/{self.b.pk}/faits/?cle=mur.u').json()['results']
        self.assertEqual(len(hist), 2)
        cat = c.get('/api/fiche/catalogue/').json()
        self.assertIn('mur.u', [k['key'] for k in cat['keys']])

    def test_other_author_name_shown_not_sub(self):
        client_for(sub='u1', preferred_username='alice').post(
            f'/api/batiments/{self.b.pk}/faits/', {'key': 'mur.u', 'value': 0.35}, format='json')
        fiche = client_for(sub='u2').get(f'/api/batiments/{self.b.pk}/fiche/').json()['facts']['mur.u']
        self.assertEqual(fiche['author'], 'alice')
        self.assertFalse(fiche['mine'])
        self.assertNotIn('u1', str(fiche))
