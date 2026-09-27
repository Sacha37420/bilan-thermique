"""Environnement « observé » (Lot AH) — reconstruction automatique des obstacles
solaires à partir du LiDAR HD IGN croisé avec la BD TOPO.

Pourquoi remplacer l'extrusion BD TOPO seule (geodata.generate_environment_mesh) :
- la hauteur BD TOPO est un attribut unique par bâtiment (souvent estimé depuis
  le nombre d'étages) et le toit est plat : un pignon, une toiture à deux pans
  ou un bâtiment à plusieurs hauteurs sont faux par construction ;
- l'empreinte BD TOPO peut être décalée de plusieurs mètres par rapport au
  terrain réel (digitalisations anciennes), et rien ne le détecte ;
- la végétation n'avait AUCUNE hauteur fiable (table par type) ni position
  individuelle (sauf arbres OSM, rares) ;
- le terrain venait d'une grille régulière d'altitudes interrogées point par
  point.

Le LiDAR HD donne tout cela directement, à ~18 points/m², classifié. La BD TOPO
reste la source des EMPRISES (murs droits, découpage en bâtiments individuels,
identifiants) : le LiDAR sert à les recaler, à dire si elles existent encore,
à trouver ce qui manque, et à construire toitures, arbres et terrain.

Module pur (aucun appel réseau, aucun modèle Django) : il prend des points
Lambert 93 et des emprises Lambert 93, et rend des objets dans le repère local
du bâtiment (Z-up, mètres, +Y = nord vrai tourné de north_offset_deg — même
convention que geometry.py / geodata._rotate_xy). Le réseau vit dans
api.lidar_source.

Chaque OBJET d'environnement est autonome : {id, kind, status, origin, label,
reason, info, footprint, vertices, triangles, k}. `Environment.envelope` (seul
format lu par api.shadow) est reconstruit à partir des objets actifs par
compose_envelope — c'est ce qui permet de retirer, restaurer ou promouvoir
un objet en « bâtiment étudié » sans régénérer quoi que ce soit.
"""

import math

import numpy as np
import shapely
import shapely.affinity
import shapely.geometry as sg
import shapely.ops
from scipy import ndimage
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry.polygon import orient

CELL_M = 0.5            # rasters bâtiment/végétation : 4 à 5 points par maille
DTM_CELL_M = 1.0        # terrain : lissé, plus robuste sous les bâtiments
MIN_BUILDING_HEIGHT_M = 1.5
MIN_TREE_HEIGHT_M = 2.5

GLOBAL_SHIFT_MAX_M = 6.0
LOCAL_SHIFT_MAX_M = 2.0

STATUS_ACTIVE = 'active'
STATUS_REMOVED = 'removed'
STATUS_STUDIED = 'studied'

# Marge des rasters au-delà de la zone demandée : un bâtiment à cheval sur la
# limite doit être vu EN ENTIER, sinon la partie hors raster n'a pas de toit
# (constaté : une maison à deux pans reconstruite plate à mi-hauteur).
RASTER_MARGIN_M = 30.0

MAX_ENV_TRIANGLES = 60_000
MAX_VEGETATION_OBJECTS = 400

# Transmittance du couvert quand le LiDAR a été acquis feuilles tombées : la
# fraction de trouée mesurée vaut alors pour l'hiver, pas pour l'été. On garde
# la mesure (k_bare) et on applique la valeur « en feuilles » par défaut déjà
# utilisée par geodata (DEFAULT_VEGETATION) — voir tree_objects.
DEFAULT_K_LEAF = 0.20
LEAF_ON_MONTHS = {5, 6, 7, 8, 9, 10}


class ObservedEnvError(ValueError):
    pass


# ── Repère local ───────────────────────────────────────────────────────────────

class LocalFrame:
    """Lambert 93 ↔ repère local d'un bâtiment.

    Deux corrections que l'équirectangulaire de geodata.local_xy ignorait parce
    qu'il partait du WGS84 — ici on part du Lambert 93, où elles ne sont plus
    négligeables :
    - **convergence des méridiens** : le nord du quadrillage Lambert 93 n'est
      le nord vrai que sur le méridien 3° E. À Tours (0,7° E) l'écart est de
      1,7° ; en Bretagne (−4,5° E) il dépasse 5°. Sans correction, toutes les
      façades seraient mal orientées d'autant, donc tous les azimuts solaires ;
    - **facteur d'échelle** de la projection (±0,1 %) : 25 cm sur 250 m.

    Les deux sont mesurés numériquement (pyproj + géodésique) au point
    d'origine, plutôt que recalculés de mémoire par formule — cf. le piège de
    signe déjà rencontré sur une formule astronomique écrite de mémoire."""

    def __init__(self, lat0, lon0, north_offset_deg=0.0, ground_z=None):
        from pyproj import Geod, Transformer

        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self.north_offset_deg = float(north_offset_deg or 0.0)
        self.ground_z = ground_z
        self._fwd = Transformer.from_crs('EPSG:4326', 'EPSG:2154', always_xy=True)
        self._inv = Transformer.from_crs('EPSG:2154', 'EPSG:4326', always_xy=True)
        self.e0, self.n0 = self._fwd.transform(self.lon0, self.lat0)

        step = 1e-3
        e1, n1 = self._fwd.transform(self.lon0, self.lat0 + step)
        # Angle (sens horaire) du nord vrai mesuré depuis le nord du quadrillage.
        self.convergence_rad = math.atan2(e1 - self.e0, n1 - self.n0)
        grid_dist = math.hypot(e1 - self.e0, n1 - self.n0)
        _az, _baz, true_dist = Geod(ellps='GRS80').inv(self.lon0, self.lat0, self.lon0, self.lat0 + step)
        self.scale = grid_dist / true_dist
        # Rotation trigonométrique appliquée à un vecteur du quadrillage : elle
        # amène le nord vrai sur +Y (convergence), puis applique le cap du
        # bâtiment exactement comme geodata._rotate_xy.
        self.rotation_rad = self.convergence_rad + math.radians(self.north_offset_deg)
        self._cos = math.cos(self.rotation_rad)
        self._sin = math.sin(self.rotation_rad)

    def to_local(self, e, n):
        dx = (np.asarray(e, dtype=float) - self.e0) / self.scale
        dy = (np.asarray(n, dtype=float) - self.n0) / self.scale
        return dx * self._cos - dy * self._sin, dx * self._sin + dy * self._cos

    def to_l93(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        dx = x * self._cos + y * self._sin
        dy = -x * self._sin + y * self._cos
        return self.e0 + dx * self.scale, self.n0 + dy * self.scale

    def latlon(self, x, y):
        e, n = self.to_l93(x, y)
        lon, lat = self._inv.transform(e, n)
        return lat, lon

    def l93_bbox(self, half_m):
        """Emprise Lambert 93 (xmin, ymin, xmax, ymax) couvrant le carré local
        [-half, half]² — tourné, donc on prend l'enveloppe de ses coins."""
        corners = np.array([[-half_m, -half_m], [half_m, -half_m], [half_m, half_m], [-half_m, half_m]])
        e, n = self.to_l93(corners[:, 0], corners[:, 1])
        return float(e.min()), float(n.min()), float(e.max()), float(n.max())

    def wgs84_bbox(self, half_m):
        corners = np.array([[-half_m, -half_m], [half_m, -half_m], [half_m, half_m], [-half_m, half_m]])
        lat, lon = self.latlon(corners[:, 0], corners[:, 1])
        return float(np.min(lat)), float(np.min(lon)), float(np.max(lat)), float(np.max(lon))


# ── Grilles ────────────────────────────────────────────────────────────────────

class Grid:
    """Grille carrée centrée sur l'origine locale, indexée [j, i] (j = y)."""

    def __init__(self, half_m, cell_m):
        self.half = float(half_m)
        self.cell = float(cell_m)
        self.n = int(math.ceil(2.0 * self.half / self.cell))
        self.coords = -self.half + (np.arange(self.n) + 0.5) * self.cell

    def index(self, x, y):
        i = np.floor((np.asarray(x) + self.half) / self.cell).astype(np.int64)
        j = np.floor((np.asarray(y) + self.half) / self.cell).astype(np.int64)
        valid = (i >= 0) & (i < self.n) & (j >= 0) & (j < self.n)
        return i, j, valid

    def centers(self):
        return np.meshgrid(self.coords, self.coords)

    def window(self, bounds, margin_cells=0):
        """Indices (i0, i1, j0, j1) — bornes exclusives — d'une fenêtre couvrant
        bounds=(xmin, ymin, xmax, ymax), écrêtée à la grille."""
        xmin, ymin, xmax, ymax = bounds
        i0 = int(math.floor((xmin + self.half) / self.cell)) - margin_cells
        i1 = int(math.ceil((xmax + self.half) / self.cell)) + margin_cells
        j0 = int(math.floor((ymin + self.half) / self.cell)) - margin_cells
        j1 = int(math.ceil((ymax + self.half) / self.cell)) + margin_cells
        return max(i0, 0), min(i1, self.n), max(j0, 0), min(j1, self.n)

    def sample(self, raster, x, y, order=1):
        """Échantillonnage bilinéaire d'un raster de cette grille en des points
        locaux arbitraires (hors grille : valeur la plus proche du bord)."""
        fi = (np.asarray(x, dtype=float) + self.half) / self.cell - 0.5
        fj = (np.asarray(y, dtype=float) + self.half) / self.cell - 0.5
        return ndimage.map_coordinates(raster, [np.atleast_1d(fj), np.atleast_1d(fi)],
                                       order=order, mode='nearest')


def _reduce_max(grid, x, y, values):
    out = np.full((grid.n, grid.n), np.nan)
    if len(x) == 0:
        return out
    i, j, valid = grid.index(x, y)
    flat = j[valid] * grid.n + i[valid]
    buf = np.full(grid.n * grid.n, -np.inf)
    np.maximum.at(buf, flat, values[valid])
    buf[np.isinf(buf)] = np.nan
    return buf.reshape(grid.n, grid.n)


def _reduce_min(grid, x, y, values):
    return -_reduce_max(grid, x, y, -np.asarray(values))


def _count(grid, x, y):
    i, j, valid = grid.index(x, y)
    return np.bincount(j[valid] * grid.n + i[valid], minlength=grid.n * grid.n).reshape(grid.n, grid.n)


def _nan_median3(raster):
    """Médiane 3×3 qui ignore les NaN — enlève cheminées, antennes et points
    isolés d'un raster de toiture sans combler les trous."""
    padded = np.pad(raster, 1, mode='constant', constant_values=np.nan)
    stack = np.stack([
        padded[dj:dj + raster.shape[0], di:di + raster.shape[1]]
        for dj in range(3) for di in range(3)
    ])
    with np.errstate(all='ignore'):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', RuntimeWarning)
            med = np.nanmedian(stack, axis=0)
    med[np.isnan(raster)] = np.nan
    return med


def _drop_facade_cells(roof, drop_m=1.5, passes=2):
    """Écarte les mailles de BORD de toit qui ne contiennent que des points de
    façade : le LiDAR classe « bâtiment » aussi les murs, et une maille à
    cheval sur la façade prend alors pour maximum un point de mur, plusieurs
    mètres sous l'égout. Constaté sur données réelles : ces mailles tiraient
    l'égout vers le bas et transformaient un toit plat en pyramide.

    Critère : maille voisine du vide ET plus basse de drop_m que la plus haute
    de ses voisines. Un égout de toit en pente n'est plus bas que sa voisine
    intérieure que de pente × 0,5 m — bien moins que 1,5 m jusqu'à 70°. Un
    ressaut INTÉRIEUR (terrasse plus basse) n'est pas au bord du vide : gardé."""
    out = roof.copy()
    for _ in range(passes):
        missing = np.isnan(out)
        border = ndimage.binary_dilation(missing, structure=np.ones((3, 3))) & ~missing
        neighbour_max = ndimage.maximum_filter(np.where(missing, -np.inf, out), size=3)
        drop = border & (neighbour_max - out > drop_m)
        if not drop.any():
            break
        out[drop] = np.nan
    return out


ROOF_OPENING_CELLS = 5        # 2,5 m
MAX_PROTRUSION_CELLS = 16     # 4 m²
MAX_PROTRUSION_SPAN = 6       # 3 m


def _roof_opening(roof):
    """Retire les petites saillies de toit (cheminées, antennes, lucarnes
    étroites) sans toucher aux faîtages.

    Une ouverture morphologique appliquée telle quelle (première version)
    écrêtait AUSSI chaque faîtage de pente × 1,25 m — constaté : une maison à
    deux pans perdait 2 m de hauteur. On ne garde donc de l'ouverture que ce
    qu'elle retire sous forme de TACHES COMPACTES et petites (≤ 4 m², ≤ 3 m de
    côté) : un faîtage écrêté est une bande longue de tout le toit, donc
    jamais concernée ; une cheminée, si.

    Pourquoi retirer ces saillies : une seule cheminée insérée comme sommet
    du TIN tire des triangles inclinés jusqu'aux bords du toit, et consomme le
    budget de points à corriger sa propre déformation. Pour un masque
    solaire, un objet de cette taille sur un toit ne compte pas."""
    missing = np.isnan(roof)
    if missing.all():
        return roof
    filled = np.where(missing, -1e4, roof)
    opened = ndimage.grey_opening(filled, size=(ROOF_OPENING_CELLS, ROOF_OPENING_CELLS))
    protrusion = ~missing & (opened > -1e3) & (roof - opened > 0.6)
    labels, n = ndimage.label(protrusion)
    out = roof.copy()
    for k, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        cells = labels[sl] == k
        span_j = sl[0].stop - sl[0].start
        span_i = sl[1].stop - sl[1].start
        if cells.sum() <= MAX_PROTRUSION_CELLS and max(span_i, span_j) <= MAX_PROTRUSION_SPAN:
            out[sl][cells] = opened[sl][cells]
    return out


# ── Rasters de base ────────────────────────────────────────────────────────────

class Rasters:
    """Tout ce qu'on tire du nuage, dans le repère local :
    dtm (1 m, altitude relative, sans trou), dtm_fine (0,5 m, idem),
    roof (0,5 m, altitude max des points bâtiment, NaN sinon),
    building_mask (0,5 m, booléen nettoyé),
    veg_top / veg_low (1 m, altitude max / min de la végétation haute),
    above (0,5 m, booléen : un sursol quelconque dépasse 2 m),
    n_ground / n_total (1 m, comptes de points — fraction de trouée)."""


def build_rasters(x, y, z, cls, half_m, ground_z=None):
    """x, y : déjà dans le repère local ; z : altitude absolue (IGN69).
    Retourne (rasters, ground_z) — ground_z = altitude du terrain à l'origine
    si non imposé, qui devient le z = 0 de tout le repère."""
    from .lidar_source import (CLASS_BUILDING, CLASS_GROUND, CLASS_VEG_HIGH,
                               CLASS_VEG_LOW, CLASS_VEG_MID)

    r = Rasters()
    g1 = Grid(half_m, DTM_CELL_M)
    g05 = Grid(half_m, CELL_M)
    r.grid, r.grid_fine = g1, g05

    ground = cls == CLASS_GROUND
    if ground.sum() < 50:
        raise ObservedEnvError("Pas assez de points « sol » dans le nuage LiDAR pour cette zone.")

    # Terrain : moyenne des points sol par maille de 1 m, puis interpolation
    # linéaire des trous (sous les bâtiments, sous les arbres denses) sur les
    # mailles renseignées, et plus proche voisin hors de leur enveloppe convexe.
    counts = _count(g1, x[ground], y[ground])
    i, j, valid = g1.index(x[ground], y[ground])
    sums = np.bincount(j[valid] * g1.n + i[valid], weights=z[ground][valid],
                       minlength=g1.n * g1.n).reshape(g1.n, g1.n)
    with np.errstate(invalid='ignore', divide='ignore'):
        dtm = sums / counts
    known = counts > 0
    if not known.all():
        X, Y = g1.centers()
        pts = np.column_stack([X[known], Y[known]])
        vals = dtm[known]
        # Sous-échantillonnage pour la triangulation : 1 maille sur 4 suffit à
        # interpoler des trous, et divise le temps de Delaunay d'autant.
        sub = slice(None, None, 4) if len(vals) > 40_000 else slice(None)
        lin = LinearNDInterpolator(pts[sub], vals[sub])
        missing = ~known
        filled = lin(X[missing], Y[missing])
        nan = np.isnan(filled)
        if nan.any():
            nearest = NearestNDInterpolator(pts[sub], vals[sub])
            filled[nan] = nearest(X[missing][nan], Y[missing][nan])
        dtm[missing] = filled
    # Légère régularisation : le bruit de mesure du sol (~5 cm) est sans intérêt
    # pour un masque solaire et gonflerait le maillage du terrain.
    dtm = ndimage.gaussian_filter(dtm, sigma=1.0, mode='nearest')

    if ground_z is None:
        ground_z = float(g1.sample(dtm, [0.0], [0.0])[0])
    r.dtm = dtm - ground_z
    Xf, Yf = g05.centers()
    r.dtm_fine = g1.sample(r.dtm, Xf.ravel(), Yf.ravel()).reshape(g05.n, g05.n)

    zr = z - ground_z

    bld = cls == CLASS_BUILDING
    roof = _reduce_max(g05, x[bld], y[bld], zr[bld])
    with np.errstate(invalid='ignore'):
        raw = ~np.isnan(roof) & (roof - r.dtm_fine > MIN_BUILDING_HEIGHT_M)
    # Nettoyage morphologique : fermer les trous d'une maille (fenêtres de
    # toit, zones sombres peu échantillonnées), puis retirer les points isolés.
    mask = ndimage.binary_closing(raw, structure=np.ones((3, 3)), iterations=1)
    mask = ndimage.binary_fill_holes(mask)
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    r.building_mask = mask
    r.roof = _roof_opening(_nan_median3(_drop_facade_cells(np.where(raw, roof, np.nan))))

    veg = cls == CLASS_VEG_HIGH
    r.veg_top = _reduce_max(g1, x[veg], y[veg], zr[veg])
    r.veg_low = _reduce_min(g1, x[veg], y[veg], zr[veg])

    above_classes = ~np.isin(cls, (CLASS_GROUND, CLASS_VEG_LOW, CLASS_VEG_MID))
    above = _reduce_max(g05, x[above_classes], y[above_classes], zr[above_classes])
    with np.errstate(invalid='ignore'):
        r.above = ~np.isnan(above) & (above - r.dtm_fine > 2.0)

    r.n_ground = _count(g1, x[ground], y[ground])
    r.n_total = _count(g1, x, y)
    from .lidar_source import CLASS_WATER
    water = cls == CLASS_WATER
    r.n_water = _count(g1, x[water], y[water])
    return r, float(ground_z)


# ── Rasterisation d'emprises ───────────────────────────────────────────────────

def polygon_mask(poly, grid, window=None):
    """Masque booléen (fenêtre ou grille entière) des mailles dont le centre
    est dans le polygone. Retourne (mask, (i0, i1, j0, j1))."""
    if window is None:
        window = grid.window(poly.bounds, margin_cells=1)
    i0, i1, j0, j1 = window
    if i1 <= i0 or j1 <= j0:
        return np.zeros((0, 0), dtype=bool), window
    X, Y = np.meshgrid(grid.coords[i0:i1], grid.coords[j0:j1])
    return shapely.contains_xy(poly, X, Y), window


def _shift(mask, dj, di):
    """Décale un masque de (dj, di) mailles, en remplissant de False."""
    out = np.zeros_like(mask)
    h, w = mask.shape
    src_j = slice(max(0, -dj), min(h, h - dj))
    dst_j = slice(max(0, dj), min(h, h + dj))
    src_i = slice(max(0, -di), min(w, w - di))
    dst_i = slice(max(0, di), min(w, w + di))
    out[dst_j, dst_i] = mask[src_j, src_i]
    return out


def _subcell(scores, center):
    """Raffinement sous-maille d'un maximum par parabole sur les voisins."""
    c = scores[center]
    left, right = scores.get(center - 1), scores.get(center + 1)
    if left is None or right is None:
        return 0.0
    denom = left - 2.0 * c + right
    if denom >= 0:
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (left - right) / denom))


def depth_weights(lidar_mask, cap_cells, outside_penalty=-1.0):
    """Poids de recalage : profondeur (en mailles, plafonnée) de chaque maille à
    l'intérieur d'un toit LiDAR, et une pénalité hors toit.

    Pourquoi pas simplement le recouvrement emprise ∩ toit : une emprise
    entièrement contenue dans son toit (débord de toiture, ou décalage d'un
    seul côté) a déjà un recouvrement maximal — AUCUN décalage ne l'améliore,
    et une bande de toit de 1 à 2 m reste à découvert d'un côté (constaté sur
    données réelles, 2026-09-27). Pondérer par la profondeur récompense au
    contraire l'emprise CENTRÉE dans son toit, ce qui répartit le débord des
    deux côtés, comme il l'est en réalité."""
    depth = ndimage.distance_transform_edt(lidar_mask)
    return np.where(lidar_mask, np.minimum(depth, cap_cells), outside_penalty)


def best_offset(footprint_mask, weights, max_lag_cells, min_gain=0.02):
    """Décalage (dx, dy) en MAILLES qui maximise la corrélation entre les
    emprises rasterisées et les poids de recalage (depth_weights). Tous les
    décalages sont évalués d'un coup par FFT, puis raffinés à la sous-maille
    par parabole.

    Retourne (dx, dy, score_best, score_zero). Un gain inférieur à min_gain
    (relatif) ramène le décalage à zéro : sans signal net, on ne déplace rien."""
    from scipy.signal import fftconvolve

    if not footprint_mask.any():
        return 0.0, 0.0, 0.0, 0.0
    h, w = footprint_mask.shape
    m = max_lag_cells
    # corr[dj, di] = Σ F[j - dj, i - di] · W[j, i]  (F décalé de (dj, di)).
    full = fftconvolve(weights, footprint_mask[::-1, ::-1].astype(float), mode='full')
    cj, ci = h - 1, w - 1
    corr = full[cj - m:cj + m + 1, ci - m:ci + m + 1]
    scores = {(dj - m, di - m): float(corr[dj, di]) for dj in range(2 * m + 1) for di in range(2 * m + 1)}
    (bj, bi), best = max(scores.items(), key=lambda kv: (round(kv[1], 6), -abs(kv[0][0]) - abs(kv[0][1])))
    zero = scores[(0, 0)]
    if best <= zero + max(abs(zero) * min_gain, 1.0):
        return 0.0, 0.0, zero, zero
    row = {k[1]: v for k, v in scores.items() if k[0] == bj}
    col = {k[0]: v for k, v in scores.items() if k[1] == bi}
    fx = bi + _subcell(row, bi)
    fy = bj + _subcell(col, bj)
    return float(fx), float(fy), best, zero


# ── Recalage des emprises BD TOPO ──────────────────────────────────────────────

def to_local_polygon(rings_l93, frame):
    """Anneaux Lambert 93 → polygone shapely valide dans le repère local."""
    rings = []
    for ring in rings_l93:
        arr = np.asarray(ring, dtype=float)
        x, y = frame.to_local(arr[:, 0], arr[:, 1])
        rings.append(list(zip(x.tolist(), y.tolist())))
    poly = sg.Polygon(rings[0], rings[1:])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.geom_type == 'MultiPolygon':
        poly = max(poly.geoms, key=lambda g: g.area)
    return poly


def register_footprints(footprints, rasters):
    """Recale les emprises BD TOPO sur le LiDAR, en deux temps.

    1. **Décalage global** : une translation unique pour toute la zone,
       celle qui maximise le recouvrement entre TOUTES les emprises et le
       masque bâtiment. C'est le cas le plus fréquent (emprises issues d'une
       même digitalisation, décalées d'un bloc) et c'est le plus robuste :
       des centaines de bâtiments votent.
    2. **Ajustement individuel**, borné à ±2 m autour du global, accepté
       seulement s'il améliore nettement le recouvrement. Les emprises
       voisines sont retirées du masque LiDAR le temps du calcul, pour
       qu'un bâtiment ne « glisse » pas sur son mitoyen.

    footprints : [{'poly': Polygon local, ...}] — complétés en place avec
    'poly' recalé, 'shift' (dx, dy global+local, m), 'cover_before' et
    'cover' (part de l'emprise où le LiDAR voit un toit).
    Retourne le décalage global (dx, dy) en mètres."""
    grid = rasters.grid_fine
    L = rasters.building_mask
    cell = grid.cell

    all_mask = np.zeros_like(L)
    masks = []
    for fp in footprints:
        m, (i0, i1, j0, j1) = polygon_mask(fp['poly'], grid)
        masks.append((m, (i0, i1, j0, j1)))
        if m.size:
            all_mask[j0:j1, i0:i1] |= m

    lag = int(round(GLOBAL_SHIFT_MAX_M / cell))
    cap = int(round(3.0 / cell))
    gdx, gdy, _best, _zero = best_offset(all_mask, depth_weights(L, cap), lag, min_gain=0.01)
    global_shift = (gdx * cell, gdy * cell)

    # Masque de toutes les emprises APRÈS décalage global, pour exclure les
    # voisins pendant l'ajustement individuel.
    shifted_all = _shift(all_mask, int(round(gdy)), int(round(gdx)))

    local_lag = int(round(LOCAL_SHIFT_MAX_M / cell))
    # Emprises mitoyennes : en îlot continu, les toits se touchent et le LiDAR ne
    # sépare pas un bâtiment de son voisin — l'ajustement individuel n'a plus de
    # signal et déplaçait des emprises de 2 m vers le voisin le plus haut
    # (constaté en centre ancien). Elles gardent le seul décalage global.
    polys = [fp['poly'] for fp in footprints]
    tree = shapely.STRtree(polys)
    touching = set()
    for idx, poly in enumerate(polys):
        for other in tree.query(poly.buffer(0.3)):
            if other != idx and polys[other].distance(poly) < 0.3:
                touching.add(idx)
                break
    for f_index, (fp, (m, _win)) in enumerate(zip(footprints, masks)):
        poly0 = fp['poly']
        area_cells = max(int(m.sum()), 1)
        cover_before = _cover(poly0, grid, L)
        base = shapely.affinity.translate(poly0, global_shift[0], global_shift[1])
        margin = local_lag + 2
        m_b, win = polygon_mask(base, grid, grid.window(base.bounds, margin_cells=margin))
        i0, i1, j0, j1 = win
        if m_b.size == 0:
            fp.update(shift=list(global_shift), cover_before=cover_before, cover=0.0)
            fp['poly'] = base
            continue
        others = shifted_all[j0:j1, i0:i1] & ~m_b
        L_eff = L[j0:j1, i0:i1] & ~others
        ldx, ldy, _b, _z = best_offset(m_b, depth_weights(L_eff, cap), local_lag, min_gain=0.03)
        # Un tout petit bâtiment (abri, garage) n'a pas assez de mailles pour
        # départager des décalages : on garde le global. Idem pour un mitoyen.
        if area_cells < 40 or f_index in touching:
            ldx = ldy = 0.0
        final = shapely.affinity.translate(base, ldx * cell, ldy * cell)
        fp['poly'] = final
        fp['shift'] = [round(global_shift[0] + ldx * cell, 2), round(global_shift[1] + ldy * cell, 2)]
        fp['cover_before'] = cover_before
        fp['cover'] = _cover(final, grid, L)
    return global_shift


def _cover(poly, grid, lidar_mask):
    m, (i0, i1, j0, j1) = polygon_mask(poly, grid)
    n = int(m.sum())
    if n == 0:
        return 0.0
    return round(float(np.count_nonzero(m & lidar_mask[j0:j1, i0:i1])) / n, 3)


def _point_coverage(poly, rasters):
    """Part des mailles de 1 m de l'emprise où le nuage contient au moins un
    point, toutes classes confondues."""
    m, (i0, i1, j0, j1) = polygon_mask(poly, rasters.grid)
    n = int(m.sum())
    if n == 0:
        return 0.0
    return float(np.count_nonzero(m & (rasters.n_total[j0:j1, i0:i1] > 0))) / n


def _above_fraction(poly, rasters):
    m, (i0, i1, j0, j1) = polygon_mask(poly, rasters.grid_fine)
    n = int(m.sum())
    if n == 0:
        return 0.0
    return float(np.count_nonzero(m & rasters.above[j0:j1, i0:i1])) / n


# ── Bâtiments vus par le LiDAR seul ────────────────────────────────────────────

def lidar_only_buildings(rasters, registered_polys, min_area_m2=12.0):
    """Bâtiments présents dans le LiDAR mais absents de la BD TOPO
    (constructions récentes, annexes non digitalisées) : composantes connexes
    du masque bâtiment qui ne touchent aucune emprise recalée (dilatée de
    1 m). Polygonisées maille par maille puis simplifiées à 0,5 m — le
    contour est alors en escalier atténué, pas en murs droits : c'est la
    limite assumée d'une emprise qu'aucune source vectorielle ne fournit."""
    grid = rasters.grid_fine
    covered = np.zeros_like(rasters.building_mask)
    for poly in registered_polys:
        m, (i0, i1, j0, j1) = polygon_mask(poly.buffer(1.5), grid)
        if m.size:
            covered[j0:j1, i0:i1] |= m
    free = rasters.building_mask & ~covered
    # Ouverture à 2,5 m : un reste de toit mince (débord de toiture, bande
    # laissée par un léger décalage d'emprise) n'est pas un bâtiment. Constaté
    # sur données réelles : sans ce filtre, la majorité des « bâtiments LiDAR
    # seul » étaient ces bandes le long d'emprises BD TOPO.
    free = ndimage.binary_opening(free, structure=np.ones((5, 5)))
    labels, n = ndimage.label(free)
    if n == 0:
        return []
    min_cells = int(min_area_m2 / (grid.cell ** 2))
    sizes = ndimage.sum(np.ones_like(labels), labels, index=np.arange(1, n + 1))
    polys = []
    slices = ndimage.find_objects(labels)
    for k, (size, sl) in enumerate(zip(sizes, slices), start=1):
        if size < min_cells or sl is None:
            continue
        sub = labels[sl] == k
        poly = _cells_to_polygon(sub, sl, grid)
        if poly is None or poly.area < min_area_m2:
            continue
        polys.append(poly)
    return polys


def _cells_to_polygon(sub, sl, grid):
    boxes = []
    j0, i0 = sl[0].start, sl[1].start
    for jj in range(sub.shape[0]):
        row = sub[jj]
        if not row.any():
            continue
        # Plages contiguës d'une ligne → un rectangle par plage.
        diff = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        starts, ends = np.nonzero(diff == 1)[0], np.nonzero(diff == -1)[0]
        y0 = -grid.half + (j0 + jj) * grid.cell
        for s, e in zip(starts, ends):
            x0 = -grid.half + (i0 + s) * grid.cell
            boxes.append(sg.box(x0, y0, -grid.half + (i0 + e) * grid.cell, y0 + grid.cell))
    if not boxes:
        return None
    merged = shapely.ops.unary_union(boxes)
    if merged.geom_type == 'MultiPolygon':
        merged = max(merged.geoms, key=lambda g: g.area)
    merged = sg.Polygon(merged.exterior)  # trous d'une maille : artefacts
    simplified = merged.simplify(0.5, preserve_topology=True)
    if simplified.is_empty or not simplified.is_valid:
        return merged
    # Un contour presque rectangulaire (cas de loin le plus courant) est
    # remplacé par son rectangle orienté : 4 murs droits au lieu d'un escalier.
    rect = simplified.minimum_rotated_rectangle
    if rect.area > 0 and simplified.area / rect.area > 0.85:
        return rect
    return simplified


# ── Maillage d'un bâtiment : murs verticaux + toiture TIN + plancher ───────────

def _ring_coords(ring):
    coords = list(ring.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return coords


def _triangulate_polygon(ring_pts, interior_pts=()):
    """Triangulation contrainte (bibliothèque `triangle`, Shewchuk) d'un
    polygone à trous, avec des points intérieurs imposés. ring_pts : liste
    d'anneaux [(x, y), ...] (extérieur puis trous), dans l'ordre de parcours.
    Retourne (vertices (N, 2), triangles (M, 3)) — les N premiers sommets sont
    exactement ceux fournis, dans l'ordre (option 'p' sans 'q' : aucun point
    de Steiner n'est ajouté, donc les murs restent raccordés au toit)."""
    import triangle as tr

    verts, segs, holes = [], [], []
    for r_index, ring in enumerate(ring_pts):
        start = len(verts)
        verts.extend(ring)
        n = len(ring)
        segs.extend([start + k, start + (k + 1) % n] for k in range(n))
        if r_index > 0:
            hole_poly = sg.Polygon(ring)
            if hole_poly.is_valid and hole_poly.area > 0:
                holes.append(hole_poly.representative_point().coords[0])
    verts.extend(interior_pts)
    data = {'vertices': np.asarray(verts, dtype=float), 'segments': np.asarray(segs, dtype=np.int32)}
    if holes:
        data['holes'] = np.asarray(holes, dtype=float)
    out = tr.triangulate(data, 'p')
    if 'triangles' not in out or len(out['vertices']) != len(verts):
        raise ObservedEnvError("Triangulation de l'emprise impossible (anneaux qui se recoupent ?).")
    return np.asarray(out['vertices']), np.asarray(out['triangles'], dtype=np.int64)


def _interp_on_triangles(verts2d, zs, tris, qx, qy):
    """Valeur interpolée (barycentrique) en chaque point de requête ; NaN hors
    de tout triangle."""
    out = np.full(len(qx), np.nan)
    for a, b, c in tris:
        ax, ay = verts2d[a]
        bx, by = verts2d[b]
        cx, cy = verts2d[c]
        xmin, xmax = min(ax, bx, cx) - 1e-6, max(ax, bx, cx) + 1e-6
        ymin, ymax = min(ay, by, cy) - 1e-6, max(ay, by, cy) + 1e-6
        sel = np.nonzero((qx >= xmin) & (qx <= xmax) & (qy >= ymin) & (qy <= ymax) & np.isnan(out))[0]
        if len(sel) == 0:
            continue
        det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(det) < 1e-12:
            continue
        px, py = qx[sel], qy[sel]
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
        l3 = 1.0 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        idx = sel[inside]
        out[idx] = l1[inside] * zs[a] + l2[inside] * zs[b] + l3[inside] * zs[c]
    return out


def roof_surface(poly, roof_samples, eave_sampler, tol_m, max_points, fallback_z):
    """Toiture en TIN par insertion gloutonne (principe de Garland–Heckbert) :
    on part des seuls sommets de l'emprise, on triangule, on mesure l'écart
    entre ce TIN et les altitudes LiDAR du toit, et on insère les points les
    plus mal représentés jusqu'à passer sous `tol_m` ou atteindre
    `max_points`. Un toit plat reste à 2 triangles, un pignon gagne son
    faîtage, un bâtiment à deux hauteurs gagne ses ressauts — le nombre de
    triangles suit la complexité réelle au lieu d'une grille fixe.

    Des points peuvent être insérés SUR le contour (échantillons tous les
    50 cm) : c'est ce qui fait apparaître la pointe d'un pignon dans le mur,
    sans quoi le mur resterait à hauteur d'égout sous un faîtage en l'air.

    roof_samples : (xy (N, 2), z (N,)) mailles de toit dans l'emprise ;
    eave_sampler(xy) → z : altitude du toit au voisinage d'un point du contour.
    Retourne (rings [(x, y, z)...] par anneau, interior [(x, y, z)], tris
    (indices dans rings aplatis puis interior))."""
    poly = orient(poly, 1.0)
    rings = [_ring_coords(poly.exterior)] + [_ring_coords(r) for r in poly.interiors]

    # Échantillons de contour : position curviligne (anneau, t) pour pouvoir
    # les insérer à leur place dans l'anneau.
    boundary_cands = []
    for r_index, ring in enumerate(rings):
        closed = ring + [ring[0]]
        ls = sg.LineString(closed)
        length = ls.length
        n_s = max(int(length / 0.5), 1)
        for s in range(n_s):
            t = (s + 0.5) * length / n_s
            p = ls.interpolate(t)
            boundary_cands.append((r_index, t, p.x, p.y))

    ring_params = []
    for ring in rings:
        closed = ring + [ring[0]]
        ls = sg.LineString(closed)
        ring_params.append([ls.project(sg.Point(p)) for p in ring])

    def z_eave(pts):
        z = eave_sampler(np.asarray(pts, dtype=float))
        return np.where(np.isnan(z), fallback_z, z)

    ring_z = [list(z_eave(ring)) for ring in rings]
    ring_t = [list(params) for params in ring_params]
    ring_xy = [list(ring) for ring in rings]

    bxy = np.array([[c[2], c[3]] for c in boundary_cands]) if boundary_cands else np.zeros((0, 2))
    bz = z_eave(bxy) if len(bxy) else np.zeros(0)
    b_used = np.zeros(len(boundary_cands), dtype=bool)

    ixy, iz = roof_samples
    ixy = np.asarray(ixy, dtype=float).reshape(-1, 2)
    iz = np.asarray(iz, dtype=float)
    i_used = np.zeros(len(iz), dtype=bool)
    interior = []

    min_spacing = 0.7
    n_points = sum(len(r) for r in ring_xy)
    for _iteration in range(400):
        flat_xy = [p for ring in ring_xy for p in ring]
        flat_z = [z for zs in ring_z for z in zs]
        verts2d, tris = _triangulate_polygon(ring_xy, [p[:2] for p in interior])
        zs = np.array(flat_z + [p[2] for p in interior])
        if n_points >= max_points:
            break

        errs = []
        if len(iz):
            est = _interp_on_triangles(verts2d, zs, tris, ixy[:, 0], ixy[:, 1])
            e = np.abs(iz - est)
            e[np.isnan(e) | i_used] = 0.0
            errs.append(('i', e))
        if len(bz):
            est_b = _interp_on_triangles(verts2d, zs, tris, bxy[:, 0], bxy[:, 1])
            e_b = np.abs(bz - est_b)
            e_b[np.isnan(e_b) | b_used] = 0.0
            errs.append(('b', e_b))
        if not errs:
            break
        candidates = []
        for kind, e in errs:
            order = np.argsort(e)[::-1][:12]
            candidates.extend((float(e[k]), kind, int(k)) for k in order if e[k] > tol_m)
        if not candidates:
            break
        candidates.sort(reverse=True)

        existing = np.array(verts2d, dtype=float)
        tree = cKDTree(existing)
        added_pts = []
        inserted = 0
        rejected = 0
        for _err, kind, k in candidates:
            if inserted >= 4 or n_points >= max_points:
                break
            p = ixy[k] if kind == 'i' else bxy[k]
            if tree.query(p)[0] < min_spacing or any(math.dist(p, q) < min_spacing for q in added_pts):
                if kind == 'i':
                    i_used[k] = True
                else:
                    b_used[k] = True
                rejected += 1
                continue
            if kind == 'i':
                interior.append((float(p[0]), float(p[1]), float(iz[k])))
                i_used[k] = True
            else:
                r_index, t, _x, _y = boundary_cands[k]
                pos = int(np.searchsorted(ring_t[r_index], t))
                ring_t[r_index].insert(pos, t)
                ring_xy[r_index].insert(pos, (float(p[0]), float(p[1])))
                ring_z[r_index].insert(pos, float(bz[k]))
                b_used[k] = True
            added_pts.append(p)
            inserted += 1
            n_points += 1
        # Tous les pires points trop proches d'un sommet existant : ils sont
        # désormais marqués, le tour suivant examinera les suivants. S'arrêter
        # ici laissait des toits à la moitié de leur budget (constaté).
        if inserted == 0 and rejected == 0:
            break

    out_rings = [[(x, y, z) for (x, y), z in zip(ring_xy[r], ring_z[r])] for r in range(len(ring_xy))]
    flat_xy = [p for ring in ring_xy for p in ring]
    verts2d, tris = _triangulate_polygon(ring_xy, [p[:2] for p in interior])
    return out_rings, interior, tris, len(flat_xy)


def solid_mesh(rings3d, interior, roof_tris, base_z, edge_groups=None):
    """Assemble un volume fermé : plancher à base_z, murs verticaux montant
    jusqu'à l'égout de chaque sommet du contour, toiture TIN.

    rings3d : anneaux orientés (extérieur trigonométrique, trous horaires),
    sommets (x, y, z_toit). Les normales sont sortantes par construction :
    pour une arête a→b d'un anneau trigonométrique, le triangle
    (a_bas, b_bas, b_haut) a pour normale h·(dy, −dx, 0), à droite du sens de
    parcours, donc vers l'extérieur ; sur un trou (horaire) la même formule
    pointe vers la cour, qui est l'extérieur du volume.

    edge_groups[r][k] : nom du groupe du mur porté par l'arête k de l'anneau r
    (l'arête k va du sommet k au sommet k+1). Retourne (vertices, triangles)
    au format Building.envelope (group/boundary)."""
    vertices = []
    top_index, bot_index = [], []
    for ring in rings3d:
        top_index.append([])
        bot_index.append([])
        for x, y, z in ring:
            z_top = max(z, base_z + 1.0)
            top_index[-1].append(len(vertices))
            vertices.append([x, y, z_top])
    for r, ring in enumerate(rings3d):
        for x, y, _z in ring:
            bot_index[r].append(len(vertices))
            vertices.append([x, y, base_z])
    interior_index = []
    for x, y, z in interior:
        interior_index.append(len(vertices))
        vertices.append([x, y, max(z, base_z + 1.0)])

    triangles = []
    flat_top = [i for ring in top_index for i in ring] + interior_index
    for a, b, c in roof_tris:
        ia, ib, ic = flat_top[a], flat_top[b], flat_top[c]
        pa, pb, pc = vertices[ia], vertices[ib], vertices[ic]
        signed = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        if abs(signed) < 1e-10:
            continue
        tri = [ia, ib, ic] if signed > 0 else [ia, ic, ib]
        triangles.append({'v': tri, 'group': 'toiture', 'boundary': 'exterior_air'})

    for r, ring in enumerate(rings3d):
        n = len(ring)
        for k in range(n):
            a_t, b_t = top_index[r][k], top_index[r][(k + 1) % n]
            a_b, b_b = bot_index[r][k], bot_index[r][(k + 1) % n]
            group = edge_groups[r][k] if edge_groups else 'mur'
            triangles.append({'v': [a_b, b_b, b_t], 'group': group, 'boundary': 'exterior_air'})
            triangles.append({'v': [a_b, b_t, a_t], 'group': group, 'boundary': 'exterior_air'})

    # Plancher : même contour, triangulé sans point intérieur, normale vers le bas.
    ring2d = [[(x, y) for x, y, _z in ring] for ring in rings3d]
    _v, floor_tris = _triangulate_polygon(ring2d)
    flat_bot = [i for ring in bot_index for i in ring]
    for a, b, c in floor_tris:
        ia, ib, ic = flat_bot[a], flat_bot[b], flat_bot[c]
        pa, pb, pc = vertices[ia], vertices[ib], vertices[ic]
        signed = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        if abs(signed) < 1e-10:
            continue
        tri = [ia, ic, ib] if signed > 0 else [ia, ib, ic]
        triangles.append({'v': tri, 'group': 'sol', 'boundary': 'ground'})
    return vertices, triangles


def _edge_groups_for(rings3d, original_rings):
    """Nom de mur par arête finale : une arête issue du découpage d'une arête
    d'origine (points de contour insérés par roof_surface) garde le groupe de
    celle-ci — mur_1..mur_N numérotés comme l'emprise, anneaux compris."""
    groups = []
    counter = 0
    for ring3d, orig in zip(rings3d, original_rings):
        orig_line = [sg.LineString([orig[k], orig[(k + 1) % len(orig)]]) for k in range(len(orig))]
        ring_groups = []
        n = len(ring3d)
        for k in range(n):
            a = ring3d[k]
            b = ring3d[(k + 1) % n]
            mid = sg.Point((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            dists = [line.distance(mid) for line in orig_line]
            ring_groups.append(f'mur_{counter + int(np.argmin(dists)) + 1}')
        counter += len(orig)
        groups.append(ring_groups)
    return groups


def _eave_sampler(rxy, rz, k=12, radius=2.5, max_rms=0.25):
    """Altitude du toit EN un point du contour de l'emprise.

    Les mailles de toit les plus proches d'un point du contour sont à 0,5–1 m à
    l'intérieur (emprise érodée, mailles de façade écartées) : prendre leur
    médiane surestime l'égout d'un toit en pente de pente × distance —
    +0,9 m mesuré sur un toit à 37°. On ajuste donc un plan sur les k mailles
    voisines et on l'EXTRAPOLE jusqu'au point. Quand ces mailles ne forment pas
    un plan (deux pans au droit d'un pignon, ressaut), résidu > max_rms : repli
    sur la médiane des 4 plus proches, sans extrapolation."""
    tree = cKDTree(rxy)

    def sample(pts):
        pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        out = np.full(len(pts), np.nan)
        kk = min(k, len(rz))
        dist, idx = tree.query(pts, k=kk, distance_upper_bound=radius)
        dist = np.atleast_2d(dist)
        idx = np.atleast_2d(idx)
        for q in range(len(pts)):
            ok = np.isfinite(dist[q])
            if not ok.any():
                continue
            nb = idx[q][ok]
            near = nb[np.argsort(dist[q][ok])[:4]]
            fallback = float(np.median(rz[near]))
            if len(nb) < 6:
                out[q] = fallback
                continue
            A = np.column_stack([np.ones(len(nb)), rxy[nb, 0] - pts[q, 0], rxy[nb, 1] - pts[q, 1]])
            coef, *_ = np.linalg.lstsq(A, rz[nb], rcond=None)
            rms = float(np.sqrt(np.mean((A @ coef - rz[nb]) ** 2)))
            if rms > max_rms:
                out[q] = fallback
                continue
            # Extrapolation bornée : un égout est SOUS ses voisins (jusqu'à 1 m),
            # jamais au-dessus — sans cette borne haute, l'extrémité d'un faîtage
            # débordait de 0,75 m au-dessus du toit (constaté).
            out[q] = float(np.clip(coef[0], rz[nb].min() - 1.0, rz[nb].max() + 0.2))
        return out
    return sample


def building_solid(poly, rasters, tol_m=0.4, max_points=40, fallback_height=None):
    """Volume complet d'un bâtiment dont l'emprise (locale) est connue.
    Retourne (vertices, triangles, info) ou lève ObservedEnvError."""
    poly = orient(poly.buffer(0) if not poly.is_valid else poly, 1.0)
    if poly.geom_type != 'Polygon' or poly.area < 2.0:
        raise ObservedEnvError("Emprise dégénérée.")
    poly = poly.simplify(0.05, preserve_topology=True)
    poly = orient(poly, 1.0)
    grid = rasters.grid_fine

    # Base : point le plus bas du terrain le long du contour. Les murs
    # s'enfoncent donc côté amont d'un terrain en pente, ce qui est sans
    # conséquence (le terrain les recouvre) — l'inverse laisserait un vide
    # sous le bâtiment côté aval, que le soleil rasant traverserait.
    ring_pts = np.array(_ring_coords(poly.exterior))
    boundary = sg.LineString(list(ring_pts) + [ring_pts[0]])
    samples = np.array([boundary.interpolate(t).coords[0]
                        for t in np.linspace(0, boundary.length, max(int(boundary.length), 8))])
    base_z = float(np.min(grid.sample(rasters.dtm_fine, samples[:, 0], samples[:, 1])))

    inner = poly.buffer(-0.3)
    if inner.is_empty:
        inner = poly
    m, (i0, i1, j0, j1) = polygon_mask(inner, grid)
    roof_win = rasters.roof[j0:j1, i0:i1]
    sel = m & ~np.isnan(roof_win)
    X, Y = np.meshgrid(grid.coords[i0:i1], grid.coords[j0:j1])
    rxy = np.column_stack([X[sel], Y[sel]])
    rz = roof_win[sel]
    coverage = float(sel.sum()) / max(int(m.sum()), 1)

    # Sous-échantillonnage des candidats : une maille sur deux (1 m) suffit à
    # trouver faîtages et ressauts, et divise le coût de chaque itération.
    # Fait AVANT de construire l'index spatial, qui doit décrire les mêmes points.
    if len(rz) > 600:
        keep = np.arange(len(rz))[::2]
        rxy, rz = rxy[keep], rz[keep]

    if coverage < 0.3 or len(rz) < 8:
        # Pas assez de toit observé : prisme à la hauteur connue.
        h = fallback_height if fallback_height else 6.0
        fz = base_z + h
        rxy = np.zeros((0, 2))
        rz = np.zeros(0)
        sampler = lambda pts: np.full(len(pts), fz)  # noqa: E731
        roof_kind = 'plat (hauteur BD TOPO)' if fallback_height else 'plat (hauteur par défaut)'
    else:
        fz = float(np.median(rz))
        sampler = _eave_sampler(rxy, rz)
        roof_kind = 'relevé LiDAR'

    rings3d, interior, tris, _n = roof_surface(poly, (rxy, rz), sampler, tol_m, max_points, fz)
    original = [_ring_coords(poly.exterior)] + [_ring_coords(r) for r in poly.interiors]
    groups = _edge_groups_for(rings3d, original)
    vertices, triangles = solid_mesh(rings3d, interior, tris, base_z, groups)
    top = max(v[2] for v in vertices)
    info = {
        'base_z': round(base_z, 2), 'height_m': round(top - base_z, 2),
        'roof': roof_kind, 'roof_coverage': round(coverage, 2),
        'roof_points': len(interior) + sum(len(r) for r in rings3d),
    }
    return vertices, triangles, info


def prism_solid(poly, z0, z1, group_prefix='mur'):
    """Prisme droit (arbre, bâtiment sans relevé de toit) — même assembleur
    que les bâtiments, toit plat."""
    poly = orient(poly, 1.0)
    rings = [_ring_coords(poly.exterior)] + [_ring_coords(r) for r in poly.interiors]
    rings3d = [[(x, y, z1) for x, y in ring] for ring in rings]
    ring2d = [list(r) for r in rings]
    _v, tris = _triangulate_polygon(ring2d)
    groups = [[f'{group_prefix}_{k + 1}' for k in range(len(r))] for r in rings]
    return solid_mesh(rings3d, [], tris, z0, groups)


# ── Végétation ─────────────────────────────────────────────────────────────────

def tree_objects(rasters, building_polys, acquisition_months, max_objects=MAX_VEGETATION_OBJECTS):
    """Arbres et massifs à partir de la végétation haute (classe 5).

    Segmentation couronne par couronne : hauteur de canopée lissée, maxima
    locaux (sommets d'arbres) comme germes, puis ligne de partage des eaux
    sur la canopée inversée. Chaque couronne devient un prisme : base = bas
    du houppier observé (10ᵉ centile des points de végétation haute, pas le
    sol — le tronc ne masque presque rien), sommet = point le plus haut.

    Transmittance : fraction de trouée MESURÉE (points sol / points totaux
    sous la couronne). Valable telle quelle si le vol a eu lieu feuilles
    présentes ; sinon elle décrit l'hiver et on applique la valeur
    « en feuilles » par défaut, en conservant la mesure dans k_bare."""
    g = rasters.grid
    chm = rasters.veg_top - rasters.dtm
    with np.errstate(invalid='ignore'):
        mask = ~np.isnan(chm) & (chm >= MIN_TREE_HEIGHT_M)
    # Les points « végétation » sur un toit (mauvaise classification, arbres
    # en surplomb) ne doivent pas créer d'arbre DANS un bâtiment.
    bld = np.zeros_like(mask)
    for poly in building_polys:
        m, (i0, i1, j0, j1) = polygon_mask(poly, g)
        if m.size:
            bld[j0:j1, i0:i1] |= m
    mask &= ~bld
    if not mask.any():
        return [], {'trees_detected': 0}

    chm_f = np.where(mask, chm, 0.0)
    smooth = ndimage.gaussian_filter(chm_f, sigma=1.0)
    peaks = (ndimage.maximum_filter(smooth, size=5) == smooth) & mask & (smooth >= MIN_TREE_HEIGHT_M)
    markers, n_peaks = ndimage.label(peaks)
    if n_peaks == 0:
        return [], {'trees_detected': 0}
    markers = markers.astype(np.int32)
    markers[~mask] = -1
    top = float(np.nanmax(smooth)) or 1.0
    inp = np.clip((1.0 - smooth / top) * 65000, 0, 65000).astype(np.uint16)
    seg = ndimage.watershed_ift(inp, markers)
    seg[~mask] = 0
    seg[seg < 0] = 0

    leaf_on = bool(acquisition_months) and all(m in LEAF_ON_MONTHS for m in acquisition_months)
    X, Y = g.centers()
    objs = []
    slices = ndimage.find_objects(seg)
    for label, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        cells = seg[sl] == label
        n_cells = int(cells.sum())
        if n_cells < 3:
            continue
        xs = X[sl][cells]
        ys = Y[sl][cells]
        heights = chm[sl][cells]
        lows = (rasters.veg_low - rasters.dtm)[sl][cells]
        dtm_c = rasters.dtm[sl][cells]
        h_top = float(np.nanmax(heights))
        base_rel = float(np.nanpercentile(lows, 10)) if np.isfinite(lows).any() else 0.4 * h_top
        base_rel = min(max(base_rel, 1.0), 0.7 * h_top)
        ground = float(np.median(dtm_c))
        n_g = float(rasters.n_ground[sl][cells].sum())
        n_t = float(rasters.n_total[sl][cells].sum())
        gap = n_g / n_t if n_t > 0 else 0.0
        k_obs = round(min(max(gap, 0.05), 0.8), 3)

        pts = sg.MultiPoint(list(zip(xs.tolist(), ys.tolist())))
        crown = pts.convex_hull.buffer(0.5 * g.cell, join_style=2)
        # Enveloppe convexe pour une couronne compacte (l'immense majorité) ;
        # contour réel du segment quand elle gonflerait l'aire de plus de 40 % —
        # haie courbe, alignement, massif en L : l'enveloppe convexe y couvrait
        # une grande surface vide (constaté sur données réelles).
        if crown.area > 1.4 * n_cells * g.cell ** 2:
            shape = _cells_to_polygon(cells, sl, g)
            if shape is not None and shape.area > 2.0:
                crown = shape.simplify(0.7, preserve_topology=True)
        crown = _limit_vertices(crown, 10)
        if crown.is_empty or crown.area < 2.0:
            continue
        cx, cy = crown.centroid.x, crown.centroid.y
        objs.append({
            'crown': crown, 'z0': ground + base_rel, 'z1': ground + h_top,
            'height_m': round(h_top, 1), 'crown_base_m': round(base_rel, 1),
            'k': k_obs if leaf_on else DEFAULT_K_LEAF, 'k_bare': None if leaf_on else k_obs,
            'k_measured': k_obs, 'dist': math.hypot(cx, cy), 'area_m2': round(crown.area, 1),
        })
    objs.sort(key=lambda o: o['dist'])
    return objs[:max_objects], {'trees_detected': len(objs), 'leaf_on': leaf_on}


def _limit_vertices(poly, max_vertices):
    """Réduit un polygone convexe à au plus max_vertices sommets en
    augmentant progressivement la tolérance de simplification."""
    if poly.geom_type != 'Polygon':
        return poly
    tol = 0.2
    out = poly
    while len(out.exterior.coords) - 1 > max_vertices and tol < 5.0:
        out = poly.simplify(tol, preserve_topology=True)
        tol *= 1.5
    return out


# ── Terrain ────────────────────────────────────────────────────────────────────

def terrain_mesh(rasters, half_m, tol_m=0.3, max_points=1500):
    """TIN du terrain par insertion gloutonne sur le MNT (même principe que
    les toitures) : un terrain plat reste à quelques triangles, un relief
    marqué gagne des points là où il en a besoin — au lieu d'une grille
    régulière qui dépensait autant de triangles sur un parking plat que sur
    un talus. La tolérance croît avec la distance à l'origine : une erreur de
    30 cm à 200 m ne change aucune hauteur angulaire visible."""
    g = rasters.grid
    X, Y = g.centers()
    step = 2 if g.n > 200 else 1
    qx, qy = X[::step, ::step].ravel(), Y[::step, ::step].ravel()
    qz = rasters.dtm[::step, ::step].ravel()
    in_square = (np.abs(qx) <= half_m) & (np.abs(qy) <= half_m)
    qx, qy, qz = qx[in_square], qy[in_square], qz[in_square]
    dist = np.hypot(qx, qy)
    tol = tol_m + 0.003 * dist

    h = half_m
    border = []
    n_side = 8
    for k in range(n_side):
        t = -h + 2 * h * k / n_side
        border += [(t, -h), (h, t), (-t, h), (-h, -t)]
    pts = np.array(border, dtype=float)
    zs = g.sample(rasters.dtm, pts[:, 0], pts[:, 1])
    used = np.zeros(len(qz), dtype=bool)
    for _iteration in range(100):
        tri = Delaunay(pts)
        simplex = tri.find_simplex(np.column_stack([qx, qy]))
        inside = simplex >= 0
        est = np.full(len(qz), np.nan)
        T = tri.transform[simplex[inside]]
        d = np.column_stack([qx[inside], qy[inside]]) - T[:, 2]
        bary = np.einsum('ijk,ik->ij', T[:, :2], d)
        bary = np.column_stack([bary, 1 - bary.sum(axis=1)])
        est[inside] = (zs[tri.simplices[simplex[inside]]] * bary).sum(axis=1)
        err = np.abs(qz - est)
        err[np.isnan(err) | used] = 0.0
        over = err > tol
        if not over.any() or len(pts) >= max_points:
            break
        order = np.argsort(err)[::-1]
        added = []
        for k in order[:400]:
            if not over[k] or len(pts) + len(added) >= max_points:
                break
            p = (qx[k], qy[k])
            if any((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2 < 25.0 for a in added):
                continue
            added.append(p)
            used[k] = True
            if len(added) >= 60:
                break
        if not added:
            break
        pts = np.vstack([pts, np.array(added)])
        zs = np.concatenate([zs, g.sample(rasters.dtm, np.array(added)[:, 0], np.array(added)[:, 1])])

    tri = Delaunay(pts)
    vertices = [[float(x), float(y), float(z)] for (x, y), z in zip(pts, zs)]
    triangles = []
    for a, b, c in tri.simplices:
        pa, pb, pc = pts[a], pts[b], pts[c]
        signed = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        triangles.append({'v': [int(a), int(b), int(c)] if signed > 0 else [int(a), int(c), int(b)]})
    return vertices, triangles


# ── Objets, composition ────────────────────────────────────────────────────────

def make_object(obj_id, kind, origin, label, vertices, triangles, footprint=None, info=None,
                status=STATUS_ACTIVE, reason=None, k=None):
    return {
        'id': int(obj_id), 'kind': kind, 'status': status, 'origin': origin, 'label': label,
        'reason': reason, 'info': info or {}, 'building_id': None,
        'footprint': _poly_to_rings(footprint) if footprint is not None else None,
        'vertices': [[round(c, 3) for c in v] for v in vertices],
        'triangles': triangles, 'k': k,
    }


def _poly_to_rings(poly):
    poly = orient(poly, 1.0)
    return [[[round(x, 3), round(y, 3)] for x, y in _ring_coords(poly.exterior)]] + [
        [[round(x, 3), round(y, 3)] for x, y in _ring_coords(r)] for r in poly.interiors
    ]


def rings_to_poly(rings):
    if not rings:
        return None
    poly = sg.Polygon(rings[0], rings[1:])
    return poly if poly.is_valid else poly.buffer(0)


def compose_envelope(objects):
    """Environment.envelope à partir des objets ACTIFS — seul format lu par
    api.shadow. Chaque triangle porte `obj` (identifiant d'objet) ; ceux de la
    végétation portent aussi `k`, ce qui les fait passer par la scène
    translucide (api.shadow.VegetationScene) au lieu du maillage opaque.

    Le terrain est percé sous l'emprise de tout bâtiment ÉTUDIÉ : sans ça, un
    terrain en pente traverserait son plancher et ses murs, et bloquerait des
    rayons partant de sa propre enveloppe (même raison que
    elevation.build_terrain_mesh, qui ramenait ces sommets à z = 0)."""
    studied = [rings_to_poly(o['footprint']) for o in objects
               if o.get('status') == STATUS_STUDIED and o.get('footprint')]
    studied = [p.buffer(0.3) for p in studied if p is not None and not p.is_empty]
    studied_union = shapely.ops.unary_union(studied) if studied else None

    vertices, triangles = [], []
    for obj in objects:
        if obj.get('status') != STATUS_ACTIVE:
            continue
        offset = len(vertices)
        verts = obj['vertices']
        vertices.extend(verts)
        k = obj.get('k')
        for tri in obj['triangles']:
            v = tri['v']
            if obj['kind'] == 'terrain' and studied_union is not None:
                cx = (verts[v[0]][0] + verts[v[1]][0] + verts[v[2]][0]) / 3.0
                cy = (verts[v[0]][1] + verts[v[1]][1] + verts[v[2]][1]) / 3.0
                if studied_union.contains(sg.Point(cx, cy)):
                    continue
            out = {'v': [v[0] + offset, v[1] + offset, v[2] + offset], 'obj': obj['id']}
            if k is not None:
                out['k'] = k
            triangles.append(out)
    return {'vertices': vertices, 'triangles': triangles}


# ── Bâtiment étudié à partir d'un objet ────────────────────────────────────────

ROOF_SECTORS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']


def building_envelope_from_object(obj, other_objects=()):
    """Voir building_envelope_from_mesh — cas d'un objet unique."""
    return building_envelope_from_mesh(obj['vertices'], obj['triangles'], {obj['id']}, other_objects)


def building_envelope_from_mesh(vertices, triangles, exclude_ids, other_objects=()):
    """Enveloppe de Building (format TriangleInputSerializer) à partir d'un
    objet bâtiment de l'environnement, DANS LE MÊME REPÈRE : aucun
    changement de coordonnées, c'est ce qui garantit l'alignement parfait
    entre le bâtiment étudié et ses voisins.

    Groupes : `mur_k` (une arête d'emprise), suffixé `_mitoyen` si un autre
    bâtiment actif longe cette arête (le mur n'est alors pas exposé à l'air
    extérieur — à assigner en conséquence), `toiture_<orientation>` par pan
    (ou `toiture_plate`), `sol` (au contact du terrain)."""
    from . import geometry

    vertices = [list(v) for v in vertices]
    triangles = [dict(t) for t in triangles]
    geom = geometry.compute_envelope_geometry(vertices, [{'v': t['v']} for t in triangles])

    neighbours = [rings_to_poly(o['footprint']) for o in other_objects
                  if o.get('kind') == 'building' and o.get('status') == STATUS_ACTIVE
                  and o.get('footprint') and o['id'] not in exclude_ids]
    neighbours = [p for p in neighbours if p is not None and not p.is_empty]
    shared_walls = _shared_wall_groups(vertices, triangles, neighbours)

    out = []
    for tri, g in zip(triangles, geom):
        group = tri.get('group') or 'toiture'
        boundary = tri.get('boundary', 'exterior_air')
        if group == 'toiture':
            if g['tilt_deg'] < 10.0:
                group = 'toiture_plate'
            else:
                sector = ROOF_SECTORS[int(((g['azimuth_deg'] + 22.5) % 360.0) // 45.0)]
                group = f'toiture_{sector}'
        elif group in shared_walls:
            group = f'{group}_mitoyen'
        out.append({'v': list(tri['v']), 'group': group, 'paroi_model_id': None,
                    'boundary': boundary, 'shading_profile_id': None})
    return vertices, out


def _shared_wall_groups(vertices, triangles, neighbours):
    if not neighbours:
        return set()
    union = shapely.ops.unary_union([p.buffer(0.4) for p in neighbours])
    groups = {}
    for tri in triangles:
        g = tri.get('group') or ''
        if not g.startswith('mur_'):
            continue
        for i in tri['v']:
            groups.setdefault(g, set()).add((round(vertices[i][0], 3), round(vertices[i][1], 3)))
    shared = set()
    for g, pts in groups.items():
        pts = sorted(pts)
        if len(pts) < 2:
            continue
        line = sg.LineString([pts[0], pts[-1]]) if len(pts) == 2 else sg.MultiPoint(pts).convex_hull
        if line.length <= 0:
            continue
        inside = line.intersection(union)
        if inside.length / line.length > 0.6:
            shared.add(g)
    return shared


# ── Modèle importé à la place d'un objet ───────────────────────────────────────

def _import_footprint(vertices, triangles):
    polys = []
    for tri in triangles:
        ring = [(vertices[i][0], vertices[i][1]) for i in tri['v']]
        p = sg.Polygon(ring)
        if p.is_valid and p.area > 1e-6:
            polys.append(p)
    if not polys:
        return None
    merged = shapely.ops.unary_union(polys)
    return merged if not merged.is_empty else None


def _iou(a, b):
    inter = a.intersection(b).area
    union = a.union(b).area
    return inter / union if union > 0 else 0.0


def fit_import(vertices, triangles, target_poly, base_z, up_axis='auto', scale='auto'):
    """Place un maillage importé (OBJ/STL, repère et unité quelconques) sur
    l'emprise d'un objet de l'environnement.

    Corrige au passage deux défauts silencieux de l'import brut : l'axe
    vertical (Blender exporte en Y-up par défaut) et l'unité (STL souvent en
    millimètres). En mode 'auto', chaque combinaison plausible est essayée et
    on garde celle dont l'emprise ressemble le plus à la cible.

    Ajustement : rotation (tous les 2°, puis affinée à 0,25°) et translation
    (centroïde puis ±1 m) maximisant l'indice de Jaccard (IoU) entre l'emprise
    du modèle et la cible ; le point le plus bas est posé à base_z.
    Les triangles dégénérés (deux sommets confondus après soudure) sont écartés
    et comptés, au lieu de faire rejeter tout le fichier par
    geometry.compute_envelope_geometry.

    Retourne (vertices, triangles, report)."""
    V = np.asarray(vertices, dtype=float)
    if V.ndim != 2 or V.shape[1] != 3 or len(V) < 3:
        raise ObservedEnvError("Maillage importé vide ou mal formé.")

    axes = ['z', 'y'] if up_axis == 'auto' else [up_axis]
    target_area = target_poly.area
    results = []
    for axis in axes:
        Va = V.copy() if axis == 'z' else np.column_stack([V[:, 0], -V[:, 2], V[:, 1]])
        fp0 = _import_footprint(Va.tolist(), triangles)
        if fp0 is None or fp0.area <= 0:
            continue
        if scale == 'auto':
            candidates = [1.0, 0.01, 0.001, 0.0254]
            s = min(candidates, key=lambda c: abs(math.log(max(fp0.area * c * c, 1e-12) / target_area)))
        else:
            s = float(scale)
        fp = shapely.affinity.scale(fp0, s, s, origin=(0, 0))
        c_src = fp.centroid
        c_dst = target_poly.centroid
        base = shapely.affinity.translate(fp, c_dst.x - c_src.x, c_dst.y - c_src.y)

        def score(angle, dx=0.0, dy=0.0, _base=base, _c=c_dst):
            g = shapely.affinity.rotate(_base, angle, origin=(_c.x, _c.y))
            return _iou(shapely.affinity.translate(g, dx, dy), target_poly)

        best_angle = max(np.arange(0.0, 360.0, 2.0), key=score)
        best_angle = max(np.arange(best_angle - 2.0, best_angle + 2.01, 0.25), key=score)
        best_shift = max(((dx, dy) for dx in np.arange(-1.0, 1.01, 0.25) for dy in np.arange(-1.0, 1.01, 0.25)),
                         key=lambda d: score(best_angle, d[0], d[1]))
        results.append((score(best_angle, *best_shift), axis, s, float(best_angle), best_shift,
                        (c_src.x, c_src.y), (c_dst.x, c_dst.y), Va))
    if not results:
        raise ObservedEnvError("Le modèle importé n'a aucune emprise au sol exploitable.")

    iou, axis, s, angle, (dx, dy), c_src, c_dst, Va = max(results, key=lambda r: r[0])
    P = Va * s
    P[:, 0] += c_dst[0] - c_src[0]
    P[:, 1] += c_dst[1] - c_src[1]
    theta = math.radians(angle)
    x = P[:, 0] - c_dst[0]
    y = P[:, 1] - c_dst[1]
    P[:, 0] = c_dst[0] + x * math.cos(theta) - y * math.sin(theta) + dx
    P[:, 1] = c_dst[1] + x * math.sin(theta) + y * math.cos(theta) + dy
    P[:, 2] += base_z - P[:, 2].min()

    kept, n_degenerate = [], 0
    for tri in triangles:
        a, b, c = (P[i] for i in tri['v'])
        if np.linalg.norm(np.cross(b - a, c - a)) < 1e-9:
            n_degenerate += 1
            continue
        kept.append(tri)
    report = {
        'iou': round(float(iou), 3), 'up_axis': axis, 'scale': s, 'rotation_deg': round(angle, 2),
        'shift_m': [round(float(dx), 2), round(float(dy), 2)], 'degenerate_removed': n_degenerate,
    }
    return P.round(4).tolist(), kept, report


# ── Orchestration ──────────────────────────────────────────────────────────────

def roof_budget(poly, dist):
    """Nombre maximal de sommets de toiture, proportionnel à la surface : un toit
    à plusieurs niveaux (terrasses, édicules) a besoin de points le long de
    chaque ressaut, et un plafond fixe de 60 reliait les niveaux par des pentes
    de plusieurs mètres sur les grands collectifs (constaté). Au-delà de 60 m,
    moitié moins : l'erreur de hauteur angulaire décroît avec la distance."""
    if dist < 60.0:
        return int(min(max(poly.area / 6.0, 24), 250))
    return int(min(max(poly.area / 12.0, 20), 120))


def _fallback_height(fp):
    """Hauteur BD TOPO d'un bâtiment, pour un prisme quand le LiDAR ne voit
    pas son toit (masqué par un arbre, par exemple)."""
    if fp.get('hauteur'):
        return fp['hauteur']
    if fp.get('etages'):
        return fp['etages'] * 3.0
    return None


def build_objects(frame, half_m, points, bdtopo, acquisition_months, include_vegetation=True,
                  include_terrain=True, self_polygon=None, progress_cb=None, return_rasters=False):
    """Chaîne complète, sans réseau. points = (e, n, z, cls) en Lambert 93 ;
    bdtopo = sortie de lidar_source.fetch_bdtopo_buildings_l93.

    self_polygon (optionnel) : emprise, dans le repère local, d'un bâtiment
    étudié DÉJÀ existant (génération « autour d'un bâtiment ») — l'objet qui
    lui correspond est marqué `studied` au lieu d'être un obstacle, et ceux
    qui l'empiètent sont signalés (même critère que geodata.resolve_against_self).

    Retourne (objects, ground_z, stats, warnings)."""
    def report(stage, pct):
        if progress_cb:
            progress_cb(stage, pct)

    e, n, z, cls = points
    x, y = frame.to_local(e, n)
    half_r = half_m + RASTER_MARGIN_M
    inside = (np.abs(x) <= half_r) & (np.abs(y) <= half_r)
    x, y, z, cls = x[inside], y[inside], z[inside], cls[inside]

    report('rasters', 35)
    rasters, ground_z = build_rasters(x, y, z, cls, half_r, ground_z=frame.ground_z)
    warnings = []
    stats = {'points_used': int(len(x))}

    report('registration', 45)
    footprints = []
    square = sg.box(-half_m, -half_m, half_m, half_m)
    for b in bdtopo:
        poly = to_local_polygon(b['rings'], frame)
        if poly.is_empty or poly.area < 4.0:
            continue
        # Toute emprise qui RECOUPE la zone est gardée, même centrée dehors :
        # l'écarter laissait son toit, lui bien présent dans le nuage, ressortir
        # comme un « bâtiment LiDAR seul » en doublon (constaté en bord de zone).
        if not poly.intersects(square):
            continue
        footprints.append({**b, 'poly': poly})
    global_shift = register_footprints(footprints, rasters) if footprints else (0.0, 0.0)
    stats['global_shift_m'] = [round(global_shift[0], 2), round(global_shift[1], 2)]
    if footprints:
        stats['cover_before'] = round(float(np.mean([f['cover_before'] for f in footprints])), 3)
        stats['cover_after'] = round(float(np.mean([f['cover'] for f in footprints])), 3)

    report('buildings', 55)
    objects = []
    next_id = [1]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1

    n_confirmed = n_absent = n_masked = n_failed = n_uncovered = 0
    building_polys = []
    for fp in footprints:
        poly = fp['poly']
        cover = fp['cover']
        status, reason, origin = STATUS_ACTIVE, None, 'bdtopo+lidar'
        fallback = _fallback_height(fp)
        if _point_coverage(poly, rasters) < 0.5:
            # Aucune mesure à cet endroit (dalle LiDAR manquante en bord de zone) :
            # l'absence de toit n'y prouve rien. Prisme à la hauteur BD TOPO.
            origin = 'bdtopo'
            n_uncovered += 1
        elif cover < 0.15:
            above = _above_fraction(poly, rasters)
            if above < 0.3:
                # Le LiDAR voit le sol à cet endroit : bâtiment démoli ou jamais
                # construit. Retiré par défaut (restaurable), jamais supprimé.
                status, reason, origin = STATUS_REMOVED, "Absent du relevé LiDAR (démoli ?).", 'bdtopo'
                n_absent += 1
            else:
                origin = 'bdtopo'
                n_masked += 1
        else:
            n_confirmed += 1
        dist = math.hypot(poly.centroid.x, poly.centroid.y)
        try:
            verts, tris, info = building_solid(
                poly, rasters, tol_m=0.35 + 0.004 * dist, max_points=roof_budget(poly, dist),
                fallback_height=fallback,
            )
        except (ObservedEnvError, ValueError) as exc:
            n_failed += 1
            warnings.append(f"Bâtiment {fp.get('id') or '?'} non reconstruit ({exc}).")
            continue
        info.update({
            'bdtopo_id': fp.get('id'), 'nature': fp.get('nature'), 'usage': fp.get('usage'),
            'mat_murs': fp.get('mat_murs'), 'mat_toit': fp.get('mat_toit'), 'annee': fp.get('annee'),
            'logements': fp.get('logements'), 'etages': fp.get('etages'),
            'shift_m': fp.get('shift'), 'lidar_cover_before': fp.get('cover_before'),
            'lidar_cover': cover, 'hauteur_bdtopo': fp.get('hauteur'), 'distance_m': round(dist, 1),
        })
        label = fp.get('usage') or fp.get('nature') or 'Bâtiment'
        objects.append(make_object(new_id(), 'building', origin, label, verts, tris, poly, info,
                                   status=status, reason=reason))
        if status == STATUS_ACTIVE:
            building_polys.append(poly)

    report('lidar-only', 65)
    n_lidar_only = 0
    for poly in lidar_only_buildings(rasters, [f['poly'] for f in footprints]):
        if not poly.intersects(square):
            continue
        dist = math.hypot(poly.centroid.x, poly.centroid.y)
        try:
            verts, tris, info = building_solid(poly, rasters, tol_m=0.35 + 0.004 * dist,
                                               max_points=roof_budget(poly, dist))
        except (ObservedEnvError, ValueError):
            continue
        info.update({'distance_m': round(dist, 1), 'lidar_cover': 1.0})
        objects.append(make_object(new_id(), 'building', 'lidar', 'Bâtiment (LiDAR seul)',
                                   verts, tris, poly, info))
        building_polys.append(poly)
        n_lidar_only += 1

    n_trees = 0
    if include_vegetation:
        report('vegetation', 75)
        trees, tree_stats = tree_objects(rasters, building_polys, acquisition_months)
        stats.update(tree_stats)
        for t in trees:
            if not t['crown'].intersects(square):
                continue
            try:
                verts, tris = prism_solid(t['crown'], t['z0'], t['z1'], group_prefix='couronne')
            except ObservedEnvError:
                continue
            info = {k: t[k] for k in ('height_m', 'crown_base_m', 'k_measured', 'k_bare', 'area_m2')}
            info['distance_m'] = round(t['dist'], 1)
            objects.append(make_object(new_id(), 'vegetation', 'lidar', 'Arbre / massif',
                                       verts, [{'v': tri['v']} for tri in tris], t['crown'], info, k=t['k']))
            n_trees += 1
        if trees and not tree_stats.get('leaf_on'):
            warnings.append(
                "LiDAR acquis hors saison de feuillage : la transparence mesurée des arbres vaut pour "
                "l'hiver. La valeur « en feuilles » par défaut (20 %) est appliquée toute l'année ; "
                "la mesure est conservée par arbre."
            )

    if include_terrain:
        report('terrain', 85)
        verts, tris = terrain_mesh(rasters, half_m)
        objects.append(make_object(new_id(), 'terrain', 'lidar', 'Terrain (MNT LiDAR)', verts, tris,
                                   info={'points': len(verts)}))

    mark_studied(objects, self_polygon, warnings)
    objects = _apply_budget(objects, warnings)

    if n_uncovered:
        warnings.append(
            f"{n_uncovered} bâtiment(s) hors de la couverture LiDAR (dalle absente) : hauteur BD TOPO, "
            "toit plat."
        )
    stats.update({
        'buildings_uncovered': n_uncovered,
        'buildings_confirmed': n_confirmed, 'buildings_absent': n_absent,
        'buildings_masked': n_masked, 'buildings_lidar_only': n_lidar_only,
        'buildings_failed': n_failed, 'trees': n_trees,
    })
    if n_absent:
        warnings.append(
            f"{n_absent} bâtiment(s) de la BD TOPO absent(s) du relevé LiDAR (démolis ?) : retirés par "
            "défaut, restaurables un par un."
        )
    if n_masked:
        warnings.append(
            f"{n_masked} bâtiment(s) sans toit visible dans le LiDAR (sous un arbre, ou mal classés) : "
            "conservés avec la hauteur BD TOPO et un toit plat."
        )
    if abs(global_shift[0]) + abs(global_shift[1]) > 0.5:
        warnings.append(
            f"Emprises BD TOPO recalées de {math.hypot(*global_shift):.1f} m en bloc sur le LiDAR, "
            "puis bâtiment par bâtiment (±2 m au plus)."
        )
    report('done', 88)
    if return_rasters:
        return objects, ground_z, stats, warnings, rasters
    return objects, ground_z, stats, warnings


SELF_IOU_MIN = 0.3


def mark_studied(objects, self_polygon, warnings):
    """Génération autour d'un bâtiment déjà existant : l'objet dont l'emprise
    ressemble le plus à la sienne (indice de Jaccard > 0,3, même seuil que
    geodata.SELF_OVERLAP_RATIO) est le bâtiment lui-même — marqué `studied`, il
    n'est pas un obstacle. Retourne l'objet marqué, ou None."""
    if self_polygon is None:
        return None
    best = None
    for obj in objects:
        if obj['kind'] != 'building' or obj['status'] == STATUS_STUDIED or not obj.get('footprint'):
            continue
        p = rings_to_poly(obj['footprint'])
        iou = _iou(p, self_polygon) if p is not None else 0.0
        if iou > SELF_IOU_MIN and (best is None or iou > best[0]):
            best = (iou, obj)
    if best is None:
        warnings.append(
            "Le bâtiment étudié n'a été retrouvé ni dans la BD TOPO ni dans le LiDAR à cet endroit : "
            "vérifiez son géoréférencement."
        )
        return None
    best[1]['status'] = STATUS_STUDIED
    best[1]['reason'] = "Correspond au bâtiment étudié."
    return best[1]


def _apply_budget(objects, warnings):
    """Plafond de triangles : terrain d'abord (il porte le relief lointain),
    puis bâtiments et végétation par distance croissante. Ce qui dépasse est
    abandonné (pas conservé retiré : ce serait autant de poids mort)."""
    def dist(o):
        return o['info'].get('distance_m', 0.0)
    ordered = sorted(objects, key=lambda o: (o['kind'] != 'terrain', dist(o)))
    kept, total, dropped = [], 0, {'building': 0, 'vegetation': 0}
    for obj in ordered:
        n = len(obj['triangles'])
        if total + n > MAX_ENV_TRIANGLES and obj['kind'] != 'terrain':
            dropped[obj['kind']] = dropped.get(obj['kind'], 0) + 1
            continue
        kept.append(obj)
        total += n
    if dropped['building'] or dropped['vegetation']:
        warnings.append(
            f"Limite de {MAX_ENV_TRIANGLES} triangles atteinte : {dropped['building']} bâtiment(s) et "
            f"{dropped['vegetation']} arbre(s) les plus éloignés abandonnés — réduire le rayon pour tout garder."
        )
    kept.sort(key=lambda o: o['id'])
    return kept


# ── Lot AI — orthophoto : albédo du sol, absorptance des toitures ──────────────

WATER_ALBEDO = 0.07
DEFAULT_GROUND_ALBEDO = 0.20
ALBEDO_CELL_M = 2.0


class OrthoImage:
    """Orthophotos RVB + infrarouge couleur (IRC) d'une même emprise Lambert 93,
    échantillonnables en coordonnées Lambert 93.

    Albédo solaire « large bande » estimé par pixel : moitié visible (moyenne
    des canaux RVB), moitié proche infrarouge (canal rouge de l'IRC) — le
    rayonnement solaire se partage à peu près en deux entre ces domaines.
    Chaque canal est d'abord ramené à une grandeur linéaire (inverse du gamma
    d'affichage, 2,2). Ce n'est PAS une réflectance calibrée (l'orthophoto est
    corrigée pour l'œil, pas pour la radiométrie) : un ordre de grandeur,
    borné à [0,04 ; 0,80]. Une pelouse, sombre dans le visible mais très claire
    dans l'infrarouge, en ressort correctement plus claire qu'un enrobé — ce
    que le visible seul inverserait."""

    def __init__(self, rgb, irc, bbox_l93):
        self.rgb = rgb
        self.irc = irc
        self.bbox = bbox_l93
        h, w = rgb.shape[:2]
        self.h, self.w = h, w
        lin = lambda a: (a.astype(np.float32) / 255.0) ** 2.2  # noqa: E731
        vis = lin(rgb).mean(axis=2)
        nir = lin(irc[..., 0])
        red = lin(irc[..., 1])
        self.albedo = np.clip(0.5 * vis + 0.5 * nir, 0.04, 0.80)
        self.ndvi = (nir - red) / np.maximum(nir + red, 1e-6)

    def _pix(self, e, n):
        xmin, ymin, xmax, ymax = self.bbox
        col = np.clip(((np.asarray(e) - xmin) / (xmax - xmin) * self.w).astype(int), 0, self.w - 1)
        row = np.clip(((ymax - np.asarray(n)) / (ymax - ymin) * self.h).astype(int), 0, self.h - 1)
        return row, col

    def albedo_at(self, e, n):
        row, col = self._pix(e, n)
        return self.albedo[row, col]

    def ndvi_at(self, e, n):
        row, col = self._pix(e, n)
        return self.ndvi[row, col]


def ground_albedo_grid(ortho, frame, rasters, half_m, cell_m=ALBEDO_CELL_M):
    """Grille d'albédo du sol dans le repère local (maille de 2 m), moyenne de
    16 échantillons d'orthophoto par maille. L'eau vient du LiDAR (classe 9),
    plus fiable que l'image — reflets et ombres y trompent l'albédo apparent.

    Retourne {'half', 'cell', 'n', 'values'} : values[j * n + i], maille (i, j)
    centrée en x = -half + (i + ½)·cell, y = -half + (j + ½)·cell."""
    g = Grid(half_m, cell_m)
    sub = (np.arange(4) + 0.5) / 4.0 - 0.5
    X, Y = g.centers()
    acc = np.zeros_like(X)
    for dx in sub:
        for dy in sub:
            e, n = frame.to_l93(X + dx * cell_m, Y + dy * cell_m)
            acc += ortho.albedo_at(e, n)
    values = acc / (len(sub) ** 2)
    water = rasters.grid.sample(rasters.n_water.astype(float), X.ravel(), Y.ravel(), order=0).reshape(X.shape)
    total = rasters.grid.sample(rasters.n_total.astype(float), X.ravel(), Y.ravel(), order=0).reshape(X.shape)
    values = np.where((total > 0) & (water / np.maximum(total, 1) > 0.5), WATER_ALBEDO, values)
    return {'half': half_m, 'cell': cell_m, 'n': g.n,
            'values': [round(float(v), 3) for v in values.ravel()]}


def albedo_lookup(grid_dict, default=DEFAULT_GROUND_ALBEDO):
    """Fonction (x, y) → albédo pour une grille ground_albedo_grid ; hors grille,
    la moyenne de la grille (le sol au-delà ressemble à celui de la zone)."""
    if not grid_dict or not grid_dict.get('values'):
        return lambda x, y: np.full(np.shape(x), default)
    half, cell, n = grid_dict['half'], grid_dict['cell'], grid_dict['n']
    vals = np.asarray(grid_dict['values'], dtype=float).reshape(n, n)
    mean = float(vals.mean())

    def lookup(x, y):
        i = np.floor((np.asarray(x) + half) / cell).astype(int)
        j = np.floor((np.asarray(y) + half) / cell).astype(int)
        ok = (i >= 0) & (i < n) & (j >= 0) & (j < n)
        out = np.full(np.shape(i), mean)
        out[ok] = vals[j[ok], i[ok]]
        return out
    return lookup


def roof_albedo(poly, ortho, frame):
    """Albédo de toiture relevé sur l'orthophoto : 75ᵉ centile des pixels de
    l'emprise érodée de 0,7 m, hors pixels de végétation (NDVI > 0,3 — arbre en
    surplomb).

    Pourquoi le 75ᵉ centile et non la médiane : l'image est prise sous un soleil
    donné, et le pan d'un toit à deux versants tourné à l'opposé paraît sombre
    sans l'être — la médiane mélangeait les deux pans (tuiles à 0,12 mesurées sur
    un quartier réel, contre 0,16 au 75ᵉ centile ; ardoises 0,09 → 0,12, zinc
    0,26 → 0,48 — ordre conservé, valeurs plus proches des références). La
    calibration de l'albédo lui-même a été vérifiée sur la même image :
    feuillus 0,15, pelouse 0,20, enrobé 0,04, minéral clair 0,44.

    Limite connue : l'orthophoto n'est pas une « vraie ortho », le toit d'un
    bâtiment haut y est déporté de sa base (jusqu'à quelques mètres en bord
    d'image). Retourne None si trop peu de pixels exploitables."""
    inner = poly.buffer(-0.7)
    if inner.is_empty or inner.area < 2.0:
        inner = poly
    xmin, ymin, xmax, ymax = inner.bounds
    xs, ys = np.meshgrid(np.arange(xmin, xmax, 0.4), np.arange(ymin, ymax, 0.4))
    sel = shapely.contains_xy(inner, xs, ys)
    if sel.sum() < 6:
        return None
    e, n = frame.to_l93(xs[sel], ys[sel])
    alb = ortho.albedo_at(e, n)
    ndvi = ortho.ndvi_at(e, n)
    alb = alb[ndvi < 0.3]
    if len(alb) < 6:
        return None
    return round(float(np.percentile(alb, 75)), 3)


def apply_ortho(objects, ortho, frame, rasters, half_m):
    """Complète les objets bâtiment de leur albédo de toiture mesuré et
    retourne la grille d'albédo du sol."""
    for obj in objects:
        if obj['kind'] != 'building' or not obj.get('footprint'):
            continue
        alb = roof_albedo(rings_to_poly(obj['footprint']), ortho, frame)
        if alb is not None:
            obj['info']['roof_albedo'] = alb
    return ground_albedo_grid(ortho, frame, rasters, half_m)


# ── Lot AI — matériaux BD TOPO → parois du catalogue ───────────────────────────

WALL_MATERIALS = {'1': 'pierre', '2': 'meulière', '3': 'béton', '4': 'briques', '5': 'aggloméré',
                  '6': 'bois', '9': 'autres'}
ROOF_MATERIALS = {'1': 'tuiles', '2': 'ardoises', '3': 'zinc aluminium', '4': 'béton', '9': 'autres'}

# Absorptance solaire usuelle du parement extérieur, par matériau (valeurs
# indicatives, même statut que le catalogue de parois). Un parpaing est
# presque toujours enduit, d'où une valeur d'enduit clair.
WALL_ALPHA = {'pierre': 0.55, 'meulière': 0.60, 'béton': 0.65, 'briques': 0.70,
              'aggloméré': 0.50, 'bois': 0.75, 'autres': 0.60}
ROOF_ALPHA = {'tuiles': 0.70, 'ardoises': 0.90, 'zinc aluminium': 0.60, 'béton': 0.75, 'autres': 0.70}


def decode_materials(code, table):
    """Code fichiers fonciers à deux chiffres : chaque chiffre non nul est un
    matériau (« 35 » = béton + aggloméré, « 10 » = pierre, « 00 » = inconnu)."""
    if not code:
        return []
    out = []
    for ch in str(code).strip():
        name = table.get(ch)
        if name and name not in out:
            out.append(name)
    return out


def _era(year):
    if year is None:
        return None
    for limit, era in ((1948, 'ancien'), (1975, '1948-1974'), (1982, '1975-1981'), (1989, '1982-1988'),
                       (2001, '1989-2000'), (2013, '2001-2012'), (2022, '2013-2021')):
        if year < limit:
            return era
    return '2022+'


# Noms EXACTS des modèles du catalogue (seed_paroi_catalogue). Une entrée
# absente du catalogue en base est simplement ignorée par l'appelant.
WALL_BY_ERA = {
    '1975-1981': 'Mur maçonné ITI — 1975–1981 (isolant 40 mm)',
    '1982-1988': 'Mur maçonné ITI — 1982–1988 (isolant 60 mm)',
    '1989-2000': 'Mur maçonné ITI — 1989–2000 (isolant 80 mm)',
    '2001-2012': 'Mur ITI — RT2005 (isolant 100 mm)',
    '2013-2021': 'Mur ITI — RT2012 (isolant 140 mm)',
    '2022+': 'Mur ITI — RE2020 (isolant 180 mm)',
}
ROOF_BY_ERA = {
    'ancien': 'Toiture non isolée (avant 1975)',
    '1948-1974': 'Toiture non isolée (avant 1975)',
    '1975-1981': 'Toiture isolée — 1975–1981 (isolant 60 mm)',
    '1982-1988': 'Toiture isolée — 1982–1988 (isolant 100 mm)',
    '1989-2000': 'Toiture isolée — 1989–2000 (isolant 150 mm)',
    '2001-2012': 'Toiture — RT2005 (isolant 200 mm)',
    '2013-2021': 'Toiture — RT2012 (isolant 300 mm)',
    '2022+': 'Toiture — RE2020 (isolant 400 mm)',
}
FLOOR_BY_ERA = {
    'ancien': 'Plancher bas non isolé (avant 1975)',
    '1948-1974': 'Plancher bas non isolé (avant 1975)',
    '1975-1981': 'Plancher bas sur terre-plein — RT2005 (isolant 40 mm)',
    '1982-1988': 'Plancher bas sur terre-plein — RT2005 (isolant 40 mm)',
    '1989-2000': 'Plancher bas sur terre-plein — RT2005 (isolant 40 mm)',
    '2001-2012': 'Plancher bas sur terre-plein — RT2005 (isolant 40 mm)',
    '2013-2021': 'Plancher bas sur terre-plein — RT2012 (isolant 80 mm)',
    '2022+': 'Plancher bas sur terre-plein — RE2020 (isolant 120 mm)',
}


def _wall_model(era, materials):
    main = materials[0] if materials else None
    if main == 'bois':
        return 'Mur pan de bois / torchis (avant 1948)' if era == 'ancien' else 'Mur ossature bois (isolant 100 mm)'
    if era == 'ancien':
        if main == 'briques':
            return 'Mur brique pleine 34 cm (avant 1948)'
        return 'Mur pierre 50 cm (avant 1948)'
    if era == '1948-1974':
        if main in ('pierre', 'meulière'):
            return 'Mur pierre 50 cm (avant 1948)'
        if main == 'béton':
            return 'Mur béton banché 16 cm non isolé (1948–1974)'
        return 'Mur parpaing 20 cm non isolé (1948–1974)'
    if era is None:
        # Sans année : le matériau seul départage l'ancien (pierre, brique) du
        # reste, qu'on ne sait pas dater — aucune suggestion plutôt qu'une
        # isolation inventée.
        if main in ('pierre', 'meulière'):
            return 'Mur pierre 50 cm (avant 1948)'
        if main == 'briques':
            return 'Mur brique pleine 34 cm (avant 1948)'
        return None
    return WALL_BY_ERA.get(era)


def suggest_materials(info):
    """Suggestion de parois et d'absorptances pour un bâtiment, à partir des
    attributs BD TOPO (matériaux, année) et de l'albédo de toiture mesuré.
    Retourne {'wall', 'roof', 'floor' (noms du catalogue ou None),
    'wall_alpha', 'roof_alpha', 'basis' (explication lisible)}."""
    walls = decode_materials(info.get('mat_murs'), WALL_MATERIALS)
    roofs = decode_materials(info.get('mat_toit'), ROOF_MATERIALS)
    year = info.get('annee')
    era = _era(year)
    roof_alb = info.get('roof_albedo')
    if roof_alb is not None:
        roof_alpha = round(min(max(1.0 - roof_alb, 0.30), 0.95), 2)
        roof_alpha_src = f"mesurée sur l'orthophoto (albédo {roof_alb:.2f})"
    elif roofs:
        roof_alpha = ROOF_ALPHA[roofs[0]]
        roof_alpha_src = f"usuelle pour {roofs[0]}"
    else:
        roof_alpha, roof_alpha_src = None, None
    wall_alpha = WALL_ALPHA[walls[0]] if walls else None

    parts = []
    parts.append(f"construit en {year}" if year else "année inconnue")
    parts.append(f"murs : {', '.join(walls)}" if walls else "matériau des murs inconnu")
    parts.append(f"toiture : {', '.join(roofs)}" if roofs else "matériau de toiture inconnu")
    if roof_alpha_src:
        parts.append(f"absorptance du toit {roof_alpha:.2f}, {roof_alpha_src}")
    return {
        'wall': _wall_model(era, walls), 'roof': ROOF_BY_ERA.get(era), 'floor': FLOOR_BY_ERA.get(era),
        'wall_alpha': wall_alpha, 'roof_alpha': roof_alpha, 'era': era, 'basis': ' ; '.join(parts) + '.',
    }


# ── Lot AI — plusieurs objets = un seul bâtiment ───────────────────────────────

def merge_objects(objs):
    """Un bâtiment réel découpé en plusieurs emprises BD TOPO (fréquent : corps
    principal, extension, garage accolé) doit être étudié comme UN volume : les
    murs entre ces parties sont intérieurs. Assembler les maillages tels quels
    garderait ces murs comme des parois exposées à l'extérieur — erreur
    directe sur les déperditions.

    On reconstruit donc une enveloppe neuve sur l'UNION des emprises (jours de
    quelques centimètres entre emprises voisines refermés), avec pour toiture
    les hauteurs des toitures des parties, relevées par lancer de rayons
    vertical sur leurs maillages : les ressauts entre parties deviennent des
    murs extérieurs seulement au-dessus du toit le plus bas, ce qui est exact.
    Retourne (vertices, triangles, polygon, info)."""
    import trimesh

    polys = [rings_to_poly(o['footprint']) for o in objs if o.get('footprint')]
    if len(polys) != len(objs):
        raise ObservedEnvError("Un des objets sélectionnés n'a pas d'emprise.")
    union = shapely.ops.unary_union([p.buffer(0.3, join_style=2) for p in polys]).buffer(-0.3, join_style=2)
    if union.geom_type != 'Polygon':
        raise ObservedEnvError(
            "Les bâtiments sélectionnés ne se touchent pas : ils ne forment pas un seul volume."
        )
    union = orient(union.simplify(0.1, preserve_topology=True), 1.0)

    verts, faces = [], []
    for o in objs:
        base = len(verts)
        verts.extend(o['vertices'])
        faces.extend([t['v'][0] + base, t['v'][1] + base, t['v'][2] + base] for t in o['triangles'])
    mesh = trimesh.Trimesh(np.asarray(verts, float), np.asarray(faces), process=False)
    base_z = float(min(v[2] for o in objs for v in o['vertices']))
    top = float(max(v[2] for o in objs for v in o['vertices'])) + 10.0

    inner = union.buffer(-0.3)
    if inner.is_empty:
        inner = union
    xmin, ymin, xmax, ymax = inner.bounds
    xs, ys = np.meshgrid(np.arange(xmin, xmax, 0.5), np.arange(ymin, ymax, 0.5))
    sel = shapely.contains_xy(inner, xs, ys)
    pts = np.column_stack([xs[sel], ys[sel]])
    origins = np.column_stack([pts, np.full(len(pts), top)])
    locs, ray_idx, _ = mesh.ray.intersects_location(origins, np.tile([0, 0, -1.0], (len(pts), 1)),
                                                    multiple_hits=False)
    rz = np.full(len(pts), np.nan)
    rz[ray_idx] = locs[:, 2]
    ok = ~np.isnan(rz)
    rxy, rz = pts[ok], rz[ok]
    if len(rz) < 8:
        raise ObservedEnvError("Toiture des bâtiments sélectionnés introuvable.")
    if len(rz) > 800:
        keep = np.arange(len(rz))[::2]
        rxy, rz = rxy[keep], rz[keep]

    budget = int(min(max(union.area / 6.0, 30), 300))
    rings3d, interior, tris, _n = roof_surface(union, (rxy, rz), _eave_sampler(rxy, rz), 0.3, budget,
                                               float(np.median(rz)))
    original = [_ring_coords(union.exterior)] + [_ring_coords(r) for r in union.interiors]
    groups = _edge_groups_for(rings3d, original)
    vertices, triangles = solid_mesh(rings3d, interior, tris, base_z, groups)

    # Attributs : ceux de la plus grande partie (matériaux, année, albédo de
    # toiture pondéré par l'aire des parties qui en ont un).
    areas = [p.area for p in polys]
    main = objs[int(np.argmax(areas))]
    info = {k: main['info'].get(k) for k in ('mat_murs', 'mat_toit', 'annee', 'usage', 'nature')}
    albs = [(o['info'].get('roof_albedo'), a) for o, a in zip(objs, areas) if o['info'].get('roof_albedo') is not None]
    if albs:
        info['roof_albedo'] = round(sum(v * a for v, a in albs) / sum(a for _v, a in albs), 3)
    info.update({'merged_ids': [o['id'] for o in objs], 'base_z': round(base_z, 2),
                 'height_m': round(max(v[2] for v in vertices) - base_z, 2)})
    return vertices, triangles, union, info
