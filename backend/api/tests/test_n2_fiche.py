"""Lot 23 (refonte) — N2 calculé depuis la fiche : parois au U de la fiche, enveloppe, baies, mitoyenneté,
lancement, « à jour / périmé », entrées figées, droits, comparaison à N1."""

from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import calc_service, facts, geometry_service
from ..engines import inputs, n1, n2_inputs
from ..models import Building, BuildingAccess, CalcRun, Job, ParoiModel, Portfolio, PortfolioMember
from ..testing import box_envelope, client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


FILMS = 1.0 / n2_inputs.H_I + 1.0 / n2_inputs.H_E
WEATHER = synthetic_year()
SQUARE = [[0, 0], [10, 0], [10, 8], [0, 8]]


def fiche(**over):
    vals = {'identite.surface_m2': 80.0, 'identite.niveaux': 1, 'identite.hauteur_m': 3.0, 'identite.emprise_m2': 80.0,
            'identite.usage': 'maison', 'mur.u': 0.5, 'toiture.u': 0.3, 'plancher.u': 0.4, 'baies.uw': 1.8,
            'baies.g': 0.6, 'ventilation.type': 'vmc_sf_auto', 'ventilation.debit_vol_h': 0.5, 'ventilation.n50': 4.0,
            'usage.consigne_chauffage_c': 19.0, 'usage.apports_w_m2': 4.0, 'toiture.type': 'terrasse',
            'plancher.type': 'terre_plein', **{f'baies.ratio_{o}': 0.15 for o in 'NESO'}}
    vals.update(over)
    return vals


def make_building(vals, footprint=SQUARE, neighbours=None, name='b'):
    b = Building.objects.create(name=name, georef_lat=47.39, georef_lon=0.69,
                                pipeline={'footprint_xy': footprint, 'neighbours': neighbours or []})
    for k, v in vals.items():
        facts.record(b, k, v, 'infere', refresh=False)
    facts.refresh_scores(b)
    return b


def tmy():
    return mock.patch('api.calc_service.tmy_weather', return_value={'series': WEATHER, 'source': 'test'})


class N2LayersTest(SimpleTestCase):
    def test_opaque_u_equals_fiche(self):
        for material, struct in n2_inputs.MASONRY.items():
            for u in (0.15, 0.5, 1.9, 3.0):
                for ins in ('ite', 'iti'):
                    with self.subTest(material=material, u=u, ins=ins):
                        ls = n2_inputs.opaque_layers(u, struct, insulation=ins, alpha=0.7, r_films=FILMS)
                        self.assertAlmostEqual(inputs.u_from_layers(ls, n2_inputs.H_I, n2_inputs.H_E), u, delta=0.01 * u)
                        self.assertEqual(ls[0]['alpha'], 0.7)

    def test_insulation_position(self):
        ite = n2_inputs.opaque_layers(0.3, n2_inputs.MASONRY['beton'], insulation='ite', r_films=FILMS)
        iti = n2_inputs.opaque_layers(0.3, n2_inputs.MASONRY['beton'], insulation='iti', r_films=FILMS)
        self.assertEqual(ite[0]['lam'], 0.04)   # isolant dehors
        self.assertEqual(iti[1]['lam'], 0.04)   # isolant derrière la structure

    def test_glazing_u_and_g(self):
        for uw, vit in ((5.7, 'simple'), (2.8, 'double'), (1.3, 'double_fe'), (0.8, 'triple')):
            with self.subTest(uw=uw):
                ls = n2_inputs.glazing_layers(uw, 0.55, vit)
                self.assertAlmostEqual(inputs.u_from_layers(ls, n2_inputs.H_I, n2_inputs.H_E), uw, delta=0.02 * uw)
                tau = 1.0
                for l in ls:
                    tau *= l['tau']
                self.assertAlmostEqual(tau, 0.55, delta=0.01)

    def test_floor_u_includes_ground_resistance(self):
        _layers, _f, desc = n2_inputs.paroi_layers_from_fiche(fiche(**{'plancher.u': 0.35}))
        self.assertAlmostEqual(desc['plancher']['u'], 0.35, delta=0.005)
        self.assertEqual({desc[k]['u'] for k in ('mur', 'toiture')}, {0.5, 0.3})


class N2EnvelopeTest(TestCase):
    def test_closed_envelope_with_glazing_ratio_per_orientation(self):
        vals = fiche(**{'baies.ratio_S': 0.30, 'baies.ratio_N': 0.08, 'identite.niveaux': 2, 'identite.hauteur_m': 5.6,
                        'toiture.type': '2pans', 'toiture.pente_deg': 35})
        b = make_building(vals)
        env, summ = n2_inputs.envelope_from_fiche(b, None, vals)
        geometry_service.check_closed(env['vertices'], env['triangles'])
        for o, r in (('S', 0.30), ('N', 0.08), ('E', 0.15)):
            self.assertAlmostEqual(summ['baies'][o]['obtenu'], r, delta=0.02, msg=o)
        self.assertEqual(summ['source'], 'emprise automatique (BD TOPO)')
        ids = {t['paroi_model_id'] for t in env['triangles']}
        self.assertEqual(ids, set(n2_inputs.IDS.values()))
        self.assertTrue(any(t.get('boundary') == 'ground' for t in env['triangles']))

    def test_declared_party_wall_is_adiabatic_and_unglazed(self):
        vals = fiche(**{'identite.mitoyennete': 0.25, 'baies.ratio_N': 0.0, 'baies.ratio_S': 0.3})
        b = make_building(vals)
        env, summ = n2_inputs.envelope_from_fiche(b, None, vals)
        adiabatic = [t for t in env['triangles'] if t.get('boundary') == 'adiabatic']
        self.assertTrue(adiabatic)
        self.assertTrue(all(t['paroi_model_id'] == n2_inputs.IDS['mur'] for t in adiabatic))
        self.assertAlmostEqual(summ['mitoyennete']['part'], 0.25, delta=0.06)
        self.assertGreater(summ['baies']['S']['obtenu'], 0.25)   # le sud, vitré, n'est pas choisi

    def test_neighbour_makes_touching_wall_adiabatic(self):
        vals = fiche()
        b = make_building(vals, neighbours=[{'xy': [[10.2, 0], [20, 0], [20, 8], [10.2, 8]], 'h': 6}])
        env, summ = n2_inputs.envelope_from_fiche(b, None, vals)
        self.assertEqual(summ['mitoyennete']['source'], 'voisins relevés')
        east = [t for t in env['triangles'] if t.get('boundary') == 'adiabatic']
        self.assertTrue(east and all(abs(t['normal'][0] - 1.0) < 1e-6 for t in east))
        self.assertEqual(summ['baies']['E']['surface_m2'], 0.0)

    def test_square_when_no_footprint_known(self):
        vals = fiche(**{'identite.emprise_m2': 64.0})
        b = Building.objects.create(name='nu')
        for k, v in vals.items():
            facts.record(b, k, v, 'declare', author_sub='x', refresh=False)
        _env, summ = n2_inputs.envelope_from_fiche(b, None, vals)
        self.assertAlmostEqual(summ['emprise_m2'], 64.0, delta=0.5)
        self.assertIn('carré', summ['source'])

    def test_heated_floor_below_is_adiabatic(self):
        vals = fiche(**{'plancher.type': 'local_chauffe'})
        env, _ = n2_inputs.envelope_from_fiche(make_building(vals), None, vals)
        sol = [t for t in env['triangles'] if t.get('group') == 'sol']
        self.assertTrue(sol and all(t['boundary'] == 'adiabatic' for t in sol))

    def test_expert_envelope_kept_with_catalogue_parois(self):
        p = ParoiModel.objects.create(name='Mur test', layers=[{'e': 0.2, 'lam': 1.0, 'rho': 2000, 'c': 1000,
                                                                 'tau': 0, 'r': 0.4, 'alpha': 0.6}])
        b = make_building(fiche())
        b.envelope = box_envelope(paroi_id=p.pk)
        b.save()
        built = n2_inputs.build(b)
        self.assertEqual(built['snapshot']['mode'], 'experte')
        self.assertEqual(set(built['layers']), {p.pk})
        self.assertIs(built['envelope'], b.envelope)
        # Une enveloppe partiellement assignée ne suffit pas : on repart de la fiche.
        b.envelope['triangles'][0]['paroi_model_id'] = None
        self.assertEqual(n2_inputs.build(b)['snapshot']['mode'], 'fiche')


class N2RunTest(TestCase):
    def _start(self, b, **kw):
        with tmy():
            return calc_service.start_n2(b, owner_sub='g', **kw)

    def test_run_done_with_indicators_inputs_and_freshness(self):
        b = make_building(fiche())
        run, fresh = self._start(b)
        self.assertTrue(fresh)
        self.assertEqual(run.status, Job.PENDING)
        self.assertEqual(run.job.kind, 'n2_fiche')
        self.assertEqual(run.inputs['mode'], 'fiche')
        self.assertEqual(run.inputs['parois']['mur']['u'], 0.5)
        self.assertEqual(run.inputs['facts']['mur.u']['method'], 'infere')
        self.assertNotIn('points', run.settings['weather_spec'])
        with tmy():
            run = calc_service.run_n2(run)
        self.assertEqual(run.status, Job.DONE)
        for k in ('heating_kwh', 'heating_kwh_m2', 'heating_kw_max', 'hours_over_28', 'primary_kwh_m2', 'confidence'):
            self.assertIn(k, run.indicators, k)
        self.assertEqual(len(run.results['monthly_heating_kwh']), 12)
        self.assertEqual(len(run.results['phi_hc_w']), len(WEATHER))
        self.assertAlmostEqual(sum(run.results['monthly_heating_kwh']), run.results['heating_kwh'], delta=1.0)
        with tmy():
            self.assertTrue(calc_service.is_up_to_date(run))
            again, fresh = calc_service.start_n2(b)
        self.assertFalse(fresh)
        self.assertEqual(again.pk, run.pk)
        facts.record(b, 'mur.u', 0.2, 'declare', author_sub='x')
        with tmy():
            self.assertFalse(calc_service.is_up_to_date(run))
        # La géométrie compte aussi : une emprise corrigée rend le N2 périmé, pas N1 seul.
        facts.retire(facts.effective(b)['mur.u'], 'x')
        with tmy():
            self.assertTrue(calc_service.is_up_to_date(run))
            geometry_service.apply_edit(b, {'footprint_xy': [[0, 0], [12, 0], [12, 8], [0, 8]], 'eave_height_m': 3,
                                            'levels': 1, 'roof_type': 'terrasse'}, author_sub='x')
            self.assertFalse(calc_service.is_up_to_date(run))

    def test_one_heavy_calc_at_a_time(self):
        b = make_building(fiche())
        self._start(b)
        with self.assertRaises(calc_service.Busy):
            self._start(make_building(fiche(), name='autre'))

    def test_calcul_3d_run_has_no_freshness(self):
        b = make_building(fiche())
        legacy = CalcRun.objects.create(building=b, level='N2', status=Job.DONE, settings={'dx_max': 0.05})
        self.assertIsNone(calc_service.is_up_to_date(legacy))
        self.assertFalse(calc_service.from_fiche(legacy))


class N1VersusN2FicheTest(TestCase):
    """Recette du lot : sur les trois cas de recette du Lot 10, écrits comme des fiches, N2 lancé depuis la
    fiche reste sous la tolérance D12 (20 %) de N1 calculé depuis la même fiche."""

    CASES = {
        'boite': (dict(**{'baies.ratio_S': 0.0, 'baies.ratio_N': 0.0, 'baies.ratio_E': 0.0, 'baies.ratio_O': 0.0}),
                  [[0, 0], [10, 0], [10, 8], [0, 8]]),
        'maison': (dict(**{'identite.hauteur_m': 5.5, 'identite.niveaux': 2, 'identite.surface_m2': 160.0,
                           'baies.ratio_S': 0.30, 'enveloppe.inertie': 'moyenne'}), [[0, 0], [10, 0], [10, 8], [0, 8]]),
        'collectif': (dict(**{'identite.usage': 'logement_collectif', 'identite.hauteur_m': 9.0, 'identite.niveaux': 3,
                              'identite.surface_m2': 720.0, 'identite.emprise_m2': 240.0, 'baies.ratio_S': 0.35,
                              'mur.materiau': 'beton', 'mur.isolation': 'iti', 'enveloppe.inertie': 'lourde'}),
                      [[0, 0], [20, 0], [20, 12], [0, 12]]),
    }

    def test_three_cases(self):
        for name, (over, fp) in self.CASES.items():
            vals = fiche(**over)
            b = make_building(vals, footprint=fp, name=name)
            with tmy():
                r1, _ = calc_service.run_level(b, 'N1')
                run, _ = calc_service.start_n2(b)
                run = calc_service.run_n2(run)
            Job.objects.filter(pk=run.job_id).update(status=Job.DONE)  # libère le verrou du lab
            h1, h2 = r1.indicators['heating_kwh'], run.indicators['heating_kwh']
            with self.subTest(cas=name, n1=h1, n2=h2):
                self.assertLess(abs(h1 / h2 - 1), 0.20)


@patch_jwks()
class N2ApiTest(TestCase):
    def setUp(self):
        self.p = Portfolio.objects.create(name='P')
        PortfolioMember.objects.create(portfolio=self.p, sub='gp', role=PortfolioMember.GESTIONNAIRE_PARC)
        self.b = make_building(fiche())
        self.b.portfolio = self.p
        self.b.save()
        BuildingAccess.objects.create(building=self.b, sub='occ', role=BuildingAccess.OCCUPANT)

    def test_manager_launches_occupant_refused(self):
        with tmy(), mock.patch('api.tasks.run_n2_fiche.delay') as delay:
            r = client_for(sub='occ').post(f'/api/batiments/{self.b.pk}/calculer/', {'niveaux': ['N2']}, format='json')
            self.assertEqual(r.status_code, 403)
            with self.captureOnCommitCallbacks(execute=True):
                r = client_for(sub='gp').post(f'/api/batiments/{self.b.pk}/calculer/', {'niveaux': ['N1', 'N2']},
                                              format='json')
        self.assertEqual(r.status_code, 200, r.content)
        runs = {x['level']: x for x in r.json()['runs']}
        self.assertEqual(runs['N1']['status'], 'DONE')
        self.assertEqual(runs['N2']['status'], 'PENDING')
        self.assertIsNotNone(runs['N2']['job'])
        delay.assert_called_once()
        # L'occupant lance toujours N0/N1.
        with tmy():
            r = client_for(sub='occ').post(f'/api/batiments/{self.b.pk}/calculer/', {'niveaux': ['N1']}, format='json')
        self.assertEqual(r.status_code, 200)

    def test_result_reports_n2_freshness(self):
        with tmy():
            run, _ = calc_service.start_n2(self.b)
            calc_service.run_n2(run)
            out = client_for(sub='gp').get(f'/api/batiments/{self.b.pk}/resultat/').json()
        self.assertTrue(out['N2']['up_to_date'])
        self.assertTrue(out['N2']['from_fiche'])
