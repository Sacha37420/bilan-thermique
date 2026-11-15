"""Aides communes aux tests du solveur et de l'environnement.

Découpé de l'ancien `api/tests.py` (Lot 5, refonte) — contenu des classes inchangé.
"""

import math
from unittest import mock
import numpy as np
from django.test import SimpleTestCase, TestCase
from .. import building_solver, elevation, geodata, geometry, serializers, shadow, solver, weather_source
from ..models import Building, Environment


DT_SECONDS = 3600.0


def _flat_wall_layer(e=0.1, lam=1.0, rho=1400.0, c=1000.0):
    return {'e': e, 'lam': lam, 'rho': rho, 'c': c, 'tau': 0.0, 'r': 0.9, 'alpha': 0.1}


def _no_sun_weather_1d(t_ext_series):
    return [{'t_ext': t, 'h_s': -10.0, 'theta_i': 0.0, 'e_dir': 0.0, 'e_dif': 0.0} for t in t_ext_series]


def _no_sun_weather_3d(t_ext_series):
    return [{'t_ext': t, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0}
            for t in t_ext_series]


def _single_triangle_envelope(area):
    """Un unique triangle rectangle isocèle d'aire `area`, normale +Z (tilt 0°)."""
    side = math.sqrt(2.0 * area)
    vertices = [[0.0, 0.0, 0.0], [side, 0.0, 0.0], [0.0, side, 0.0]]
    triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])
    return {'vertices': vertices, 'triangles': triangles}


def _angle_diff_deg(a, b):
    """Écart angulaire signé minimal a-b, dans [-180, 180] — évite les faux
    échecs 0 vs 360 quand on compare des azimuths modulo 360."""
    return (a - b + 180.0) % 360.0 - 180.0


def _wall_envelope(area):
    """Un unique triangle vertical (tilt_deg=90°, normale horizontale) — pour
    distinguer de _single_triangle_envelope (normale +Z, tilt=0°, toiture)."""
    side = math.sqrt(2.0 * area)
    vertices = [[0.0, 0.0, 0.0], [side, 0.0, 0.0], [0.0, 0.0, side]]
    triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])
    return {'vertices': vertices, 'triangles': triangles}


def _no_sun_weather_3d_wind(t_ext_series, wind_series):
    return [
        {'t_ext': t, 'sun_azimuth': 0.0, 'sun_elevation': -10.0, 'e_dir': 0.0, 'e_dif': 0.0, 'wind_m_s': w}
        for t, w in zip(t_ext_series, wind_series)
    ]


def _veg_box(x0, y0, x1, y1, z0, z1, obj, k, base_vertex=0):
    """Boîte translucide (végétation) — 8 sommets, 12 triangles portant k/obj."""
    v = [
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ]
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    t = []
    for a, b, c, d in quads:
        for tri in ((a, b, c), (a, c, d)):
            t.append({'v': [base_vertex + i for i in tri], 'k': k, 'obj': obj})
    return v, t


# ── Lot AH — environnement observé (LiDAR HD × BD TOPO) ──────────────────────
#
# Scène synthétique dont la vérité est connue par construction : les points
# sont générés depuis une géométrie décrite ici, jamais depuis le code testé.
# Le repère local est celui d'un LocalFrame réel (pyproj) ; les points et les
# emprises BD TOPO sont exprimés en Lambert 93, exactement comme le réseau les
# fournit.

SCENE_LAT, SCENE_LON = 47.3445, 0.6613
SCENE_GROUND_Z = 100.0
SCENE_SLOPE_X = 0.02                   # terrain : +2 cm par mètre vers l'est
BDTOPO_OFFSET = (1.5, -1.0)            # décalage volontaire des emprises BD TOPO

HOUSE = {'x0': -5.0, 'x1': 5.0, 'y0': -4.0, 'y1': 4.0, 'eave': 6.0, 'ridge': 9.0}   # faîtage selon x
FLAT = {'x0': 16.0, 'x1': 24.0, 'y0': 11.0, 'y1': 19.0, 'h': 12.0}
SHED = {'x0': -23.0, 'x1': -17.0, 'y0': -21.0, 'y1': -15.0, 'h': 4.0}            # absent de la BD TOPO
GONE = {'x0': 17.0, 'x1': 23.0, 'y0': -23.0, 'y1': -17.0}                         # démoli
TREE = {'x': -20.0, 'y': 20.0, 'r': 3.0, 'base': 3.0, 'top': 10.0}


def _scene_ground(x):
    return SCENE_GROUND_Z + SCENE_SLOPE_X * x


def _scene_points(half=40.0, density=16.0, seed=1):
    """Nuage (e, n, z, classe) Lambert 93 de la scène, avec des points de FAÇADE
    classés bâtiment, comme le fait réellement le LiDAR HD."""
    import numpy as np
    from .. import observed_env as oe

    rng = np.random.default_rng(seed)
    ext = half + oe.RASTER_MARGIN_M
    n = int((2 * ext) ** 2 * density)
    x = rng.uniform(-ext, ext, n)
    y = rng.uniform(-ext, ext, n)
    z = _scene_ground(x)
    cls = np.full(n, 2, dtype=np.uint8)

    def inside(b):
        return (x >= b['x0']) & (x <= b['x1']) & (y >= b['y0']) & (y <= b['y1'])

    h = inside(HOUSE)
    half_w = (HOUSE['y1'] - HOUSE['y0']) / 2.0
    cy = (HOUSE['y1'] + HOUSE['y0']) / 2.0
    z[h] = _scene_ground(0.0) + HOUSE['ridge'] - (HOUSE['ridge'] - HOUSE['eave']) * np.abs(y[h] - cy) / half_w
    cls[h] = 6
    f = inside(FLAT)
    z[f] = _scene_ground(20.0) + FLAT['h']
    cls[f] = 6
    s = inside(SHED)
    z[s] = _scene_ground(-20.0) + SHED['h']
    cls[s] = 6

    # Arbre : houppier conique, avec 25 % de trouées qui laissent voir le sol.
    d = np.hypot(x - TREE['x'], y - TREE['y'])
    t = d <= TREE['r']
    crown = t & (rng.uniform(size=n) > 0.25)
    frac = 1.0 - d[crown] / TREE['r']
    z[crown] = _scene_ground(TREE['x']) + TREE['base'] + (TREE['top'] - TREE['base']) * frac * rng.uniform(0.7, 1.0, crown.sum())
    cls[crown] = 5

    # Points de façade : sur les murs de la maison et de l'immeuble, à toute hauteur.
    fx, fy, fz = [], [], []
    for b, top in ((HOUSE, HOUSE['eave']), (FLAT, FLAT['h'])):
        for _ in range(400):
            side = rng.integers(4)
            u = rng.uniform()
            px = b['x0'] + u * (b['x1'] - b['x0']) if side < 2 else (b['x0'] if side == 2 else b['x1'])
            py = (b['y0'] if side == 0 else b['y1']) if side < 2 else b['y0'] + u * (b['y1'] - b['y0'])
            fx.append(px + rng.normal(0, 0.05))
            fy.append(py + rng.normal(0, 0.05))
            fz.append(_scene_ground(px) + rng.uniform(0.5, top))
    x = np.concatenate([x, fx])
    y = np.concatenate([y, fy])
    z = np.concatenate([z, fz])
    cls = np.concatenate([cls, np.full(len(fx), 6, dtype=np.uint8)])

    frame = oe.LocalFrame(SCENE_LAT, SCENE_LON)
    e, nn = frame.to_l93(x, y)
    return frame, (e, nn, z, cls)


def _scene_bdtopo(frame):
    dx, dy = BDTOPO_OFFSET
    out = []
    for key, b in (('A', HOUSE), ('B', FLAT), ('D', GONE)):
        ring = [(b['x0'] + dx, b['y0'] + dy), (b['x1'] + dx, b['y0'] + dy),
                (b['x1'] + dx, b['y1'] + dy), (b['x0'] + dx, b['y1'] + dy), (b['x0'] + dx, b['y0'] + dy)]
        e, n = frame.to_l93([p[0] for p in ring], [p[1] for p in ring])
        out.append({'id': key, 'rings': [list(zip(e.tolist(), n.tolist()))],
                    'hauteur': 7.0, 'etages': None, 'z_sol': None, 'z_toit_max': None,
                    'nature': 'Indifférenciée', 'usage': 'Résidentiel'})
    return out


def _closed_and_outward(obj):
    import trimesh
    m = trimesh.Trimesh(obj['vertices'], [t['v'] for t in obj['triangles']], process=False)
    return m.is_watertight and m.is_winding_consistent and m.volume > 0


def _terrain_env(half=60.0, obj=7):
    return {'vertices': [[-half, -half, 0.0], [half, -half, 0.0], [half, half, 0.0], [-half, half, 0.0]],
            'triangles': [{'v': [0, 1, 2], 'obj': obj}, {'v': [0, 2, 3], 'obj': obj}]}


def _vertical_wall_envelope(z0=0.0):
    """Triangle vertical, normale −Y (face au sud du repère), pied à z0."""
    vertices = [[0.0, 0.0, z0], [4.0, 0.0, z0 + 0.001], [0.0, 0.0, z0 + 4.0]]
    triangles = geometry.compute_envelope_geometry(vertices, [{'v': [0, 1, 2], 'paroi_model_id': 1}])
    return {'vertices': vertices, 'triangles': triangles}


# ── Lot AK — façades : projection, vitrages réels ─────────────────────────────

def _box_building(w=10.0, d=8.0, h=6.0):
    import shapely.geometry as sg
    from .. import observed_env as oe
    v, t = oe.prism_solid(sg.box(0, 0, w, d), 0.0, h)
    return v, t
