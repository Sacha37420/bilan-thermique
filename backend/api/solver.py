"""Solveur thermique 1D par éléments finis pour une paroi multicouche.

Implémente le modèle documenté par la page « Théorie » de l'application :
maillage par subdivision de couches, matrices élémentaires de conduction et
de capacité (capacité concentrée), propagation du rayonnement solaire couche
par couche, assemblage des conditions aux limites, intégration temporelle par
schéma d'Euler implicite (pas horaire).

Module volontairement indépendant de Django : entrées/sorties en dictionnaires
et listes Python, aucune dépendance à un modèle ou une requête HTTP.
"""

import math

import numpy as np

DT_SECONDS = 3600.0

MAX_NODES = 300
MAX_WEATHER_POINTS = 8784  # une année horaire (366 * 24), marge bissextile


class SimulationError(ValueError):
    """Entrée invalide ou hors des limites de calcul raisonnables."""


def _build_mesh(layers, dx_max):
    element_layer = []
    element_length = []
    element_lambda = []
    element_rhoc = []
    layer_start_node = []
    layer_end_node = []
    x = [0.0]
    node = 0

    for i, layer in enumerate(layers):
        n = max(1, math.ceil(layer['e'] / dx_max))
        h = layer['e'] / n
        layer_start_node.append(node)
        for _ in range(n):
            element_layer.append(i)
            element_length.append(h)
            element_lambda.append(layer['lam'])
            element_rhoc.append(layer['rho'] * layer['c'])
            node += 1
            x.append(x[-1] + h)
        layer_end_node.append(node)

    n_wall_nodes = node + 1
    if n_wall_nodes > MAX_NODES:
        raise SimulationError(
            f"Le maillage ({n_wall_nodes} nœuds) dépasse la limite de {MAX_NODES} — "
            "augmenter dx_max ou réduire le nombre/l'épaisseur des couches."
        )

    return {
        'element_layer': element_layer,
        'element_length': element_length,
        'element_lambda': element_lambda,
        'element_rhoc': element_rhoc,
        'layer_start_node': layer_start_node,
        'layer_end_node': layer_end_node,
        'x': x,
        'n_wall_nodes': n_wall_nodes,
    }


def _assemble_kc(mesh):
    n = mesh['n_wall_nodes']
    K = np.zeros((n, n))
    C = np.zeros((n, n))

    for idx in range(len(mesh['element_layer'])):
        i, j = idx, idx + 1
        length = mesh['element_length'][idx]
        lam = mesh['element_lambda'][idx]
        rhoc = mesh['element_rhoc'][idx]

        k = lam / length
        K[i, i] += k
        K[i, j] -= k
        K[j, i] -= k
        K[j, j] += k

        cap = rhoc * length / 2.0  # capacité concentrée : moitié de l'élément à chaque nœud
        C[i, i] += cap
        C[j, j] += cap

    return K, C


def _propagate_solar(layers, e_glo, mesh):
    """Bilan réfléchi/absorbé/transmis couche par couche (section 05 de la théorie).

    Retourne une liste de contributions :
    - ('surface', nœud, flux W/m²) pour l'absorption à la première interface
      opaque rencontrée — la propagation s'arrête net (mur Trombe...).
    - ('volumetric', indice de couche, énergie totale absorbée W/m²) pour
      chaque couche translucide traversée.
    - ('interior', None, énergie W/m²) — AU PLUS une fois, seulement si TOUTES
      les couches sont translucides (aucune n'a jamais arrêté la propagation,
      contrairement au mur Trombe) : l'énergie qui a fini de traverser toute
      la paroi sans rencontrer de couche opaque (ex. un simple/double vitrage
      sans encadrement opaque en fond — le cas de tout le catalogue de
      vitrages actuel). Physiquement entrée dans la pièce : à ajouter comme
      gain direct sur le nœud d'air par l'appelant, plutôt que de disparaître
      du bilan (piège identifié le 2026-08-08, voir to_do.md).
    """
    sources = []
    e_inc = e_glo

    for i, layer in enumerate(layers):
        if e_inc <= 1e-9:
            e_inc = 0.0
            break
        absorbed = layer['alpha'] * e_inc
        transmitted = layer['tau'] * e_inc
        if layer['tau'] <= 1e-9:
            node = mesh['layer_start_node'][i]
            sources.append(('surface', node, absorbed))
            e_inc = 0.0
            break
        sources.append(('volumetric', i, absorbed))
        e_inc = transmitted
    else:
        # Boucle terminée SANS "break" : aucune couche opaque n'a jamais
        # arrêté la propagation — e_inc (dernier `transmitted` calculé) a
        # traversé toute la paroi.
        if e_inc > 1e-9:
            sources.append(('interior', None, e_inc))

    return sources


def _assemble_F(layers, mesh, h_e, weather_point, f_ciel, n_dof, air_idx=None, g_vent=0.0):
    F = np.zeros(n_dof)

    t_ext = weather_point['t_ext']
    h_s = weather_point['h_s']
    theta_i = weather_point['theta_i']
    e_dir = weather_point['e_dir']
    e_dif = weather_point['e_dif']

    # Le soleil sous l'horizon (h_s <= 0) ou une paroi qui ne "voit" pas le
    # rayon (cos négatif : incidence > 90°) annulent la composante directe —
    # deux garde-fous physiques indépendants des données d'entrée fournies.
    if h_s > 0:
        cos_ti = max(math.cos(math.radians(theta_i)), 0.0)
    else:
        cos_ti = 0.0
    e_glo = e_dir * cos_ti + e_dif * f_ciel

    F[0] += h_e * t_ext

    for kind, ref, value in _propagate_solar(layers, e_glo, mesh):
        if kind == 'surface':
            F[ref] += value
        elif kind == 'interior':
            # Rayonnement transmis intégralement (aucune couche opaque
            # rencontrée, ex. un simple/double vitrage) : a fini de traverser
            # la paroi, chauffe directement l'air plutôt que de disparaître du
            # bilan. Sans nœud d'air (mode 'imposed', 1D) : ne s'applique pas,
            # même convention que g_vent ci-dessous.
            if air_idx is not None:
                F[air_idx] += value
        else:
            layer_idx = ref
            s0, s1 = mesh['layer_start_node'][layer_idx], mesh['layer_end_node'][layer_idx]
            length_layer = layers[layer_idx]['e']
            s_vol = value / length_layer
            for el in range(s0, s1):
                l_e = mesh['element_length'][el]
                contrib = s_vol * l_e / 2.0
                F[el] += contrib
                F[el + 1] += contrib

    # Renouvellement d'air : source directe sur le nœud d'air, en plus de ce
    # qui remonte par la paroi — un logement ventilé perd de la chaleur par
    # les infiltrations/la VMC indépendamment de ce que la paroi modélisée
    # laisse passer. Terme de bord symétrique à F[0] += h_e * t_ext, mais visé
    # sur le nœud d'air plutôt que sur la surface extérieure.
    if air_idx is not None and g_vent:
        F[air_idx] += g_vent * t_ext

    return F


def run_simulation(payload):
    layers = payload['layers']
    dx_max = payload['dx_max']
    h_e = payload['h_e']
    wall_tilt_deg = payload.get('wall_tilt_deg', 90.0)
    f_ciel = (1.0 + math.cos(math.radians(wall_tilt_deg))) / 2.0
    interior = payload['interior']
    t_init = payload['t_init']
    weather = payload['weather']

    if len(weather) > MAX_WEATHER_POINTS:
        raise SimulationError(
            f"{len(weather)} pas horaires demandés, au-delà de la limite de {MAX_WEATHER_POINTS} "
            "(un an de données horaires)."
        )
    if not weather:
        raise SimulationError("La série météo est vide.")

    mesh = _build_mesh(layers, dx_max)
    n_wall = mesh['n_wall_nodes']
    last = n_wall - 1
    K, C = _assemble_kc(mesh)

    # Convection extérieure ET intérieure : deux conditions de Robin structurellement
    # identiques (contribution constante dans le temps), symétriques aux deux bords.
    # Sans ce terme côté intérieur, T_int serait directement la température de SURFACE
    # (résistance superficielle nulle) au lieu de la température d'air séparée de la
    # surface par 1/h_i — ce qui casserait la symétrie avec le traitement extérieur.
    h_i = interior['h_i']
    K[0, 0] += h_e
    K[last, last] += h_i

    has_air_node = interior['mode'] == 'free'

    if has_air_node:
        n_dof = n_wall + 1
        air = n_wall
        K2 = np.zeros((n_dof, n_dof))
        C2 = np.zeros((n_dof, n_dof))
        K2[:n_wall, :n_wall] = K
        C2[:n_wall, :n_wall] = C
        K, C = K2, C2
        # K[last, last] += h_i déjà appliqué ci-dessus (contribution commune aux deux
        # modes) — ici on ajoute seulement le couplage vers le nœud d'air.
        K[last, air] -= h_i
        K[air, last] -= h_i
        K[air, air] += h_i
        C[air, air] += interior['c_air_int']

        # Renouvellement d'air (infiltrations/VMC) : conductance directe
        # air <-> extérieur, ramenée au m² de paroi comme le reste du système
        # 1D (voir doc Théorie, section 01 — "toutes les grandeurs sont
        # exprimées par unité de surface de paroi"). Ce n'est PAS un débit réel
        # converti automatiquement : contrairement au solveur bâtiment 3D (qui
        # connaît un vrai volume et une vraie surface totale), le 1D ne modélise
        # qu'UNE paroi notionnelle de 1 m² — g_vent est donc déjà la conductance
        # attribuable à ce m², à charge pour l'appelant de l'avoir réduite lui-même
        # si elle vient d'un débit réel (voir api.building_solver côté 3D pour la
        # conversion 0,34 * débit_m3h * (1-rendement), qui elle s'applique en
        # absolu au nœud d'air unique du bâtiment, jamais par m² de paroi).
        g_vent = interior.get('g_vent', 0.0)
        K[air, air] += g_vent
        air_idx = air
    else:
        n_dof = n_wall
        t_int = interior['t_int']
        air_idx = None
        g_vent = 0.0

    A = C / DT_SECONDS + K

    T = np.full(n_dof, float(t_init))
    temperatures = [T.copy()]

    for point in weather:
        F = _assemble_F(layers, mesh, h_e, point, f_ciel, n_dof, air_idx=air_idx, g_vent=g_vent)
        if not has_air_node:
            F[last] += h_i * t_int  # T_int connue : forçage direct, symétrique à F[0] += h_e*T_ext
        b = (C / DT_SECONDS) @ T + F
        T = np.linalg.solve(A, b)
        temperatures.append(T.copy())

    layer_boundary_nodes = mesh['layer_start_node'] + [n_wall - 1]
    layer_boundaries_x = [mesh['x'][i] for i in layer_boundary_nodes]

    return {
        'x': mesh['x'],
        'layer_boundary_nodes': layer_boundary_nodes,
        'layer_boundaries_x': layer_boundaries_x,
        'has_air_node': has_air_node,
        'n_wall_nodes': n_wall,
        'temperatures': [row.tolist() for row in temperatures],
    }
