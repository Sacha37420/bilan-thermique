"""Spécification d'un document de rendu (I6.a) : schéma JSON servi à l'interface, validation côté serveur.

Toute erreur est un `SpecError` au message **fixe** (jamais l'entrée recopiée). La validation normalise (valeurs
par défaut posées) : ce qui est enregistré ou rendu est toujours une spécification complète.
"""

import math

from . import catalogue

TYPES_BLOCS = ['texte', 'indicateurs', 'tableau', 'graphe', 'carte', 'vue3d', 'fiche', 'saut_de_page']
SERIES_MOTS = ['reel', 'bouquets', 'reel_et_bouquets']
SOURCES = ['batiments', 'potentiels', 'actions', 'faits', 'calculs']
AGREGATIONS = ['somme', 'moyenne', 'mediane', 'aucune']
FICHE_SECTIONS = ['identite', 'geometrie', 'enveloppe', 'systemes', 'meteo', 'environnement', 'resultats',
                  'potentiels', 'ameliorations', 'sources']
CAMERAS = ['iso', 'sud', 'nord', 'est', 'ouest', 'dessus']
GRANDEURS_3D = ['pertes_hiver_kwh_m2', 'pertes_hiver_kwh', 'gains_ete_kwh_m2', 'gains_ete_kwh', 'solaire_ete_kwh_m2',
                't_si_hiver_moy_c', 't_si_ete_max_c']
FILTRES = ['usage', 'periode', 'classe_dpe', 'approfondissement', 'niveau', 'batiment', 'mois', 'famille_action',
           'application']
CASCADE_MESURES = ('heating_kwh', 'heating_kwh_m2')
MAX_SECTIONS, MAX_BLOCS, MAX_BLOCS_TOTAL, MAX_TEXTE, MAX_TITRE = 30, 40, 150, 20000, 200


class SpecError(ValueError):
    pass


def _titre(v, msg, vide_ok=True):
    if v is None:
        v = ''
    if not isinstance(v, str) or len(v) > MAX_TITRE:
        raise SpecError(msg)
    v = ''.join(c for c in v if c.isprintable()).strip()
    if not v and not vide_ok:
        raise SpecError(msg)
    return v


def _series(v):
    if v is None:
        return 'reel'
    if isinstance(v, str) and v in SERIES_MOTS:
        return v
    if isinstance(v, list) and 0 < len(v) <= 12 and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        return list(dict.fromkeys(v))
    raise SpecError('Séries attendues : « reel », « bouquets », « reel_et_bouquets » ou une liste de bouquets.')


NIVEAUX = ('N0', 'N1', 'N2')


def _niveaux(v, au_plus=3):
    """Simulations choisies pour un bloc : liste de niveaux (ordre N0, N1, N2), vide = le plus fin à jour."""
    if v is None:
        return []
    if not isinstance(v, list) or any(n not in NIVEAUX for n in v):
        raise SpecError('Simulations : une liste parmi « N0 », « N1 », « N2 ».')
    lv = [n for n in NIVEAUX if n in set(v)]
    if len(lv) > au_plus:
        raise SpecError('Une carte ne montre qu’une simulation à la fois.')
    return lv


def _avec_niveaux(d, b, au_plus=3):
    lv = _niveaux(b.get('niveaux'), au_plus)
    if lv:
        d['niveaux'] = lv
    return d


def _bool(v, defaut):
    if v is None:
        return defaut
    if not isinstance(v, bool):
        raise SpecError('Mise en page : oui ou non attendu.')
    return v


def valider_graphe(g, mesures=None):
    mesures = mesures if mesures is not None else catalogue.mesures()
    if not isinstance(g, dict):
        raise SpecError('Graphe attendu (objet).')
    forme = g.get('forme')
    if forme not in catalogue.FORMES:
        raise SpecError('Forme de graphe inconnue.')
    dims = catalogue.DIM_GRAIN
    x = g.get('x')
    numeric_x = forme in catalogue.FORMES_NUMERIQUES_X and x in mesures and mesures[x]['grain'] == 'batiment'
    if forme == 'histogramme' and not numeric_x:
        raise SpecError('Histogramme : une mesure numérique est attendue en abscisse.')
    if not numeric_x and x not in dims:
        raise SpecError('Dimension inconnue en abscisse.')
    y = g.get('y') if g.get('y') is not None else []
    if not isinstance(y, list) or len(y) > 8 or any(m not in mesures for m in y):
        raise SpecError('Mesures inconnues (huit au plus).')
    y = list(dict.fromkeys(y))
    if forme not in ('histogramme',) and not y:
        raise SpecError('Au moins une mesure est attendue.')
    if forme in ('parallele',) and len(y) < 2:
        raise SpecError('Coordonnées parallèles : deux mesures au moins.')
    if forme in ('radar',) and len(y) < 3:
        raise SpecError('Radar : trois mesures au moins.')
    if forme in ('cascade', 'camembert', 'carte_chaleur') and len(y) != 1:
        raise SpecError('Cette forme ne porte qu’une mesure.')
    couleur = g.get('couleur')
    if couleur is not None and couleur not in dims:
        raise SpecError('Dimension de couleur inconnue.')
    if forme == 'carte_chaleur' and couleur is None:
        raise SpecError('Carte de chaleur : une dimension en lignes (« couleur ») est attendue.')
    taille = g.get('taille')
    if taille is not None and (taille not in mesures or mesures[taille]['grain'] != 'batiment'):
        raise SpecError('Mesure de taille inconnue.')
    if forme == 'bulles' and taille is None:
        raise SpecError('Bulles : une mesure de taille est attendue.')
    facettes = g.get('facettes')
    if facettes is not None and (facettes not in dims or forme in ('parallele', 'cascade', 'radar')):
        raise SpecError('Facettes : dimension inconnue ou forme sans facettes.')
    if forme == 'cascade' and (x != 'poste_bilan' or y[0] not in CASCADE_MESURES):
        raise SpecError('Cascade : du réel au potentiel, poste par poste (abscisse « poste_bilan », besoin de chauffage).')
    # Un seul grain par graphe.
    grains = {mesures[m]['grain'] for m in y} | ({'batiment'} if taille else set())
    if forme == 'cascade':
        grains = {'poste'}
    if len(grains) > 1 and not (forme == 'histogramme'):
        raise SpecError('Ces mesures ne se comptent pas à la même échelle : un graphe par échelle.')
    grain = next(iter(grains)) if grains else 'batiment'
    if numeric_x and grain != 'batiment':
        raise SpecError('Ces mesures ne se comptent pas à la même échelle : un graphe par échelle.')
    for d in (None if numeric_x else x, couleur, facettes):
        if d is None:
            continue
        dg = dims[d]
        if dg is not None and dg != grain and not (grain == 'heure' and dg == 'mois'):
            raise SpecError('Cette dimension n’existe pas pour ces mesures.')
    series = _series(g.get('series'))
    if forme == 'cascade' and series == 'reel':
        raise SpecError('Cascade : un bouquet est attendu dans les séries.')
    filtre = g.get('filtre') or {}
    if not isinstance(filtre, dict) or any(k not in FILTRES for k in filtre):
        raise SpecError('Filtre inconnu.')
    f_out = {}
    for k, v in filtre.items():
        if k == 'periode':
            if (not isinstance(v, list) or len(v) != 2
                    or any(a is not None and (isinstance(a, bool) or not isinstance(a, int)) for a in v)):
                raise SpecError('Filtre de période : [année min, année max] attendu.')
            f_out[k] = list(v)
        elif k == 'batiment':
            if not isinstance(v, list) or len(v) > 2000 or any(isinstance(a, bool) or not isinstance(a, int) for a in v):
                raise SpecError('Filtre de bâtiments : liste d’identifiants attendue.')
            f_out[k] = list(v)
        else:
            if not isinstance(v, list) or len(v) > 50 or any(not isinstance(a, (str, int)) or isinstance(a, bool)
                                                             for a in v):
                raise SpecError('Filtre : liste de valeurs attendue.')
            f_out[k] = [str(a)[:60] for a in v]
    agg = g.get('agregation') or ('aucune' if forme in ('nuage', 'bulles', 'boite', 'violon', 'histogramme',
                                                        'parallele') else 'moyenne')
    if agg not in AGREGATIONS:
        raise SpecError('Agrégation inconnue.')
    log = g.get('axe_y_log', False)
    if not isinstance(log, bool):
        raise SpecError('Axe logarithmique : oui ou non attendu.')
    return {'forme': forme, 'x': x, 'y': y, 'couleur': couleur, 'taille': taille, 'facettes': facettes,
            'series': series, 'filtre': f_out, 'agregation': agg,
            'titre': _titre(g.get('titre'), 'Titre de graphe trop long (200 caractères au plus).'),
            'axe_y_log': log, '_grain': grain, '_x_numerique': numeric_x}


def public_graphe(g):
    return {k: v for k, v in g.items() if not k.startswith('_')}


def _bloc(b, portee, mesures):
    if not isinstance(b, dict) or b.get('type') not in TYPES_BLOCS:
        raise SpecError('Type de bloc inconnu.')
    t = b['type']
    if t == 'texte':
        c = b.get('contenu', '')
        if not isinstance(c, str) or len(c) > MAX_TEXTE:
            raise SpecError('Texte trop long (20 000 caractères au plus).')
        return {'type': t, 'contenu': c}
    if t == 'indicateurs':
        cles = b.get('cles')
        if (not isinstance(cles, list) or len(cles) > 20
                or any(c not in mesures or mesures[c]['grain'] != 'batiment' for c in cles)):
            raise SpecError('Indicateurs inconnus (vingt au plus).')
        return _avec_niveaux({'type': t, 'cles': list(dict.fromkeys(cles)), 'series': _series(b.get('series'))}, b)
    if t == 'tableau':
        from . import tableaux
        src = b.get('source')
        if src not in SOURCES:
            raise SpecError('Source de tableau inconnue.')
        cols = b.get('colonnes')
        connues = tableaux.colonnes(src, mesures)
        if cols is None or cols == []:
            cols = tableaux.colonnes_defaut(src)  # tableau tout juste ajouté : colonnes par défaut, pas un refus
        if not isinstance(cols, list) or not cols or len(cols) > 25 or any(c not in connues for c in cols):
            raise SpecError('Colonnes de tableau inconnues.')
        tri = b.get('tri') or None  # '' = ordre du serveur (bloc tout juste ajouté)
        if tri is not None and tri not in connues:
            raise SpecError('Colonne de tri inconnue.')
        ordre = b.get('ordre') or 'asc'
        if ordre not in ('asc', 'desc'):
            raise SpecError('Ordre de tri : « asc » ou « desc ».')
        lim = b.get('limite', 50)
        if isinstance(lim, bool) or not isinstance(lim, int) or not 1 <= lim <= 500:
            raise SpecError('Limite de tableau : entre 1 et 500 lignes.')
        return _avec_niveaux({'type': t, 'source': src, 'colonnes': list(dict.fromkeys(cols)), 'tri': tri,
                              'ordre': ordre, 'limite': lim}, b)
    if t == 'graphe':
        return {'type': t, 'graphe': public_graphe(valider_graphe(b.get('graphe'), mesures))}
    if t == 'carte':
        ind = b.get('indicateur')
        if ind not in mesures or mesures[ind]['grain'] != 'batiment':
            raise SpecError('Indicateur de carte inconnu.')
        s = b.get('series', 'reel')
        if not (s == 'reel' or (isinstance(s, int) and not isinstance(s, bool))):
            raise SpecError('Série de carte : « reel » ou un bouquet.')
        return _avec_niveaux({'type': t, 'indicateur': ind, 'series': s}, b, au_plus=1)
    if t == 'vue3d':
        calc = b.get('calcul', 'dernier_N2')
        if not (calc == 'dernier_N2' or (isinstance(calc, int) and not isinstance(calc, bool) and calc > 0)):
            raise SpecError('Calcul de la vue 3D : « dernier_N2 » ou un identifiant de calcul.')
        gr = b.get('grandeur', 'pertes_hiver_kwh_m2')
        if gr not in GRANDEURS_3D:
            raise SpecError('Grandeur de la vue 3D inconnue.')
        cam = b.get('camera', 'iso')
        if cam not in CAMERAS:
            raise SpecError('Caméra de la vue 3D inconnue.')
        if portee != 'batiment':
            raise SpecError('La vue 3D appartient à un rendu de bâtiment.')
        return {'type': t, 'calcul': calc, 'grandeur': gr, 'camera': cam}
    if t == 'fiche':
        secs = b.get('sections') if b.get('sections') is not None else list(FICHE_SECTIONS)
        if not isinstance(secs, list) or not secs or any(s not in FICHE_SECTIONS for s in secs):
            raise SpecError('Sections de fiche inconnues.')
        if portee != 'batiment':
            raise SpecError('La fiche d’identité appartient à un rendu de bâtiment.')
        return {'type': t, 'sections': list(dict.fromkeys(secs))}
    return {'type': 'saut_de_page'}


def valider(spec, mesures=None):
    mesures = mesures if mesures is not None else catalogue.mesures()
    if not isinstance(spec, dict):
        raise SpecError('Spécification attendue (objet).')
    if spec.get('version', 1) != 1:
        raise SpecError('Version de spécification inconnue.')
    portee = spec.get('portee')
    if portee not in catalogue.PORTEES:
        raise SpecError('Portée attendue : « parc » ou « batiment ».')
    mp = spec.get('mise_en_page') or {}
    if not isinstance(mp, dict):
        raise SpecError('Mise en page attendue (objet).')
    fmt = mp.get('format', 'A4')
    ori = mp.get('orientation', 'portrait')
    if fmt not in ('A4', 'A3') or ori not in ('portrait', 'paysage'):
        raise SpecError('Mise en page : format A4 ou A3, portrait ou paysage.')
    sections = spec.get('sections')
    if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:
        raise SpecError('Sections attendues (trente au plus).')
    out, total = [], 0
    for s in sections:
        if not isinstance(s, dict):
            raise SpecError('Section attendue (objet).')
        blocs = s.get('blocs') or []
        if not isinstance(blocs, list) or len(blocs) > MAX_BLOCS:
            raise SpecError('Blocs attendus (quarante au plus par section).')
        total += len(blocs)
        if total > MAX_BLOCS_TOTAL:
            raise SpecError('Document trop long (150 blocs au plus).')
        out.append({'titre': _titre(s.get('titre'), 'Titre de section trop long (200 caractères au plus).'),
                    'blocs': [_bloc(b, portee, mesures) for b in blocs]})
    return {'version': 1, 'portee': portee,
            'titre': _titre(spec.get('titre'), 'Titre trop long (200 caractères au plus).'),
            'mise_en_page': {'format': fmt, 'orientation': ori, 'sommaire': _bool(mp.get('sommaire'), True),
                             'numeros': _bool(mp.get('numeros'), True)},
            'sections': out}


def schema_json():
    """Schéma JSON (draft 2020-12) de la spécification : sert à l'interface pour guider l'édition. Les listes
    ouvertes (dimensions, mesures, bouquets) sont données par leurs routes ; la validation fait foi."""
    serie = {'oneOf': [{'enum': SERIES_MOTS}, {'type': 'array', 'items': {'type': 'integer'}, 'maxItems': 12}]}
    graphe = {'type': 'object', 'required': ['forme', 'x'], 'properties': {
        'forme': {'enum': catalogue.FORMES}, 'x': {'type': 'string', 'description': 'GET /api/rendu/dimensions/'},
        'y': {'type': 'array', 'items': {'type': 'string', 'description': 'GET /api/rendu/mesures/'}, 'maxItems': 8},
        'couleur': {'type': ['string', 'null']}, 'taille': {'type': ['string', 'null']},
        'facettes': {'type': ['string', 'null']}, 'series': serie,
        'filtre': {'type': 'object', 'propertyNames': {'enum': FILTRES}},
        'agregation': {'enum': AGREGATIONS}, 'titre': {'type': 'string', 'maxLength': MAX_TITRE},
        'axe_y_log': {'type': 'boolean'}}}
    blocs = [
        {'properties': {'type': {'const': 'texte'}, 'contenu': {'type': 'string', 'maxLength': MAX_TEXTE,
                        'description': 'Markdown restreint : paragraphes, **gras**, *italique*, listes « - », titres « ### ».'}}},
        {'properties': {'type': {'const': 'indicateurs'}, 'cles': {'type': 'array', 'items': {'type': 'string'}},
                        'series': serie, 'niveaux': {'type': 'array', 'items': {'enum': list(NIVEAUX)}, 'uniqueItems': True, 'description': 'Simulations affichées ; absent ou vide : la plus fine à jour.'}}},
        {'properties': {'type': {'const': 'tableau'}, 'source': {'enum': SOURCES},
                        'colonnes': {'type': 'array', 'items': {'type': 'string'}}, 'tri': {'type': ['string', 'null']},
                        'ordre': {'enum': ['asc', 'desc']}, 'limite': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                        'niveaux': {'type': 'array', 'items': {'enum': list(NIVEAUX)}, 'uniqueItems': True, 'description': 'Simulations affichées ; absent ou vide : la plus fine à jour.'}}},
        {'properties': {'type': {'const': 'graphe'}, 'graphe': graphe}},
        {'properties': {'type': {'const': 'carte'}, 'indicateur': {'type': 'string'},
                        'series': {'oneOf': [{'const': 'reel'}, {'type': 'integer'}]},
                        'niveaux': {'type': 'array', 'items': {'enum': list(NIVEAUX)}, 'maxItems': 1}}},
        {'properties': {'type': {'const': 'vue3d'}, 'calcul': {'oneOf': [{'const': 'dernier_N2'}, {'type': 'integer'}]},
                        'grandeur': {'enum': GRANDEURS_3D}, 'camera': {'enum': CAMERAS}}},
        {'properties': {'type': {'const': 'fiche'}, 'sections': {'type': 'array', 'items': {'enum': FICHE_SECTIONS}}}},
        {'properties': {'type': {'const': 'saut_de_page'}}},
    ]
    for b in blocs:
        b['type'] = 'object'
        b['required'] = ['type']
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'Spécification de rendu (I6.a)',
        'type': 'object', 'required': ['version', 'portee', 'sections'],
        'properties': {
            'version': {'const': 1}, 'portee': {'enum': catalogue.PORTEES},
            'titre': {'type': 'string', 'maxLength': MAX_TITRE},
            'mise_en_page': {'type': 'object', 'properties': {
                'format': {'enum': ['A4', 'A3']}, 'orientation': {'enum': ['portrait', 'paysage']},
                'sommaire': {'type': 'boolean'}, 'numeros': {'type': 'boolean'}}},
            'sections': {'type': 'array', 'maxItems': MAX_SECTIONS, 'items': {
                'type': 'object', 'properties': {'titre': {'type': 'string', 'maxLength': MAX_TITRE},
                                                 'blocs': {'type': 'array', 'maxItems': MAX_BLOCS,
                                                           'items': {'oneOf': blocs}}}}},
        },
        '$defs': {'graphe': graphe},
        'x-limites': {'blocs_total': MAX_BLOCS_TOTAL, 'texte': MAX_TEXTE},
        'x-couleurs': ('Le serveur n’écrit aucune couleur : le rendu pose palette[i % n] sur data[i].marker.color et '
                       'data[i].line.color quand ils sont absents, et remplace layout.coloraxis.colorscale / '
                       'data[i].colorscale quand layout.meta.palette ≠ categorielle.'),
    }


def nombre_fini(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
