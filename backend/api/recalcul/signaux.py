"""Signaux qui planifient le recalcul (secteur C). Importé par `ApiConfig.ready()`.

Déclencheurs (contrat I1) — un signal ne fait **que** planifier (`planification.planifier`, regroupé, aucun calcul,
aucun appel réseau) :
- `BuildingFact`, `ShadingDevice`, `GeometryEdit` (enregistré ou supprimé), sauf ceux d'une amélioration générée
  (`Scenario.genere` : écrits par le calcul d'un potentiel, ils ne relancent rien — pas de boucle) ;
- `Scenario` non généré (enregistré ou supprimé) ;
- `Building` : `envelope`, `environment`, `weather_dataset`, `georef_*` modifiés ; `pipeline_status` qui passe de
  « en attente / en cours » à « complet / incomplet » = fin de la chaîne (cause 'chaine') ;
- `Environment` modifié (en plus de la liste du contrat : obstacles retouchés ⇒ N2 des bâtiments reliés) ;
- `Bouquet` enregistré : tous les bâtiments concernés (du parc, ou tout le lab pour un bouquet du lab) ;
- `Portfolio` : réglages `orchestration` ou `parametrage` modifiés ⇒ `planifier_parc`.
Une suppression en cascade depuis un bâtiment, un parc ou une amélioration générée ne planifie rien.
Un `QuerySet.update()` n'émet aucun signal : ce que le code écrit ainsi (scores, statut intermédiaire de la chaîne,
météo par défaut A7) est couvert par la fin de chaîne, ou n'est pas une entrée des calculs.
"""

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from ..models import (Bouquet, Building, BuildingFact, Environment, GeometryEdit, Portfolio, Scenario,
                      ShadingDevice)
from . import planification as plan

BUILDING_FIELDS = ('envelope', 'environment_id', 'weather_dataset_id', 'georef_lat', 'georef_lon',
                   'georef_north_offset_deg', 'georef_ground_z', 'pipeline_status')
BUILDING_UPDATE_NAMES = {f[:-3] if f.endswith('_id') else f for f in BUILDING_FIELDS} | set(BUILDING_FIELDS)
PORTFOLIO_FIELDS = ('orchestration', 'parametrage')
FIN_DE_CHAINE = ('complet', 'incomplet')
CAUSES_BATIMENT = {'envelope': 'enveloppe', 'environment_id': 'environnement relié',
                   'weather_dataset_id': 'météo'}

_genere = {}   # scenario_id → genere (un scénario ne change jamais de nature) ; borné


def _scenario_genere(scenario_id):
    if scenario_id is None:
        return False
    if scenario_id not in _genere:
        if len(_genere) > 5000:
            _genere.clear()
        val = Scenario.objects.filter(pk=scenario_id).values_list('genere', flat=True).first()
        if val is None:
            return True      # scénario déjà supprimé : suppression en cascade, rien à planifier
        _genere[scenario_id] = bool(val)
    return _genere[scenario_id]


def _cascade(kwargs):
    origin = kwargs.get('origin')
    return isinstance(origin, (Building, Portfolio)) or (isinstance(origin, Scenario) and origin.genere)


def _planifier_id(building_id, cause):
    if building_id is not None:
        plan.planifier(Building(pk=building_id), cause)   # `planifier` relit en base ce dont il a besoin


# ── Faits, protections, géométrie ──────────────────────────────────────────

def _detail(sender, **kwargs):
    if not plan.actif():
        return
    inst = kwargs['instance']
    if kwargs.get('raw') or _cascade(kwargs) or _scenario_genere(inst.scenario_id):
        return
    if sender is BuildingFact:
        cause = f'donnée {inst.key}'
    elif sender is ShadingDevice:
        cause = 'protection solaire'
    else:
        cause = 'géométrie corrigée'
    _planifier_id(inst.building_id, cause)


for _m in (BuildingFact, ShadingDevice, GeometryEdit):
    post_save.connect(_detail, sender=_m, dispatch_uid=f'recalcul_save_{_m.__name__}')
    post_delete.connect(_detail, sender=_m, dispatch_uid=f'recalcul_delete_{_m.__name__}')


@receiver(post_save, sender=Scenario, dispatch_uid='recalcul_save_scenario')
@receiver(post_delete, sender=Scenario, dispatch_uid='recalcul_delete_scenario')
def _scenario(sender, instance, **kwargs):
    if not plan.actif() or kwargs.get('raw') or instance.genere or _cascade(kwargs):
        return
    _planifier_id(instance.building_id, 'amélioration modifiée')


# ── Bâtiment ───────────────────────────────────────────────────────────────

@receiver(pre_save, sender=Building, dispatch_uid='recalcul_pre_building')
def _building_avant(sender, instance, **kwargs):
    instance._recalcul_avant = None
    if not plan.actif() or kwargs.get('raw') or instance.pk is None:
        return
    uf = kwargs.get('update_fields')
    if uf is not None and not (set(uf) & BUILDING_UPDATE_NAMES):
        return
    instance._recalcul_avant = Building.objects.filter(pk=instance.pk).values(*BUILDING_FIELDS).first()


@receiver(post_save, sender=Building, dispatch_uid='recalcul_post_building')
def _building_apres(sender, instance, created, **kwargs):
    avant = getattr(instance, '_recalcul_avant', None)
    instance._recalcul_avant = None
    if created or not avant or not plan.actif():
        return
    changed = [f for f in BUILDING_FIELDS if getattr(instance, f) != avant[f]]
    if not changed:
        return
    if 'pipeline_status' in changed and avant['pipeline_status'] in plan.CHAINE_EN_COURS \
            and instance.pipeline_status in FIN_DE_CHAINE:
        plan.planifier(instance, plan.CHAINE)
        return
    changed = [f for f in changed if f != 'pipeline_status']
    if changed:
        plan.planifier(instance, CAUSES_BATIMENT.get(changed[0], 'position'))


@receiver(post_save, sender=Environment, dispatch_uid='recalcul_post_environment')
def _environment(sender, instance, created, **kwargs):
    if created or kwargs.get('raw') or not plan.actif():
        return
    # Un par un (au plus 200) : une retouche faite par le recalcul lui-même (environnement généré avant un N2) reste
    # une « suite du recalcul », sans boucle.
    for bid in Building.objects.filter(environment_id=instance.pk).order_by('pk').values_list('pk', flat=True)[:200]:
        _planifier_id(bid, 'environnement retouché')


# ── Bouquets, réglages du parc ─────────────────────────────────────────────

@receiver(post_save, sender=Bouquet, dispatch_uid='recalcul_post_bouquet')
def _bouquet(sender, instance, **kwargs):
    if kwargs.get('raw') or not plan.actif():
        return
    qs = Building.objects.all() if instance.portfolio_id is None else Building.objects.filter(
        portfolio_id=instance.portfolio_id)
    plan.planifier_batiments(qs, f'bouquet « {instance.nom} »'[:200])


@receiver(pre_save, sender=Portfolio, dispatch_uid='recalcul_pre_portfolio')
def _portfolio_avant(sender, instance, **kwargs):
    instance._recalcul_avant = None
    if not plan.actif() or kwargs.get('raw') or instance.pk is None:
        return
    uf = kwargs.get('update_fields')
    if uf is not None and not (set(uf) & set(PORTFOLIO_FIELDS)):
        return
    instance._recalcul_avant = Portfolio.objects.filter(pk=instance.pk).values(*PORTFOLIO_FIELDS).first()


@receiver(post_save, sender=Portfolio, dispatch_uid='recalcul_post_portfolio')
def _portfolio_apres(sender, instance, created, **kwargs):
    avant = getattr(instance, '_recalcul_avant', None)
    instance._recalcul_avant = None
    if created or not avant or not plan.actif():
        return
    if any((getattr(instance, f) or {}) != (avant[f] or {}) for f in PORTFOLIO_FIELDS):
        plan.planifier_parc(instance, 'réglages du parc')
