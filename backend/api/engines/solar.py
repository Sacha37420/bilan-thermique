"""Irradiance sur les façades et la toiture, depuis la série météo de l'app (Lot 10, refonte).

Série météo : `e_dir` = rayonnement direct NORMAL (DNI), `e_dif` = diffus horizontal (DHI),
`sun_azimuth` (0 = nord, sens horaire) et `sun_elevation` en degrés. Modèle de ciel isotrope
(comme le N2) ; réflexion du sol par un albédo par défaut de 0,20 (`refonte.md` § 6.3 : même
valeur que `observed_env.DEFAULT_GROUND_ALBEDO`, pour que N1 reste comparable).
"""

import numpy as np

ORIENT_AZ = {'N': 0.0, 'E': 90.0, 'S': 180.0, 'O': 270.0}
DEFAULT_ALBEDO = 0.20


def arrays(weather):
    t = np.array([p['t_ext'] for p in weather], dtype=float)
    az = np.radians(np.array([p['sun_azimuth'] for p in weather], dtype=float))
    el = np.radians(np.array([p['sun_elevation'] for p in weather], dtype=float))
    dni = np.array([p['e_dir'] for p in weather], dtype=float)
    dhi = np.array([p['e_dif'] for p in weather], dtype=float)
    hidx = np.array([p.get('hour_index', i) if p.get('hour_index') is not None else i
                     for i, p in enumerate(weather)], dtype=int)
    return t, az, el, dni, dhi, hidx


def surface_irradiance(az, el, dni, dhi, surf_az_deg, tilt_deg, horizon_deg=0.0, albedo=DEFAULT_ALBEDO,
                       beam_factor=None, horizon_profile=None):
    """Irradiance (W/m²) sur une surface d'azimut et d'inclinaison donnés, heure par heure.

    horizon_profile : profil d'horizon de la façade (Lot 30, `api.horizon`) — le direct est multiplié
    par la part des points de la façade qui voient le soleil à son azimut. À défaut, horizon_deg
    (résumé d'avant le Lot 30) : le direct est coupé quand le soleil est plus bas que cet angle. beam_factor : facteur supplémentaire sur le direct
    (protections solaires paramétriques, Lot 14), tableau horaire ou scalaire."""
    sa, st = np.radians(surf_az_deg), np.radians(tilt_deg)
    up = el > 0
    cos_inc = np.sin(el) * np.cos(st) + np.cos(el) * np.sin(st) * np.cos(az - sa)
    beam = dni * np.clip(cos_inc, 0.0, None) * up
    if horizon_profile:
        from ..horizon import beam_visible
        beam = beam * beam_visible(horizon_profile, az, el)
    elif horizon_deg:
        beam = np.where(el < np.radians(horizon_deg), 0.0, beam)
    if beam_factor is not None:
        beam = beam * beam_factor
    ghi = dni * np.clip(np.sin(el), 0.0, None) + dhi
    diffuse = dhi * (1 + np.cos(st)) / 2
    reflected = albedo * ghi * (1 - np.cos(st)) / 2
    return beam + diffuse + reflected, beam
