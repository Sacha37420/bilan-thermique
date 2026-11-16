"""Chantier bt-trois-niveaux, lot 42 (secteur C) — exécution du recalcul : ordre et files, « à jour » non refait,
verrou des calculs lourds occupé ⇒ replanifié, réglages coupés, erreur qui n'arrête pas les suivants (une seule
nouvelle tentative), une demande pendant l'exécution ⇒ une seule exécution de plus, une exécution à la fois.

Les calculs sont simulés ici (contrat I3 côté consommateur) ; `test_recalcul_reel` les joue pour de vrai."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .. import calc_service
from ..models import Building, Portfolio, Recalcul
from ..recalcul import execution as ex, planification as plan, taches
from ..testing import without_auto_environment

_env = without_auto_environment()


def setUpModule():
    _env.start()


def tearDownModule():
    _env.stop()


BQ = [SimpleNamespace(pk=11, nom='Économiser'), SimpleNamespace(pk=12, nom='Confort')]


@override_settings(RECALCUL_EN_TEST=True)
class _Base(TestCase):
    def setUp(self):
        self.calls = []
        patches = {
            'send': mock.patch.object(taches.recalcul_batiment, 'apply_async'),
            'send_fond': mock.patch.object(taches.recalcul_fond, 'apply_async'),
            'run_level': mock.patch('api.calc_service.run_level', side_effect=self._run_level),
            'run_n2': mock.patch('api.calc_service.run_n2_now', side_effect=self._run_n2),
            'bqs': mock.patch('api.ameliorations.potentiels.bouquets_visibles', return_value=BQ, create=True),
            'a_jour': mock.patch('api.ameliorations.potentiels.a_jour', side_effect=self._a_jour, create=True),
            'calculer': mock.patch('api.ameliorations.potentiels.calculer', side_effect=self._calculer, create=True),
        }
        self.m = {}
        for k, p in patches.items():
            self.m[k] = p.start()
            self.addCleanup(p.stop)
        self.up_to_date = set()
        self.fail = set()
        self.busy = False
        self.p = Portfolio.objects.create(name='parc', owner_sub='g')
        self.b = Building.objects.create(name='b', portfolio=self.p, georef_lat=47.39, georef_lon=0.69,
                                         pipeline_status='complet')

    # ── faux calculs ──
    def _run_level(self, building, niveau, **kw):
        self.calls.append(('reel', niveau))
        if ('reel', niveau) in self.fail:
            raise calc_service.CalcError('x')
        return object(), ('reel', niveau) not in self.up_to_date

    def _run_n2(self, building, **kw):
        if self.busy:
            raise calc_service.Busy('occupé')
        self.calls.append(('reel', 'N2'))
        return object(), ('reel', 'N2') not in self.up_to_date

    def _a_jour(self, building, bq, niveau):
        return (bq.pk, niveau) in self.up_to_date

    def _calculer(self, building, bq, niveau, owner_sub=''):
        if niveau == 'N2' and self.busy:
            raise calc_service.Busy('occupé')
        self.calls.append((bq.pk, niveau))
        if (bq.pk, niveau) in self.fail:
            raise RuntimeError('boum')
        return SimpleNamespace(statut='fait')

    # ── aides ──
    def planned(self, **kw):
        Recalcul.objects.update_or_create(building=self.b, defaults={
            'statut': 'planifie', 'prevu_pour': timezone.now() - timedelta(seconds=1), 'cause': 'donnée mur.u', **kw})

    def r(self):
        return Recalcul.objects.get(building=self.b)

    def run_all(self):
        """Exécute comme le worker : file `celery`, puis la phase de fond postée en file `parc`."""
        with self.captureOnCommitCallbacks(execute=True):
            out = ex.executer(self.b.pk, 'celery')
        if out == 'rapide':
            c = self.m['send_fond'].call_args
            self.assertEqual(c.kwargs['queue'], 'parc')
            with self.captureOnCommitCallbacks(execute=True):
                out = ex.executer_fond(*c.kwargs['args'])
        return out

    def resultats(self):
        return [(e['etat'], e['niveau'], e['resultat']) for e in self.r().etapes]


class OrdreTest(_Base):
    def test_ordre_et_files(self):
        self.planned()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rapide')
        self.assertEqual(self.calls, [('reel', 'N0'), ('reel', 'N1')])     # file celery : réel N0/N1 seulement
        self.assertEqual(self.r().statut, 'en_cours')
        c = self.m['send_fond'].call_args
        self.assertEqual((c.kwargs['args'], c.kwargs['queue']), ([self.b.pk, 'donnée mur.u'], 'parc'))
        with self.captureOnCommitCallbacks(execute=True):
            ex.executer_fond(*c.kwargs['args'])
        self.assertEqual(self.calls, [('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2'),
                                      (11, 'N0'), (12, 'N0'), (11, 'N1'), (12, 'N1'), (11, 'N2'), (12, 'N2')])
        r = self.r()
        self.assertEqual((r.statut, r.message), ('inactif', ''))
        self.assertIsNotNone(r.termine_le)
        self.assertEqual({x[2] for x in self.resultats()}, {'calcule'})
        self.assertEqual(len(r.etapes), 9)
        self.m['send'].assert_not_called()     # rien de replanifié

    def test_file_parc_tout_dans_la_meme_tache(self):
        self.planned()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(ex.executer(self.b.pk, 'parc'), 'fait')
        self.assertEqual(len(self.calls), 9)
        self.m['send_fond'].assert_not_called()

    def test_a_jour_non_recalcule(self):
        self.up_to_date = {('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2'), (11, 'N0'), (11, 'N1'), (11, 'N2'),
                           (12, 'N0'), (12, 'N1'), (12, 'N2')}
        self.planned()
        self.run_all()
        self.assertEqual({x[2] for x in self.resultats()}, {'deja_a_jour'})
        self.m['calculer'].assert_not_called()   # potentiels : `a_jour` dit vrai, aucun calcul


class VerrousTest(_Base):
    def test_une_execution_a_la_fois(self):
        self.planned()
        Recalcul.objects.filter(building=self.b).update(statut='en_cours', commence_le=timezone.now())
        self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rien')
        self.assertEqual(self.calls, [])

    def test_trop_tot_la_tache_se_renvoie(self):
        self.planned(prevu_pour=timezone.now() + timedelta(seconds=40))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rien')
        c = self.m['send'].call_args
        self.assertGreaterEqual(c.kwargs['countdown'], 39)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.r().statut, 'planifie')

    def test_rien_a_faire(self):
        self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rien')       # pas de ligne
        Recalcul.objects.create(building=self.b, statut='inactif')
        self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rien')
        self.assertEqual(self.calls, [])

    def test_chaine_en_cours_abandonne(self):
        self.planned()
        Building.objects.filter(pk=self.b.pk).update(pipeline_status='en_attente')
        self.assertEqual(ex.executer(self.b.pk, 'celery'), 'rien')
        self.assertEqual((self.r().statut, self.calls), ('inactif', []))

    def test_execution_interrompue_reprise(self):
        self.planned()
        Recalcul.objects.filter(building=self.b).update(statut='en_cours', commence_le=timezone.now() - timedelta(hours=3))
        self.assertEqual(self.run_all(), 'fait')
        self.assertEqual(len(self.calls), 9)

    def test_verrou_occupe_replanifie(self):
        self.busy = True
        self.planned()
        self.run_all()
        res = self.resultats()
        self.assertIn(('reel', 'N2', 'occupe'), res)
        self.assertEqual([x for x in res if x[1] == 'N2'],
                         [('reel', 'N2', 'occupe'), (11, 'N2', 'occupe'), (12, 'N2', 'occupe')])
        self.assertNotIn((11, 'N2'), self.calls)           # pas d'essai inutile une fois le verrou vu pris
        self.assertIn((11, 'N1'), self.calls)              # les rapides passent quand même
        r = self.r()
        self.assertEqual((r.statut, r.message, r.cause), ('planifie', '', plan.OCCUPE))   # jamais un échec affiché
        c = self.m['send'].call_args
        self.assertEqual((c.kwargs['countdown'], c.kwargs['queue']), (plan.DELAI_OCCUPE_S, 'parc'))
        # Le verrou se libère : le nouvel essai calcule le N2, rien d'autre (le reste est à jour).
        self.busy, self.calls = False, []
        self.up_to_date = {('reel', 'N0'), ('reel', 'N1'), (11, 'N0'), (12, 'N0'), (11, 'N1'), (12, 'N1')}
        Recalcul.objects.filter(building=self.b).update(prevu_pour=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            ex.executer(self.b.pk, 'parc')
        self.assertEqual(self.calls, [('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2'), (11, 'N2'), (12, 'N2')])
        self.assertEqual(self.r().statut, 'inactif')


class ReglagesTest(_Base):
    def test_niveaux_et_potentiels_coupes(self):
        self.p.orchestration = {'calcul.n2': False, 'calcul.n0': False}
        Portfolio.objects.filter(pk=self.p.pk).update(orchestration=self.p.orchestration)
        self.planned()
        self.run_all()
        self.assertEqual(self.calls, [('reel', 'N1'), (11, 'N1'), (12, 'N1')])
        res = self.resultats()
        self.assertEqual({x for x in res if x[2] == 'desactive'},
                         {('reel', 'N0', 'desactive'), ('reel', 'N2', 'desactive'), (11, 'N0', 'desactive'),
                          (12, 'N0', 'desactive'), (11, 'N2', 'desactive'), (12, 'N2', 'desactive')})
        Portfolio.objects.filter(pk=self.p.pk).update(orchestration={'calcul.potentiels': False})
        self.calls = []
        self.planned()
        self.run_all()
        self.assertEqual(self.calls, [('reel', 'N0'), ('reel', 'N1'), ('reel', 'N2')])


class ErreursTest(_Base):
    def test_erreur_n_arrete_pas_les_suivants_une_seule_nouvelle_tentative(self):
        self.fail = {('reel', 'N1'), (11, 'N0')}
        self.planned()
        self.run_all()
        self.assertEqual(len(self.calls), 9)               # tout a été tenté
        res = self.resultats()
        self.assertIn(('reel', 'N1', 'erreur'), res)
        self.assertIn((11, 'N0', 'erreur'), res)
        self.assertIn((12, 'N0', 'calcule'), res)
        r = self.r()
        self.assertEqual((r.statut, r.cause), ('planifie', plan.NOUVEL_ESSAI))
        self.assertEqual(self.m['send'].call_args.kwargs['countdown'], plan.DELAI_NOUVEL_ESSAI_S)
        # Le nouvel essai échoue encore : statut « erreur », message fixe, plus rien de planifié.
        self.m['send'].reset_mock()
        Recalcul.objects.filter(building=self.b).update(prevu_pour=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            ex.executer(self.b.pk, 'parc')
        r = self.r()
        self.assertEqual((r.statut, r.message), ('erreur', ex.MESSAGE_ERREUR))
        self.m['send'].assert_not_called()

    def test_potentiel_rendu_en_erreur(self):
        self.m['calculer'].side_effect = lambda *a, **k: SimpleNamespace(statut='erreur')
        self.planned()
        self.run_all()
        self.assertIn((11, 'N0', 'erreur'), self.resultats())


class RedemandeTest(_Base):
    def test_demande_pendant_execution_une_seule_de_plus(self):
        def edit(building, niveau, **kw):
            if niveau == 'N1':   # une personne corrige la fiche pendant le calcul (hors du fil du recalcul)
                tok = plan.en_recalcul.set(None)
                try:
                    plan.planifier(Building.objects.get(pk=self.b.pk), 'donnée toiture.u')
                    plan.planifier(Building.objects.get(pk=self.b.pk), 'donnée mur.u')
                finally:
                    plan.en_recalcul.reset(tok)
            return object(), True
        self.m['run_level'].side_effect = edit
        self.planned()
        self.run_all()
        self.assertEqual(self.m['send'].call_count, 1)           # une seule exécution de plus
        r = self.r()
        self.assertEqual((r.statut, r.cause), ('planifie', 'donnée mur.u'))
        self.assertEqual(self.m['send'].call_args.kwargs['queue'], 'celery')

    def test_ecriture_du_recalcul_lui_meme_une_suite_au_plus(self):
        def self_write(building, niveau, **kw):
            plan.planifier(Building.objects.get(pk=self.b.pk), 'enveloppe')   # dans le fil du recalcul
            return object(), True
        self.m['run_level'].side_effect = self_write
        self.planned()
        self.run_all()
        self.assertEqual(self.r().cause, plan.SUITE)
        self.assertEqual(self.m['send'].call_count, 1)           # une suite…
        self.m['send'].reset_mock()
        Recalcul.objects.filter(building=self.b).update(prevu_pour=timezone.now())
        self.run_all()
        self.assertEqual(self.r().statut, 'inactif')              # …mais pas de boucle
        self.m['send'].assert_not_called()


class RepriseTest(_Base):
    def test_demarrage_du_worker(self):
        self.planned()
        b2 = Building.objects.create(name='b2', portfolio=self.p)
        Recalcul.objects.create(building=b2, statut='en_cours', commence_le=timezone.now())
        self.assertEqual(taches.reprendre(), 2)
        self.assertEqual(Recalcul.objects.get(building=b2).statut, 'planifie')
        self.assertEqual({c.kwargs['queue'] for c in self.m['send'].call_args_list}, {'parc'})
