"""Fiche d'un bâtiment : faits, provenance, fiche effective (Lot 8, refonte — principes 2 et 3).

Un **fait** (`BuildingFact`) est une valeur d'une clé du catalogue ci-dessous, avec sa méthode
(`mesure > document > declare > infere > defaut`), sa source, sa confiance, son auteur et sa date.
Rien n'est jamais écrasé :
- une valeur de rang supérieur **prime** sans effacer l'autre ;
- une correction de l'utilisateur est une **surcharge** : la retirer (`retired_at`) rend la valeur
  automatique ;
- une réimportation automatique (nouveau millésime BDNB) remplace l'ancienne valeur automatique
  (`supersedes`) mais jamais une surcharge.

La **fiche effective** d'un bâtiment (ou d'une variante, Lot 18) = pour chaque clé, le fait actif
de plus haut rang (à rang égal, le plus récent), plus les surcharges de la variante.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .fingerprint import stable_hash

METHOD_RANK = {'mesure': 5, 'document': 4, 'declare': 3, 'infere': 2, 'defaut': 1}
USER_METHODS = ('mesure', 'document', 'declare')
SYSTEM_METHODS = ('infere', 'defaut')
DEFAULT_CONFIDENCE = {'mesure': 0.95, 'document': 0.85, 'declare': 0.7, 'infere': 0.4, 'defaut': 0.15}

ORIENTATIONS = ('N', 'E', 'S', 'O')

USAGES = {
    'maison': 'Maison individuelle', 'logement_collectif': 'Logement collectif', 'bureau': 'Bureaux',
    'enseignement': 'Enseignement', 'commerce': 'Commerce', 'sante': 'Santé', 'autre': 'Autre',
}
WALL_MATERIALS = {
    'pierre': 'Pierre', 'brique_pleine': 'Brique pleine', 'brique_creuse': 'Brique creuse',
    'parpaing': 'Parpaing (bloc béton)', 'beton': 'Béton banché', 'bois': 'Ossature bois',
    'pan_de_bois': 'Pan de bois', 'monomur': 'Monomur / béton cellulaire', 'autre': 'Autre',
}
INSULATION = {'aucune': 'Aucune', 'iti': 'Par l’intérieur (ITI)', 'ite': 'Par l’extérieur (ITE)',
              'repartie': 'Répartie (monomur)', 'inconnue': 'Inconnue'}
INERTIA = {'legere': 'Légère', 'moyenne': 'Moyenne', 'lourde': 'Lourde'}
GLAZING = {'simple': 'Simple vitrage', 'double': 'Double vitrage', 'double_fe': 'Double vitrage faible émissivité',
           'triple': 'Triple vitrage'}
VENTILATION = {'naturelle': 'Naturelle (défauts d’étanchéité, grilles)', 'vmc_sf_auto': 'VMC simple flux autoréglable',
               'vmc_sf_hygro': 'VMC simple flux hygroréglable', 'vmc_df': 'VMC double flux'}
FLOOR = {'terre_plein': 'Terre-plein', 'vide_sanitaire': 'Vide sanitaire', 'sous_sol': 'Sous-sol non chauffé',
         'local_chauffe': 'Local chauffé'}
GENERATORS = {
    'chaudiere_gaz': 'Chaudière gaz standard', 'chaudiere_gaz_cond': 'Chaudière gaz à condensation',
    'chaudiere_fioul': 'Chaudière fioul', 'pac_air_eau': 'Pompe à chaleur air/eau',
    'pac_air_air': 'Pompe à chaleur air/air', 'joule': 'Effet Joule (convecteurs)',
    'reseau_chaleur': 'Réseau de chaleur', 'bois': 'Chaudière / poêle bois',
}
EMITTERS = {'radiateurs_ht': 'Radiateurs haute température', 'radiateurs_bt': 'Radiateurs basse température',
            'plancher': 'Plancher chauffant', 'convecteurs': 'Convecteurs / panneaux', 'soufflage': 'Soufflage d’air'}
REGULATION = {'aucune': 'Aucune', 'centrale': 'Thermostat central', 'pieces': 'Robinets / thermostats par pièce',
              'programmable': 'Programmation et régulation par pièce'}
ECS = {'ballon_elec': 'Ballon électrique', 'thermodynamique': 'Chauffe-eau thermodynamique',
       'chaudiere': 'Par la chaudière', 'solaire': 'Solaire + appoint', 'instantane_gaz': 'Instantané gaz'}


@dataclass(frozen=True)
class Key:
    key: str
    label: str
    block: str
    kind: str  # number | int | enum | bool
    unit: str = ''
    min: float | None = None
    max: float | None = None
    choices: dict | None = None
    weight: float = 1.0      # importance pour la confiance et la complétude
    help: str = ''


def _k(*a, **kw):
    return Key(*a, **kw)


CATALOGUE = [
    # Identité
    _k('identite.usage', 'Usage', 'identite', 'enum', choices=USAGES, weight=2),
    _k('identite.annee_construction', 'Année de construction', 'identite', 'int', '', 1500, 2030, weight=3),
    _k('identite.surface_m2', 'Surface chauffée (habitable / utile)', 'identite', 'number', 'm²', 5, 200000, weight=3),
    _k('identite.niveaux', 'Nombre de niveaux', 'identite', 'int', '', 1, 80, weight=1),
    _k('identite.logements', 'Nombre de logements', 'identite', 'int', '', 0, 5000, weight=0.5),
    _k('identite.hauteur_m', 'Hauteur à l’égout', 'identite', 'number', 'm', 2, 300, weight=1),
    _k('identite.emprise_m2', 'Emprise au sol', 'identite', 'number', 'm²', 5, 100000, weight=1),
    _k('identite.mitoyennete', 'Part des murs mitoyens', 'identite', 'number', '', 0, 0.9, weight=2,
       help='Part de la surface de murs accolée à un bâtiment voisin chauffé (0 = isolé, 0,5 = moitié).'),
    # Enveloppe
    _k('mur.materiau', 'Matériau des murs', 'enveloppe', 'enum', choices=WALL_MATERIALS, weight=1),
    _k('mur.isolation', 'Isolation des murs', 'enveloppe', 'enum', choices=INSULATION, weight=2),
    _k('mur.isolation_epaisseur_m', 'Épaisseur d’isolant des murs', 'enveloppe', 'number', 'm', 0, 0.5, weight=1),
    _k('mur.u', 'U des murs', 'enveloppe', 'number', 'W/m²·K', 0.08, 4.0, weight=4),
    _k('toiture.u', 'U de la toiture / des combles', 'enveloppe', 'number', 'W/m²·K', 0.06, 5.0, weight=3),
    _k('toiture.type', 'Forme de toiture', 'enveloppe', 'enum',
       choices={'terrasse': 'Terrasse', '2pans': 'Deux pans', '4pans': 'Quatre pans'}, weight=0.3),
    _k('toiture.pente_deg', 'Pente de toiture', 'enveloppe', 'number', '°', 0, 70, weight=0.3),
    _k('plancher.type', 'Plancher bas', 'enveloppe', 'enum', choices=FLOOR, weight=0.5),
    _k('plancher.u', 'U du plancher bas', 'enveloppe', 'number', 'W/m²·K', 0.08, 4.0, weight=2),
    _k('enveloppe.inertie', 'Inertie', 'enveloppe', 'enum', choices=INERTIA, weight=1),
    _k('enveloppe.couleur_absorptance', 'Absorptance solaire des murs', 'enveloppe', 'number', '', 0.1, 0.98, weight=0.3),
    # Menuiseries
    *[_k(f'baies.ratio_{o}', f'Taux de vitrage façade {o}', 'menuiseries', 'number', '', 0, 0.9, weight=1.2)
      for o in ORIENTATIONS],
    _k('baies.vitrage', 'Type de vitrage', 'menuiseries', 'enum', choices=GLAZING, weight=1),
    _k('baies.uw', 'Uw des fenêtres', 'menuiseries', 'number', 'W/m²·K', 0.5, 6.5, weight=2),
    _k('baies.g', 'Facteur solaire g', 'menuiseries', 'number', '', 0.05, 0.9, weight=1),
    _k('baies.occultations', 'Occultations (volets)', 'menuiseries', 'bool', weight=0.5),
    # Ventilation
    _k('ventilation.type', 'Ventilation', 'ventilation', 'enum', choices=VENTILATION, weight=2),
    _k('ventilation.debit_vol_h', 'Renouvellement d’air hygiénique', 'ventilation', 'number', 'vol/h', 0.05, 5, weight=2),
    _k('ventilation.rendement_recup', 'Rendement de récupération (double flux)', 'ventilation', 'number', '', 0, 0.95, weight=1),
    _k('ventilation.n50', 'Perméabilité à l’air n50', 'ventilation', 'number', 'vol/h', 0.3, 20, weight=1.5),
    # Usage
    _k('usage.consigne_chauffage_c', 'Consigne de chauffage', 'usage', 'number', '°C', 12, 25, weight=2),
    _k('usage.consigne_froid_c', 'Consigne de refroidissement (vide = pas de climatisation)', 'usage', 'number', '°C', 22, 30, weight=0.5),
    _k('usage.apports_w_m2', 'Apports internes moyens', 'usage', 'number', 'W/m²', 0, 30, weight=1),
    _k('usage.occupants', 'Occupants', 'usage', 'int', '', 0, 10000, weight=0.5),
    # Systèmes (Lot 16)
    _k('systeme.generateur', 'Générateur de chauffage', 'systemes', 'enum', choices=GENERATORS, weight=2),
    _k('systeme.annee_generateur', 'Année du générateur', 'systemes', 'int', '', 1950, 2030, weight=0.5),
    _k('systeme.emetteurs', 'Émetteurs', 'systemes', 'enum', choices=EMITTERS, weight=0.5),
    _k('systeme.regulation', 'Régulation', 'systemes', 'enum', choices=REGULATION, weight=0.5),
    _k('systeme.rendement_generation', 'Rendement de génération (ou COP saisonnier)', 'systemes', 'number', '', 0.3, 6, weight=1),
    _k('systeme.ecs', 'Eau chaude sanitaire', 'systemes', 'enum', choices=ECS, weight=0.5),
    _k('dpe.etiquette', 'Étiquette DPE affichée', 'identite', 'enum',
       choices={c: c for c in 'ABCDEFG'}, weight=0.3),
    _k('dpe.conso_ep_kwh_m2', 'Consommation DPE (énergie primaire)', 'identite', 'number', 'kWh/m²·an', 0, 1500, weight=0.3),
]
BY_KEY = {k.key: k for k in CATALOGUE}
BLOCKS = [('identite', 'Identité'), ('enveloppe', 'Enveloppe'), ('menuiseries', 'Menuiseries'),
          ('ventilation', 'Ventilation'), ('usage', 'Usage'), ('systemes', 'Systèmes')]
# Clés du calcul : celles qui comptent pour la confiance et la complétude.
CORE_KEYS = [k.key for k in CATALOGUE if not k.key.startswith('dpe.')]


class FactError(ValueError):
    pass


def validate_value(key, value):
    """Valeur normalisée pour une clé du catalogue, ou FactError (message fixe, sans écho brut)."""
    spec = BY_KEY.get(key)
    if spec is None:
        raise FactError('Clé inconnue.')
    if value is None:
        if key == 'usage.consigne_froid_c':
            return None
        raise FactError(f'« {spec.label} » : valeur manquante.')
    if spec.kind in ('number', 'int'):
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise FactError(f'« {spec.label} » : nombre attendu.') from None
        if v != v or v in (float('inf'), float('-inf')):
            raise FactError(f'« {spec.label} » : nombre attendu.')
        if spec.kind == 'int':
            if abs(v - round(v)) > 1e-9:
                raise FactError(f'« {spec.label} » : nombre entier attendu.')
            v = int(round(v))
        if (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
            raise FactError(f'« {spec.label} » : hors de la plage plausible [{spec.min} ; {spec.max}] {spec.unit}.'.rstrip())
        return v
    if spec.kind == 'enum':
        if value not in (spec.choices or {}):
            raise FactError(f'« {spec.label} » : choix inconnu.')
        return value
    if spec.kind == 'bool':
        if not isinstance(value, bool):
            raise FactError(f'« {spec.label} » : oui ou non attendu.')
        return value
    raise FactError('Type de clé inconnu.')


def is_override(f):
    """Surcharge = valeur posée par une personne (auteur connu), par opposition à une valeur
    automatique (chaîne open data, archétype, calage), même de méthode « document »."""
    return bool(f.author_sub)


def _better(a, b):
    """a prime-t-il sur b ? Une surcharge prime sur toute valeur automatique (principe 3 : un DPE
    réimporté ne reprend pas la main sur une correction) ; puis rang de méthode, puis date."""
    if is_override(a) != is_override(b):
        return is_override(a)
    ra, rb = METHOD_RANK[a.method], METHOD_RANK[b.method]
    if ra != rb:
        return ra > rb
    return (a.created_at, a.pk or 0) > (b.created_at, b.pk or 0)


def active_facts(building, scenario=None):
    """Faits actifs (non retirés, non remplacés) du bâtiment — et ceux de la variante si donnée."""
    from .models import BuildingFact
    qs = BuildingFact.objects.filter(building=building, retired_at__isnull=True, superseded_by__isnull=True)
    if scenario is None:
        qs = qs.filter(scenario__isnull=True)
    else:
        from django.db.models import Q
        qs = qs.filter(Q(scenario__isnull=True) | Q(scenario=scenario))
    return list(qs.order_by('created_at', 'pk'))


def effective(building, scenario=None):
    """{clé: fait retenu}. Une surcharge de variante prime sur la base, quel que soit son rang."""
    best = {}
    for f in active_facts(building, scenario):
        cur = best.get(f.key)
        if cur is None:
            best[f.key] = f
        elif scenario is not None and (f.scenario_id is not None) != (cur.scenario_id is not None):
            if f.scenario_id is not None:
                best[f.key] = f
        elif _better(f, cur):
            best[f.key] = f
    return best


def effective_values(building, scenario=None):
    return {k: f.value for k, f in effective(building, scenario).items()}


def fiche_hash(building, scenario=None):
    return stable_hash(sorted(effective_values(building, scenario).items()))


def scores(eff):
    """(confiance, complétude) de la fiche effective, pondérées par l'importance des clés.

    confiance = moyenne pondérée des confiances (une clé absente compte 0) ;
    complétude = part pondérée des clés renseignées autrement que par un défaut.
    """
    total = sum(BY_KEY[k].weight for k in CORE_KEYS)
    conf = comp = 0.0
    for k in CORE_KEYS:
        f = eff.get(k)
        if f is None:
            continue
        w = BY_KEY[k].weight
        conf += w * float(f.confidence)
        if f.method != 'defaut':
            comp += w
    return round(conf / total, 3), round(comp / total, 3)


def refresh_scores(building):
    conf, comp = scores(effective(building))
    building.__class__.objects.filter(pk=building.pk).update(confidence=conf, completeness=comp)
    building.confidence, building.completeness = conf, comp
    return conf, comp


@transaction.atomic
def record(building, key, value, method, *, source='', confidence=None, author_sub='', author_name='',
           unit=None, scenario=None, extract=None, refresh=True):
    """Enregistre un fait. Une valeur automatique (infere/defaut) remplace la précédente valeur
    automatique de même méthode (`superseded_by`) ; une valeur de l'utilisateur ne remplace que
    sa propre valeur précédente de même méthode — jamais une valeur automatique (elle prime)."""
    from .models import BuildingFact
    if method not in METHOD_RANK:
        raise FactError('Méthode inconnue.')
    value = validate_value(key, value)
    spec = BY_KEY[key]
    conf = DEFAULT_CONFIDENCE[method] if confidence is None else max(0.0, min(1.0, float(confidence)))
    same = BuildingFact.objects.filter(building=building, key=key, method=method, scenario=scenario,
                                       author_sub=author_sub or '', retired_at__isnull=True,
                                       superseded_by__isnull=True).order_by('-created_at').first()
    if same is not None and same.value == value and same.source == (source or '')[:300]:
        return same  # rejouer la chaîne ne multiplie pas les faits identiques
    fact = BuildingFact.objects.create(
        building=building, key=key, value=value, unit=spec.unit if unit is None else unit, method=method,
        source=(source or '')[:300], confidence=conf, author_sub=author_sub or '', author_name=(author_name or '')[:150],
        scenario=scenario, extract=extract or {},
    )
    prev = BuildingFact.objects.filter(building=building, key=key, method=method, scenario=scenario,
                                       retired_at__isnull=True, superseded_by__isnull=True).exclude(pk=fact.pk)
    if method in USER_METHODS:
        prev = prev.filter(author_sub=author_sub or '')
    prev.update(superseded_by=fact)
    if refresh and scenario is None:
        refresh_scores(building)
    return fact


@transaction.atomic
def retire(fact, by_sub=''):
    """« Revenir à l'automatique » : la surcharge est retirée (gardée dans l'historique)."""
    if fact.method not in USER_METHODS:
        raise FactError('Une valeur automatique ne se retire pas : corrigez-la par une valeur déclarée.')
    fact.retired_at = timezone.now()
    fact.retired_by = by_sub or ''
    fact.save(update_fields=['retired_at', 'retired_by'])
    if fact.scenario_id is None:
        refresh_scores(fact.building)
    return fact


def fact_dict(f, viewer_sub=''):
    return {
        'id': f.pk, 'key': f.key, 'value': f.value, 'unit': f.unit, 'method': f.method, 'source': f.source,
        'confidence': f.confidence, 'created_at': f.created_at, 'scenario': f.scenario_id,
        'author': ('vous' if viewer_sub and f.author_sub == viewer_sub else (f.author_name or ''))
        if f.method in USER_METHODS else '',
        'mine': bool(viewer_sub) and f.author_sub == viewer_sub,
        'extract': f.extract or {},
    }


def catalogue_json():
    return [{'key': k.key, 'label': k.label, 'block': k.block, 'kind': k.kind, 'unit': k.unit, 'min': k.min,
             'max': k.max, 'choices': k.choices, 'weight': k.weight, 'help': k.help} for k in CATALOGUE]
