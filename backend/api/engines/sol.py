"""Sol simulé en 1D et couplage du plancher bas (Lot 32).

Jusqu'au Lot 32, tout plancher bas échangeait avec un sol à **12 °C constants**, qu'il soit sur terre-plein,
sur vide sanitaire ou sur sous-sol non chauffé (propriétaire, 2026-11-16 : un couplage sous-sol / sol fondé sur
une simulation de la température du sol en profondeur et dans le temps, d'après la météo, est indispensable).

**Sol.** Colonne 1D de 15 m (40 mailles, de 2,5 cm en surface à ~1,2 m au fond), conduction transitoire
implicite au pas horaire : ρc ∂T/∂t = ∂/∂z (λ ∂T/∂z). Surface : échange avec l'air extérieur
(h = 15 W/m²·K, convection et rayonnement linéarisé ; ni soleil, ni évaporation, ni neige — leurs effets se
compensent en partie, c'est l'hypothèse usuelle de Kusuda). Fond : flux nul. Sol limoneux humide par défaut
(λ = 1,5 W/m·K, ρc = 2,0 MJ/m³·K : diffusivité 7,5·10⁻⁷ m²/s, profondeur d'amortissement annuel 2,7 m).
La série météo est rejouée trois fois (mise en régime) ; la dernière passe est retenue.

**Couplage**, selon `plancher.type`, rendu comme une température « vue » par le plancher heure par heure, avec
le U propre du plancher vers elle (flux U_propre × A × (T_intérieur − T_vue)). `plancher.u` reste un U
**équivalent vers l'extérieur**, comme le « U final » du DPE (3CL) : le U propre en est déduit pour un vide
sanitaire ou un sous-sol (`floor_u_node`), de sorte qu'en moyenne annuelle le modèle redonne ce U :
- **terre-plein** : sol à la profondeur z = B′/2, bornée à 0,5–6 m (B′ = A / (½ P exposé), dimension
  caractéristique de l'EN ISO 13370 : sous un grand plancher, le sol utile est plus profond et plus stable) ;
- **vide sanitaire** : nœud d'air entre le plancher (vers l'intérieur), le sol à 30 cm et l'air extérieur
  (vide de 60 cm ventilé à 2 vol/h) ;
- **sous-sol non chauffé** : nœud couplé au plancher (vers l'intérieur), aux murs enterrés (sol moyen sur la
  profondeur, U 1,2), au radier (sol 1 m sous le fond, U 0,6), aux 40 cm de murs hors sol (U 2,0) et à l'air
  extérieur par sa ventilation (0,5 vol/h) ; profondeur : `sous_sol.profondeur_m` (2,5 m par défaut) ;
- **local chauffé dessous** : pas d'échange (comme avant).
Les nœuds de vide sanitaire et de sous-sol prennent la consigne de chauffage pour température intérieure
(couplage non itéré, écart de second ordre) et ont une inertie (constante de temps 6 h et 48 h).
"""

import hashlib
from functools import lru_cache

import numpy as np

SOIL_LAMBDA = 1.5          # W/m·K
SOIL_RHO_C = 2.0e6         # J/m³·K
DEPTH_M = 15.0
N_NODES = 40
FIRST_DZ_M = 0.025
H_SURFACE = 15.0           # W/m²·K
SPINUP = 3
DT_S = 3600.0

Z_MIN_M, Z_MAX_M = 0.5, 6.0
CRAWL = {'hauteur_m': 0.6, 'renouvellement_vol_h': 2.0, 'u_sol': 1.5, 'z_sol_m': 0.3, 'tau_h': 6.0}
BASEMENT = {'profondeur_m': 2.5, 'hors_sol_m': 0.4, 'u_murs_enterres': 1.2, 'u_radier': 0.6, 'u_murs_hors_sol': 2.0,
            'renouvellement_vol_h': 0.5, 'tau_h': 48.0}


def _grid():
    """Profondeurs des nœuds (m) : pas géométrique de FIRST_DZ_M en surface, somme DEPTH_M."""
    lo, hi = 1.0001, 2.0
    for _ in range(80):
        r = (lo + hi) / 2
        if FIRST_DZ_M * (r ** (N_NODES - 1) - 1) / (r - 1) > DEPTH_M:
            hi = r
        else:
            lo = r
    dz = FIRST_DZ_M * r ** np.arange(N_NODES - 1)
    dz *= DEPTH_M / dz.sum()
    return np.concatenate([[0.0], np.cumsum(dz)])


Z = _grid()


def _matrix(lam, rho_c):
    from scipy.linalg import lu_factor
    z = Z
    n = len(z)
    dz = np.diff(z)
    vol = np.empty(n)                         # épaisseur « de contrôle » de chaque nœud
    vol[0] = dz[0] / 2
    vol[-1] = dz[-1] / 2
    vol[1:-1] = (dz[:-1] + dz[1:]) / 2
    g = lam / dz                              # conductances entre nœuds (W/m²·K)
    A = np.zeros((n, n))
    for i in range(n - 1):
        A[i, i] += g[i]
        A[i + 1, i + 1] += g[i]
        A[i, i + 1] -= g[i]
        A[i + 1, i] -= g[i]
    A[0, 0] += H_SURFACE
    cap = rho_c * vol / DT_S
    M = A + np.diag(cap)
    return lu_factor(M), cap


@lru_cache(maxsize=16)
def _profile_cached(key, t_bytes, lam, rho_c):
    from scipy.linalg import lu_solve
    t_ext = np.frombuffer(t_bytes, dtype=float)
    lu, cap = _matrix(lam, rho_c)
    T = np.full(len(Z), float(t_ext.mean()))
    out = np.empty((len(t_ext), len(Z)))
    for rep in range(SPINUP):
        for k, te in enumerate(t_ext):
            rhs = cap * T
            rhs[0] += H_SURFACE * te
            T = lu_solve(lu, rhs)
            if rep == SPINUP - 1:
                out[k] = T
    return out


def soil_profile(t_ext, lam=SOIL_LAMBDA, rho_c=SOIL_RHO_C):
    """Température du sol (°C) heure par heure et par profondeur : tableau (heures, nœuds) ; profondeurs : `Z`."""
    t = np.ascontiguousarray(np.asarray(t_ext, dtype=float))
    b = t.tobytes()
    return _profile_cached(hashlib.sha1(b).hexdigest(), b, float(lam), float(rho_c))


def at_depth(profile, z):
    """Série (heures) à la profondeur z (interpolation linéaire entre nœuds)."""
    z = min(max(float(z), 0.0), DEPTH_M)
    j = int(np.searchsorted(Z, z))
    if j <= 0:
        return profile[:, 0]
    if j >= len(Z):
        return profile[:, -1]
    w = (z - Z[j - 1]) / (Z[j] - Z[j - 1])
    return (1 - w) * profile[:, j - 1] + w * profile[:, j]


def mean_over(profile, z0, z1, n=8):
    return np.mean([at_depth(profile, z) for z in np.linspace(z0, z1, n)], axis=0)


def _lowpass(x, tau_h):
    if tau_h <= 0:
        return x
    a = 1.0 / (1.0 + tau_h)
    out = np.empty_like(x)
    acc = x[:min(len(x), int(tau_h * 4) + 1)].mean()
    for _rep in range(2):                     # deux passes : régime établi en début de série
        for k, v in enumerate(x):
            acc += a * (v - acc)
            out[k] = acc
    return out


def characteristic_dimension(area_m2, exposed_perimeter_m):
    """B′ = A / (½ P) — EN ISO 13370 (m)."""
    return area_m2 / max(0.5 * exposed_perimeter_m, 1e-6)


U_NODE_MAX = 5.0          # plancher nu (W/m²·K) : borne du U propre déduit


def _node_conductances(floor_type, area, perim, depth):
    """Conductances (W/K) du nœud de vide sanitaire ou de sous-sol vers le sol et l'air extérieur, hors plancher."""
    if floor_type == 'vide_sanitaire':
        c = CRAWL
        return {'sol': area * c['u_sol'], 'ventilation': 0.34 * c['renouvellement_vol_h'] * area * c['hauteur_m']}
    c = BASEMENT
    return {'murs_enterres': perim * depth * c['u_murs_enterres'], 'radier': area * c['u_radier'],
            'murs_hors_sol': perim * c['hors_sol_m'] * c['u_murs_hors_sol'],
            'ventilation': 0.34 * c['renouvellement_vol_h'] * area * (depth + c['hors_sol_m'])}


def floor_u_node(floor_type, area_m2, exposed_perimeter_m, u_equivalent, basement_depth_m=None):
    """U propre du plancher (W/m²·K, vers le nœud qu'il voit) et une note éventuelle.

    `plancher.u` est un U **équivalent vers l'extérieur** (« final », comme le DPE 3CL : sol, vide sanitaire ou
    sous-sol compris). Terre-plein : il est gardé (le sol simulé à B′/2 en tient lieu, même moyenne annuelle que
    l'air). Vide sanitaire et sous-sol : U propre Uf tel que 1/Ue = 1/Uf + A/ΣH(nœud) (relation en série de
    l'EN ISO 13370) — le modèle redonne alors Ue en moyenne annuelle, sans compter deux fois le local tampon."""
    ue = float(u_equivalent)
    if floor_type not in ('vide_sanitaire', 'sous_sol'):
        return ue, None
    area = max(float(area_m2), 1.0)
    depth = float(basement_depth_m or BASEMENT['profondeur_m'])
    h = sum(_node_conductances(floor_type, area, max(float(exposed_perimeter_m), 1.0), depth).values())
    inv = 1.0 / max(ue, 1e-6) - area / h
    if inv <= 1.0 / U_NODE_MAX:
        return U_NODE_MAX, (f'U du plancher ({ue:.2f} W/m²·K) supérieur à ce qu’un plancher nu au-dessus de ce local '
                            f'peut donner : U propre plafonné à {U_NODE_MAX:.0f} W/m²·K.')
    return 1.0 / inv, None


def floor_boundary(floor_type, area_m2, exposed_perimeter_m, u_floor, t_ext, t_inside, basement_depth_m=None):
    """(température vue par le plancher heure par heure (°C), U propre du plancher vers elle, description).

    u_floor : U équivalent vers l'extérieur (`plancher.u`) ; t_ext, t_inside : séries horaires (°C) — t_inside :
    consigne de chauffage (couplage non itéré)."""
    t_ext = np.asarray(t_ext, dtype=float)
    t_in = np.broadcast_to(np.asarray(t_inside, dtype=float), t_ext.shape)
    prof = soil_profile(t_ext)
    area = max(float(area_m2), 1.0)
    perim = max(float(exposed_perimeter_m), 1.0)
    u_node, note = floor_u_node(floor_type, area, perim, u_floor, basement_depth_m)
    extra = {'u_equivalent': round(float(u_floor), 3), 'u_propre': round(u_node, 3)}
    if note:
        extra['note'] = note
    if floor_type == 'local_chauffe':
        return np.array(t_in, dtype=float), u_node, {'modele': 'local chauffé dessous : pas d’échange'}
    if floor_type in ('vide_sanitaire', 'sous_sol'):
        depth = float(basement_depth_m or BASEMENT['profondeur_m'])
        hs = _node_conductances(floor_type, area, perim, depth)
        h_f = area * u_node
        if floor_type == 'vide_sanitaire':
            c = CRAWL
            num = h_f * t_in + hs['sol'] * at_depth(prof, c['z_sol_m']) + hs['ventilation'] * t_ext
            desc = {'modele': 'vide sanitaire ventilé (nœud d’air : plancher, sol à 30 cm, air extérieur)',
                    **{k: c[k] for k in ('hauteur_m', 'renouvellement_vol_h')}}
        else:
            c = BASEMENT
            num = (h_f * t_in + hs['murs_enterres'] * mean_over(prof, 0.0, depth)
                   + hs['radier'] * at_depth(prof, depth + 1.0) + (hs['murs_hors_sol'] + hs['ventilation']) * t_ext)
            desc = {'modele': 'sous-sol non chauffé (nœud : plancher, murs enterrés, radier, murs hors sol, ventilation)',
                    'profondeur_m': round(depth, 2), 'renouvellement_vol_h': c['renouvellement_vol_h']}
        t = _lowpass(num / (h_f + sum(hs.values())), c['tau_h'])
        return t, u_node, {**desc, **extra}
    b = characteristic_dimension(area, perim)
    z = min(max(b / 2.0, Z_MIN_M), Z_MAX_M)
    return at_depth(prof, z), u_node, {'modele': 'terre-plein (sol à la profondeur B′/2)', 'b_prime_m': round(b, 2),
                                       'profondeur_m': round(z, 2), **extra}


def summary(t_seen, description):
    """Résumé lisible (entrées figées d'un calcul)."""
    t = np.asarray(t_seen, dtype=float)
    return {**description, 't_moyenne_c': round(float(t.mean()), 2), 't_min_c': round(float(t.min()), 2),
            't_max_c': round(float(t.max()), 2),
            'sol': {'lambda_w_mk': SOIL_LAMBDA, 'rho_c_mj_m3k': SOIL_RHO_C / 1e6, 'profondeur_colonne_m': DEPTH_M}}
