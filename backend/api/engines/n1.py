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


def envelope_terms(p, az, el, dni, dhi, t_e=None, hidx=None, by_orientation=None, evapo=True):
    """Termes horaires d'enveloppe d'un jeu de paramètres :
    {'win' : apports solaires par les baies (W), 'opq' : apports par les parois opaques, puits d'évapotranspiration
    déduits (W), 'irr' : irradiance par orientation, 'h_w' : conductance des vitrages (W/K), ΔR des fermetures compris}.

    Protections solaires (Lot 14, lot 36) : `p.devices` → facteurs sur le direct et le diffus des baies, bandes de mur
    ombrées par les débords, résistance ajoutée par les fermetures (`engines.shading.orientation_effects`). Lot 36 :
    albédo des abords, absorptance de toiture, arbres plantés (`engines.vegetation.tree_beam_factor`), toiture et
    murs végétalisés (U, absorptance du feuillage, puits d'évapotranspiration si `evapo`). `by_orientation` (dict,
    facultatif) reçoit les gains par les baies de chaque orientation (kWh), pour la restitution par façade."""
    from . import shading as sh
    from . import vegetation as veg
    T = len(az)
    win = np.zeros(T)
    opq = np.zeros(T)
    h_w = np.zeros(T)
    irr = {}
    if hidx is None:
        hidx = np.arange(T)
    if t_e is None:
        t_e = np.full(T, 20.0)
    occupied = None
    if p.devices and not p.residential:
        hour, day = hidx % 24, (hidx // 24) % 7
        occupied = (hour >= 8) & (hour < 19) & (day < 5)
    months = sh.month_of(hidx) if p.trees else None
    for o in ORIENTATIONS:
        w = p.walls[o]
        if w['window'] <= 0 and w['opaque'] <= 0:
            irr[o] = np.zeros(T)
            continue
        total, beam = solar.surface_irradiance(az, el, dni, dhi, solar.ORIENT_AZ[o], 90.0,
                                               horizon_deg=p.horizon.get(o, 0.0) or 0.0,
                                               horizon_profile=(p.horizon_profile or {}).get(o), albedo=p.albedo)
        if p.trees and p.facade_points and p.facade_points.get(o):
            tf = veg.tree_beam_factor(p.facade_points[o], p.trees, az, el, months, p.north_deg)
            total, beam = total - beam * (1.0 - tf), beam * tf
        diffuse_part = total - beam
        irr[o] = total
        r_add = None
        zones = []
        if p.devices:
            eff = sh.orientation_effects(p.devices, o, solar.ORIENT_AZ[o], az, el, hidx, total, t_e, p.g_window, occupied)
            beam_w, diffuse_w = beam * eff['beam'], diffuse_part * eff['diffuse']
            r_add = eff['r_add']
            zones = sh.opaque_zone(eff, w['opaque'], w['window'], p.wall_height)
        else:
            beam_w, diffuse_w = beam, diffuse_part
        gains_o = w['window'] * p.g_window * p.frame_factor * (beam_w + diffuse_w)
        win += gains_o
        if w['window'] > 0:
            h_w += w['window'] / (1.0 / max(w['uw'], 1e-9) + (r_add if r_add is not None else 0.0))
        if by_orientation is not None:
            by_orientation[o] = round(float(gains_o.sum() / 1000.0), 1)
        a_o, u_o = p.alpha_wall(o), p.u_wall_eff(o)
        shaded_area = sum(z[0] for z in zones)
        received = (w['opaque'] - shaded_area) * total
        for area, zb, zd in zones:
            received = received + area * (beam * zb + diffuse_part * zd)
        opq += a_o * R_SE * u_o * received
        g = p.green_walls
        if evapo and g and o in (g.get('orientations') or []) and w['opaque'] > 0:
            mean_irr = received / max(w['opaque'], 1e-9)
            opq -= R_SE * u_o * w['opaque'] * veg.latent_flux(veg.ALPHA_FOLIAGE * mean_irr, t_e, g['lai'], g['part'])
    roof_total, _ = solar.surface_irradiance(az, el, dni, dhi, 180.0, 0.0, albedo=p.albedo)
    u_r = p.u_roof_eff
    opq += p.alpha_roof * R_SE * u_r * p.roof_area * roof_total
    if evapo and p.green_roof and p.roof_area > 0:
        g = p.green_roof
        opq -= R_SE * u_r * p.roof_area * veg.latent_flux(veg.ALPHA_FOLIAGE * roof_total, t_e, g['lai'], g['part'])
    irr['H'] = roof_total
    return {'win': win, 'opq': opq, 'irr': irr, 'h_w': h_w}


def solar_terms(p, az, el, dni, dhi, t_e=None, hidx=None, by_orientation=None, evapo=True):
    """(gains solaires par les baies, gains par les parois opaques, irradiance par orientation) — W."""
    e = envelope_terms(p, az, el, dni, dhi, t_e, hidx, by_orientation, evapo)
    return e['win'], e['opq'], e['irr']


def run(params, weather, *, t_init=None, keep_series=True):
    """Simule S jeux de paramètres (liste de `Params`) sur la série météo. Rend une liste de résultats.

    Lot 36 : conductance des vitrages horaire (ΔR des fermetures), surventilation nocturne (débit ajouté quand
    `engines.surcharges.night_static_mask`, air intérieur de l'heure précédente au-dessus de 24 °C et plus chaud que dehors
    de 1 °C),
    indicateurs de confort RE2020 et séries mensuelles (`engines.comfort`)."""
    from . import comfort
    from .inputs import recovery_active
    from .surcharges import NIGHT_INDOOR_TRIGGER_C, NIGHT_MARGIN_C, night_static_mask, opening_allowed
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
    h_op = vec(lambda p: p.h_tr_op_air)
    h_fl = vec(lambda p: p.floor_area) * u_fl
    h_ve = np.maximum(vec(lambda p: p.h_ve), 1e-6)
    h_is = H_IS * a_t
    h_ms = H_MS * a_m
    h_op_tot = h_op + h_fl
    h_em = 1.0 / np.maximum(1.0 / np.maximum(h_op_tot, 1e-9) - 1.0 / h_ms, 1e-9)
    floor_share = np.where(h_op_tot > 0, h_fl / np.maximum(h_op_tot, 1e-9), 0.0)

    gain_f, _ = schedules(hidx, ps[0].residential, 0.0)
    phi_int = vec(lambda p: p.gains_w_m2)[:, None] * a_f[:, None] * gain_f[None, :]
    sol_w = np.zeros((S, T))
    sol_o = np.zeros((S, T))
    h_w_all = np.zeros((S, T))
    irr0 = None
    by_o = [{} for _ in ps]
    for i, p in enumerate(ps):
        e = envelope_terms(p, az, el, dni, dhi, t_e, hidx, by_o[i])
        sol_w[i], sol_o[i], h_w_all[i] = e['win'], e['opq'], e['h_w']
        if irr0 is None:
            irr0 = e['irr']
    phi_sol = sol_w + sol_o
    t_cool = np.array([p.t_cool if p.t_cool is not None else np.inf for p in ps])
    # Surventilation nocturne (lot 36) : conductance ajoutée, part « statique » (nuit d'été, air sous le seuil).
    h_night = vec(lambda p: 0.34 * p.night_vent * p.volume)
    night_ok = np.zeros((S, T), dtype=bool)
    for i, p in enumerate(ps):
        if p.night_vent > 0:
            night_ok[i] = night_static_mask(hidx, t_e, p.night_vent_threshold)
    any_night = bool(night_ok.any())
    # Lot 40 : by-pass d'été de la double flux (`inputs.recovery_active`) — conductance de récupération retirée
    # (h_ve sans échangeur = h_ve + h_rec) quand l'échangeur est by-passé.
    h_rec = vec(lambda p: 0.34 * p.n_recup * p.eta_recup * p.volume if p.bypass else 0.0)
    any_bypass = bool((h_rec > 0).any())
    t_heat_now = t_heat
    any_night = any_night or any_bypass
    bypass_hours = np.zeros(S, dtype=int)
    # Lot 40 (I9) : ouverture des fenêtres (réglage du parc) — débit ajouté, échangeur by-passé baies ouvertes.
    h_open = vec(lambda p: 0.34 * p.window_opening['vol_h'] * p.volume if p.window_opening else 0.0)
    open_ok = np.zeros((S, T), dtype=bool)
    open_seuil = np.full(S, np.inf)
    open_ecart = np.zeros(S)
    for i, p in enumerate(ps):
        if p.window_opening:
            open_ok[i] = opening_allowed(hidx, p.residential, p.window_opening['occupation'])
            open_seuil[i], open_ecart[i] = p.window_opening['seuil_c'], p.window_opening['ecart_c']
    any_open = bool((h_open > 0).any())
    any_night = any_night or any_open
    open_hours = np.zeros(S, dtype=int)
    night_hours = np.zeros(S, dtype=int)

    phi_ia = 0.5 * phi_int
    share_m = a_m / a_t

    theta_m = np.full(S, t_init if t_init is not None else float(ps[0].t_heat))
    cap = c_m / 3600.0
    t_air = np.empty((S, T))
    t_s = np.empty((S, T))
    phi_hc = np.empty((S, T))
    h_ve_used = np.empty((S, T)) if any_night else None

    def step(tm_prev, te, tg, phi_ia_t, phi_st_t, phi_m_t, phi, hw, h1, h2, h3, hve):
        # Plancher : sa part de H_tr_em échange avec le sol (θ_g) et non avec l'air extérieur.
        te_m = te + floor_share * (tg - te)
        phi_mtot = phi_m_t + h_em * te_m + h3 * (phi_st_t + hw * te + h1 * ((phi_ia_t + phi) / hve + te)) / h2
        tm = (tm_prev * (cap - 0.5 * (h3 + h_em)) + phi_mtot) / (cap + 0.5 * (h3 + h_em))
        tm_avg = 0.5 * (tm + tm_prev)
        ts = (h_ms * tm_avg + phi_st_t + hw * te + h1 * (te + (phi_ia_t + phi) / hve)) / (h_ms + hw + h1)
        ta = (h_is * ts + hve * te + phi_ia_t + phi) / (h_is + hve)
        return tm, ts, ta

    zero = np.zeros(S)
    test = PHI_TEST_W_M2 * a_f
    t_prev = theta_m.copy()
    for k in range(T):
        te = t_e[k]
        hw = h_w_all[:, k]
        hve = h_ve
        if any_night:
            on = night_ok[:, k] & (t_prev > te + NIGHT_MARGIN_C) & (t_prev > NIGHT_INDOOR_TRIGGER_C)
            night_hours += on
            hve = h_ve + np.where(on, h_night, 0.0)
            opened = np.zeros(S, dtype=bool)
            if any_open:
                opened = open_ok[:, k] & (h_open > 0) & (t_prev > open_seuil) & (t_prev > te + open_ecart)
                open_hours += opened
                hve = hve + np.where(opened, h_open, 0.0)
            if any_bypass:
                off = (~recovery_active(te, t_prev, t_heat_now[:, k]) | opened) & (h_rec > 0)
                bypass_hours += off
                hve = hve + np.where(off, h_rec, 0.0)
            h_ve_used[:, k] = hve
        h1 = 1.0 / (1.0 / hve + 1.0 / h_is)
        h2 = h1 + hw
        h3 = 1.0 / (1.0 / h2 + 1.0 / h_ms)
        share_s = 1.0 - share_m - hw / (H_MS * a_t)
        base_k = 0.5 * phi_int[:, k] + phi_sol[:, k]
        coef = (hw, h1, h2, h3, hve)
        args = (t_g_all[:, k], phi_ia[:, k], share_s * base_k, share_m * base_k)
        tm0, ts0, ta0 = step(theta_m, te, *args, zero, *coef)
        need_h = ta0 < t_heat[:, k]
        need_c = ta0 > t_cool
        phi = zero.copy()
        if need_h.any() or need_c.any():
            _, _, ta10 = step(theta_m, te, *args, test, *coef)
            slope = (ta10 - ta0) / test
            slope = np.where(np.abs(slope) < 1e-12, 1e-12, slope)
            phi = np.where(need_h, (t_heat[:, k] - ta0) / slope, phi)
            phi = np.where(need_c, (t_cool - ta0) / slope, phi)
            tm0, ts0, ta0 = step(theta_m, te, *args, phi, *coef)
        theta_m = tm0
        t_prev = ta0
        t_air[:, k], t_s[:, k], phi_hc[:, k] = ta0, ts0, phi

    heat = np.clip(phi_hc, 0, None)
    cool = np.clip(-phi_hc, 0, None)
    t_op = 0.3 * t_air + 0.7 * t_s
    loss_tr = (h_w_all + h_op[:, None]) * (t_air - t_e[None, :]) + h_fl[:, None] * (t_air - t_g_all)
    loss_ve = (h_ve_used if any_night else h_ve[:, None]) * (t_air - t_e[None, :])
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
            ind, mens = comfort.indicators(t_op[i], t_e, hidx, ps[i].residential, phi_hc[i])
            r['confort'] = ind
            r['mensuel'] = mens
            if any_night and ps[i].night_vent > 0:
                r['surventilation_nocturne_h'] = int(night_hours[i])
            if h_rec[i] > 0:
                r['bypass_double_flux_h'] = int(bypass_hours[i])
            if ps[i].window_opening:
                r['ouverture_fenetres_h'] = int(open_hours[i])
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
