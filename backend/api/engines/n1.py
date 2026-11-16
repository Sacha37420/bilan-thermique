"""N1 — modèle horaire réduit 5R1C (ISO 13790 annexe C, repris par l'ISO 52016-1) (Lot 10, refonte).

Trois nœuds (air θ_air, surface θ_s, masse θ_m), cinq conductances, une capacité ; schéma de
Crank-Nicolson au pas horaire, besoin de chauffage/refroidissement par interpolation linéaire
(C.4 de la norme). **Vectorisé sur S jeux de paramètres** : un tirage Monte-Carlo de 200 jeux coûte
à peine plus qu'un calcul unique (Lot 17).

Sorties : besoins (kWh), puissance maximale, températures d'air et **opérative**
(θ_op = 0,3 θ_air + 0,7 θ_s — confort d'été, `refonte.md` D9), bilan par poste, besoins mensuels.
"""

import time

import numpy as np

from . import solar
from .inputs import ground_series
from .inputs import ORIENTATIONS

R_SE = 0.04
H_IS = 3.45      # W/m²K, couplage air–surface (par m² de surface intérieure)
H_MS = 9.1       # W/m²K, couplage surface–masse
A_T_FACTOR = 4.5
PHI_TEST_W_M2 = 10.0
MONTH_HOURS = np.cumsum([0, 744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744])


class N1Error(ValueError):
    pass


def schedules(hidx, residential, t_heat):
    """(facteur d'apports internes, consigne de chauffage) heure par heure.

    Logement : apports et consigne constants (moyenne). Autres usages : occupé 8 h–19 h du lundi au
    vendredi (jour 0 de la série = lundi, convention), apports × 2,2 occupé et × 0,3 sinon,
    consigne réduite de 3 °C hors occupation."""
    if residential:
        return np.ones(len(hidx)), np.full(len(hidx), t_heat)
    hour, day = hidx % 24, (hidx // 24) % 7
    occ = (hour >= 8) & (hour < 19) & (day < 5)
    return np.where(occ, 2.2, 0.3), np.where(occ, t_heat, t_heat - 3.0)


def solar_terms(p, az, el, dni, dhi, t_e=None, hidx=None, by_orientation=None):
    """(gains solaires par les baies, gains par les parois opaques, irradiance par orientation) — W.

    Protections solaires (Lot 14) : `p.devices` → facteurs horaires sur le direct et le diffus des
    baies de chaque orientation (`engines.shading.factors`). `by_orientation` (dict, facultatif) reçoit
    les gains par les baies de chaque orientation (kWh), pour la restitution par façade."""
    from . import shading as sh
    win = np.zeros(len(az))
    opq = np.zeros(len(az))
    irr = {}
    if hidx is None:
        hidx = np.arange(len(az))
    if t_e is None:
        t_e = np.full(len(az), 20.0)
    occupied = None
    if p.devices and not p.residential:
        hour, day = hidx % 24, (hidx // 24) % 7
        occupied = (hour >= 8) & (hour < 19) & (day < 5)
    for o in ORIENTATIONS:
        w = p.walls[o]
        if w['window'] <= 0 and w['opaque'] <= 0:
            irr[o] = np.zeros(len(az))
            continue
        total, beam = solar.surface_irradiance(az, el, dni, dhi, solar.ORIENT_AZ[o], 90.0,
                                               horizon_deg=p.horizon.get(o, 0.0) or 0.0,
                                               horizon_profile=(p.horizon_profile or {}).get(o))
        diffuse_part = total - beam
        if p.devices:
            fb, fd = sh.factors(p.devices, o, solar.ORIENT_AZ[o], az, el, hidx, total, t_e, p.g_window, occupied)
            beam, diffuse_part = beam * fb, diffuse_part * fd
        total_win = beam + diffuse_part
        irr[o] = total
        gains_o = w['window'] * p.g_window * p.frame_factor * total_win
        win += gains_o
        if by_orientation is not None:
            by_orientation[o] = round(float(gains_o.sum() / 1000.0), 1)
        opq += p.absorptance * R_SE * w['u'] * w['opaque'] * total
    roof_total, _ = solar.surface_irradiance(az, el, dni, dhi, 180.0, 0.0)
    opq += p.absorptance * R_SE * p.u_roof * p.roof_area * roof_total
    irr['H'] = roof_total
    return win, opq, irr


def run(params, weather, *, t_init=None, keep_series=True):
    """Simule S jeux de paramètres (liste de `Params`) sur la série météo. Rend une liste de résultats."""
    if not weather:
        raise N1Error('Série météo vide.')
    if len(weather) > 8784:
        raise N1Error('Série météo de plus d’un an.')
    t0 = time.monotonic()
    ps = params if isinstance(params, (list, tuple)) else [params]
    S = len(ps)
    t_e, az, el, dni, dhi, hidx = solar.arrays(weather)
    T = len(t_e)

    def vec(f):
        return np.array([f(p) for p in ps], dtype=float)

    t_heat = np.empty((S, T))
    for i, p in enumerate(ps):
        t_heat[i] = schedules(hidx, p.residential, p.t_heat)[1]
    # Lot 32 : température vue par le plancher bas heure par heure (sol simulé, vide sanitaire, sous-sol) et U
    # propre du plancher vers elle.
    t_g_all = np.empty((S, T))
    u_fl = np.empty(S)
    ground_desc = []
    for i, p in enumerate(ps):
        g, u, d = ground_series(p, t_e, t_heat[i])
        t_g_all[i], u_fl[i] = g, u
        ground_desc.append(d)

    a_f = vec(lambda p: p.a_floor)
    a_m = vec(lambda p: p.a_m_factor) * a_f
    a_t = A_T_FACTOR * a_f
    c_m = vec(lambda p: p.c_m) * a_f
    h_w = vec(lambda p: p.h_tr_w)
    h_op = vec(lambda p: p.h_tr_op_air)
    h_fl = vec(lambda p: p.floor_area) * u_fl
    h_ve = np.maximum(vec(lambda p: p.h_ve), 1e-6)
    h_is = H_IS * a_t
    h_ms = H_MS * a_m
    h_op_tot = h_op + h_fl
    h_em = 1.0 / np.maximum(1.0 / np.maximum(h_op_tot, 1e-9) - 1.0 / h_ms, 1e-9)
    floor_share = np.where(h_op_tot > 0, h_fl / np.maximum(h_op_tot, 1e-9), 0.0)
    h1 = 1.0 / (1.0 / h_ve + 1.0 / h_is)
    h2 = h1 + h_w
    h3 = 1.0 / (1.0 / h2 + 1.0 / h_ms)

    gain_f, _ = schedules(hidx, ps[0].residential, 0.0)
    phi_int = vec(lambda p: p.gains_w_m2)[:, None] * a_f[:, None] * gain_f[None, :]
    sol_w = np.zeros((S, T))
    sol_o = np.zeros((S, T))
    irr0 = None
    by_o = [{} for _ in ps]
    for i, p in enumerate(ps):
        w, o, irr = solar_terms(p, az, el, dni, dhi, t_e, hidx, by_o[i])
        sol_w[i], sol_o[i] = w, o
        if irr0 is None:
            irr0 = irr
    phi_sol = sol_w + sol_o
    t_cool = np.array([p.t_cool if p.t_cool is not None else np.inf for p in ps])

    phi_ia = 0.5 * phi_int
    share_m = a_m / a_t
    share_s = 1.0 - share_m - h_w / (H_MS * a_t)
    phi_m_all = share_m[:, None] * (0.5 * phi_int + phi_sol)
    phi_st_all = share_s[:, None] * (0.5 * phi_int + phi_sol)

    theta_m = np.full(S, t_init if t_init is not None else float(ps[0].t_heat))
    cap = c_m / 3600.0
    t_air = np.empty((S, T))
    t_s = np.empty((S, T))
    phi_hc = np.empty((S, T))

    def step(tm_prev, te, tg, phi_ia_t, phi_st_t, phi_m_t, phi):
        # Plancher : sa part de H_tr_em échange avec le sol (θ_g) et non avec l'air extérieur.
        te_m = te + floor_share * (tg - te)
        phi_mtot = phi_m_t + h_em * te_m + h3 * (phi_st_t + h_w * te + h1 * ((phi_ia_t + phi) / h_ve + te)) / h2
        tm = (tm_prev * (cap - 0.5 * (h3 + h_em)) + phi_mtot) / (cap + 0.5 * (h3 + h_em))
        tm_avg = 0.5 * (tm + tm_prev)
        ts = (h_ms * tm_avg + phi_st_t + h_w * te + h1 * (te + (phi_ia_t + phi) / h_ve)) / (h_ms + h_w + h1)
        ta = (h_is * ts + h_ve * te + phi_ia_t + phi) / (h_is + h_ve)
        return tm, ts, ta

    zero = np.zeros(S)
    test = PHI_TEST_W_M2 * a_f
    for k in range(T):
        te = t_e[k]
        args = (t_g_all[:, k], phi_ia[:, k], phi_st_all[:, k], phi_m_all[:, k])
        tm0, ts0, ta0 = step(theta_m, te, *args, zero)
        need_h = ta0 < t_heat[:, k]
        need_c = ta0 > t_cool
        phi = zero.copy()
        if need_h.any() or need_c.any():
            _, _, ta10 = step(theta_m, te, *args, test)
            slope = (ta10 - ta0) / test
            slope = np.where(np.abs(slope) < 1e-12, 1e-12, slope)
            phi = np.where(need_h, (t_heat[:, k] - ta0) / slope, phi)
            phi = np.where(need_c, (t_cool - ta0) / slope, phi)
            tm0, ts0, ta0 = step(theta_m, te, *args, phi)
        theta_m = tm0
        t_air[:, k], t_s[:, k], phi_hc[:, k] = ta0, ts0, phi

    heat = np.clip(phi_hc, 0, None)
    cool = np.clip(-phi_hc, 0, None)
    t_op = 0.3 * t_air + 0.7 * t_s
    loss_tr = (h_w + h_op)[:, None] * (t_air - t_e[None, :]) + h_fl[:, None] * (t_air - t_g_all)
    loss_ve = h_ve[:, None] * (t_air - t_e[None, :])
    dur = time.monotonic() - t0
    results = []
    month_edges = MONTH_HOURS[MONTH_HOURS <= T].tolist()
    if month_edges[-1] != T:
        month_edges.append(T)
    for i in range(S):
        r = {
            'level': 'N1', 'hours': T,
            'heating_kwh': float(heat[i].sum() / 1000.0), 'cooling_kwh': float(cool[i].sum() / 1000.0),
            'heating_kw_max': round(float(heat[i].max() / 1000.0), 2),
            't_air_mean': float(t_air[i].mean()), 't_op_max': round(float(t_op[i].max()), 2),
            'balance': {
                'transmission_kwh': round(float(np.clip(loss_tr[i], 0, None).sum() / 1000.0), 1),
                'ventilation_kwh': round(float(np.clip(loss_ve[i], 0, None).sum() / 1000.0), 1),
                'solar_windows_kwh': round(float(sol_w[i].sum() / 1000.0), 1),
                'solar_opaque_kwh': round(float(sol_o[i].sum() / 1000.0), 1),
                'internal_kwh': round(float(phi_int[i].sum() / 1000.0), 1),
            },
            'monthly_heating_kwh': [round(float(heat[i][a:b].sum() / 1000.0), 1)
                                    for a, b in zip(month_edges[:-1], month_edges[1:])],
            'duration_s': round(dur / S, 4),
            'sol': ground_desc[i],
        }
        if keep_series:
            r['t_air'] = [round(float(x), 2) for x in t_air[i]]
            r['t_op'] = [round(float(x), 2) for x in t_op[i]]
            r['phi_hc_w'] = [round(float(x), 1) for x in phi_hc[i]]
        results.append(r)
    for i, r in enumerate(results):
        r['solar_windows_by_orientation_kwh'] = by_o[i]
    if irr0 is not None and keep_series:
        results[0]['solar_by_orientation_kwh_m2'] = {o: round(float(v.sum() / 1000.0), 1) for o, v in irr0.items()}
    return results


def run_one(params, weather, **kw):
    return run([params], weather, **kw)[0]
