"""Planification du recalcul automatique (chantier bt-trois-niveaux, I1 — secteur C).

Un seul mécanisme : les signaux (`signaux.py`) et les gestes « chaîne » / « Recalculer » appellent `planifier` ;
la ligne `Recalcul` du bâtiment porte l'état ; une tâche Celery `recalcul_batiment` l'exécute (`execution.py`).

- **Regroupement** : `prevu_pour = maintenant + délai du parc` (`calcul.recalcul_delai_s`, 30 s par défaut) ; une
  nouvelle demande repousse l'échéance (les modifications rapprochées donnent un seul recalcul). Une seule tâche
  est en vol par bâtiment : celle qui se réveille trop tôt se renvoie elle-même jusqu'à l'échéance.
- **Une exécution à la fois par bâtiment** : la tâche réclame la ligne sous verrou (`select_for_update`) ; une
  demande qui arrive pendant l'exécution est notée (`demande_le > commence_le`) et donne **une** exécution de plus,
  à la fin.
- **Rien dans les tests ordinaires** : `settings.TESTING and not settings.RECALCUL_EN_TEST` ⇒ aucun effet.
- **Jamais d'exception** vers l'appelant (un signal ne doit pas faire échouer l'enregistrement d'une donnée), et
  **aucun appel réseau** : on écrit une ligne et on poste une tâche après validation de la transaction.
"""

import contextvars
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

CHAINE, MANUEL = 'chaine', 'manuel'
LIBELLES = {CHAINE: 'fin de la chaîne automatique', MANUEL: 'recalcul demandé'}
SUITE = 'suite du recalcul'                   # demande née pendant l'exécution, de l'exécution elle-même
NOUVEL_ESSAI = 'nouvel essai après une erreur'
OCCUPE = 'calcul lourd occupé : nouvel essai'

DELAI_DEFAUT_S = 30
DELAI_OCCUPE_S = 120          # verrou des calculs lourds pris : nouvel essai
DELAI_NOUVEL_ESSAI_S = 300    # une erreur : une seule nouvelle tentative
PERDU_APRES = timedelta(minutes=10)       # échéance dépassée d'autant : tâche perdue (file Redis recréée) → renvoyée
EN_COURS_PERIME = timedelta(hours=2)      # exécution interrompue (worker redémarré) : reprise
CHAINE_EN_COURS = ('en_attente', 'en_cours')
CHAINE_PERIMEE = timedelta(minutes=30)    # même délai que la reprise d'un bâtiment de parc (`parc.STALE`)

FILE_INTERACTIVE, FILE_FOND = 'celery', 'parc'

# Bâtiment dont le recalcul s'exécute dans ce fil (pas de boucle : ses propres écritures sont « suite du recalcul »).
en_recalcul = contextvars.ContextVar('recalcul_en_cours', default=None)


def actif():
    return not (getattr(settings, 'TESTING', False) and not getattr(settings, 'RECALCUL_EN_TEST', False))


def libelle(cause):
    return (LIBELLES.get(cause) or str(cause or ''))[:200]


def _orchestration_parc(portfolio):
    from .. import reglages
    return reglages.effective(portfolio, 'orchestration') if portfolio is not None else reglages.defaults('orchestration')


def orchestration(building):
    """Réglages « orchestration » effectifs ; hors parc : les défauts (contrat I0)."""
    from .. import reglages
    return reglages.for_building(building, 'orchestration') or reglages.defaults('orchestration')


def delai_s(orch):
    try:
        return max(0, int(orch.get('calcul.recalcul_delai_s', DELAI_DEFAUT_S)))
    except (TypeError, ValueError):
        return DELAI_DEFAUT_S


def envoyer(building_ids, countdown, file):
    """Poste la tâche après validation de la transaction (le worker voit la ligne). Un courtier injoignable n'est
    pas une erreur pour l'appelant : la ligne reste « planifié » et sera renvoyée (`PERDU_APRES`, démarrage du
    worker)."""
    ids = list(building_ids)
    if not ids:
        return

    def go():
        from .taches import recalcul_batiment
        for i in ids:
            try:
                recalcul_batiment.apply_async(args=[i, file], countdown=max(0, int(countdown)), queue=file)
            except Exception:  # noqa: BLE001
                log.warning('recalcul : tâche non postée (bâtiment %s)', i)
    transaction.on_commit(go)


def _chaine_en_cours(status, updated_at, now):
    return status in CHAINE_EN_COURS and (updated_at is None or updated_at > now - CHAINE_PERIMEE)


# ── Un bâtiment ─────────────────────────────────────────────────────────────

def planifier(building, cause, *, immediat=False, file=FILE_INTERACTIVE):
    """I1. Regroupe ; sans effet si `calcul.recalcul_auto` est faux et `cause` ≠ 'chaine'/'manuel' (la demande est
    seulement notée : `demande_le`). Ne lève jamais."""
    if not actif() or building is None or getattr(building, 'pk', None) is None:
        return None
    try:
        with transaction.atomic():
            _planifier(building, cause, immediat, file)
    except Exception:  # noqa: BLE001 — un signal ne fait jamais échouer l'écriture qui l'a déclenché
        log.exception('recalcul : planification impossible (bâtiment %s)', building.pk)
    return None


def _planifier(building, cause, immediat, file):
    from ..models import Building, Portfolio, Recalcul
    now = timezone.now()
    row = Building.objects.filter(pk=building.pk).values_list('pipeline_status', 'updated_at', 'portfolio_id').first()
    if row is None:
        return
    status, updated_at, portfolio_id = row
    if en_recalcul.get() == building.pk:
        cause = SUITE
    if cause not in (CHAINE, MANUEL) and _chaine_en_cours(status, updated_at, now):
        return   # la chaîne écrit ses faits : elle planifie elle-même à sa fin (transition de `pipeline_status`)
    orch = _orchestration_parc(Portfolio.objects.filter(pk=portfolio_id).first() if portfolio_id else None)
    label = libelle(cause)
    Recalcul.objects.get_or_create(building_id=building.pk)
    r = Recalcul.objects.select_for_update().get(building_id=building.pk)
    if cause not in (CHAINE, MANUEL) and not orch.get('calcul.recalcul_auto', True):
        r.demande_le = now      # rien n'est planifié : la demande dit seulement que des calculs sont à refaire
        r.save(update_fields=['demande_le'])
        return
    delai = 0 if immediat else delai_s(orch)
    cible = now + timedelta(seconds=delai)
    fields = ['demande_le', 'cause', 'prevu_pour']
    pending_outside = (r.statut == 'en_cours' and r.demande_le and r.commence_le and r.demande_le > r.commence_le)
    if not (cause == SUITE and pending_outside):
        r.cause = label         # une « suite » ne remplace pas une demande venue d'ailleurs pendant l'exécution
    r.demande_le = now
    send = False
    if r.statut == 'en_cours' and not (r.commence_le and r.commence_le < now - EN_COURS_PERIME):
        r.prevu_pour = cible    # échéance de l'exécution de plus
    elif r.statut == 'planifie':
        perdu = r.prevu_pour is None or r.prevu_pour < now - PERDU_APRES
        if immediat or perdu:
            r.prevu_pour, send = cible, True
        else:
            r.prevu_pour = max(r.prevu_pour, cible)
    else:                       # inactif, erreur, ou exécution interrompue
        r.statut, r.prevu_pour, r.message, send = 'planifie', cible, '', True
        fields += ['statut', 'message']
    r.save(update_fields=fields)
    if send:
        envoyer([building.pk], delai, file)


# ── Plusieurs bâtiments (parc, bouquet du lab) ──────────────────────────────

def planifier_batiments(queryset, cause, *, file=FILE_FOND):
    """Planifie un ensemble de bâtiments en quelques requêtes (pas une par bâtiment). Rend le nombre planifié."""
    if not actif():
        return 0
    try:
        with transaction.atomic():
            return _planifier_lot(queryset, cause, file)
    except Exception:  # noqa: BLE001
        log.exception('recalcul : planification d\'un lot impossible')
        return 0


def _planifier_lot(queryset, cause, file):
    from ..models import Portfolio, Recalcul
    now = timezone.now()
    rows = list(queryset.order_by().values_list('pk', 'portfolio_id', 'pipeline_status', 'updated_at'))
    if not rows:
        return 0
    pids = {p for _i, p, _s, _u in rows if p is not None}
    orchs = {p.pk: _orchestration_parc(p) for p in Portfolio.objects.filter(pk__in=pids)}
    defaults = _orchestration_parc(None)
    eligible, noted = {}, []      # delai → [ids] ; ids dont la demande est seulement notée
    for pk, pid, status, updated_at in rows:
        if cause not in (CHAINE, MANUEL) and _chaine_en_cours(status, updated_at, now):
            continue
        orch = orchs.get(pid, defaults) if pid is not None else defaults
        if cause not in (CHAINE, MANUEL) and not orch.get('calcul.recalcul_auto', True):
            noted.append(pk)
            continue
        eligible.setdefault(delai_s(orch), []).append(pk)
    all_ids = noted + [i for ids in eligible.values() for i in ids]
    existing = set(Recalcul.objects.filter(building_id__in=all_ids).values_list('building_id', flat=True))
    Recalcul.objects.bulk_create([Recalcul(building_id=i) for i in all_ids if i not in existing], ignore_conflicts=True)
    if noted:
        Recalcul.objects.filter(building_id__in=noted).update(demande_le=now)
    label, n = libelle(cause), 0
    for delai, ids in eligible.items():
        cible = now + timedelta(seconds=delai)
        state = list(Recalcul.objects.select_for_update().filter(building_id__in=ids)
                     .values_list('building_id', 'statut', 'prevu_pour', 'commence_le'))
        start, running, debounce, lost = [], [], [], []
        for bid, statut, prevu, commence in state:
            if statut == 'en_cours' and not (commence and commence < now - EN_COURS_PERIME):
                running.append(bid)
            elif statut == 'planifie':
                (lost if prevu is None or prevu < now - PERDU_APRES else debounce).append(bid)
            else:
                start.append(bid)
        common = {'demande_le': now, 'cause': label}
        Recalcul.objects.filter(building_id__in=start).update(statut='planifie', prevu_pour=cible, message='', **common)
        Recalcul.objects.filter(building_id__in=running + lost).update(prevu_pour=cible, **common)
        # Regroupement : l'échéance recule (jamais n'avance) pour ceux qui attendent déjà.
        from django.db.models import Case, F, When
        Recalcul.objects.filter(building_id__in=debounce).update(
            prevu_pour=Case(When(prevu_pour__lt=cible, then=cible), default=F('prevu_pour')), **common)
        envoyer(start + lost, delai, file)
        n += len(state)
    return n


def planifier_parc(portfolio, cause):
    """I1. Tous les bâtiments du parc, en file de fond. Rend le nombre de bâtiments planifiés."""
    from ..models import Building
    if portfolio is None or getattr(portfolio, 'pk', None) is None:
        return 0
    return planifier_batiments(Building.objects.filter(portfolio_id=portfolio.pk), cause, file=FILE_FOND)


def planifier_tous(cause='mise à niveau'):
    """Tous les bâtiments du lab (mise en service du recalcul automatique, changement d'un bouquet du lab)."""
    from ..models import Building
    return planifier_batiments(Building.objects.all(), cause, file=FILE_FOND)
