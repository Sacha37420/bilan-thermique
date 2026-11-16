"""Chantier bt-trois-niveaux, lot 42 (secteur C) — le recalcul joué pour de vrai (moteurs N0/N1/N2 et potentiels réels,
météo synthétique, aucun réseau, Celery simulé) : une modification de la fiche ⇒ tout recalculé sans geste ; rejoué
sans modification ⇒ rien n'est refait ; N2 sous le verrou des calculs lourds (`calc_service.run_n2_now`)."""

from unittest import mock

from django.test import TestCase, override_settings

from .. import calc_service, facts
from ..ameliorations import bouquets as bq_service
from ..models import Bouquet, CalcRun, Job, Potentiel, Recalcul
from ..recalcul import etat, execution as ex, taches
from ..testing import synthetic_year, without_auto_environment
from .test_n2_fiche import fiche, make_building

YEAR = synthetic_year(days=21)
_env = without_auto_environment()
_tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})


def setUpModule():
    _env.start()
    _tmy.start()


def tearDownModule():
    _tmy.stop()
    _env.stop()


class RunN2NowTest(TestCase):
    def test_occupe_sans_rien_ecrire(self):
        b = make_building(fiche())
        Job.objects.create(kind='calcul_3d', status=Job.RUNNING)
        with self.assertRaises(calc_service.Busy):
            calc_service.run_n2_now(b)
        self.assertFalse(CalcRun.objects.filter(building=b, level='N2').exists())

    def test_calcule_puis_a_jour(self):
        b = make_building(fiche())
        run, created = calc_service.run_n2_now(b)
        self.assertTrue(created)
        self.assertEqual(run.status, Job.DONE)
        self.assertEqual(run.job.status, Job.DONE)            # le verrou est rendu
        self.assertTrue(calc_service.is_up_to_date(run))
        again, created = calc_service.run_n2_now(b)
        self.assertEqual((again.pk, created), (run.pk, False))

    def test_erreur_rend_le_verrou(self):
        b = make_building(fiche())
        with mock.patch('api.calc_service.run_n2', side_effect=RuntimeError('boum')):
            with self.assertRaises(calc_service.CalcError):
                calc_service.run_n2_now(b)
        self.assertFalse(Job.objects.filter(status__in=[Job.PENDING, Job.RUNNING]).exists())


@override_settings(RECALCUL_EN_TEST=True)
class ChaineCompleteTest(TestCase):
    def test_modification_tout_recalcule_puis_rien(self):
        bq_service.ensure_defaults()
        n_bq = Bouquet.objects.filter(portfolio__isnull=True).count()
        with mock.patch.object(taches.recalcul_batiment, 'apply_async') as send, \
                mock.patch.object(taches.recalcul_fond, 'apply_async') as send_fond:
            b = make_building(fiche())
            Recalcul.objects.all().delete()
            with self.captureOnCommitCallbacks(execute=True):
                facts.record(b, 'mur.u', 0.4, 'declare', author_sub='u')      # une personne affine le bâtiment
            self.assertEqual(Recalcul.objects.get(building=b).statut, 'planifie')
            self.assertEqual(send.call_count, 1)
            Recalcul.objects.filter(building=b).update(prevu_pour=None)      # l'échéance du regroupement est passée
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(ex.executer(b.pk, 'celery'), 'rapide')
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(ex.executer_fond(*send_fond.call_args.kwargs['args']), 'fait')
            r = Recalcul.objects.get(building=b)
            self.assertEqual(r.statut, 'inactif', r.etapes)
            self.assertEqual(len(r.etapes), 3 * (1 + n_bq))
            self.assertEqual({e['resultat'] for e in r.etapes}, {'calcule'}, r.etapes)
            e = etat.etat_batiment(b)
            self.assertEqual({c['statut'] for c in e['calculs']}, {'a_jour'})
            self.assertEqual(e['calculs_restants'], 0)
            self.assertEqual(Potentiel.objects.filter(building=b, statut='fait').count(), 3 * n_bq)
            # Faits des améliorations générées écrits pendant le calcul : aucune boucle.
            self.assertEqual(send.call_count, 1)
            # Rejoué sans modification : rien n'est refait.
            n_runs = CalcRun.objects.filter(building=b).count()
            Recalcul.objects.filter(building=b).update(statut='planifie', prevu_pour=None)
            with self.captureOnCommitCallbacks(execute=True):
                ex.executer(b.pk, 'parc')
            r = Recalcul.objects.get(building=b)
            self.assertEqual({e['resultat'] for e in r.etapes}, {'deja_a_jour'}, r.etapes)
            self.assertEqual(CalcRun.objects.filter(building=b).count(), n_runs)
