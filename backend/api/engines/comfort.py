"""Confort d'été : degrés-heures d'inconfort RE2020 et séries mensuelles (lot 36, chantier bt-potentiels — I3).

**DH RE2020** (arrêté du 4 août 2021, annexe — méthode Th-BCE 2020, indicateur « degrés-heures d'inconfort ») :
DH = Σ max(0, T_op − T_conf) sur les heures d'occupation de l'année, T_op la température opérative, T_conf une
température de confort **adaptative** bornée entre 26 et 28 °C :
- le jour, T_conf = 0,33·θ_rm + 21,8 °C bornée à [26 ; 28] °C — limite haute de la catégorie II du confort adaptatif
  de l'EN 16798-1:2019 (θ_max = 0,33·θ_rm + 18,8 + 3), que la RE2020 reprend avec ces bornes ;
- la nuit dans les logements (22 h – 7 h, sommeil), T_conf = 26 °C ;
- θ_rm, moyenne glissante de la température extérieure : θ_rm,j = (1 − α)·θ_e,j−1 + α·θ_rm,j−1, α = 0,8
  (EN 16798-1:2019 éq. B.1), θ_e,j−1 la moyenne journalière de la veille ; amorcée sur la moyenne de la première
  semaine.
Heures comptées : toutes pour un logement ; heures d'occupation (8 h – 19 h, lundi – vendredi, même convention que N1)
pour les autres usages. Le DH « base 26 °C » de l'app (`degree_hours_over_26`, toutes heures) reste à côté (`dh_26`).
"""

import numpy as np

T_CONF_MIN, T_CONF_MAX = 26.0, 28.0
ALPHA_RM = 0.8
NIGHT = (22, 7)
METHODE = ('RE2020 (Th-BCE 2020) : T_conf = 0,33·θ_rm + 21,8 °C bornée à [26 ; 28] °C le jour, 26 °C la nuit '
           'en logement ; θ_rm moyenne glissante EN 16798-1 (α = 0,8)')
MONTH_EDGES = np.cumsum([0, 744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744])


def running_mean(t_ext, hidx=None):
    """θ_rm heure par heure (°C) — constante sur la journée, d'après les moyennes journalières précédentes."""
    t = np.asarray(t_ext, dtype=float)
    if hidx is None:
        hidx = np.arange(len(t))
    day = (np.asarray(hidx) // 24).astype(int)
    days = np.unique(day)
    means = {d: float(t[day == d].mean()) for d in days}
    first = days[:7]
    rm = float(np.mean([means[d] for d in first])) if len(first) else 15.0
    by_day = {}
    prev = None
    for d in days:
        if prev is not None:
            rm = (1.0 - ALPHA_RM) * means[prev] + ALPHA_RM * rm
        by_day[d] = rm
        prev = d
    return np.array([by_day[d] for d in day])


def t_conf(t_ext, hidx=None, residential=True):
    if hidx is None:
        hidx = np.arange(len(t_ext))
    rm = running_mean(t_ext, hidx)
    day = np.clip(0.33 * rm + 21.8, T_CONF_MIN, T_CONF_MAX)
    if residential:
        hour = np.asarray(hidx) % 24
        night = (hour >= NIGHT[0]) | (hour < NIGHT[1])
        return np.where(night, T_CONF_MIN, day)
    return day


def occupied_mask(hidx, residential):
    hidx = np.asarray(hidx)
    if residential:
        return np.ones(len(hidx), dtype=bool)
    hour, d = hidx % 24, (hidx // 24) % 7
    return (hour >= 8) & (hour < 19) & (d < 5)


def dh_re2020(t_op, t_ext, hidx=None, residential=True):
    """(DH °C·h, heures d'inconfort, série horaire des degrés au-dessus de T_conf)."""
    t_op = np.asarray(t_op, dtype=float)
    if hidx is None:
        hidx = np.arange(len(t_op))
    tc = t_conf(t_ext, hidx, residential)
    occ = occupied_mask(hidx, residential)
    over = np.where(occ, np.clip(t_op - tc, 0.0, None), 0.0)
    return float(over.sum()), int((over > 0).sum()), over


def monthly(series, n=None, how='sum'):
    """Agrégat par mois (année type commençant le 1er janvier), 12 valeurs au plus."""
    x = np.asarray(series, dtype=float)
    n = len(x) if n is None else n
    edges = [e for e in MONTH_EDGES.tolist() if e <= n]
    if edges[-1] != n:
        edges.append(n)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        seg = x[a:b]
        if not len(seg):
            out.append(None)
        elif how == 'max':
            out.append(round(float(seg.max()), 2))
        elif how == 'mean':
            out.append(round(float(seg.mean()), 2))
        else:
            out.append(round(float(seg.sum()), 1))
    return out


def indicators(t_op, t_ext, hidx, residential, phi_hc_w=None):
    """Indicateurs I3 de confort et séries mensuelles d'un calcul horaire (N1 ou N2)."""
    dh, hours, over = dh_re2020(t_op, t_ext, hidx, residential)
    t_op = np.asarray(t_op, dtype=float)
    mens = {'dh_re2020': monthly(over), 't_op_max_c': monthly(t_op, how='max'),
            't_ext_moy_c': monthly(t_ext, how='mean')}
    if phi_hc_w is not None:
        phi = np.asarray(phi_hc_w, dtype=float)
        mens['heating_kwh'] = monthly(np.clip(phi, 0, None) / 1000.0)
        mens['cooling_kwh'] = monthly(np.clip(-phi, 0, None) / 1000.0)
    ind = {'dh_re2020': round(dh, 1), 'heures_inconfort': hours, 't_conf_methode': METHODE}
    return ind, mens
