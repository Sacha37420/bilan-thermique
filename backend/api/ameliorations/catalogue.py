"""Catalogue des actions d'amélioration (I7.a, contrat bt-potentiels v3, secteur A).

Une **action** est un levier élémentaire (isoler les murs par l'extérieur, planter deux arbres au sud-ouest…).
Chacune dit : ses paramètres (forme I1), ses **conditions** évaluées sur la fiche du bâtiment (bloquantes : l'action
est écartée avec sa raison ; non bloquantes : elle est appliquée, avec un avertissement), les indicateurs pour lesquels
elle est pertinente, ses **cibles** (deux éléments d'un bouquet ne visent jamais la même), et comment chaque niveau de
calcul la prend en compte (en mots).

Appliquer une action ne fait qu'écrire, sur une amélioration (`Scenario`), ce que permet I2 : des faits (a), des
surcharges non scalaires (b), des protections solaires (c). Jamais d'enveloppe, de `Building`, ni d'environnement.

Les dix anciennes clés de `scenarios.BUNDLES` sont des actions **à l'identique** : mêmes réponses au questionnaire
(`questionnaire.answers_to_facts`), mêmes réglages de parc (`reglages.bundle_*` : actif, épaisseur), mêmes protections.
"""

import math

from .. import facts, questionnaire, reglages, scenarios

VERSION = 'actions-1'   # entre dans l'empreinte des potentiels : à changer quand une application change

FAMILLES = [
    {'cle': 'isolation', 'libelle': 'Isolation'},
    {'cle': 'ventilation', 'libelle': 'Ventilation'},
    {'cle': 'protections', 'libelle': 'Protections solaires'},
    {'cle': 'vegetalisation', 'libelle': 'Végétalisation de la parcelle'},
    {'cle': 'toiture_albedo', 'libelle': 'Toiture et albédos'},
    {'cle': 'systemes', 'libelle': 'Chauffage et systèmes'},
]

ORIENTATIONS = {'S': 'Sud', 'O': 'Ouest', 'E': 'Est', 'SO': 'Sud et ouest', 'SEO': 'Sud, est et ouest',
                'all': 'Toutes les façades'}
ORIENT_SPLIT = {'S': ['S'], 'O': ['O'], 'E': ['E'], 'SO': ['S', 'O'], 'SEO': ['S', 'E', 'O'], 'all': ['all']}
ORIENT_AZ = {'N': 0.0, 'E': 90.0, 'S': 180.0, 'O': 270.0}   # azimut depuis le nord, sens horaire (x est, y nord)
WINDOW_LABELS = reglages.WINDOW_LABELS


def _p(cle, libelle, type_, defaut, *, unite='', mn=None, mx=None, pas=None, choix=None, groupe='', aide='',
       niveaux=('N0', 'N1', 'N2')):
    return {'cle': cle, 'libelle': libelle, 'unite': unite, 'type': type_, 'min': mn, 'max': mx, 'pas': pas,
            'defaut': defaut, 'choix': choix, 'groupe': groupe, 'aide': aide, 'niveaux': list(niveaux)}


def _kind_label(kind):
    """Libellé d'un modèle de protection, lu au catalogue I1 du secteur P (ancienne ou nouvelle forme)."""
    try:
        from ..engines import shading
        m = shading.KINDS.get(kind) or {}
        return m.get('libelle') or m.get('label') or kind
    except Exception:  # noqa: BLE001
        return kind


def _num(v, default=None):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _fmt(x, digits=2):
    if isinstance(x, float):
        s = f'{x:.{digits}f}'.rstrip('0').rstrip('.')
    else:
        s = str(x)
    return s.replace('.', ',')


# ── Conditions ──────────────────────────────────────────────────────────────
# Une condition : (code, libellé, bloquante, test). test(ctx, params, plan) → True (remplie), False (non remplie),
# None (information manquante : jamais bloquante, rendue comme avertissement « à vérifier »).

def _cond(code, libelle, bloquante, test, raison=None):
    return {'code': code, 'libelle': libelle, 'bloquante': bloquante, 'test': test, 'raison': raison or libelle}


def _new(plan, key):
    for k, v, _src in plan['facts']:
        if k == key:
            return v
    return None


def _gain(key, sens=-1, ecart=1e-3):
    """La valeur posée par l'action améliore la valeur actuelle (sens -1 : plus bas est mieux)."""
    def test(ctx, params, plan):
        old, new = _num(ctx['values'].get(key)), _num(_new(plan, key))
        if new is None:
            return True
        if old is None:
            return None
        return (new - old) * sens > ecart
    return test


def _enum_not_in(key, values):
    def test(ctx, params, plan):
        v = ctx['values'].get(key)
        if v is None:
            return None
        return v not in values
    return test


def _enum_in(key, values):
    def test(ctx, params, plan):
        v = ctx['values'].get(key)
        if v is None:
            return None
        return v in values
    return test


def _bundle_active(key):
    return lambda ctx, params, plan: reglages.bundle_active(ctx['cfg'], key)


def _windows_on(ctx, params, plan):
    """Au moins une façade visée porte des fenêtres (taux de vitrage de la fiche)."""
    oris = ORIENT_SPLIT.get(params.get('orientation'), [])
    if 'all' in oris:
        oris = ['N', 'E', 'S', 'O']
    vals = [_num(ctx['values'].get(f'baies.ratio_{o}')) for o in oris]
    if all(v is None for v in vals):
        return None
    return any((v or 0) > 0.005 for v in vals)


def _roof_flat(ctx, params, plan):
    t, pente = ctx['values'].get('toiture.type'), _num(ctx['values'].get('toiture.pente_deg'))
    if t == 'terrasse':
        return True
    if pente is not None:
        return pente <= 30
    if t is None:
        return None
    return False


def _old_before_1948(ctx, params, plan):
    y = _num(ctx['values'].get('identite.annee_construction'))
    return None if y is None else y >= 1948


def _not_breathing_wall(ctx, params, plan):
    m = ctx['values'].get('mur.materiau')
    return None if m is None else m not in ('pierre', 'pan_de_bois', 'brique_pleine')


def _footprint_known(ctx, params, plan):
    return bool(ctx.get('footprint'))


def _higher(key, param, default):
    def test(ctx, params, plan):
        cur = _num(ctx['values'].get(key), default)
        return params[param] > cur + 1e-6
    return test


def _lower(key, param, default=None):
    def test(ctx, params, plan):
        cur = _num(ctx['values'].get(key), default)
        if cur is None:
            return None
        return params[param] < cur - 1e-6
    return test


# ── Applications ────────────────────────────────────────────────────────────
# plan = {'facts': [(clé, valeur, source)], 'surcharges': {clé: valeur}, 'devices': [dict], 'effets': [str]}

def _plan():
    return {'facts': [], 'surcharges': {}, 'devices': [], 'effets': [], 'bundle': None}


def _from_bundle(key):
    """Ancien bouquet : réponses du questionnaire (épaisseur du parc ou du paramètre), ou protection — à l'identique
    de `scenarios.apply_bundles`."""
    def apply(ctx, params):
        bundle = scenarios.BUNDLES[key]
        plan = _plan()
        plan['bundle'] = key
        if 'answers' in bundle:
            answers = reglages.bundle_answers(key, bundle, ctx['cfg'])
            if key in reglages.BUNDLE_THICKNESS and 'epaisseur_cm' in params:
                answers[reglages.BUNDLE_THICKNESS[key][0]] = params['epaisseur_cm']
            plan['facts'] = list(questionnaire.answers_to_facts(answers, ctx['values'], ctx['cfg']))
        if 'device' in bundle:
            d = bundle['device']
            plan['devices'].append({'kind': d['kind'], 'orientation': d['orientation'], 'params': dict(d.get('params', {})),
                                    'control': dict(d.get('control', {}))})
        return plan
    return apply


def _floor(ctx, params):
    plan = _plan()
    u0 = _num(ctx['values'].get('plancher.u'))
    if u0 is not None:
        lam = (ctx['cfg'] or {}).get('isolants.lambda_mur', questionnaire.LAMBDA_INSULATION)
        e = params['epaisseur_cm'] / 100.0
        plan['facts'].append(('plancher.u', round(1.0 / (1.0 / u0 + e / lam), 3), f'{_fmt(params["epaisseur_cm"])} cm sous le plancher'))
    return plan


def _airtight(ctx, params):
    plan = _plan()
    plan['facts'].append(('ventilation.n50', float(params['n50_cible']), 'travaux d’étanchéité à l’air'))
    return plan


def _night(ctx, params):
    plan = _plan()
    plan['facts'] += [('ventilation.nocturne_vol_h', float(params['debit_vol_h']), 'surventilation nocturne'),
                      ('ventilation.nocturne_seuil_c', float(params['seuil_c']), 'surventilation nocturne')]
    return plan


def _fact_param(key, param, src):
    def apply(ctx, params):
        plan = _plan()
        plan['facts'].append((key, float(params[param]), src))
        return plan
    return apply


AUTO_CONTROL = {'mode': 'auto', 'irradiance_w_m2': 200, 't_ext_min_c': 20}


def _mobile(ctx, params):
    plan = _plan()
    for o in ORIENT_SPLIT[params['orientation']]:
        plan['devices'].append({'kind': params['modele'], 'orientation': o, 'params': {}, 'control': dict(AUTO_CONTROL)})
    return plan


def _fixed(ctx, params):
    plan = _plan()
    kind, depth = params['modele'], float(params['profondeur_m'])
    if kind == 'joues':
        p = {'fin_depth_m': depth, 'window_width_m': 1.2}
    else:
        p = {'depth_m': depth, 'gap_m': 0.2, 'window_height_m': 1.5}
    for o in ORIENT_SPLIT[params['orientation']]:
        plan['devices'].append({'kind': kind, 'orientation': o, 'params': dict(p), 'control': {}})
    return plan


def _glazing(ctx, params):
    plan = _plan()
    for o in ORIENT_SPLIT[params['orientation']]:
        plan['devices'].append({'kind': params['modele'], 'orientation': o, 'params': {'g': float(params['g'])}, 'control': {}})
    return plan


def _trees(ctx, params):
    """Arbres plantés devant les façades visées, à `distance_m` du nu de façade, répartis le long de la façade."""
    plan = _plan()
    fp = ctx.get('footprint') or []
    if fp:
        xs, ys = [p[0] for p in fp], [p[1] for p in fp]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half_x, half_y = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    else:   # emprise inconnue : carré équivalent à l'emprise (ou surface / niveaux), centré sur l'origine
        v = ctx['values']
        a = _num(v.get('identite.emprise_m2')) or ((_num(v.get('identite.surface_m2')) or 100.0) / max(_num(v.get('identite.niveaux')) or 1, 1))
        cx = cy = 0.0
        half_x = half_y = math.sqrt(max(a, 10.0)) / 2
    oris = [o for o in ORIENT_SPLIT[params['orientation']] if o != 'all'] or ['S', 'E', 'O', 'N']
    n, d, h, r = int(params['nombre']), float(params['distance_m']), float(params['hauteur_m']), float(params['rayon_houppier_m'])
    persistant = bool(params['persistant'])
    t_ete, t_hiver = (0.2, 0.25) if persistant else (0.2, 0.7)
    per = {o: [i for i in range(n) if i % len(oris) == j] for j, o in enumerate(oris)}   # répartition tour à tour
    slots = []
    for o, idx in per.items():
        slots += [(o, (k + 0.5) / len(idx) - 0.5) for k in range(len(idx))]   # régulièrement le long de la façade
    trees = []
    for o, frac in slots:
        if o in ('S', 'N'):
            x, y = cx + frac * 2 * half_x, cy + (-1 if o == 'S' else 1) * (half_y + d)
        else:
            x, y = cx + (1 if o == 'E' else -1) * (half_x + d), cy + frac * 2 * half_y
        trees.append({'x_m': round(x, 2), 'y_m': round(y, 2), 'hauteur_m': h, 'rayon_houppier_m': r,
                      'tronc_m': round(min(2.0, h / 3), 2), 'persistant': persistant,
                      'transmission_ete': t_ete, 'transmission_hiver': t_hiver})
    plan['surcharges']['arbres'] = trees
    plan['effets'].append(f'{n} arbre{"s" if n > 1 else ""} {"persistant" if persistant else "caduc"}{"s" if n > 1 else ""} '
                          f'de {_fmt(h)} m planté{"s" if n > 1 else ""} à {_fmt(d)} m des façades '
                          f'({ORIENTATIONS[params["orientation"]].lower()})')
    return plan


def _green_roof(ctx, params):
    plan = _plan()
    plan['facts'].append(('toiture.vegetalisee', True, 'toiture végétalisée'))
    plan['surcharges']['toiture_vegetalisee'] = {'substrat_m': float(params['substrat_m']), 'lai': float(params['lai']),
                                                 'part': float(params['part'])}
    plan['effets'].append(f'toiture végétalisée : substrat {_fmt(params["substrat_m"] * 100)} cm, indice foliaire '
                          f'{_fmt(params["lai"])}, {_fmt(round(params["part"] * 100))} % de la toiture')
    return plan


def _green_walls(ctx, params):
    plan = _plan()
    plan['facts'].append(('murs.vegetalises', True, 'murs végétalisés'))
    oris = ORIENT_SPLIT[params['orientation']]
    plan['surcharges']['murs_vegetalises'] = {'orientations': ['N', 'E', 'S', 'O'] if 'all' in oris else oris,
                                              'lai': float(params['lai']), 'part': float(params['part'])}
    plan['effets'].append(f'murs végétalisés ({ORIENTATIONS[params["orientation"]].lower()}) : '
                          f'{_fmt(round(params["part"] * 100))} % de la surface, indice foliaire {_fmt(params["lai"])}')
    return plan


HYPOTHESE_AIR = ('Hypothèse, non une mesure : la végétalisation de la parcelle rafraîchirait l’air au contact du bâtiment '
                 'de {d} °C l’après-midi en juin, juillet et août (ordre de grandeur relevé sous des arbres et près de '
                 'surfaces végétalisées ; il dépend fortement du site, de l’arrosage et du vent).')


def _air(ctx, params):
    plan = _plan()
    d = float(params['delta_c'])
    plan['surcharges']['rafraichissement_air'] = {'delta_c': d, 'mois': [6, 7, 8], 'heures': list(range(12, 19)),
                                                  'hypothese': HYPOTHESE_AIR.format(d=_fmt(d, 1))}
    plan['effets'].append(f'air extérieur abaissé de {_fmt(d, 1)} °C de 12 h à 18 h en juin–août (hypothèse)')
    return plan


# ── Textes d'application par niveau (table § 7 du contrat) ───────────────────

APP_U = {'N0': 'U des parois modifié dans le bilan mensuel.', 'N1': 'U des parois modifié dans la simulation horaire.',
         'N2': 'Couches dimensionnées sur le nouveau U (fiche) ; enveloppe experte : non appliquée, le calcul le dit.'}
APP_VENT = {'N0': 'Renouvellement d’air effectif modifié.', 'N1': 'Renouvellement d’air effectif modifié, heure par heure.',
            'N2': 'Débit du nœud d’air modifié.'}
APP_NIGHT = {'N0': 'Non appliquée : le bilan mensuel ne distingue pas la nuit.',
             'N1': 'Débit ajouté la nuit quand l’air extérieur est sous le seuil.',
             'N2': 'Débit ajouté la nuit quand l’air extérieur est sous le seuil (planning horaire).'}
APP_PROT = {'N0': 'Facteurs d’ombrage du catalogue des protections sur les baies visées.',
            'N1': 'Facteurs d’ombrage et pilotage (automatique) heure par heure.',
            'N2': 'Géométrie pour les protections fixes, profils par triangle pour les mobiles, g pour le vitrage.'}
APP_TREES = {'N0': 'Profil d’horizon des façades avec la transmission saisonnière du feuillage.',
             'N1': 'Profil d’horizon des façades avec la transmission saisonnière du feuillage.',
             'N2': 'Obstacles translucides ajoutés à l’environnement du calcul.'}
APP_ALBEDO = {'N0': 'Albédo du sol modifié.', 'N1': 'Albédo du sol modifié.', 'N2': 'Grille d’albédo du sol remplacée.'}
APP_ALPHA = {'N0': 'Absorptances de la toiture et des murs séparées.', 'N1': 'Absorptances de la toiture et des murs séparées.',
             'N2': 'Absorptance des couches extérieures modifiée.'}
APP_GREEN = {'N0': 'U et absorptance modifiés.', 'N1': 'U et absorptance modifiés, évapotranspiration en surface.',
             'N2': 'Couches et puits de chaleur en surface (évapotranspiration).'}
APP_AIR = {'N0': 'Température extérieure corrigée (mensuelle), présentée comme hypothèse.',
           'N1': 'Température extérieure corrigée aux heures visées, présentée comme hypothèse.',
           'N2': 'Température extérieure corrigée aux heures visées, présentée comme hypothèse.'}
APP_SYS = {'N0': 'Rendement de génération modifié (consommations).', 'N1': 'Rendement de génération modifié (consommations).',
           'N2': 'Rendement de génération modifié (consommations).'}

ORIENT_PARAM = lambda d='SO': _p('orientation', 'Façades visées', 'enum', d, choix=ORIENTATIONS, groupe='Position')  # noqa: E731
MOBILES = ('volet_roulant', 'volet_battant', 'persienne', 'store_ext', 'store_int', 'bso')
FIXES = ('casquette', 'debord_toit', 'joues', 'loggia')


def _thickness_param(key):
    _q, d, mx = reglages.BUNDLE_THICKNESS[key]
    return _p('epaisseur_cm', 'Épaisseur d’isolant', 'number', d, unite='cm', mn=1, mx=mx, pas=1, groupe='Isolant')


def _old_vig(key):
    return list(scenarios.BUNDLES[key]['vigilance'])


def _a(cle, famille, libelle, description, indicateurs, parametres, conditions, effets, cibles, application, vigilance,
       apply, old=False):
    return {'cle': cle, 'famille': famille, 'libelle': libelle, 'description': description,
            'indicateurs_pertinents': list(indicateurs), 'parametres': parametres, 'conditions': conditions,
            'effets': effets, 'cibles': cibles, 'application': application, 'vigilance': vigilance,
            '_apply': apply, '_old': old}


ACTIONS = [
    # ── Isolation ──
    _a('ite', 'isolation', scenarios.BUNDLES['ite']['label'],
       'Isolant posé sur la face extérieure des murs, sous un enduit ou un bardage.', ['energie', 'confort'],
       [_thickness_param('ite')],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('ite'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('murs_pas_deja_ite', 'Murs pas déjà isolés par l’extérieur', True, _enum_not_in('mur.isolation', ['ite']),
              'Murs déjà isolés par l’extérieur.'),
        _cond('gain_u_murs', 'Le U des murs baisse', True, _gain('mur.u'), 'Le U des murs n’en serait pas amélioré.'),
        _cond('bati_recent', 'Bâti postérieur à 1948', False, _old_before_1948,
              'Bâti d’avant 1948 : aspect des façades et matériaux anciens, avis d’un architecte conseillé.')],
       'Faits de l’amélioration : isolation des murs (ITE), épaisseur, U des murs déduit de l’épaisseur et de λ.',
       ['mur.u', 'mur.isolation'], APP_U, _old_vig('ite'), _from_bundle('ite'), old=True),
    _a('iti', 'isolation', scenarios.BUNDLES['iti']['label'],
       'Isolant posé sur la face intérieure des murs, derrière un doublage.', ['energie'],
       [_thickness_param('iti')],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('iti'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('gain_u_murs', 'Le U des murs baisse', True, _gain('mur.u'), 'Le U des murs n’en serait pas amélioré.'),
        _cond('mur_non_respirant', 'Murs ni en pierre, ni en pan de bois, ni en brique pleine', False, _not_breathing_wall,
              'Murs anciens perspirants : risque hygrothermique, matériaux et pare-vapeur adaptés.')],
       'Faits de l’amélioration : isolation des murs (ITI), épaisseur, U des murs déduit (majoré des ponts thermiques).',
       ['mur.u', 'mur.isolation'], APP_U, _old_vig('iti'), _from_bundle('iti'), old=True),
    _a('plancher_bas', 'isolation', 'Isolation du plancher bas (par-dessous)',
       'Isolant fixé en sous-face du plancher, depuis le vide sanitaire ou le sous-sol.', ['energie'],
       [_p('epaisseur_cm', 'Épaisseur d’isolant', 'number', 10, unite='cm', mn=1, mx=30, pas=1, groupe='Isolant')],
       [_cond('plancher_accessible', 'Plancher sur vide sanitaire ou sous-sol', True,
              _enum_in('plancher.type', ['vide_sanitaire', 'sous_sol']),
              'Plancher sur terre-plein ou sur local chauffé : pas d’isolation par-dessous.'),
        _cond('u_plancher_connu', 'U du plancher bas connu', True,
              lambda ctx, p, plan: _num(ctx['values'].get('plancher.u')) is not None, 'U du plancher bas inconnu.'),
        _cond('gain_u_plancher', 'Le U du plancher baisse', True, _gain('plancher.u'), 'Le U du plancher n’en serait pas amélioré.')],
       'Fait de l’amélioration : U du plancher bas = 1 / (1/U actuel + e/λ).',
       ['plancher.u'], APP_U, ['Hauteur libre du vide sanitaire ; traiter les réseaux qui le traversent.'], _floor),
    _a('fenetres', 'isolation', scenarios.BUNDLES['fenetres']['label'],
       'Remplacement des menuiseries par du double vitrage à faible émissivité.', ['energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('fenetres'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('gain_uw', 'Le Uw des fenêtres baisse', True, _gain('baies.uw'), 'Les fenêtres actuelles sont au moins aussi performantes.')],
       'Faits de l’amélioration : type de vitrage, Uw et facteur solaire g.',
       ['baies.uw', 'baies.g', 'baies.vitrage'], APP_U, _old_vig('fenetres'), _from_bundle('fenetres'), old=True),
    _a('triple', 'isolation', scenarios.BUNDLES['triple']['label'],
       'Remplacement des menuiseries par du triple vitrage.', ['energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('triple'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('gain_uw', 'Le Uw des fenêtres baisse', True, _gain('baies.uw'), 'Les fenêtres actuelles sont au moins aussi performantes.')],
       'Faits de l’amélioration : type de vitrage, Uw et facteur solaire g.',
       ['baies.uw', 'baies.g', 'baies.vitrage'], APP_U, _old_vig('triple'), _from_bundle('triple'), old=True),
    # ── Ventilation ──
    _a('vmc_hygro', 'ventilation', scenarios.BUNDLES['vmc_hygro']['label'],
       'Extraction mécanique dont le débit suit l’humidité des pièces.', ['energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('vmc_hygro'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('ventilation_moins_bonne', 'Ventilation actuelle ni hygroréglable ni double flux', True,
              _enum_not_in('ventilation.type', ['vmc_sf_hygro', 'vmc_df']), 'Ventilation déjà hygroréglable ou double flux.')],
       'Faits de l’amélioration : type de ventilation, renouvellement d’air, rendement de récupération.',
       ['ventilation.type', 'ventilation.debit_vol_h', 'ventilation.rendement_recup'], APP_VENT,
       _old_vig('vmc_hygro'), _from_bundle('vmc_hygro'), old=True),
    _a('vmc_df', 'ventilation', scenarios.BUNDLES['vmc_df']['label'],
       'Ventilation mécanique avec récupération de chaleur sur l’air extrait, et étanchéité à l’air traitée.', ['energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('vmc_df'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('pas_deja_df', 'Pas déjà en double flux', True, _enum_not_in('ventilation.type', ['vmc_df']), 'Déjà en double flux.')],
       'Faits de l’amélioration : type de ventilation, renouvellement d’air, rendement de récupération, perméabilité n50.',
       ['ventilation.type', 'ventilation.debit_vol_h', 'ventilation.rendement_recup', 'ventilation.n50'], APP_VENT,
       _old_vig('vmc_df'), _from_bundle('vmc_df'), old=True),
    _a('etancheite', 'ventilation', 'Étanchéité à l’air', 'Traitement des fuites d’air (menuiseries, traversées, '
       'jonctions) jusqu’à une perméabilité cible.', ['energie'],
       [_p('n50_cible', 'Perméabilité visée n50', 'number', 3.0, unite='vol/h', mn=0.6, mx=6, pas=0.1, groupe='Étanchéité')],
       [_cond('gain_n50', 'Le bâtiment fuit davantage que la cible', True, _gain('ventilation.n50'),
              'Le bâtiment est déjà au moins aussi étanche que la cible.')],
       'Fait de l’amélioration : perméabilité à l’air n50.', ['ventilation.n50'], APP_VENT,
       ['Garder des entrées d’air : une ventilation suffisante reste nécessaire.', 'Test d’étanchéité recommandé.'], _airtight),
    _a('surventilation_nocturne', 'ventilation', 'Surventilation nocturne en été',
       'Ouvrir ou ventiler fortement la nuit quand l’air extérieur est frais, pour décharger la chaleur des murs.', ['confort'],
       [_p('debit_vol_h', 'Renouvellement d’air la nuit', 'number', 3.0, unite='vol/h', mn=0.5, mx=10, pas=0.5,
           groupe='Ventilation', niveaux=('N1', 'N2')),
        _p('seuil_c', 'Seuil de température extérieure', 'number', 22.0, unite='°C', mn=15, mx=28, pas=0.5,
           groupe='Ventilation', niveaux=('N1', 'N2'))],
       [_cond('pas_deja', 'Surventilation actuelle plus faible', True, _higher('ventilation.nocturne_vol_h', 'debit_vol_h', 0.0),
              'Surventilation nocturne déjà au moins aussi forte.')],
       'Faits de l’amélioration : débit et seuil de la surventilation nocturne.',
       ['ventilation.nocturne_vol_h', 'ventilation.nocturne_seuil_c'], APP_NIGHT,
       ['Ouvrants sécurisés la nuit (effraction, pluie, bruit, insectes).', 'Sans effet sur les besoins de chauffage.'], _night),
    # ── Protections solaires ──
    _a('store_sud', 'protections', scenarios.BUNDLES['store_sud']['label'],
       'Store extérieur sur les baies sud, abaissé automatiquement au soleil quand il fait chaud.', ['confort'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('store_sud'),
              'Amélioration désactivée dans les réglages du parc.')],
       'Protection de l’amélioration : store extérieur, façade sud, pilotage automatique.',
       ['protections.mobiles'], APP_PROT, _old_vig('store_sud'), _from_bundle('store_sud'), old=True),
    _a('casquette_sud', 'protections', scenarios.BUNDLES['casquette_sud']['label'],
       'Auvent horizontal de 80 cm au-dessus des baies sud.', ['confort', 'energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('casquette_sud'),
              'Amélioration désactivée dans les réglages du parc.')],
       'Protection de l’amélioration : casquette, façade sud.', ['protections.fixes'], APP_PROT,
       _old_vig('casquette_sud'), _from_bundle('casquette_sud'), old=True),
    _a('protection_mobile', 'protections', 'Protection mobile pilotée',
       'Volet, store ou brise-soleil orientable sur les façades choisies, fermé au soleil quand il fait chaud.', ['confort'],
       [_p('modele', 'Modèle', 'enum', 'store_ext', choix={k: _kind_label(k) for k in MOBILES}, groupe='Protection'),
        ORIENT_PARAM('SO')],
       [_cond('baies_visees', 'Les façades visées ont des fenêtres', True, _windows_on, 'Pas de fenêtres sur les façades visées.')],
       'Protections de l’amélioration : une par façade visée, pilotage automatique (éclairement > 200 W/m², T ext > 20 °C).',
       ['protections.mobiles'], APP_PROT, ['Résistance au vent ; motorisation et pilotage à prévoir.'], _mobile),
    _a('protection_fixe', 'protections', 'Protection fixe (casquette, débord, joues, loggia)',
       'Débord horizontal ou avancées latérales au-dessus et autour des baies des façades choisies.', ['confort', 'energie'],
       [_p('modele', 'Modèle', 'enum', 'casquette', choix={k: _kind_label(k) for k in FIXES}, groupe='Protection'),
        ORIENT_PARAM('S'),
        _p('profondeur_m', 'Profondeur', 'number', 0.8, unite='m', mn=0.2, mx=3, pas=0.1, groupe='Géométrie')],
       [_cond('baies_visees', 'Les façades visées ont des fenêtres', True, _windows_on, 'Pas de fenêtres sur les façades visées.')],
       'Protections de l’amélioration : une par façade visée (paramètres non précisés : défauts du catalogue des protections).',
       ['protections.fixes'], APP_PROT, ['Autorisation d’urbanisme (modification de façade).',
                                         'Réduit aussi les apports solaires utiles l’hiver.'], _fixed),
    _a('vitrage_solaire', 'protections', 'Film ou vitrage de contrôle solaire',
       'Réduit le facteur solaire g des baies des façades choisies.', ['confort'],
       [_p('modele', 'Modèle', 'enum', 'film', choix={k: _kind_label(k) for k in ('film', 'vitrage_controle')}, groupe='Protection'),
        ORIENT_PARAM('SO'),
        _p('g', 'Facteur solaire g', 'number', 0.35, mn=0.1, mx=0.6, pas=0.01, groupe='Vitrage')],
       [_cond('baies_visees', 'Les façades visées ont des fenêtres', True, _windows_on, 'Pas de fenêtres sur les façades visées.'),
        _cond('gain_g', 'Le facteur solaire baisse', True, _lower('baies.g', 'g'), 'Le vitrage actuel a déjà un facteur solaire plus bas.')],
       'Protections de l’amélioration : g ramené à la valeur choisie sur les façades visées.',
       ['protections.vitrage'], APP_PROT, ['Réduit aussi les apports solaires d’hiver et la lumière naturelle.'], _glazing),
    # ── Végétalisation de la parcelle ──
    _a('arbres', 'vegetalisation', 'Arbres plantés devant les façades',
       'Arbres d’ombrage plantés sur la parcelle devant les façades choisies.', ['confort', 'energie'],
       [ORIENT_PARAM('SO'),
        _p('nombre', 'Nombre d’arbres', 'int', 2, mn=1, mx=10, pas=1, groupe='Plantation'),
        _p('hauteur_m', 'Hauteur à maturité', 'number', 8.0, unite='m', mn=3, mx=25, pas=0.5, groupe='Plantation'),
        _p('rayon_houppier_m', 'Rayon du houppier', 'number', 3.0, unite='m', mn=1, mx=8, pas=0.5, groupe='Plantation'),
        _p('distance_m', 'Distance à la façade', 'number', 6.0, unite='m', mn=2, mx=30, pas=0.5, groupe='Plantation'),
        _p('persistant', 'Feuillage persistant', 'bool', False, groupe='Plantation',
           aide='Caduc : ombre l’été (transmission 0,2), laisse passer le soleil l’hiver (0,7). Persistant : 0,2 / 0,25.')],
       [_cond('emprise_connue', 'Emprise du bâtiment connue', False, _footprint_known,
              'Emprise du bâtiment inconnue : arbres placés autour d’un carré de même surface.')],
       'Surcharges de l’amélioration : arbres (position dans le repère du bâtiment, hauteur, houppier, transmissions).',
       ['abords.arbres'], APP_TREES,
       ['Effet atteint à maturité (10 à 20 ans).', 'Réseaux enterrés, fondations et limites de propriété.'], _trees),
    _a('sol_vegetalise', 'vegetalisation', 'Abords végétalisés (désimperméabilisation)',
       'Remplacement des abords minéraux (enrobé, dallage) par de la pelouse ou des plantations basses.', ['confort'],
       [_p('albedo', 'Albédo des abords végétalisés', 'number', 0.22, mn=0.15, mx=0.3, pas=0.01, groupe='Sol')],
       [_cond('abords_differents', 'Albédo actuel des abords différent', True,
              lambda ctx, p, plan: abs(_num(ctx['values'].get('abords.albedo'), 0.20) - p['albedo']) > 1e-3,
              'Abords déjà végétalisés (même albédo).')],
       'Fait de l’amélioration : albédo des abords. Le rafraîchissement de l’air est une action à part (hypothèse).',
       ['abords.albedo'], APP_ALBEDO, ['Gain surtout sur l’îlot de chaleur, que le calcul ne modélise pas.'],
       _fact_param('abords.albedo', 'albedo', 'abords végétalisés')),
    _a('toiture_vegetalisee', 'vegetalisation', 'Toiture végétalisée',
       'Substrat et plantes sur la toiture : isolation d’appoint, toiture moins chaude l’été (évapotranspiration).',
       ['confort', 'energie'],
       [_p('substrat_m', 'Épaisseur de substrat', 'number', 0.12, unite='m', mn=0.05, mx=0.5, pas=0.01, groupe='Toiture'),
        _p('lai', 'Indice de surface foliaire', 'number', 3.0, mn=0.5, mx=6, pas=0.5, groupe='Toiture'),
        _p('part', 'Part de la toiture', 'number', 1.0, mn=0.1, mx=1, pas=0.05, groupe='Toiture')],
       [_cond('toiture_faible_pente', 'Toiture terrasse ou pente ≤ 30°', True, _roof_flat, 'Toiture trop pentue (> 30°).')],
       'Fait (toiture végétalisée) et surcharge (substrat, indice foliaire, part) de l’amélioration.',
       ['toiture.vegetalisee', 'toiture.absorptance'], APP_GREEN,
       ['Charge sur la structure (60 à 150 kg/m² saturé) : vérification par un bureau d’études.',
        'Étanchéité anti-racines ; arrosage possible en été.'], _green_roof),
    _a('murs_vegetalises', 'vegetalisation', 'Murs végétalisés',
       'Plantes grimpantes ou mur végétal sur les façades choisies.', ['confort'],
       [ORIENT_PARAM('SO'),
        _p('lai', 'Indice de surface foliaire', 'number', 2.0, mn=0.5, mx=5, pas=0.5, groupe='Végétation'),
        _p('part', 'Part des façades couverte', 'number', 0.6, mn=0.1, mx=1, pas=0.05, groupe='Végétation')],
       [],
       'Fait (murs végétalisés) et surcharge (orientations, indice foliaire, part) de l’amélioration.',
       ['murs.vegetalises'], APP_GREEN, ['Entretien ; supports de grimpantes sans percer l’isolant.'], _green_walls),
    _a('rafraichissement_air', 'vegetalisation', 'Rafraîchissement de l’air par la végétation (hypothèse)',
       'Hypothèse affichée : l’air au contact du bâtiment serait plus frais l’après-midi d’été grâce à la végétation.',
       ['confort'],
       [_p('delta_c', 'Abaissement de la température de l’air', 'number', 1.0, unite='°C', mn=0.2, mx=3, pas=0.1,
           groupe='Hypothèse')],
       [],
       'Surcharge de l’amélioration : abaissement de la température extérieure (juin–août, 12 h–18 h), avec son texte '
       'd’hypothèse figé dans les entrées du calcul.',
       ['air.rafraichissement'], APP_AIR,
       ['C’est une hypothèse, pas un résultat : à présenter comme telle.'], _air),
    # ── Toiture et albédos ──
    _a('combles', 'toiture_albedo', scenarios.BUNDLES['combles']['label'],
       'Isolant posé dans les combles perdus ou sous la toiture.', ['energie', 'confort'],
       [_thickness_param('combles')],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('combles'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('gain_u_toiture', 'Le U de la toiture baisse', True, _gain('toiture.u'), 'Le U de la toiture n’en serait pas amélioré.'),
        _cond('pas_terrasse', 'Toiture à combles', False, _enum_not_in('toiture.type', ['terrasse']),
              'Toiture terrasse : isolation par l’extérieur, au-dessus de l’étanchéité.')],
       'Fait de l’amélioration : U de la toiture déduit de l’épaisseur et de λ.',
       ['toiture.u'], APP_U, _old_vig('combles'), _from_bundle('combles'), old=True),
    _a('toiture_claire', 'toiture_albedo', 'Toiture claire (revêtement réfléchissant)',
       'Revêtement de toiture clair qui renvoie une grande part du soleil.', ['confort'],
       [_p('absorptance', 'Absorptance solaire de la toiture', 'number', 0.25, mn=0.1, mx=0.98, pas=0.01, groupe='Toiture')],
       [_cond('gain_alpha', 'Toiture actuelle plus sombre', True,
              lambda ctx, p, plan: p['absorptance'] < _num(ctx['values'].get('toiture.absorptance'),
                                                           _num(ctx['values'].get('enveloppe.couleur_absorptance'), 0.6)) - 1e-6,
              'La toiture est déjà au moins aussi claire.')],
       'Fait de l’amélioration : absorptance solaire de la toiture.', ['toiture.absorptance'], APP_ALPHA,
       ['Légère hausse du besoin de chauffage l’hiver.', 'Encrassement : l’effet baisse sans entretien.'],
       _fact_param('toiture.absorptance', 'absorptance', 'toiture claire')),
    _a('murs_clairs', 'toiture_albedo', 'Murs clairs',
       'Enduit ou peinture de façade clair.', ['confort'],
       [_p('absorptance', 'Absorptance solaire des murs', 'number', 0.3, mn=0.1, mx=0.98, pas=0.01, groupe='Murs')],
       [_cond('gain_alpha', 'Murs actuels plus sombres', True, _lower('enveloppe.couleur_absorptance', 'absorptance', 0.6),
              'Les murs sont déjà au moins aussi clairs.')],
       'Fait de l’amélioration : absorptance solaire des murs.', ['enveloppe.couleur_absorptance'], APP_ALPHA,
       ['Teintes de façade parfois imposées par le plan local d’urbanisme.', 'Effet faible sur des murs bien isolés.'],
       _fact_param('enveloppe.couleur_absorptance', 'absorptance', 'murs clairs')),
    _a('abords_clairs', 'toiture_albedo', 'Abords clairs',
       'Revêtement clair autour du bâtiment (dallage, béton clair).', ['confort', 'energie'],
       [_p('albedo', 'Albédo des abords', 'number', 0.35, mn=0.05, mx=0.9, pas=0.01, groupe='Sol')],
       [_cond('gain_albedo', 'Abords actuels plus sombres', True, _higher('abords.albedo', 'albedo', 0.20),
              'Les abords sont déjà au moins aussi clairs.')],
       'Fait de l’amélioration : albédo des abords. Plus de soleil réfléchi vers les façades : gains d’hiver, mais '
       'surchauffe d’été possible derrière les vitrages.', ['abords.albedo'], APP_ALBEDO,
       ['Éblouissement ; l’îlot de chaleur évité n’est pas modélisé.'],
       _fact_param('abords.albedo', 'albedo', 'abords clairs')),
    # ── Chauffage et systèmes ──
    _a('pac', 'systemes', scenarios.BUNDLES['pac']['label'],
       'Remplacement du générateur de chauffage par une pompe à chaleur air/eau.', ['energie'], [],
       [_cond('parc_actif', 'Amélioration active dans les réglages du parc', True, _bundle_active('pac'),
              'Amélioration désactivée dans les réglages du parc.'),
        _cond('pas_deja_pac', 'Générateur actuel autre qu’une pompe à chaleur', True,
              _enum_not_in('systeme.generateur', ['pac_air_eau', 'pac_air_air']), 'Déjà chauffé par une pompe à chaleur.'),
        _cond('emetteurs_bt', 'Émetteurs basse température', False, _enum_in('systeme.emetteurs', ['radiateurs_bt', 'plancher']),
              'Émetteurs haute température : PAC haute température ou émetteurs à remplacer.')],
       'Faits de l’amélioration : générateur et rendement de génération (COP saisonnier).',
       ['systeme.generateur', 'systeme.rendement_generation'], APP_SYS, _old_vig('pac'), _from_bundle('pac'), old=True),
]
BY_KEY = {a['cle']: a for a in ACTIONS}
assert set(scenarios.BUNDLES) <= set(BY_KEY), 'chaque ancien bouquet est une action'


def _effective_param_defaults(action, cfg):
    out = []
    for p in action['parametres']:
        p = dict(p)
        if action['_old'] and p['cle'] == 'epaisseur_cm' and cfg and f'bouquets.{action["cle"]}.epaisseur_cm' in cfg:
            p['defaut'] = cfg[f'bouquets.{action["cle"]}.epaisseur_cm']
        out.append(p)
    return out


def action_label(action, cfg=None):
    if action['_old']:
        return reglages.bundle_label(action['cle'], scenarios.BUNDLES[action['cle']], cfg)
    return action['libelle']


def catalogue_json(cfg=None):
    """Forme I7.a (+ I7.c : `desactivee`, défauts du parc)."""
    out = []
    for a in ACTIONS:
        row = {k: v for k, v in a.items() if not k.startswith('_')}
        row['libelle'] = action_label(a, cfg)
        row['parametres'] = _effective_param_defaults(a, cfg)
        row['conditions'] = [{'code': c['code'], 'libelle': c['libelle'], 'bloquante': c['bloquante']} for c in a['conditions']]
        row['desactivee'] = bool(a['_old'] and not reglages.bundle_active(cfg, a['cle']))
        out.append(row)
    return {'familles': FAMILLES, 'actions': out}


# ── Paramètres ──────────────────────────────────────────────────────────────

class ActionError(ValueError):
    pass


def validate_params(action_key, raw):
    """Paramètres validés d'un élément (clés inconnues → erreur fixe). Les absents restent absents : leur défaut
    (celui du parc, s'il y en a un) s'applique au moment de l'application."""
    a = BY_KEY.get(action_key)
    if a is None:
        raise ActionError('Action inconnue.')
    if raw in (None, ''):
        return {}
    if not isinstance(raw, dict):
        raise ActionError('Paramètres attendus.')
    specs = {p['cle']: p for p in a['parametres']}
    out = {}
    for k, v in raw.items():
        spec = specs.get(k)
        if spec is None:
            raise ActionError('Paramètre inconnu.')
        if spec['type'] in ('number', 'int'):
            if isinstance(v, bool):
                raise ActionError(f'« {spec["libelle"]} » : nombre attendu.')
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ActionError(f'« {spec["libelle"]} » : nombre attendu.') from None
            if x != x or (spec['min'] is not None and x < spec['min']) or (spec['max'] is not None and x > spec['max']):
                raise ActionError(f'« {spec["libelle"]} » : entre {_fmt(spec["min"])} et {_fmt(spec["max"])}.')
            if spec['type'] == 'int':
                if abs(x - round(x)) > 1e-9:
                    raise ActionError(f'« {spec["libelle"]} » : nombre entier attendu.')
                x = int(round(x))
            out[k] = x
        elif spec['type'] == 'enum':
            if v not in (spec['choix'] or {}):
                raise ActionError(f'« {spec["libelle"]} » : choix inconnu.')
            out[k] = v
        else:
            if not isinstance(v, bool):
                raise ActionError(f'« {spec["libelle"]} » : oui ou non attendu.')
            out[k] = v
    return out


def full_params(action, params, cfg):
    return {**{p['cle']: p['defaut'] for p in _effective_param_defaults(action, cfg)}, **(params or {})}


# ── Évaluation et application à un bâtiment ─────────────────────────────────

def evaluate(action_key, params, ctx):
    """(plan | None, raison | None, avertissements, paramètres complets). Ne touche à rien en base."""
    a = BY_KEY[action_key]
    p = full_params(a, params, ctx['cfg'])
    plan = a['_apply'](ctx, p)
    warnings = []
    for c in a['conditions']:
        ok = c['test'](ctx, p, plan)
        if ok is None:
            warnings.append(f'Information manquante pour vérifier « {c["libelle"]} ».')
        elif not ok:
            if c['bloquante']:
                return None, c['raison'], warnings, p
            warnings.append(c['raison'])
    return plan, None, warnings, p


def describe_effects(plan, values):
    out = []
    for k, v, _src in plan['facts']:
        spec = facts.BY_KEY.get(k)
        label = spec.label if spec else k
        old = values.get(k)
        if old is None:
            out.append(f'{label} : {facts.display_value(k, v)}')
        else:
            out.append(f'{label} : {facts.display_value(k, old)} → {facts.display_value(k, v)}')
    for d in plan['devices']:
        o = {'all': 'toutes façades'}.get(d['orientation'], f'façade {ORIENTATIONS.get(d["orientation"], d["orientation"]).lower()}')
        pilot = ', pilotage automatique' if (d.get('control') or {}).get('mode') == 'auto' else ''
        out.append(f'{_kind_label(d["kind"])} ({o}){pilot}')
    return out + list(plan['effets'])
