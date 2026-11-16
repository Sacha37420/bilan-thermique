"""Bouquets de solutions (I7.b, I7.c) : validation, bouquets du lab, application à un bâtiment.

Appliquer un bouquet = appliquer **toutes** les actions dont les conditions bloquantes sont remplies ; un élément
« une parmi » retient la première action applicable. Le résultat est une amélioration (`Scenario`, `genere=True`)
**unique par (bâtiment, bouquet)** : réutilisée et vidée à chaque application (jamais dupliquée), pour que des
entrées identiques retrouvent le calcul déjà fait (empreinte de `calc_service`).

Bouquets du lab : créés par `ensure_defaults()`, idempotente, appelée à chaque lecture d'une liste de bouquets ou
de potentiels — plutôt qu'une migration de données : le code reste la seule définition (pas de copie figée dans une
migration qui divergerait), une base neuve ou vidée (tests) se répare seule, et aucun fichier partagé n'est touché.
"""

from django.db import transaction

from .. import facts, reglages
from ..models import Bouquet, Scenario, ShadingDevice
from . import catalogue

MAX_ELEMENTS = 30
MAX_OPTIONS = 6


class BouquetError(ValueError):
    pass


LAB_DEFAULTS = [
    {'objectif': 'energie', 'nom': 'Économiser l’énergie', 'ordre': 0,
     'description': 'Réduire les consommations de chauffage : isoler l’enveloppe, renouveler les menuiseries, '
                    'récupérer la chaleur de l’air extrait, changer de générateur.',
     'elements': [
         {'une_parmi': [{'action': 'ite', 'parametres': {}}, {'action': 'iti', 'parametres': {}}]},
         {'action': 'combles', 'parametres': {}},
         {'action': 'plancher_bas', 'parametres': {}},
         {'une_parmi': [{'action': 'triple', 'parametres': {}}, {'action': 'fenetres', 'parametres': {}}]},
         {'une_parmi': [{'action': 'vmc_df', 'parametres': {}}, {'action': 'vmc_hygro', 'parametres': {}}]},
         {'action': 'pac', 'parametres': {}},
     ]},
    {'objectif': 'confort', 'nom': 'Minimiser les DH', 'ordre': 1,
     'description': 'Réduire les degrés-heures d’inconfort d’été : protéger les baies du soleil, décharger la chaleur '
                    'la nuit, éclaircir ou végétaliser toiture et murs, ombrager par des arbres, isoler la toiture.',
     'elements': [
         {'action': 'protection_mobile', 'parametres': {'modele': 'store_ext', 'orientation': 'SO'}},
         {'action': 'surventilation_nocturne', 'parametres': {}},
         {'une_parmi': [{'action': 'toiture_vegetalisee', 'parametres': {}}, {'action': 'toiture_claire', 'parametres': {}}]},
         {'action': 'combles', 'parametres': {}},
         {'action': 'murs_clairs', 'parametres': {}},
         {'action': 'arbres', 'parametres': {'orientation': 'SO', 'nombre': 2}},
     ]},
]


def ensure_defaults():
    """Les deux bouquets du lab existent et suivent leur définition du code (idempotente)."""
    for d in LAB_DEFAULTS:
        b = Bouquet.objects.filter(portfolio__isnull=True, par_defaut=True, objectif=d['objectif']).order_by('pk').first()
        if b is None:
            Bouquet.objects.create(portfolio=None, par_defaut=True, auteur_sub='', **d)
            continue
        changed = [k for k in ('nom', 'description', 'elements', 'ordre') if getattr(b, k) != d[k]]
        if changed:
            for k in changed:
                setattr(b, k, d[k])
            b.save(update_fields=changed + ['modifie_le'])


# ── Validation (I7.b) ───────────────────────────────────────────────────────

def _one(raw):
    if not isinstance(raw, dict) or set(raw) - {'action', 'parametres'} or 'action' not in raw:
        raise BouquetError('Élément de bouquet mal formé.')
    try:
        params = catalogue.validate_params(raw['action'], raw.get('parametres'))
    except catalogue.ActionError as exc:
        raise BouquetError(str(exc)) from None
    return {'action': raw['action'], 'parametres': params}


def validate_elements(raw):
    if not isinstance(raw, list):
        raise BouquetError('Liste d’éléments attendue.')
    if len(raw) > MAX_ELEMENTS:
        raise BouquetError(f'{MAX_ELEMENTS} éléments au plus par bouquet.')
    out, seen = [], set()
    for el in raw:
        if isinstance(el, dict) and 'une_parmi' in el:
            if set(el) != {'une_parmi'} or not isinstance(el['une_parmi'], list) or not 1 <= len(el['une_parmi']) <= MAX_OPTIONS:
                raise BouquetError(f'« Une parmi » : de une à {MAX_OPTIONS} actions.')
            opts = [_one(o) for o in el['une_parmi']]
            item = {'une_parmi': opts}
        else:
            opts = [_one(el)]
            item = opts[0]
        cibles = set()
        for o in opts:
            cibles |= set(catalogue.BY_KEY[o['action']]['cibles'])
        if cibles & seen:
            raise BouquetError('Deux éléments du bouquet visent la même cible.')
        seen |= cibles
        out.append(item)
    return out


def validate(data, partial=False):
    out = {}
    if not partial or 'nom' in data:
        nom = str(data.get('nom') or '').strip()[:150]
        if not nom:
            raise BouquetError('Nom du bouquet attendu.')
        out['nom'] = nom
    if 'description' in data:
        out['description'] = str(data.get('description') or '')[:2000]
    if not partial or 'objectif' in data:
        obj = data.get('objectif', 'libre')
        if obj not in dict(Bouquet.OBJECTIFS):
            raise BouquetError('Objectif : energie, confort ou libre.')
        out['objectif'] = obj
    if not partial or 'elements' in data:
        out['elements'] = validate_elements(data.get('elements') if 'elements' in data else [])
    if 'ordre' in data:
        try:
            out['ordre'] = max(0, min(1000, int(data['ordre'])))
        except (TypeError, ValueError):
            raise BouquetError('Ordre : nombre entier attendu.') from None
    return out


def serialize(b, can_write=False):
    return {'id': b.pk, 'nom': b.nom, 'description': b.description, 'objectif': b.objectif, 'elements': b.elements,
            'ordre': b.ordre, 'par_defaut': b.par_defaut, 'portfolio': b.portfolio_id,
            'modifiable': bool(can_write and b.portfolio_id is not None), 'modifie_le': b.modifie_le}


def visible_for_building(building):
    from django.db.models import F, Q
    q = Q(portfolio__isnull=True)
    if building.portfolio_id is not None:
        q |= Q(portfolio_id=building.portfolio_id)
    return Bouquet.objects.filter(q).order_by(F('portfolio_id').asc(nulls_first=True), 'ordre', 'pk')


# ── Application ─────────────────────────────────────────────────────────────

def context(building):
    from .. import geometry_service
    fp = None
    try:
        summary = geometry_service.effective_summary(building) or {}
        fp = summary.get('footprint_xy') or geometry_service.auto_footprint(building)
    except Exception:  # noqa: BLE001 — l'emprise n'est qu'un placement d'arbres ; repli documenté
        fp = None
    return {'building': building, 'values': facts.effective_values(building),
            'cfg': reglages.for_building(building, 'parametrage') or {}, 'footprint': fp}


def plan_bouquet(building, bouquet, ctx=None):
    """(plans appliqués [(clé, plan)], actions I5) — sans rien écrire."""
    ctx = ctx or context(building)
    values = dict(ctx['values'])
    applied, report = [], []
    for el in bouquet.elements or []:
        opts = el['une_parmi'] if 'une_parmi' in el else [el]
        chosen = None
        for o in opts:
            a = catalogue.BY_KEY.get(o['action'])
            if a is None:   # action retirée du catalogue depuis l'enregistrement du bouquet
                report.append({'cle': o['action'], 'libelle': o['action'], 'appliquee': False,
                               'raison': 'Action retirée du catalogue.', 'parametres': o.get('parametres') or {},
                               'effets': [], 'avertissements': []})
                continue
            label = catalogue.action_label(a, ctx['cfg'])
            if chosen is not None:
                report.append({'cle': a['cle'], 'libelle': label, 'appliquee': False,
                               'raison': f'Une seule action du groupe : « {chosen} » a été retenue.',
                               'parametres': catalogue.full_params(a, o.get('parametres'), ctx['cfg']),
                               'effets': [], 'avertissements': []})
                continue
            plan, raison, warnings, params = catalogue.evaluate(a['cle'], o.get('parametres'), {**ctx, 'values': values})
            row = {'cle': a['cle'], 'libelle': label, 'appliquee': plan is not None, 'raison': raison,
                   'parametres': params, 'effets': [], 'avertissements': warnings}
            if plan is not None:
                row['effets'] = catalogue.describe_effects(plan, values)
                for k, v, _s in plan['facts']:
                    values[k] = v
                applied.append((a['cle'], plan))
                chosen = label
            report.append(row)
    return applied, report


@transaction.atomic
def apply_to_building(building, bouquet, owner_sub='', owner_name=''):
    """Écrit l'amélioration générée du couple (bâtiment, bouquet) : (scenario, actions I5)."""
    ctx = context(building)
    applied, report = plan_bouquet(building, bouquet, ctx)
    s = (Scenario.objects.select_for_update().filter(building=building, bouquet=bouquet, genere=True)
         .order_by('pk').first())
    name = f'Potentiel « {bouquet.nom} »'[:150]
    if s is None:
        s = Scenario.objects.create(building=building, name=name, bouquet=bouquet, genere=True, author_sub=owner_sub or '',
                                    description=bouquet.description[:2000])
    else:
        s.facts.all().delete()
        s.shading_devices.all().delete()
        s.name = name
    src = f'amélioration « {s.name} »'[:300]
    surcharges, old_keys = {}, []
    for key, plan in applied:
        for k, v, _detail in plan['facts']:
            facts.record(building, k, v, 'declare', source=src, author_sub=owner_sub or '', author_name=owner_name or '',
                         scenario=s, refresh=False)
        for d in plan['devices']:
            # Intégration (lot 40) : protection enregistrée complète et validée par le catalogue I1 (secteur P).
            from ..engines import shading as _sh
            ShadingDevice.objects.create(building=building, scenario=s, kind=d['kind'], orientation=d['orientation'],
                                         params=_sh.validate_params(d['kind'], d.get('params') or {}),
                                         control=_sh.validate_control(d['kind'], d.get('control') or {}),
                                         label=catalogue.action_label(catalogue.BY_KEY[key], ctx['cfg'])[:150],
                                         author_sub=owner_sub or '')
        surcharges.update(plan['surcharges'])
        if plan.get('bundle'):
            old_keys.append(plan['bundle'])
    s.surcharges = surcharges
    # Anciennes clés appliquées : `scenarios.vigilance()` les lit (compatibilité du comparateur).
    s.weather = {**{k: v for k, v in (s.weather or {}).items() if k != 'bundles'}, 'bundles': sorted(old_keys)}
    s.save()
    return s, report
