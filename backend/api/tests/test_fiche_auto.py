"""Lot 9 (refonte) — fiche initiale automatique : BDNB, archétypes, géométrie, cache, quota.
Aucun appel réseau : les services sont simulés (réponses relevées sur les vrais services)."""

import time
from unittest import mock

import requests
import shapely.geometry
from django.test import SimpleTestCase, TestCase

from .. import archetypes, facts, pipeline, sources, tasks
from ..models import Building, BuildingFact, Job, SourceCache
from ..testing import client_for, patch_jwks, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


# Réponse BDNB relevée le 2026-11-14 (maison de 1992 en Indre-et-Loire, DPE 2016), réduite.
DPE_MAISON = {
    'batiment_groupe_id': 'bdnb-bg-D2R2-WG5P-NWGX', 'identifiant_dpe': '1637V1003369X',
    'date_reception_dpe': '2016-11-22T00:00:00', 'type_batiment_dpe': 'maison',
    'classe_conso_energie_arrete_2012': 'E', 'conso_3_usages_ep_m2_arrete_2012': 249.0,
    'classe_inertie': 'Lourde', 'facteur_solaire_baie_vitree': 0.47,
    'materiaux_structure_mur_exterieur': 'murs en blocs de béton creux',
    'pourcentage_surface_baie_vitree_exterieur': 0.162, 'surface_habitable_logement': 174.0,
    'surface_mur_deperditif': 161.11, 'surface_vitree_est': 2.0, 'surface_vitree_nord': 7.35,
    'surface_vitree_ouest': 0.0, 'surface_vitree_sud': 12.15, 'type_energie_chauffage': 'electricite',
    'type_fermeture': 'Persienne coulissante ou volet battant PVC', 'type_generateur_chauffage': 'generateurs a effet joule',
    'type_generateur_ecs': 'ballon accumulation electrique', 'type_isolation_mur_exterieur': 'ITI',
    'type_ventilation': 'Ventilation mécanique auto réglable « après 1982 »', 'type_vitrage': 'double vitrage',
    'u_mur_exterieur': 0.35, 'u_plancher_bas_final_deperditif': 0.52, 'u_plancher_haut_deperditif': 0.34, 'uw': 3.2,
}
GROUPE = {'batiment_groupe_id': 'bdnb-bg-D2R2-WG5P-NWGX', 'annee_construction': 1992,
          'usage_niveau_1_txt': 'Résidentiel individuel', 'nb_log': 1, 'nb_niveau': 2, 'hauteur_mean': 6,
          's_geom_groupe': 112, 'mat_mur_txt': 'INDETERMINE'}
BDNB_DATA = {'groupes': [GROUPE], 'dpe': [DPE_MAISON]}


def rect(x0, y0, x1, y1):
    return shapely.geometry.box(x0, y0, x1, y1)


class BdnbMappingTest(SimpleTestCase):
    def test_house_dpe_gives_document_values(self):
        rows = {k: (v, m) for k, v, m, _c, _s in pipeline.bdnb_facts(BDNB_DATA)}
        self.assertEqual(rows['mur.u'], (0.35, 'document'))
        self.assertEqual(rows['toiture.u'], (0.34, 'document'))
        self.assertEqual(rows['baies.uw'], (3.2, 'document'))
        self.assertEqual(rows['identite.annee_construction'], (1992, 'infere'))
        self.assertEqual(rows['identite.usage'][0], 'maison')
        self.assertEqual(rows['systeme.generateur'][0], 'joule')
        self.assertEqual(rows['ventilation.type'][0], 'vmc_sf_auto')
        self.assertEqual(rows['mur.isolation'][0], 'iti')
        self.assertEqual(rows['mur.materiau'][0], 'parpaing')
        self.assertEqual(rows['baies.vitrage'][0], 'double')
        self.assertEqual(rows['enveloppe.inertie'][0], 'lourde')
        self.assertEqual(rows['dpe.etiquette'], ('E', 'document'))
        self.assertEqual(rows['identite.surface_m2'], (174.0, 'document'))
        self.assertGreater(rows['baies.ratio_S'][0], rows['baies.ratio_O'][0])

    def test_collective_dpe_is_only_inferred(self):
        d = {**DPE_MAISON, 'type_batiment_dpe': 'appartement'}
        rows = {k: (v, m) for k, v, m, _c, _s in pipeline.bdnb_facts({'groupes': [GROUPE], 'dpe': [d]})}
        self.assertEqual(rows['mur.u'][1], 'infere')
        self.assertNotIn('identite.surface_m2', rows)  # surface d'UN logement : pas celle du bâtiment
        self.assertEqual(rows['baies.ratio_N'][0], 0.162)

    def test_every_mapped_value_is_valid_for_the_catalogue(self):
        for k, v, m, c, s in pipeline.bdnb_facts(BDNB_DATA):
            facts.validate_value(k, v)
            self.assertIn(m, facts.METHOD_RANK)

    def test_empty_bdnb(self):
        self.assertEqual(pipeline.bdnb_facts({'groupes': [], 'dpe': []}), [])


class ArchetypeTest(SimpleTestCase):
    def test_older_is_leakier_and_all_values_valid(self):
        old, _ = archetypes.defaults(1930, 'maison')
        new, label = archetypes.defaults(2018, 'maison')
        self.assertGreater(old['mur.u'], new['mur.u'])
        self.assertEqual(label, 'depuis 2013')
        self.assertEqual(old['ventilation.type'], 'naturelle')
        for k, v in {**old, **new}.items():
            facts.validate_value(k, v)

    def test_wood_is_light(self):
        self.assertEqual(archetypes.defaults(1990, 'maison', 'bois')[0]['enveloppe.inertie'], 'legere')


class GeometryTest(SimpleTestCase):
    def test_isolated_box_orientations(self):
        g = pipeline.geometry_from_footprints(rect(0, 0, 10, 8), [], 6.0)
        self.assertEqual(g['emprise_m2'], 80.0)
        self.assertEqual(g['mitoyennete'], 0.0)
        self.assertAlmostEqual(g['murs_exposes_m2']['S'], 60.0, places=1)  # 10 m × 6 m
        self.assertAlmostEqual(g['murs_exposes_m2']['E'], 48.0, places=1)

    def test_terraced_house_shares_walls(self):
        left, right = rect(-10, 0, 0, 8), rect(10, 0, 20, 8)
        g = pipeline.geometry_from_footprints(rect(0, 0, 10, 8), [(left, 6.0), (right, 6.0)], 6.0)
        self.assertAlmostEqual(g['mitoyennete'], 16 / 36, places=2)
        self.assertAlmostEqual(g['murs_exposes_m2']['E'], 0.0, places=1)
        self.assertAlmostEqual(g['murs_exposes_m2']['O'], 0.0, places=1)

    def test_tall_neighbour_south_raises_south_horizon(self):
        tower = rect(0, -30, 10, -20)  # à 20 m au sud, 30 m de haut
        g = pipeline.geometry_from_footprints(rect(0, 0, 10, 8), [(tower, 30.0)], 6.0)
        # Lot 30 : profil mesuré depuis la façade — plein sud, à mi-hauteur : atan((30 − 3) / 20) ≈ 53°.
        mid = g['horizon_profil']['S'][1::3]
        self.assertTrue(all(row[18] > 45 for row in mid))
        self.assertGreater(g['horizon_deg']['S'], 20)        # angle face à la façade (tour de 10 m de large)
        self.assertEqual(g['horizon_deg']['N'], 0.0)


def fake_ign(bbox):
    """BD TOPO simulée : la maison au point (0,0) et une voisine accolée à l'est, en lat/lon."""
    lat0, lon0 = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    dlat, dlon = 1 / 111_320, 1 / (111_320 * 0.68)
    def ring(x0, y0, x1, y1):
        return [[lat0 + y * dlat, lon0 + x * dlon] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]
    return [{'footprint_latlon': ring(-5, -4, 5, 4), 'height_m': 6.5, 'approx_height': False, 'base_z': None},
            {'footprint_latlon': ring(5, -4, 15, 4), 'height_m': 6.0, 'approx_height': False, 'base_z': None}]


SOURCES = {
    'api.sources.geocode': {'lat': 47.39, 'lon': 0.68, 'label': '1 rue Exemple 37000 Tours', 'score': 0.95},
    'api.sources.rnb_closest': {'rnb_id': 'ABCD1234EFGH', 'distance': 0.0, 'lat': 47.39, 'lon': 0.68, 'addresses': []},
    'api.sources.bdnb_for_rnb': BDNB_DATA,
}


class PipelineTest(TestCase):
    def _run(self, building, fail=None):
        patches = [mock.patch(k, side_effect=fail) if k == (fail and 'api.sources.bdnb_for_rnb') else
                   mock.patch(k, return_value=v) for k, v in SOURCES.items()]
        patches.append(mock.patch('api.geodata.fetch_ign_buildings', side_effect=fake_ign))
        for p in patches:
            p.start()
        try:
            return pipeline.initialize(building)
        finally:
            for p in patches:
                p.stop()

    def test_address_initialised_without_any_input(self):
        b = Building.objects.create(name='a', address='1 rue Exemple Tours')
        report = self._run(b)
        b.refresh_from_db()
        self.assertEqual(b.pipeline_status, 'complet', report)
        self.assertEqual(b.rnb_id, 'ABCD1234EFGH')
        eff = facts.effective_values(b)
        self.assertEqual(eff['mur.u'], 0.35)                       # DPE
        self.assertEqual(eff['identite.annee_construction'], 1992)  # BDNB
        self.assertIn('ventilation.n50', eff)                       # archétype
        self.assertGreater(eff['identite.mitoyennete'], 0.1)        # voisine accolée
        self.assertTrue(b.envelope.get('triangles'))               # enveloppe LOD1 pour le N2
        self.assertGreater(b.confidence, 0.3)
        self.assertGreater(b.completeness, 0.5)

    def test_inconsistent_dpe_surface_is_discarded(self):
        data = {'groupes': [{**GROUPE, 's_geom_groupe': 900, 'nb_niveau': 3}], 'dpe': [DPE_MAISON]}
        b = Building.objects.create(name='musee', address='x y z')
        patches = [mock.patch(k, return_value=(data if k.endswith('bdnb_for_rnb') else v)) for k, v in SOURCES.items()]
        patches.append(mock.patch('api.pipeline.fetch_footprints', return_value=None))
        for p in patches:
            p.start()
        try:
            report = pipeline.initialize(b)
        finally:
            for p in patches:
                p.stop()
        eff = facts.effective(b)
        self.assertEqual(eff['identite.surface_m2'].value, 2160.0)  # 900 × 3 × 0,8
        self.assertTrue(any('incohérente' in w for w in report['warnings']))

    def test_failed_step_marks_incomplete_but_keeps_archetype(self):
        b = Building.objects.create(name='b', address='1 rue Exemple Tours')
        report = self._run(b, fail=sources.SourceError('BDNB : délai dépassé'))
        b.refresh_from_db()
        self.assertEqual(b.pipeline_status, 'incomplet')
        self.assertEqual(report['steps']['A2'], {'ok': False, 'error': 'BDNB : délai dépassé'})
        self.assertIn('mur.u', facts.effective_values(b))  # archétype

    def test_rerun_keeps_override_and_does_not_duplicate(self):
        b = Building.objects.create(name='c', address='1 rue Exemple Tours')
        self._run(b)
        n = BuildingFact.objects.filter(building=b).count()
        facts.record(b, 'mur.u', 0.2, 'declare', author_sub='u1')
        self._run(b)
        extra = BuildingFact.objects.filter(building=b).exclude(superseded_by__isnull=False).count()
        dup = [f'{f.key}:{f.source}' for f in BuildingFact.objects.filter(building=b, superseded_by__isnull=False)]
        self.assertEqual(BuildingFact.objects.filter(building=b).count(), n + 1, dup)
        self.assertEqual(facts.effective_values(b)['mur.u'], 0.2)


class SourcesTest(TestCase):
    def test_cache_avoids_second_network_call(self):
        calls = []
        def fetch():
            calls.append(1)
            return {'x': 1}
        self.assertEqual(sources.cached('bdnb', 'X1', fetch), {'x': 1})
        self.assertEqual(sources.cached('bdnb', 'X1', fetch), {'x': 1})
        self.assertEqual(len(calls), 1)
        self.assertEqual(SourceCache.objects.count(), 1)

    def test_errors_not_cached_and_message_fixed(self):
        with mock.patch('requests.get', side_effect=requests.ConnectionError('https://api.bdnb.io/x?secret=1')):
            with self.assertRaises(sources.SourceError) as ctx:
                sources.bdnb_for_rnb('ABCD1234EFGH')
        self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual(SourceCache.objects.count(), 0)

    def test_bdnb_quota_interval(self):
        resp = mock.Mock(status_code=200, json=lambda: [])
        resp.raise_for_status = lambda: None
        sources._bdnb_last[0] = 0.0
        with mock.patch('requests.get', return_value=resp) as get, mock.patch('time.sleep') as sleep:
            sources._get_json('BDNB', sources.BDNB_URL + '/a')
            sources._get_json('BDNB', sources.BDNB_URL + '/b')
        self.assertEqual(get.call_count, 2)
        self.assertTrue(sleep.called)
        self.assertLessEqual(sleep.call_args.args[0], sources.BDNB_MIN_INTERVAL_S)
        self.assertEqual(get.call_args.kwargs['headers']['User-Agent'], sources.net.USER_AGENT)


@patch_jwks()
class InitApiTest(TestCase):
    def test_init_creates_building_and_job(self):
        with mock.patch('api.tasks.init_building.delay') as delay:
            r = client_for().post('/api/batiments/initialiser/', {'address': '1 rue Exemple Tours'}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        b = Building.objects.get(pk=r.json()['building_id'])
        self.assertEqual(b.pipeline_status, 'en_attente')
        delay.assert_called_once()

    def test_init_requires_some_location(self):
        self.assertEqual(client_for().post('/api/batiments/initialiser/', {}, format='json').status_code, 400)

    def test_task_runs_pipeline(self):
        b = Building.objects.create(name='t', address='x y z')
        job = Job.objects.create(kind='init_building')
        with mock.patch('api.pipeline.initialize', return_value={'steps': {}}) as init:
            tasks.init_building.apply(args=[job.pk, b.pk])
        init.assert_called_once()
        self.assertEqual(Job.objects.get(pk=job.pk).status, Job.DONE)
