"""Poses des photos Panoramax par photogrammétrie (Lot AM).

Le recalage photo par photo (Lots AK–AL) ne tenait pas : la position GPS est
à ~4 m près, le cap annoncé (`view:azimuth`) s'est révélé faux de 4 à 12° et
l'inclinaison varie de 3 à 5° d'une photo à l'autre (véhicule). Aucune
recherche locale sur les arêtes du modèle ne rattrape cela de façon stable.

Ici, les photos d'une zone sont reconstruites ENSEMBLE par COLMAP
(pycolmap, BSD, modèle de caméra équirectangulaire natif, sans GPU) : leurs
positions et orientations relatives sont alors exactes à quelques
centimètres près. Chaque modèle reconstruit est ensuite placé dans le repère
local du bâtiment :
1. verticale : moyenne des axes « haut » des panoramas ;
2. similitude horizontale (échelle, rotation, translation) ajustée par
   RANSAC sur les positions GPS de TOUTES les photos du modèle — l'erreur
   GPS se moyenne (constaté : écart médian 1,6 m photo par photo, le modèle
   entier tombe pile sur les arêtes LiDAR) ;
3. hauteur : terrain LiDAR sous les prises de vue + hauteur d'appareil
   (médiane).

Pièges constatés sur données réelles (2026-09-27) :
- un contributeur incruste un bandeau illustré identique au bas de chaque
  panorama : ses points, identiques partout, faisaient conclure à COLMAP que
  l'appareil ne bougeait pas → masque de −25° à +50° d'inclinaison (écarte
  aussi le véhicule et le ciel) ;
- l'amorçage exige par défaut 16° de triangulation, impossible avec 1,4 m
  entre photos et des façades à 15 m → 2°.
"""

import math
import os
import shutil
import time
from pathlib import Path

import numpy as np

from . import facades as F

SFM_DIR = os.path.join(F.CACHE_DIR, 'sfm')
SFM_WIDTH = 4096
MASK_PITCH = (-25.0, 50.0)
GPS_INLIER_M = 4.0


class SfmError(ValueError):
    pass


def _mask(width, height):
    from PIL import Image
    pitch = 90 - (np.arange(height) + 0.5) * 180 / height
    keep = ((pitch > MASK_PITCH[0]) & (pitch < MASK_PITCH[1])).astype(np.uint8) * 255
    return Image.fromarray(np.repeat(keep[:, None], width, axis=1))


def reconstruct(panos, workdir, progress=None):
    """Reconstruction COLMAP des panoramas `panos` (dicts Panoramax, 'xy'
    local renseigné). Retourne la liste des modèles pycolmap (≥ 3 images)
    et la table nom de fichier → pano."""
    import pycolmap
    from PIL import Image

    work = Path(workdir)
    shutil.rmtree(work, ignore_errors=True)
    (work / 'panos').mkdir(parents=True)
    (work / 'masks').mkdir()
    by_name = {}
    seqs = sorted({p.get('sequence') or '' for p in panos})
    ordered = sorted(panos, key=lambda p: (p.get('sequence') or '', p.get('datetime') or ''))
    for k, p in enumerate(ordered):
        img = Image.fromarray(F.load_panorama(p, 'hd'))
        name = f"{seqs.index(p.get('sequence') or ''):02d}_{k:03d}_{p['id'][:8]}.jpg"
        img.resize((SFM_WIDTH, SFM_WIDTH // 2), Image.LANCZOS).save(work / 'panos' / name, quality=92)
        by_name[name] = p
        if progress:
            progress('Préparation des photos', (k + 1) / len(ordered))
    mask = _mask(SFM_WIDTH, SFM_WIDTH // 2)
    for name in by_name:
        mask.save(work / 'masks' / (name + '.png'))

    db = work / 'database.db'
    eo = pycolmap.FeatureExtractionOptions(use_gpu=False, num_threads=2)
    if progress:
        progress('Points caractéristiques', 0.0)
    pycolmap.extract_features(
        db, work / 'panos',
        reader_options=pycolmap.ImageReaderOptions(camera_model='EQUIRECTANGULAR', mask_path=work / 'masks'),
        camera_mode=pycolmap.CameraMode.SINGLE, extraction_options=eo)
    if progress:
        progress('Appariement des photos', 0.0)
    mo = pycolmap.FeatureMatchingOptions()
    mo.use_gpu = False
    mo.num_threads = 2
    pycolmap.match_exhaustive(db, matching_options=mo)
    if progress:
        progress('Reconstruction 3D', 0.0)
    opts = pycolmap.IncrementalPipelineOptions(num_threads=2)
    opts.mapper.init_min_tri_angle = 2.0
    opts.min_model_size = 3
    (work / 'sparse').mkdir()
    recs = pycolmap.incremental_mapping(db, work / 'panos', work / 'sparse', opts)
    models = [r for r in recs.values() if r.num_reg_images() >= 3]
    return models, by_name


def _umeyama2(a, b):
    ma, mb = a.mean(0), b.mean(0)
    A, B = a - ma, b - mb
    U, S, Vt = np.linalg.svd(B.T @ A)
    D = np.diag([1.0, np.sign(np.linalg.det(U @ Vt))])
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / max((A ** 2).sum(), 1e-12))
    return s, R, mb - s * R @ ma


def align_poses(C, Rw, G, ground_at, rng_seed=0):
    """Place des poses reconstruites (centres C, rotations monde ← caméra Rw,
    repère arbitraire) dans le repère local d'après les positions GPS G (x, y
    locaux). Retourne (centres, rotations, résidus GPS, statistiques)."""
    C, Rw, G = np.asarray(C, float), np.asarray(Rw, float), np.asarray(G, float)
    n = len(C)
    if n < 3:
        raise SfmError("Moins de 3 photos dans le modèle.")
    # 1) Verticale : axe « haut » des panoramas (y image vers le bas).
    up = np.array([R @ np.array([0.0, -1.0, 0.0]) for R in Rw]).mean(axis=0)
    up /= np.linalg.norm(up)
    v = np.cross(up, [0.0, 0.0, 1.0])
    s_, c_ = np.linalg.norm(v), float(up[2])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    Rg = np.eye(3) + vx + vx @ vx * ((1 - c_) / s_ ** 2) if s_ > 1e-9 else np.eye(3)
    Cg = C @ Rg.T
    # 2) Similitude horizontale, RANSAC sur paires de photos éloignées.
    rng = np.random.default_rng(rng_seed)
    best = None
    for _ in range(min(400, n * (n - 1))):
        i, j = rng.choice(n, 2, replace=False)
        if np.linalg.norm(G[i] - G[j]) < 3.0:
            continue
        s, Rz, t = _umeyama2(Cg[[i, j], :2], G[[i, j]])
        res = np.linalg.norm(s * (Cg[:, :2] @ Rz.T) + t - G, axis=1)
        inl = res < GPS_INLIER_M
        if best is None or inl.sum() > best.sum():
            best = inl
    if best is None or best.sum() < 3:
        raise SfmError("Calage GPS impossible (photos trop groupées).")
    spread = float(np.linalg.norm(G[best].max(0) - G[best].min(0)))
    if spread < 8.0:
        raise SfmError(f"Photos trop proches les unes des autres ({spread:.0f} m) pour caler l'orientation.")
    s, Rz, t = _umeyama2(Cg[best, :2], G[best])
    res = np.linalg.norm(s * (Cg[:, :2] @ Rz.T) + t - G, axis=1)
    R3 = np.eye(3)
    R3[:2, :2] = Rz
    S = R3 @ Rg
    Cw = s * (C @ S.T)
    Cw[:, :2] += t
    # 3) Hauteur : terrain LiDAR + hauteur d'appareil, en médiane.
    Cw[:, 2] += float(np.median([ground_at(x, y) + F.CAMERA_HEIGHT_M - z for x, y, z in Cw]))
    Rworld = np.array([S @ R for R in Rw])
    stats = {'n_images': n, 'n_inliers': int(best.sum()), 'scale': round(s, 4),
             'gps_residual_median_m': round(float(np.median(res[best])), 2)}
    return Cw, Rworld, res, stats


def align_model(rec, by_name, ground_at):
    """Modèle pycolmap → ({pano_id: pose}, statistiques). Lève SfmError si le
    calage n'est pas crédible."""
    names, C, Rw, G = [], [], [], []
    for img in rec.images.values():
        if img.name not in by_name:
            continue
        T = img.cam_from_world()
        R = np.array(T.rotation.matrix())
        C.append(-R.T @ np.array(T.translation))
        Rw.append(R.T)
        names.append(img.name)
        G.append(by_name[img.name]['xy'])
    Cw, Rworld, res, stats = align_poses(C, Rw, G, ground_at)
    out = {}
    for k, name in enumerate(names):
        out[by_name[name]['id']] = {
            'camera': [float(c) for c in Cw[k]], 'R': Rworld[k].tolist(),
            'source': 'sfm', 'gps_residual_m': round(float(res[k]), 2),
        }
    stats.update(n_points=int(rec.num_points3D()),
                 reproj_px=round(float(rec.compute_mean_reprojection_error()), 3))
    return out, stats


def project(points, cam, R, width, height):
    """Points du repère local → pixels (x, y) d'une photo équirectangulaire
    de pose SfM (R : monde ← caméra ; convention COLMAP : x droite, y bas,
    z avant)."""
    d = (np.asarray(points, float) - np.asarray(cam, float)) @ np.asarray(R, float)
    yaw = np.arctan2(d[:, 0], d[:, 2])
    pitch = -np.arctan2(d[:, 1], np.hypot(d[:, 0], d[:, 2]))
    return (1 + yaw / np.pi) / 2 * width, (1 - 2 * pitch / np.pi) / 2 * height


def solve_poses(panos, workdir, ground_at, progress=None):
    """Poses SfM de toutes les photos reconstructibles. Retourne ({id: pose},
    rapport). Ne lève pas : en cas d'échec, dictionnaire vide et rapport."""
    t0 = time.time()
    report = {'n_panos': len(panos), 'models': [], 'errors': []}
    try:
        models, by_name = reconstruct(panos, workdir, progress)
    except Exception as exc:  # noqa: BLE001 — la photogrammétrie est un plus, pas un prérequis
        report['errors'].append(f"Reconstruction impossible ({exc}).")
        return {}, report
    poses = {}
    for rec in models:
        try:
            out, stats = align_model(rec, by_name, ground_at)
        except SfmError as exc:
            report['errors'].append(str(exc))
            continue
        poses.update(out)
        report['models'].append(stats)
    report['n_registered'] = len(poses)
    report['seconds'] = round(time.time() - t0)
    return poses, report


def pose_angles(R, north_offset_deg):
    """Cap (0 = nord) et inclinaison du centre d'image d'une pose SfM."""
    fwd = np.asarray(R, float) @ np.array([0.0, 0.0, 1.0])
    enu = F._true_enu(fwd[None], north_offset_deg)[0]
    return (math.degrees(math.atan2(enu[0], enu[1])) % 360.0,
            math.degrees(math.asin(enu[2] / np.linalg.norm(enu))))
