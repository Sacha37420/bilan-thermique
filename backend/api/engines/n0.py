"""N0 — bilan statique mensuel (ISO 13790 méthode mensuelle, proche de la 3CL) (Lot 10, refonte).

Besoin mensuel de chauffage : Q_H = Q_ht − η·Q_gn, avec Q_ht les déperditions (transmission +
ventilation) et Q_gn les apports (internes + solaires), η le facteur d'utilisation des apports
(a = 1 + τ/15, τ la constante de temps du bâtiment en heures). Mêmes paramètres et mêmes
apports solaires que le N1 (`n1.solar_terms`) : seul le pas de temps change. < 0,1 s.
"""

import time

import numpy as np

from . import solar
from .n1 import MONTH_HOURS, schedules, solar_terms


def utilisation_factor(gamma, tau_h):
    a = 1.0 + tau_h / 15.0
    if gamma <= 0:
        return 1.0
    if abs(gamma - 1.0) < 1e-6:
        return a / (a + 1.0)
    return (1.0 - gamma ** a) / (1.0 - gamma ** (a + 1.0))


def run(p, weather):
    t0 = time.monotonic()
    t_e, az, el, dni, dhi, hidx = solar.arrays(weather)
    T = len(t_e)
    sol_w, sol_o, _ = solar_terms(p, az, el, dni, dhi)
    gain_f, t_set = schedules(hidx, p.residential, p.t_heat)
    phi_int = p.gains_w_m2 * p.a_floor * gain_f
    h_air = p.h_tr_w + p.h_tr_op_air + p.h_ve
    h_tot = h_air + p.h_floor
    tau_h = p.c_m * p.a_floor / 3600.0 / max(h_tot, 1e-9)
    edges = [e for e in MONTH_HOURS.tolist() if e <= T]
    if edges[-1] != T:
        edges.append(T)
    monthly = []
    total = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        hours = b - a
        q_ht = (h_air * np.clip(t_set[a:b] - t_e[a:b], 0, None).sum()
                + p.h_floor * np.clip(t_set[a:b] - p.t_ground, 0, None).sum()) / 1000.0
        q_gn = (phi_int[a:b].sum() + sol_w[a:b].sum() + sol_o[a:b].sum()) / 1000.0
        gamma = q_gn / q_ht if q_ht > 1e-9 else 1e9
        eta = utilisation_factor(gamma, tau_h) if q_ht > 1e-9 else 0.0
        q_h = max(0.0, q_ht - eta * q_gn)
        monthly.append(round(q_h, 1))
        total += q_h
        _ = hours
    return {
        'level': 'N0', 'hours': T, 'heating_kwh': round(total, 1), 'cooling_kwh': 0.0,
        'monthly_heating_kwh': monthly, 'tau_h': round(tau_h, 1),
        'h_tot_w_k': round(h_tot, 1), 'duration_s': round(time.monotonic() - t0, 4),
    }
