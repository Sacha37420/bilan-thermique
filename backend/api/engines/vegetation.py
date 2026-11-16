"""Végétalisation : toitures et murs végétalisés, arbres plantés (lot 36, chantier bt-potentiels — I2).

**Toiture et murs végétalisés** (`green_surface`) — modèle simple, hypothèses écrites dans les entrées figées :
- **U** : résistance ajoutée en série, au prorata de la part végétalisée (surfaces en parallèle) —
  toiture : substrat d'épaisseur e, λ = 0,40 W/m·K (substrat humide, ordre de grandeur des règles professionnelles
  françaises des toitures végétalisées, CSTB/Adivet 2018 : 0,3 à 0,6) ; mur : lame d'air faiblement ventilée
  derrière le feuillage, R = 0,09 m²·K/W (EN ISO 6946:2017 § 6.9.3, moitié d'une lame d'air de 2 cm non ventilée) ;
- **albédo** : feuillage d'absorptance solaire 0,75 (albédo 0,2 à 0,3 d'un couvert végétal — Gaffin et al., 2005,
  « Energy balance modeling applied to a comparison of white and green roof cooling efficiency ») ;
- **inertie** : la masse du substrat est extérieure à l'isolant — sans effet en N0/N1 (la capacité du modèle 5R1C est
  celle des parois intérieures) ; en N2, couche de substrat (ρc = 1,5 MJ/m³·K) dans la paroi ;
- **évapotranspiration** (puits de chaleur en surface) : Priestley & Taylor (1972, Monthly Weather Review 100(2)) —
  LE = α_PT · Δ/(Δ+γ) · (R_n − G) · β · f_c, avec α_PT = 1,26 ; Δ, pente de la pression de vapeur saturante (FAO-56,
  Allen et al. 1998, éq. 13) ; γ = 0,0665 kPa/K (FAO-56 éq. 8, 101,3 kPa) ; R_n pris égal au solaire absorbé par le
  feuillage (bilan infrarouge négligé), G = 0,1·R_n ; β = 0,5, eau disponible d'une toiture extensive non arrosée
  (hypothèse affichée) ; f_c = 1 − exp(−0,5·LAI), part du rayonnement interceptée par le feuillage (Beer-Lambert,
  Monsi & Saeki 1953). Ordre de grandeur : 600 W/m² absorbés à 25 °C, LAI 3 → LE ≈ 200 W/m², soit 1,5 à 2,5 mm/j
  l'été, dans la fourchette mesurée sur des toitures extensives (1 à 4 mm/j).

**Arbres plantés** (`tree_beam_factor`, `tree_meshes`) : houppier cylindrique de rayon r entre le haut du tronc et le
sommet, translucide (transmission du direct en saison de feuilles et hors saison ; persistant : la même). N0/N1 : le
direct de chaque façade est multiplié, heure par heure, par la transmission moyenne vue des points de la façade
(mêmes points que le profil d'horizon, `api.horizon`) ; N2 : obstacles translucides ajoutés à l'environnement du
lancer de rayons. Diffus du ciel non réduit (même limite que le profil d'horizon).
"""

import math

import numpy as np

SUBSTRATE_LAMBDA = 0.40        # W/m·K
SUBSTRATE_RHO_C = 1.5e6        # J/m³·K (ρ ≈ 1 200 kg/m³, c ≈ 1 250 J/kg·K, substrat humide)
WALL_LAYER_R = 0.09            # m²·K/W
ALPHA_FOLIAGE = 0.75
ALPHA_PT = 1.26
GAMMA_PSY = 0.0665             # kPa/K
G_SHARE = 0.10
WATER_BETA = 0.5
K_EXTINCTION = 0.5

GREEN_ROOF_DEFAULT = {'substrat_m': 0.10, 'lai': 2.0, 'part': 1.0}
GREEN_WALLS_DEFAULT = {'orientations': ['S', 'O'], 'lai': 2.0, 'part': 0.5}

HYPOTHESE_EVAPO = ('Évapotranspiration de Priestley-Taylor (α = 1,26), eau disponible β = 0,5 (toiture extensive non '
                   'arrosée), rayonnement net pris égal au solaire absorbé par le feuillage.')


def cover_fraction(lai):
    return 1.0 - math.exp(-K_EXTINCTION * max(float(lai), 0.0))


def delta_over_delta_gamma(t_c):
    """Δ/(Δ+γ) à la température de l'air t (°C) — FAO-56 éq. 13."""
    t = np.asarray(t_c, dtype=float)
    es = 0.6108 * np.exp(17.27 * t / (t + 237.3))
    delta = 4098.0 * es / (t + 237.3) ** 2
    return delta / (delta + GAMMA_PSY)


def latent_coefficient(lai, part=1.0):
    """Facteur constant c tel que LE (W/m² de paroi) = c · Δ/(Δ+γ)(T) · (solaire absorbé par le feuillage)."""
    return ALPHA_PT * (1.0 - G_SHARE) * WATER_BETA * cover_fraction(lai) * float(part)


def latent_flux(absorbed_w_m2, t_c, lai, part=1.0):
    """Puits d'évapotranspiration (W/m², ≥ 0) heure par heure."""
    return latent_coefficient(lai, part) * delta_over_delta_gamma(t_c) * np.clip(np.asarray(absorbed_w_m2), 0.0, None)


def green_u(u, r_add, part):
    """U d'une paroi dont la part `part` reçoit une résistance `r_add` en série (surfaces en parallèle)."""
    u = float(u)
    if u <= 0 or r_add <= 0 or part <= 0:
        return u
    return part / (1.0 / u + r_add) + (1.0 - part) * u


def roof_resistance(substrat_m):
    return max(float(substrat_m), 0.0) / SUBSTRATE_LAMBDA


def mixed_alpha(alpha, part):
    return float(part) * ALPHA_FOLIAGE + (1.0 - float(part)) * float(alpha)


# ── Arbres plantés ────────────────────────────────────────────────────────────

def _season_k(tree, summer_mask):
    k_e, k_h = float(tree.get('transmission_ete', 0.2)), float(tree.get('transmission_hiver', 0.7))
    if tree.get('persistant'):
        k_h = k_e
    return np.where(summer_mask, k_e, k_h)


def tree_beam_factor(points, trees, az, el, months, north_deg=0.0, leaf_months=(5, 6, 7, 8, 9)):
    """Transmission moyenne du direct vue des points d'une façade, heure par heure (1 : aucun arbre devant).

    points : [(x, y, z)] dans le repère local ; trees : surcharges `arbres` ; az, el : soleil (rad, azimut
    géographique) ; north_deg : azimut géographique = azimut local + north_deg."""
    az = np.asarray(az, dtype=float)
    el = np.asarray(el, dtype=float)
    out = np.ones(len(az))
    if not points or not trees:
        return out
    summer = np.isin(np.asarray(months), leaf_months)
    acc = np.zeros(len(az))
    for (px, py, pz) in points:
        f = np.ones(len(az))
        for t in trees:
            dx, dy = float(t['x_m']) - px, float(t['y_m']) - py
            d = math.hypot(dx, dy)
            r = float(t.get('rayon_houppier_m', 3.0))
            if d <= r + 0.1:
                continue                                   # point dans le houppier : ignoré (arbre contre la façade)
            a_geo = (math.degrees(math.atan2(dx, dy)) + north_deg) % 360.0
            half = math.degrees(math.asin(min(r / d, 1.0)))
            lo = math.atan2(float(t.get('tronc_m', 2.0)) - pz, d)
            hi = math.atan2(float(t.get('hauteur_m', 8.0)) - pz, max(d - r, 0.1))
            daz = np.abs((np.degrees(az) - a_geo + 180.0) % 360.0 - 180.0)
            inside = (daz <= half) & (el >= lo) & (el <= hi) & (el > 0)
            f = np.where(inside, f * _season_k(t, summer), f)
        acc += f
    return acc / len(points)


def tree_meshes(trees, season='ete', n_sides=8, start_obj=900_000):
    """Houppiers translucides (prismes à n côtés) pour le lancer de rayons du N2 — transmission de la saison."""
    verts, tris = [], []
    for i, t in enumerate(trees or []):
        x, y = float(t['x_m']), float(t['y_m'])
        r = float(t.get('rayon_houppier_m', 3.0))
        z0, z1 = float(t.get('tronc_m', 2.0)), float(t.get('hauteur_m', 8.0))
        if z1 <= z0 or r <= 0:
            continue
        k = float(t.get('transmission_ete', 0.2)) if (season == 'ete' or t.get('persistant')) \
            else float(t.get('transmission_hiver', 0.7))
        k = min(max(k, 1e-3), 0.999)
        base = len(verts)
        ring = [(x + r * math.cos(2 * math.pi * j / n_sides), y + r * math.sin(2 * math.pi * j / n_sides))
                for j in range(n_sides)]
        verts += [[px, py, z0] for px, py in ring] + [[px, py, z1] for px, py in ring]
        verts += [[x, y, z0], [x, y, z1]]
        cb, ct = base + 2 * n_sides, base + 2 * n_sides + 1
        obj = start_obj + i
        for j in range(n_sides):
            a, b = base + j, base + (j + 1) % n_sides
            c, d = a + n_sides, b + n_sides
            for v in ((a, b, d), (a, d, c), (cb, b, a), (ct, c, d)):
                tris.append({'v': list(v), 'k': round(k, 3), 'obj': obj})
    return {'vertices': verts, 'triangles': tris} if tris else None
