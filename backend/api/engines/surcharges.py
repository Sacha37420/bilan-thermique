"""Améliorations appliquées aux moteurs (lot 36, chantier bt-potentiels — I2, table § 7 du contrat).

Une amélioration (`Scenario`) modifie des **faits** (lus par `inputs.from_fiche` et `n2_inputs`), des **surcharges**
non scalaires (`Scenario.surcharges` : arbres plantés, toiture et murs végétalisés, rafraîchissement de l'air) et
des **protections** (`ShadingDevice.scenario`). Ce module :
- valide les surcharges (une surcharge mal formée n'est pas appliquée, et c'est écrit) — `clean` ;
- les pose sur les paramètres N0/N1 — `apply_to_params` ;
- corrige la température extérieure du rafraîchissement de l'air — `corrected_weather` (hypothèse affichée) ;
- fixe la règle de la surventilation nocturne commune à N1 et N2 — `night_static_mask` ;
- décrit, niveau par niveau, ce qui a été appliqué — `describe` (→ `CalcRun.inputs['ameliorations']`).

**Surventilation nocturne** (`ventilation.nocturne_vol_h`, `ventilation.nocturne_seuil_c`) : débit ajouté de 22 h à
7 h, de mai à septembre, quand l'air extérieur est sous le seuil, que l'air intérieur de l'heure précédente dépasse
24 °C (il y a de la chaleur à évacuer — sans cette condition, une nuit fraîche de mai refroidit un logement chauffé et
augmente le chauffage) **et** qu'il est plus chaud d'au moins 1 °C que dehors (ouverture utile) ; hypothèse écrite
dans les entrées figées. N0 (mensuel) ne la représente pas.

**Rafraîchissement de l'air** (`rafraichissement_air` : delta_c, mois, heures) : la température extérieure vue par le
bâtiment (air, ventilation, sol) est abaissée de delta_c aux mois et heures donnés — **une hypothèse**, recopiée
telle quelle (texte et valeur) dans les entrées figées du calcul ; elle n'est pas calculée par l'app.
"""

import math

import numpy as np

NIGHT_HOURS = (22, 7)
NIGHT_MONTHS = (5, 6, 7, 8, 9)
NIGHT_MARGIN_C = 1.0
NIGHT_INDOOR_TRIGGER_C = 24.0
HYPOTHESE_NOCTURNE = ('Surventilation de 22 h à 7 h, de mai à septembre, si l’air extérieur est sous le seuil, '
                      'l’air intérieur au-dessus de 24 °C et plus chaud que dehors d’au moins 1 °C.')
MAX_TREES = 50

HYPOTHESE_RAFRAICHISSEMENT = ('Hypothèse : rafraîchissement de l’air local (végétalisation de la parcelle) — la '
                              'température extérieure est abaissée de la valeur indiquée ; ce n’est pas un calcul.')

N01_KEYS = {
    'identite.surface_m2', 'identite.niveaux', 'identite.hauteur_m', 'identite.emprise_m2', 'identite.mitoyennete',
    'identite.usage', 'mur.u', 'toiture.u', 'toiture.type', 'toiture.pente_deg', 'plancher.type', 'plancher.u',
    'sous_sol.profondeur_m', 'toiture.absorptance', 'abords.albedo', 'toiture.vegetalisee', 'murs.vegetalises',
    'enveloppe.inertie', 'enveloppe.couleur_absorptance', 'baies.uw', 'baies.g', 'ventilation.type',
    'ventilation.debit_vol_h', 'ventilation.rendement_recup', 'ventilation.n50', 'ventilation.nocturne_vol_h',
    'ventilation.nocturne_seuil_c', 'usage.consigne_chauffage_c', 'usage.consigne_froid_c', 'usage.apports_w_m2',
    *{f'baies.ratio_{o}' for o in 'NESO'}}
N2_FICHE_EXTRA = {'mur.materiau', 'mur.isolation', 'baies.vitrage'}
N2_EXPERT_IGNORED = {'mur.u', 'toiture.u', 'plancher.u', 'baies.uw', 'baies.g', 'mur.materiau', 'mur.isolation',
                     'baies.vitrage', 'enveloppe.inertie', 'enveloppe.couleur_absorptance', 'toiture.type',
                     'toiture.pente_deg', 'identite.niveaux', 'identite.hauteur_m', 'identite.emprise_m2',
                     'identite.mitoyennete', 'murs.vegetalises', 'toiture.vegetalisee',
                     *{f'baies.ratio_{o}' for o in 'NESO'}}
SYSTEM_PREFIXES = ('systeme.', 'usage.occupants', 'identite.logements', 'dpe.')


# ── Validation ────────────────────────────────────────────────────────────────

def _num(d, k, lo, hi, default=None):
    v = d.get(k, default)
    if isinstance(v, bool) or v is None:
        raise ValueError(k)
    v = float(v)
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(k)
    return v


def _tree(t):
    out = {'x_m': _num(t, 'x_m', -500, 500), 'y_m': _num(t, 'y_m', -500, 500),
           'hauteur_m': _num(t, 'hauteur_m', 1, 60), 'rayon_houppier_m': _num(t, 'rayon_houppier_m', 0.3, 30),
           'tronc_m': _num(t, 'tronc_m', 0, 40, 2.0), 'persistant': bool(t.get('persistant', False)),
           'transmission_ete': _num(t, 'transmission_ete', 0, 1, 0.2),
           'transmission_hiver': _num(t, 'transmission_hiver', 0, 1, 0.7)}
    if out['tronc_m'] >= out['hauteur_m']:
        raise ValueError('tronc_m')
    return out


def clean(surcharges):
    """(surcharges valides, [{'cle', 'raison'}] rejetées). Rien n'est appliqué de ce qui est mal formé."""
    s = surcharges if isinstance(surcharges, dict) else {}
    ok, rejected = {}, []

    def reject(key):
        rejected.append({'cle': key, 'raison': 'non appliquée : surcharge mal formée (valeur hors bornes ou manquante)'})

    if s.get('arbres') is not None:
        try:
            trees = s['arbres']
            if not isinstance(trees, list) or len(trees) > MAX_TREES:
                raise ValueError('arbres')
            ok['arbres'] = [_tree(t) for t in trees if isinstance(t, dict)]
            if len(ok['arbres']) != len(trees):
                raise ValueError('arbres')
        except (ValueError, TypeError):
            ok.pop('arbres', None)
            reject('arbres')
    if s.get('toiture_vegetalisee') is not None:
        try:
            g = s['toiture_vegetalisee']
            ok['toiture_vegetalisee'] = {'substrat_m': _num(g, 'substrat_m', 0.02, 1.0, 0.10),
                                         'lai': _num(g, 'lai', 0, 8, 2.0), 'part': _num(g, 'part', 0, 1, 1.0)}
        except (ValueError, TypeError, AttributeError):
            reject('toiture_vegetalisee')
    if s.get('murs_vegetalises') is not None:
        try:
            g = s['murs_vegetalises']
            ors = g.get('orientations')
            if not isinstance(ors, list) or not ors or any(o not in ('N', 'E', 'S', 'O') for o in ors):
                raise ValueError('orientations')
            ok['murs_vegetalises'] = {'orientations': sorted(set(ors), key='NESO'.index),
                                      'lai': _num(g, 'lai', 0, 8, 2.0), 'part': _num(g, 'part', 0, 1, 0.5)}
        except (ValueError, TypeError, AttributeError):
            reject('murs_vegetalises')
    if s.get('rafraichissement_air') is not None:
        try:
            r = s['rafraichissement_air']
            months = r.get('mois') or list(range(1, 13))
            hours = r.get('heures') or list(range(24))
            if not all(isinstance(m, int) and 1 <= m <= 12 for m in months) or \
                    not all(isinstance(h, int) and 0 <= h <= 23 for h in hours):
                raise ValueError('mois')
            ok['rafraichissement_air'] = {'delta_c': _num(r, 'delta_c', 0, 5), 'mois': sorted(set(months)),
                                          'heures': sorted(set(hours)),
                                          'hypothese': str(r.get('hypothese') or HYPOTHESE_RAFRAICHISSEMENT)[:500]}
        except (ValueError, TypeError, AttributeError):
            reject('rafraichissement_air')
    for k in s:
        if k not in ('arbres', 'toiture_vegetalisee', 'murs_vegetalises', 'rafraichissement_air'):
            rejected.append({'cle': k, 'raison': 'non appliquée : surcharge inconnue des moteurs'})
    return ok, rejected


def of(scenario):
    return clean((scenario.surcharges or {}) if scenario is not None else {})[0]


# ── N0 / N1 ───────────────────────────────────────────────────────────────────

def _footprint_poly(geometry, a_floor, levels):
    import shapely.geometry
    fp = (geometry or {}).get('footprint_xy')
    if fp and len(fp) >= 3:
        return shapely.geometry.Polygon(fp)
    side = math.sqrt(max(a_floor / max(levels, 1) / 0.8, 9.0))
    return shapely.geometry.Polygon([(0, 0), (side, 0), (side, side), (0, side)])


def apply_to_params(p, surcharges, values=None, geometry=None, north_deg=0.0):
    """Pose les surcharges valides sur des paramètres N0/N1 (verdure, arbres) ; la fiche (faits) l'est déjà."""
    values = values or {}
    s = surcharges or {}
    p.north_deg = float(north_deg or 0.0)
    if 'toiture_vegetalisee' in s and values.get('toiture.vegetalisee') is not False:
        p.green_roof = dict(s['toiture_vegetalisee'])
    if 'murs_vegetalises' in s and values.get('murs.vegetalises') is not False:
        p.green_walls = dict(s['murs_vegetalises'])
    if s.get('arbres'):
        from .. import horizon
        levels = int(values.get('identite.niveaux') or 1)
        poly = _footprint_poly(geometry, p.a_floor, levels)
        p.trees = list(s['arbres'])
        p.facade_points = horizon.facade_points(poly, p.wall_height, north_deg=p.north_deg)
    return p


def corrected_weather(weather, surcharges):
    """Série météo dont la température extérieure est abaissée par le rafraîchissement de l'air (hypothèse)."""
    r = (surcharges or {}).get('rafraichissement_air')
    if not r or not r.get('delta_c'):
        return weather
    from .shading import month_of
    months, hours, d = set(r['mois']), set(r['heures']), float(r['delta_c'])
    out = []
    for i, w in enumerate(weather):
        h = w.get('hour_index') if w.get('hour_index') is not None else i
        if (int(h) % 24) in hours and int(month_of([h])[0]) in months:
            w = {**w, 't_ext': round(float(w['t_ext']) - d, 3)}
        out.append(w)
    return out


def night_static_mask(hidx, t_ext, threshold):
    """Heures où la surventilation nocturne peut jouer (nuit d'été, air extérieur sous le seuil) — la condition
    « plus frais que dedans » est ajoutée par le moteur, qui connaît la température intérieure."""
    from .shading import month_of
    hidx = np.asarray(hidx)
    hour = hidx % 24
    night = (hour >= NIGHT_HOURS[0]) | (hour < NIGHT_HOURS[1])
    return night & np.isin(month_of(hidx), NIGHT_MONTHS) & (np.asarray(t_ext, dtype=float) < float(threshold))


# ── Description (entrées figées) ──────────────────────────────────────────────

def _fact_application(key, level, n2_mode):
    if key.startswith(SYSTEM_PREFIXES):
        return True, 'consommations : systèmes et usages (après le besoin)'
    if level == 'N0' and key in ('ventilation.nocturne_vol_h', 'ventilation.nocturne_seuil_c'):
        return False, 'non appliquée : le N0 est mensuel (surventilation nocturne en N1 et N2)'
    if level in ('N0', 'N1'):
        if key in N01_KEYS:
            return True, 'paramètres du bâtiment (fiche)'
        return False, 'sans effet direct sur ce niveau (le U et les paramètres déduits en tiennent compte)'
    if n2_mode == 'experte' and key in N2_EXPERT_IGNORED:
        return False, 'non appliquée : enveloppe experte (parois du catalogue assignées à la main)'
    if key in N01_KEYS or key in N2_FICHE_EXTRA:
        return True, 'parois reconstituées et réglages du N2 (fiche)'
    return False, 'sans effet direct sur ce niveau'


SURCHARGE_APPLICATION = {
    'arbres': {'N0': 'profil d’horizon des façades : transmission saisonnière du houppier sur le direct',
               'N1': 'profil d’horizon des façades : transmission saisonnière du houppier sur le direct',
               'N2': 'obstacles translucides ajoutés à l’environnement du lancer de rayons (houppiers), saison par saison'},
    'toiture_vegetalisee': {'N0': 'U de la toiture (substrat) et absorptance du feuillage',
                            'N1': 'U, absorptance du feuillage et puits d’évapotranspiration en surface',
                            'N2': 'couche de substrat dans la toiture, absorptance du feuillage et puits d’évapotranspiration'},
    'murs_vegetalises': {'N0': 'U des murs des façades choisies (lame d’air) et absorptance du feuillage',
                         'N1': 'U, absorptance du feuillage et puits d’évapotranspiration en surface',
                         'N2': 'lame d’air dans les murs des façades choisies, absorptance et puits d’évapotranspiration'},
    'rafraichissement_air': {lvl: 'température extérieure abaissée (hypothèse affichée)' for lvl in ('N0', 'N1', 'N2')},
}


def describe(building, scenario, level, n2_mode=None):
    """Ce que l'amélioration modifie et comment ce niveau l'applique : liste d'éléments
    {type, cle, valeur, appliquee, application, [libelle], [hypothese]} — vide pour l'état actuel."""
    if scenario is None:
        return []
    from .. import facts
    from ..models import BuildingFact, ShadingDevice
    from . import shading
    out = []
    for f in BuildingFact.objects.filter(building=building, scenario=scenario, retired_at__isnull=True).order_by('key'):
        ok, how = _fact_application(f.key, level, n2_mode)
        spec = facts.BY_KEY.get(f.key)
        item = {'type': 'fait', 'cle': f.key, 'libelle': spec.label if spec else f.key, 'valeur': f.value,
                'appliquee': ok, 'application': how}
        if f.key == 'ventilation.nocturne_vol_h' and ok:
            item['hypothese'] = HYPOTHESE_NOCTURNE
        out.append(item)
    valid, rejected = clean(scenario.surcharges or {})
    for k, v in valid.items():
        item = {'type': 'surcharge', 'cle': k, 'valeur': v, 'appliquee': True,
                'application': SURCHARGE_APPLICATION[k][level]}
        if level == 'N2' and n2_mode == 'experte' and k in ('toiture_vegetalisee', 'murs_vegetalises'):
            item.update(appliquee=False, application='non appliquée : enveloppe experte (parois du catalogue)')
        if k == 'rafraichissement_air':
            item['hypothese'] = v['hypothese']
        if k in ('toiture_vegetalisee', 'murs_vegetalises') and level != 'N0':
            from .vegetation import HYPOTHESE_EVAPO
            item['hypothese'] = HYPOTHESE_EVAPO
        out.append(item)
    for r in rejected:
        out.append({'type': 'surcharge', 'cle': r['cle'], 'valeur': None, 'appliquee': False, 'application': r['raison']})
    for d in ShadingDevice.objects.filter(building=building, scenario=scenario).order_by('pk'):
        m = shading.KINDS.get(d.kind)
        out.append({'type': 'protection', 'cle': d.kind, 'id': d.pk, 'libelle': m['libelle'] if m else d.kind,
                    'valeur': {'orientation': d.orientation, 'mode': d.mode, 'params': d.params, 'control': d.control},
                    'appliquee': m is not None, 'application': m['application'][level] if m else
                    'non appliquée : type de protection inconnu'})
    return out
