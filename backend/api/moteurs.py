"""Catalogue des moteurs de calcul (chantier bt-navigation, contrat § 2.4) — `GET /api/calculs/moteurs/`.

Statique. Les textes sont repris des docstrings et constantes des moteurs (`engines/n0.py`,
`engines/n1.py`, `engines/inputs.py`, `engines/n2_inputs.py`, `engines/systems.py`,
`uncertainty.py`, `simulation.py`, `solver.py`, `building_solver.py`, `access.py`, `views.py`) :
modifier un moteur, c'est relire sa fiche ici.
"""

from . import building_solver, calc_service, simulation, solver, uncertainty
from .engines import n2_inputs

_COMMON = ('Hypothèses communes avec le N2 (pour rester comparables, § 6.3) : pas d’échange infrarouge avec le '
           'ciel, ciel isotrope, albédo du sol 0,20, plancher bas couplé à une température de sol constante.')
_BRIDGES = 'Ponts thermiques : non comptés (hors périmètre, comme le N2 — `refonte.md` D9).'
_SYSTEMS = ('Du besoin à l’énergie finale, primaire, au CO₂ et au coût : rendement global = génération × émission × '
            'distribution × régulation (repères de la méthode 3CL). Aide à la décision, pas un DPE (D6).')
_FICHE_IN = ['Fiche effective du bâtiment (valeur retenue par clé, avec sa provenance)',
             'Géométrie simplifiée de la chaîne (emprise, murs exposés par orientation, mitoyenneté, horizon)',
             'Protections solaires paramétriques (Lot 14)', 'Météo horaire (année type du site ou jeu choisi)']
_DECLARE = ('Toute personne qui peut modifier la fiche : gestionnaire de parc, gestionnaire ou occupant du bâtiment ; '
            'un lecteur ne lance rien.')
_MANAGE = 'Gestionnaire de parc ou du bâtiment (D2/D37 : jamais en lot).'

_a, _b, _shade = simulation.N2_SECONDS


def _num(x):
    return f'{x:g}'.replace('.', ',')


CATALOGUE = [
    {
        'id': 'N0', 'libelle': 'Mensuel',
        'resume': 'N0 — bilan statique mensuel (ISO 13790 méthode mensuelle, proche de la 3CL).',
        'methode': ('Besoin mensuel de chauffage : Q_H = Q_ht − η·Q_gn, avec Q_ht les déperditions (transmission + '
                    'ventilation) et Q_gn les apports (internes + solaires), η le facteur d’utilisation des apports '
                    '(a = 1 + τ/15, τ la constante de temps du bâtiment en heures). Mêmes paramètres et mêmes apports '
                    'solaires que le N1 (`n1.solar_terms`) : seul le pas de temps change.'),
        'hypotheses': [_COMMON],
        'entrees': _FICHE_IN,
        'sorties': ['Besoin de chauffage annuel et mensuel', 'Énergie finale, primaire, CO₂, coût, étiquette du modèle'],
        'duree': '< 0,1 s.',
        'qui': _DECLARE,
        'limites': [_BRIDGES, _SYSTEMS],
        'version': calc_service.ENGINE_VERSION['N0'], 'expert_route': None,
    },
    {
        'id': 'N1', 'libelle': 'Horaire simplifié',
        'resume': 'N1 — modèle horaire réduit 5R1C (ISO 13790 annexe C, repris par l’ISO 52016-1).',
        'methode': ('Trois nœuds (air θ_air, surface θ_s, masse θ_m), cinq conductances, une capacité ; schéma de '
                    'Crank-Nicolson au pas horaire, besoin de chauffage/refroidissement par interpolation linéaire '
                    '(C.4 de la norme). Vectorisé sur S jeux de paramètres.'),
        'hypotheses': [_COMMON,
                       ('Fourchette : chaque valeur numérique de la fiche est tirée autour de sa valeur effective avec '
                        'un écart relatif qui dépend de sa méthode (un défaut est très incertain, une mesure peu).')],
        'entrees': _FICHE_IN,
        'sorties': ['Besoins (kWh), puissance maximale',
                    'Températures d’air et opérative (θ_op = 0,3 θ_air + 0,7 θ_s — confort d’été, D9)',
                    'Bilan par poste, besoins mensuels',
                    f'Fourchette P10–P90 du besoin de chauffage ({uncertainty.DEFAULT_SAMPLES} tirages)',
                    'Énergie finale, primaire, CO₂, coût, étiquette du modèle'],
        'duree': f'{uncertainty.DEFAULT_SAMPLES} tirages coûtent à peine plus qu’un calcul.',
        'qui': _DECLARE,
        'limites': [_BRIDGES, _SYSTEMS],
        'version': calc_service.ENGINE_VERSION['N1'], 'expert_route': None,
    },
    {
        'id': 'N2', 'libelle': 'Détaillé par paroi',
        'resume': 'Entrées du N2 (FEM par triangle) depuis la fiche (Lot 23, refonte — décisions D34 à D37).',
        'methode': ('Chaque triangle de l’enveloppe est sa propre paroi 1D (mêmes matrices K/C par unité de surface que '
                    'solver.py), mais tous les triangles couplent leur dernier nœud au même nœud d’air intérieur — '
                    'pondéré par l’aire de chaque triangle. Système creux (scipy.sparse), factorisation LU.'),
        'hypotheses': [
            ('Géométrie : emprise corrigée (Lot 15), sinon automatique (BD TOPO), sinon un carré de l’emprise déclarée ; '
             'hauteur, niveaux et toiture de la fiche ; enveloppe fermée.'),
            ('Mitoyenneté : murs accolés à un voisin de la chaîne (même règle que N0/N1), sinon les murs qui approchent '
             'au mieux la part déclarée — rendus adiabatiques.'),
            ('Baies : une bande vitrée par niveau et par façade, dimensionnée pour le taux de vitrage de la fiche.'),
            ('Parois reconstituées en mémoire, dont le U égale celui de la fiche : structure selon le matériau et '
             'l’inertie, isolant dimensionné (ITE dehors, ITI dedans) ; vitrages à Uw et g de la fiche.'),
            ('Réglages : ventilation effective, consignes, apports internes et horaires d’occupation identiques à N1, '
             'pour que les niveaux restent comparables.'),
            ('Mode « géométrie experte » (D35) : si l’enveloppe du bâtiment est entièrement assignée à la main, elle est '
             'prise telle quelle avec ses parois du catalogue ; seuls les réglages et la météo viennent de la fiche.'),
        ],
        'entrees': ['Fiche effective du bâtiment', 'Géométrie (emprise, hauteur, niveaux, toiture) ou enveloppe experte',
                    'Obstacles : environnement relié (LiDAR × BD TOPO) s’il existe, sinon les voisins de la chaîne, '
                    'extrudés ; protections solaires géométriques (Lot 14)', 'Météo horaire'],
        'sorties': ['Besoins de chauffage et de refroidissement, puissance maximale', 'Températures d’air et opérative',
                    'Bilan, besoins mensuels', 'Énergie finale, primaire, CO₂, coût, étiquette du modèle'],
        'duree': (f'Par année simulée : {_num(_a)} s + {_num(_b)} s par triangle, + {_num(_shade)} s pour l’ombrage '
                  '(mesures du Lot 23). En tâche de fond : un calcul lourd à la fois sur le lab.'),
        'qui': _MANAGE,
        'limites': [
            'Un seul nœud d’air (pas de planchers intermédiaires ni de refends, D9).',
            'La consigne réduite hors occupation du tertiaire n’est pas reproduite (le planning du N2 ne porte pas de consigne).',
            'Les protections paramétriques ne jouent que dans N0/N1 (le N2 voit les protections géométriques).',
        ],
        'version': n2_inputs.ENGINE_VERSION, 'expert_route': None,
    },
    {
        'id': '1D', 'libelle': 'Paroi 1D',
        'resume': 'Solveur thermique 1D par éléments finis pour une paroi multicouche.',
        'methode': ('Maillage par subdivision de couches, matrices élémentaires de conduction et de capacité (capacité '
                    'concentrée), propagation du rayonnement solaire couche par couche, assemblage des conditions aux '
                    'limites, intégration temporelle par schéma d’Euler implicite (pas horaire).'),
        'hypotheses': ['Régime transitoire 1D, heure par heure (voir la page « Théorie »).'],
        'entrees': ['Définition de la paroi (couches)', 'Maillage souhaité', 'Conditions aux limites intérieures',
                    'Série météo extérieure'],
        'sorties': ['Températures et flux de la paroi, heure par heure'],
        'duree': (f'Synchrone ; au plus {solver.MAX_NODES} nœuds et {solver.MAX_WEATHER_POINTS} heures.'),
        'qui': 'Tout membre de l’app (outil expert, ne touche à aucune fiche).',
        'limites': ['Une paroi multicouche seule (pas de bâtiment).'],
        'version': None, 'expert_route': '/calcul-1d',
    },
    {
        'id': '3D', 'libelle': 'Bâtiment maillé 3D',
        'resume': 'Solveur thermique 3D — généralisation de solver.py à un bâtiment entier.',
        'methode': ('Chaque triangle de l’enveloppe est sa propre paroi 1D, tous couplés au même nœud d’air intérieur '
                    'pondéré par l’aire ; rayonnement solaire par triangle (orientation et visibilité solaire '
                    'précalculée).'),
        'hypotheses': ['Enveloppe triangulaire assignée à la main, parois du catalogue (page Maillage).'],
        'entrees': ['Enveloppe maillée et ses parois', 'Environnement (ombrage)', 'Météo horaire',
                    'Réglages du formulaire de la page Calcul 3D'],
        'sorties': ['Températures, besoins, flux par paroi'],
        'duree': 'Mesuré à ~28 s pour 338 triangles sur une année horaire complète ; en tâche de fond.',
        'qui': _MANAGE.split(' (')[0] + '.',
        'limites': [f'Au plus {building_solver.MAX_TOTAL_DOF} degrés de liberté et {building_solver.MAX_WEATHER_POINTS} heures.',
                    'Réglages saisis dans le formulaire : on ne sait pas dire si le résultat est à jour de la fiche.'],
        'version': None, 'expert_route': '/calcul-3d',
    },
]
