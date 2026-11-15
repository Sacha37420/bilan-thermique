"""Protections solaires paramétriques (Lot 14, refonte — § B4).

Facteurs horaires appliqués au rayonnement reçu par les baies d'une orientation :
- **fixes** (casquette, joues, loggia, balcon de l'étage supérieur, débord de toiture) : fraction
  ensoleillée exacte d'une baie rectangulaire sous un débord horizontal et entre deux joues verticales,
  d'après l'angle de profil du soleil (même géométrie que l'annexe F de l'EN ISO 52016-1 pour un
  débord et des joues) ; le diffus est réduit du facteur de vue du ciel masqué ;
- **mobiles** (volet roulant, battant, persienne, store extérieur ou intérieur, brise-soleil orientable) :
  position 0 (ouvert) à 1 (fermé), pilotée par un planning horaire ou automatiquement (éclairement sur la
  façade, température extérieure, absence d'occupants) ; fermé, il laisse passer `fs_dir` / `fs_dif` ;
- **vitrage** de contrôle solaire ou film : facteur g ramené à une nouvelle valeur ;
- **végétation** caduque (D8) : transmission du direct différente en été (feuilles) et en hiver.

Le N1 ne modélise que l'effet solaire des protections mobiles (pas leur résistance thermique ΔR la
nuit, faible devant l'effet solaire) ; le N2 garde ses volets à ΔR (Lot J) et reçoit les protections
fixes comme obstacles géométriques (`shading_service.geometric_occluders`).
"""

import numpy as np

FIXED = ('casquette', 'joues', 'loggia', 'balcon', 'debord_toit')
MOBILE = ('volet_roulant', 'volet_battant', 'persienne', 'store_ext', 'store_int', 'bso')
GLAZING = ('film', 'vitrage_controle')
VEGETATION = ('arbre_caduc', 'pergola')

MOBILE_DEFAULTS = {  # transmission fermé (direct, diffus) — repères usuels, comme SHADING_PROFILES du N2
    'volet_roulant': (0.0, 0.0), 'volet_battant': (0.0, 0.05), 'persienne': (0.05, 0.15),
    'store_ext': (0.15, 0.40), 'store_int': (0.55, 0.65), 'bso': (0.05, 0.25),
}
VEGETATION_DEFAULTS = {'arbre_caduc': (0.30, 0.75), 'pergola': (0.35, 0.85)}  # (été, hiver) sur le direct
SUMMER_MONTHS = (5, 6, 7, 8, 9)

CATALOGUE = [
    {'kind': 'casquette', 'family': 'fixe', 'label': 'Casquette (auvent)', 'params': ['depth_m', 'gap_m', 'window_height_m', 'extension_m', 'window_width_m']},
    {'kind': 'balcon', 'family': 'fixe', 'label': 'Balcon de l’étage supérieur', 'params': ['depth_m', 'gap_m', 'window_height_m']},
    {'kind': 'debord_toit', 'family': 'fixe', 'label': 'Débord de toiture', 'params': ['depth_m', 'gap_m', 'window_height_m']},
    {'kind': 'joues', 'family': 'fixe', 'label': 'Joues (avancées latérales)', 'params': ['fin_depth_m', 'window_width_m']},
    {'kind': 'loggia', 'family': 'fixe', 'label': 'Loggia', 'params': ['depth_m', 'gap_m', 'window_height_m', 'fin_depth_m', 'window_width_m']},
    {'kind': 'volet_roulant', 'family': 'mobile', 'label': 'Volet roulant'},
    {'kind': 'volet_battant', 'family': 'mobile', 'label': 'Volet battant'},
    {'kind': 'persienne', 'family': 'mobile', 'label': 'Persienne'},
    {'kind': 'store_ext', 'family': 'mobile', 'label': 'Store extérieur'},
    {'kind': 'store_int', 'family': 'mobile', 'label': 'Store intérieur'},
    {'kind': 'bso', 'family': 'mobile', 'label': 'Brise-soleil orientable'},
    {'kind': 'film', 'family': 'vitrage', 'label': 'Film de contrôle solaire', 'params': ['g']},
    {'kind': 'vitrage_controle', 'family': 'vitrage', 'label': 'Vitrage de contrôle solaire', 'params': ['g']},
    {'kind': 'arbre_caduc', 'family': 'vegetation', 'label': 'Arbre à feuilles caduques'},
    {'kind': 'pergola', 'family': 'vegetation', 'label': 'Pergola végétalisée'},
]
KINDS = {c['kind']: c for c in CATALOGUE}


def _relative_angles(az, el, facade_az_deg):
    gamma = np.angle(np.exp(1j * (az - np.radians(facade_az_deg))))  # écart d'azimut, [-π, π]
    front = np.cos(gamma) > 1e-6
    tan_profile = np.where(front, np.tan(np.clip(el, 0, None)) / np.maximum(np.cos(gamma), 1e-6), np.inf)
    return gamma, front, tan_profile


def overhang_fraction(az, el, facade_az_deg, depth, gap, height, extension=None, width=None):
    """Fraction ensoleillée d'une baie de hauteur `height` dont le haut est à `gap` sous un débord de
    profondeur `depth` (débord infini si `extension`/`width` absents ; sinon débordement latéral
    `extension` de chaque côté d'une baie de largeur `width`)."""
    gamma, front, tp = _relative_angles(az, el, facade_az_deg)
    shadow = depth * tp - gap  # hauteur ombrée depuis le haut de la baie
    shaded_h = np.clip(shadow / max(height, 1e-6), 0.0, 1.0)
    if extension is not None and width:
        # Ombre décalée latéralement : seule la part sous le débord est ombrée.
        shift = np.abs(depth * np.tan(np.clip(gamma, -1.5, 1.5)))
        covered = np.clip((width + 2 * extension - shift) / max(width, 1e-6), 0.0, 1.0)
        shaded_h = shaded_h * covered
    return np.where(front & (el > 0), 1.0 - shaded_h, 0.0)


def fins_fraction(az, el, facade_az_deg, fin_depth, width):
    """Fraction ensoleillée d'une baie de largeur `width` entre deux joues de profondeur `fin_depth`."""
    gamma, front, _tp = _relative_angles(az, el, facade_az_deg)
    shaded_w = np.clip(fin_depth * np.abs(np.tan(np.clip(gamma, -1.55, 1.55))) / max(width, 1e-6), 0.0, 1.0)
    return np.where(front & (el > 0), 1.0 - shaded_w, 0.0)


def diffuse_factor(depth=0.0, gap=0.0, height=1.5, fin_depth=0.0, width=1.2):
    """Part du ciel vue par la baie qui reste visible (approximation : angle masqué au milieu de la baie)."""
    f = 1.0
    if depth:
        theta = np.arctan(depth / (gap + height / 2.0))
        f *= 1.0 - 0.5 * np.sin(theta)
    if fin_depth:
        phi = np.arctan(2 * fin_depth / max(width, 1e-6))
        f *= 1.0 - 0.25 * np.sin(phi)
    return float(f)


def mobile_position(dev, hidx, irradiance, t_ext, occupied=None):
    """Position horaire (0 ouvert … 1 fermé) d'une protection mobile selon son pilotage."""
    ctl = dev.get('control') or {}
    mode = ctl.get('mode', 'planning')
    pos_closed = float(ctl.get('position', 1.0))
    if mode == 'auto':
        seuil = float(ctl.get('irradiance_w_m2', 200.0))
        t_min = float(ctl.get('t_ext_min_c', 20.0))
        closed = (irradiance > seuil) & (t_ext > t_min)
        if ctl.get('close_when_absent') and occupied is not None:
            closed = closed | (~occupied & (t_ext > t_min))
        return closed * pos_closed
    hours = set(int(h) for h in (ctl.get('hours') or []))
    if not hours:
        return np.zeros(len(hidx))
    months = ctl.get('months')
    pos = np.array([pos_closed if (int(h) % 24) in hours else 0.0 for h in hidx])
    if months:
        m = month_of(hidx)
        pos = np.where(np.isin(m, months), pos, 0.0)
    return pos


def month_of(hidx):
    """Mois (1-12) de chaque heure, la série commençant le 1er janvier (convention des années types)."""
    days = (np.asarray(hidx) // 24) % 365
    edges = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    return np.searchsorted(edges, days, side='right')


def factors(devices, orientation, facade_az, az, el, hidx, total_irradiance, t_ext, g_window, occupied=None):
    """(facteur sur le direct, facteur sur le diffus) horaires pour les baies d'une orientation."""
    T = len(az)
    fb = np.ones(T)
    fd = np.ones(T)
    for dev in devices:
        if dev.get('orientation') not in (orientation, 'all'):
            continue
        kind, p = dev['kind'], dev.get('params') or {}
        if kind in FIXED and dev.get('mode', 'parametrique') == 'parametrique':
            depth, gap = float(p.get('depth_m', 0.0)), float(p.get('gap_m', 0.0))
            h, w = float(p.get('window_height_m', 1.5)), float(p.get('window_width_m', 1.2))
            fin = float(p.get('fin_depth_m', 0.0))
            if kind in ('casquette', 'balcon', 'debord_toit', 'loggia') and depth > 0:
                ext = p.get('extension_m')
                fb *= overhang_fraction(az, el, facade_az, depth, gap, h,
                                        float(ext) if ext is not None else None, w if ext is not None else None)
            if kind in ('joues', 'loggia') and fin > 0:
                fb *= fins_fraction(az, el, facade_az, fin, w)
            fd *= diffuse_factor(depth if kind != 'joues' else 0.0, gap, h, fin, w)
        elif kind in MOBILE:
            fs_dir, fs_dif = MOBILE_DEFAULTS[kind]
            fs_dir, fs_dif = float(p.get('fs_dir', fs_dir)), float(p.get('fs_dif', fs_dif))
            pos = mobile_position(dev, hidx, total_irradiance, t_ext, occupied)
            fb *= 1.0 - pos * (1.0 - fs_dir)
            fd *= 1.0 - pos * (1.0 - fs_dif)
        elif kind in GLAZING:
            g_new = float(p.get('g', g_window))
            ratio = g_new / g_window if g_window > 0 else 1.0
            fb *= ratio
            fd *= ratio
        elif kind in VEGETATION:
            summer, winter = VEGETATION_DEFAULTS[kind]
            summer, winter = float(p.get('t_summer', summer)), float(p.get('t_winter', winter))
            fb *= np.where(np.isin(month_of(hidx), SUMMER_MONTHS), summer, winter)
            fd *= np.where(np.isin(month_of(hidx), SUMMER_MONTHS), 0.5 + summer / 2, 0.5 + winter / 2)
    return fb, fd
