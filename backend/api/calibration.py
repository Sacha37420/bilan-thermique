"""Mesures, sensibilité et calage (Lot 17, refonte — § B6 et § 6.4).

- **Mesures** : factures (périodes + kWh), courbes Linky / Gazpar (CSV horaire ou journalier, agrégé par
  mois), sondes de température intérieure, test d'étanchéité (fait « mesuré » sur `ventilation.n50`).
  Données personnelles : visibles de leur auteur et des gestionnaires du bâtiment de parc seulement.
- **Sensibilité** : criblage de Morris (effets élémentaires, r trajectoires) sur les valeurs incertaines,
  dans un seul passage N1 vectorisé ; μ* classe l'influence, σ signale les interactions.
- **Calage** : sur la météo **réelle** de la période facturée, optimisation bornée (± 2 écarts types de
  la méthode, en multiplicateur logarithmique) des seuls paramètres influents qui ne sont pas des
  déclarations de l'utilisateur ; critères ASHRAE Guideline 14 au pas mensuel : CV(RMSE) ≤ 15 %,
  |NMBE| ≤ 5 %. Les valeurs ajustées entrent comme « inféré par calage », jamais comme mesurées.
"""

import csv
import io
import math
from collections import OrderedDict
from datetime import date, datetime, timedelta

import numpy as np

from . import facts, uncertainty
from .engines import inputs as engine_inputs
from .engines import n1, systems

CV_RMSE_MAX = 15.0
NMBE_MAX = 5.0
MAX_CALIBRATED = 4


class MeasureError(ValueError):
    pass


# ── Lecture des mesures ─────────────────────────────────────────────────────

def _d(s):
    s = str(s).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M', '%Y-%m-%dT%H:%M:%SZ'):
        try:
            return datetime.strptime(s[:19] if 'T' in s and fmt.endswith('%S') else s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        raise MeasureError('Date illisible.') from None


def parse_bills(rows):
    """[{start, end, kwh}] → liste triée, contrôlée (périodes de 7 à 400 jours, kWh ≥ 0)."""
    out = []
    for r in rows:
        try:
            s, e, k = _d(r['start']).date(), _d(r['end']).date(), float(str(r['kwh']).replace(',', '.'))
        except (KeyError, TypeError, ValueError):
            raise MeasureError('Facture : début, fin et kWh attendus.') from None
        if e <= s or not 7 <= (e - s).days <= 400 or k < 0:
            raise MeasureError('Facture : période de 7 à 400 jours, consommation positive.')
        out.append({'start': s.isoformat(), 'end': e.isoformat(), 'kwh': round(k, 2)})
    if not out:
        raise MeasureError('Aucune facture.')
    return sorted(out, key=lambda x: x['start'])


def parse_curve_csv(text):
    """Courbe de charge (date;kWh), horaire ou journalière → consommations mensuelles."""
    text = text.lstrip('﻿')
    delim = ';' if text.count(';') >= text.count(',') else ','
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    monthly = OrderedDict()
    n = 0
    for i, row in enumerate(reader):
        if len(row) < 2:
            continue
        try:
            t = _d(row[0])
            v = float(row[1].replace(',', '.'))
        except (MeasureError, ValueError):
            if i == 0:
                continue  # en-tête
            raise MeasureError(f'Ligne {i + 1} illisible (date;kWh).') from None
        if v < 0:
            raise MeasureError(f'Ligne {i + 1} : consommation négative.')
        key = (t.year, t.month)
        monthly[key] = monthly.get(key, 0.0) + v
        n += 1
        if n > 40000:
            raise MeasureError('Courbe trop longue (plus de 4 ans horaires).')
    if not monthly:
        raise MeasureError('Courbe vide.')
    out = []
    for (y, m), kwh in monthly.items():
        start = date(y, m, 1)
        end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append({'start': start.isoformat(), 'end': end.isoformat(), 'kwh': round(kwh, 2)})
    return out


def parse_sensor_csv(text):
    """Sonde (date;°C) → série [{t, value}] (au plus un an horaire)."""
    reader = csv.reader(io.StringIO(text.lstrip('﻿')), delimiter=';' if text.count(';') >= text.count(',') else ',')
    out = []
    for i, row in enumerate(reader):
        try:
            out.append({'t': _d(row[0]).isoformat(), 'value': float(row[1].replace(',', '.'))})
        except (IndexError, MeasureError, ValueError):
            if i == 0:
                continue
            raise MeasureError(f'Ligne {i + 1} illisible (date;°C).') from None
        if len(out) > 9000:
            raise MeasureError('Plus d’un an de mesures horaires.')
    if not out:
        raise MeasureError('Aucune mesure.')
    return out


# ── Modèle mensuel de consommation ──────────────────────────────────────────

def period_bounds(bills):
    return date.fromisoformat(bills[0]['start']), date.fromisoformat(bills[-1]['end']) - timedelta(days=1)


def consumption_by_bill(values, results, weather, bills, energy, a_floor, volume):
    """Consommation modélisée de l'énergie `energy` sur chaque période facturée (kWh), pour une liste de
    résultats N1 (séries `phi_hc_w` requises) calculés sur la météo de la période."""
    t_ext = np.array([w['t_ext'] for w in weather])
    hidx = np.array([w.get('hour_index', i) for i, w in enumerate(weather)])
    start, _end = period_bounds(bills)
    out = []
    for r in results:
        heat = np.clip(np.asarray(r['phi_hc_w'], dtype=float), 0, None)
        e = systems.evaluate(values, float(heat.sum() / 1000.0), r.get('cooling_kwh') or 0.0, a_floor, volume, heat, t_ext)
        heat_share = e['by_usage_kwh']['chauffage'] / max(heat.sum() / 1000.0, 1e-9)  # kWh finaux par kWh utile
        # Autres usages de la même énergie (ECS, éclairage…) : répartis uniformément dans le temps,
        # au prorata de la durée (évalués sur un an, ramenés à la période simulée).
        other_year = sum(v for u, v in e['by_usage_kwh'].items() if u != 'chauffage' and e['by_usage_energy'][u] == energy)
        heating_is_energy = e['energy'] == energy
        n_hours = len(heat)
        per_bill = []
        for b in bills:
            s = (date.fromisoformat(b['start']) - start).days * 24
            f = (date.fromisoformat(b['end']) - start).days * 24
            m = (hidx >= s) & (hidx < f)
            kwh = (heat[m].sum() / 1000.0) * heat_share if heating_is_energy else 0.0
            kwh += other_year * (m.sum() / 8760.0)
            per_bill.append(kwh)
        out.append(per_bill)
    return np.array(out)


def criteria(measured, modelled):
    m, p = np.asarray(measured, dtype=float), np.asarray(modelled, dtype=float)
    n = len(m)
    mean = m.mean() if n else 0.0
    if n < 2 or mean <= 0:
        return {'cv_rmse_pct': None, 'nmbe_pct': None, 'ok': False}
    cv = math.sqrt(((m - p) ** 2).sum() / (n - 1)) / mean * 100
    nmbe = (m - p).sum() / ((n - 1) * mean) * 100
    return {'cv_rmse_pct': round(float(cv), 1), 'nmbe_pct': round(float(nmbe), 1),
            'ok': bool(cv <= CV_RMSE_MAX and abs(nmbe) <= NMBE_MAX)}


# ── Sensibilité (Morris) ────────────────────────────────────────────────────

def _numeric_keys(eff, only_automatic=False):
    keys = []
    for k in uncertainty.UNCERTAIN_KEYS:
        f = eff.get(k)
        if f is None or not isinstance(f.value, (int, float)) or isinstance(f.value, bool):
            continue
        if only_automatic and facts.is_override(f):
            continue
        keys.append(k)
    return keys


def _spread(f, k):
    if k in uncertainty.ABSOLUTE:
        return ('abs', uncertainty.ABSOLUTE[k][f.method])
    return ('rel', uncertainty.REL_SPREAD[f.method])


def _apply(eff, base, k, z):
    """Valeur de `k` à z écarts types de sa valeur effective (z ∈ [-2, 2]), bornée au catalogue."""
    f = eff[k]
    kind, s = _spread(f, k)
    x = f.value + z * s if kind == 'abs' else f.value * math.exp(z * s)
    spec = facts.BY_KEY[k]
    return min(max(x, spec.min if spec.min is not None else -1e18), spec.max if spec.max is not None else 1e18)


def morris(building, eff, weather, geom, r=8, levels=4, seed=1, keys=None, output=None):
    """Effets élémentaires de Morris. Rend [{key, mu_star, sigma}] trié par μ* décroissant."""
    from . import shading_service
    keys = keys or _numeric_keys(eff)
    k = len(keys)
    if not k:
        return []
    rng = np.random.default_rng(seed)
    grid = np.linspace(-2, 2, levels)
    delta = grid[levels // 2] - grid[0]
    base = {kk: f.value for kk, f in eff.items()}
    points, traj = [], []
    for _ in range(r):
        x = rng.choice(grid[:levels // 2], size=k)
        order = rng.permutation(k)
        cur = x.copy()
        pts = [cur.copy()]
        for j in order:
            cur = cur.copy()
            cur[j] += delta
            pts.append(cur)
        traj.append(order)
        points += pts
    params = []
    for z in points:
        v = dict(base)
        for j, kk in enumerate(keys):
            v[kk] = _apply(eff, base, kk, float(z[j]))
        p = engine_inputs.from_fiche(v, geom)
        shading_service.apply(p, building)
        params.append(p)
    res = n1.run(params, weather, keep_series=output is not None)
    y = np.array([output(rr) if output else rr['heating_kwh'] for rr in res])
    ee = {kk: [] for kk in keys}
    for t, order in enumerate(traj):
        off = t * (k + 1)
        for step, j in enumerate(order):
            ee[keys[j]].append((y[off + step + 1] - y[off + step]) / delta)
    out = [{'key': kk, 'label': facts.BY_KEY[kk].label, 'mu_star': round(float(np.mean(np.abs(v))), 1),
            'sigma': round(float(np.std(v)), 1), 'method': eff[kk].method} for kk, v in ee.items()]
    return sorted(out, key=lambda x: -x['mu_star'])


# ── Calage ──────────────────────────────────────────────────────────────────

def calibrate(building, bills, energy, weather, *, geom=None, max_params=MAX_CALIBRATED, max_iter=40):
    """Ajuste les paramètres influents (non déclarés) pour reproduire les factures. Ne lève pas : rend un
    compte rendu (paramètres, critères avant/après, séries mensuelles)."""
    from scipy.optimize import minimize
    from . import shading_service
    eff = facts.effective(building)
    base = {k: f.value for k, f in eff.items()}
    p0 = engine_inputs.from_fiche(base, geom)
    shading_service.apply(p0, building)
    measured = np.array([b['kwh'] for b in bills])
    a_floor, volume = p0.a_floor, p0.volume

    def model(vals_list):
        ps = []
        for v in vals_list:
            p = engine_inputs.from_fiche(v, geom)
            shading_service.apply(p, building)
            ps.append(p)
        res = n1.run(ps, weather, keep_series=True)
        return consumption_by_bill(vals_list[0], res, weather, bills, energy, a_floor, volume)

    before = model([base])[0]
    crit_before = criteria(measured, before)
    # Grandeurs géométriques (surface, hauteur, emprise, mitoyenneté) : elles se mesurent ou se
    # corrigent, elles ne se calent pas — sinon le calage « explique » une facture par une surface fausse.
    candidates = [k for k in _numeric_keys(eff, only_automatic=True) if not k.startswith('identite.')]
    ranking = []
    if candidates:
        def out_fn(rr):
            return float(np.clip(np.asarray(rr['phi_hc_w']), 0, None).sum())
        ranking = morris(building, eff, weather, geom, r=6, keys=candidates, output=out_fn)
    chosen = [x['key'] for x in ranking if x['mu_star'] > 0][:max_params]
    if not chosen:
        return {'ok': False, 'reason': 'Aucun paramètre ajustable (tout est déclaré ou sans influence).',
                'before': crit_before, 'measured': measured.tolist(), 'model_before': before.tolist()}

    def vals_for(z):
        v = dict(base)
        for j, k in enumerate(chosen):
            v[k] = _apply(eff, base, k, float(z[j]))
        return v

    norm = max(float((measured ** 2).sum()), 1e-9)
    h = 0.05
    evaluations = [0]

    def cost_and_grad(z):
        # Valeur et gradient (différences finies avant) en UN passage N1 vectorisé : le jeu courant et
        # ses k perturbations sont simulés ensemble — k+1 fois moins d'appels qu'un optimiseur naïf.
        zs = [np.array(z, dtype=float)] + [np.array(z, dtype=float) + h * np.eye(len(z))[j] for j in range(len(z))]
        preds = model([vals_for(np.clip(q, -2, 2)) for q in zs])
        evaluations[0] += len(zs)
        costs = [float(((pr - measured) ** 2).sum() / norm) + 0.002 * float(np.sum(np.square(q))) for pr, q in zip(preds, zs)]
        grad = np.array([(costs[j + 1] - costs[0]) / h for j in range(len(z))])
        return costs[0], grad
    res = minimize(cost_and_grad, np.zeros(len(chosen)), jac=True, method='L-BFGS-B', bounds=[(-2, 2)] * len(chosen),
                   options={'maxiter': max_iter})
    best = vals_for(res.x)
    after = model([best])[0]
    crit_after = criteria(measured, after)
    return {
        'ok': crit_after['ok'], 'energy': energy, 'before': crit_before, 'after': crit_after,
        'parameters': [{'key': k, 'label': facts.BY_KEY[k].label, 'from': round(float(base[k]), 3),
                        'to': round(float(best[k]), 3), 'z': round(float(res.x[j]), 2)} for j, k in enumerate(chosen)],
        'ranking': ranking[:8], 'measured': measured.tolist(), 'model_before': [round(x, 1) for x in before],
        'model_after': [round(x, 1) for x in after], 'bills': bills, 'n_evaluations': evaluations[0],
    }


def record_calibration(building, report, author_sub=''):
    """Valeurs ajustées → faits « inféré par calage » (automatiques, sous toute déclaration)."""
    src = (f"calage sur factures (CV(RMSE) {report['after']['cv_rmse_pct']} %, NMBE {report['after']['nmbe_pct']} %)")[:300]
    for p in report.get('parameters', []):
        facts.record(building, p['key'], p['to'], 'infere', source=src, confidence=0.6, refresh=False)
    facts.refresh_scores(building)
