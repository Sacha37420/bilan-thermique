"""Solveur thermique 3D — généralisation de solver.py à un bâtiment entier.

Chaque triangle de l'enveloppe est sa propre paroi 1D (mêmes matrices K/C par
unité de surface que solver.py, réutilisées telles quelles), mais tous les
triangles couplent leur dernier nœud au MÊME nœud d'air intérieur — pondéré
par l'aire de chaque triangle (une paroi deux fois plus grande transporte
deux fois plus de flux). Le système global est donc en unités ABSOLUES
(W/K, J/K, W), pas par m² comme dans solver.py : chaque bloc par-triangle
(K, C, et les termes de Robin déjà dedans) est multiplié par son aire avant
insertion dans le système global.

Nombre de triangles pouvant monter à quelques milliers, le système global
(quelques milliers à dizaines de milliers de degrés de liberté, mais
extrêmement creux — chaque triangle n'est couplé qu'à lui-même et au nœud
d'air partagé) est résolu en creux (scipy.sparse), avec factorisation LU en
principe une seule fois — les matrices ne changent pas d'une heure à l'autre
dans le cas de base, seul le second membre change. Deux paramètres, s'ils
varient dans le temps, entrent néanmoins dans la matrice elle-même plutôt que
dans le seul second membre : g_vent (Lot Q, jusqu'à 24 valeurs via un planning
horaire) et h_e (Lot R, jusqu'à quelques dizaines de valeurs via le vent réel)
— la factorisation est alors refaite une fois PAR COMBINAISON DISTINCTE des
deux réellement rencontrée, jamais par heure simulée (voir _factorize_for).

Rayonnement solaire : chaque triangle a sa propre orientation (tilt_deg,
azimuth_deg, normal — calculés par api.geometry à l'import) et sa propre
visibilité solaire précalculée (api.shadow, Lot C) — contrairement à
solver.py où theta_i/h_s étaient donnés directement par ligne météo, ici la
météo ne fournit que la position du soleil (azimuth, élévation) et chaque
triangle calcule son propre cos(theta_i) = normal · direction_soleil.
"""

import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from . import solver as wall_solver
from . import shadow

DT_SECONDS = 3600.0
MAX_TOTAL_DOF = 60_000
MAX_WEATHER_POINTS = 8784
# triangles x heures — garde-fou du mode 'realtime' (lancer de rayons rejoué
# à chaque heure, contrairement au mode précalculé qui teste une grille fixe
# une fois pour toutes) : au-delà, le calcul monopoliserait le worker du lab
# (--concurrency=1) trop longtemps.
MAX_REALTIME_RAYCAST_OPS = 2_000_000
# Nombre de combinaisons DISTINCTES (g_vent, h_e) — chacune une factorisation
# LU (Lot Q + Lot R) — au-delà duquel on refuse plutôt que de laisser un run
# pathologique (météo artificiellement bruitée) monopoliser le worker. Mesuré
# en réel (to_do.md, Lot R) : ~340 au pire sur un an de vent réel (Paris/Brest)
# combiné à un planning de ventilation à 24 valeurs toutes distinctes — marge
# large.
MAX_DISTINCT_BOUNDARY_COMBOS = 2000


class BuildingSimulationError(ValueError):
    pass


# ── Lot R : h_e dynamique (vent) et h_i par orientation ──────────────────────

# Corrélation de Jürges — h_e (W/m²K) depuis la vitesse de vent à 10 m (m/s).
# Choisie plutôt que McAdams (5,7 + 3,8·v, très proche numériquement, écart
# <5% sur la plage usuelle) car plus couramment citée en physique du bâtiment
# française — voir to_do.md, Lot R. Une seule valeur pour tout le bâtiment à
# une heure donnée : l'orientation de chaque façade par rapport à la direction
# du vent (face au vent / sous le vent) n'est volontairement PAS modélisée —
# simplification assumée, pas un oubli.
JURGES_A = 5.8
JURGES_B = 3.94


def h_e_from_wind(wind_m_s):
    return JURGES_A + JURGES_B * max(wind_m_s, 0.0)


# ISO 6946 (Rsi) — résistance superficielle intérieure selon la direction du
# flux de chaleur : classée par TYPE d'élément (mur/plafond/plancher), pas par
# le signe instantané du flux à un instant donné — même convention que la
# norme elle-même, cohérent avec le fait que h_i n'a jamais varié dans le
# temps dans ce solveur (seulement, désormais, d'un triangle à l'autre).
H_I_WALL = 7.7      # flux horizontal (mur, tilt_deg proche de 90°)
H_I_CEILING = 10.0  # flux montant (plafond/toiture, tilt_deg proche de 0°)
H_I_FLOOR = 5.9     # flux descendant (plancher/sol, tilt_deg proche de 180°)


def h_i_from_tilt(tilt_deg):
    if tilt_deg <= 60.0:
        return H_I_CEILING
    if tilt_deg >= 120.0:
        return H_I_FLOOR
    return H_I_WALL


def _build_triangle_systems(triangles, paroi_layers_by_id, dx_max):
    """Une entrée par triangle : (mesh, K, C, layers), K/C « nues » — par unité
    de surface, SANS h_e ni h_i (posés par _assemble_global_kc/run_building_simulation,
    voir Lot R : h_e varie par HEURE, h_i par TRIANGLE, ni l'un ni l'autre n'est
    plus une constante unique bakeable ici une fois pour toutes). Mise en cache
    par (paroi_model_id, dx_max) uniquement — ce cache ne dépend donc plus des
    coefficients de convection, beaucoup de triangles partageant le même
    modèle de paroi."""
    cache = {}
    systems = []
    total_nodes = 0
    for idx, tri in enumerate(triangles):
        pid = tri.get('paroi_model_id')
        if pid is None:
            raise BuildingSimulationError(f"Triangle {idx} sans modèle de paroi assigné.")
        if pid not in paroi_layers_by_id:
            raise BuildingSimulationError(f"Triangle {idx} : modèle de paroi #{pid} introuvable.")
        key = (pid, dx_max)
        if key not in cache:
            layers = paroi_layers_by_id[pid]
            mesh = wall_solver._build_mesh(layers, dx_max)
            K, C = wall_solver._assemble_kc(mesh)
            cache[key] = (mesh, K, C, layers)
        systems.append(cache[key])
        total_nodes += cache[key][0]['n_wall_nodes']
        if total_nodes > MAX_TOTAL_DOF:
            raise BuildingSimulationError(
                f"Le maillage total ({total_nodes}+ nœuds) dépasse la limite de {MAX_TOTAL_DOF} — "
                "augmenter dx_max."
            )
    return systems


def _assemble_global_kc(systems, areas, h_i_list, frame_g=None):
    """h_i_list : une valeur PAR TRIANGLE (Lot R — ISO 6946 par orientation via
    h_i_from_tilt, ou la même constante répétée pour tous si h_i_auto est
    désactivé — voir run_building_simulation) : remplace l'ancien h_i scalaire
    unique. frame_g : liste optionnelle (une par triangle, Lot I) — conductance
    directe du cadre de fenêtre vers le nœud d'air, ajoutée à
    K_global[air_idx,air_idx] exactement comme g_vent (Lot G), mais PAR TRIANGLE
    plutôt qu'un terme global unique (des fenêtres différentes peuvent avoir des
    cadres différents). Le cadre n'a pas de nœud de surface propre (pas de
    comportement optique, capacité négligeable — voir ParoiModel.frame_u/
    frame_fraction).

    Retourne aussi K_e_pattern (Lot R) : motif de sensibilité à h_e, +area_i sur
    la diagonale du PREMIER nœud (extérieur) de chaque triangle. h_e varie par
    HEURE (vent), pas par triangle : plutôt que reconstruire tout K_global à
    chaque valeur distincte de h_e rencontrée, K_global_final = K_global + h_e *
    K_e_pattern (relation linéaire, addition creuse peu coûteuse) — voir
    _factorize_for."""
    offsets = []
    total = 0
    for mesh, K, C, layers in systems:
        offsets.append(total)
        total += mesh['n_wall_nodes']
    air_idx = total
    n_dof = total + 1

    K_global = sp.lil_matrix((n_dof, n_dof))
    C_global = sp.lil_matrix((n_dof, n_dof))
    K_e_pattern = sp.lil_matrix((n_dof, n_dof))

    for i, (mesh, K, C, layers) in enumerate(systems):
        off = offsets[i]
        n = mesh['n_wall_nodes']
        area = areas[i]
        last = off + n - 1
        h_i = h_i_list[i]

        K_global[off:off + n, off:off + n] += K * area
        C_global[off:off + n, off:off + n] += C * area
        K_e_pattern[off, off] += area

        K_global[last, last] += h_i * area
        K_global[last, air_idx] -= h_i * area
        K_global[air_idx, last] -= h_i * area
        K_global[air_idx, air_idx] += h_i * area

        if frame_g is not None and frame_g[i]:
            K_global[air_idx, air_idx] += frame_g[i]

    return K_global.tocsc(), C_global.tocsc(), K_e_pattern.tocsc(), offsets, air_idx, n_dof


def _assemble_F_hour(systems, areas, offsets, n_dof, triangles_geom, h_e, point,
                      sky_view_factor=None, sun_visibility_grid=None,
                      occluder_intersector=None, centroids=None,
                      air_idx=None, g_vent=0.0, apports_internes_w=0.0, t_ground=None,
                      frame_g=None):
    """h_e : la valeur DE CETTE HEURE (Lot R — constante du run, ou dérivée du
    vent via h_e_from_wind par l'appelant), une seule pour tout le bâtiment
    (pas par triangle, voir h_e_from_wind).
    sky_view_factor : liste par triangle (précalculée OU recalculée en
    temps réel par l'appelant) ou None -> repli analytique par triangle.
    Occlusion du rayon direct — deux sources mutuellement exclusives :
      - sun_visibility_grid : lookup dans la grille précalculée (Lot C).
      - occluder_intersector + centroids : lancer de rayon réel, à l'azimuth/
        élévation EXACTS de l'heure (mode 'realtime', pas de discrétisation).
    Aucune des deux : pas de test d'occlusion (cos(theta_i) géométrique seul).
    """
    F = np.zeros(n_dof)
    t_ext = point['t_ext']
    sun_el = point['sun_elevation']
    sun_az = point['sun_azimuth']
    e_dir = point['e_dir']
    e_dif = point['e_dif']

    sun_up = sun_el > 0.0
    direction = shadow.sun_direction(sun_az, sun_el) if sun_up else None

    # Test d'occlusion en temps réel : un seul lot de rayons pour tous les
    # triangles faisant face au soleil à cette heure (même principe que
    # shadow.compute_visibility_grid, mais rejoué à chaque heure réelle
    # plutôt que sur une grille (azimuth, élévation) précalculée).
    realtime_blocked = None
    if sun_up and occluder_intersector is not None:
        normals = np.array([g['normal'] for g in triangles_geom])
        facing = normals @ direction > 1e-6
        realtime_blocked = np.zeros(len(triangles_geom), dtype=bool)
        if facing.any():
            idx = np.nonzero(facing)[0]
            origins = centroids[idx]
            directions = np.tile(direction, (len(idx), 1))
            realtime_blocked[idx] = occluder_intersector.intersects_any(origins, directions)

    for i, (mesh, K, C, layers) in enumerate(systems):
        off = offsets[i]
        n = mesh['n_wall_nodes']
        area = areas[i]
        geom = triangles_geom[i]
        # Facteur de vue du ciel : valeur précalculée ou recalculée par
        # lancer de rayons si disponible, sinon repli sur la formule
        # analytique du ciel isotrope sans obstacle (même formule que
        # solver.py).
        if sky_view_factor is not None:
            f_ciel = sky_view_factor[i]
        else:
            f_ciel = (1.0 + math.cos(math.radians(geom['tilt_deg']))) / 2.0

        cos_ti = 0.0
        if sun_up:
            cos_ti = max(float(np.dot(geom['normal'], direction)), 0.0)
            if cos_ti > 0.0:
                if realtime_blocked is not None:
                    if realtime_blocked[i]:
                        cos_ti = 0.0
                elif sun_visibility_grid is not None:
                    if not shadow.lookup_visibility(sun_visibility_grid, i, sun_az, sun_el):
                        cos_ti = 0.0
        e_glo = e_dir * cos_ti + e_dif * f_ciel

        # Lot K : un triangle 'ground' échange avec la température de sol
        # constante plutôt qu'avec l'air extérieur — même conductance h_e
        # (voir to_do.md, un h_e distinct pour le sol reste une extension
        # possible), seule la température cible change.
        t_boundary = t_ground if (geom.get('boundary') == 'ground' and t_ground is not None) else t_ext

        f_local = np.zeros(n)
        f_local[0] += h_e * t_boundary
        for kind, ref, value in wall_solver._propagate_solar(layers, e_glo, mesh):
            if kind == 'surface':
                f_local[ref] += value
            elif kind == 'interior':
                # Rayonnement transmis intégralement (aucune couche opaque
                # rencontrée, ex. un simple/double vitrage) : a fini de
                # traverser la paroi, chauffe directement l'air plutôt que de
                # disparaître du bilan — voir wall_solver._propagate_solar.
                # air_idx (contrairement à f_local) est un indice GLOBAL,
                # partagé entre tous les triangles : ajouté directement à F,
                # à l'échelle de CE triangle (value est par m², d'où *area).
                if air_idx is not None:
                    F[air_idx] += value * area
            else:
                layer_idx = ref
                s0, s1 = mesh['layer_start_node'][layer_idx], mesh['layer_end_node'][layer_idx]
                length_layer = layers[layer_idx]['e']
                s_vol = value / length_layer
                for el_ in range(s0, s1):
                    l_e = mesh['element_length'][el_]
                    contrib = s_vol * l_e / 2.0
                    f_local[el_] += contrib
                    f_local[el_ + 1] += contrib

        F[off:off + n] += f_local * area

        # Cadre de fenêtre (Lot I) : résistance directe vers le nœud d'air,
        # exactement comme g_vent/apports_internes_w plus bas, mais PAR triangle
        # (voir _assemble_global_kc) — utilise le même t_boundary que la paroi
        # elle-même (t_ground si ce triangle est 'ground', t_ext sinon ; combo
        # non réaliste pour une fenêtre mais géré sans crash).
        if frame_g is not None and frame_g[i] and air_idx is not None:
            F[air_idx] += frame_g[i] * t_boundary

    # Renouvellement d'air + apports internes : deux sources directes sur le
    # nœud d'air global, hors de la boucle par triangle — ni la ventilation ni
    # les apports internes (occupants, éclairage, électroménager, Lot H)
    # n'appartiennent à une paroi en particulier, un seul terme de chaque pour
    # tout le bâtiment (voir run_building_simulation). apports_internes_w est
    # une puissance directe (W), pas une conductance : pas de multiplication
    # par t_ext, contrairement à g_vent.
    if air_idx is not None:
        if g_vent:
            F[air_idx] += g_vent * t_ext
        if apports_internes_w:
            F[air_idx] += apports_internes_w

    return F


def _g_vent_from(source):
    """g_vent (W/K absolu) à partir d'un dict portant debit_vent_m3h/eta_recup_vent
    — factorisé pour être appliqué identiquement à `interior` (valeur constante) ou
    à chaque entrée d'un `planning` horaire (Lot Q)."""
    return 0.34 * source.get('debit_vent_m3h', 0.0) * (1.0 - source.get('eta_recup_vent', 0.0))


def _factorize_for(K_global_base, K_e_pattern, C_global, air_idx, mode, g_vent, h_e):
    """Construit K_global = K_global_base + h_e*K_e_pattern (Lot R — relation
    linéaire, voir _assemble_global_kc) puis K_global[air_idx,air_idx] += g_vent
    (Lot Q), puis factorise (spla.splu, l'opération coûteuse) le(s) système(s)
    linéaire(s) nécessaires au mode donné — à appeler une fois PAR COMBINAISON
    DISTINCTE (g_vent, h_e) rencontrée dans le run (jamais une fois par heure
    simulée : K_global_base/C_global ne sont pas mutées, `.tolil()`/l'addition
    creuse en créent toujours une copie). g_vent : Lot Q (jusqu'à 24 valeurs
    distinctes via `planning`, sinon une seule constante). h_e : Lot R (jusqu'à
    quelques dizaines de valeurs distinctes via le vent réel arrondi au m/s
    près, sinon une seule constante) — vérifié en réel (to_do.md, Lot R) que le
    produit des deux reste largement absorbable sur un an de données réelles
    (quelques centaines de combinaisons au pire, ~30 s de calcul).

    Retourne un dict :
      'imposed'    : {'solver', 'col_saved', 'dirichlet_row'}
      'free'       : {'solver'}
      'thermostat' : {'solver', 'pinned_solver', 'A_free', 'col_saved_pinned'}
                      (A_free conservée pour le résidu HVAC, voir la boucle horaire)
    """
    K_global = (K_global_base + h_e * K_e_pattern).tolil()
    K_global[air_idx, air_idx] += g_vent
    K_global = K_global.tocsc()
    A_free = (C_global / DT_SECONDS + K_global).tocsc()

    if mode == 'imposed':
        dirichlet_row = air_idx
        col_saved = np.asarray(A_free[:, dirichlet_row].todense()).flatten()
        A_pinned_imposed = A_free.tolil()
        A_pinned_imposed[dirichlet_row, :] = 0.0
        A_pinned_imposed[:, dirichlet_row] = 0.0
        A_pinned_imposed[dirichlet_row, dirichlet_row] = 1.0
        A_pinned_imposed = A_pinned_imposed.tocsc()
        return {'solver': spla.splu(A_pinned_imposed), 'col_saved': col_saved, 'dirichlet_row': dirichlet_row}

    if mode == 'free':
        return {'solver': spla.splu(A_free)}

    # thermostat
    free_solver = spla.splu(A_free)
    A_pinned = A_free.tolil()
    col_saved_pinned = np.asarray(A_free[:, air_idx].todense()).flatten()
    A_pinned[air_idx, :] = 0.0
    A_pinned[:, air_idx] = 0.0
    A_pinned[air_idx, air_idx] = 1.0
    A_pinned = A_pinned.tocsc()
    return {
        'solver': free_solver, 'pinned_solver': spla.splu(A_pinned),
        'A_free': A_free, 'col_saved_pinned': col_saved_pinned,
    }


def run_building_simulation(building_envelope, paroi_layers_by_id, sun_visibility, payload,
                             environment_envelope=None, progress_cb=None, paroi_frame_by_id=None):
    """payload : {dx_max, h_e, h_e_dynamic?, interior: {mode, h_i?, h_i_auto?, c_air_int,
    t_int?, t_min?, t_max?, debit_vent_m3h?, eta_recup_vent?, apports_internes_w?}, t_init,
    weather: [{t_ext, sun_azimuth, sun_elevation, e_dir, e_dif, wind_m_s?}, ...],
    shadow_mode: 'precomputed' | 'realtime' (défaut 'precomputed')} — h_e_dynamic/h_i_auto :
    voir plus bas (Lot R).

    paroi_frame_by_id (Lot I, optionnel) : {paroi_model_id: (frame_u, frame_fraction)} pour
    les seuls modèles de paroi qui ont un cadre (voir ParoiModel.frame_u/frame_fraction —
    frame_u déjà inclusif des résistances superficielles, comme Uf/Uw dans le vocabulaire
    fenêtre standard). Pour un triangle dont le paroi_model_id y figure : le maillage
    multicouche existant (solaire, capacité) garde sa physique complète mais sur l'aire
    RÉDUITE (1 - frame_fraction) * aire_triangle — le cadre lui-même est traité comme une
    résistance directe extérieur -> nœud d'air, exactement comme g_vent (Lot G), de
    conductance frame_fraction * frame_u * aire_triangle (voir _assemble_global_kc/
    _assemble_F_hour). Pas de nœud de surface propre pour le cadre : pas de comportement
    optique (tau=0 implicite), capacité thermique négligeable — hypothèses assumées, cf.
    to_do.md Lot I.

    debit_vent_m3h/eta_recup_vent (modes 'free'/'thermostat' uniquement, ignorés en 'imposed') :
    renouvellement d'air (infiltrations/VMC), voir le calcul de g_vent plus bas.

    apports_internes_w (modes 'free'/'thermostat' uniquement, ignoré en 'imposed' — Lot H) :
    puissance constante (occupants, éclairage, électroménager) injectée directement au nœud
    d'air global via F[air_idx].

    t_ground (payload, Lot K) : température de sol constante — utilisée à la place de
    weather[h]['t_ext'] pour tout triangle dont envelope.triangles[i]['boundary'] == 'ground'.
    Sans effet sur les triangles 'exterior_air' (comportement historique, défaut).

    planning (payload, optionnel — Lot Q) : liste de 24 dicts {debit_vent_m3h?,
    eta_recup_vent?, apports_internes_w?}, un par heure de la journée (index 0 =
    minuit), qui REMPLACENT les constantes de `interior` heure par heure — modes
    'free'/'thermostat' uniquement, comme pour les constantes équivalentes. Absent
    (défaut) -> comportement inchangé (valeurs constantes de `interior`).
    heure_debut (payload, 0-23, défaut 0) : heure du premier point de `weather`,
    utilisée pour indexer le planning (`planning[(heure_debut + hour_idx) % 24]`).

    h_e_dynamic (payload, optionnel booléen, défaut False — Lot R) : si True,
    h_e de chaque heure est dérivé de weather[h]['wind_m_s'] (requis pour
    CHAQUE heure dans ce cas — voir BuildingCalculRequestSerializer) via
    h_e_from_wind (corrélation de Jürges), plutôt que la constante `h_e` du
    payload (alors ignorée). Le vent est arrondi au m/s le plus proche avant
    conversion — indispensable pour borner le nombre de valeurs DISTINCTES de
    h_e rencontrées (vérifié en réel : 14-21 sur une année complète, Paris et
    Brest — voir to_do.md) plutôt qu'une par heure si le vent brut (continu)
    était utilisé tel quel.

    h_i_auto (payload.interior, optionnel booléen, défaut False — Lot R) : si
    True, h_i de chaque triangle est dérivé de son tilt_deg (déjà calculé par
    api.geometry) via h_i_from_tilt (ISO 6946 : mur/plafond/plancher), plutôt
    que la constante `interior.h_i` (alors ignorée, et non requise). Contrairement
    à h_e, h_i_auto ne varie jamais dans le temps (seulement d'un triangle à
    l'autre) : aucun impact sur le nombre de factorisations.

    Point dur traité : g_vent (Lot Q) ET h_e (Lot R) entrent dans K_global (pas
    seulement F), donc la factorisation LU (spla.splu, l'étape coûteuse) est
    refaite une fois PAR COMBINAISON DISTINCTE (g_vent, h_e) rencontrée — au
    plus 24 valeurs de g_vent (planning) x quelques dizaines de valeurs de h_e
    (vent réel arrondi), jamais une par heure simulée — voir _factorize_for
    (bundles construits PARESSEUSEMENT, seulement les combinaisons réellement
    rencontrées, pas le produit cartésien complet) et MAX_DISTINCT_BOUNDARY_COMBOS
    pour le garde-fou. apports_internes_w, lui, n'affecte que F (déjà recalculé
    à chaque heure) : aucun problème de factorisation de ce côté.

    'precomputed' : utilise la grille d'ombrage + facteur de vue du ciel déjà
    calculés (api.shadow, Lot C) — rapide, discrétisé sur une grille
    (azimuth, élévation), nécessite d'avoir lancé le précalcul au préalable.
    'realtime' : reconstruit le maillage occulteur une seule fois puis relance
    un vrai lancer de rayons à l'azimuth/élévation EXACTS de chaque heure —
    aucune discrétisation, mais nettement plus lent (jusqu'à ~50x plus de
    lancers de rayons qu'un précalcul sur un an d'heures) ; le facteur de vue
    du ciel est aussi recalculé une fois (pas par heure, il ne dépend que de
    la géométrie) plutôt que d'utiliser la formule analytique par défaut.
    """
    triangles = building_envelope['triangles']
    if not triangles:
        raise BuildingSimulationError("Le bâtiment n'a aucun triangle.")

    dx_max = payload['dx_max']
    h_e_constant = payload['h_e']
    h_e_dynamic = payload.get('h_e_dynamic', False)
    interior = payload['interior']
    h_i_auto = interior.get('h_i_auto', False)
    mode = interior['mode']
    t_init = payload['t_init']
    weather = payload['weather']
    shadow_mode = payload.get('shadow_mode', 'precomputed')
    t_ground = payload.get('t_ground')

    if len(weather) > MAX_WEATHER_POINTS:
        raise BuildingSimulationError(f"{len(weather)} pas horaires, au-delà de la limite de {MAX_WEATHER_POINTS}.")
    if not weather:
        raise BuildingSimulationError("La série météo est vide.")
    if shadow_mode not in ('precomputed', 'realtime'):
        raise BuildingSimulationError(f"shadow_mode inconnu : {shadow_mode!r}")
    if shadow_mode == 'realtime' and len(triangles) * len(weather) > MAX_REALTIME_RAYCAST_OPS:
        raise BuildingSimulationError(
            f"{len(triangles)} triangles × {len(weather)} heures dépasse la limite du mode temps réel "
            f"({MAX_REALTIME_RAYCAST_OPS} — utiliser le mode précalculé, ou réduire le maillage/la période)."
        )
    if h_e_dynamic:
        # Garde-fou défensif — le serializer valide déjà ceci, mais
        # run_building_simulation reste appelable directement (voir tests.py).
        missing = [i for i, p in enumerate(weather) if p.get('wind_m_s') is None]
        if missing:
            raise BuildingSimulationError(
                f"h_e_dynamic actif : vent manquant pour {len(missing)} heure(s) (ex. index {missing[0]})."
            )
    if not h_i_auto and 'h_i' not in interior:
        raise BuildingSimulationError("interior.h_i est requis sauf si interior.h_i_auto est activé.")

    areas = [tri['area'] for tri in triangles]

    # Cadre de fenêtre (Lot I) : réduit l'aire "vitrage" (maillage multicouche
    # existant, solaire+capacité inchangés) à (1-frame_fraction)*aire pour les
    # triangles concernés, et calcule la conductance directe du cadre
    # (frame_fraction*frame_u*aire, sur l'aire ORIGINALE — c'est bien l'aire du
    # cadre lui-même) — voir _assemble_global_kc/_assemble_F_hour pour son usage.
    frame_g = [0.0] * len(triangles)
    if paroi_frame_by_id:
        for i, tri in enumerate(triangles):
            frame_info = paroi_frame_by_id.get(tri.get('paroi_model_id'))
            if frame_info is None:
                continue
            frame_u, frame_fraction = frame_info
            if not (0.0 <= frame_fraction < 1.0):
                # frame_fraction == 1.0 annulerait entièrement l'aire "vitrage" du
                # triangle -> lignes nulles dans la matrice globale -> système
                # singulier (voir ParoiModelSerializer.frame_fraction, plafonné à
                # 0,95 côté API — ce garde-fou couvre tout appelant direct de
                # run_building_simulation, hors du serializer).
                raise BuildingSimulationError(
                    f"frame_fraction doit être dans [0, 1[ (obtenu {frame_fraction!r})."
                )
            frame_g[i] = frame_fraction * frame_u * areas[i]
            areas[i] = areas[i] * (1.0 - frame_fraction)

    occluder_intersector = None
    centroids = None
    sky_view_factor = None
    sun_visibility_grid = None

    if shadow_mode == 'realtime':
        occluder_intersector = shadow.build_occluder_intersector(building_envelope, environment_envelope)
        vertices = building_envelope['vertices']
        centroids = np.array([
            np.mean([vertices[j] for j in tri['v']], axis=0) + np.array(tri['normal']) * shadow.RAY_ORIGIN_EPSILON
            for tri in triangles
        ])
        sky_view_factor = shadow.compute_sky_view_factors(building_envelope, environment_envelope)
    elif sun_visibility is not None and sun_visibility.get('per_triangle'):
        sun_visibility_grid = sun_visibility
        if 'sky_view_factor' in sun_visibility:
            sky_view_factor = sun_visibility['sky_view_factor']
    # h_i (Lot R) : soit une constante répétée pour tous les triangles (défaut,
    # comportement historique), soit dérivée de tilt_deg par triangle (ISO 6946
    # via h_i_from_tilt) si interior.h_i_auto est activé.
    if h_i_auto:
        h_i_list = [h_i_from_tilt(tri['tilt_deg']) for tri in triangles]
    else:
        h_i_list = [interior['h_i']] * len(triangles)

    systems = _build_triangle_systems(triangles, paroi_layers_by_id, dx_max)
    K_global, C_global, K_e_pattern, offsets, air_idx, n_dof = _assemble_global_kc(
        systems, areas, h_i_list, frame_g=frame_g,
    )

    planning = payload.get('planning')
    heure_debut = payload.get('heure_debut', 0)

    # g_vent_by_slot/apports_by_slot : listes de 24 valeurs (une par heure de la
    # journée, Lot Q) si `planning` est fourni, sinon None -> une seule valeur
    # constante pour tout le run (comportement des Lots G/H, inchangé). Comme
    # avant, sans effet en mode 'imposed' (Dirichlet écrase la ligne du nœud
    # d'air) : le planning n'est pas exploité dans ce mode, exactement comme
    # les constantes équivalentes.
    g_vent_by_slot = None
    apports_by_slot = None
    g_vent_constant = 0.0
    apports_constant = 0.0
    if mode in ('free', 'thermostat'):
        if planning:
            g_vent_by_slot = [_g_vent_from(p) for p in planning]
            apports_by_slot = [p.get('apports_internes_w', 0.0) for p in planning]
        else:
            # 0,34 Wh/(m3.K) = capacité thermique volumique de l'air (valeur
            # usuelle) ; (1 - eta_recup_vent) réduit la perte nette pour une VMC
            # double flux à récupération de chaleur — voir _g_vent_from.
            g_vent_constant = _g_vent_from(interior)
            apports_constant = interior.get('apports_internes_w', 0.0)

    if mode in ('free', 'thermostat'):
        C_global = C_global.tolil()
        C_global[air_idx, air_idx] += interior['c_air_int']
        C_global = C_global.tocsc()
        if mode == 'thermostat':
            t_min = interior['t_min']
            t_max = interior['t_max']
    elif mode != 'imposed':
        raise BuildingSimulationError(f"Mode intérieur inconnu : {mode!r}")
    # mode == 'imposed' : T_air figée par Dirichlet, sans nœud d'air libre — la
    # ventilation n'a aucune incidence sur la solution (même raisonnement que le
    # mode 'imposed' de solver.py).

    # Factorisation LU (spla.splu, l'étape coûteuse) : une fois PAR COMBINAISON
    # DISTINCTE (g_vent, h_e) rencontrée — construite PARESSEUSEMENT (au premier
    # usage, mise en cache) plutôt que le produit cartésien complet à l'avance :
    # sur un an de données réelles, les combinaisons RÉELLEMENT rencontrées sont
    # nettement moins nombreuses que g_vent_classes x h_e_classes (vérifié en
    # réel, to_do.md Lot R) — voir _factorize_for.
    bundles = {}

    T = np.full(n_dof, float(t_init))
    t_air_series = [float(T[air_idx])]
    heating_j = 0.0
    cooling_j = 0.0
    flux_series = []  # flux net (W) de l'enveloppe vers l'intérieur, par heure

    n_hours_total = len(weather)
    for hour_idx, point in enumerate(weather):
        if g_vent_by_slot is not None:
            slot = (heure_debut + hour_idx) % 24
            g_vent = g_vent_by_slot[slot]
            apports_internes_w = apports_by_slot[slot]
        else:
            g_vent = g_vent_constant
            apports_internes_w = apports_constant

        if h_e_dynamic:
            # Arrondi au m/s le plus proche AVANT la formule de Jürges : c'est ce
            # qui borne le nombre de valeurs distinctes de h_e (14-21 sur un an
            # réel, vérifié) plutôt qu'une par heure si le vent continu brut
            # entrait directement dans h_e_from_wind.
            h_e = h_e_from_wind(round(point['wind_m_s']))
        else:
            h_e = h_e_constant

        key = (g_vent, h_e)
        bundle = bundles.get(key)
        if bundle is None:
            if len(bundles) >= MAX_DISTINCT_BOUNDARY_COMBOS:
                raise BuildingSimulationError(
                    f"Plus de {MAX_DISTINCT_BOUNDARY_COMBOS} combinaisons distinctes "
                    "(ventilation x vent) rencontrées — série météo anormalement bruitée."
                )
            bundle = _factorize_for(K_global, K_e_pattern, C_global, air_idx, mode, g_vent, h_e)
            bundles[key] = bundle

        F = _assemble_F_hour(
            systems, areas, offsets, n_dof, triangles, h_e, point,
            sky_view_factor=sky_view_factor, sun_visibility_grid=sun_visibility_grid,
            occluder_intersector=occluder_intersector, centroids=centroids,
            air_idx=air_idx, g_vent=g_vent, apports_internes_w=apports_internes_w, t_ground=t_ground,
            frame_g=frame_g,
        )
        b_free = (C_global / DT_SECONDS) @ T + F

        if mode == 'imposed':
            b = b_free - bundle['col_saved'] * interior['t_int']
            b[bundle['dirichlet_row']] = interior['t_int']
            T_next = bundle['solver'].solve(b)
            hvac = 0.0
        elif mode == 'free':
            T_next = bundle['solver'].solve(b_free)
            hvac = 0.0
        else:  # thermostat
            T_candidate = bundle['solver'].solve(b_free)
            air_temp = T_candidate[air_idx]
            if air_temp < t_min:
                t_pin = t_min
            elif air_temp > t_max:
                t_pin = t_max
            else:
                t_pin = None

            if t_pin is None:
                T_next = T_candidate
                hvac = 0.0
            else:
                b_pinned = b_free - bundle['col_saved_pinned'] * t_pin
                b_pinned[air_idx] = t_pin
                T_next = bundle['pinned_solver'].solve(b_pinned)
                # Résidu à la ligne du DOF d'air pincé : exactement la puissance
                # HVAC qu'il a fallu injecter (positif) ou retirer (négatif) pour
                # que la solution pincée reste cohérente avec le système d'origine.
                hvac = float((bundle['A_free'] @ T_next)[air_idx] - b_free[air_idx])
                if hvac > 0:
                    heating_j += hvac * DT_SECONDS
                else:
                    cooling_j += -hvac * DT_SECONDS

        # Flux net de l'enveloppe vers l'intérieur à cette heure : somme, sur
        # tous les triangles, de h_i*A_i*(T_surface,i - T_air) — c'est
        # exactement ce que chaque paroi injecte dans le nœud d'air. h_i_list[i]
        # (Lot R) remplace l'ancien h_i scalaire unique.
        flux = 0.0
        t_air_next = T_next[air_idx]
        for i, (mesh, K, C, layers) in enumerate(systems):
            off = offsets[i]
            n = mesh['n_wall_nodes']
            t_surf = T_next[off + n - 1]
            flux += h_i_list[i] * areas[i] * (t_surf - t_air_next)
        flux_series.append(flux)

        T = T_next
        t_air_series.append(float(t_air_next))

        if progress_cb:
            progress_cb(hour_idx + 1, n_hours_total)

    t_air_arr = np.array(t_air_series[1:])
    flux_arr = np.array(flux_series)
    positive_kwh = float(flux_arr[flux_arr > 0].sum()) * DT_SECONDS / 3.6e6
    negative_kwh = float(flux_arr[flux_arr < 0].sum()) * DT_SECONDS / 3.6e6

    # État final par triangle (dernière heure), pour une visualisation 3D du
    # résultat — pas toute la série temporelle par nœud (bien trop volumineux
    # pour un an d'heures × des milliers de triangles).
    final_exterior_surface_temp = []
    final_interior_surface_temp = []
    for i, (mesh, K, C, layers) in enumerate(systems):
        off = offsets[i]
        n = mesh['n_wall_nodes']
        final_exterior_surface_temp.append(float(T[off]))
        final_interior_surface_temp.append(float(T[off + n - 1]))

    return {
        'hours': len(weather),
        't_air': t_air_series,
        't_air_mean': float(t_air_arr.mean()),
        'heating_kwh': heating_j / 3.6e6,
        'cooling_kwh': cooling_j / 3.6e6,
        'final_exterior_surface_temp': final_exterior_surface_temp,
        'final_interior_surface_temp': final_interior_surface_temp,
        'envelope_flux_w': flux_series,
        'flux_positive_kwh': positive_kwh,
        'flux_negative_kwh': negative_kwh,
    }
