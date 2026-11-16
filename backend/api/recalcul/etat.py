"""État des calculs (chantier bt-trois-niveaux, I2 — secteur C). Lecture seule, sans réseau, sans calcul.

`etat_batiment` : exact — chaque calcul (réel et potentiels, N0/N1/N2) est dit « à jour » par son empreinte
d'entrées (`calc_service.is_up_to_date`, `potentiels.a_jour`).

`resume_parc` / `lignes_parc` : **estimés sans requête par bâtiment** (un parc compte des centaines de bâtiments) :
lus sur les lignes `Recalcul` seules. Comme toute modification utile planifie un recalcul, un bâtiment « inactif »
sans demande en attente est à jour ; un bâtiment planifié a tous ses calculs actifs à refaire (une donnée de la
fiche change l'empreinte de tous) ; un bâtiment en cours a ce qui reste de son plan ; un bâtiment en erreur, ses
étapes en erreur. Un bâtiment sans ligne (jamais recalculé) compte tous ses calculs comme restants.
"""

from django.db.models import Q

from . import execution as ex
from . import planification as plan

LIBELLE_REEL = 'État actuel'
RESTANTS = ('a_refaire', 'en_file', 'en_cours', 'jamais')


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _reels(building):
    from .. import calc_service
    from ..models import CalcRun, Job
    out = {}
    running = set(CalcRun.objects.filter(building=building, scenario__isnull=True,
                                         status__in=[Job.PENDING, Job.RUNNING]).values_list('level', flat=True))
    for niveau in ex.NIVEAUX:
        run = calc_service.current_or_latest(building, niveau)
        a_jour = bool(run is not None and calc_service.is_up_to_date(run))
        out[niveau] = {'run': run, 'a_jour': a_jour, 'en_cours': niveau in running, 'erreur': False}
    return out


def _potentiels(building, bqs):
    from ..ameliorations import potentiels
    from ..models import Potentiel
    pots = {(p.bouquet_id, p.niveau): p for p in Potentiel.objects.filter(building=building, bouquet__in=bqs)}
    out = {}
    for bq in bqs:
        for niveau in ex.NIVEAUX:
            p = pots.get((bq.pk, niveau))
            try:
                a_jour = bool(p is not None and potentiels.a_jour(building, bq, niveau))
            except Exception:  # noqa: BLE001 — l'état ne doit jamais faire échouer l'écran
                a_jour = False
            out[(bq.pk, niveau)] = {'pot': p, 'a_jour': a_jour,
                                    'en_cours': p is not None and p.statut == 'en_cours' and not a_jour,
                                    'erreur': p is not None and p.statut == 'erreur'}
    return out


def etat_batiment(building, *, peut_recalculer=False):
    """I2 (ETAT). `peut_recalculer` : décidé par la vue (gestionnaire du bâtiment)."""
    from ..models import Recalcul
    r = Recalcul.objects.filter(building_id=building.pk).first()
    statut = r.statut if r is not None else 'inactif'
    orch = plan.orchestration(building)
    try:
        bqs = ex.bouquets(building)
    except Exception:  # noqa: BLE001
        bqs = []
    reels, pots = _reels(building), _potentiels(building, bqs)
    order = ex.plan_calculs(building, orch, bqs)
    last = {(e.get('etat'), e.get('niveau')): e.get('resultat') for e in ((r.etapes if r else None) or [])}
    current = None
    if statut == 'en_cours':
        current = next((k for k in order if k not in last), None)
    noms = {bq.pk: bq.nom for bq in bqs}
    calculs = []
    for etat, niveau in order:
        if etat == ex.REEL:
            info = reels[niveau]
            run = info['run']
            run_id, calcule_le = (run.pk, run.created_at) if run is not None else (None, None)
            actif = ex.niveau_actif(orch, niveau)
        else:
            info = pots[(etat, niveau)]
            p = info['pot']
            run_id, calcule_le = (p.run_id, p.calcule_le) if p is not None else (None, None)
            actif = ex.potentiels_actifs(orch) and ex.niveau_actif(orch, niveau)
        if not actif:
            st = 'desactive'
        elif info['a_jour']:
            st = 'a_jour'
        elif (etat, niveau) == current or info['en_cours']:
            st = 'en_cours'
        elif statut in ('planifie', 'en_cours'):
            st = 'en_file'
        elif last.get((etat, niveau)) == 'erreur' or info['erreur']:
            st = 'erreur'
        elif run_id is None:
            st = 'jamais'
        else:
            st = 'a_refaire'
        calculs.append({'etat': etat, 'libelle': LIBELLE_REEL if etat == ex.REEL else noms.get(etat, ''),
                        'niveau': niveau, 'statut': st, 'run_id': run_id, 'calcule_le': _iso(calcule_le)})
    return {'statut': statut, 'cause': r.cause if r is not None else '',
            'prevu_pour': _iso(r.prevu_pour) if r is not None and statut == 'planifie' else None,
            'message': (r.message or '') if r is not None and statut == 'erreur' else '',
            'peut_recalculer': bool(peut_recalculer), 'calculs': calculs,
            'calculs_restants': sum(1 for c in calculs if c['statut'] in RESTANTS)}


# ── Parc ───────────────────────────────────────────────────────────────────

def _nombre_calculs(portfolio):
    """Calculs actifs par bâtiment du parc : niveaux actifs × (réel + bouquets visibles si potentiels actifs)."""
    from ..models import Bouquet
    orch = plan._orchestration_parc(portfolio)
    n_niv = sum(1 for n in ex.NIVEAUX if ex.niveau_actif(orch, n))
    n_bq = Bouquet.objects.filter(Q(portfolio__isnull=True) | Q(portfolio=portfolio)).count() \
        if ex.potentiels_actifs(orch) else 0
    return n_niv * (1 + n_bq)


def lignes_parc(portfolio, buildings=None):
    """(résumé I1, [{"id", "statut", "calculs_restants"}]) — trois requêtes, quel que soit le nombre de bâtiments."""
    from ..models import Building, Recalcul
    qs = buildings if buildings is not None else Building.objects.filter(portfolio=portfolio)
    ids = list(qs.order_by('pk').values_list('pk', flat=True))
    n_plan = _nombre_calculs(portfolio)
    rows = {b: (s, d, c, t, e) for b, s, d, c, t, e in Recalcul.objects.filter(building_id__in=ids).values_list(
        'building_id', 'statut', 'demande_le', 'commence_le', 'termine_le', 'etapes')}
    lignes = []
    resume = {'batiments': len(ids), 'a_jour': 0, 'planifies': 0, 'en_cours': 0, 'erreurs': 0, 'calculs_restants': 0}
    for bid in ids:
        row = rows.get(bid)
        if row is None:
            statut, restants = 'inactif', n_plan
        else:
            statut, demande, _commence, termine, etapes = row
            etapes = etapes or []
            if statut == 'planifie':
                restants = n_plan
            elif statut == 'en_cours':
                restants = max(0, n_plan - len(etapes))
            elif statut == 'erreur':
                restants = sum(1 for e in etapes if e.get('resultat') in ('erreur', 'occupe'))
            else:
                stale = demande is not None and (termine is None or demande > termine)
                restants = n_plan if stale else 0
        resume['calculs_restants'] += restants
        if statut == 'planifie':
            resume['planifies'] += 1
        elif statut == 'en_cours':
            resume['en_cours'] += 1
        elif statut == 'erreur':
            resume['erreurs'] += 1
        elif restants == 0:
            resume['a_jour'] += 1
        lignes.append({'id': bid, 'statut': statut, 'calculs_restants': restants})
    return resume, lignes


def resume_parc(portfolio, buildings=None):
    """I1. `buildings` (facultatif) : restreindre aux bâtiments visibles de l'appelant."""
    return lignes_parc(portfolio, buildings)[0]
