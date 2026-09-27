"""Façades du bâtiment étudié à partir des photos de rue Panoramax (Lot AK) :
texture redressée de chaque façade, détection des vitrages, et intégration des
vrais vitrages dans le maillage.

Source : Panoramax (IGN × OpenStreetMap France), photos 360° équirectangulaires
sous licence CC-BY-SA 4.0, API ouverte sans clé. Vérifié le 2026-09-27 : chaque
photo porte sa position (précision annoncée ~4 m), le cap du centre de l'image
(`view:azimuth`), son inclinaison (`pers:pitch`) et des versions HD/SD.

Géométrie : repère local du bâtiment (Z-up, mètres, +Y = nord vrai tourné de
north_offset_deg, voir observed_env.LocalFrame). Une façade = un groupe de murs
`mur_k` (plan vertical) ; coordonnées de façade (s, t) : s horizontal, de gauche
à droite vu de l'extérieur, t = hauteur (z), en mètres.
"""

import io
import math
import os
import time

import numpy as np
import requests
import shapely.geometry as sg
import shapely.ops
from scipy import ndimage

PANORAMAX_SEARCH_URL = 'https://api.panoramax.xyz/api/search'
USER_AGENT = 'bilan-thermique-lab/1.0 (usage interne)'
CACHE_DIR = '/tmp/bilan-thermique-facades'
CAMERA_HEIGHT_M = 2.2
TEXTURE_M_PER_PX = 0.04
TEXTURE_MAX_PX = 1600


class FacadeError(ValueError):
    pass


# ── Géométrie des façades ──────────────────────────────────────────────────────

def facade_planes(vertices, triangles, min_area=2.0):
    """Une entrée par groupe de murs exposé (`mur_k`, hors mitoyens) : plan,
    repère (s, t) et contour dans ce repère.

    u = direction horizontale « vers la droite » vu de l'extérieur, soit
    (−n_y, n_x, 0) pour une normale sortante n ; v = +Z."""
    V = np.asarray(vertices, dtype=float)
    groups = {}
    for idx, tri in enumerate(triangles):
        g = tri.get('group') or ''
        if g.startswith('mur_') and not g.endswith('_mitoyen'):
            groups.setdefault(g, []).append(idx)
    planes = {}
    for g, idxs in groups.items():
        area_n = np.zeros(3)
        for i in idxs:
            a, b, c = V[triangles[i]['v']]
            area_n += 0.5 * np.cross(b - a, c - a)
        area = float(np.linalg.norm(area_n))
        if area < min_area:
            continue
        n = area_n / area
        n[2] = 0.0
        if np.linalg.norm(n) < 0.5:
            continue
        n /= np.linalg.norm(n)
        u = np.array([-n[1], n[0], 0.0])
        pts = V[sorted({j for i in idxs for j in triangles[i]['v']})]
        s = pts @ u
        origin = np.array([0.0, 0.0, 0.0])
        s0, s1 = float(s.min()), float(s.max())
        t0, t1 = float(pts[:, 2].min()), float(pts[:, 2].max())
        # point de référence : projection du coin bas-gauche sur le plan
        d = float(pts[0] @ n)
        origin = u * s0 + n * d
        origin[2] = t0
        polys = []
        for i in idxs:
            P = V[triangles[i]['v']]
            ring = [(float((p - origin) @ u), float(p[2] - t0)) for p in P]
            poly = sg.Polygon(ring)
            if poly.is_valid and poly.area > 1e-6:
                polys.append(poly)
        outline = shapely.ops.unary_union(polys) if polys else None
        planes[g] = {
            'group': g, 'origin': origin.tolist(), 'u': u.tolist(), 'n': n.tolist(),
            'width': s1 - s0, 'height': t1 - t0, 'area': area,
            'outline': outline,
        }
    return planes


# ── Panoramax ──────────────────────────────────────────────────────────────────

def search_panoramas(bbox_wgs84, limit=400):
    """(lat_min, lon_min, lat_max, lon_max) → photos 360° de la zone."""
    lat_min, lon_min, lat_max, lon_max = bbox_wgs84
    try:
        resp = requests.get(PANORAMAX_SEARCH_URL, params={
            'bbox': f'{lon_min},{lat_min},{lon_max},{lat_max}', 'limit': str(limit),
        }, headers={'User-Agent': USER_AGENT}, timeout=40)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise FacadeError(f"Panoramax injoignable ({exc}).") from exc
    out = []
    for f in data.get('features', []):
        p = f.get('properties') or {}
        fov = (p.get('pers:interior_orientation') or {}).get('field_of_view')
        if fov != 360 or p.get('view:azimuth') is None:
            continue
        lon, lat = f['geometry']['coordinates'][:2]
        assets = f.get('assets') or {}
        out.append({
            'id': f['id'], 'lat': lat, 'lon': lon, 'sequence': f.get('collection'),
            'heading': float(p['view:azimuth']), 'pitch': float(p.get('pers:pitch') or 0.0),
            'roll': float(p.get('pers:roll') or 0.0),
            'datetime': p.get('datetime'), 'license': p.get('license'),
            'producer': p.get('geovisio:producer'),
            'hd': (assets.get('hd') or {}).get('href'), 'sd': (assets.get('sd') or {}).get('href'),
            'accuracy_m': p.get('quality:horizontal_accuracy'),
        })
    return out


def load_panorama(pano, quality='hd'):
    """Image équirectangulaire (h, w, 3) uint8, mise en cache disque."""
    from PIL import Image
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{pano['id']}_{quality}.jpg")
    if not os.path.exists(path):
        url = pano.get(quality) or pano.get('sd')
        if not url:
            raise FacadeError("Photo Panoramax sans image téléchargeable.")
        last = None
        for attempt in range(3):
            try:
                resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=90)
                resp.raise_for_status()
                with open(path, 'wb') as fh:
                    fh.write(resp.content)
                break
            except requests.RequestException as exc:
                last = exc
                time.sleep(1 + attempt)
        else:
            raise FacadeError(f"Téléchargement Panoramax impossible ({last}).")
    return np.asarray(Image.open(path).convert('RGB'))


# ── Projection équirectangulaire ───────────────────────────────────────────────

def _true_enu(d_local, north_offset_deg):
    """Vecteurs du repère local → (est, nord, haut) vrais : inverse de la
    rotation geodata._rotate_xy(·, north_offset)."""
    th = math.radians(-north_offset_deg)
    c, s = math.cos(th), math.sin(th)
    x, y = d_local[..., 0], d_local[..., 1]
    return np.stack([x * c - y * s, x * s + y * c, d_local[..., 2]], axis=-1)


def project_to_pano(points_local, cam_local, heading_deg, pitch_deg, north_offset_deg, img_w, img_h):
    """Points du repère local → pixels (x, y) de la photo équirectangulaire.

    Le centre de l'image regarde le cap `heading` (degrés depuis le nord,
    sens horaire), incliné de `pitch` au-dessus de l'horizon."""
    d = _true_enu(np.asarray(points_local, dtype=float) - np.asarray(cam_local, dtype=float), north_offset_deg)
    h = math.radians(heading_deg)
    p = math.radians(pitch_deg)
    f = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
    r = np.array([math.cos(h), -math.sin(h), 0.0])
    up = np.cross(r, f)
    xc, yc, zc = d @ r, d @ f, d @ up
    yaw = np.arctan2(xc, yc)
    pitch = np.arctan2(zc, np.hypot(xc, yc))
    px = ((yaw / (2 * math.pi) + 0.5) % 1.0) * img_w
    py = (0.5 - pitch / math.pi) * img_h
    return px, py


def rectify(plane, pano_img, cam_local, heading, pitch, north_offset_deg, m_per_px=TEXTURE_M_PER_PX):
    """Texture redressée de la façade (h, w, 3) uint8, ligne 0 = HAUT."""
    W, H = plane['width'], plane['height']
    scale = max(m_per_px, max(W, H) / TEXTURE_MAX_PX)
    nw, nh = max(8, int(round(W / scale))), max(8, int(round(H / scale)))
    s = (np.arange(nw) + 0.5) * W / nw
    t = H - (np.arange(nh) + 0.5) * H / nh
    S, T = np.meshgrid(s, t)
    o = np.asarray(plane['origin']); u = np.asarray(plane['u'])
    P = o[None, None, :] + S[..., None] * u[None, None, :]
    P[..., 2] = o[2] + T
    ih, iw = pano_img.shape[:2]
    px, py = project_to_pano(P.reshape(-1, 3), cam_local, heading, pitch, north_offset_deg, iw, ih)
    out = np.empty((nh * nw, 3), dtype=np.uint8)
    coords = [py - 0.5, px - 0.5]
    for ch in range(3):
        out[:, ch] = ndimage.map_coordinates(pano_img[..., ch], coords, order=1, mode='wrap')
    return out.reshape(nh, nw, 3), scale


# ── Recalage de la prise de vue ────────────────────────────────────────────────

def model_edge_points(objects, cam_xy, radius=35.0, step=0.25):
    """Points 3D échantillonnés le long des arêtes de façade (contours des murs :
    coins verticaux, égouts, pieds) des bâtiments proches de la caméra, côté
    caméra seulement — repères précis au centimètre (LiDAR) sur lesquels caler
    la photo, dont le GPS n'est précis qu'à ~4 m. Retourne (points (N, 3),
    vertical (N,) booléen : arête plutôt verticale que horizontale)."""
    pts, vert, inner = [], [], []
    cx, cy = cam_xy
    for o in objects:
        if o.get('kind') != 'building' or o.get('status') == 'removed' or not o.get('footprint'):
            continue
        ring = np.asarray(o['footprint'][0], dtype=float)
        if np.min(np.hypot(ring[:, 0] - cx, ring[:, 1] - cy)) > radius:
            continue
        for pl in facade_planes(o['vertices'], o['triangles']).values():
            n = np.asarray(pl['n'])
            mid = np.asarray(pl['origin']) + np.asarray(pl['u']) * pl['width'] / 2
            if (np.array([cx - mid[0], cy - mid[1]]) @ n[:2]) <= 0.5:
                continue
            outline = pl['outline']
            if outline is None or outline.is_empty:
                continue
            o3 = np.asarray(pl['origin']); u = np.asarray(pl['u'])
            # Points INTÉRIEURS de la façade (maille 0,7 m, bords exclus) : ils
            # ne doivent jamais tomber sur du ciel.
            core = outline.buffer(-0.4)
            if not core.is_empty:
                xmin, ymin, xmax, ymax = core.bounds
                for ss in np.arange(xmin, xmax, 0.7):
                    for tt in np.arange(ymin, ymax, 0.7):
                        if core.contains(sg.Point(ss, tt)):
                            q = o3 + u * ss
                            q[2] = o3[2] + tt
                            inner.append(q)
            for gm in getattr(outline, 'geoms', [outline]):
                coords = list(gm.exterior.coords)
                for (s0, t0), (s1, t1) in zip(coords[:-1], coords[1:]):
                    length = math.hypot(s1 - s0, t1 - t0)
                    if length < 0.8:
                        continue
                    is_vert = abs(t1 - t0) > abs(s1 - s0)
                    for k in range(max(int(length / step), 2)):
                        a = (k + 0.5) / max(int(length / step), 2)
                        q = o3 + u * (s0 + a * (s1 - s0))
                        q[2] = o3[2] + t0 + a * (t1 - t0)
                        pts.append(q)
                        vert.append(is_vert)
    if not pts:
        return np.zeros((0, 3)), np.zeros(0, dtype=bool), np.zeros((0, 3))
    return np.asarray(pts), np.asarray(vert, dtype=bool), np.asarray(inner) if inner else np.zeros((0, 3))


def _edge_maps(img):
    """Gradients horizontal (|∂/∂x|, contours verticaux) et vertical (|∂/∂y|,
    contours horizontaux), végétation masquée : un arbre, très texturé, offre
    bien plus de contours qu'une façade et attirait le recalage (constaté)."""
    f = img.astype(np.float32)
    g = ndimage.gaussian_filter(f.mean(axis=2), 1.0)
    gx = np.abs(ndimage.sobel(g, axis=1))
    gy = np.abs(ndimage.sobel(g, axis=0))
    r, gr, b = f[..., 0], f[..., 1], f[..., 2]
    green = (gr > r * 1.05) & (gr > b * 1.05) & (gr - np.minimum(r, b) > 12)
    green = ndimage.binary_dilation(green, iterations=2)
    gx[green] = 0.0
    gy[green] = 0.0
    gx = ndimage.gaussian_filter(gx, 1.5)
    gy = ndimage.gaussian_filter(gy, 1.5)
    norm = 0.5 * (gx.mean() + gy.mean()) + 1e-6
    # Ciel : bleu dominant et lumineux, ou très clair et peu saturé, peu texturé.
    lum = f.mean(axis=2)
    texture = ndimage.gaussian_filter(np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1)), 2.0)
    sky = (((b > r + 15) & (b >= gr) & (lum > 110)) | ((lum > 200) & (f.max(axis=2) - f.min(axis=2) < 25))) \
        & (texture < np.percentile(texture, 60))
    return gx / norm, gy / norm, sky.astype(np.float32)


def edge_scorer(pano_img, edges, cam0, heading0, pitch, north_offset_deg, sky_weight=4.0, downsample=2):
    """Fonction raw(dx, dy, dz, dh, dp=0) : alignement des arêtes du modèle sur les
    contours de l'image, de MÊME orientation (verticale sur verticale,
    horizontale sur horizontale), végétation masquée, façade interdite dans le
    ciel. None si trop peu d'arêtes visibles."""
    edge_pts, vert, inner_pts = edges
    if len(edge_pts) < 30:
        return None
    small = pano_img[::downsample, ::downsample] if downsample > 1 else pano_img
    gx, gy, sky = _edge_maps(small)
    ih, iw = gx.shape

    def raw(dx, dy, dz, dh, dp=0.0):
        cam = (cam0[0] + dx, cam0[1] + dy, cam0[2] + dz)
        px, py = project_to_pano(edge_pts, cam, heading0 + dh, pitch + dp, north_offset_deg, iw, ih)
        c = [py - 0.5, px - 0.5]
        vx = ndimage.map_coordinates(gx, c, order=1, mode='wrap')
        vy = ndimage.map_coordinates(gy, c, order=1, mode='wrap')
        value = float(np.where(vert, vx, vy).mean())
        if len(inner_pts):
            qx, qy = project_to_pano(inner_pts, cam, heading0 + dh, pitch + dp, north_offset_deg, iw, ih)
            value -= sky_weight * float(ndimage.map_coordinates(sky, [qy - 0.5, qx - 0.5], order=0, mode='wrap').mean())
        return value
    return raw


def register_camera(pano_img, edges, cam0, heading0, pitch, north_offset_deg,
                    xy_range=2.0, heading_range=3.0, z_range=0.0, sigma_m=2.0, prior_weight=1.0,
                    sky_weight=4.0):
    """Recalage d'UNE photo seule (Lot AK) — remplacé pour l'analyse par le
    recalage par séquence (facade_texture.register_sequences). Rappel gaussien
    vers la position GPS. Retourne (cam, heading, score, score_initial)."""
    raw = edge_scorer(pano_img, edges, cam0, heading0, pitch, north_offset_deg, sky_weight)
    if raw is None:
        return list(cam0), heading0, 0.0, 0.0

    def score(dx, dy, dz, dh):
        return raw(dx, dy, dz, dh) - prior_weight * (dx * dx + dy * dy) / (2 * sigma_m ** 2)

    initial = raw(0, 0, 0, 0)
    best = (score(0, 0, 0, 0), 0.0, 0.0, 0.0, 0.0)
    for dx in np.arange(-xy_range, xy_range + 1e-6, 1.0):
        for dy in np.arange(-xy_range, xy_range + 1e-6, 1.0):
            for dh in np.arange(-heading_range, heading_range + 1e-6, 1.0):
                sc = score(dx, dy, 0.0, dh)
                if sc > best[0]:
                    best = (sc, dx, dy, 0.0, dh)
    for _round in range(2):
        _s, bx, by, bz, bh = best
        for dx in (bx - 0.5, bx - 0.25, bx, bx + 0.25, bx + 0.5):
            for dy in (by - 0.5, by - 0.25, by, by + 0.25, by + 0.5):
                for dz in (np.arange(bz - z_range, bz + z_range + 1e-6, 0.4) if z_range > 0 else (bz,)):
                    for dh in (bh - 0.5, bh, bh + 0.5):
                        sc = score(dx, dy, dz, dh)
                        if sc > best[0]:
                            best = (sc, dx, dy, dz, dh)
    _sc, dx, dy, dz, dh = best
    return [cam0[0] + dx, cam0[1] + dy, cam0[2] + dz], heading0 + dh, raw(dx, dy, dz, dh), initial


# ── Détection des vitrages ─────────────────────────────────────────────────────

def detect_windows(texture, m_per_px, outline=None, facade_height=None):
    """Rectangles de baies [s0, t0, s1, t1] (mètres, repère de façade) sur une
    texture redressée (ligne 0 = haut).

    Principe (sans modèle appris) : la couleur du mur est ce qui domine la
    façade ; une baie s'en distingue — vitre sombre ou reflet, volet d'une autre
    teinte — et forme un rectangle de taille plausible. On écarte ce qui n'est
    pas la façade (végétation, ciel) et la bande basse, où portes, murets,
    clôtures et voitures ressemblent à des baies sans en être. Heuristique
    assumée : le résultat est montré à l'utilisateur, qui le vérifie (vue sans
    texture, vitrages en couleur) et peut revenir à une proportion.
    Retourne (baies, masque de façade utile (fraction), rapport)."""
    img = texture.astype(np.float32)
    h, w = img.shape[:2]
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    lum = img.mean(axis=2)
    green = (g > r * 1.05) & (g > b * 1.05) & (g - np.minimum(r, b) > 12)
    sky = ((b > r + 15) & (b >= g) & (lum > 110)) | ((lum > 215) & (img.max(axis=2) - img.min(axis=2) < 20))
    valid = ~ndimage.binary_dilation(green | sky, iterations=2)
    if outline is not None:
        yy, xx = np.mgrid[0:h, 0:w]
        s = (xx + 0.5) * m_per_px
        t = (h - yy - 0.5) * m_per_px
        import shapely
        valid &= shapely.contains_xy(outline.buffer(-0.1), s, t)
    usable = float(valid.mean())
    if valid.sum() < 200:
        return [], usable
    wall = np.median(img[valid], axis=0)
    dist = np.linalg.norm(img - wall[None, None, :], axis=2)
    mad = np.median(np.abs(dist[valid] - np.median(dist[valid]))) + 1e-3
    cand = valid & (dist > np.median(dist[valid]) + 4.0 * mad) & (dist > 28)
    k = max(1, int(round(0.08 / m_per_px)))
    cand = ndimage.binary_closing(cand, structure=np.ones((2 * k + 1, 2 * k + 1)))
    cand = ndimage.binary_opening(cand, structure=np.ones((2 * k + 1, 2 * k + 1)))
    cand = ndimage.binary_fill_holes(cand)
    labels, n = ndimage.label(cand)
    windows = []
    bottom_band = 0.35
    for idx, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        comp = labels[sl] == idx
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        bw, bh = (x1 - x0) * m_per_px, (y1 - y0) * m_per_px
        fill = comp.sum() / comp.size
        s0, s1 = x0 * m_per_px, x1 * m_per_px
        t1, t0 = (h - y0) * m_per_px, (h - y1) * m_per_px
        if not (0.4 <= bw <= 4.0 and 0.5 <= bh <= 3.0 and bw * bh >= 0.25):
            continue
        if fill < 0.55 or not (0.25 <= bh / bw <= 4.0):
            continue
        if t0 < bottom_band:
            continue
        if facade_height is not None and t1 > facade_height - 0.15:
            continue
        windows.append([round(s0, 2), round(t0, 2), round(s1, 2), round(t1, 2)])
    return windows, usable


# ── Détecteur de baies : OWL-ViT (Google, Apache 2.0), ONNX quantifié ──────────
# Historique des essais sur façades réelles (Lots AK–AL, 2026-09-27) : règle de
# couleur (volets clairs manqués, trouées de haie prises pour des baies) ;
# SegFormer/ADE20K (aucune fenêtre) ; OWLv2 (bon, mais ~30–50 s par passe sur
# 2 cœurs — une façade large découpée en tuiles coûtait plus de 4 min) ;
# OWL-ViT v1 patch 32 (~6 s par passe, entrée 768 px en 24 × 24 zones) :
# moins bon sur une façade entière, meilleur qu'OWLv2 entier sur TUILES
# d'environ 6 m, où les baies occupent assez de place dans l'image.
OWL_MODELS = {
    'v1': {'repo': 'https://huggingface.co/Xenova/owlvit-base-patch32/resolve/main',
           'dir': os.path.join(CACHE_DIR, 'owlvit32'), 'size': 768},
    'v2': {'repo': 'https://huggingface.co/Xenova/owlv2-base-patch16-ensemble/resolve/main',
           'dir': os.path.join(CACHE_DIR, 'owlv2'), 'size': 960},
}
OWL_FILES = {'model_quantized.onnx': 'onnx/model_quantized.onnx', 'tokenizer.json': 'tokenizer.json'}
OWL_QUERIES = [('window', 'a window'), ('window', 'a window shutter'), ('door', 'a door')]
OWL_MEAN = np.array([0.48145466, 0.4578275, 0.40821073])
OWL_STD = np.array([0.26862954, 0.26130258, 0.27577711])
OWL_THRESHOLDS = {'v1': {'window': 0.2, 'door': 0.25}, 'v2': {'window': 0.15, 'door': 0.20}}
OWL_TILE_M = 6.0
# Complément OWLv2 (façade entière) quand OWL-ViT a trouvé moins de 10 % de
# vitrage sur une façade assez vue (proportion courante d'un logement : 15 à
# 25 %) : constaté sur un collectif clair en plein soleil, où OWL-ViT ne
# propose aucune fenêtre et OWLv2 les trouve. Les deux listes sont réunies.
OWL_V2_MIN_SEEN_M2 = 8.0
OWL_V2_BELOW_RATIO = 0.10
_owl = {}


def _owl_session(kind='v1'):
    """Session ONNX et jetons des requêtes, chargés une fois par processus.
    Chaque modèle (~155 Mo) est téléchargé au premier usage dans le cache local."""
    if kind in _owl:
        return _owl[kind]
    cfg = OWL_MODELS[kind]
    model_dir, repo = cfg['dir'], cfg['repo']
    import onnxruntime as ort
    from tokenizers import Tokenizer
    os.makedirs(model_dir, exist_ok=True)
    for name, remote in OWL_FILES.items():
        path = os.path.join(model_dir, name)
        if os.path.exists(path):
            continue
        try:
            with requests.get(f'{repo}/{remote}', stream=True, timeout=120,
                              headers={'User-Agent': USER_AGENT}) as resp:
                resp.raise_for_status()
                tmp = path + '.part'
                with open(tmp, 'wb') as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
                os.replace(tmp, path)
        except requests.RequestException as exc:
            raise FacadeError(f"Modèle de détection indisponible ({exc}).") from exc
    sess = ort.InferenceSession(os.path.join(model_dir, 'model_quantized.onnx'), providers=['CPUExecutionProvider'])
    tok = Tokenizer.from_file(os.path.join(model_dir, 'tokenizer.json'))
    L = 16
    ids = np.zeros((len(OWL_QUERIES), L), dtype=np.int64)
    mask = np.zeros_like(ids)
    for k, (_label, q) in enumerate(OWL_QUERIES):
        e = tok.encode(q).ids[:L]
        ids[k, :len(e)] = e
        mask[k, :len(e)] = 1
    _owl[kind] = (sess, ids, mask)
    return _owl[kind]


def _merge_boxes(boxes, scores, labels, gap_px):
    """Regroupe les détections d'une même baie. Constaté sur façade réelle :
    OWLv2 encadre une fenêtre à volets à la fois en entier, par vantail, et
    par carreau — trois cadres emboîtés, ou deux cadres côte à côte. Un cadre
    contenu (aux 3/4) dans un autre est absorbé ; deux cadres de même nature,
    contigus (écart < gap_px) et de même hauteur à 30 % près, sont réunis."""
    items = [[list(b), s_, l_] for b, s_, l_ in zip(boxes, scores, labels)]
    changed = True
    while changed:
        changed = False
        for i in range(len(items)):
            for j in range(len(items)):
                if i == j or items[i] is None or items[j] is None:
                    continue
                a, b = items[i][0], items[j][0]
                if items[i][2] != items[j][2]:
                    continue
                ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
                iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                area_b = (b[2] - b[0]) * (b[3] - b[1])
                contained = area_b > 0 and ix * iy / area_b > 0.75
                ha, hb = a[3] - a[1], b[3] - b[1]
                side_by_side = (iy > 0.7 * min(ha, hb) and abs(ha - hb) < 0.3 * max(ha, hb)
                                and (max(a[0], b[0]) - min(a[2], b[2])) < gap_px)
                if contained or side_by_side:
                    items[i][0] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    items[i][1] = max(items[i][1], items[j][1])
                    items[j] = None
                    changed = True
        items = [x for x in items if x is not None]
    return [tuple(x[0]) for x in items], [x[1] for x in items], [x[2] for x in items]


def _nms(boxes, scores, iou=0.3):
    order = np.argsort(-scores)
    keep = []
    for i in order:
        ok = True
        for j in keep:
            xa, ya = max(boxes[i][0], boxes[j][0]), max(boxes[i][1], boxes[j][1])
            xb, yb = min(boxes[i][2], boxes[j][2]), min(boxes[i][3], boxes[j][3])
            inter = max(0.0, xb - xa) * max(0.0, yb - ya)
            ua = (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]) + \
                 (boxes[j][2] - boxes[j][0]) * (boxes[j][3] - boxes[j][1]) - inter
            if ua > 0 and inter / ua > iou:
                ok = False
                break
        if ok:
            keep.append(i)
    return keep


def _owl_run(canvas, kind='v1'):
    """Une passe OWL-ViT (v1) ou OWLv2 (v2) sur une image carrée uint8 → (boîtes px x0,y0,x1,y1,
    scores, étiquettes) au-dessus des seuils."""
    from PIL import Image
    sess, ids, mask = _owl_session(kind)
    size = OWL_MODELS[kind]['size']
    side = canvas.shape[0]
    x = np.asarray(Image.fromarray(canvas).resize((size, size), Image.BILINEAR)) / 255.0
    x = ((x - OWL_MEAN) / OWL_STD).transpose(2, 0, 1)[None].astype(np.float32)
    names = [o.name for o in sess.get_outputs()]
    res = sess.run(None, {'input_ids': ids, 'attention_mask': mask, 'pixel_values': x})
    prob = 1.0 / (1.0 + np.exp(-res[names.index('logits')][0]))
    boxes = res[names.index('pred_boxes')][0] * side
    out = []
    for q, (label, _text) in enumerate(OWL_QUERIES):
        for i in np.nonzero(prob[:, q] > OWL_THRESHOLDS[kind][label])[0]:
            cx, cy, bw, bh = boxes[i]
            out.append(((cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2), float(prob[i, q]), label))
    return out


def _postprocess(cands, h, w, m_per_px, outline):
    """Détections d'UNE façade (px de sa texture) → baies en mètres (s, t)."""
    cand_boxes, cand_scores, cand_labels = [], [], []
    for (x0, y0, x1, y1), sc, label in cands:
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
        if x1 > x0 and y1 > y0:
            cand_boxes.append((x0, y0, x1, y1)); cand_scores.append(sc); cand_labels.append(label)
    out = []
    if not cand_boxes:
        return out
    cand_boxes, cand_scores, cand_labels = _merge_boxes(cand_boxes, cand_scores, cand_labels,
                                                        gap_px=0.15 / m_per_px)
    core = outline.buffer(-0.05) if outline is not None else None
    for i in _nms(cand_boxes, np.asarray(cand_scores)):
        x0, y0, x1, y1 = cand_boxes[i]
        s0, s1 = x0 * m_per_px, x1 * m_per_px
        t0, t1 = (h - y1) * m_per_px, (h - y0) * m_per_px
        bw, bh = s1 - s0, t1 - t0
        if not (0.3 <= bw <= 5.0 and 0.4 <= bh <= 3.5):
            continue
        if core is not None:
            rect = sg.box(s0, t0, s1, t1)
            if rect.intersection(core).area < 0.7 * rect.area:
                continue
        label = cand_labels[i]
        # (« porte » en étage → fenêtre : décidé dans facade_texture.fix_door_labels,
        # qui sait si le bas du cadre est vu ou caché par une clôture.)
        # Une « porte » de 1,5 m de large ou plus est une baie coulissante
        # vitrée (constaté sur une maison : la baie du séjour sortait en porte).
        if label == 'door' and bw >= 1.5:
            label = 'window'
        out.append({'label': label, 'score': round(cand_scores[i], 3),
                    's0': round(s0, 2), 't0': round(t0, 2), 's1': round(s1, 2), 't1': round(t1, 2)})
    return out


def detect_openings_batch(items):
    """Baies de plusieurs façades, par tuiles carrées d'environ OWL_TILE_M
    (chevauchement d'un quart) sur fond gris ; tuile presque entièrement non
    vue (gris 128 de equalize_seen) sautée. Regrouper plusieurs façades sur
    une même image a été essayé et abandonné : la même façade donnait 5 baies
    seule et 0 en compagnie d'autres (score très dépendant du contexte).
    items : {clé: (texture (h, w, 3) ligne 0 = haut, m_par_px, contour)}.
    Retourne {clé: [baies]}."""
    out = {}
    for key, (tex, scale, outline) in items.items():
        h, w = tex.shape[:2]
        side = max(64, int(round(OWL_TILE_M / scale)))
        stride = max(1, int(side * 0.75))
        unseen = (tex == 128).all(axis=2)
        xs = list(range(0, max(w - side, 0) + 1, stride))
        ys = list(range(0, max(h - side, 0) + 1, stride))
        if xs[-1] + side < w:
            xs.append(w - side)
        if ys[-1] + side < h:
            ys.append(h - side)
        cands = []
        for y0 in ys:
            for x0 in xs:
                sub = tex[y0:y0 + side, x0:x0 + side]
                if (~unseen[y0:y0 + side, x0:x0 + side]).mean() < 0.15:
                    continue
                canvas = np.full((side, side, 3), 128, dtype=np.uint8)
                canvas[:sub.shape[0], :sub.shape[1]] = sub
                for (bx0, by0, bx1, by1), sc, label in _owl_run(canvas):
                    cands.append(((bx0 + x0, by0 + y0, bx1 + x0, by1 + y0), sc, label))
        found = _postprocess(cands, h, w, scale, outline)
        seen_m2 = float((~unseen).sum()) * scale * scale
        glazed = sum((o['s1'] - o['s0']) * (o['t1'] - o['t0']) for o in found if o['label'] == 'window')
        if seen_m2 >= OWL_V2_MIN_SEEN_M2 and glazed < OWL_V2_BELOW_RATIO * seen_m2:
            full = max(h, w, 480)
            canvas = np.full((full, full, 3), 128, dtype=np.uint8)
            canvas[:h, :w] = tex
            found = _postprocess(cands + _owl_run(canvas, 'v2'), h, w, scale, outline)
        # Cadre collé au sommet du mur : garde-corps de terrasse ou rive de
        # toit, pas une fenêtre (constaté sur un collectif à attique).
        top = h * scale
        out[key] = [o for o in found if o['t1'] < top - 0.25]
    return out


def detect_openings(texture, m_per_px, outline=None):
    """Baies d'une texture redressée (ligne 0 = haut) : liste de
    {'label': 'window'|'door', 'score', 's0', 't0', 's1', 't1'} en mètres
    (repère de façade). Une fenêtre ou un volet compte comme vitrage ; une
    porte reste opaque. Filtrage : taille plausible, dans le contour du mur,
    suppression des doublons (NMS)."""
    return detect_openings_batch({0: (texture, m_per_px, outline)})[0]


# ── Choix de la prise de vue pour chaque façade ────────────────────────────────

# Segmentation de scène SegFormer-b0 / ADE20K (ONNX, 15 Mo) : sert à mesurer
# la part de façade réellement visible sur une photo candidate. Une règle de
# couleur manquait les haies sèches (brunes) et ne voyait ni voitures ni
# clôtures (constaté). Poids NVIDIA sous licence non commerciale — usage de
# lab personnel ; repli automatique sur l'heuristique couleur s'il manque.
SEG_REPO = 'https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/resolve/main/onnx/model.onnx'
SEG_PATH = os.path.join(CACHE_DIR, 'segformer', 'model.onnx')
# Classes ADE20K qui SONT la façade : bâtiment, maison, fenêtre, porte,
# gratte-ciel, porte-écran, store, auvent, colonne. PAS « mur » (0) : sur une
# façade redressée, SegFormer range la façade en « bâtiment » et réserve
# « mur » aux murets et clôtures devant elle — le compter faisait préférer une
# photo où un muret remplit le bas de l'image (constaté).
SEG_FACADE = {1, 8, 14, 25, 48, 58, 63, 86, 42}
_seg = {}


def _seg_session():
    if 'sess' in _seg:
        return _seg['sess']
    import onnxruntime as ort
    if not os.path.exists(SEG_PATH):
        os.makedirs(os.path.dirname(SEG_PATH), exist_ok=True)
        resp = requests.get(SEG_REPO, timeout=120, headers={'User-Agent': USER_AGENT})
        resp.raise_for_status()
        with open(SEG_PATH + '.part', 'wb') as fh:
            fh.write(resp.content)
        os.replace(SEG_PATH + '.part', SEG_PATH)
    _seg['sess'] = ort.InferenceSession(SEG_PATH, providers=['CPUExecutionProvider'])
    return _seg['sess']


def facade_visibility_mask(texture):
    """Masque booléen (h, w) : vrai là où la texture montre la façade elle-même."""
    from PIL import Image
    sess = _seg_session()
    h, w = texture.shape[:2]
    x = np.asarray(Image.fromarray(texture).resize((512, 512), Image.BILINEAR)) / 255.0
    x = ((x - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])).transpose(2, 0, 1)
    logits = sess.run(None, {sess.get_inputs()[0].name: x[None].astype(np.float32)})[0][0]
    lab = logits.argmax(0).astype(np.uint8)
    lab = np.asarray(Image.fromarray(lab).resize((w, h), Image.NEAREST))
    return np.isin(lab, list(SEG_FACADE))


def occlusion_fraction(texture):
    """Part de la texture qui n'est PAS la façade (végétation, véhicule,
    clôture, chaussée, ciel…) : sert à préférer, parmi plusieurs photos, celle
    qui voit le mur. SegFormer, avec repli sur une règle de couleur."""
    try:
        return float(1.0 - facade_visibility_mask(texture).mean())
    except Exception:  # noqa: BLE001 — modèle indisponible : heuristique
        pass
    f = texture.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    lum = f.mean(axis=2)
    green = (g > r * 1.05) & (g > b * 1.05) & (g - np.minimum(r, b) > 12)
    sky = ((b > r + 15) & (b >= g) & (lum > 110))
    return float((green | sky).mean())


def visible_fraction(plane, cam, intersector):
    """Part de la façade réellement visible depuis la caméra : rayons vers une
    grille de points de la façade ; un point est caché si le premier obstacle
    (bâtiments, relief — y compris le bâtiment lui-même) est touché nettement
    avant lui. Constaté : sans ce test, un pignon caché par la maison voisine
    recevait la photo de CETTE maison, et on y détectait ses fenêtres."""
    if intersector is None:
        return 1.0
    o = np.asarray(plane['origin'], float); u = np.asarray(plane['u'], float); n = np.asarray(plane['n'], float)
    outline = plane.get('outline')
    pts = []
    for fs in np.linspace(0.1, 0.9, 7):
        for ft in np.linspace(0.1, 0.9, 5):
            s_, t_ = fs * plane['width'], ft * plane['height']
            if outline is not None and not outline.contains(sg.Point(s_, t_)):
                continue
            q = o + u * s_ + n * 0.05
            q[2] = o[2] + t_
            pts.append(q)
    if not pts:
        return 0.0
    P = np.asarray(pts)
    C = np.tile(np.asarray(cam, float), (len(P), 1))
    D = P - C
    dist = np.linalg.norm(D, axis=1)
    D /= dist[:, None]
    locs, ray_idx, _t = intersector.intersects_location(C, D, multiple_hits=False)
    blocked = np.zeros(len(P), dtype=bool)
    for loc, r in zip(locs, ray_idx):
        if np.linalg.norm(loc - C[r]) < dist[r] - 0.3:
            blocked[r] = True
    return float(1.0 - blocked.mean())


def _occluder(objects):
    import trimesh
    from .shadow import _ray_intersector
    verts, faces = [], []
    for o in objects:
        if o.get('kind') not in ('building', 'terrain') or o.get('status') == 'removed':
            continue
        base = len(verts)
        verts.extend(o['vertices'])
        faces.extend([base + t['v'][0], base + t['v'][1], base + t['v'][2]] for t in o['triangles'])
    if not faces:
        return None
    return _ray_intersector(trimesh.Trimesh(np.asarray(verts, float), np.asarray(faces), process=False))


def candidate_panoramas(plane, panos, max_count=3, min_dist=4.0, max_dist=35.0, max_angle_deg=60.0):
    """Photos qui voient la façade de face ou presque, triées par pertinence
    (distance × obliquité). panos : avec 'xy' local renseigné."""
    n = np.asarray(plane['n'])[:2]
    mid = np.asarray(plane['origin'])[:2] + np.asarray(plane['u'])[:2] * plane['width'] / 2
    cos_min = math.cos(math.radians(max_angle_deg))
    ranked = []
    for p in panos:
        d = np.asarray(p['xy']) - mid
        dist = float(np.linalg.norm(d))
        if not (min_dist <= dist <= max_dist):
            continue
        cos_a = float(d @ n) / dist
        if cos_a < cos_min:
            continue
        ranked.append((dist / cos_a, p))
    ranked.sort(key=lambda r: r[0])
    return [p for _c, p in ranked[:max_count]]


def rectify_to_file(plane, pano, cam, heading, north_offset_deg, path, m_per_px=0.03):
    from PIL import Image
    img = load_panorama(pano, 'hd')
    tex, scale = rectify(plane, img, cam, heading, pano['pitch'], north_offset_deg, m_per_px=m_per_px)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(tex).save(path, quality=88)
    return tex, scale


# ── Vrais vitrages dans le maillage ────────────────────────────────────────────

def _group_boundary_ring(vertices, triangles, idxs):
    """Contour (indices de sommets, dans l'ordre) d'un groupe de triangles
    coplanaires : arêtes n'appartenant qu'à un seul triangle du groupe, chaînées
    dans le sens des triangles. Conserver EXACTEMENT ces sommets garantit que
    le mur retriangulé reste raccordé au toit, au sol et aux murs voisins."""
    count = {}
    for i in idxs:
        a, b, c = triangles[i]['v']
        for e in ((a, b), (b, c), (c, a)):
            key = (min(e), max(e))
            count[key] = count.get(key, 0) + 1
    nxt = {}
    for i in idxs:
        a, b, c = triangles[i]['v']
        for u_, v_ in ((a, b), (b, c), (c, a)):
            if count[(min(u_, v_), max(u_, v_))] == 1:
                nxt[u_] = v_
    if not nxt:
        return None
    rings = []
    seen = set()
    for start in nxt:
        if start in seen:
            continue
        ring = [start]
        seen.add(start)
        cur = nxt[start]
        while cur != start:
            if cur in seen or cur not in nxt:
                return None
            ring.append(cur)
            seen.add(cur)
            cur = nxt[cur]
        rings.append(ring)
    if len(rings) != 1:
        return None      # mur percé ou en plusieurs morceaux : non traité
    return rings[0]


def synthetic_openings(plane, ratio, storey_m=2.8, win_w=1.2, win_h=1.35):
    """Fenêtres régulières quand aucune photo ne voit la façade : une rangée
    par étage, centrée à 1,5 m au-dessus du plancher de l'étage, nombre choisi
    pour approcher `ratio` (surface vitrée / surface de la façade)."""
    outline = plane['outline']
    if outline is None or ratio <= 0:
        return []
    core = outline.buffer(-0.3)
    if core.is_empty:
        return []
    target = ratio * outline.area
    n_storeys = max(1, int(plane['height'] // storey_m))
    per_storey = max(0, int(round(target / (win_w * win_h) / n_storeys)))
    out = []
    for k in range(n_storeys):
        t_mid = k * storey_m + 1.5
        if per_storey == 0:
            break
        step = plane['width'] / per_storey
        for j in range(per_storey):
            s_mid = (j + 0.5) * step
            rect = sg.box(s_mid - win_w / 2, t_mid - win_h / 2, s_mid + win_w / 2, t_mid + win_h / 2)
            if core.contains(rect):
                out.append({'label': 'window', 'score': None, 's0': round(s_mid - win_w / 2, 2),
                            't0': round(t_mid - win_h / 2, 2), 's1': round(s_mid + win_w / 2, 2),
                            't1': round(t_mid + win_h / 2, 2), 'synthetic': True})
    return out


def insert_openings(vertices, triangles, group, plane, openings, glazing_model_id, door_model_id=None):
    """Retriangule le mur `group` avec ses baies : chaque vitrage devient un
    trou rectangulaire dans le mur, rempli par deux triangles du groupe
    `vitrage_<k>` (modèle de vitrage) ; une porte, par ceux du groupe
    `porte_<k>` (modèle du mur, ou door_model_id). Le contour du mur est
    conservé sommet pour sommet (voir _group_boundary_ring) : le volume reste
    fermé. Retourne (vertices, triangles, n_baies_insérées) ; le mur est laissé
    tel quel si sa forme n'est pas traitable."""
    from .observed_env import _triangulate_polygon
    idxs = [i for i, t in enumerate(triangles) if t.get('group') == group]
    ring = _group_boundary_ring(vertices, triangles, idxs)
    if ring is None or not openings:
        return vertices, triangles, 0
    o = np.asarray(plane['origin'], dtype=float)
    u = np.asarray(plane['u'], dtype=float)
    n = np.asarray(plane['n'], dtype=float)
    V = np.asarray(vertices, dtype=float)
    ring_st = [(float((V[k] - o) @ u), float(V[k][2] - o[2])) for k in ring]
    wall = sg.Polygon(ring_st)
    if not wall.is_valid or wall.area < 1.0:
        return vertices, triangles, 0
    core = wall.buffer(-0.12)
    rects = []
    for op in sorted(openings, key=lambda a: -((a['s1'] - a['s0']) * (a['t1'] - a['t0']))):
        r = sg.box(op['s0'], op['t0'], op['s1'], op['t1']).intersection(core)
        if r.is_empty or r.area < 0.2:
            continue
        r = sg.box(*r.bounds)
        if any(r.buffer(0.08).intersects(x) for x, _l in rects):
            continue
        rects.append((r, op['label']))
    if not rects:
        return vertices, triangles, 0

    # Anneau extérieur dans le sens trigonométrique du plan (s, t) ; les trous
    # dans le sens horaire (convention de _triangulate_polygon).
    if sg.LinearRing(ring_st).is_ccw is False:
        ring = ring[::-1]
        ring_st = ring_st[::-1]
    holes = []
    for r, _label in rects:
        x0, y0, x1, y1 = r.bounds
        holes.append([(x0, y0), (x0, y1), (x1, y1), (x1, y0)])   # horaire
    verts2d, tris = _triangulate_polygon([ring_st] + holes)

    def to3d(s, t):
        p = o + u * s
        p[2] = o[2] + t
        return [round(float(c), 4) for c in p]

    new_vertices = [list(v) for v in vertices]
    index_of = {}
    for k, vid in enumerate(ring):
        index_of[k] = vid
    base = len(ring)
    for h_i, hole in enumerate(holes):
        for c_i, (s, t) in enumerate(hole):
            index_of[base + 4 * h_i + c_i] = len(new_vertices)
            new_vertices.append(to3d(s, t))

    template = dict(triangles[idxs[0]])
    template.pop('v', None)
    for key in ('area', 'normal', 'tilt_deg', 'azimuth_deg'):
        template.pop(key, None)
    k_num = group.split('_')[1] if '_' in group else group

    def oriented(a, b, c):
        pa, pb, pc = (np.asarray(new_vertices[x]) for x in (a, b, c))
        return [a, b, c] if np.cross(pb - pa, pc - pa) @ n > 0 else [a, c, b]

    out_tris = [t for i, t in enumerate(triangles) if i not in set(idxs)]
    for a, b, c in tris:
        out_tris.append({**template, 'v': oriented(index_of[a], index_of[b], index_of[c])})
    for h_i, (r, label) in enumerate(rects):
        q = [index_of[base + 4 * h_i + c_i] for c_i in range(4)]
        if label == 'door':
            extra = {**template, 'group': f'porte_{k_num}'}
            if door_model_id is not None:
                extra['paroi_model_id'] = door_model_id
        else:
            extra = {**template, 'group': f'vitrage_{k_num}', 'paroi_model_id': glazing_model_id,
                     'alpha_ext': None}
        out_tris.append({**extra, 'v': oriented(q[0], q[1], q[2])})
        out_tris.append({**extra, 'v': oriented(q[0], q[2], q[3])})
    return new_vertices, out_tris, len(rects)


# ── Orchestration ──────────────────────────────────────────────────────────────

def _terrain_sampler(objects, fallback):
    """Altitude du terrain (repère local) en (x, y), lue sur le maillage de
    terrain de l'environnement par un rayon vertical ; `fallback` sans terrain."""
    import trimesh
    terrain = [o for o in objects if o.get('kind') == 'terrain' and o.get('status') != 'removed']
    if not terrain:
        return lambda x, y: fallback
    o = terrain[0]
    mesh = trimesh.Trimesh(np.asarray(o['vertices'], float), np.asarray([t['v'] for t in o['triangles']]),
                           process=False)

    def at(x, y):
        loc, _r, _t = mesh.ray.intersects_location([[x, y, 1e4]], [[0, 0, -1.0]], multiple_hits=False)
        return float(loc[0][2]) if len(loc) else fallback
    return at


def texture_path(building_id, group, key):
    return os.path.join(CACHE_DIR, 'textures', f'{building_id}_{group}_{key}.jpg')


def facade_azimuth(plane, north_offset_deg):
    """Azimut vrai (0 = nord, sens horaire) de la normale sortante d'une façade."""
    e, n_, _z = _true_enu(np.asarray(plane['n'], float), north_offset_deg)
    return math.degrees(math.atan2(e, n_)) % 360.0


def glazing_reference(planes, frame, north, footprint):
    """Proportion de baies déclarée dans les DPE (BDNB) pour ce bâtiment, ou
    None. Points d'interrogation : un point intérieur de chaque partie de
    l'emprise, et une grille à 15 m pour une grande emprise (bâtiments réunis)."""
    from . import bdnb
    if footprint is None:
        return None
    pts = []
    for part in getattr(footprint, 'geoms', [footprint]):
        pts.append(part.representative_point())
        if part.area > 400:
            x0, y0, x1, y1 = part.bounds
            for x in np.arange(x0 + 7.5, x1, 15.0):
                for y in np.arange(y0 + 7.5, y1, 15.0):
                    q = sg.Point(x, y)
                    if part.contains(q):
                        pts.append(q)
    latlon = [frame.latlon(q.x, q.y) for q in pts[:12]]
    try:
        rnb = bdnb.rnb_ids_at([(float(a), float(b)) for a, b in latlon])
        dpes, fiches = bdnb.dpe_for_rnb(rnb)
    except bdnb.BdnbError:
        return None
    areas = {}
    for pl in planes.values():
        c = bdnb.cardinal_of(facade_azimuth(pl, north))
        areas[c] = areas.get(c, 0.0) + pl['area']
    info = bdnb.glazing_ratios(dpes, fiches, areas)
    if info is not None:
        info['rnb_ids'] = rnb
    return info


def _pano_meta(p):
    return {kk: p.get(kk) for kk in ('id', 'hd', 'sd', 'lat', 'lon', 'pitch', 'producer', 'license',
                                     'datetime', 'sequence')}


def analyse(building_id, vertices, triangles, frame, env_objects, progress_cb=None,
            ground_z=None, own_footprint=None):
    """Analyse des façades exposées d'un bâtiment DANS son environnement
    (Lot AL, voir api.facade_texture) : photos recalées par séquence, texture
    composée texel par texel sur toutes les vues qui voient réellement la
    façade (occultation par la scène ET par le nuage LiDAR brut), baies
    détectées sur la composition et complétées en grille dans les parties
    non vues, proportion de baies des DPE (BDNB) en référence.
    Retourne le dict à ranger dans Building.facades (sans l'enveloppe de base)."""
    from . import facade_texture as FT

    def report(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    planes = facade_planes(vertices, triangles)
    if not planes:
        raise FacadeError("Aucune façade exposée à analyser.")
    north = frame.north_offset_deg
    V = np.asarray(vertices, dtype=float)
    margin = FT.MAX_VIEW_DIST_M + 5
    bounds = (V[:, 0].min() - margin, V[:, 1].min() - margin, V[:, 0].max() + margin, V[:, 1].max() + margin)
    lat_a, lon_a = frame.latlon(bounds[0], bounds[1])
    lat_b, lon_b = frame.latlon(bounds[2], bounds[3])
    report("Recherche des photos Panoramax…", 2)
    panos = search_panoramas((min(lat_a, lat_b), min(lon_a, lon_b), max(lat_a, lat_b), max(lon_a, lon_b)))
    for p in panos:
        e, n = frame._fwd.transform(p['lon'], p['lat'])
        x, y = frame.to_local(e, n)
        p['xy'] = (float(x), float(y))
    # Photos utiles : devant au moins une façade, à portée. Limitées aux 16
    # plus proches : chacune coûte un téléchargement (~6 Mo), un recalage et
    # une segmentation — 40 photos prenaient plus de 10 min pour une maison.
    useful = []
    for p in panos:
        best = None
        for pl in planes.values():
            mid = np.asarray(pl['origin']) + np.asarray(pl['u']) * pl['width'] / 2
            d = np.asarray(p['xy']) - mid[:2]
            dist = float(np.linalg.norm(d))
            if 2.0 < dist < FT.MAX_VIEW_DIST_M + pl['width'] / 2 and float(d @ np.asarray(pl['n'])[:2]) / dist > 0.1:
                best = dist if best is None else min(best, dist)
        if best is not None:
            useful.append((best, p))
    useful.sort(key=lambda r: r[0])
    panos = [p for _d, p in useful[:16]]
    panos_by_id = {p['id']: p for p in panos}

    report("Référence DPE (BDNB)…", 4)
    reference = glazing_reference(planes, frame, north, own_footprint)

    report("Nuage LiDAR de l'environnement (haies, murets, arbres)…", 6)
    occupancy, occ_note = None, None
    try:
        occupancy = FT.load_occupancy(frame, ground_z, bounds, own_footprint.buffer(0.8) if own_footprint else None)
    except Exception as exc:  # noqa: BLE001 — sans LiDAR : scène seule
        occ_note = f"Occultation LiDAR indisponible ({exc}) : seuls les objets de la scène masquent."
    mesh_occ = _occluder(env_objects)
    ground_at = _terrain_sampler(env_objects, fallback=float(V[:, 2].min()))

    registered = {}
    if panos:
        report(f"Recalage de {len(panos)} photo(s) par séquence…", 10)
        registered = FT.register_sequences(
            panos, env_objects, ground_at, north,
            progress=lambda d, t: report(f"Recalage des photos ({d}/{t})…", 10 + int(30 * d / max(t, 1))))

    out = {}
    to_detect, originals = {}, {}
    groups = sorted(planes, key=lambda g: -planes[g]['area'])
    try:
        for k, g in enumerate(groups):
            pl = planes[g]
            report(f"Façade {k + 1}/{len(groups)} : composition des vues…", 42 + int(33 * k / len(groups)))
            base = {'group': g, 'plane': {kk: pl[kk] for kk in ('origin', 'u', 'n', 'width', 'height')},
                    'area': round(pl['area'], 2), 'azimuth': round(facade_azimuth(pl, north), 1)}
            views = FT.candidate_views(pl, registered, panos_by_id)
            if not views:
                out[g] = {**base, 'status': 'sans_photo', 'coverage': 0.0, 'openings': []}
                continue
            tex, seen_cells, scale, coverage, seen_img, used = FT.compose_facade(
                pl, views, north, mesh_occ=mesh_occ, occupancy=occupancy)
            seen = FT.encode_seen(seen_cells)
            if not used or coverage < 0.1:
                out[g] = {**base, 'status': 'masquee', 'coverage': round(coverage, 3), 'openings': [],
                          'seen': seen}
                continue
            key = 'mv' + used[0][0]['id'][:6]
            path = texture_path(building_id, g, key)
            from PIL import Image
            os.makedirs(os.path.dirname(path), exist_ok=True)
            Image.fromarray(tex).save(path, quality=88)
            main_pano, main_reg, _n = used[0]
            out[g] = {
                **base, 'coverage': round(coverage, 3), 'occlusion': round(1 - coverage, 2),
                'status': 'analysee' if coverage >= 0.6 else 'partielle',
                'seen': seen, 'texture_key': key, 'm_per_px': scale,
                'tex_size': [int(tex.shape[1]), int(tex.shape[0])],
                'pano': _pano_meta(main_pano), 'camera': [round(c, 3) for c in main_reg['camera']],
                'heading': round(main_reg['heading'], 2),
                'registration': {'score_gps': main_reg['score_gps'], 'score': main_reg['score'],
                                 'common': main_reg['common'], 'n_joint': main_reg['n_joint']},
                'views': [{'pano': _pano_meta(p), 'camera': [round(c, 3) for c in r['camera']],
                           'heading': round(r['heading'], 2), 'pitch': round(r.get('pitch', p['pitch']), 2),
                           'texels': int(n_)} for p, r, n_ in used],
                'openings': [],
            }
            # Détection seulement si assez de façade vue pour contenir une baie.
            if coverage * pl['area'] >= 3.0 and min(pl['width'], pl['height']) >= 1.0:
                to_detect[g] = (FT.equalize_seen(tex, seen_img), scale, pl['outline'])
                originals[g] = tex
    finally:
        FT.clear_caches()

    if to_detect:
        report(f"Détection des baies ({len(to_detect)} façade(s))…", 78)
        found_all = detect_openings_batch(to_detect)
        for g, found in found_all.items():
            seen = out[g]['seen']
            # Une baie « détectée » dans une zone non vue est un artefact (gris uniforme).
            found = [o for o in found if FT.seen_fraction(seen, o['s0'], o['t0'], o['s1'], o['t1']) >= 0.5
                     and FT.vegetation_fraction(originals[g], o, out[g]['m_per_px']) < 0.25]
            found = FT.fix_door_labels(found, seen)
            openings, grid = FT.regularize_openings(found, planes[g], seen)
            out[g].update(openings=openings, grid=grid)
    report("Terminé.", 98)
    result = {'facades': out, 'n_panoramas': len(panos), 'method': 'multivue'}
    if reference:
        result['reference'] = reference
    if occ_note:
        result['note'] = occ_note
    return result


class TextureMissing(FacadeError):
    """Texture composée absente du cache (redéploiement) : à recomposer par
    une tâche de fond (recompose_textures), pas dans une requête."""


def regenerate_texture(building_id, entry, north_offset_deg):
    """Octets JPEG de la texture d'une façade. Texture d'une seule photo (Lot
    AK) : refaite à la volée ; texture composée (Lot AL) : TextureMissing."""
    path = texture_path(building_id, entry['group'], entry['texture_key'])
    if not os.path.exists(path):
        if entry.get('views'):
            raise TextureMissing("Texture à recomposer (cache effacé par un redéploiement).")
        pano = dict(entry['pano'])
        rectify_to_file(entry['plane'], pano, entry['camera'], entry['heading'], north_offset_deg, path)
    with open(path, 'rb') as fh:
        return fh.read()


def recompose_textures(building_id, vertices, triangles, facades, frame, env_objects, ground_z=None,
                       own_footprint=None, progress_cb=None):
    """Recompose les textures multi-vues à partir des vues et recalages
    enregistrés (sans refaire le recalage ni la détection)."""
    from PIL import Image

    from . import facade_texture as FT
    planes = facade_planes(vertices, triangles)
    north = frame.north_offset_deg
    V = np.asarray(vertices, dtype=float)
    margin = FT.MAX_VIEW_DIST_M + 5
    bounds = (V[:, 0].min() - margin, V[:, 1].min() - margin, V[:, 0].max() + margin, V[:, 1].max() + margin)
    try:
        occupancy = FT.load_occupancy(frame, ground_z, bounds, own_footprint.buffer(0.8) if own_footprint else None)
    except Exception:  # noqa: BLE001
        occupancy = None
    mesh_occ = _occluder(env_objects)
    todo = [(g, e) for g, e in facades.items() if e.get('views') and g in planes]
    try:
        for k, (g, e) in enumerate(todo):
            if progress_cb:
                progress_cb(f"Façade {k + 1}/{len(todo)}…", 5 + int(90 * k / max(len(todo), 1)))
            views = [(v['pano'], {'camera': v['camera'], 'heading': v['heading'],
                                  'pitch': v.get('pitch', v['pano'].get('pitch') or 0.0)}) for v in e['views']]
            # Vues enregistrées déjà recalées sur la façade : pas de second recalage.
            tex, *_rest = FT.compose_facade(planes[g], views, north, mesh_occ=mesh_occ, occupancy=occupancy,
                                            refine=False)
            path = texture_path(building_id, g, e['texture_key'])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            Image.fromarray(tex).save(path, quality=88)
    finally:
        FT.clear_caches()
    return len(todo)


def apply_openings(base_vertices, base_triangles, facades, glazing_model_id, fallback_ratio,
                   use_detection, wall_model_id=None, reference=None):
    """Enveloppe du bâtiment avec ses vrais vitrages, à partir de l'enveloppe
    de BASE (avant toute insertion : on peut réappliquer autrement).
    - Façade vue (analysée ou partiellement vue) et retenue → baies détectées
      (grille complétée dans les parties non vues) ; façade partiellement vue
      SANS grille établie → fenêtres de repli ajoutées dans les seules parties
      non vues.
    - Sinon → fenêtres régulières à la proportion de repli : celle des DPE
      (BDNB) pour l'orientation de la façade si `reference` est fourni, sinon
      `fallback_ratio`.
    Retourne (vertices, triangles, rapport par façade)."""
    from . import bdnb
    from . import facade_texture as FT
    vertices = [list(v) for v in base_vertices]
    triangles = [dict(t) for t in base_triangles]
    planes = facade_planes(vertices, triangles)
    ref_ratios = (reference or {}).get('ratios') or {}
    report = {}
    for g, pl in planes.items():
        entry = facades.get(g) or {}
        ratio, ratio_src = fallback_ratio, 'proportion'
        if ref_ratios and entry.get('azimuth') is not None:
            c = bdnb.cardinal_of(entry['azimuth'])
            if c in ref_ratios:
                ratio, ratio_src = ref_ratios[c], 'DPE'
        seen_ok = entry.get('status') in ('analysee', 'partielle')
        # Façade vue sur photo : la détection fait foi, MÊME sans baie trouvée — un
        # pignon aveugle vu de face doit rester aveugle (constaté : il recevait
        # des fenêtres de repli quand on n'utilisait la détection que si elle
        # avait trouvé quelque chose).
        use = use_detection.get(g, seen_ok)
        if use and seen_ok:
            openings, source = list(entry.get('openings') or []), 'détection'
            if entry.get('status') == 'partielle' and not entry.get('grid'):
                extra = [o for o in synthetic_openings(pl, ratio)
                         if FT.seen_fraction(entry.get('seen'), o['s0'], o['t0'], o['s1'], o['t1']) < 0.3
                         and not any(FT._overlap(FT.sg_box(o), FT.sg_box(q)) for q in openings)]
                if extra:
                    openings += extra
                    source = f'détection + {ratio_src} (non vu)'
        else:
            openings, source = synthetic_openings(pl, ratio), ratio_src
        vertices, triangles, n = insert_openings(vertices, triangles, g, pl, openings, glazing_model_id)
        glazed = sum((o['s1'] - o['s0']) * (o['t1'] - o['t0']) for o in openings if o['label'] == 'window')
        report[g] = {'source': source, 'n_openings': n, 'glazed_ratio': round(glazed / max(pl['area'], 1e-6), 3)}
    if wall_model_id is not None:
        for t in triangles:
            if (t.get('group') or '').startswith('mur_'):
                t['paroi_model_id'] = wall_model_id
    return vertices, triangles, report
