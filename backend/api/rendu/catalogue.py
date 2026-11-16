"""Dimensions et mesures du constructeur de graphes (I6.a, formes I6.g). Listes construites ici, servies par
`GET /api/rendu/dimensions/` et `/mesures/` — aucune recopie côté Angular.

Une mesure appartient à un **grain** (ce que compte une ligne de données) : `batiment` (une ligne par bâtiment ×
série), `mois`, `heure` (mois × heure du jour), `poste` (postes du bilan de chauffage), `poste_energie`
(consommation par usage), `orientation`, `action` (actions d'un bouquet). Les dimensions communes
(bâtiment, usage, période, classe DPE…) existent à tous les grains ; les autres n'existent qu'au leur. Un graphe
ne mélange pas deux grains (refus 400 fixe).
"""

from .. import facts

FORMES = ['barres', 'barres_empilees', 'barres_groupees', 'courbes', 'aires', 'nuage', 'bulles', 'boite', 'violon',
          'histogramme', 'camembert', 'radar', 'carte_chaleur', 'cascade', 'parallele']
FORMES_NUMERIQUES_X = ('nuage', 'bulles', 'histogramme')   # x peut y être une mesure (dimension « ordonnee » numérique)
PORTEES = ['parc', 'batiment']
TOUTES = ['parc', 'batiment']

MOIS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
PERIODES = [(None, 1948, 'Avant 1949'), (1949, 1974, '1949-1974'), (1975, 1989, '1975-1989'),
            (1990, 2005, '1990-2005'), (2006, 2012, '2006-2012'), (2013, None, 'Après 2012')]
CLASSES = list('ABCDEFG')
APPROFONDISSEMENTS = ['Automatique', 'Complété', 'Documenté', 'Mesuré', 'Expert']
NIVEAUX = {'N0': 'N0 — mensuel', 'N1': 'N1 — horaire simplifié', 'N2': 'N2 — détaillé par paroi'}
POSTES = [('transmission_kwh', 'Déperditions par les parois', 'perte'),
          ('ventilation_kwh', 'Déperditions par renouvellement d’air', 'perte'),
          ('solar_windows_kwh', 'Apports solaires des baies', 'apport'),
          ('solar_opaque_kwh', 'Apports solaires des parois opaques', 'apport'),
          ('internal_kwh', 'Apports internes', 'apport')]
POSTES_ENERGIE = {'chauffage': 'Chauffage', 'ecs': 'Eau chaude sanitaire', 'refroidissement': 'Refroidissement',
                  'eclairage': 'Éclairage', 'auxiliaires': 'Auxiliaires'}
ORIENTATIONS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO', 'H']
APPLICATION = ['Appliquée', 'Écartée']

# ── Dimensions ───────────────────────────────────────────────────────────────
# (cle, libelle, type, grain | None (commune), portees)
_DIMS = [
    ('batiment', 'Bâtiment', 'categorie', None, TOUTES),
    ('usage', 'Usage', 'categorie', None, TOUTES),
    ('periode_construction', 'Période de construction', 'ordonnee', None, TOUTES),
    ('classe_dpe', 'Classe DPE affichée', 'ordonnee', None, TOUTES),
    ('etiquette_modele', 'Étiquette du modèle', 'ordonnee', None, TOUTES),
    ('approfondissement', 'Approfondissement de la fiche', 'ordonnee', None, TOUTES),
    ('bouquet', 'Série (réel ou bouquet)', 'categorie', None, TOUTES),
    ('niveau', 'Niveau de calcul', 'ordonnee', None, TOUTES),
    ('mois', 'Mois', 'temps', 'mois', TOUTES),
    ('heure', 'Heure du jour', 'temps', 'heure', TOUTES),
    ('poste_bilan', 'Poste du bilan de chauffage', 'categorie', 'poste', TOUTES),
    ('poste_energie', 'Usage de l’énergie', 'categorie', 'poste_energie', TOUTES),
    ('orientation', 'Orientation des baies', 'categorie', 'orientation', TOUTES),
    ('action', 'Action', 'categorie', 'action', TOUTES),
    ('famille_action', 'Famille d’actions', 'categorie', 'action', TOUTES),
    ('application', 'Action appliquée ou écartée', 'categorie', 'action', TOUTES),
]
DIM_GRAIN = {c: g for c, _l, _t, g, _p in _DIMS}
DIM_LIBELLE = {c: l for c, l, _t, _g, _p in _DIMS}
DIM_TYPE = {c: t for c, _l, t, _g, _p in _DIMS}

# ── Mesures ──────────────────────────────────────────────────────────────────
# Grain « bâtiment » : noms I3 (figés par le contrat) — libellés repris de GET /api/indicateurs/ quand P l'a livré ;
# ceux-ci servent tant qu'il ne l'est pas (et pour les clés qu'il ne liste pas).
_I3_DEFAUT = [
    ('heating_kwh_m2', 'Besoin de chauffage', 'kWh/m²·an', 'energie', False),
    ('heating_kwh', 'Besoin de chauffage', 'kWh/an', 'energie', True),
    ('cooling_kwh_m2', 'Besoin de refroidissement', 'kWh/m²·an', 'energie', False),
    ('cooling_kwh', 'Besoin de refroidissement', 'kWh/an', 'energie', True),
    ('heating_kw_max', 'Puissance de chauffage maximale', 'kW', 'energie', True),
    ('final_kwh_m2', 'Énergie finale', 'kWh/m²·an', 'energie', False),
    ('primary_kwh_m2', 'Énergie primaire', 'kWh/m²·an', 'energie', False),
    ('final_kwh', 'Énergie finale', 'kWh/an', 'energie', True),
    ('primary_kwh', 'Énergie primaire', 'kWh/an', 'energie', True),
    ('co2_kg_m2', 'Émissions de CO₂', 'kg/m²·an', 'energie', False),
    ('co2_kg', 'Émissions de CO₂', 'kg/an', 'energie', True),
    ('cost_eur', 'Coût de l’énergie', '€/an', 'energie', True),
    ('dh_re2020', 'Degrés-heures d’inconfort (RE2020)', '°C·h', 'confort', False),
    ('dh_26', 'Degrés-heures au-dessus de 26 °C', '°C·h', 'confort', False),
    ('heures_inconfort', 'Heures d’inconfort (RE2020)', 'h', 'confort', False),
    ('hours_over_26', 'Heures au-dessus de 26 °C', 'h', 'confort', False),
    ('hours_over_28', 'Heures au-dessus de 28 °C', 'h', 'confort', False),
    ('confidence', 'Confiance', '', 'donnees', False),
    ('completeness', 'Complétude', '', 'donnees', False),
]
FAITS_NUMERIQUES = [k.key for k in facts.CATALOGUE if k.kind in ('number', 'int')]
# Grains autres que « bâtiment » : (cle, libelle, unite, famille, grain, additif)
_MESURES_GRAIN = [
    ('mensuel.heating_kwh', 'Besoin de chauffage mensuel', 'kWh', 'energie', 'mois', True),
    ('mensuel.cooling_kwh', 'Besoin de refroidissement mensuel', 'kWh', 'energie', 'mois', True),
    ('mensuel.dh_re2020', 'Degrés-heures d’inconfort mensuels (RE2020)', '°C·h', 'confort', 'mois', True),
    ('mensuel.t_op_max_c', 'Température opérative maximale du mois', '°C', 'confort', 'mois', False),
    ('mensuel.t_ext_moy_c', 'Température extérieure moyenne du mois', '°C', 'confort', 'mois', False),
    ('horaire.t_op_c', 'Température opérative moyenne', '°C', 'confort', 'heure', False),
    ('bilan.kwh', 'Poste du bilan de chauffage', 'kWh/an', 'bilan', 'poste', True),
    ('bilan.kwh_m2', 'Poste du bilan de chauffage', 'kWh/m²·an', 'bilan', 'poste', False),
    ('energie.poste_kwh', 'Énergie finale par usage', 'kWh/an', 'energie', 'poste_energie', True),
    ('solaire.baies_kwh', 'Apports solaires des baies', 'kWh/an', 'bilan', 'orientation', True),
    ('actions.nombre', 'Nombre d’actions', '', 'donnees', 'action', True),
]
NB_BATIMENTS = ('nb_batiments', 'Nombre de bâtiments', '', 'donnees', 'batiment', True)
SERIES_HORS_REEL_GRAINS = ('action',)        # le grain « action » n'existe que pour un bouquet


def _i3_par_cle(liste_i3):
    out = {c: {'libelle': l, 'unite': u, 'famille': f, 'additif': a} for c, l, u, f, a in _I3_DEFAUT}
    for e in liste_i3 or []:
        if not isinstance(e, dict) or not isinstance(e.get('cle'), str):
            continue
        fam = e.get('famille') if e.get('famille') in ('energie', 'confort', 'donnees') else 'donnees'
        out[e['cle']] = {'libelle': str(e.get('libelle') or e['cle']), 'unite': str(e.get('unite') or ''),
                         'famille': fam, 'additif': bool(e.get('additif'))}
    out.pop('t_conf_methode', None)    # texte, pas une mesure
    return out


def mesures(liste_i3=None):
    """{cle: {libelle, unite, famille, grain, additif, source}} — `source` : i3 | fait | grain."""
    out = {}
    for c, d in _i3_par_cle(liste_i3).items():
        out[c] = {**d, 'grain': 'batiment', 'source': 'i3'}
    c, l, u, f, g, a = NB_BATIMENTS
    out[c] = {'libelle': l, 'unite': u, 'famille': f, 'grain': g, 'additif': a, 'source': 'compte'}
    for k in FAITS_NUMERIQUES:
        spec = facts.BY_KEY[k]
        out[k] = {'libelle': spec.label, 'unite': spec.unit or '', 'famille': 'donnees', 'grain': 'batiment',
                  'additif': False, 'source': 'fait'}
    for c, l, u, f, g, a in _MESURES_GRAIN:
        out[c] = {'libelle': l, 'unite': u, 'famille': f, 'grain': g, 'additif': a, 'source': 'grain'}
    return out


def _formes_mesure(grain):
    if grain == 'batiment':
        return list(FORMES)
    # un grain fin : pas de coordonnées parallèles (une ligne = un bâtiment)
    return [f for f in FORMES if f not in ('parallele', 'cascade')]


def liste_mesures(liste_i3=None):
    out = []
    for c, d in mesures(liste_i3).items():
        formes = _formes_mesure(d['grain'])
        if c in ('heating_kwh', 'heating_kwh_m2'):
            formes = sorted(set(formes) | {'cascade'}, key=FORMES.index)
        out.append({'cle': c, 'libelle': d['libelle'], 'unite': d['unite'], 'portees': list(TOUTES),
                    'famille': d['famille'], 'formes': formes})
    return out


def liste_dimensions(liste_i3=None):
    out = []
    for c, l, t, g, p in _DIMS:
        # histogramme : abscisse numérique seulement ; cascade : du réel au potentiel, poste par poste
        formes = [f for f in FORMES if f not in ('histogramme', 'cascade') or (f == 'cascade' and c == 'poste_bilan')]
        out.append({'cle': c, 'libelle': l, 'unite': None, 'portees': list(p), 'type': t, 'formes': formes})
    # Mesures du grain « bâtiment » utilisables en abscisse numérique (nuage, bulles, histogramme).
    for c, d in mesures(liste_i3).items():
        if d['grain'] == 'batiment' and c != 'nb_batiments':
            out.append({'cle': c, 'libelle': d['libelle'], 'unite': d['unite'] or None, 'portees': list(TOUTES),
                        'type': 'ordonnee', 'formes': list(FORMES_NUMERIQUES_X)})
    return out


def periode_de(annee):
    if annee is None:
        return None
    for lo, hi, lib in PERIODES:
        if (lo is None or annee >= lo) and (hi is None or annee <= hi):
            return lib
    return None


ORDRES = {
    'periode_construction': [p[2] for p in PERIODES], 'classe_dpe': CLASSES, 'etiquette_modele': CLASSES,
    'approfondissement': APPROFONDISSEMENTS, 'niveau': list(NIVEAUX.values()), 'mois': MOIS,
    'heure': [f'{h} h' for h in range(24)], 'poste_bilan': [p[1] for p in POSTES],
    'poste_energie': list(POSTES_ENERGIE.values()), 'orientation': ORIENTATIONS, 'application': APPLICATION,
}
