"""Chantier bt-trois-niveaux, lot 42 (secteur C) — planification du recalcul : regroupement, déclencheurs (signaux),
réglages du parc, chaîne en cours, aucun recalcul dans les tests ordinaires, aucune boucle sur les faits générés."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .. import facts
from ..models import (Bouquet, Building, BuildingFact, Environment, GeometryEdit, Portfolio, Recalcul, Scenario,
                      ShadingDevice, WeatherDataset)
from ..recalcul import planification as plan, taches
from ..testing import without_auto_environment

_env = without_auto_environment()


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()


class _Base(TestCase):
    def setUp(self):
        p1 = mock.patch.object(taches.recalcul_batiment, 'apply_async')
        p2 = mock.patch.object(taches.recalcul_fond, 'apply_async')
        self.send, self.send_fond = p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.p = Portfolio.objects.create(name='parc', owner_sub='g', orchestration={'calcul.recalcul_delai_s': 60})
        self.b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69,
                                         pipeline_status='complet')

    def r(self, b=None):
        return Recalcul.objects.filter(building=b or self.b).first()

    def commit(self):
        return self.captureOnCommitCallbacks(execute=True)

    def sent_ids(self):
        return [c.kwargs['args'][0] for c in self.send.call_args_list]


class TestsOrdinairesTest(_Base):
    def test_rien_sans_activation(self):
        with self.commit():
            facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
            plan.planifier(self.b, 'manuel', immediat=True)
            self.assertEqual(plan.planifier_parc(self.p, 'x'), 0)
        self.assertIsNone(self.r())
        self.send.assert_not_called()


@override_settings(RECALCUL_EN_TEST=True)
class RegroupementTest(_Base):
    def test_deux_modifications_une_tache_echeance_repoussee(self):
        t0 = timezone.now()
        with self.commit():
            facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
        r = self.r()
        self.assertEqual((r.statut, r.cause), ('planifie', 'donnée mur.u'))
        first = r.prevu_pour
        self.assertGreaterEqual(first, t0 + timedelta(seconds=59))
        self.assertEqual(self.send.call_count, 1)
        c = self.send.call_args
        self.assertEqual((c.kwargs['args'], c.kwargs['countdown'], c.kwargs['queue']), ([self.b.pk, 'celery'], 60, 'celery'))
        with self.commit():
            facts.record(self.b, 'toiture.u', 0.3, 'declare', author_sub='u')
        r = self.r()
        self.assertEqual(self.send.call_count, 1)       # regroupé : pas de seconde tâche
        self.assertGreaterEqual(r.prevu_pour, first)    # échéance repoussée
        self.assertEqual(r.cause, 'donnée toiture.u')

    def test_immediat_envoie_tout_de_suite(self):
        with self.commit():
            plan.planifier(self.b, 'x')
            plan.planifier(self.b, plan.MANUEL, immediat=True)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.send.call_args.kwargs['countdown'], 0)
        self.assertEqual(self.r().cause, 'recalcul demandé')

    def test_hors_parc_delai_par_defaut(self):
        b = Building.objects.create(name='seul', georef_lat=47.0, georef_lon=1.0)
        with self.commit():
            plan.planifier(b, 'x')
        self.assertEqual(self.send.call_args.kwargs['countdown'], 30)

    def test_demande_pendant_execution_notee_sans_tache(self):
        Recalcul.objects.create(building=self.b, statut='en_cours', commence_le=timezone.now() - timedelta(seconds=5))
        with self.commit():
            plan.planifier(self.b, 'x')
        r = self.r()
        self.assertEqual(r.statut, 'en_cours')
        self.assertGreater(r.demande_le, r.commence_le)
        self.send.assert_not_called()

    def test_tache_perdue_renvoyee(self):
        Recalcul.objects.create(building=self.b, statut='planifie', prevu_pour=timezone.now() - timedelta(hours=1))
        with self.commit():
            plan.planifier(self.b, 'x')
        self.assertEqual(self.send.call_count, 1)

    def test_erreur_redevient_planifie(self):
        Recalcul.objects.create(building=self.b, statut='erreur', message='m')
        with self.commit():
            plan.planifier(self.b, 'x')
        self.assertEqual((self.r().statut, self.r().message), ('planifie', ''))

    def test_ne_leve_jamais(self):
        with mock.patch.object(plan, '_planifier', side_effect=RuntimeError('boum')):
            self.assertIsNone(plan.planifier(self.b, 'x'))


@override_settings(RECALCUL_EN_TEST=True)
class ReglagesTest(_Base):
    def test_recalcul_auto_coupe_note_seulement(self):
        self.p.orchestration = {'calcul.recalcul_auto': False}
        self.p.save()
        Recalcul.objects.all().delete()
        self.send.reset_mock()
        with self.commit():
            facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
        r = self.r()
        self.assertEqual(r.statut, 'inactif')
        self.assertIsNotNone(r.demande_le)
        self.send.assert_not_called()
        with self.commit():       # la chaîne et le geste « Recalculer » passent quand même
            plan.planifier(self.b, plan.CHAINE)
        self.assertEqual(self.r().statut, 'planifie')
        self.assertEqual(self.send.call_count, 1)


@override_settings(RECALCUL_EN_TEST=True)
class DeclencheursTest(_Base):
    def assertPlanned(self, cause=None, b=None):
        r = self.r(b)
        self.assertIsNotNone(r)
        self.assertEqual(r.statut, 'planifie')
        if cause:
            self.assertEqual(r.cause, cause)
        Recalcul.objects.all().delete()

    def test_fait_enregistre_et_retire(self):
        with self.commit():
            f = facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
        self.assertPlanned('donnée mur.u')
        with self.commit():
            BuildingFact.objects.filter(pk=f.pk).first().delete()
        self.assertPlanned('donnée mur.u')

    def test_protection_et_geometrie(self):
        with self.commit():
            d = ShadingDevice.objects.create(building=self.b, kind='casquette', orientation='S')
        self.assertPlanned('protection solaire')
        with self.commit():
            d.delete()
        self.assertPlanned('protection solaire')
        with self.commit():
            GeometryEdit.objects.create(building=self.b, footprint_xy=[[0, 0], [5, 0], [5, 5]], eave_height_m=3, levels=1)
        self.assertPlanned('géométrie corrigée')

    def test_champs_du_batiment(self):
        with self.commit():
            self.b.envelope = {'vertices': [], 'triangles': []}
            self.b.save()
        self.assertPlanned('enveloppe')
        env = Environment.objects.create(name='e')
        with self.commit():
            self.b.environment = env
            self.b.save(update_fields=['environment'])
        self.assertPlanned('environnement relié')
        ds = WeatherDataset.objects.create(kind='tmy', label='m', series_hash='h' * 64)
        with self.commit():
            self.b.weather_dataset = ds
            self.b.save()
        self.assertPlanned('météo')
        with self.commit():
            self.b.georef_lat = 47.5
            self.b.save()
        self.assertPlanned('position')

    def test_champ_sans_effet_et_update_fields_hors_liste(self):
        with self.commit():
            self.b.description = 'texte'
            self.b.save()
            self.b.sun_visibility_stale = False
            self.b.save(update_fields=['sun_visibility_stale'])
        self.assertIsNone(self.r())

    def test_environnement_retouche(self):
        env = Environment.objects.create(name='e')
        Building.objects.filter(pk=self.b.pk).update(environment=env)
        with self.commit():
            env.scene_objects = [{'id': 1}]
            env.save()
        self.assertPlanned('environnement retouché')

    def test_fin_de_chaine(self):
        Building.objects.filter(pk=self.b.pk).update(pipeline_status='en_cours')
        b = Building.objects.get(pk=self.b.pk)
        with self.commit():   # pendant la chaîne : ses faits ne planifient rien
            facts.record(b, 'mur.u', 0.9, 'infere')
        self.assertIsNone(self.r())
        with self.commit():
            b.pipeline_status = 'complet'
            b.save()
        self.assertPlanned('fin de la chaîne automatique')

    def test_chaine_perimee_ne_bloque_pas(self):
        Building.objects.filter(pk=self.b.pk).update(pipeline_status='en_cours',
                                                     updated_at=timezone.now() - timedelta(hours=1))
        with self.commit():
            facts.record(self.b, 'mur.u', 0.9, 'declare', author_sub='u')
        self.assertPlanned()

    def test_amelioration_utilisateur_et_generee(self):
        with self.commit():
            s = Scenario.objects.create(building=self.b, name='ma variante')
        self.assertPlanned('amélioration modifiée')
        with self.commit():
            facts.record(self.b, 'mur.u', 0.2, 'declare', author_sub='u', scenario=s)
        self.assertPlanned()
        with self.commit():   # amélioration générée (potentiel) : rien — pas de boucle
            g = Scenario.objects.create(building=self.b, name='Potentiel', genere=True)
            facts.record(self.b, 'mur.u', 0.2, 'declare', author_sub='u', scenario=g)
            ShadingDevice.objects.create(building=self.b, scenario=g, kind='casquette', orientation='S')
            g.facts.all().delete()
            g.delete()
        self.assertIsNone(self.r())

    def test_suppression_en_cascade_du_batiment(self):
        facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
        Recalcul.objects.all().delete()
        with self.commit():
            Building.objects.get(pk=self.b.pk).delete()
        self.assertFalse(Recalcul.objects.exists())

    def test_suppression_par_queryset(self):
        # `QuerySet.delete()` (nettoyer_brouillons) : l'origine des signaux est le QuerySet, pas l'instance. Sans le
        # traiter, un recalcul était planifié pour un bâtiment supprimé et la transaction échouait en service sur la clé
        # étrangère (un TestCase ne valide jamais : seule l'absence de ligne `recalculs` le montre ici).
        facts.record(self.b, 'mur.u', 0.4, 'declare', author_sub='u')
        ShadingDevice.objects.create(building=self.b, kind='casquette', orientation='S')
        Recalcul.objects.all().delete()
        with self.commit():
            Building.objects.filter(pk=self.b.pk).delete()
        self.assertFalse(Recalcul.objects.exists())
        self.send.assert_not_called()
        p2 = Portfolio.objects.create(name='parc 2', owner_sub='g')
        b2 = Building.objects.create(name='b2', portfolio=p2, pipeline_status='complet')
        facts.record(b2, 'mur.u', 0.4, 'declare', author_sub='u')
        Recalcul.objects.all().delete()
        with self.commit():
            Portfolio.objects.filter(pk=p2.pk).delete()
        self.assertFalse(Recalcul.objects.exists())

    def test_bouquet_du_parc_tous_ses_batiments(self):
        b2 = Building.objects.create(name='b2', portfolio=self.p, pipeline_status='complet')
        autre = Building.objects.create(name='ailleurs', pipeline_status='complet')
        with self.commit():
            Bouquet.objects.create(portfolio=self.p, nom='Mon bouquet', elements=[])
        self.assertEqual(sorted(self.sent_ids()), sorted([self.b.pk, b2.pk]))
        self.assertEqual({c.kwargs['queue'] for c in self.send.call_args_list}, {'parc'})
        self.assertIsNone(self.r(autre))
        self.assertEqual(self.r(b2).cause, 'bouquet « Mon bouquet »')

    def test_bouquet_du_lab_tout_le_lab(self):
        autre = Building.objects.create(name='ailleurs', pipeline_status='complet')
        with self.commit():
            Bouquet.objects.create(portfolio=None, nom='Lab', elements=[])
        self.assertEqual(sorted(self.sent_ids()), sorted([self.b.pk, autre.pk]))

    def test_reglages_du_parc(self):
        b2 = Building.objects.create(name='b2', portfolio=self.p, pipeline_status='complet')
        attente = Building.objects.create(name='b3', portfolio=self.p, pipeline_status='en_attente')
        with self.commit():
            self.p.parametrage = {'ventilation.debit': 1}
            self.p.save(update_fields=['parametrage'])
        self.assertEqual(sorted(self.sent_ids()), sorted([self.b.pk, b2.pk]))   # pas celui dont la chaîne attend
        self.assertIsNone(self.r(attente))
        self.assertEqual(self.r().cause, 'réglages du parc')
        self.send.reset_mock()
        with self.commit():   # rien changé, ou une autre section : rien
            self.p.save()
            self.p.rendu = {'x': 1}
            self.p.name = 'autre nom'
            self.p.save()
        self.send.assert_not_called()

    def test_planifier_parc_regroupe(self):
        with self.commit():
            self.assertEqual(plan.planifier_parc(self.p, 'x'), 1)
            self.assertEqual(plan.planifier_parc(self.p, 'y'), 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.r().cause, 'y')

    def test_planifier_parc_requetes_bornees(self):
        for i in range(30):
            Building.objects.create(name=f'x{i}', portfolio=self.p, pipeline_status='complet')
        with self.assertNumQueries(8):   # indépendant du nombre de bâtiments
            with self.commit():
                plan.planifier_parc(self.p, 'x')
        self.assertEqual(Recalcul.objects.filter(statut='planifie').count(), 31)
