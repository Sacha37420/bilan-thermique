"""Gabarits du lab (I6.e) et questions du constructeur guidé (I6.g `GET /api/rendu/gabarits/`).

Les gabarits sont des `RapportModele` sans parc (`portfolio=None`, `par_defaut=True`), tenus à jour par
`assurer()` : la spécification fait foi ici, dans le code (une modification de gabarit est un déploiement).

Chantier bt-trois-niveaux (I5) : la fiche et la synthèse montrent les trois niveaux de calcul (réel et bouquets) par
les questions `niveaux_*` (dimension « niveau » en abscisse ou en couleur) ; les autres blocs, sans niveau précisé,
lisent chaque bâtiment au niveau le plus fin disponible et à jour (`donnees.AUTO`), ce que dit leur résumé.
"""

from ..models import RapportModele
from . import spec as spec_mod

TOUTES = ['parc', 'batiment']


def _g(forme, x, y, series='reel_et_bouquets', agregation=None, titre='', **kw):
    g = {'forme': forme, 'x': x, 'y': y, 'couleur': None, 'taille': None, 'facettes': None, 'series': series,
         'filtre': {}, 'titre': titre, 'axe_y_log': False}
    if agregation:
        g['agregation'] = agregation
    g.update(kw)
    return g


QUESTIONS = [
    ('reel_vs_bouquets', 'Combien chaque bouquet fait-il gagner sur le besoin de chauffage ?', TOUTES,
     _g('barres', 'bouquet', ['heating_kwh_m2'], agregation='moyenne', titre='Besoin de chauffage : réel et potentiels')),
    ('dh_bouquets', 'Quel bouquet réduit le plus l’inconfort d’été (DH RE2020) ?', TOUTES,
     _g('barres', 'bouquet', ['dh_re2020', 'dh_26'], agregation='moyenne', titre='Degrés-heures d’inconfort : réel et potentiels')),
    ('besoins_mensuels', 'Comment le besoin de chauffage se répartit-il dans l’année ?', TOUTES,
     _g('courbes', 'mois', ['mensuel.heating_kwh'], agregation='somme', titre='Besoins de chauffage mensuels')),
    ('dh_mensuels', 'Quels mois concentrent l’inconfort d’été ?', TOUTES,
     _g('barres_groupees', 'mois', ['mensuel.dh_re2020'], agregation='somme', titre='Degrés-heures d’inconfort par mois')),
    ('aires_mensuelles', 'Chauffage et refroidissement, mois par mois ?', TOUTES,
     _g('aires', 'mois', ['mensuel.heating_kwh', 'mensuel.cooling_kwh'], series='reel', agregation='somme',
        titre='Besoins mensuels de chauffage et de refroidissement')),
    ('postes_bilan', 'Par où la chaleur part-elle, et que changent les bouquets ?', TOUTES,
     _g('barres_groupees', 'poste_bilan', ['bilan.kwh_m2'], agregation='moyenne', titre='Postes du bilan de chauffage')),
    ('cascade_actions', 'Du réel au potentiel : quel poste fait le gain ?', TOUTES,
     _g('cascade', 'poste_bilan', ['heating_kwh_m2'], series='bouquets', agregation='moyenne',
        titre='Du réel au potentiel, poste par poste')),
    ('nuage_besoin_dh', 'Les bâtiments qui chauffent le plus sont-ils ceux qui ont le plus chaud l’été ?', TOUTES,
     _g('nuage', 'heating_kwh_m2', ['dh_re2020'], series='reel', couleur='usage', titre='Besoin de chauffage et inconfort d’été')),
    ('bulles_surface', 'Où sont les gros bâtiments les plus consommateurs ?', ['parc'],
     _g('bulles', 'heating_kwh_m2', ['primary_kwh_m2'], series='reel', taille='identite.surface_m2', couleur='usage',
        titre='Besoin, énergie primaire et surface')),
    ('boite_usage', 'Comment le besoin de chauffage varie-t-il selon l’usage ?', ['parc'],
     _g('boite', 'usage', ['heating_kwh_m2'], series='reel', titre='Dispersion du besoin par usage')),
    ('violon_bouquets', 'Quelle est la dispersion du besoin, réel et potentiels ?', ['parc'],
     _g('violon', 'bouquet', ['heating_kwh_m2'], titre='Dispersion du besoin : réel et potentiels')),
    ('histogramme_besoin', 'Combien de bâtiments par tranche de besoin ?', ['parc'],
     _g('histogramme', 'heating_kwh_m2', [], series='reel', titre='Répartition du besoin de chauffage')),
    ('periode_construction', 'L’âge du bâtiment pèse-t-il sur le besoin ?', ['parc'],
     _g('barres', 'periode_construction', ['heating_kwh_m2'], agregation='moyenne', titre='Besoin par période de construction')),
    ('energie_usages', 'Quels usages consomment l’énergie ?', TOUTES,
     _g('camembert', 'poste_energie', ['energie.poste_kwh'], series='reel', agregation='somme',
        titre='Énergie finale par usage')),
    ('radar_bouquets', 'Quel bouquet est le plus équilibré entre énergie, carbone et confort ?', TOUTES,
     _g('radar', 'bouquet', ['heating_kwh_m2', 'primary_kwh_m2', 'co2_kg_m2', 'dh_re2020'], agregation='moyenne',
        titre='Réel et potentiels sur quatre indicateurs')),
    ('confort_horaire', 'À quelle heure et quel mois fait-il le plus chaud dedans ?', ['batiment'],
     _g('carte_chaleur', 'heure', ['horaire.t_op_c'], series='reel', couleur='mois', agregation='moyenne',
        titre='Température opérative moyenne par heure et par mois')),
    ('actions_familles', 'Quelles familles d’actions chaque bouquet applique-t-il ?', TOUTES,
     _g('barres_empilees', 'bouquet', ['actions.nombre'], series='bouquets', couleur='famille_action',
        agregation='somme', titre='Actions appliquées par famille')),
    # Chantier bt-trois-niveaux : réel et bouquets sur les trois niveaux de calcul.
    ('niveaux_reel', 'Que donnent les trois niveaux de calcul (N0, N1, N2) pour l’état actuel ?', TOUTES,
     _g('barres', 'niveau', ['heating_kwh_m2', 'primary_kwh_m2'], series='reel', agregation='moyenne',
        titre='État actuel : besoin et énergie primaire par niveau de calcul')),
    ('niveaux_bouquets', 'Les gains des bouquets se confirment-ils d’un niveau de calcul à l’autre ?', TOUTES,
     _g('barres_groupees', 'bouquet', ['heating_kwh_m2'], couleur='niveau', agregation='moyenne',
        titre='Besoin de chauffage : réel et potentiels par niveau de calcul')),
    ('niveaux_dh', 'L’inconfort d’été, réel et bouquets, selon le niveau de calcul (N1, N2) ?', TOUTES,
     _g('barres_groupees', 'bouquet', ['dh_re2020'], couleur='niveau', agregation='moyenne',
        titre='DH RE2020 : réel et potentiels par niveau de calcul')),
    ('parallele_parc', 'Quels bâtiments cumulent besoin, carbone et inconfort ?', ['parc'],
     _g('parallele', 'batiment', ['heating_kwh_m2', 'co2_kg_m2', 'dh_re2020', 'confidence'], series='reel',
        titre='Bâtiments sur quatre indicateurs')),
]


def questions():
    return [{'cle': c, 'question': q, 'portees': list(p), 'graphe': spec_mod.public_graphe(spec_mod.valider_graphe(g))}
            for c, q, p, g in QUESTIONS]


def _q(cle):
    for c, _q2, _p, g in QUESTIONS:
        if c == cle:
            return {'type': 'graphe', 'graphe': dict(g)}
    raise KeyError(cle)


MISE_EN_PAGE = {'format': 'A4', 'orientation': 'portrait', 'sommaire': True, 'numeros': True}

FICHE = {
    'version': 1, 'portee': 'batiment', 'titre': 'Fiche d’identité', 'mise_en_page': MISE_EN_PAGE,
    'sections': [
        {'titre': 'Le bâtiment', 'blocs': [{'type': 'fiche', 'sections': ['identite', 'geometrie', 'enveloppe',
                                                                          'systemes', 'meteo', 'environnement']}]},
        {'titre': 'Résultats et potentiels', 'blocs': [
            {'type': 'fiche', 'sections': ['resultats', 'potentiels']},
            {'type': 'indicateurs', 'cles': ['heating_kwh_m2', 'primary_kwh_m2', 'co2_kg_m2', 'dh_re2020', 'heures_inconfort'],
             'series': 'reel_et_bouquets'},
            _q('reel_vs_bouquets'), _q('dh_bouquets')]},
        {'titre': 'Trois niveaux de calcul', 'blocs': [
            {'type': 'texte', 'contenu': 'Le même bâtiment, réel et bouquets, calculé en **N0** (mensuel), **N1** '
                                         '(horaire simplifié) et **N2** (détaillé par paroi). Ailleurs dans ce document, '
                                         'chaque valeur est lue au niveau le plus fin disponible et à jour.'},
            _q('niveaux_reel'), _q('niveaux_bouquets'), _q('niveaux_dh')]},
        {'titre': 'Au fil de l’année', 'blocs': [_q('besoins_mensuels'), _q('dh_mensuels')]},
        {'titre': 'Bilan et gains', 'blocs': [_q('postes_bilan'), _q('cascade_actions'),
                                              {'type': 'tableau', 'source': 'actions', 'colonnes': ['bouquet', 'action',
                                               'famille_action', 'application', 'raison'], 'tri': None, 'ordre': 'asc',
                                               'limite': 100}]},
        {'titre': 'Pertes et gains par paroi (N2)', 'blocs': [
            {'type': 'texte', 'contenu': 'Calcul détaillé par paroi : **pertes d’hiver** puis **gains d’été**, '
                                         'triangle par triangle de l’enveloppe réellement calculée.'},
            {'type': 'vue3d', 'calcul': 'dernier_N2', 'grandeur': 'pertes_hiver_kwh_m2', 'camera': 'iso'},
            {'type': 'vue3d', 'calcul': 'dernier_N2', 'grandeur': 'gains_ete_kwh_m2', 'camera': 'sud'}]},
        {'titre': 'Améliorations et sources', 'blocs': [{'type': 'fiche', 'sections': ['ameliorations', 'sources']}]},
    ]}

SYNTHESE = {
    'version': 1, 'portee': 'parc', 'titre': 'Synthèse de parc', 'mise_en_page': {**MISE_EN_PAGE, 'orientation': 'paysage'},
    'sections': [
        {'titre': 'Le parc en chiffres', 'blocs': [
            {'type': 'indicateurs', 'cles': ['heating_kwh', 'heating_kwh_m2', 'primary_kwh_m2', 'co2_kg', 'dh_re2020'],
             'series': 'reel_et_bouquets'},
            {'type': 'carte', 'indicateur': 'heating_kwh_m2', 'series': 'reel'}]},
        {'titre': 'Réel et potentiels', 'blocs': [_q('reel_vs_bouquets'), _q('dh_bouquets'), _q('cascade_actions')]},
        {'titre': 'Trois niveaux de calcul', 'blocs': [_q('niveaux_reel'), _q('niveaux_bouquets'), _q('niveaux_dh')]},
        {'titre': 'Lecture du parc', 'blocs': [_q('nuage_besoin_dh'), _q('boite_usage'), _q('periode_construction')]},
        {'titre': 'Au fil de l’année', 'blocs': [_q('besoins_mensuels'), _q('postes_bilan')]},
        {'titre': 'Bâtiments', 'blocs': [
            {'type': 'saut_de_page'},
            {'type': 'tableau', 'source': 'batiments', 'colonnes': ['batiment', 'usage', 'annee', 'heating_kwh_m2',
                                                                    'dh_re2020', 'classe_dpe', 'approfondissement',
                                                                    'niveau_calcul'],
             'tri': 'heating_kwh_m2', 'ordre': 'desc', 'limite': 100},
            {'type': 'tableau', 'source': 'potentiels', 'colonnes': None, 'tri': 'batiment', 'ordre': 'asc',
             'limite': 200}]},
    ]}

GABARITS = [('Fiche d’identité', 'batiment', FICHE), ('Synthèse de parc', 'parc', SYNTHESE)]


def assurer():
    """Crée ou met à jour les gabarits du lab ; rend la liste (ordre fixe)."""
    out = []
    for nom, portee, spec in GABARITS:
        clean = spec_mod.valider(spec)
        m = RapportModele.objects.filter(portfolio__isnull=True, nom=nom).order_by('pk').first()
        if m is None:
            m = RapportModele.objects.create(portfolio=None, nom=nom, portee=portee, spec=clean, par_defaut=True)
        elif m.spec != clean or m.portee != portee or not m.par_defaut:
            m.spec, m.portee, m.par_defaut = clean, portee, True
            m.save(update_fields=['spec', 'portee', 'par_defaut', 'modifie_le'])
        out.append(m)
    return out
