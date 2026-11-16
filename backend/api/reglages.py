"""Réglages d'un parc (chantier bt-navigation, contrat § 2.3) : orchestration, paramétrage, rendu.

Chaque section est un **schéma** (liste d'entrées à clés « à plat ») avec ses **défauts** — les
constantes actuelles du code, lues ici, jamais recopiées — et les **surcharges** du parc
(`Portfolio.orchestration|parametrage|rendu`). Valeurs effectives = défauts ⊕ surcharges.

`applique` dit honnêtement si le réglage est pris en compte par le serveur : une entrée montrée mais
non branchée porte `applique: False`.

Un bâtiment **hors parc** ne lit aucun réglage (`for_building` rend `{}`) : le code appelant retombe
sur ses constantes, donc sur exactement le comportement d'avant.
"""

import math

from . import archetypes, facts, questionnaire

SECTIONS = ('orchestration', 'parametrage', 'rendu')
ALL = ['N0', 'N1', 'N2']
TEXT_MAX = 150


class ReglageError(ValueError):
    pass


def _e(cle, libelle, type_, defaut, *, aide='', unite='', min=None, max=None, choix=None, groupe='',
       modeles=None, applique=True, nullable=False):
    return {'cle': cle, 'libelle': libelle, 'aide': aide, 'type': type_, 'unite': unite, 'min': min, 'max': max,
            'choix': choix, 'groupe': groupe, 'modeles': list(ALL if modeles is None else modeles),
            'applique': applique, 'nullable': nullable, '_defaut': defaut}


# ── Orchestration ───────────────────────────────────────────────────────────

def _orchestration():
    return [
        _e('identification.rnb_auto', 'Rechercher l’identifiant RNB le plus proche', 'bool', True,
           aide='Étape A1 : après le géocodage de l’adresse (BAN), le bâtiment RNB le plus proche du point est retenu. '
                'Désactivé : seuls les bâtiments importés avec leur identifiant RNB reçoivent la fiche nationale.',
           groupe='Identification'),
        _e('bdnb.actif', 'Fiche nationale BDNB', 'bool', True,
           aide='Étape A2 : année, usage, niveaux, matériau des murs et DPE représentatif (CSTB, Licence Ouverte 2.0). '
                'Désactivée : l’étape est sautée et notée dans le compte rendu.', groupe='Fiche nationale'),
        _e('bdtopo.actif', 'Géométrie BD TOPO', 'bool', True,
           aide='Étape A3 : emprise, hauteur, mitoyenneté, enveloppe simplifiée et horizon par façade (IGN). '
                'Désactivée : l’étape est sautée et notée dans le compte rendu.', groupe='Géométrie'),
        _e('bdtopo.rayon_voisins_m', 'Rayon de recherche des voisins', 'int', 60, unite='m', min=20, max=150,
           aide='Bâtiments BD TOPO lus autour du point : mitoyenneté, horizon (voisins à moins de 80 m) et obstacles du '
                'calcul détaillé.', groupe='Géométrie'),
        _e('compositions.source', 'Compositions par défaut', 'enum', 'tabula',
           choix={'tabula': 'Archétypes TABULA-FR par période (état courant)'},
           aide='Étape A5 : valeurs par défaut (U des parois, Uw, n50, ventilation, rendement) sous toute donnée meilleure. '
                'Les valeurs par période se règlent dans Paramétrage.', groupe='Compositions'),
        _e('meteo.type', 'Météo par défaut des calculs automatiques', 'enum', 'tmy',
           choix={'tmy': 'Année type du site (PVGIS)', 'reel': 'Année réelle (réanalyse Open-Meteo)'},
           aide='Année réelle : un jeu météo de l’année choisie est construit et relié au bâtiment s’il n’en a pas déjà un.',
           groupe='Météo'),
        _e('meteo.annee', 'Année réelle', 'int', None, min=1979, max=2100, nullable=True,
           aide='Vide : la dernière année complète.', groupe='Météo'),
        _e('calcul.n0', 'Calcul mensuel (N0) automatique', 'bool', True, modeles=['N0'],
           aide='Lancé après la chaîne automatique.', groupe='Calcul automatique'),
        _e('calcul.n1', 'Calcul horaire simplifié (N1) automatique', 'bool', True, modeles=['N1'],
           aide='Lancé après la chaîne automatique, avec sa fourchette.', groupe='Calcul automatique'),
        _e('environnement.mode', 'Environnement observé (LiDAR HD × BD TOPO)', 'enum', 'chaine',
           choix={'manuel': 'À la demande (onglet Bâtiment)',
                  'avant_n2': 'Avant chaque calcul détaillé, s’il manque',
                  'chaine': 'Pendant la récupération automatique, pour chaque bâtiment'},
           modeles=['N0', 'N1', 'N2'],
           aide='Bâtiments, arbres et terrain reconstruits depuis le LiDAR HD et la BD TOPO, albédo du sol relevé sur '
                'les orthophotos. Relié au bâtiment, il donne sa géométrie croisée BD TOPO × LiDAR (emprise recalée, '
                'hauteur à l’égout mesurée, toiture, voisins réels : la BD TOPO seule se trompe souvent de plusieurs '
                'mètres sur la hauteur), l’horizon par façade de N0/N1, et les obstacles et la réflexion du sol du '
                'calcul détaillé. Opération lourde : de quelques secondes à plusieurs minutes par bâtiment. '
                '« Pendant la récupération automatique » (par défaut) l’ajoute à la chaîne de chaque bâtiment (étape '
                'A6, avant N0/N1) et couvre aussi le calcul détaillé ; « avant chaque calcul détaillé » le génère au '
                'début d’un N2 ; « à la demande » garde la géométrie BD TOPO seule tant qu’aucun environnement n’est '
                'relié. Un environnement déjà relié (éventuellement retouché) est toujours gardé, et un échec '
                'n’arrête rien : le calcul reprend la géométrie et les voisins BD TOPO.',
           groupe='Environnement'),
        _e('environnement.rayon_m', 'Rayon de l’environnement', 'int', 150, unite='m', min=30, max=250,
           aide='Demi-côté de la zone reconstruite autour du bâtiment (150 m, comme dans l’outil Environnement). '
                'Le temps de calcul croît comme le carré du rayon.', groupe='Environnement'),
        _e('environnement.vegetation', 'Arbres et massifs', 'bool', True,
           aide='Houppiers relevés par le LiDAR, essence (conifère ou feuillu) par CoSIA.', groupe='Environnement'),
        _e('environnement.terrain', 'Relief', 'bool', True,
           aide='Maillage du terrain : masque d’un relief amont et sol de la réflexion.', groupe='Environnement'),
        _e('facades.mode', 'Analyse des façades (Panoramax)', 'enum', 'manuel', choix={'manuel': 'À la demande'},
           modeles=['N2'], aide='Seul mode disponible : l’analyse se lance depuis l’outil de maillage.', groupe='Façades'),
    ]


# ── Paramétrage ─────────────────────────────────────────────────────────────

BUNDLE_THICKNESS = {  # bouquet: (réponse du questionnaire portant l'épaisseur, défaut cm, max cm)
    'ite': ('murs_epaisseur', 14, 40), 'iti': ('murs_epaisseur', 10, 40), 'combles': ('toiture', 30, 60),
}
BUNDLE_LABEL_TEMPLATE = {
    'ite': 'Isolation des murs par l’extérieur ({} cm)', 'iti': 'Isolation des murs par l’intérieur ({} cm)',
    'combles': 'Isolation des combles ou de la toiture ({} cm)',
}
WINDOW_LABELS = {'simple': 'Simple vitrage', 'double_ancien': 'Double vitrage d’avant 2000',
                 'double_recent': 'Double vitrage récent', 'triple': 'Triple vitrage'}


def _fmt_cm(v):
    return f'{v:g}'.replace('.', ',')


def _parametrage():
    from . import scenarios
    out = [
        _e('isolants.lambda_mur', 'Conductivité de l’isolant des murs', 'number', questionnaire.LAMBDA_INSULATION,
           unite='W/m·K', min=0.015, max=0.07, groupe='Isolants',
           aide='Sert à traduire une épaisseur d’isolant des murs en U (questionnaire et améliorations).'),
        _e('isolants.lambda_toiture', 'Conductivité de l’isolant de toiture', 'number', questionnaire.LAMBDA_ROOF,
           unite='W/m·K', min=0.015, max=0.07, groupe='Isolants',
           aide='Sert à traduire une épaisseur d’isolant en combles ou toiture en U.'),
        _e('isolants.majoration_iti_pct', 'Majoration du U pour une isolation intérieure', 'number',
           questionnaire.ITI_MAJORATION_PCT, unite='%', min=0, max=50, groupe='Isolants',
           aide='Ponts thermiques de plancher et de refend d’une isolation par l’intérieur.'),
    ]
    for key, b in scenarios.BUNDLES.items():
        out.append(_e(f'bouquets.{key}.actif', b['label'], 'bool', True, groupe='Améliorations',
                      aide='Désactivé : l’amélioration n’est plus proposée ni appliquée pour les bâtiments du parc.'))
        if key in BUNDLE_THICKNESS:
            _q, _d, mx = BUNDLE_THICKNESS[key]
            out.append(_e(f'bouquets.{key}.epaisseur_cm', f'{b["label"].split(" (")[0]} : épaisseur d’isolant',
                          'number', b['answers'][_q], unite='cm', min=1, max=mx, groupe='Améliorations'))
    for opt, (_vit, uw, g) in questionnaire.WINDOWS.items():
        out.append(_e(f'menuiseries.{opt}.uw', f'{WINDOW_LABELS[opt]} : Uw', 'number', uw, unite='W/m²·K',
                      min=0.5, max=6.5, groupe='Menuiseries',
                      aide='Valeur posée par la réponse « Quelles fenêtres ? » et par les améliorations de menuiseries.'))
        out.append(_e(f'menuiseries.{opt}.g', f'{WINDOW_LABELS[opt]} : facteur solaire g', 'number', g,
                      min=0.05, max=0.9, groupe='Menuiseries'))
    for row, slug in zip(archetypes.PERIODS, archetypes.PERIOD_SLUGS):
        _end, label, u_mur, u_toit, u_pb, uw, n50, _inertie = row
        grp = f'Archétype {label}'
        aide = 'Valeur par défaut (étape A5 de la chaîne, et après une réponse sur l’année ou l’usage).'
        out += [
            _e(f'archetypes.{slug}.mur_u', 'U des murs', 'number', u_mur, unite='W/m²·K', min=0.08, max=4.0, groupe=grp, aide=aide),
            _e(f'archetypes.{slug}.toiture_u', 'U de la toiture', 'number', u_toit, unite='W/m²·K', min=0.06, max=5.0, groupe=grp),
            _e(f'archetypes.{slug}.plancher_u', 'U du plancher bas', 'number', u_pb, unite='W/m²·K', min=0.08, max=4.0, groupe=grp),
            _e(f'archetypes.{slug}.baies_uw', 'Uw des fenêtres', 'number', uw, unite='W/m²·K', min=0.5, max=6.5, groupe=grp),
            _e(f'archetypes.{slug}.n50', 'Perméabilité à l’air n50', 'number', n50, unite='vol/h', min=0.3, max=20, groupe=grp),
        ]
    for vent, (debit, recup) in archetypes.VENTILATION_DEFAULT.items():
        out += [
            _e(f'ventilation.{vent}.debit', f'{facts.VENTILATION[vent]} : renouvellement d’air', 'number', debit,
               unite='vol/h', min=0.05, max=5, groupe='Ventilation'),
            _e(f'ventilation.{vent}.recup', f'{facts.VENTILATION[vent]} : rendement de récupération', 'number', recup,
               min=0, max=0.95, groupe='Ventilation'),
        ]
    for gen, eta in archetypes.GENERATOR_EFFICIENCY.items():
        out.append(_e(f'rendements.{gen}', facts.GENERATORS[gen], 'number', eta, min=0.3, max=6, groupe='Rendements',
                      aide='Rendement saisonnier de génération (COP saisonnier pour une pompe à chaleur).'))
    return out


# ── Rendu ───────────────────────────────────────────────────────────────────

PARC_COLUMNS = {
    'heating_kwh_m2': 'Besoin de chauffage (kWh/m²·an)', 'heating_kwh': 'Besoin de chauffage (kWh/an)',
    'heating_kw_max': 'Puissance maximale (kW)', 'hours_over_28': 'Heures au-dessus de 28 °C',
    'final_kwh_m2': 'Énergie finale (kWh/m²·an)', 'primary_kwh_m2': 'Énergie primaire (kWh/m²·an)',
    'confidence': 'Confiance', 'completeness': 'Complétude', 'priority': 'Priorité',
    'surface_m2': 'Surface', 'annee': 'Année', 'usage': 'Usage', 'dpe': 'DPE affiché',
    'model_label': 'Étiquette du modèle', 'approfondissement': 'Approfondissement', 'status': 'État',
}
PARC_INDICATORS = {k: PARC_COLUMNS[k] for k in ('heating_kwh_m2', 'heating_kwh', 'heating_kw_max', 'hours_over_28',
                                               'final_kwh_m2', 'primary_kwh_m2', 'confidence', 'completeness',
                                               'priority')}
SORTS = {'name': 'Nom', **PARC_INDICATORS}
BUILDING_SECTIONS = {'identite': 'Identité', 'geometrie': 'Géométrie', 'enveloppe': 'Enveloppe',
                     'systemes': 'Systèmes', 'meteo': 'Météo', 'environnement': 'Environnement',
                     'resultats': 'Résultats', 'ameliorations': 'Améliorations', 'sources': 'Sources'}


def _rendu():
    aide = 'Mise en page de l’onglet Rendu : enregistrée ici, appliquée par l’affichage.'
    return [
        _e('parc.titre', 'Titre du rendu de parc', 'text', '', groupe='Parc', modeles=[], aide=aide),
        _e('parc.indicateur', 'Indicateur principal', 'enum', 'heating_kwh_m2', choix=PARC_INDICATORS, groupe='Parc',
           modeles=[], aide=aide),
        _e('parc.colonnes', 'Colonnes du tableau', 'list',
           ['usage', 'surface_m2', 'heating_kwh_m2', 'confidence', 'approfondissement', 'priority'],
           choix=PARC_COLUMNS, groupe='Parc', modeles=[], aide=aide),
        _e('parc.tri', 'Tri du tableau', 'enum', 'name', choix=SORTS, groupe='Parc', modeles=[], aide=aide),
        _e('batiment.titre', 'Titre de la fiche d’identité', 'text', '', groupe='Bâtiment', modeles=[], aide=aide),
        _e('batiment.sections', 'Sections de la fiche d’identité', 'list', list(BUILDING_SECTIONS),
           choix=BUILDING_SECTIONS, groupe='Bâtiment', modeles=[], aide=aide),
        _e('batiment.niveau_prefere', 'Modèle mis en avant', 'enum', 'N1',
           choix={'N0': 'Mensuel', 'N1': 'Horaire simplifié', 'N2': 'Détaillé par paroi'}, groupe='Bâtiment',
           modeles=[], aide=aide),
    ]


_BUILDERS = {'orchestration': _orchestration, 'parametrage': _parametrage, 'rendu': _rendu}
_CACHE = {}


def _entries(section):
    if section not in _CACHE:
        _CACHE[section] = _BUILDERS[section]()
    return _CACHE[section]


def schema(section):
    return [{k: v for k, v in e.items() if not k.startswith('_')} for e in _entries(section)]


def defaults(section):
    return {e['cle']: e['_defaut'] for e in _entries(section)}


def _check(e, v):
    lib = e['libelle']
    if v is None:
        if e['nullable']:
            return None
        raise ReglageError(f'« {lib} » : valeur manquante.')
    t = e['type']
    if t == 'bool':
        if not isinstance(v, bool):
            raise ReglageError(f'« {lib} » : oui ou non attendu.')
        return v
    if t in ('number', 'int'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ReglageError(f'« {lib} » : nombre attendu.')
        if t == 'int':
            if v != int(v):
                raise ReglageError(f'« {lib} » : nombre entier attendu.')
            v = int(v)
        else:
            v = float(v)
        if (e['min'] is not None and v < e['min']) or (e['max'] is not None and v > e['max']):
            raise ReglageError(f'« {lib} » : hors de la plage [{e["min"]} ; {e["max"]}] {e["unite"]}.'.rstrip())
        return v
    if t == 'enum':
        if not isinstance(v, str) or v not in e['choix']:
            raise ReglageError(f'« {lib} » : choix inconnu.')
        return v
    if t == 'text':
        if not isinstance(v, str) or len(v) > TEXT_MAX:
            raise ReglageError(f'« {lib} » : texte de {TEXT_MAX} caractères au plus attendu.')
        return ''.join(c for c in v if c.isprintable()).strip()
    if t == 'list':
        if not isinstance(v, list) or any(not isinstance(x, str) or x not in e['choix'] for x in v):
            raise ReglageError(f'« {lib} » : liste de choix connus attendue.')
        if len(set(v)) != len(v):
            raise ReglageError(f'« {lib} » : un choix figure deux fois.')
        return list(v)
    raise ReglageError('Type de réglage inconnu.')


def validate(section, surcharges):
    """Surcharges normalisées, ou ReglageError (message fixe : jamais l'entrée de l'utilisateur)."""
    if not isinstance(surcharges, dict):
        raise ReglageError('Surcharges attendues (objet clé → valeur).')
    by_key = {e['cle']: e for e in _entries(section)}
    out = {}
    for k, v in surcharges.items():
        e = by_key.get(k)
        if e is None:
            raise ReglageError('Réglage inconnu.')
        out[k] = _check(e, v)
    if section == 'orchestration' and out.get('meteo.annee') is not None:
        from datetime import date
        if out['meteo.annee'] >= date.today().year:
            raise ReglageError('« Année réelle » : une année complète passée est attendue.')
    return out


def surcharges_of(portfolio, section):
    raw = getattr(portfolio, section) or {}
    known = {e['cle'] for e in _entries(section)}
    return {k: v for k, v in raw.items() if k in known}   # une clé retirée du schéma est ignorée


def effective(portfolio, section):
    """Valeurs effectives (défauts ⊕ surcharges) d'une section pour un parc."""
    return {**defaults(section), **surcharges_of(portfolio, section)}


def for_building(building, section):
    """Réglages effectifs d'une section pour le parc d'un bâtiment, relus en base ; `{}` hors parc
    (le code appelant garde alors ses constantes, donc le comportement d'avant)."""
    if building is None or getattr(building, 'portfolio_id', None) is None:
        return {}
    from .models import Portfolio
    p = Portfolio.objects.filter(pk=building.portfolio_id).first()
    return effective(p, section) if p is not None else {}


def payload(portfolio, section, can_edit):
    return {'section': section, 'peut_modifier': bool(can_edit), 'schema': schema(section),
            'defauts': defaults(section), 'surcharges': surcharges_of(portfolio, section),
            'valeurs': effective(portfolio, section)}


# ── Améliorations (bouquets) vues depuis un parc ─────────────────────────────

def bundle_active(cfg, key):
    return bool(cfg.get(f'bouquets.{key}.actif', True)) if cfg else True


def bundle_label(key, bundle, cfg):
    if cfg and key in BUNDLE_THICKNESS and f'bouquets.{key}.epaisseur_cm' in cfg:
        return BUNDLE_LABEL_TEMPLATE[key].format(_fmt_cm(cfg[f'bouquets.{key}.epaisseur_cm']))
    return bundle['label']


def bundle_answers(key, bundle, cfg):
    answers = dict(bundle.get('answers') or {})
    if cfg and key in BUNDLE_THICKNESS and f'bouquets.{key}.epaisseur_cm' in cfg:
        answers[BUNDLE_THICKNESS[key][0]] = cfg[f'bouquets.{key}.epaisseur_cm']
    return answers
