"""Potentiels d'un bâtiment et d'un parc (I5, contrat bt-potentiels v3, secteur A).

Un **potentiel** = le bâtiment avec toutes les actions applicables d'un bouquet (amélioration générée) et son
calcul. N1 : calcul synchrone (bâtiment) ou en file `parc` (tout un parc), un potentiel à la fois. N2 : à la demande,
un bâtiment et un bouquet à la fois, gestionnaire seulement, jamais en lot (D37) — `calc_service.start_n2`.

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

NIVEAUX = ('N1', 'N2')
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


# ── Calculs ─────────────────────────────────────────────────────────────────

def ensure_base_n1(building, owner_sub=''):
    """Le N1 de l'état actuel, à jour (calculé s'il le faut) — sert de référence aux écarts."""
    return calc_service.run_level(building, 'N1', owner_sub=owner_sub)[0]


def compute_n1(building, bouquet, owner_sub='', owner_name='', force=False):
    pot, _ = Potentiel.objects.get_or_create(building=building, bouquet=bouquet, niveau='N1')
    emp = empreinte(building, bouquet, 'N1')
    if not force and pot.statut == 'fait' and pot.empreinte == emp and pot.run_id is not None:
        return pot
    Potentiel.objects.filter(pk=pot.pk).update(statut='en_cours', message='')
    s, actions = bq_service.apply_to_building(building, bouquet, owner_sub, owner_name)
    pot.scenario, pot.actions, pot.empreinte = s, actions, emp
    try:
        ensure_base_n1(building, owner_sub)
        run, _ = calc_service.run_level(building, 'N1', scenario=s, owner_sub=owner_sub)
    except calc_service.CalcError as exc:
        pot.statut, pot.message, pot.run = 'erreur', str(exc)[:500], None
    else:
        pot.statut, pot.message, pot.run, pot.calcule_le = 'fait', '', run, timezone.now()
    pot.save()
    return pot


def start_n2(building, bouquet, owner_sub='', owner_name=''):
    """Lance le N2 d'un potentiel. Lève `calc_service.Busy` si un calcul lourd tourne déjà sur le lab."""
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


def _entry(building, bouquet, pot, refs, hashes):
    head = {'bouquet': {'id': bouquet.pk, 'nom': bouquet.nom, 'objectif': bouquet.objectif}}
    if pot is None:
        return {**head, 'niveau': 'N1', 'statut': None, 'a_jour': False, 'calcule_le': None, 'scenario_id': None,
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


def building_payload(building, can_manage):
    bq_service.ensure_defaults()
    base = reel(building, 'N1')
    n2 = calc_service.current_or_latest(building, 'N2')
    refs = {'N1': base['indicateurs'], 'N2': n2.indicators if n2 is not None and n2.status == Job.DONE else None}
    pots = {(p.bouquet_id, p.niveau): p for p in Potentiel.objects.filter(building=building).select_related('run')}
    rows, hashes = [], {}
    for b in bq_service.visible_for_building(building):
        rows.append(_entry(building, b, pots.get((b.pk, 'N1')), refs, hashes))
        if (b.pk, 'N2') in pots:
            rows.append(_entry(building, b, pots[(b.pk, 'N2')], refs, hashes))
    return {'peut_calculer': bool(can_manage), 'peut_calculer_n2': bool(can_manage), 'reel': base, 'bouquets': rows}


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


def enqueue_parc(portfolio, buildings, bouquets):
    """Met en file `parc` les potentiels N1 qui ne sont pas à jour ; rend le nombre mis en file."""
    n = 0
    for b in buildings:
        for bq in bouquets:
            pot, _ = Potentiel.objects.get_or_create(building=b, bouquet=bq, niveau='N1')
            if pot.statut == 'fait' and pot.empreinte == empreinte(b, bq, 'N1'):
                continue
            if pot.statut != 'en_attente':
                Potentiel.objects.filter(pk=pot.pk).update(statut='en_attente', message='')
            n += 1
    return n


def next_pending(portfolio_id):
    """Prochain potentiel N1 en attente d'un parc, marqué « en cours » (verrou : jamais traité deux fois)."""
    from django.db import transaction
    with transaction.atomic():
        pot = (Potentiel.objects.select_for_update(skip_locked=True)
               .filter(building__portfolio_id=portfolio_id, niveau='N1', statut='en_attente').order_by('pk').first())
        if pot is None:
            return None
        Potentiel.objects.filter(pk=pot.pk).update(statut='en_cours')
    return Potentiel.objects.select_related('building', 'bouquet').get(pk=pot.pk)


def process_one(pot, owner_sub=''):
    """Calcule un potentiel de la file. Ne lève jamais : une erreur reste sur le potentiel."""
    from .. import net
    try:
        compute_n1(pot.building, pot.bouquet, owner_sub, force=True)
    except Exception as exc:  # noqa: BLE001
        Potentiel.objects.filter(pk=pot.pk).update(statut='erreur', message=net.public_message(exc)[:500])


def parc_payload(portfolio, buildings, can_manage):
    from .. import parc
    bq_service.ensure_defaults()
    bqs = list(visible_for_portfolio(portfolio))
    pots = {}
    for p in Potentiel.objects.filter(building__in=buildings, niveau='N1').select_related('run'):
        pots[(p.building_id, p.bouquet_id)] = p
    rows, series = [], {'reel': []}
    for bq in bqs:
        series[str(bq.pk)] = []
    for b in buildings:
        base = reel(b, 'N1')
        if base['indicateurs']:
            series['reel'].append(base['indicateurs'])
        potentials, hashes = {}, {}
        for bq in bqs:
            e = _entry(b, bq, pots.get((b.pk, bq.pk)), {'N1': base['indicateurs']}, hashes)
            potentials[str(bq.pk)] = {
                'statut': e['statut'], 'a_jour': e['a_jour'], 'calcule_le': e['calcule_le'],
                'indicateurs': e['indicateurs'], 'ecarts': e['ecarts'], 'ecarts_pct': e['ecarts_pct'],
                'actions_appliquees': sum(1 for a in e['actions'] if a.get('appliquee')),
                'actions_ecartees': sum(1 for a in e['actions'] if not a.get('appliquee'))}
            if e['statut'] == 'fait' and e['indicateurs']:
                series[str(bq.pk)].append(e['indicateurs'])
        rows.append({'id': b.pk, 'nom': b.name, 'reel': base, 'potentiels': potentials})
    qs = Potentiel.objects.filter(building__in=buildings, niveau='N1')
    return {'peut_calculer': bool(can_manage),
            'file': {'en_attente': qs.filter(statut='en_attente').count(), 'en_cours': qs.filter(statut='en_cours').count()},
            'bouquets': [{'id': bq.pk, 'nom': bq.nom, 'objectif': bq.objectif} for bq in bqs],
            'batiments': rows,
            'agregats': {k: parc.agregats_potentiels(v) for k, v in series.items()}}

