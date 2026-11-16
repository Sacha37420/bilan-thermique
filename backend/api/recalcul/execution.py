"""Exécution d'un recalcul (chantier bt-trois-niveaux, I1 — secteur C).

Ordre (contrat I1) :
1. **file `celery`** — réel N0, réel N1 (rapides : le bâtiment est à jour en quelques secondes) ;
2. **file `parc`** — réel N2 (sous le verrou des calculs lourds), puis les potentiels de chaque bouquet visible :
   tous les N0, puis tous les N1, puis tous les N2 (les résultats rapides de tous les bouquets arrivent d'abord).

Un calcul « à jour » (même empreinte d'entrées) n'est jamais refait : `run_level`, `run_n2_now` et
`potentiels.a_jour` le disent sans calcul. L'erreur d'un calcul n'arrête pas les suivants. Verrou des calculs
lourds pris ⇒ étape « occupé », recalcul replanifié (`DELAI_OCCUPE_S`), jamais d'échec affiché. Une erreur ⇒
une seule nouvelle tentative (`DELAI_NOUVEL_ESSAI_S`), puis statut « erreur » avec un message fixe.

Le système lance le N2 de lui-même (D2 : la règle « gestionnaire seulement » vaut pour les gestes à la main) ;
`owner_sub` vide = calcul du système.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import planification as plan

log = logging.getLogger(__name__)

NIVEAUX = ('N0', 'N1', 'N2')
CLES = {'N0': 'calcul.n0', 'N1': 'calcul.n1', 'N2': 'calcul.n2'}
REEL = 'reel'
MESSAGE_ERREUR = 'Un ou plusieurs calculs ont échoué ; les autres sont à jour. Corrigez la fiche ou relancez.'


def niveau_actif(orch, niveau):
    return bool(orch.get(CLES[niveau], True))


def potentiels_actifs(orch):
    return bool(orch.get('calcul.potentiels', True))


def bouquets(building):
    """I3 (secteur D) : bouquets visibles, dans l'ordre d'affichage (lab, puis parc)."""
    from ..ameliorations import potentiels
    return list(potentiels.bouquets_visibles(building))


def plan_calculs(building, orch, bqs):
    """[(etat, niveau)] dans l'ordre d'exécution."""
    out = [(REEL, n) for n in NIVEAUX]
    out += [(bq.pk, n) for n in NIVEAUX for bq in bqs]
    return out


def _etape(etat, niveau, resultat):
    return {'etat': etat, 'niveau': niveau, 'resultat': resultat}


def _noter(building_id, etapes):
    from ..models import Recalcul
    Recalcul.objects.filter(building_id=building_id).update(etapes=list(etapes))


# ── Réclamer / terminer (sous verrou de ligne) ──────────────────────────────

def reclamer(building_id, file):
    """(bâtiment, cause de départ) si cette tâche prend l'exécution, sinon None (déjà en cours, rien à faire, trop
    tôt — la tâche est alors renvoyée à l'échéance —, ou chaîne en cours — elle replanifiera à sa fin)."""
    from ..models import Building, Recalcul
    now = timezone.now()
    with transaction.atomic():
        r = Recalcul.objects.select_for_update().filter(building_id=building_id).first()
        if r is None:
            return None
        if r.statut == 'en_cours':
            if not (r.commence_le and r.commence_le < now - plan.EN_COURS_PERIME):
                return None
        elif r.statut != 'planifie':
            return None
        if r.prevu_pour and r.prevu_pour > now + timedelta(seconds=1):
            plan.envoyer([building_id], (r.prevu_pour - now).total_seconds() + 1, file)
            return None
        b = Building.objects.filter(pk=building_id).first()
        if b is None:
            return None
        if plan._chaine_en_cours(b.pipeline_status, b.updated_at, now):
            r.statut = 'inactif'    # la chaîne replanifie à sa fin ; la demande reste notée (demande_le)
            r.save(update_fields=['statut'])
            return None
        cause = r.cause
        r.statut, r.commence_le, r.etapes, r.message = 'en_cours', now, [], ''
        r.save(update_fields=['statut', 'commence_le', 'etapes', 'message'])
    return b, cause


def terminer(building_id, etapes, cause_depart):
    from ..models import Recalcul
    now = timezone.now()
    with transaction.atomic():
        r = Recalcul.objects.select_for_update().filter(building_id=building_id).first()
        if r is None:
            return None
        r.etapes, r.termine_le = list(etapes), now
        erreurs = any(e['resultat'] == 'erreur' for e in etapes)
        occupe = any(e['resultat'] == 'occupe' for e in etapes)
        suite_seule = r.cause == plan.SUITE and cause_depart == plan.SUITE
        redemande = bool(r.demande_le and r.commence_le and r.demande_le > r.commence_le and not suite_seule)
        fields = ['etapes', 'termine_le', 'statut', 'message', 'prevu_pour', 'cause']
        send = None
        if redemande:   # une demande pendant l'exécution : UNE exécution de plus, à son échéance
            prevu = max(r.prevu_pour or now, now)
            r.statut, r.prevu_pour, r.message = 'planifie', prevu, ''
            send = ((prevu - now).total_seconds(), plan.FILE_INTERACTIVE)
        elif occupe:
            r.statut, r.message, r.cause = 'planifie', '', plan.OCCUPE
            r.prevu_pour = now + timedelta(seconds=plan.DELAI_OCCUPE_S)
            send = (plan.DELAI_OCCUPE_S, plan.FILE_FOND)
        elif erreurs and cause_depart != plan.NOUVEL_ESSAI:
            r.statut, r.message, r.cause = 'planifie', '', plan.NOUVEL_ESSAI
            r.prevu_pour = now + timedelta(seconds=plan.DELAI_NOUVEL_ESSAI_S)
            send = (plan.DELAI_NOUVEL_ESSAI_S, plan.FILE_FOND)
        elif erreurs:
            r.statut, r.message, r.prevu_pour = 'erreur', MESSAGE_ERREUR, None
        else:
            r.statut, r.message, r.prevu_pour = 'inactif', '', None
        r.save(update_fields=fields)
        if send:
            plan.envoyer([building_id], send[0], send[1])
    return r


# ── Les calculs ─────────────────────────────────────────────────────────────

def _reel_rapide(building, niveau):
    from .. import calc_service
    try:
        _run, created = calc_service.run_level(building, niveau, owner_sub='')
    except calc_service.Busy:
        return 'occupe'
    except Exception:  # noqa: BLE001 — l'erreur d'un calcul n'arrête pas les suivants
        log.info('recalcul : %s du bâtiment %s en erreur', niveau, building.pk, exc_info=True)
        return 'erreur'
    return 'calcule' if created else 'deja_a_jour'


def _reel_n2(building):
    from .. import calc_service
    try:
        _run, created = calc_service.run_n2_now(building, owner_sub='')
    except calc_service.Busy:
        return 'occupe'
    except Exception:  # noqa: BLE001
        log.info('recalcul : N2 du bâtiment %s en erreur', building.pk, exc_info=True)
        return 'erreur'
    return 'calcule' if created else 'deja_a_jour'


def _potentiel(building, bq, niveau):
    from .. import calc_service
    from ..ameliorations import potentiels
    try:
        if potentiels.a_jour(building, bq, niveau):
            return 'deja_a_jour'
        pot = potentiels.calculer(building, bq, niveau, owner_sub='')
    except calc_service.Busy:
        return 'occupe'
    except Exception:  # noqa: BLE001
        log.info('recalcul : potentiel %s/%s du bâtiment %s en erreur', bq.pk, niveau, building.pk, exc_info=True)
        return 'erreur'
    return 'erreur' if getattr(pot, 'statut', 'fait') == 'erreur' else 'calcule'


def phase_rapide(building, etapes):
    orch = plan.orchestration(building)
    for niveau in ('N0', 'N1'):
        res = _reel_rapide(building, niveau) if niveau_actif(orch, niveau) else 'desactive'
        etapes.append(_etape(REEL, niveau, res))
        _noter(building.pk, etapes)


def phase_fond(building, etapes):
    orch = plan.orchestration(building)
    if niveau_actif(orch, 'N2'):
        res = _reel_n2(building)
        building.refresh_from_db()   # l'environnement a pu être généré et la géométrie croisée
    else:
        res = 'desactive'
    etapes.append(_etape(REEL, 'N2', res))
    _noter(building.pk, etapes)
    occupe = res == 'occupe'
    try:
        bqs = bouquets(building)
    except Exception:  # noqa: BLE001
        log.info('recalcul : bouquets du bâtiment %s illisibles', building.pk, exc_info=True)
        bqs = []
    for niveau in NIVEAUX:
        for bq in bqs:
            if not (potentiels_actifs(orch) and niveau_actif(orch, niveau)):
                res = 'desactive'
            elif niveau == 'N2' and occupe:
                res = 'occupe'      # un lourd tourne : inutile d'essayer les autres
            else:
                res = _potentiel(building, bq, niveau)
                occupe = occupe or (niveau == 'N2' and res == 'occupe')
            etapes.append(_etape(bq.pk, niveau, res))
            _noter(building.pk, etapes)


def executer(building_id, file=plan.FILE_INTERACTIVE):
    """Tâche `recalcul_batiment`. File `celery` : la phase rapide, puis la phase de fond est postée en file `parc` ;
    file `parc` (lot, reprise) : tout dans la même tâche."""
    got = reclamer(building_id, file)
    if got is None:
        return 'rien'
    building, cause = got
    token = plan.en_recalcul.set(building_id)
    etapes = []
    try:
        phase_rapide(building, etapes)
        if file == plan.FILE_INTERACTIVE:
            from .taches import recalcul_fond
            transaction.on_commit(lambda: recalcul_fond.apply_async(args=[building_id, cause], queue=plan.FILE_FOND))
            return 'rapide'
        phase_fond(building, etapes)
    except Exception:  # noqa: BLE001 — jamais une ligne bloquée « en cours »
        log.exception('recalcul : exécution interrompue (bâtiment %s)', building_id)
        etapes.append(_etape(REEL, '', 'erreur'))
    finally:
        plan.en_recalcul.reset(token)
    terminer(building_id, etapes, cause)
    return 'fait'


def executer_fond(building_id, cause):
    """Tâche `recalcul_fond` (file `parc`) : suite d'une exécution commencée en file `celery`."""
    from ..models import Building, Recalcul
    r = Recalcul.objects.filter(building_id=building_id).first()
    b = Building.objects.filter(pk=building_id).first()
    if r is None or b is None or r.statut != 'en_cours':
        return 'rien'
    etapes = list(r.etapes or [])
    token = plan.en_recalcul.set(building_id)
    try:
        phase_fond(b, etapes)
    except Exception:  # noqa: BLE001
        log.exception('recalcul : exécution interrompue (bâtiment %s)', building_id)
        etapes.append(_etape(REEL, '', 'erreur'))
    finally:
        plan.en_recalcul.reset(token)
    terminer(building_id, etapes, cause)
    return 'fait'
