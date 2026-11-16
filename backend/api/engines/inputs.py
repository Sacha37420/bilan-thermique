"""Traducteurs vers les paramètres communs de N0/N1 (Lot 10, refonte — § 6.2).

`from_fiche(values, geometry)` : fiche effective (dict clé → valeur) + géométrie simplifiée de la
chaîne (facultative) → paramètres. `from_envelope(...)` : bâtiment maillé du N2 (triangles +
modèles de paroi) → mêmes paramètres, pour comparer N1 et N2 sur un même bâtiment.

Hypothèses communes avec le N2 (pour rester comparables, § 6.3) : pas d'échange infrarouge avec le
ciel, ciel isotrope, albédo du sol 0,20, plancher bas couplé à une température de sol constante.
Ponts thermiques : non comptés (hors périmètre, comme le N2 — `refonte.md` D9).
"""

import math
from dataclasses import dataclass, field

ORIENTATIONS = ('N', 'E', 'S', 'O')
CEILING_H = 2.5
INERTIA = {  # C_m (J/K par m² de plancher) et A_m (m² par m² de plancher), ISO 13790 tableau 12
    'legere': (80_000.0, 2.5), 'moyenne': (165_000.0, 2.5), 'lourde': (260_000.0, 3.0),
}
GROUND_T_DEFAULT = 12.0
RESIDENTIAL = ('maison', 'logement_collectif')


@dataclass
class Params:
    a_floor: float                      # surface chauffée (m²)
    volume: float                       # volume chauffé (m³)
    walls: dict                         # orientation → {'opaque': m², 'window': m², 'u': W/m²K, 'uw': W/m²K}
    roof_area: float
    u_roof: float
    floor_area: float
    u_floor: float                      # vers le sol, U « final » (résistance du sol comprise)
    g_window: float
    frame_factor: float = 0.8           # part vitrée d'une baie (1 − part de cadre)
    absorptance: float = 0.6
    c_m: float = 165_000.0
    a_m_factor: float = 2.5
    n_vent: float = 0.5                 # renouvellement effectif (vol/h, récupération déduite)
    gains_w_m2: float = 4.0
    t_heat: float = 19.0
    t_cool: float | None = None
    residential: bool = True
    t_ground: float = GROUND_T_DEFAULT
    horizon: dict = field(default_factory=lambda: {o: 0.0 for o in ORIENTATIONS})
    horizon_profile: dict | None = None   # Lot 30 : profil par tranche d'azimut (`api.horizon`)
    # Protections solaires (Lot 14) : [{kind, orientation, mode, params, control}] — engines.shading.
    devices: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)

    @property
    def h_tr_w(self):
        return sum(w['window'] * w['uw'] for w in self.walls.values())

    @property
    def h_tr_op_air(self):
        return sum(w['opaque'] * w['u'] for w in self.walls.values()) + self.roof_area * self.u_roof

    @property
    def h_floor(self):
        return self.floor_area * self.u_floor

    @property
    def h_ve(self):
        return 0.34 * self.n_vent * self.volume

    def envelope_area(self):
        return sum(w['opaque'] + w['window'] for w in self.walls.values()) + self.roof_area + self.floor_area


def _v(values, key, default):
    v = values.get(key)
    return default if v is None else v


def effective_ventilation(values):
    vtype = _v(values, 'ventilation.type', 'vmc_sf_auto')
    n_hyg = float(_v(values, 'ventilation.debit_vol_h', 0.5))
    eta = float(_v(values, 'ventilation.rendement_recup', 0.0)) if vtype == 'vmc_df' else 0.0
    n_inf = float(_v(values, 'ventilation.n50', 6.0)) / 20.0  # règle de Sherman-Grimsrud
    if vtype == 'naturelle':
        return max(n_hyg, n_inf)
    return n_hyg * (1.0 - eta) + n_inf


def from_fiche(values, geometry=None):
    """Paramètres N0/N1 d'une fiche effective. `geometry` = `Building.pipeline['geometry']` (facultatif)."""
    a_floor = float(_v(values, 'identite.surface_m2', 100.0))
    levels = max(1, int(_v(values, 'identite.niveaux', 1)))
    height = float(_v(values, 'identite.hauteur_m', levels * 3.0))
    emprise = float(_v(values, 'identite.emprise_m2', a_floor / levels / 0.8))
    mit = float(_v(values, 'identite.mitoyennete', 0.0))
    # Murs bruts par orientation : répartition de la géométrie relevée, sinon un carré.
    if geometry and geometry.get('murs_exposes_m2') and geometry.get('perimetre_m'):
        exposed = geometry['murs_exposes_m2']
        tot = sum(exposed.values()) or 1.0
        shares = {o: exposed.get(o, 0.0) / tot for o in ORIENTATIONS}
        perim = float(geometry['perimetre_m'])
    else:
        shares = {o: 0.25 for o in ORIENTATIONS}
        perim = 4.0 * math.sqrt(max(emprise, 1.0))
    gross_wall = perim * height * (1.0 - mit)
    u_wall = float(_v(values, 'mur.u', 1.0))
    uw = float(_v(values, 'baies.uw', 2.8))
    walls = {}
    for o in ORIENTATIONS:
        gross = gross_wall * shares[o]
        ratio = float(_v(values, f'baies.ratio_{o}', 0.15))
        walls[o] = {'opaque': gross * (1 - ratio), 'window': gross * ratio, 'u': u_wall, 'uw': uw}
    inertia = _v(values, 'enveloppe.inertie', 'moyenne')
    c_m, a_m = INERTIA.get(inertia, INERTIA['moyenne'])
    usage = _v(values, 'identite.usage', 'autre')
    floor_type = _v(values, 'plancher.type', 'terre_plein')
    floor_area = 0.0 if floor_type == 'local_chauffe' else emprise
    t_cool = values.get('usage.consigne_froid_c')
    roof_area = emprise
    if _v(values, 'toiture.type', 'terrasse') in ('2pans', '4pans'):
        roof_area = emprise / max(math.cos(math.radians(float(_v(values, 'toiture.pente_deg', 35.0)))), 0.3)
    return Params(
        a_floor=a_floor, volume=a_floor * CEILING_H, walls=walls,
        roof_area=roof_area, u_roof=float(_v(values, 'toiture.u', 1.0)),
        floor_area=floor_area, u_floor=float(_v(values, 'plancher.u', 1.0)),
        g_window=float(_v(values, 'baies.g', 0.6)),
        absorptance=float(_v(values, 'enveloppe.couleur_absorptance', 0.6)),
        c_m=c_m, a_m_factor=a_m, n_vent=effective_ventilation(values),
        gains_w_m2=float(_v(values, 'usage.apports_w_m2', 4.0)),
        t_heat=float(_v(values, 'usage.consigne_chauffage_c', 19.0)),
        t_cool=float(t_cool) if t_cool is not None else None,
        residential=usage in RESIDENTIAL,
        horizon=(geometry or {}).get('horizon_deg') or {o: 0.0 for o in ORIENTATIONS},
        horizon_profile=(geometry or {}).get('horizon_profil') or None,
    )


# ── Depuis un bâtiment maillé (comparaison N1 / N2) ─────────────────────────

def u_from_layers(layers, h_i, h_e):
    r = 1.0 / h_i + 1.0 / h_e + sum(l['e'] / l['lam'] for l in layers)
    return 1.0 / r


def _tau(layers):
    t = 1.0
    for l in layers:
        t *= float(l.get('tau', 0.0))
    return t


def from_envelope(envelope, paroi_layers, *, h_i=7.7, h_e=25.0, r_ground=0.5, n_vent=0.5, gains_w=0.0,
                  t_heat=19.0, a_floor=None, c_m_per_m2=165_000.0, t_ground=GROUND_T_DEFAULT):
    """Paramètres N1 d'un bâtiment maillé (triangles avec `paroi_model_id`, `boundary`, normale)."""
    walls = {o: {'opaque': 0.0, 'window': 0.0, 'u_a': 0.0, 'uw_a': 0.0} for o in ORIENTATIONS}
    roof_ua = roof_a = floor_ua = floor_a = 0.0
    g_a = alpha_a = opaque_a = 0.0
    from ..bdnb import cardinal_of
    for t in envelope['triangles']:
        layers = paroi_layers[t['paroi_model_id']]
        area, tilt = t['area'], t['tilt_deg']
        tau = _tau(layers)
        if t.get('boundary') == 'ground':
            r = 1.0 / h_i + sum(l['e'] / l['lam'] for l in layers) + r_ground
            floor_ua += area / r
            floor_a += area
        elif tilt < 60:
            roof_ua += area * u_from_layers(layers, h_i, h_e)
            roof_a += area
            alpha_a += area * float(layers[0].get('alpha', 0.6))
            opaque_a += area
        else:
            o = cardinal_of(t['azimuth_deg'])
            u = u_from_layers(layers, h_i, h_e)
            if tau > 0:
                walls[o]['window'] += area
                walls[o]['uw_a'] += area * u
                g_a += area * tau
            else:
                walls[o]['opaque'] += area
                walls[o]['u_a'] += area * u
                alpha_a += area * float(layers[0].get('alpha', 0.6))
                opaque_a += area
    win_total = sum(w['window'] for w in walls.values())
    out = {o: {'opaque': w['opaque'], 'window': w['window'],
               'u': w['u_a'] / w['opaque'] if w['opaque'] else 0.0,
               'uw': w['uw_a'] / w['window'] if w['window'] else 0.0} for o, w in walls.items()}
    af = a_floor or floor_a or 100.0
    vol = af * CEILING_H
    return Params(
        a_floor=af, volume=vol, walls=out, roof_area=roof_a, u_roof=roof_ua / roof_a if roof_a else 0.0,
        floor_area=floor_a, u_floor=floor_ua / floor_a if floor_a else 0.0,
        g_window=g_a / win_total if win_total else 0.0, frame_factor=1.0, absorptance=alpha_a / opaque_a if opaque_a else 0.6,
        c_m=c_m_per_m2, n_vent=n_vent, gains_w_m2=gains_w / af, t_heat=t_heat, t_ground=t_ground,
    )
