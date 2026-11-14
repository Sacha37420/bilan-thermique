"""Fourchette d'un résultat N1 par propagation Monte-Carlo (refonte, § 6.4 — amorcée au Lot 11).

Chaque valeur numérique de la fiche est tirée autour de sa valeur effective avec un écart relatif
qui dépend de sa **méthode** (un défaut est très incertain, une mesure peu). Le N1 est vectorisé :
48 tirages coûtent à peine plus qu'un calcul. On publie P10–P90 du besoin de chauffage.
Graine fixe (dérivée de l'empreinte de la fiche) : même fiche, même fourchette.
"""

import numpy as np

from . import facts

REL_SPREAD = {'defaut': 0.30, 'infere': 0.18, 'declare': 0.10, 'document': 0.07, 'mesure': 0.03}
UNCERTAIN_KEYS = (
    'identite.surface_m2', 'identite.hauteur_m', 'identite.emprise_m2', 'identite.mitoyennete',
    'mur.u', 'toiture.u', 'plancher.u', 'baies.uw', 'baies.g',
    'baies.ratio_N', 'baies.ratio_E', 'baies.ratio_S', 'baies.ratio_O',
    'ventilation.debit_vol_h', 'ventilation.n50', 'ventilation.rendement_recup',
    'usage.consigne_chauffage_c', 'usage.apports_w_m2',
)
ABSOLUTE = {'usage.consigne_chauffage_c': {'defaut': 1.5, 'infere': 1.0, 'declare': 0.7, 'document': 0.5, 'mesure': 0.3}}
DEFAULT_SAMPLES = 48


def sample_values(eff, n=DEFAULT_SAMPLES, seed=0):
    """Liste de n fiches (dict clé → valeur) ; la première est la fiche nominale."""
    rng = np.random.default_rng(seed)
    base = {k: f.value for k, f in eff.items()}
    out = [dict(base)]
    for _ in range(n - 1):
        v = dict(base)
        for k in UNCERTAIN_KEYS:
            f = eff.get(k)
            if f is None or not isinstance(f.value, (int, float)) or isinstance(f.value, bool):
                continue
            spec = facts.BY_KEY[k]
            if k in ABSOLUTE:
                x = f.value + rng.normal(0.0, ABSOLUTE[k][f.method])
            else:
                x = f.value * float(np.exp(rng.normal(0.0, REL_SPREAD[f.method])))
            lo = spec.min if spec.min is not None else -np.inf
            hi = spec.max if spec.max is not None else np.inf
            v[k] = float(min(max(x, lo), hi))
        out.append(v)
    return out


def percentiles(values, ps=(10, 50, 90)):
    arr = np.asarray(values, dtype=float)
    return {p: float(np.percentile(arr, p)) for p in ps}
