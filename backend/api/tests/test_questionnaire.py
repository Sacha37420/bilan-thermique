"""Lot 12 (refonte) — questionnaire guidé, incohérences, paroi mitoyenne (N2)."""

from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import building_solver, facts, pipeline, questionnaire
from ..models import Building
from ..testing import client_for, patch_jwks, synthetic_year
from .test_moteurs import LAYERS
from ..testing import box_with_window

WEATHER = {'series': synthetic_year(), 'source': 'synthétique'}


def seeded_building():
    b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
    vals, _ = pipeline.archetypes.defaults(1962, 'maison')
    for k, v in vals.items():
        facts.record(b, k, v, 'defaut', source='archétype')
    for k, v in (('identite.surface_m2', 110.0), ('identite.usage', 'maison'), ('identite.annee_construction', 1962)):
        facts.record(b, k, v, 'infere', source='BDNB')
    return b


class AnswersTest(SimpleTestCase):
    def test_wall_insulation_thickness_gives_u(self):
        rows = {k: v for k, v, _ in questionnaire.answers_to_facts(
            {'murs': 'ite', 'murs_epaisseur': 12}, {'identite.annee_construction': 1962})}
        self.assertEqual(rows['mur.isolation'], 'ite')
        self.assertAlmostEqual(rows['mur.u'], 1 / (1 / 1.6 + 0.12 / 0.035), places=3)
        iti = {k: v for k, v, _ in questionnaire.answers_to_facts({'murs': 'iti', 'murs_epaisseur': 12},
                                                                   {'identite.annee_construction': 1962})}
        self.assertGreater(iti['mur.u'], rows['mur.u'])  # pont thermique de l'ITI

    def test_windows_ventilation_and_airtightness(self):
        rows = {k: v for k, v, _ in questionnaire.answers_to_facts(
            {'fenetres': 'double_recent', 'ventilation': 'vmc_df', 'etancheite': 'beaucoup'}, {})}
        self.assertEqual((rows['baies.uw'], rows['baies.vitrage']), (1.5, 'double_fe'))
        self.assertEqual(rows['ventilation.rendement_recup'], 0.8)
        self.assertEqual(rows['ventilation.n50'], 12.0)

    def test_every_answer_is_a_valid_fact(self):
        answers = {'usage': 'bureau', 'annee': 1990, 'surface': 300, 'niveaux': 2, 'mitoyennete': '0.25',
                   'murs': 'ite', 'murs_epaisseur': 14, 'toiture': 30, 'fenetres': 'triple', 'vitrage': 25,
                   'ventilation': 'vmc_sf_hygro', 'etancheite': 'mesure', 'n50': 1.2, 'consigne': 20,
                   'chauffage': 'pac_air_eau'}
        for k, v, _ in questionnaire.answers_to_facts(answers, {}):
            facts.validate_value(k, v)

    def test_bad_answers_refused_with_fixed_message(self):
        for bad in ({'murs': 'paille'}, {'consigne': 40}, {'inconnue': 1}, {'surface': '<b>'}):
            with self.assertRaises(questionnaire.QuestionError):
                questionnaire.answers_to_facts(bad, {})

    def test_inconsistencies(self):
        msgs = questionnaire.inconsistencies({'baies.vitrage': 'triple', 'baies.uw': 3.0, 'mur.isolation': 'aucune',
                                              'mur.u': 0.3, 'identite.annee_construction': 1940})
        self.assertGreaterEqual(len(msgs), 2)
        self.assertTrue(any('Uw élevé' in m['message'] for m in msgs))


class RankingTest(TestCase):
    def test_uncertain_influential_questions_first(self):
        b = seeded_building()
        r = questionnaire.ranked_questions(b, WEATHER['series'][:24 * 90])
        top = [q['id'] for q in r['questions'] if q['priority']]
        self.assertTrue(set(top) & {'murs', 'ventilation', 'etancheite', 'toiture', 'consigne', 'fenetres'})
        self.assertNotIn('chauffage', top)  # n'influe pas sur le besoin
        self.assertGreater(r['questions'][0]['influence_kwh'], 0)

    def test_answered_questions_move_down(self):
        b = seeded_building()
        facts.record(b, 'mur.u', 0.4, 'declare', author_sub='u')
        facts.record(b, 'mur.isolation', 'ite', 'declare', author_sub='u')
        r = questionnaire.ranked_questions(b, WEATHER['series'][:24 * 60])
        murs = next(q for q in r['questions'] if q['id'] == 'murs')
        self.assertTrue(murs['answered'])
        self.assertFalse(murs['priority'])


@patch_jwks()
class QuestionnaireApiTest(TestCase):
    def test_answer_moves_result_and_confidence(self):
        """Recette du lot : une réponse fait bouger le résultat et la confiance."""
        b = seeded_building()
        c = client_for(sub='occ')
        with mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            c.post(f'/api/batiments/{b.pk}/calculer/', {}, format='json')
            r = c.post(f'/api/batiments/{b.pk}/questionnaire/',
                       {'answers': {'murs': 'ite', 'murs_epaisseur': 14, 'toiture': 30}}, format='json')
            q = c.get(f'/api/batiments/{b.pk}/questions/')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertLess(d['after']['indicators']['heating_kwh'], d['before']['indicators']['heating_kwh'])
        self.assertGreater(d['confidence_after'], d['confidence_before'])
        self.assertEqual(d['fiche']['facts']['mur.u']['method'], 'declare')
        self.assertEqual(q.status_code, 200)

    def test_year_answer_refreshes_archetype_defaults_only(self):
        b = seeded_building()
        facts.record(b, 'toiture.u', 0.2, 'declare', author_sub='u')
        u_before = facts.effective_values(b)['mur.u']
        with mock.patch('api.calc_service.tmy_weather', return_value=WEATHER):
            client_for(sub='u').post(f'/api/batiments/{b.pk}/questionnaire/', {'answers': {'annee': 2010}}, format='json')
        eff = facts.effective(b)
        self.assertLess(eff['mur.u'].value, u_before)       # défaut recalculé (2006-2012)
        self.assertEqual(eff['mur.u'].method, 'defaut')
        self.assertEqual(eff['toiture.u'].value, 0.2)       # la surcharge reste

    def test_reader_cannot_answer(self):
        from ..models import Portfolio, PortfolioMember
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        b = Building.objects.create(name='x', portfolio=p)
        r = client_for(sub='l').post(f'/api/batiments/{b.pk}/questionnaire/', {'answers': {'consigne': 19}}, format='json')
        self.assertEqual(r.status_code, 403)


class PartyWallN2Test(SimpleTestCase):
    """E9 : une façade mitoyenne (adiabatique) réduit le besoin du N2, sans ensoleillement."""

    def test_adiabatic_walls_reduce_need(self):
        env = box_with_window(w=10, d=8, h=3, win_ratio_south=0.2, wall_id=1, roof_id=2, floor_id=3, glass_id=4)
        weather = synthetic_year(days=20)
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 19.0, 'shadow_mode': 'precomputed', 't_ground': 12.0,
                   'weather': weather, 'interior': {'mode': 'thermostat', 'h_i': 7.7, 'c_air_int': 1e6, 't_min': 19.0,
                                                    't_max': 60.0, 'debit_vent_m3h': 100.0}}
        free = building_solver.run_building_simulation(env, LAYERS, None, payload)['heating_kwh']
        party = {**env, 'triangles': [dict(t, boundary='adiabatic') if t['group'] in ('mur_est', 'mur_ouest') else t
                                      for t in env['triangles']]}
        attached = building_solver.run_building_simulation(party, LAYERS, None, payload)['heating_kwh']
        self.assertLess(attached, free * 0.95)

    def test_serializer_accepts_adiabatic(self):
        from ..serializers import TriangleInputSerializer
        s = TriangleInputSerializer(data={'v': [0, 1, 2], 'boundary': 'adiabatic'})
        self.assertTrue(s.is_valid(), s.errors)
