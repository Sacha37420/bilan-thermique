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
d'air partagé) est résolu en creux (scipy.sparse), avec factorisation LU une
seule fois (les matrices ne changent pas d'une heure à l'autre, seul le
second membre change).

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


class BuildingSimulationError(ValueError):
    pass


def _sun_direction(azimuth_deg, elevation_deg):
    """Même convention que api.shadow.sun_direction (Z-up, azimuth 0°=+Y)."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    return np.array([
        math.sin(az) * math.cos(el),
        math.cos(az) * math.cos(el),
        math.sin(el),
    ])


def _build_triangle_systems(triangles, paroi_layers_by_id, dx_max, h_e, h_i):
    """Une entrée par triangle : (mesh, K, C, layers), K/C par unité de
    surface avec h_e/h_i déjà posés sur les nœuds 0/dernier (comme dans
    solver.run_simulation) — mise en cache par (paroi_model_id, dx_max),
    beaucoup de triangles partageant le même modèle de paroi."""
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
            K = K.copy()
            K[0, 0] += h_e
            K[-1, -1] += h_i
            cache[key] = (mesh, K, C, layers)
        systems.append(cache[key])
        total_nodes += cache[key][0]['n_wall_nodes']
        if total_nodes > MAX_TOTAL_DOF:
            raise BuildingSimulationError(
                f"Le maillage total ({total_nodes}+ nœuds) dépasse la limite de {MAX_TOTAL_DOF} — "
                "augmenter dx_max."
            )
    return systems


def _assemble_global_kc(systems, areas, h_i):
    offsets = []
    total = 0
    for mesh, K, C, layers in systems:
        offsets.append(total)
        total += mesh['n_wall_nodes']
    air_idx = total
    n_dof = total + 1

    K_global = sp.lil_matrix((n_dof, n_dof))
    C_global = sp.lil_matrix((n_dof, n_dof))

    for i, (mesh, K, C, layers) in enumerate(systems):
        off = offsets[i]
        n = mesh['n_wall_nodes']
        area = areas[i]
        last = off + n - 1

        K_global[off:off + n, off:off + n] += K * area
        C_global[off:off + n, off:off + n] += C * area

        K_global[last, air_idx] -= h_i * area
        K_global[air_idx, last] -= h_i * area
        K_global[air_idx, air_idx] += h_i * area

    return K_global.tocsc(), C_global.tocsc(), offsets, air_idx, n_dof


def _assemble_F_hour(systems, areas, offsets, n_dof, triangles_geom, sun_visibility, h_e, point):
    F = np.zeros(n_dof)
    t_ext = point['t_ext']
    sun_el = point['sun_elevation']
    sun_az = point['sun_azimuth']
    e_dir = point['e_dir']
    e_dif = point['e_dif']

    sun_up = sun_el > 0.0
    direction = _sun_direction(sun_az, sun_el) if sun_up else None

    for i, (mesh, K, C, layers) in enumerate(systems):
        off = offsets[i]
        n = mesh['n_wall_nodes']
        area = areas[i]
        geom = triangles_geom[i]
        tilt = geom['tilt_deg']
        f_ciel = (1.0 + math.cos(math.radians(tilt))) / 2.0

        cos_ti = 0.0
        if sun_up:
            cos_ti = max(float(np.dot(geom['normal'], direction)), 0.0)
            if cos_ti > 0.0 and sun_visibility is not None:
                if not shadow.lookup_visibility(sun_visibility, i, sun_az, sun_el):
                    cos_ti = 0.0
        e_glo = e_dir * cos_ti + e_dif * f_ciel

        f_local = np.zeros(n)
        f_local[0] += h_e * t_ext
        for kind, ref, value in wall_solver._propagate_solar(layers, e_glo, mesh):
            if kind == 'surface':
                f_local[ref] += value
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

    return F


def run_building_simulation(building_envelope, paroi_layers_by_id, sun_visibility, payload, progress_cb=None):
    """payload : {dx_max, h_e, interior: {mode, h_i, c_air_int, t_int?, t_min?, t_max?},
    t_init, weather: [{t_ext, sun_azimuth, sun_elevation, e_dir, e_dif}, ...]}
    """
    triangles = building_envelope['triangles']
    if not triangles:
        raise BuildingSimulationError("Le bâtiment n'a aucun triangle.")

    dx_max = payload['dx_max']
    h_e = payload['h_e']
    interior = payload['interior']
    h_i = interior['h_i']
    mode = interior['mode']
    t_init = payload['t_init']
    weather = payload['weather']

    if len(weather) > MAX_WEATHER_POINTS:
        raise BuildingSimulationError(f"{len(weather)} pas horaires, au-delà de la limite de {MAX_WEATHER_POINTS}.")
    if not weather:
        raise BuildingSimulationError("La série météo est vide.")

    areas = [tri['area'] for tri in triangles]
    systems = _build_triangle_systems(triangles, paroi_layers_by_id, dx_max, h_e, h_i)
    K_global, C_global, offsets, air_idx, n_dof = _assemble_global_kc(systems, areas, h_i)

    if mode == 'free':
        C_global = C_global.tolil()
        C_global[air_idx, air_idx] += interior['c_air_int']
        C_global = C_global.tocsc()
    elif mode == 'thermostat':
        C_global = C_global.tolil()
        C_global[air_idx, air_idx] += interior['c_air_int']
        C_global = C_global.tocsc()
        t_min = interior['t_min']
        t_max = interior['t_max']
    elif mode == 'imposed':
        pass
    else:
        raise BuildingSimulationError(f"Mode intérieur inconnu : {mode!r}")

    A_free = (C_global / DT_SECONDS + K_global).tocsc()

    dirichlet_row = None
    col_saved = None
    A_pinned_solver = None
    if mode == 'imposed':
        dirichlet_row = air_idx
        col_saved = np.asarray(A_free[:, dirichlet_row].todense()).flatten()
        A_free = A_free.tolil()
        A_free[dirichlet_row, :] = 0.0
        A_free[:, dirichlet_row] = 0.0
        A_free[dirichlet_row, dirichlet_row] = 1.0
        A_free = A_free.tocsc()

    free_solver = spla.splu(A_free)

    if mode == 'thermostat':
        A_pinned = A_free.tolil()
        col_saved = np.asarray(A_free[:, air_idx].todense()).flatten()
        A_pinned[air_idx, :] = 0.0
        A_pinned[:, air_idx] = 0.0
        A_pinned[air_idx, air_idx] = 1.0
        A_pinned = A_pinned.tocsc()
        A_pinned_solver = spla.splu(A_pinned)

    T = np.full(n_dof, float(t_init))
    t_air_series = [float(T[air_idx])]
    heating_j = 0.0
    cooling_j = 0.0
    flux_series = []  # flux net (W) de l'enveloppe vers l'intérieur, par heure

    n_hours_total = len(weather)
    for hour_idx, point in enumerate(weather):
        F = _assemble_F_hour(systems, areas, offsets, n_dof, triangles, sun_visibility, h_e, point)
        b_free = (C_global / DT_SECONDS) @ T + F

        if mode == 'imposed':
            b = b_free - col_saved * interior['t_int']
            b[dirichlet_row] = interior['t_int']
            T_next = free_solver.solve(b)
            hvac = 0.0
        elif mode == 'free':
            T_next = free_solver.solve(b_free)
            hvac = 0.0
        else:  # thermostat
            T_candidate = free_solver.solve(b_free)
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
                b_pinned = b_free - col_saved * t_pin
                b_pinned[air_idx] = t_pin
                T_next = A_pinned_solver.solve(b_pinned)
                # Résidu à la ligne du DOF d'air pincé : exactement la puissance
                # HVAC qu'il a fallu injecter (positif) ou retirer (négatif) pour
                # que la solution pincée reste cohérente avec le système d'origine.
                hvac = float((A_free @ T_next)[air_idx] - b_free[air_idx])
                if hvac > 0:
                    heating_j += hvac * DT_SECONDS
                else:
                    cooling_j += -hvac * DT_SECONDS

        # Flux net de l'enveloppe vers l'intérieur à cette heure : somme, sur
        # tous les triangles, de h_i*A_i*(T_surface,i - T_air) — c'est
        # exactement ce que chaque paroi injecte dans le nœud d'air.
        flux = 0.0
        t_air_next = T_next[air_idx]
        for i, (mesh, K, C, layers) in enumerate(systems):
            off = offsets[i]
            n = mesh['n_wall_nodes']
            t_surf = T_next[off + n - 1]
            flux += h_i * areas[i] * (t_surf - t_air_next)
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
