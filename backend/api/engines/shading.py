"""Protections solaires paramétrables (Lot 14, refonte — § B4 ; catalogue v2 : lot 36, chantier bt-potentiels I1).

**Catalogue** (`catalogue()`, servi par `GET /api/protections/catalogue/`) : chaque modèle dit ses paramètres (type,
bornes, pas, défaut, groupe, aide, niveaux où ils jouent), son pilotage et la façon dont N0, N1 et N2 l'appliquent.
`ShadingDevice.params` / `.control` sont validés par lui (`validate_params`, `validate_control`).

**Physique, heure par heure, pour les baies d'une orientation** (`orientation_effects`) :

- **débords** (casquette, balcon, débord de toiture, plafond de loggia) : fraction ensoleillée d'une baie
  rectangulaire de hauteur h et largeur w sous un débord de profondeur D (projection horizontale), dont la sous-face
  est à `gap` au-dessus de la baie, incliné de β (bord extérieur plus bas si β > 0), débordant latéralement de e_g à
  gauche et e_d à droite (vu de l'extérieur, face à la façade). Même géométrie que l'annexe F de l'EN ISO 52016-1
  (débord et joues d'après l'angle de profil) : un point de la baie à x sous son haut est à l'ombre si le rayon qui
  le relie au soleil coupe le débord, soit à la distance d* = (x + gap) / (tan φ + tan β) ≤ D, et si, à cette
  distance, le décalage latéral d*·tan γ le laisse sous le débord (γ : écart d'azimut, φ : angle de profil,
  tan φ = tan h_s / cos γ). Intégré sur la hauteur de la baie (16 points) ;
- **joues** : ombre de la joue côté soleil, de profondeur F et de hauteur H_j depuis l'appui ; un point à z de
  l'appui et u de la joue est à l'ombre si u ≤ F·|tan γ| et si le rayon passe sous le haut de la joue
  (z + u·tan φ/|tan γ| ≤ H_j) ; intégré sur la hauteur. Loggia : débord × joues (hypothèse d'indépendance) ;
- **diffus** sous un débord ou entre des joues : part du ciel vue par le milieu de la baie (approximation) ;
- **lames** (brise-soleil orientable, brise-soleil fixe) : modèle géométrique des stores vénitiens de l'ISO 15099:2003
  § 7 (réflexions entre lames négligées — majorant de la protection pour des lames claires). Lames de largeur L,
  d'entraxe s, d'épaisseur t, inclinées de ψ (0 : horizontales ; > 0 : bord extérieur vers le bas). Part du direct
  qui passe entre les lames : f = 1 − (L·|sin(φ+ψ)| + t·|cos(φ+ψ)|) / (s·cos φ), bornée à [0, 1] ; la part arrêtée
  traverse la lame à sa transmission τ_l. Diffus (ciel et sol isotropes) : ∫ f(φ) cos φ dφ / 2 sur φ ∈ [−90°, 90°].
  Suivi du soleil : ψ minimal qui coupe le direct, ψ = arcsin(s·cos φ / L) − φ ;
- **fermetures mobiles** (volet roulant, battant, persienne, store extérieur ou intérieur, BSO) : position 0
  (ouvert) à 1 (fermé), pilotée (fixe, planning, automatique sur l'éclairement de la façade et la température
  extérieure, fermée la nuit) ; fermée, elle laisse passer `fs_dir` / `fs_dif` (lames : modèle ci-dessus) et ajoute
  sa **résistance ΔR** au vitrage au prorata de sa position (EN ISO 10077-1:2017, annexe G — de 0,08 m²·K/W pour un
  store très perméable à 0,25 pour un volet roulant peu perméable) ;
- **vitrage** de contrôle solaire ou film : facteur g ramené à une nouvelle valeur ;
- **végétation** caduque, pergola (D8) : transmission du direct différente en saison de feuilles et hors saison.

**Opaque** : un débord ombre aussi la bande de mur qui l'entoure (hauteur gap + h, largeur w + e_g + e_d par baie,
nombre de baies déduit de la surface vitrée ; débord filant : toute la façade) — `opaque_zone`.

**Clés absentes** (protections enregistrées avant le lot 36) : les clés anciennes gardent leur sens et leur valeur
de repli d'alors (`LEGACY`) — profondeur 0, hauteur de baie 1,5 m, débord latéral infini, transmissions par type ;
les clés nouvelles prennent le défaut du catalogue, choisi neutre quand une valeur neutre existe (inclinaison 0,
joues sur toute la hauteur, pas de fermeture nocturne). Une protection créée ou modifiée par l'API est enregistrée
complète (défauts du catalogue posés).
"""

import math

import numpy as np

FIXED = ('casquette', 'balcon', 'debord_toit', 'joues', 'loggia', 'brise_soleil_fixe')
OVERHANGS = ('casquette', 'balcon', 'debord_toit', 'loggia')
FINS = ('joues', 'loggia')
SLATS = ('bso', 'brise_soleil_fixe')
GEOMETRIC = ('casquette', 'balcon', 'debord_toit', 'joues', 'loggia')   # ceux qu'on sait construire en obstacle 3D
MOBILE = ('volet_roulant', 'volet_battant', 'persienne', 'store_ext', 'store_int', 'bso')
GLAZING = ('film', 'vitrage_controle')
VEGETATION = ('arbre_caduc', 'pergola')

MOBILE_DEFAULTS = {  # transmission fermé (direct, diffus) — repères usuels, comme SHADING_PROFILES du N2
    'volet_roulant': (0.0, 0.0), 'volet_battant': (0.0, 0.05), 'persienne': (0.05, 0.15),
    'store_ext': (0.15, 0.40), 'store_int': (0.55, 0.65), 'bso': (0.05, 0.25),
}
# ΔR fermé (m²·K/W) — EN ISO 10077-1:2017 annexe G : 0,08 (perméabilité très élevée : stores, lames) à 0,25
# (volet roulant peu perméable) ; volet roulant 0,20 comme SHADING_PROFILES du N2 (Lot J).
DELTA_R_DEFAULTS = {'volet_roulant': 0.20, 'volet_battant': 0.15, 'persienne': 0.08, 'store_ext': 0.08,
                    'store_int': 0.05, 'bso': 0.04}
VEGETATION_DEFAULTS = {'arbre_caduc': (0.30, 0.75), 'pergola': (0.35, 0.85)}  # (feuilles, sans feuilles) sur le direct
SUMMER_MONTHS = (5, 6, 7, 8, 9)
SLAT_DEFAULTS = {'bso': {'lame_inclinaison_deg': 45.0, 'lame_largeur_m': 0.08, 'lame_entraxe_m': 0.07,
                         'lame_epaisseur_m': 0.001, 'lame_transmission': 0.0},
                 'brise_soleil_fixe': {'lame_inclinaison_deg': 30.0, 'lame_largeur_m': 0.20, 'lame_entraxe_m': 0.25,
                                       'lame_epaisseur_m': 0.02, 'lame_transmission': 0.0}}
N_INTEGRATION = 16

LEVELS = ['N0', 'N1', 'N2']


class CatalogueError(ValueError):
    """Message fixe, rendu tel quel par l'API."""


# ── Catalogue (I1) ────────────────────────────────────────────────────────────

def _p(cle, libelle, unite, type_, mn=None, mx=None, pas=None, defaut=None, groupe='', aide='', niveaux=None, choix=None):
    return {'cle': cle, 'libelle': libelle, 'unite': unite, 'type': type_, 'min': mn, 'max': mx, 'pas': pas,
            'defaut': defaut, 'choix': choix, 'groupe': groupe, 'aide': aide, 'niveaux': list(niveaux or LEVELS)}


def _window(h=1.5, w=1.2):
    return [_p('window_height_m', 'Hauteur de la baie', 'm', 'number', 0.3, 5, 0.05, h, 'Baie protégée',
               'Hauteur du vitrage protégé (une baie type de la façade). En N2, les baies réelles du modèle.',
               niveaux=['N0', 'N1']),
            _p('window_width_m', 'Largeur de la baie', 'm', 'number', 0.3, 20, 0.05, w, 'Baie protégée',
               'Largeur d’une baie type ; le nombre de baies est déduit de la surface vitrée de la façade. En N2, '
               'les baies réelles du modèle.', niveaux=['N0', 'N1'])]


def _overhang(depth, gap, tilt=True, lateral=True):
    out = [_p('depth_m', 'Profondeur du débord', 'm', 'number', 0, 5, 0.05, depth, 'Débord',
              'Avancée horizontale depuis la façade (projection au sol si le débord est incliné).'),
           _p('gap_m', 'Hauteur de la sous-face au-dessus de la baie', 'm', 'number', 0, 3, 0.05, gap, 'Débord',
              'Du haut du vitrage à la sous-face du débord, à la façade.')]
    if tilt:
        out.append(_p('inclinaison_deg', 'Inclinaison du débord', '°', 'number', -30, 60, 5, 0.0, 'Débord',
                      '0 : horizontal ; positif : bord extérieur plus bas (auvent incliné), négatif : relevé.'))
    if lateral:
        out += [_p('extension_m', 'Débord latéral (des deux côtés)', 'm', 'number', 0, 5, 0.05, 2.0, 'Débords latéraux',
                   'Dépassement du débord de chaque côté de la baie ; précisé côté par côté par les deux réglages suivants.'),
                _p('extension_gauche_m', 'Débord latéral à gauche', 'm', 'number', 0, 5, 0.05, None, 'Débords latéraux',
                   'Vu de l’extérieur, face à la façade. Vide : le débord latéral des deux côtés.'),
                _p('extension_droite_m', 'Débord latéral à droite', 'm', 'number', 0, 5, 0.05, None, 'Débords latéraux',
                   'Vu de l’extérieur, face à la façade. Vide : le débord latéral des deux côtés.')]
    return out


def _fins(depth=0.4, full=True):
    out = [_p('fin_depth_m', 'Profondeur des joues', 'm', 'number', 0, 3, 0.05, depth, 'Joues',
              'Avancée des joues verticales de part et d’autre de la baie.')]
    if full:
        out += [_p('fin_height_m', 'Hauteur des joues', 'm', 'number', 0.3, 10, 0.05, 3.0, 'Joues',
                   'Depuis l’appui de la baie ; au-delà de la hauteur de la baie, elles la couvrent entièrement.'),
                _p('fin_cote', 'Joues présentes', '', 'enum', defaut='deux', groupe='Joues', choix=[
                    {'valeur': 'deux', 'libelle': 'des deux côtés'}, {'valeur': 'gauche', 'libelle': 'à gauche seulement'},
                    {'valeur': 'droite', 'libelle': 'à droite seulement'}],
                   aide='Vu de l’extérieur, face à la façade.')]
    return out


def _slats(kind):
    d = SLAT_DEFAULTS[kind]
    tilt_min = 0 if kind == 'bso' else -30
    return [
        _p('lame_inclinaison_deg', 'Inclinaison des lames', '°', 'number', tilt_min, 90, 5, d['lame_inclinaison_deg'], 'Lames',
           '0 : lames horizontales (ouvertes) ; 90 : verticales (fermées) ; bord extérieur vers le bas quand l’angle croît.'),
        _p('lame_largeur_m', 'Largeur des lames', 'm', 'number', 0.02, 0.6, 0.005, d['lame_largeur_m'], 'Lames',
           'Profondeur de la lame dans sa section.'),
        _p('lame_entraxe_m', 'Entraxe des lames', 'm', 'number', 0.02, 0.8, 0.005, d['lame_entraxe_m'], 'Lames',
           'Distance verticale entre deux lames. Lames jointives fermées quand la largeur atteint l’entraxe.'),
        _p('lame_epaisseur_m', 'Épaisseur des lames', 'm', 'number', 0, 0.05, 0.0005, d['lame_epaisseur_m'], 'Lames',
           'Réduit le passage entre lames, surtout lames ouvertes.'),
        _p('lame_transmission', 'Transmission des lames', '', 'number', 0, 0.6, 0.01, d['lame_transmission'], 'Lames',
           '0 : lame pleine (métal, bois) ; lame perforée ou toile : 0,05 à 0,3.'),
    ]


def _mobile_closed(kind):
    fs_dir, fs_dif = MOBILE_DEFAULTS[kind]
    out = []
    if kind not in SLATS:
        out += [_p('fs_dir', 'Transmission du direct, fermé', '', 'number', 0, 1, 0.01, fs_dir, 'Fermé',
                   'Part du rayonnement direct qui passe quand la protection est fermée.'),
                _p('fs_dif', 'Transmission du diffus, fermé', '', 'number', 0, 1, 0.01, fs_dif, 'Fermé',
                   'Part du rayonnement diffus qui passe quand la protection est fermée.')]
    out.append(_p('delta_r_m2k_w', 'Résistance ajoutée, fermé (ΔR)', 'm²·K/W', 'number', 0, 0.5, 0.01,
                  DELTA_R_DEFAULTS[kind], 'Fermé',
                  'Lame d’air entre la fermeture et le vitrage (EN ISO 10077-1, annexe G) : 0,08 store, 0,15 à 0,25 volet.'))
    return out


def _pilotage(kind):
    params = [
        _p('position', 'Position fermée', '', 'number', 0, 1, 0.05, 1.0, 'Pilotage',
           '1 : entièrement fermée (ou déroulée) quand elle se ferme ; 0,5 : à mi-course.'),
        _p('heure_debut', 'Fermée à partir de', 'h', 'int', 0, 23, 1, 13, 'Planning',
           'Planning : heure de fermeture (incluse).'),
        _p('heure_fin', 'Rouverte à', 'h', 'int', 0, 23, 1, 19, 'Planning',
           'Planning : heure d’ouverture (exclue) ; plus petite que la fermeture : passe minuit.'),
        _p('mois_debut', 'Du mois', '', 'int', 1, 12, 1, 5, 'Planning', 'Planning : premier mois (inclus).'),
        _p('mois_fin', 'Au mois', '', 'int', 1, 12, 1, 9, 'Planning', 'Planning : dernier mois (inclus).'),
        _p('irradiance_w_m2', 'Fermée au-delà de', 'W/m²', 'number', 50, 800, 10, 200.0, 'Automatique',
           'Automatique : éclairement reçu par la façade au-delà duquel elle se ferme.'),
        _p('t_ext_min_c', 'Si l’air extérieur dépasse', '°C', 'number', 10, 35, 0.5, 20.0, 'Automatique',
           'Automatique : pas de fermeture par temps frais (apports gratuits l’hiver).'),
        _p('close_when_absent', 'Fermée en l’absence d’occupants', '', 'bool', defaut=False, groupe='Automatique',
           aide='Tertiaire : fermée hors occupation si l’air extérieur dépasse le seuil.'),
        _p('fermeture_nocturne', 'Fermée la nuit', '', 'bool', defaut=kind in ('volet_roulant', 'volet_battant', 'persienne'),
           groupe='Nuit', aide='Fermée du coucher au lever du soleil, en plus du pilotage : sa résistance ΔR réduit '
                              'les déperditions des vitrages.'),
    ]
    if kind == 'bso':
        params.append(_p('lame_suivi_soleil', 'Lames suivant le soleil', '', 'bool', defaut=False, groupe='Lames',
                         aide='Fermé, les lames prennent l’angle juste nécessaire pour couper le direct (plus de lumière '
                              'diffuse) au lieu de l’inclinaison réglée.'))
    return {'modes': ['fixe', 'planning', 'auto'], 'parametres': params}


APPLICATION = {
    'debord': {'N0': 'Fraction ensoleillée des baies (direct, angle de profil) et part du ciel masquée (diffus), heure '
                     'par heure puis sommée par mois ; bande de mur autour du débord ombrée aussi.',
               'N1': 'Mêmes facteurs heure par heure sur les apports des baies et sur la bande de mur ombrée.',
               'N2': 'Obstacle géométrique réel (inclinaison, débords latéraux) dans le lancer de rayons, quel que soit '
                     'le mode — direct, ciel et mur ombrés exactement. Page Calcul 3D : en mode géométrique seulement.'},
    'joues': {'N0': 'Fraction ensoleillée des baies entre les joues (direct) et diffus réduit.',
              'N1': 'Mêmes facteurs heure par heure.',
              'N2': 'Joues construites en obstacles géométriques (profondeur, hauteur, côtés), quel que soit le mode. '
                    'Page Calcul 3D : en mode géométrique seulement.'},
    'lames_fixes': {'N0': 'Transmission des lames selon l’angle de profil (direct) et intégrée sur le ciel (diffus).',
                    'N1': 'Mêmes facteurs heure par heure.',
                    'N2': 'Mêmes facteurs appliqués au rayonnement de chaque triangle de baie de la façade (pas de '
                          'lames maillées).'},
    'mobile': {'N0': 'Facteurs heure par heure selon la position pilotée (fermée : transmissions), sommés par mois ; '
                     'ΔR fermé ajouté aux vitrages au prorata de la position (nuit comprise).',
               'N1': 'Position heure par heure (pilotage) : transmissions sur les apports des baies, ΔR sur le U des '
                     'vitrages.',
               'N2': 'Profil par triangle de baie : rayonnement reçu réduit et résistance ΔR ajoutée côté extérieur, '
                     'heure par heure selon la position (position partielle au prorata).'},
    'vitrage': {'N0': 'Facteur g des baies de la façade ramené à la valeur donnée.', 'N1': 'Idem.',
                'N2': 'Rayonnement reçu par les triangles de baie réduit dans le rapport g nouveau / g des vitrages.'},
    'vegetal': {'N0': 'Transmission du direct en saison de feuilles et hors saison ; diffus réduit de moitié de '
                      'l’écart.', 'N1': 'Idem, heure par heure.',
                'N2': 'Mêmes facteurs sur les triangles de baie de la façade (pour un arbre placé, voir l’amélioration '
                      '« arbres plantés »).'},
}

_FAMILLES = [{'cle': 'fixe', 'libelle': 'Protections fixes'}, {'cle': 'mobile', 'libelle': 'Fermetures et stores mobiles'},
             {'cle': 'vitrage', 'libelle': 'Vitrages et films'}, {'cle': 'vegetal', 'libelle': 'Végétation'}]


def _model(cle, famille, libelle, description, parametres, application, modes=('parametrique',), pilotage=None):
    return {'cle': cle, 'famille': famille, 'libelle': libelle, 'description': description, 'modes': list(modes),
            'parametres': parametres, 'pilotage': pilotage or {'modes': [], 'parametres': []},
            'application': APPLICATION[application]}


def _build_catalogue():
    geo = ('parametrique', 'geometrique')
    m = [
        _model('casquette', 'fixe', 'Casquette (auvent)', 'Débord horizontal ou incliné au-dessus de la baie.',
               _overhang(0.8, 0.2) + _window(), 'debord', geo),
        _model('balcon', 'fixe', 'Balcon de l’étage supérieur', 'La dalle du balcon du dessus fait débord.',
               _overhang(1.2, 0.3) + _window(), 'debord', geo),
        _model('debord_toit', 'fixe', 'Débord de toiture', 'Avancée de toiture au-dessus des baies du dernier niveau.',
               _overhang(0.5, 0.4) + _window(), 'debord', geo),
        _model('joues', 'fixe', 'Joues (avancées latérales)', 'Parois verticales de part et d’autre de la baie.',
               _fins() + _window(), 'joues', geo),
        _model('loggia', 'fixe', 'Loggia', 'Baie en retrait : plafond (débord) et joues sur toute la hauteur.',
               _overhang(1.2, 0.2, tilt=False, lateral=False) + _fins(1.2, full=False) + _window(), 'debord', geo),
        _model('brise_soleil_fixe', 'fixe', 'Brise-soleil à lames fixes', 'Lames horizontales fixes devant la baie.',
               _slats('brise_soleil_fixe') + [_p('couverture', 'Part de la baie couverte', '', 'number', 0, 1, 0.05, 1.0,
                                                 'Lames', 'Hauteur de baie couverte par les lames.')], 'lames_fixes'),
        _model('volet_roulant', 'mobile', 'Volet roulant', 'Tablier opaque enroulable.', _mobile_closed('volet_roulant'),
               'mobile', pilotage=_pilotage('volet_roulant')),
        _model('volet_battant', 'mobile', 'Volet battant', 'Volet plein à vantaux.', _mobile_closed('volet_battant'),
               'mobile', pilotage=_pilotage('volet_battant')),
        _model('persienne', 'mobile', 'Persienne', 'Volet à lames fixes.', _mobile_closed('persienne'), 'mobile',
               pilotage=_pilotage('persienne')),
        _model('store_ext', 'mobile', 'Store extérieur', 'Toile extérieure (screen, store vertical).',
               _mobile_closed('store_ext'), 'mobile', pilotage=_pilotage('store_ext')),
        _model('store_int', 'mobile', 'Store intérieur', 'Toile ou store vénitien côté intérieur.',
               _mobile_closed('store_int'), 'mobile', pilotage=_pilotage('store_int')),
        _model('bso', 'mobile', 'Brise-soleil orientable', 'Lames orientables extérieures (BSO).',
               _slats('bso') + _mobile_closed('bso'), 'mobile', pilotage=_pilotage('bso')),
        _model('film', 'vitrage', 'Film de contrôle solaire', 'Film collé sur le vitrage existant.',
               [_p('g', 'Facteur solaire g avec le film', '', 'number', 0.05, 0.9, 0.01, 0.35, 'Vitrage',
                   'Remplace le g des baies de la façade.')], 'vitrage'),
        _model('vitrage_controle', 'vitrage', 'Vitrage de contrôle solaire', 'Vitrage à couche sélective.',
               [_p('g', 'Facteur solaire g du vitrage', '', 'number', 0.05, 0.9, 0.01, 0.30, 'Vitrage',
                   'Remplace le g des baies de la façade.')], 'vitrage'),
    ]
    for kind, label, desc in (('arbre_caduc', 'Arbre à feuilles caduques', 'Arbre devant la façade, sans position.'),
                              ('pergola', 'Pergola végétalisée', 'Treille de plantes grimpantes devant la baie.')):
        ts, tw = VEGETATION_DEFAULTS[kind]
        m.append(_model(kind, 'vegetal', label, desc, [
            _p('t_summer', 'Transmission en saison de feuilles', '', 'number', 0, 1, 0.01, ts, 'Végétation',
               'Part du direct qui passe le feuillage.'),
            _p('t_winter', 'Transmission hors saison', '', 'number', 0, 1, 0.01, tw, 'Végétation',
               'Branches nues (arbre caduc).'),
            _p('mois_feuilles_debut', 'Feuilles à partir du mois', '', 'int', 1, 12, 1, 5, 'Végétation',
               'Premier mois de la saison de feuilles (inclus).'),
            _p('mois_feuilles_fin', 'Feuilles jusqu’au mois', '', 'int', 1, 12, 1, 9, 'Végétation',
               'Dernier mois de la saison de feuilles (inclus).'),
        ], 'vegetal'))
    return m


MODELS = _build_catalogue()
KINDS = {m['cle']: m for m in MODELS}
VERSION = 2


def catalogue():
    return {'version': VERSION, 'familles': _FAMILLES, 'modeles': MODELS}


def _param_spec(kind, key, control=False):
    m = KINDS.get(kind)
    if m is None:
        return None
    for p in (m['pilotage']['parametres'] if control else m['parametres']):
        if p['cle'] == key:
            return p
    return None


def _check(spec, value):
    t = spec['type']
    if value is None:
        if spec['defaut'] is None:
            return None
        raise CatalogueError('Valeur manquante.')
    if t == 'bool':
        if not isinstance(value, bool):
            raise CatalogueError('Oui ou non attendu.')
        return value
    if t == 'enum':
        if value not in [c['valeur'] for c in spec['choix'] or []]:
            raise CatalogueError('Choix inconnu.')
        return value
    if isinstance(value, bool):
        raise CatalogueError('Nombre attendu.')
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise CatalogueError('Nombre attendu.') from None
    if not math.isfinite(x):
        raise CatalogueError('Nombre attendu.')
    if t == 'int':
        if x != int(x):
            raise CatalogueError('Nombre entier attendu.')
        x = int(x)
    if not spec['min'] <= x <= spec['max']:
        raise CatalogueError(f"{spec['libelle']} : entre {spec['min']} et {spec['max']}.")
    return x


def validate_params(kind, params):
    """Paramètres validés et complétés par les défauts du catalogue. Lève CatalogueError (message fixe)."""
    if kind not in KINDS:
        raise CatalogueError('Type de protection inconnu.')
    if not isinstance(params, dict):
        raise CatalogueError('Paramètres attendus.')
    out = {}
    for k, v in params.items():
        spec = _param_spec(kind, k)
        if spec is None:
            raise CatalogueError('Paramètre inconnu.')
        out[k] = _check(spec, v)
    for spec in KINDS[kind]['parametres']:
        if spec['cle'] not in out:
            out[spec['cle']] = spec['defaut']
    return out


def validate_control(kind, control):
    """Pilotage validé et complété ; vide pour un modèle sans pilotage."""
    if kind not in KINDS:
        raise CatalogueError('Type de protection inconnu.')
    pil = KINDS[kind]['pilotage']
    if not isinstance(control, dict):
        raise CatalogueError('Pilotage attendu.')
    if not pil['modes']:
        if control:
            raise CatalogueError('Cette protection n’a pas de pilotage.')
        return {}
    mode = control.get('mode', 'planning')
    if mode not in pil['modes']:
        raise CatalogueError('Pilotage : fixe, planning ou auto.')
    out = {'mode': mode}
    for k, v in control.items():
        if k == 'mode':
            continue
        spec = _param_spec(kind, k, control=True)
        if spec is None:
            raise CatalogueError('Paramètre de pilotage inconnu.')
        out[k] = _check(spec, v)
    for spec in pil['parametres']:
        if spec['cle'] not in out:
            out[spec['cle']] = spec['defaut']
    return out


# ── Lecture des paramètres (clés absentes : repli d'avant le lot 36) ───────────

LEGACY = {'depth_m': 0.0, 'gap_m': 0.0, 'window_height_m': 1.5, 'window_width_m': 1.2, 'extension_m': None,
          'fin_depth_m': 0.0, 'fin_height_m': None, 'fin_cote': 'deux', 'couverture': 1.0, 'inclinaison_deg': 0.0}
LEGACY_CONTROL = {'mode': 'planning', 'position': 1.0, 'irradiance_w_m2': 200.0, 't_ext_min_c': 20.0,
                  'close_when_absent': False, 'fermeture_nocturne': False, 'lame_suivi_soleil': False}


def param(dev, key):
    p = dev.get('params') or {}
    v = p.get(key)
    if v is not None:
        return v
    kind = dev['kind']
    if key == 'fs_dir' and kind in MOBILE_DEFAULTS:
        return MOBILE_DEFAULTS[kind][0]
    if key == 'fs_dif' and kind in MOBILE_DEFAULTS:
        return MOBILE_DEFAULTS[kind][1]
    if key == 'delta_r_m2k_w' and kind in DELTA_R_DEFAULTS:
        return DELTA_R_DEFAULTS[kind]
    if key in ('t_summer', 't_winter') and kind in VEGETATION_DEFAULTS:
        return VEGETATION_DEFAULTS[kind][0 if key == 't_summer' else 1]
    if key in LEGACY:
        return LEGACY[key]
    spec = _param_spec(kind, key)
    if spec:
        return spec['defaut']
    return SLAT_DEFAULTS.get(kind, {}).get(key)


def control(dev, key):
    c = dev.get('control') or {}
    if c.get(key) is not None:
        return c[key]
    if key in LEGACY_CONTROL:
        return LEGACY_CONTROL[key]
    spec = _param_spec(dev['kind'], key, control=True)
    return spec['defaut'] if spec else None


def extensions(dev):
    """(débord latéral à gauche, à droite) en m ; (None, None) : débord latéral infini (clés absentes)."""
    if dev['kind'] == 'loggia':
        return 0.0, 0.0
    ext = param(dev, 'extension_m')
    left, right = param(dev, 'extension_gauche_m'), param(dev, 'extension_droite_m')
    if ext is None and left is None and right is None:
        return None, None
    base = float(ext) if ext is not None else 0.0
    return (float(left) if left is not None else base), (float(right) if right is not None else base)


# ── Géométrie solaire ─────────────────────────────────────────────────────────

def _relative_angles(az, el, facade_az_deg):
    gamma = np.angle(np.exp(1j * (az - np.radians(facade_az_deg))))  # écart d'azimut, [-π, π] ; > 0 : soleil à gauche
    front = np.cos(gamma) > 1e-6
    tan_profile = np.where(front, np.tan(np.clip(el, 0, None)) / np.maximum(np.cos(gamma), 1e-6), np.inf)
    return gamma, front, tan_profile


def _covered(shift, width, left, right):
    """Part de la largeur d'une baie sous un débord décalé de `shift` (> 0 : ombre décalée vers la droite, soleil à
    gauche). Débord de −left à width + right le long de la baie."""
    lo = np.maximum(0.0, shift - left)
    hi = np.minimum(width, shift + width + right)
    return np.clip((hi - lo) / max(width, 1e-6), 0.0, 1.0)


def overhang_fraction(az, el, facade_az_deg, depth, gap, height, extension=None, width=None, tilt_deg=0.0,
                      ext_left=None, ext_right=None):
    """Fraction ensoleillée (direct) d'une baie sous un débord. `extension` : débord latéral des deux côtés ;
    `ext_left`/`ext_right` le précisent côté par côté ; tout absent (ou `width` absent) : débord latéral infini."""
    az = np.atleast_1d(np.asarray(az, dtype=float))
    el = np.atleast_1d(np.asarray(el, dtype=float))
    gamma, front, tp = _relative_angles(az, el, facade_az_deg)
    lateral = width is not None and (extension is not None or ext_left is not None or ext_right is not None)
    tb = math.tan(math.radians(tilt_deg))
    tp_f = np.where(np.isfinite(tp), tp, 1e9)
    slope = tp_f + tb
    x_max = np.where(slope > 1e-9, depth * slope - gap, -1.0)
    h_sh = np.clip(x_max, 0.0, height)                        # hauteur à l'ombre depuis le haut de la baie
    shaded = h_sh / max(height, 1e-6)
    if lateral:
        base = float(extension or 0.0)
        left = float(ext_left) if ext_left is not None else base
        right = float(ext_right) if ext_right is not None else base
        k = (np.arange(N_INTEGRATION) + 0.5) / N_INTEGRATION
        x = h_sh[:, None] * k[None, :]
        d_star = (x + gap) / np.maximum(slope[:, None], 1e-9)
        shift = d_star * np.tan(np.clip(gamma, -1.55, 1.55))[:, None]
        shaded = shaded * _covered(shift, float(width), left, right).mean(axis=1)
    return np.where(front & (el > 0), 1.0 - np.clip(shaded, 0.0, 1.0), 0.0)


def fins_fraction(az, el, facade_az_deg, fin_depth, width, fin_height=None, window_height=1.5, sides='deux'):
    """Fraction ensoleillée (direct) d'une baie entre des joues : seule la joue côté soleil porte ombre.
    `fin_height` (depuis l'appui) : None = joues de toute hauteur."""
    az = np.atleast_1d(np.asarray(az, dtype=float))
    el = np.atleast_1d(np.asarray(el, dtype=float))
    gamma, front, tp = _relative_angles(az, el, facade_az_deg)
    tg = np.abs(np.tan(np.clip(gamma, -1.55, 1.55)))
    if sides == 'gauche':
        active = gamma > 0
    elif sides == 'droite':
        active = gamma < 0
    else:
        active = np.ones_like(gamma, dtype=bool)
    u_max = fin_depth * tg                                     # largeur à l'ombre d'une joue de pleine hauteur
    if fin_height is None:
        shaded_w = np.clip(u_max / max(width, 1e-6), 0.0, 1.0)
    else:
        k = (np.arange(N_INTEGRATION) + 0.5) / N_INTEGRATION
        z = window_height * k
        tp_f = np.maximum(np.where(np.isfinite(tp), tp, 1e9), 1e-9)
        lim = np.clip(fin_height - z, 0.0, None)[None, :] * tg[:, None] / tp_f[:, None]
        shaded_w = np.clip(np.minimum(u_max[:, None], lim) / max(width, 1e-6), 0.0, 1.0).mean(axis=1)
    shaded_w = np.where(active, shaded_w, 0.0)
    return np.where(front & (el > 0), 1.0 - shaded_w, 0.0)


def diffuse_factor(depth=0.0, gap=0.0, height=1.5, fin_depth=0.0, width=1.2, tilt_deg=0.0, fin_height=None):
    """Part du ciel vue par la baie qui reste visible (approximation : angle masqué au milieu de la baie)."""
    f = 1.0
    if depth:
        edge = gap - depth * math.tan(math.radians(tilt_deg)) + height / 2.0
        theta = math.atan2(depth, max(edge, 0.05))
        f *= 1.0 - 0.5 * math.sin(theta)
    if fin_depth:
        phi = math.atan(2 * fin_depth / max(width, 1e-6))
        cover = 1.0 if fin_height is None else min(1.0, fin_height / max(height, 1e-6))
        f *= 1.0 - 0.25 * math.sin(phi) * cover
    return float(f)


def profile_angle(az, el, facade_az_deg):
    """Angle de profil φ (rad) et masque « soleil devant la façade, au-dessus de l'horizon »."""
    _g, front, tp = _relative_angles(az, el, facade_az_deg)
    return np.arctan(np.where(np.isfinite(tp), tp, 1e9)), front & (el > 0)


def slat_open_fraction(phi, psi, width, spacing, thickness=0.0):
    """ISO 15099 § 7 (géométrie seule) : part du rayonnement d'angle de profil φ qui passe entre des lames."""
    phi = np.asarray(phi, dtype=float)
    a = phi + np.asarray(psi, dtype=float)
    shadow = width * np.abs(np.sin(a)) + thickness * np.abs(np.cos(a))
    return np.clip(1.0 - shadow / np.maximum(spacing * np.cos(phi), 1e-9), 0.0, 1.0)


def slat_beam(phi, psi, width, spacing, thickness, transmission):
    f = slat_open_fraction(phi, psi, width, spacing, thickness)
    return f + (1.0 - f) * transmission


_PHI = np.linspace(-np.pi / 2, np.pi / 2, 91)
_W_PHI = np.cos(_PHI) * (_PHI[1] - _PHI[0])
_W_PHI[[0, -1]] *= 0.5                                      # trapèzes


def slat_diffuse(psi, width, spacing, thickness, transmission):
    """Transmission du diffus isotrope (ciel et sol) : ∫ f(φ) cos φ dφ / 2. `psi` scalaire ou tableau horaire."""
    psi_a = np.atleast_1d(np.asarray(psi, dtype=float))
    uniq, inv = np.unique(np.round(psi_a, 3), return_inverse=True)
    vals = slat_beam(_PHI[None, :], uniq[:, None], width, spacing, thickness, transmission)
    out = (vals * _W_PHI[None, :]).sum(axis=1) / 2.0
    res = out[inv]
    return res if np.ndim(psi) else float(res[0])


def tracking_tilt(phi, width, spacing):
    """Inclinaison minimale qui coupe le direct (suivi du soleil), bornée à [0, 90°]."""
    phi = np.asarray(phi, dtype=float)
    x = np.clip(spacing * np.cos(phi) / max(width, 1e-9), -1.0, 1.0)
    psi = np.where(spacing * np.cos(phi) >= width, np.pi / 2, np.arcsin(x) - phi)
    return np.clip(psi, 0.0, np.pi / 2)


# ── Pilotage ──────────────────────────────────────────────────────────────────

def month_of(hidx):
    """Mois (1-12) de chaque heure, la série commençant le 1er janvier (convention des années types)."""
    days = (np.asarray(hidx) // 24) % 365
    edges = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    return np.searchsorted(edges, days, side='right')


def in_months(months, start, end):
    """Masque des mois compris entre start et end inclus (cyclique : novembre → mars)."""
    v = np.asarray(months)
    if start <= end:
        return (v >= start) & (v <= end)
    return (v >= start) | (v <= end)


def mobile_position(dev, hidx, irradiance, t_ext, occupied=None, el=None):
    """Position horaire (0 ouvert … 1 fermé) d'une protection mobile selon son pilotage."""
    hidx = np.asarray(hidx)
    mode = control(dev, 'mode') or 'planning'
    pos_closed = float(control(dev, 'position'))
    n = len(hidx)
    if mode == 'fixe':
        pos = np.full(n, pos_closed)
    elif mode == 'auto':
        seuil = float(control(dev, 'irradiance_w_m2'))
        t_min = float(control(dev, 't_ext_min_c'))
        closed = (np.asarray(irradiance) > seuil) & (np.asarray(t_ext) > t_min)
        if control(dev, 'close_when_absent') and occupied is not None:
            closed = closed | (~occupied & (np.asarray(t_ext) > t_min))
        pos = closed * pos_closed
    else:
        c = dev.get('control') or {}
        hour = hidx % 24
        if c.get('heure_debut') is not None or c.get('heure_fin') is not None:
            h0, h1 = int(control(dev, 'heure_debut')), int(control(dev, 'heure_fin'))
            if h0 == h1:
                in_hours = np.zeros(n, dtype=bool)
            elif h0 < h1:
                in_hours = (hour >= h0) & (hour < h1)
            else:
                in_hours = (hour >= h0) | (hour < h1)
            m_ok = in_months(month_of(hidx), int(control(dev, 'mois_debut')), int(control(dev, 'mois_fin')))
            pos = np.where(in_hours & m_ok, pos_closed, 0.0)
        else:                                                 # planning d'avant le lot 36 : listes d'heures et de mois
            hours = set(int(h) for h in (c.get('hours') or []))
            if not hours:
                pos = np.zeros(n)
            else:
                pos = np.where(np.isin(hour, list(hours)), pos_closed, 0.0)
                months = c.get('months')
                if months:
                    pos = np.where(np.isin(month_of(hidx), months), pos, 0.0)
    if control(dev, 'fermeture_nocturne') and el is not None:
        pos = np.where(np.asarray(el) <= 0, 1.0, pos)
    return np.asarray(pos, dtype=float)


# ── Effets sur une orientation ────────────────────────────────────────────────

def _fixed_factors(dev, az, el, facade_az):
    kind = dev['kind']
    h, w = float(param(dev, 'window_height_m')), float(param(dev, 'window_width_m'))
    fb = np.ones(len(az))
    fd = 1.0
    if kind in OVERHANGS:
        depth, gap = float(param(dev, 'depth_m')), float(param(dev, 'gap_m'))
        tilt = float(param(dev, 'inclinaison_deg')) if kind != 'loggia' else 0.0
        left, right = extensions(dev)
        if depth > 0:
            fb = fb * overhang_fraction(az, el, facade_az, depth, gap, h, width=w if left is not None else None,
                                        tilt_deg=tilt, ext_left=left, ext_right=right)
            fd *= diffuse_factor(depth, gap, h, tilt_deg=tilt)
    if kind in FINS:
        fin = float(param(dev, 'fin_depth_m'))
        fh = param(dev, 'fin_height_m') if kind == 'joues' else None
        sides = param(dev, 'fin_cote') if kind == 'joues' else 'deux'
        if fin > 0:
            fb = fb * fins_fraction(az, el, facade_az, fin, w, None if fh is None else float(fh), h, sides or 'deux')
            fd *= diffuse_factor(0.0, 0.0, h, fin, w, fin_height=None if fh is None else float(fh))
    return fb, fd


def _slat_factors(dev, az, el, facade_az, tracking=None):
    """(direct, diffus) au travers des lames, heure par heure."""
    phi, front = profile_angle(az, el, facade_az)
    L, s = float(param(dev, 'lame_largeur_m')), float(param(dev, 'lame_entraxe_m'))
    t, tau = float(param(dev, 'lame_epaisseur_m')), float(param(dev, 'lame_transmission'))
    psi = math.radians(float(param(dev, 'lame_inclinaison_deg')))
    if tracking is not None and np.any(tracking):
        psi = np.where(tracking & front, tracking_tilt(phi, L, s), psi)
    fd = slat_diffuse(psi, L, s, t, tau)
    fb = np.where(front, slat_beam(phi, psi, L, s, t, tau), 1.0)
    return fb, fd


def uses_slats(dev):
    """BSO enregistré avant le lot 36 (sans clé de lame) : couple de transmissions fixes, comme avant."""
    if dev['kind'] == 'brise_soleil_fixe':
        return True
    return dev['kind'] == 'bso' and any(k.startswith('lame_') for k in (dev.get('params') or {}))


def orientation_effects(devices, orientation, facade_az, az, el, hidx, total_irradiance, t_ext, g_window, occupied=None):
    """Effets horaires des protections sur les baies d'une orientation :
    {'beam', 'diffuse' : facteurs (T,), 'r_add' : résistance ajoutée aux vitrages (m²·K/W, T,),
     'opaque' : bandes de mur ombrées par les débords (voir `opaque_zone`)}."""
    T = len(az)
    fb = np.ones(T)
    fd = np.ones(T)
    r_add = np.zeros(T)
    opaque = []
    for dev in devices:
        if dev.get('orientation') not in (orientation, 'all'):
            continue
        kind = dev['kind']
        if kind in FIXED and kind not in SLATS:
            b, d = _fixed_factors(dev, az, el, facade_az)
            fb, fd = fb * b, fd * d
            if kind in OVERHANGS and float(param(dev, 'depth_m')) > 0:
                h, w = float(param(dev, 'window_height_m')), float(param(dev, 'window_width_m'))
                gap, depth = float(param(dev, 'gap_m')), float(param(dev, 'depth_m'))
                tilt = float(param(dev, 'inclinaison_deg')) if kind != 'loggia' else 0.0
                left, right = extensions(dev)
                band = gap + h
                width = w + (left or 0.0) + (right or 0.0)
                ob = overhang_fraction(az, el, facade_az, depth, 0.0, band, tilt_deg=tilt)
                opaque.append({'band_h_m': band, 'unit_window_m2': h * w, 'continuous': left is None,
                               'unit_m2': max(band * width - h * w, 0.0), 'beam': ob,
                               'diffuse': diffuse_factor(depth, 0.0, band, tilt_deg=tilt)})
        elif kind == 'brise_soleil_fixe':
            b, d = _slat_factors(dev, az, el, facade_az)
            cov = float(param(dev, 'couverture'))
            fb = fb * (1.0 - cov + cov * b)
            fd = fd * (1.0 - cov + cov * d)
        elif kind in MOBILE:
            pos = mobile_position(dev, hidx, total_irradiance, t_ext, occupied, el)
            if uses_slats(dev):
                track = (pos > 0) if control(dev, 'lame_suivi_soleil') else None
                b, d = _slat_factors(dev, az, el, facade_az, track)
            else:
                b, d = float(param(dev, 'fs_dir')), float(param(dev, 'fs_dif'))
            fb = fb * (1.0 - pos * (1.0 - b))
            fd = fd * (1.0 - pos * (1.0 - d))
            r_add = r_add + pos * float(param(dev, 'delta_r_m2k_w'))
        elif kind in GLAZING:
            g = (dev.get('params') or {}).get('g')
            ratio = float(g) / g_window if (g is not None and g_window > 0) else 1.0
            fb, fd = fb * ratio, fd * ratio
        elif kind in VEGETATION:
            summer, winter = float(param(dev, 't_summer')), float(param(dev, 't_winter'))
            leaves = in_months(month_of(hidx), int(param(dev, 'mois_feuilles_debut')), int(param(dev, 'mois_feuilles_fin')))
            fb = fb * np.where(leaves, summer, winter)
            fd = fd * np.where(leaves, 0.5 + summer / 2, 0.5 + winter / 2)
    return {'beam': fb, 'diffuse': fd, 'r_add': r_add, 'opaque': opaque}


def factors(devices, orientation, facade_az, az, el, hidx, total_irradiance, t_ext, g_window, occupied=None):
    """(facteur sur le direct, facteur sur le diffus) horaires pour les baies d'une orientation."""
    e = orientation_effects(devices, orientation, facade_az, az, el, hidx, total_irradiance, t_ext, g_window, occupied)
    return e['beam'], e['diffuse']


def opaque_zone(effects, opaque_m2, window_m2, wall_height_m):
    """Bandes de mur ombrées par les débords d'une façade : [(surface m², facteur direct, facteur diffus)]."""
    out = []
    left = max(opaque_m2, 0.0)
    for z in effects['opaque']:
        if left <= 0:
            break
        if z['continuous']:                          # débord filant : bande sur toute la façade
            area = (opaque_m2 + window_m2) * min(z['band_h_m'] / max(wall_height_m, 1e-6), 1.0) - window_m2
        else:
            area = window_m2 / max(z['unit_window_m2'], 1e-6) * z['unit_m2']
        area = min(max(area, 0.0), left)
        if area > 0:
            out.append((area, z['beam'], z['diffuse']))
            left -= area
    return out
