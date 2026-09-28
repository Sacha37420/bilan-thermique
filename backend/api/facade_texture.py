"""Texture des façades dans l'ENSEMBLE de leur environnement (Lot AL).

Le Lot AK texturait chaque façade isolément, depuis UNE photo, recalée seule.
Sur un vrai quartier (collectifs en haut d'un talus planté, vus d'en bas depuis
la rue) le résultat était inutilisable (constaté le 2026-09-27) :
- le bas des façades montrait la haie du talus : ni la haie, ni le talus
  planté, ni les murets n'existent dans la scène (seuls les arbres de plus de
  quelques mètres y sont reconstruits), donc le test d'occultation par la scène
  ne pouvait pas les voir ;
- la mesure d'occultation par SegFormer s'appliquait à la texture redressée
  d'un morceau de mur parfois large de 14 px, étirée en 512 × 512 : une haie
  pleine y ressortait « visible à 99 % » ;
- chaque petit morceau de mur (enveloppe fusionnée de plusieurs emprises)
  recevait sa propre photo, sans cohérence avec ses voisins.

Ce module remplace le « une façade = une photo » par une composition texel par
texel sur toutes les photos qui voient réellement ce point de la façade :
1. **Occultation par le nuage LiDAR brut** (`LidarOccupancy`) : voxels de tous
   les points hors sol (haies, murets, arbustes, voitures présentes lors du
   vol, autres bâtiments) — c'est l'environnement complet, pas la scène
   simplifiée. Les objets bas (< 4 m) sont remplis jusqu'au sol : vus
   d'avion, une haie ou un muret n'ont de points que sur leur dessus.
2. **Recalage par séquence** (`register_sequences`) : une séquence Panoramax
   est prise par le même appareil, sur le même véhicule, à quelques mètres
   d'intervalle — son erreur GPS varie lentement et son biais de cap est
   constant. Un décalage commun est estimé sur toutes les photos voisines
   d'une même séquence (bien plus robuste qu'une photo seule, qui pouvait
   partir sur les lames d'un volet), puis affiné de quelques décimètres par
   photo.
3. **Segmentation par panorama** (`semantic_map`) : SegFormer une fois sur le
   panorama entier, puis consulté au pixel où tombe chaque texel — un texel qui
   tombe sur de la végétation, une voiture, le ciel… est écarté pour CETTE vue.
4. **Composition** (`compose_facade`) : la vue qui apporte le plus (texels
   réellement vus × résolution) couvre tout ce qu'elle voit ; les suivantes
   ne comblent que ses trous, par régions entières. Mélanger les vues texel
   par texel a été essayé et abandonné : la vraie façade n'est pas plane
   comme le modèle (balcons, loggias), chaque photo la voit avec une
   parallaxe différente, et le mélange donnait une mosaïque délavée à images
   fantômes. Un texel qu'aucune vue ne voit reste « non vu » (gris) : il n'est
   plus texturé avec l'obstacle.
"""

import math

import numpy as np
from scipy import ndimage

from . import facades as F

VOXEL_M = 0.3
# Classes LiDAR HD qui occultent : non classé, végétation moyenne et haute,
# bâtiment, sursol pérenne (murets, clôtures, abris…). Pas la végétation basse
# (< 50 cm, herbe) : elle occulterait tous les rayons rasants vers le pied des murs.
OCCLUDER_CLASSES = (1, 4, 5, 6, 64)
COLUMN_FILL_MAX_M = 4.0
MAX_VIEW_DIST_M = 40.0
COARSE_M = 0.25
SEEN_CELL_M = 0.5
UNSEEN_RGB = (150, 150, 150)

# ADE20K : classes qui ne sont PAS la façade et la cachent quand un texel y
# tombe (ciel = recalage imparfait sur le bord du toit). « Mur » (0) n'y est
# PAS : un pignon lisse est souvent classé mur, l'écarter vidait la façade.
# Les murets devant la façade sont couverts par le LiDAR (sursol pérenne).
# Classes qui SONT une façade : bâtiment, maison, fenêtre, porte, mur, store,
# volet roulant… (mur compris ici : c'est un indice de vue plausible, pas un
# critère d'occultation).
SEG_FACADE_LIKE = {0, 1, 8, 14, 25, 48, 58, 63, 86, 42}
SEG_OCCLUDERS = {2, 4, 6, 9, 11, 12, 13, 17, 20, 29, 32, 34, 43, 46, 52, 66, 68, 72, 80, 83,
                 87, 91, 93, 94, 102, 116, 127, 136, 137}


# ── Occultation par le nuage LiDAR ─────────────────────────────────────────────

class LidarOccupancy:
    """Grille de voxels occupés par les points LiDAR hors sol, dans le repère
    local. `exclude` : polygone (x, y) dont les points sont ignorés — le
    bâtiment étudié lui-même, déjà présent comme maillage exact dans
    l'occulteur de scène (ses propres points de façade et de débord de toit
    masqueraient sinon ses murs)."""

    def __init__(self, x, y, z, cls, bounds, exclude=None, voxel=VOXEL_M):
        xmin, ymin, xmax, ymax = bounds
        self.voxel = voxel
        ground = cls == 2
        # Terrain : moyenne des points sol par maille de 1 m, trous comblés par
        # le plus proche voisin.
        self.g_origin = (xmin, ymin)
        gn_x = int(math.ceil((xmax - xmin) / 1.0)) + 1
        gn_y = int(math.ceil((ymax - ymin) / 1.0)) + 1
        gi = np.clip(((x[ground] - xmin) / 1.0).astype(int), 0, gn_x - 1)
        gj = np.clip(((y[ground] - ymin) / 1.0).astype(int), 0, gn_y - 1)
        sums = np.zeros((gn_y, gn_x)); cnt = np.zeros((gn_y, gn_x))
        np.add.at(sums, (gj, gi), z[ground]); np.add.at(cnt, (gj, gi), 1)
        known = cnt > 0
        dtm = np.where(known, sums / np.maximum(cnt, 1), np.nan)
        if known.any() and not known.all():
            _d, (ii, jj) = ndimage.distance_transform_edt(~known, return_indices=True)
            dtm = dtm[ii, jj]
        elif not known.any():
            dtm = np.full((gn_y, gn_x), float(np.min(z)) if len(z) else 0.0)
        self.dtm = dtm

        keep = np.isin(cls, OCCLUDER_CLASSES)
        px, py, pz = x[keep], y[keep], z[keep]
        if exclude is not None and len(px):
            import shapely
            inside = shapely.contains_xy(exclude, px, py)
            px, py, pz = px[~inside], py[~inside], pz[~inside]
        gz = self.ground(px, py)
        h = pz - gz
        ok = h > 0.3
        px, py, pz, gz, h = px[ok], py[ok], pz[ok], gz[ok], h[ok]

        self.origin = np.array([xmin, ymin, float(np.nanmin(dtm)) - 1.0])
        top = float(pz.max()) + 1.0 if len(pz) else self.origin[2] + 5.0
        self.shape = (int(math.ceil((xmax - xmin) / voxel)) + 1,
                      int(math.ceil((ymax - ymin) / voxel)) + 1,
                      int(math.ceil((top - self.origin[2]) / voxel)) + 1)
        grid = np.zeros(self.shape, dtype=bool)
        i = ((px - self.origin[0]) / voxel).astype(int)
        j = ((py - self.origin[1]) / voxel).astype(int)
        k1 = ((pz - self.origin[2]) / voxel).astype(int)
        valid = (i >= 0) & (i < self.shape[0]) & (j >= 0) & (j < self.shape[1]) & (k1 >= 0) & (k1 < self.shape[2])
        i, j, k1, gz, h = i[valid], j[valid], k1[valid], gz[valid], h[valid]
        grid[i, j, k1] = True
        # Objets bas : remplis jusqu'au sol (une haie vue d'avion n'a de points
        # que sur son dessus, un rayon qui la traverse à mi-hauteur n'en
        # croiserait aucun).
        low = h < COLUMN_FILL_MAX_M
        k0 = ((gz[low] + 0.3 - self.origin[2]) / voxel).astype(int)
        li, lj, lk1 = i[low], j[low], k1[low]
        for dk in range(int(COLUMN_FILL_MAX_M / voxel) + 1):
            kk = k0 + dk
            m = kk <= lk1
            if not m.any():
                break
            grid[li[m], lj[m], kk[m]] = True
        # Un point isolé ne bouche pas une maille entière : on n'épaissit pas,
        # et un rayon n'est déclaré caché qu'après deux voxels occupés.
        self.grid = grid

    def ground(self, x, y):
        gi = np.clip(((np.asarray(x) - self.g_origin[0]) / 1.0).astype(int), 0, self.dtm.shape[1] - 1)
        gj = np.clip(((np.asarray(y) - self.g_origin[1]) / 1.0).astype(int), 0, self.dtm.shape[0] - 1)
        return self.dtm[gj, gi]

    def blocked(self, cams, pts, start_m=0.8, stop_before_m=0.6, step=None, min_hits=2):
        """Rayons caméra → point : vrai si le segment [start, dist − stop]
        traverse au moins `min_hits` échantillons dans des voxels occupés."""
        step = step or self.voxel * 0.5
        C = np.asarray(cams, float); P = np.asarray(pts, float)
        D = P - C
        dist = np.linalg.norm(D, axis=1)
        D = D / np.maximum(dist, 1e-9)[:, None]
        n_steps = int(math.ceil((dist.max() if len(dist) else 0) / step)) + 1
        hits = np.zeros(len(P), dtype=np.int32)
        for s in range(n_steps):
            tt = start_m + s * step
            active = tt < dist - stop_before_m
            if not active.any():
                break
            Q = C[active] + D[active] * tt
            ijk = ((Q - self.origin) / self.voxel).astype(int)
            ok = ((ijk >= 0) & (ijk < np.array(self.shape))).all(axis=1)
            occ = np.zeros(len(Q), dtype=bool)
            occ[ok] = self.grid[ijk[ok, 0], ijk[ok, 1], ijk[ok, 2]]
            hits[np.nonzero(active)[0][occ]] += 1
        return hits >= min_hits


def load_occupancy(frame, ground_z, bounds_local, exclude_poly):
    """Lit le nuage LiDAR HD de l'emprise locale `bounds_local` (xmin, ymin,
    xmax, ymax) et construit l'occupation. `ground_z` : altitude (IGN69) du
    z = 0 du repère. Lève lidar_source.LidarUnavailable."""
    from . import lidar_source
    xmin, ymin, xmax, ymax = bounds_local
    cx, cy = [xmin, xmax, xmax, xmin], [ymin, ymin, ymax, ymax]
    e, n = frame.to_l93(np.array(cx), np.array(cy))
    lat, lon = frame.latlon(np.array(cx), np.array(cy))
    tiles = lidar_source.fetch_lidar_tiles((float(np.min(lat)) - 1e-4, float(np.min(lon)) - 1e-4,
                                            float(np.max(lat)) + 1e-4, float(np.max(lon)) + 1e-4))
    if not tiles:
        raise lidar_source.LidarUnavailable("zone non couverte par le LiDAR HD")
    (xe, yn, z, cls), _stats = lidar_source.read_points(
        tiles, (float(e.min()), float(n.min()), float(e.max()), float(n.max())))
    x, y = frame.to_local(xe, yn)
    return LidarOccupancy(np.asarray(x), np.asarray(y), np.asarray(z) - (ground_z or 0.0),
                          np.asarray(cls), bounds_local, exclude=exclude_poly)


# ── Recalage par séquence ──────────────────────────────────────────────────────

def _chunks(panos, gap_m=15.0, span_m=40.0):
    """Découpe les photos d'une même séquence (triées par date) en tronçons
    contigus : l'erreur GPS varie lentement le long du trajet, mais pas d'un
    bout à l'autre d'une rue."""
    out, cur = [], []
    for p in panos:
        if cur:
            d_prev = math.dist(p['xy'], cur[-1]['xy'])
            d_first = math.dist(p['xy'], cur[0]['xy'])
            if d_prev > gap_m or d_first > span_m:
                out.append(cur); cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def register_sequences(panos, env_objects, ground_at, north, progress=None,
                       xy_range=3.0, heading_range=3.0, sigma_m=2.0):
    """Recale toutes les photos : décalage (dx, dy, dcap) COMMUN à chaque
    tronçon de séquence, puis affinage individuel (±0,5 m, ±0,5°) rappelé vers
    ce décalage commun. Inclinaison : celle des métadonnées (`pers:pitch`).
    Estimer aussi inclinaison et hauteur d'appareil par les arêtes a été
    essayé et abandonné (2026-09-27) : score presque plat en inclinaison
    (des massifs pris pour du bâti par le LiDAR brouillent les arêtes
    horizontales), l'estimation partait à l'opposé de la valeur annoncée, que
    la superposition des arêtes sur la photo confirmait juste.
    Retourne {id: {'camera', 'heading', 'pitch', 'score', 'score_gps',
    'common': [dx, dy, dh], 'n_joint'}}.
    """
    by_seq = {}
    for p in panos:
        by_seq.setdefault(p.get('sequence') or p['id'], []).append(p)
    chunks = []
    for seq in by_seq.values():
        seq.sort(key=lambda p: p.get('datetime') or '')
        chunks.extend(_chunks(seq))
    out = {}
    done = 0
    for chunk in chunks:
        scorers = []
        for p in chunk:
            try:
                img = F.load_panorama(p, 'hd')
            except F.FacadeError:
                continue
            edges = F.model_edge_points(env_objects, p['xy'])
            z_cam = ground_at(p['xy'][0], p['xy'][1]) + F.CAMERA_HEIGHT_M
            cam0 = [p['xy'][0], p['xy'][1], z_cam]
            raw = F.edge_scorer(img, edges, cam0, p['heading'], p['pitch'], north, downsample=4)
            del img
            scorers.append((p, cam0, raw))
            done += 1
            if progress:
                progress(done, len(panos))
        usable = [s for s in scorers if s[2] is not None]

        def joint(dx, dy, dh):
            v = sum(r(dx, dy, 0.0, dh) for _p, _c, r in usable) / len(usable)
            return v - (dx * dx + dy * dy) / (2 * sigma_m ** 2)

        best = (0.0, 0.0, 0.0, 0.0)          # score, dx, dy, dh
        if usable:
            best = (joint(0, 0, 0), 0.0, 0.0, 0.0)
            for dx in np.arange(-xy_range, xy_range + 1e-6, 1.0):
                for dy in np.arange(-xy_range, xy_range + 1e-6, 1.0):
                    for dh in np.arange(-heading_range, heading_range + 1e-6, 1.0):
                        sc = joint(dx, dy, dh)
                        if sc > best[0]:
                            best = (sc, dx, dy, dh)
            for step in (0.5, 0.25):
                _s, bx, by, bh = best
                for dx in (bx - step, bx, bx + step):
                    for dy in (by - step, by, by + step):
                        for dh in (bh - step, bh, bh + step):
                            sc = joint(dx, dy, dh)
                            if sc > best[0]:
                                best = (sc, dx, dy, dh)
        _s, cdx, cdy, cdh = best
        for p, cam0, raw in scorers:
            if raw is None:
                dx, dy, dh, sc, sc0 = cdx, cdy, cdh, 0.0, 0.0
            else:
                sc0 = raw(0, 0, 0, 0)
                cur = (raw(cdx, cdy, 0, cdh), cdx, cdy, cdh)
                for dx in np.arange(cdx - 0.5, cdx + 0.51, 0.25):
                    for dy in np.arange(cdy - 0.5, cdy + 0.51, 0.25):
                        for dh in np.arange(cdh - 0.5, cdh + 0.51, 0.25):
                            v = raw(dx, dy, 0, dh) - ((dx - cdx) ** 2 + (dy - cdy) ** 2) / (2 * 0.5 ** 2)
                            if v > cur[0]:
                                cur = (v, dx, dy, dh)
                _v, dx, dy, dh = cur
                sc = raw(dx, dy, 0, dh)
            out[p['id']] = {
                'camera': [cam0[0] + dx, cam0[1] + dy, cam0[2]], 'heading': p['heading'] + dh,
                'pitch': p['pitch'],
                'score': round(float(sc), 3), 'score_gps': round(float(sc0), 3),
                'common': [round(float(v), 2) for v in (cdx, cdy, cdh)],
                'n_joint': len(usable),
            }
    return out


# ── Segmentation par panorama ──────────────────────────────────────────────────

_sem_cache = {}
_img_cache = {}
_edge_cache = {}
IMG_CACHE_MAX = 10


def pano_image(pano):
    """Panorama HD décodé, gardé en mémoire (~90 Mo chacun) le
    temps d'une analyse : chaque photo sert à plusieurs façades (10 au plus, ~0,9 Go)."""
    pid = pano['id']
    if pid in _img_cache:
        img = _img_cache.pop(pid)
    else:
        img = F.load_panorama(pano, 'hd')
        while len(_img_cache) >= IMG_CACHE_MAX:
            _img_cache.pop(next(iter(_img_cache)))
    _img_cache[pid] = img
    return img


def semantic_map(pano, width=1024):
    """Étiquettes ADE20K (h, w) uint8 du panorama entier à `width` px de large,
    par tuiles carrées (SegFormer accepte une taille libre ; des tuiles
    limitent la mémoire). 1024 px suffisent : le masque n'est consulté que
    par vote sur des mailles de 25 cm, et 2048 px coûtaient ~10 s par photo.
    Mis en cache mémoire pour la durée de l'analyse."""
    if pano['id'] in _sem_cache:
        return _sem_cache[pano['id']]
    from PIL import Image
    sess = F._seg_session()
    img = Image.fromarray(pano_image(pano))
    w, h = width, width // 2
    arr = np.asarray(img.resize((w, h), Image.BILINEAR)) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
    labels = np.zeros((h, w), dtype=np.uint8)
    tile = h
    for x0 in range(0, w, tile):
        x = arr[:, x0:x0 + tile].transpose(2, 0, 1)[None].astype(np.float32)
        logits = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
        lab = logits.argmax(0).astype(np.uint8)
        labels[:, x0:x0 + tile] = np.asarray(Image.fromarray(lab).resize((x.shape[3], h), Image.NEAREST))
    _sem_cache[pano['id']] = labels
    return labels


def clear_caches():
    _sem_cache.clear()
    _img_cache.clear()
    _edge_cache.clear()


def edge_maps(pano):
    """Contours (gx, gy, ciel) du panorama à demi-résolution, en cache."""
    if pano['id'] not in _edge_cache:
        img = pano_image(pano)
        _edge_cache[pano['id']] = F._edge_maps(img[::2, ::2])
    return _edge_cache[pano['id']]


def outline_edges(plane, step=0.1):
    """Points (s, t) du contour de la façade SAUF son pied (presque toujours
    caché : haie, clôture, voiture), avec leur orientation : 'v' (angle), 'h'
    (égout), 'o' (rampant de pignon)."""
    outline = plane.get('outline')
    if outline is None:
        return np.zeros((0, 2)), np.zeros(0, dtype='<U1')
    pts, kinds = [], []
    for gm in getattr(outline, 'geoms', [outline]):
        c = list(gm.exterior.coords)
        for (s0, t0), (s1, t1) in zip(c[:-1], c[1:]):
            length = math.hypot(s1 - s0, t1 - t0)
            if length < 0.5 or (max(t0, t1) < 0.3):          # pied du mur
                continue
            kind = 'v' if abs(s1 - s0) < 0.2 * length else 'h' if abs(t1 - t0) < 0.2 * length else 'o'
            n = max(int(length / step), 2)
            for k in range(n):
                a = (k + 0.5) / n
                pts.append((s0 + a * (s1 - s0), t0 + a * (t1 - t0)))
                kinds.append(kind)
    return np.asarray(pts, float).reshape(-1, 2), np.asarray(kinds)


def refine_view_on_facade(plane, pano, reg, north, dz_range=1.0, dh_range=1.0):
    """Petit recalage d'UNE vue sur le contour de CETTE façade : décalage
    vertical de l'appareil (dz) et de cap (dh) qui alignent égout, rampants et
    angles sur les contours de l'image. La hauteur réelle de l'appareil est
    inconnue (voiture, piéton, vélo…) : constaté sur une maison derrière une
    clôture, deux photos consécutives d'une même séquence plaçaient la
    façade à ~1 m d'écart vertical l'une de l'autre. Retourne (reg corrigé,
    gain de score)."""
    st, kinds = outline_edges(plane)
    if len(st) < 20:
        return reg, 0.0
    gx, gy, sky = edge_maps(pano)
    ih, iw = gx.shape
    P = _plane_points(plane, st[:, 0], st[:, 1])
    cam0 = np.asarray(reg['camera'], float)
    pitch = reg.get('pitch', pano['pitch'])

    def score(dz, dh):
        px, py = F.project_to_pano(P, cam0 + [0, 0, dz], reg['heading'] + dh, pitch, north, iw, ih)
        c = [py - 0.5, px - 0.5]
        vx = ndimage.map_coordinates(gx, c, order=1, mode='wrap')
        vy = ndimage.map_coordinates(gy, c, order=1, mode='wrap')
        v = np.where(kinds == 'v', vx, np.where(kinds == 'h', vy, 0.5 * (vx + vy)))
        return float(v.mean()) - 0.3 * (dz * dz + dh * dh)

    base = score(0.0, 0.0)
    best = (base, 0.0, 0.0)
    for dz in np.arange(-dz_range, dz_range + 1e-6, 0.1):
        for dh in np.arange(-dh_range, dh_range + 1e-6, 0.25):
            v = score(dz, dh)
            if v > best[0]:
                best = (v, dz, dh)
    _v, dz, dh = best
    out = dict(reg)
    out['camera'] = [float(cam0[0]), float(cam0[1]), float(cam0[2] + dz)]
    out['heading'] = float(reg['heading'] + dh)
    return out, float(best[0] - base)


# ── Composition multi-vues ─────────────────────────────────────────────────────

def _texel_grid(plane, m_per_px=0.02):
    W, H = plane['width'], plane['height']
    scale = max(m_per_px, max(W, H) / F.TEXTURE_MAX_PX)
    nw, nh = max(8, int(round(W / scale))), max(8, int(round(H / scale)))
    return scale, nw, nh


def _plane_points(plane, s, t):
    o = np.asarray(plane['origin'], float); u = np.asarray(plane['u'], float)
    P = o[None, :] + s[:, None] * u[None, :]
    P[:, 2] = o[2] + t
    return P


def candidate_views(plane, registered, panos_by_id, max_views=5):
    """Photos recalées devant la façade et à portée, les plus utiles d'abord
    (proches et de face)."""
    n = np.asarray(plane['n'], float)
    mid = np.asarray(plane['origin'], float) + np.asarray(plane['u'], float) * plane['width'] / 2
    ranked = []
    for pid, reg in registered.items():
        d = np.asarray(reg['camera'][:2]) - mid[:2]
        dist = float(np.linalg.norm(d))
        if dist < 2.0 or dist > MAX_VIEW_DIST_M + plane['width'] / 2:
            continue
        cos_a = float(d @ n[:2]) / dist
        if cos_a < 0.1:
            continue
        ranked.append((dist / cos_a, pid))
    ranked.sort()
    return [(panos_by_id[pid], registered[pid]) for _c, pid in ranked[:max_views]]


def compose_facade(plane, views, north, mesh_occ=None, occupancy=None, m_per_px=0.02, refine=True):
    """Texture composée (h, w, 3) uint8 (ligne 0 = haut), masque « vu » au pas
    SEEN_CELL_M, échelle, et part vue (0–1) de la façade. `views` : [(pano,
    reg)] de candidate_views."""
    import shapely
    scale, nw, nh = _texel_grid(plane, m_per_px)
    W, H = plane['width'], plane['height']
    outline = plane.get('outline')
    n = np.asarray(plane['n'], float)

    # Maille grossière pour la visibilité (rayons), texels pour la couleur.
    cw, ch = max(1, int(math.ceil(W / COARSE_M))), max(1, int(math.ceil(H / COARSE_M)))
    cs = (np.arange(cw) + 0.5) * W / cw
    ct = (np.arange(ch) + 0.5) * H / ch
    CS, CT = np.meshgrid(cs, ct)                      # ligne 0 = bas
    Pc = _plane_points(plane, CS.ravel(), CT.ravel()) + n * 0.05
    s = (np.arange(nw) + 0.5) * W / nw
    t = H - (np.arange(nh) + 0.5) * H / nh             # ligne 0 = haut
    S, T = np.meshgrid(s, t)
    Pt = _plane_points(plane, S.ravel(), T.ravel())
    inside = np.ones(nh * nw, dtype=bool)
    if outline is not None:
        inside = shapely.contains_xy(outline.buffer(0.02), S.ravel(), T.ravel())
    # texel → maille grossière
    ci = np.clip((S.ravel() / W * cw).astype(int), 0, cw - 1)
    cj = np.clip((T.ravel() / H * ch).astype(int), 0, ch - 1)
    cidx = cj * cw + ci

    colors, weights, valids, plausibility = [], [], [], []
    used = []
    for pano, reg in views:
        # Pose photogrammétrique (Lot AM) : exacte, aucun recalage local.
        if refine and 'R' not in reg:
            try:
                reg, _gain = refine_view_on_facade(plane, pano, reg, north)
            except F.FacadeError:
                continue
        cam = np.asarray(reg['camera'], float)
        D = Pc - cam
        dist = np.linalg.norm(D, axis=1)
        facing = (-(D @ n) / np.maximum(dist, 1e-9))           # cos d'incidence
        vis = (facing > 0.08) & (dist < MAX_VIEW_DIST_M)
        if not vis.any():
            continue
        if mesh_occ is not None:
            idx = np.nonzero(vis)[0]
            dirs = D[idx] / dist[idx, None]
            origins = np.repeat(cam[None], len(idx), axis=0)
            locs, rays, _tri = mesh_occ.intersects_location(origins, dirs, multiple_hits=False)
            hit_d = np.full(len(idx), np.inf)
            if len(rays):
                hit_d[rays] = np.linalg.norm(locs - origins[rays], axis=1)
            vis[idx[hit_d < dist[idx] - 0.3]] = False
        if occupancy is not None and vis.any():
            idx = np.nonzero(vis)[0]
            vis[idx[occupancy.blocked(np.repeat(cam[None], len(idx), axis=0), Pc[idx])]] = False
        if not vis.any():
            continue
        try:
            img = pano_image(pano)
        except F.FacadeError:
            continue
        ih, iw = img.shape[:2]
        tv = vis[cidx] & inside
        sel = np.nonzero(tv)[0]
        if 'R' in reg:
            from .facade_sfm import project as sfm_project
            px, py = sfm_project(Pt[sel], cam, reg['R'], iw, ih)
        else:
            px, py = F.project_to_pano(Pt[sel], cam, reg['heading'], reg.get('pitch', pano['pitch']), north, iw, ih)
        col = np.zeros((nh * nw, 3), dtype=np.float32)
        coords = [py - 0.5, px - 0.5]
        for c in range(3):
            col[sel, c] = ndimage.map_coordinates(img[..., c], coords, order=1, mode='wrap')
        del img
        # Segmentation : texel sur végétation, véhicule, ciel… → écarté.
        try:
            lab = semantic_map(pano)
            lh, lw = lab.shape
            li = np.clip((px * lw / iw).astype(int), 0, lw - 1)
            lj = np.clip((py * lh / ih).astype(int), 0, lh - 1)
            labels_v = lab[lj, li]
            bad = np.isin(labels_v, list(SEG_OCCLUDERS))
            facade_frac = float(np.isin(labels_v, list(SEG_FACADE_LIKE)).mean()) if len(labels_v) else 0.0
            # Un pixel isolé ne condamne pas un texel : on érode le masque
            # d'obstacle au pas grossier (vote par maille).
            bad_c = np.bincount(cidx[sel], weights=bad.astype(float), minlength=cw * ch) / \
                np.maximum(np.bincount(cidx[sel], minlength=cw * ch), 1)
            tv[sel[bad_c[cidx[sel]] > 0.5]] = False
        except Exception:  # noqa: BLE001 — segmentation indisponible : LiDAR seul
            facade_frac = 1.0
        if not tv.any():
            continue
        # Résolution utile : au-delà, une vue très proche (< 8 m) voit peu de
        # façade, sous un angle qui amplifie la moindre erreur de hauteur ou
        # d'inclinaison et la parallaxe des balcons — elle ne doit pas
        # l'emporter sur une vue de face à 10–15 m.
        w_c = np.where(vis, facing / np.maximum(dist, 8.0), 0.0)
        colors.append(col)
        weights.append(np.where(tv, w_c[cidx], 0.0).astype(np.float32))
        valids.append(tv)
        used.append((pano, reg, float(tv.sum())))
        plausibility.append(facade_frac)

    tex = np.empty((nh * nw, 3), dtype=np.uint8)
    tex[:] = UNSEEN_RGB
    seen = np.zeros(nh * nw, dtype=bool)
    if colors:
        # Vue principale d'abord : la façade réelle n'est PAS plane comme le
        # modèle (balcons, loggias, courbes) et chaque photo la voit décalée
        # d'une parallaxe différente — mélanger les vues texel par texel
        # donnait une mosaïque délavée à images fantômes (constaté sur un
        # collectif à balcons). La vue qui apporte le plus (texels vus ×
        # résolution) couvre tout ce qu'elle voit ; les suivantes ne comblent
        # que ses trous, par régions entières.
        # Classement : texels vus × résolution × PLAUSIBILITÉ² — part des
        # texels que la segmentation reconnaît comme façade (bâtiment, mur,
        # fenêtre, porte). Constaté sur une maison derrière une clôture et un
        # muret de soutènement : une vue à peine décalée (position GPS) plaçait
        # clôture et massifs sur tout le plan du mur et l'emportait par le
        # nombre de texels ; elle ne contient presque rien de « façade ».
        order = np.argsort([-float(w.sum()) * plausibility[k] ** 2 for k, w in enumerate(weights)])
        assigned = np.full(nh * nw, -1, dtype=np.int32)
        for v in order:
            free = valids[v] & (assigned < 0)
            if v != order[0]:
                # Pas de confettis : une vue secondaire ne comble que des
                # trous d'au moins ~0,5 m² d'un seul tenant.
                blob, nb = ndimage.label(free.reshape(nh, nw))
                if nb:
                    sizes = np.bincount(blob.ravel())
                    small = sizes < (0.5 / (scale * scale))
                    small[0] = False
                    free &= ~small[blob.ravel()]
            assigned[free] = v
        seen = (assigned >= 0) & inside
        for v in order:
            m = assigned == v
            tex[m] = np.clip(colors[v][m], 0, 255).astype(np.uint8)
        # Petits trous isolés (un voxel LiDAR de garde-corps, une maille
        # écartée par la segmentation) au milieu d'une zone vue : bouchés par
        # la couleur vue la plus proche. Les grandes zones non vues (haie,
        # talus) restent grises.
        hole = (~seen & inside).reshape(nh, nw)
        blob, nb = ndimage.label(hole)
        if nb:
            sizes = np.bincount(blob.ravel())
            touches = np.zeros(nb + 1, dtype=bool)
            for edge in (blob[0], blob[-1], blob[:, 0], blob[:, -1]):
                touches[np.unique(edge)] = True
            small = (sizes < 0.6 / (scale * scale)) & ~touches
            small[0] = False
            fill = small[blob]
            if fill.any():
                seen2 = seen.reshape(nh, nw)
                _d, (ii, jj) = ndimage.distance_transform_edt(~seen2, return_indices=True)
                t2 = tex.reshape(nh, nw, 3)
                src = t2[ii, jj]
                t2[fill] = src[fill]
                seen = (seen2 | fill).ravel()
        used = [used[v] + (int((assigned == v).sum()),) for v in order if (assigned == v).any()]
        used = [(p, r, n_) for p, r, _n0, n_ in used]
    tex = tex.reshape(nh, nw, 3)
    seen_img = seen.reshape(nh, nw)
    # Masque « vu » au pas SEEN_CELL_M (ligne 0 = bas, comme t).
    sw, sh = max(1, int(math.ceil(W / SEEN_CELL_M))), max(1, int(math.ceil(H / SEEN_CELL_M)))
    si = np.clip((S.ravel() / W * sw).astype(int), 0, sw - 1)
    sj = np.clip((T.ravel() / H * sh).astype(int), 0, sh - 1)
    cnt_in = np.bincount(sj * sw + si, weights=inside.astype(float), minlength=sw * sh)
    cnt_seen = np.bincount(sj * sw + si, weights=seen.astype(float), minlength=sw * sh)
    seen_cells = (cnt_seen >= 0.5 * np.maximum(cnt_in, 1)).reshape(sh, sw)
    coverage = float(seen.sum() / max(inside.sum(), 1))
    return tex, seen_cells, scale, coverage, seen_img, used


def equalize_seen(tex, seen_img):
    """Égalisation d'histogramme par canal, calculée sur les seuls texels
    vus (le gris « non vu » ne compte pas), pour la DÉTECTION seulement.
    Mesuré sur un collectif clair en plein soleil : les fenêtres à volets
    blancs d'une façade surexposée passent d'un score OWLv2 ≈ 0,2 (sous le
    seuil) à 0,26–0,34 ; sur une façade déjà contrastée, 0,42 → 0,53."""
    out = np.full_like(tex, 128)
    if not seen_img.any():
        return out
    for c in range(3):
        ch = tex[..., c]
        hist = np.bincount(ch[seen_img].ravel(), minlength=256).astype(float)
        cdf = np.cumsum(hist)
        cdf = (cdf - cdf[hist.nonzero()[0][0]]) / max(cdf[-1] - cdf[hist.nonzero()[0][0]], 1.0)
        lut = np.clip(np.round(cdf * 255), 0, 255).astype(np.uint8)
        out[..., c] = np.where(seen_img, lut[ch], 128)
    return out


def vegetation_fraction(tex, o, scale):
    """Part de pixels verts (feuillage) dans le cadre d'une baie : un cadre
    posé sur le haut d'une haie, au bord de la zone vue, n'est pas une
    fenêtre (constaté)."""
    h = tex.shape[0]
    x0, x1 = int(o['s0'] / scale), int(math.ceil(o['s1'] / scale))
    y0, y1 = int(h - o['t1'] / scale), int(math.ceil(h - o['t0'] / scale))
    sub = tex[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)].astype(np.int32)
    if sub.size == 0:
        return 0.0
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    green = (g > r * 1.05) & (g > b * 1.05) & (g - np.minimum(r, b) > 12)
    return float(green.mean())


def fix_door_labels(openings, seen):
    """Une porte part du sol : une « porte » dont le cadre commence à plus
    d'1 m est une fenêtre à volets (le détecteur prend souvent une fenêtre
    fermée par ses volets pour une porte)… SAUF si le bas du cadre est
    caché : constaté sur une maison derrière une clôture, la porte d'entrée
    n'était détectée qu'au-dessus de la clôture et devenait une fenêtre."""
    out = []
    for o in openings:
        if o['label'] == 'door' and o['t0'] > 1.0:
            below = seen_fraction(seen, o['s0'], max(o['t0'] - 0.6, 0.0), o['s1'], o['t0'])
            if below >= 0.5:
                o = {**o, 'label': 'window'}
        out.append(o)
    return out


def encode_seen(cells):
    return {'cell_m': SEEN_CELL_M, 'rows': [''.join('1' if v else '0' for v in row) for row in cells]}


def seen_fraction(seen, s0, t0, s1, t1):
    """Part vue du rectangle (s, t) d'après le masque encodé (1 si absent)."""
    if not seen or not seen.get('rows'):
        return 1.0
    c = seen['cell_m']
    rows = seen['rows']
    j0, j1 = max(0, int(t0 / c)), min(len(rows) - 1, int(max(t1 - 1e-6, t0) / c))
    i0 = max(0, int(s0 / c))
    vals = []
    for j in range(j0, j1 + 1):
        row = rows[j]
        i1 = min(len(row) - 1, int(max(s1 - 1e-6, s0) / c))
        vals.extend(row[i] == '1' for i in range(i0, i1 + 1))
    return float(np.mean(vals)) if vals else 1.0


# ── Grille des baies ───────────────────────────────────────────────────────────

def _cluster(values, tol):
    order = np.argsort(values)
    groups, cur = [], [order[0]]
    for a, b in zip(order[:-1], order[1:]):
        if values[b] - values[a] <= tol:
            cur.append(b)
        else:
            groups.append(cur); cur = [b]
    groups.append(cur)
    return groups


def regularize_openings(openings, plane, seen):
    """Aligne les fenêtres détectées sur une grille (étages × travées) et
    complète la grille dans les parties NON VUES de la façade (haie, talus,
    voiture) — jamais dans une partie vue sans baie (mur réellement aveugle).
    Retourne (baies, grille ou None)."""
    wins = [o for o in openings if o['label'] == 'window']
    others = [o for o in openings if o['label'] != 'window']
    if len(wins) < 2:
        return openings, None
    outline = plane.get('outline')
    tc = np.array([(o['t0'] + o['t1']) / 2 for o in wins])
    sc = np.array([(o['s0'] + o['s1']) / 2 for o in wins])
    rows = _cluster(tc, 0.45)
    cols = _cluster(sc, 0.35)
    row_t = [(float(np.median([wins[i]['t0'] for i in g])), float(np.median([wins[i]['t1'] for i in g])))
             for g in rows]
    col_s = [(float(np.median([wins[i]['s0'] for i in g])), float(np.median([wins[i]['s1'] for i in g])))
             for g in cols]
    # Alignement : chaque fenêtre prend les bords médians de son étage et de sa travée.
    row_of = {i: r for r, g in enumerate(rows) for i in g}
    col_of = {i: c for c, g in enumerate(cols) for i in g}
    snapped = []
    for i, o in enumerate(wins):
        (t0, t1), (s0, s1) = row_t[row_of[i]], col_s[col_of[i]]
        snapped.append({**o, 's0': round(s0, 2), 's1': round(s1, 2), 't0': round(t0, 2), 't1': round(t1, 2)})
    # Étages : extrapolés vers le haut et le bas au pas d'étage observé (ou
    # 2,8 m) tant qu'on reste dans la façade — typiquement un rez-de-chaussée
    # caché par la haie.
    centers = sorted((a + b) / 2 for a, b in row_t)
    pitch = float(np.median(np.diff(centers))) if len(centers) >= 2 else 2.8
    if not (2.3 <= pitch <= 4.5):
        pitch = 2.8
    h_win = float(np.median([b - a for a, b in row_t]))
    all_rows = list(row_t)
    c = centers[0] - pitch
    while c - h_win / 2 >= 0.6:
        all_rows.append((c - h_win / 2, c + h_win / 2)); c -= pitch
    c = centers[-1] + pitch
    while c + h_win / 2 <= plane['height'] - 0.4:
        all_rows.append((c - h_win / 2, c + h_win / 2)); c += pitch
    # Remplissage : uniquement si la grille est établie (≥ 2 étages ou ≥ 2 travées).
    if len(rows) < 2 and len(cols) < 2:
        return snapped + others, None
    occupied = [sg_box(o) for o in snapped + others]
    added = []
    for (t0, t1) in all_rows:
        for (s0, s1) in col_s:
            if seen_fraction(seen, s0, t0, s1, t1) > 0.3:
                continue
            box = (s0, t0, s1, t1)
            if any(_overlap(box, b) for b in occupied):
                continue
            if outline is not None:
                import shapely.geometry as sgeo
                r = sgeo.box(s0, t0, s1, t1)
                if r.intersection(outline.buffer(-0.05)).area < 0.9 * r.area:
                    continue
            added.append({'label': 'window', 'score': 0.0, 'source': 'grille',
                          's0': round(s0, 2), 't0': round(t0, 2), 's1': round(s1, 2), 't1': round(t1, 2)})
            occupied.append(box)
    grid = {'rows': len(all_rows), 'cols': len(col_s), 'pitch_m': round(pitch, 2), 'n_added': len(added)}
    return snapped + others + added, grid


def sg_box(o):
    return (o['s0'], o['t0'], o['s1'], o['t1'])


def _overlap(a, b, margin=0.1):
    return not (a[2] <= b[0] + margin or b[2] <= a[0] + margin or a[3] <= b[1] + margin or b[3] <= a[1] + margin)
