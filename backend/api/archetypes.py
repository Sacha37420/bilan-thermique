"""Archétypes de bâtiments français (Lot 9, refonte — décision D14).

Valeurs **par défaut** quand rien de mieux n'est connu : période de construction × type (maison,
collectif, tertiaire), d'après les grandes périodes réglementaires françaises et la typologie
TABULA-FR (état « courant », c'est-à-dire en partie rénové — l'état d'origine surestimerait
nettement les déperditions du parc actuel). Ordres de grandeur, pas une table réglementaire :
toutes ces valeurs entrent dans la fiche avec la méthode `defaut` et une confiance faible, et
toute information meilleure (BDNB, DPE, saisie) prime.
"""

PERIODS = [
    # (fin incluse, libellé, mur.u, toiture.u, plancher.u, baies.uw, n50, inertie)
    (1914, 'avant 1915', 1.90, 1.20, 1.20, 3.0, 10.0, 'lourde'),
    (1948, '1915-1948', 1.90, 1.10, 1.20, 3.0, 10.0, 'lourde'),
    (1967, '1949-1967', 1.60, 1.00, 1.10, 3.0, 9.0, 'lourde'),
    (1974, '1968-1974', 1.40, 0.80, 1.00, 2.9, 8.0, 'lourde'),
    (1981, '1975-1981', 0.95, 0.50, 0.90, 2.8, 7.0, 'moyenne'),
    (1989, '1982-1989', 0.70, 0.35, 0.70, 2.6, 6.0, 'moyenne'),
    (2000, '1990-2000', 0.55, 0.28, 0.50, 2.5, 5.0, 'moyenne'),
    (2005, '2001-2005', 0.45, 0.25, 0.40, 2.2, 4.0, 'moyenne'),
    (2012, '2006-2012', 0.36, 0.20, 0.30, 1.8, 3.0, 'moyenne'),
    (9999, 'depuis 2013', 0.23, 0.15, 0.20, 1.4, 1.5, 'moyenne'),
]
DEFAULT_YEAR = 1970

USAGE_DEFAULTS = {
    # usage: (taux de vitrage, consigne chauffage, apports internes W/m², m² par occupant)
    'maison': (0.14, 19.0, 4.0, 35.0),
    'logement_collectif': (0.18, 19.0, 4.5, 30.0),
    'bureau': (0.30, 20.0, 9.0, 15.0),
    'enseignement': (0.25, 19.0, 8.0, 6.0),
    'commerce': (0.25, 19.0, 10.0, 20.0),
    'sante': (0.22, 21.0, 7.0, 25.0),
    'autre': (0.20, 19.0, 5.0, 30.0),
}

VENTILATION_DEFAULT = {
    # type: renouvellement hygiénique (vol/h), rendement de récupération
    'naturelle': (0.6, 0.0),
    'vmc_sf_auto': (0.5, 0.0),
    'vmc_sf_hygro': (0.35, 0.0),
    'vmc_df': (0.5, 0.8),
}

GENERATOR_EFFICIENCY = {
    # rendement saisonnier de génération (COP saisonnier pour les PAC) — repères 3CL simplifiés
    'chaudiere_gaz': 0.85, 'chaudiere_gaz_cond': 0.95, 'chaudiere_fioul': 0.82,
    'pac_air_eau': 2.8, 'pac_air_air': 3.0, 'joule': 1.0, 'reseau_chaleur': 0.95, 'bois': 0.70,
}


def period_slug(label):
    """Identifiant d'une période pour les réglages de parc : « avant 1915 » → « avant_1915 »."""
    return label.replace(' ', '_').replace('-', '_')


PERIOD_SLUGS = [period_slug(row[1]) for row in PERIODS]


def period_for(year):
    y = int(year) if year else DEFAULT_YEAR
    for row in PERIODS:
        if y <= row[0]:
            return row
    return PERIODS[-1]


def ventilation_type_for(year, usage):
    y = int(year) if year else DEFAULT_YEAR
    if y < 1969:
        return 'naturelle'
    if usage in ('bureau', 'enseignement', 'commerce', 'sante') and y >= 2006:
        return 'vmc_df'
    if y >= 1983:
        return 'vmc_sf_hygro' if usage in ('maison', 'logement_collectif') and y >= 2001 else 'vmc_sf_auto'
    return 'vmc_sf_auto'


def period_values(year, cfg=None):
    """(libellé, mur.u, toiture.u, plancher.u, baies.uw, n50, inertie) de la période, avec les valeurs
    du parc (`cfg` = réglages « parametrage » effectifs, clés `archetypes.<période>.…`) si données."""
    _end, label, u_mur, u_toit, u_pb, uw, n50, inertie = period_for(year)
    if cfg:
        slug = period_slug(label)
        g = lambda k, d: cfg.get(f'archetypes.{slug}.{k}', d)  # noqa: E731
        u_mur, u_toit, u_pb, uw, n50 = g('mur_u', u_mur), g('toiture_u', u_toit), g('plancher_u', u_pb), \
            g('baies_uw', uw), g('n50', n50)
    return label, u_mur, u_toit, u_pb, uw, n50, inertie


def ventilation_values(vent, cfg=None):
    debit, recup = VENTILATION_DEFAULT[vent]
    if cfg:
        debit = cfg.get(f'ventilation.{vent}.debit', debit)
        recup = cfg.get(f'ventilation.{vent}.recup', recup)
    return debit, recup


def generator_efficiency(gen, cfg=None):
    eta = GENERATOR_EFFICIENCY[gen]
    return cfg.get(f'rendements.{gen}', eta) if cfg else eta


def defaults(year=None, usage=None, wall_material=None, cfg=None):
    """{clé: valeur} par défaut pour une fiche — tout est `defaut`. `cfg` : réglages « parametrage »
    effectifs du parc du bâtiment (None hors parc : constantes ci-dessus)."""
    usage = usage if usage in USAGE_DEFAULTS else 'autre'
    label, u_mur, u_toit, u_pb, uw, n50, inertie = period_values(year, cfg)
    if wall_material in ('bois', 'pan_de_bois'):
        inertie = 'legere'
    ratio, t_set, gains, m2_occ = USAGE_DEFAULTS[usage]
    vent = ventilation_type_for(year, usage)
    debit, recup = ventilation_values(vent, cfg)
    y = int(year) if year else DEFAULT_YEAR
    out = {
        'mur.u': u_mur, 'toiture.u': u_toit, 'plancher.u': u_pb, 'baies.uw': uw,
        'baies.g': 0.62 if uw >= 2.9 else 0.55 if uw >= 2.0 else 0.50,
        'baies.vitrage': 'simple' if uw >= 4.5 else 'double' if uw >= 2.0 else 'double_fe' if uw >= 1.2 else 'triple',
        'ventilation.n50': n50, 'enveloppe.inertie': inertie,
        'ventilation.type': vent, 'ventilation.debit_vol_h': debit, 'ventilation.rendement_recup': recup,
        'usage.consigne_chauffage_c': t_set, 'usage.apports_w_m2': gains,
        'mur.isolation': 'aucune' if y < 1975 else 'iti',
        'plancher.type': 'terre_plein' if usage == 'maison' else 'sous_sol',
        'systeme.generateur': 'chaudiere_gaz', 'systeme.rendement_generation': generator_efficiency('chaudiere_gaz', cfg),
        'enveloppe.couleur_absorptance': 0.6, 'baies.occultations': usage in ('maison', 'logement_collectif'),
        'identite.mitoyennete': 0.0,
    }
    for o in ('N', 'E', 'S', 'O'):
        out[f'baies.ratio_{o}'] = ratio
    return out, label
