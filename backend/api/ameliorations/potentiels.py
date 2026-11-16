"""Potentiels d'un bâtiment et d'un parc sur trois niveaux (I5 de bt-potentiels v3 ; I3 de bt-trois-niveaux v1).

Un **potentiel** = le bâtiment avec toutes les actions applicables d'un bouquet (amélioration générée, unique par
bâtiment et bouquet — commune aux trois niveaux) et son calcul, unique par (bâtiment, bouquet, niveau).

- N0, N1 : calcul synchrone (`calculer`, `compute_level`), ou en file `parc` pour tout un parc, un à la fois.
- N2 : `calculer` (le système — recalcul automatique, secteur C — et la file du parc) l'exécute **dans le
  processus appelant**, sous le verrou des calculs lourds (`joblock`) ; il lève `calc_service.Busy` si un calcul
  lourd tourne. Un humain le lance à la main par `start_n2` (en tâche de fond, gestionnaire seulement — D2).

« À jour » : l'**empreinte** du potentiel — fiche de l'état actuel, protections, géométrie, environnement relié
(N2), météo choisie du bâtiment, éléments du bouquet, réglages « parametrage » du parc, version des moteurs et des
actions — est celle calculée maintenant. Elle se calcule sans réseau (aucune météo n'est résolue pour la lire).
"""

from django.utils import timezone

from .. import calc_service, facts, geometry_service, reglages, shading_service
from ..fingerprint import stable_hash
from ..models import Job, Potentiel
from . import bouquets as bq_service
from . import catalogue

NIVEAUX = ('N0', 'N1', 'N2')
NIVEAUX_RAPIDES = ('N0', 'N1')
MAX_N1_PAR_APPEL = 4
SKIP_ECARTS = {'confidence', 'completeness', 'surface_m2', 'n_samples', 'dpe_gap_pct'}


class PotentielError(ValueError):
    pass


def empreinte(building, bouquet, niveau):
    parts = [niveau, calc_service.ENGINE_VERSION[niveau], catalogue.VERSION, facts.fiche_hash(building),
             shading_service.signature(building), geometry_service.effective_summary(building),
             building.weather_dataset_id, bouquet.elements, reglages.for_building(building, 'parametrage') or {}]
    if niveau == 'N2':
        parts.append(calc_service.n2_context_hash(building, None))
    return stable_hash(*parts)


def _sync_n2(pot):
    """Un N2 lancé en tâche de fond : le statut du potentiel suit celui de son calcul."""
    if pot.niveau != 'N2' or pot.statut not in ('en_attente', 'en_cours') or pot.run_id is None:
        return
    run = pot.run
    if run.status == Job.DONE:
        pot.statut, pot.calcule_le, pot.message = 'fait', timezone.now(), ''
    elif run.status == Job.ERROR:
        pot.statut, pot.message = 'erreur', (run.message or 'Calcul en erreur.')[:500]
    else:
        return
    pot.save(update_fields=['statut', 'calcule_le', 'message'])


def bouquets_visibles(building):
    """I3 : bouquets du lab puis ceux du parc du bâtiment (ordre d'affichage)."""
    bq_service.ensure_defaults()
    return list(bq_service.visible_for_building(building))


def _fait_a_jour(pot, emp):
    if pot.statut != 'fait' or pot.empreinte != emp or pot.run_id is None:
        return False
    return pot.run.status == Job.DONE


def a_jour(building, bouquet, niveau):
    """I3 : vrai si le potentiel (bâtiment, bouquet, niveau) est calculé avec les entrées d'aujourd'hui. Aucun calcul."""
    if niveau not in NIVEAUX:
        return False
    pot = (Potentiel.objects.filter(building=building, bouquet=bouquet, niveau=niveau).select_related('run').first())
    if pot is None:
        return False
    _sync_n2(pot)
    return _fait_a_jour(pot, empreinte(building, bouquet, niveau))


# ── Calculs ─────────────────────────────────────────────────────────────────

def ensure_base(building, niveau, owner_sub=''):
    """Le calcul N0/N1 de l'état actuel, à jour (calculé s'il le faut) — sert de référence aux écarts."""
    return calc_service.run_level(building, niveau, owner_sub=owner_sub)[0]


def ensure_base_n1(building, owner_sub=''):
    return ensure_base(building, 'N1', owner_sub)


def compute_level(building, bouquet, niveau, owner_sub='', owner_name='', force=False):
    """Potentiel N0 ou N1, synchrone. Une erreur de calcul reste sur le potentiel (statut « erreur »)."""
    if niveau not in NIVEAUX_RAPIDES:
        raise PotentielError('Niveau : N0 ou N1.')
    pot, _ = Potentiel.objects.get_or_create(building=building, bouquet=bouquet, niveau=niveau)
    emp = empreinte(building, bouquet, niveau)
    if not force and pot.statut == 'fait' and pot.empreinte == emp and pot.run_id is not None:
        return pot
    Potentiel.objects.filter(pk=pot.pk).update(statut='en_cours', message='')
    s, actions = bq_service.apply_to_building(building, bouquet, owner_sub, owner_name)
    pot.scenario, pot.actions, pot.empreinte = s, actions, emp
    try:
        ensure_base(building, niveau, owner_sub)
        run, _ = calc_service.run_level(building, niveau, scenario=s, owner_sub=owner_sub)
    except calc_service.CalcError as exc:
        pot.statut, pot.message, pot.run = 'erreur', str(exc)[:500], None
    else:
        pot.statut, pot.message, pot.run, pot.calcule_le = 'fait', '', run, timezone.now()
    pot.save()
    return pot


def compute_n1(building, bouquet, owner_sub='', owner_name='', force=False):
    return compute_level(building, bouquet, 'N1', owner_sub, owner_name, force)


def compute_n0(building, bouquet, owner_sub='', owner_name='', force=False):
    return compute_level(building, bouquet, 'N0', owner_sub, owner_name, force)


def lourd_en_cours():
    """Vrai si un calcul lourd est actif sur le lab (même règle que `joblock.launch_exclusive`, sans verrou)."""
    from .. import joblock
    return Job.objects.filter(status__in=joblock.ACTIVE).exists()


BUSY = 'Un calcul lourd est déjà en cours sur le lab — réessayez dans quelques minutes.'


def n2_synchrone(building, scenario=None, owner_sub=''):
    """N2 depuis la fiche dans le processus appelant, sous le verrou des calculs lourds (`calc_service.run_n2_now`,
    secteur C). Rend le calcul terminé ; lève `calc_service.Busy` (un lourd tourne) ou `CalcError`."""
    if lourd_en_cours():
        raise calc_service.Busy(BUSY)
    return calc_service.run_n2_now(building, scenario=scenario, owner_sub=owner_sub)[0]


def compute_n2(building, bouquet, owner_sub='', owner_name='', force=False):
    """Potentiel N2 synchrone (système). Lève `calc_service.Busy` sans rien écrire si un calcul lourd tourne."""
    pot, _ = Potentiel.objects.get_or_create(building=building, bouquet=bouquet, niveau='N2')
    _sync_n2(pot)
    emp = empreinte(building, bouquet, 'N2')
    if not force and _fait_a_jour(pot, emp):
        return pot
    if pot.run_id is not None and pot.statut in ('en_attente', 'en_cours') and pot.run.status in (Job.PENDING, Job.RUNNING):
        raise calc_service.Busy('Un N2 de ce potentiel est déjà en cours.')
    if lourd_en_cours():
        raise calc_service.Busy(BUSY)
    s, actions = bq_service.apply_to_building(building, bouquet, owner_sub, owner_name)
    pot.scenario, pot.actions, pot.empreinte = s, actions, emp
    try:
        run = n2_synchrone(building, s, owner_sub)
    except calc_service.Busy:
        pot.save(update_fields=['scenario', 'actions', 'empreinte'])
        raise
    except calc_service.CalcError as exc:
        pot.statut, pot.message, pot.run = 'erreur', str(exc)[:500], None
    else:
        pot.statut, pot.message, pot.run, pot.calcule_le = 'fait', '', run, timezone.now()
    pot.save()
    return pot


def calculer(building, bouquet, niveau, owner_sub=''):
    """I3 : calcule (ou rend tel quel s'il est à jour) le potentiel (bâtiment, bouquet, niveau), synchrone.
    N2 : lève `calc_service.Busy` si un calcul lourd tourne. Une erreur de calcul reste sur le potentiel."""
    if niveau in NIVEAUX_RAPIDES:
        return compute_level(building, bouquet, niveau, owner_sub)
    if niveau == 'N2':
        return compute_n2(building, bouquet, owner_sub)
    raise PotentielError('Niveau : N0, N1 ou N2.')


def start_n2(building, bouquet, owner_sub='', owner_name=''):
    """Lance à la main le N2 d'un potentiel en tâche de fond (gestionnaire, D2). Lève `calc_service.Busy` si un
    calcul lourd tourne déjà sur le lab."""
    pot, _ = Potentiel.objects.get_or_create(building=building, bouquet=bouquet, niveau='N2')
    _sync_n2(pot)
    if pot.run_id is not None and pot.statut in ('en_attente', 'en_cours'):
        return pot   # un N2 de ce potentiel tourne déjà
    emp = empreinte(building, bouquet, 'N2')
    s, actions = bq_service.apply_to_building(building, bouquet, owner_sub, owner_name)
    run, created = calc_service.start_n2(building, scenario=s, owner_sub=owner_sub)
    pot.scenario, pot.actions, pot.empreinte, pot.run, pot.message = s, actions, emp, run, ''
    if run.status == Job.DONE:
        pot.statut, pot.calcule_le = 'fait', timezone.now()
    else:
        pot.statut = 'en_cours'
    pot.save()
    return pot


# ── Lecture (I5) ────────────────────────────────────────────────────────────

def _numeric(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def ecarts(ind, ref):
    """(écarts absolus, écarts relatifs en fraction) bouquet − réel, sur les indicateurs numériques communs."""
    d, pct = {}, {}
    if not ind or not ref:
        return d, pct
    for k, v in ind.items():
        r = ref.get(k)
        if k in SKIP_ECARTS or not _numeric(v) or not _numeric(r):
            continue
        d[k] = round(v - r, 3)
        if r:
            pct[k] = round((v - r) / abs(r), 4)
    return d, pct


def reel(building, niveau='N1'):
    run = calc_service.current_or_latest(building, niveau)
    if run is None:
        return {'niveau': niveau, 'a_jour': False, 'indicateurs': None, 'run_id': None, 'calcule_le': None}
    a_jour = calc_service.is_up_to_date(run)
    return {'niveau': niveau, 'a_jour': bool(a_jour), 'indicateurs': run.indicators or None, 'run_id': run.pk,
            'calcule_le': run.created_at}


def _entry(building, bouquet, pot, refs, hashes, niveau='N1'):
    head = {'bouquet': {'id': bouquet.pk, 'nom': bouquet.nom, 'objectif': bouquet.objectif}}
    if pot is None:
        return {**head, 'niveau': niveau, 'statut': None, 'a_jour': False, 'calcule_le': None, 'scenario_id': None,
                'run_id': None, 'indicateurs': None, 'ecarts': {}, 'ecarts_pct': {}, 'actions': [], 'message': ''}
    _sync_n2(pot)
    ind = pot.run.indicators if (pot.run_id and pot.statut == 'fait' and pot.run.status == Job.DONE) else None
    ref = refs.get(pot.niveau)
    d, pct = ecarts(ind, ref)
    key = (bouquet.pk, pot.niveau)
    if key not in hashes:
        hashes[key] = empreinte(building, bouquet, pot.niveau)
    return {**head, 'niveau': pot.niveau, 'statut': pot.statut,
            'a_jour': pot.statut == 'fait' and pot.empreinte == hashes[key], 'calcule_le': pot.calcule_le,
            'scenario_id': pot.scenario_id, 'run_id': pot.run_id, 'indicateurs': ind, 'ecarts': d, 'ecarts_pct': pct,
            'actions': pot.actions or [], 'message': pot.message if pot.statut == 'erreur' else ''}


def _ref(base):
    return base['indicateurs'] if base['indicateurs'] else None


def building_payload(building, can_manage):
    """GET bâtiment (I5 / I5.b, I3 v1) : `reel` reste le N1 (forme v3) ; `reels` donne l'état actuel aux trois
    niveaux ; une entrée par bouquet **et par niveau** (N0, N1, N2), « jamais calculé » là où rien n'existe."""
    bq_service.ensure_defaults()
    reels = {n: reel(building, n) for n in NIVEAUX}
    refs = {n: _ref(reels[n]) for n in NIVEAUX}
    pots = {(p.bouquet_id, p.niveau): p for p in Potentiel.objects.filter(building=building).select_related('run')}
    rows, hashes = [], {}
    for b in bq_service.visible_for_building(building):
        for n in NIVEAUX:
            rows.append(_entry(building, b, pots.get((b.pk, n)), refs, hashes, n))
    return {'peut_calculer': bool(can_manage), 'peut_calculer_n2': bool(can_manage), 'reel': reels['N1'],
            'reels': reels, 'bouquets': rows}


def resolve_bouquets(visible, ids):
    if ids in (None, ''):
        return list(visible)
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise PotentielError('Liste de bouquets attendue.')
    by = {b.pk: b for b in visible}
    if any(i not in by for i in ids):
        raise PotentielError('Bouquet inconnu pour ce bâtiment ou ce parc.')
    return [by[i] for i in dict.fromkeys(ids)]


# ── Parc ────────────────────────────────────────────────────────────────────

def visible_for_portfolio(portfolio):
    from django.db.models import F, Q
    from ..models import Bouquet
    return Bouquet.objects.filter(Q(portfolio__isnull=True) | Q(portfolio=portfolio)).order_by(F('portfolio_id').asc(nulls_first=True), 'ordre', 'pk')


def enqueue_parc(portfolio, buildings, bouquets, niveau='N1'):
    """Met en file `parc` les potentiels du niveau qui ne sont pas à jour ; rend le nombre mis en file."""
    n = 0
    for b in buildings:
        for bq in bouquets:
            pot, _ = Potentiel.objects.get_or_create(building=b, bouquet=bq, niveau=niveau)
            if niveau == 'N2':
                _sync_n2(pot)
                if pot.statut == 'en_cours' and pot.run_id is not None and pot.run.status in (Job.PENDING, Job.RUNNING):
                    continue   # un N2 lancé à la main tourne déjà
            if pot.statut == 'fait' and pot.empreinte == empreinte(b, bq, niveau):
                continue
            if pot.statut != 'en_attente':
                Potentiel.objects.filter(pk=pot.pk).update(statut='en_attente', message='')
            n += 1
    return n


def next_pending(portfolio_id):
    """Prochain potentiel en attente d'un parc (N0, puis N1, puis N2), marqué « en cours » (verrou : jamais traité
    deux fois)."""
    from django.db import transaction
    with transaction.atomic():
        pot = (Potentiel.objects.select_for_update(skip_locked=True)
               .filter(building__portfolio_id=portfolio_id, niveau__in=NIVEAUX, statut='en_attente')
               .order_by('niveau', 'pk').first())
        if pot is None:
            return None
        Potentiel.objects.filter(pk=pot.pk).update(statut='en_cours')
    return Potentiel.objects.select_related('building', 'bouquet', 'run').get(pk=pot.pk)


def process_one(pot, owner_sub=''):
    """Calcule un potentiel de la file. Ne lève jamais : une erreur reste sur le potentiel. Rend `'occupe'` si un
    N2 n'a pas pu partir (calcul lourd en cours) : le potentiel est remis en attente."""
    from .. import net
    try:
        if pot.niveau == 'N2':
            compute_n2(pot.building, pot.bouquet, owner_sub)
        elif pot.niveau == 'N0':
            compute_n0(pot.building, pot.bouquet, owner_sub, force=True)
        else:
            compute_n1(pot.building, pot.bouquet, owner_sub, force=True)
    except calc_service.Busy:
        Potentiel.objects.filter(pk=pot.pk).update(statut='en_attente')
        return 'occupe'
    except Exception as exc:  # noqa: BLE001
        Potentiel.objects.filter(pk=pot.pk).update(statut='erreur', message=net.public_message(exc)[:500])
    return 'fait'


def parc_payload(portfolio, buildings, can_manage, niveau='N1'):
    """GET parc (I5.b, I3 v1) : réel, potentiels, file et agrégats **du niveau demandé**, `niveau` en tête."""
    from .. import parc
    bq_service.ensure_defaults()
    bqs = list(visible_for_portfolio(portfolio))
    pots = {}
    for p in Potentiel.objects.filter(building__in=buildings, niveau=niveau).select_related('run'):
        pots[(p.building_id, p.bouquet_id)] = p
    rows, series = [], {'reel': []}
    for bq in bqs:
        series[str(bq.pk)] = []
    for b in buildings:
        base = reel(b, niveau)
        if base['indicateurs']:
            series['reel'].append(base['indicateurs'])
        potentials, hashes = {}, {}
        for bq in bqs:
            e = _entry(b, bq, pots.get((b.pk, bq.pk)), {niveau: base['indicateurs']}, hashes, niveau)
            potentials[str(bq.pk)] = {
                'statut': e['statut'], 'a_jour': e['a_jour'], 'calcule_le': e['calcule_le'],
                'indicateurs': e['indicateurs'], 'ecarts': e['ecarts'], 'ecarts_pct': e['ecarts_pct'],
                'actions_appliquees': sum(1 for a in e['actions'] if a.get('appliquee')),
                'actions_ecartees': sum(1 for a in e['actions'] if not a.get('appliquee'))}
            if e['statut'] == 'fait' and e['indicateurs']:
                series[str(bq.pk)].append(e['indicateurs'])
        rows.append({'id': b.pk, 'nom': b.name, 'reel': base, 'potentiels': potentials})
    qs = Potentiel.objects.filter(building__in=buildings, niveau=niveau)
    return {'niveau': niveau, 'peut_calculer': bool(can_manage),
            'file': {'en_attente': qs.filter(statut='en_attente').count(), 'en_cours': qs.filter(statut='en_cours').count()},
            'bouquets': [{'id': bq.pk, 'nom': bq.nom, 'objectif': bq.objectif} for bq in bqs],
            'batiments': rows,
            'agregats': {k: parc.agregats_potentiels(v) for k, v in series.items()}}
