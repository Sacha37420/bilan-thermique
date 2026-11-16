"""Repli MNS quand le nuage LiDAR HD manque (Lot 31).

La BD TOPO ne sert jamais seule à placer et dimensionner le bâtiment (propriétaire, 2026-11-16). Sans dalle
LiDAR HD (zone pas encore publiée, service des dalles injoignable), la surface du sursol est prise sur un
**modèle numérique de surface** de l'IGN (WMS-R, valeurs brutes `image/x-bil;bits=32`) :

1. **MNS LiDAR HD** (`IGNF_LIDAR-HD_MNS_…`, 50 cm) et son MNT, là où ils sont publiés ;
2. sinon **MNS Correl** (`ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES.MNS`, photogrammétrie des prises de vue
   aériennes, couverture nationale) et le MNT du **RGE ALTI** (`…HIGHRES`). Arêtes moins nettes que le
   LiDAR (relevé à Tours, 2026-11-16 : 95ᵉ centile de l'écart au MNS LiDAR, 10,8 m, sur les bords de toits).

Le MNS est transformé en **nuage synthétique** (un point par maille de 50 cm), classé sans la BD TOPO —
sinon le recalage confirmerait la BD TOPO par construction : sol (sursol < 0,5 m), bâtiment (sursol ≥ 2,5 m,
pas de végétation), végétation haute (sursol ≥ 2,5 m et NDVI > 0,2 sur l'orthophoto infrarouge ; sans elle,
surface localement rugueuse), végétation basse entre les deux. Ce nuage passe ensuite par la même chaîne que le LiDAR
(`observed_env.build_objects` : recalage des emprises BD TOPO, toits, arbres, terrain).
Limites : un MNS ne voit que le dessus (pas de trouée sous les arbres : houppiers opaques), et le MNS
Correl date de la prise de vue, pas du relevé LiDAR.
"""

import numpy as np
import requests
from scipy import ndimage

from . import net
from .geodata import GeodataError
from .lidar_source import (CLASS_BUILDING, CLASS_GROUND, CLASS_VEG_HIGH, CLASS_VEG_LOW, WMS_R_URL,
                           LidarUnavailable, _throttled_get)

CELL_M = 0.5
MAX_PX = 2048
NODATA_BELOW = -1000.0
SOURCES = [
    ('mns_lidar', 'IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93',
     'IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93', 'MNS LiDAR HD (IGN)'),
    ('mns_correl', 'ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES.MNS', 'ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES',
     'MNS Correl et RGE ALTI (IGN)'),
]
GROUND_MAX_M = 0.5
HIGH_MIN_M = 2.5
ROUGH_MAX_M = 0.35
ROUGH_MAX_CORREL_M = 0.6
NDVI_VEG = 0.2
MIN_VALID = 0.9


def fetch_grid(layer, bbox_l93, cell=CELL_M):
    """Altitudes (m, IGN69) d'une couche WMS-R sur l'emprise : tableau (lignes du nord au sud). NaN = sans donnée."""
    xmin, ymin, xmax, ymax = bbox_l93
    w = max(16, min(MAX_PX, int(round((xmax - xmin) / cell))))
    h = max(16, min(MAX_PX, int(round((ymax - ymin) / cell))))
    params = {'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap', 'LAYERS': layer, 'STYLES': '',
              'CRS': 'EPSG:2154', 'BBOX': f'{xmin},{ymin},{xmax},{ymax}', 'WIDTH': str(w), 'HEIGHT': str(h),
              'FORMAT': 'image/x-bil;bits=32'}
    try:
        resp = _throttled_get(WMS_R_URL, params=params)
    except requests.RequestException as exc:
        raise GeodataError(f"Modèle numérique de surface injoignable ({net.reason(exc)}).") from exc
    data = resp.content
    if len(data) != w * h * 4:
        raise GeodataError('Modèle numérique de surface : réponse inattendue.')
    a = np.frombuffer(data, dtype='<f4').reshape(h, w).astype(float)
    a[(a < NODATA_BELOW) | ~np.isfinite(a)] = np.nan
    return a


def classify(mns, mnt, ndvi=None, rough_max=ROUGH_MAX_M):
    """Classes LiDAR (sol, végétation basse/haute, bâtiment) d'une grille MNS/MNT."""
    h = mns - mnt
    # Écart à la médiane locale : un bord de toit (marche franche) le garde nul, un houppier (surface bruitée) non.
    detail = mns - ndimage.median_filter(mns, size=3, mode='nearest')
    rough = np.sqrt(np.maximum(ndimage.uniform_filter(detail ** 2, size=3, mode='nearest'), 0.0))
    cls = np.full(mns.shape, CLASS_VEG_LOW, dtype=np.uint8)
    cls[h < GROUND_MAX_M] = CLASS_GROUND
    high = h >= HIGH_MIN_M
    # Avec l'infrarouge, c'est lui qui décide : la rugosité seule prenait cheminées et lucarnes pour des arbres
    # (346 « arbres » sur le MNS LiDAR à Tours, contre 83 dans le nuage classé, 2026-11-16).
    veg = (ndvi > NDVI_VEG) if ndvi is not None else (rough > rough_max)
    cls[high & veg] = CLASS_VEG_HIGH
    cls[high & ~veg] = CLASS_BUILDING
    return cls


def _ndvi_grid(bbox_l93, shape):
    """NDVI de l'orthophoto infrarouge, rééchantillonné sur la grille ; None si indisponible."""
    from . import lidar_source
    try:
        irc, _w, _h = lidar_source.fetch_orthophoto(bbox_l93, lidar_source.ORTHO_IRC_LAYER, px_per_m=1.0 / CELL_M)
        img = lidar_source.decode_jpeg(irc).astype(float) / 255.0
    except (GeodataError, OSError, ValueError):
        return None
    lin = img ** 2.2
    nir, red = lin[..., 0], lin[..., 1]
    nd = (nir - red) / np.maximum(nir + red, 1e-6)
    zoom = (shape[0] / nd.shape[0], shape[1] / nd.shape[1])
    return ndimage.zoom(nd, zoom, order=0)[:shape[0], :shape[1]]


def synthetic_points(bbox_l93, fetch=None, ndvi=None):
    """(points (e, n, z, cls), description) depuis le premier MNS disponible ; lève LidarUnavailable sinon."""
    fetch = fetch or fetch_grid
    ndvi = ndvi or _ndvi_grid
    errors = []
    for key, mns_layer, mnt_layer, label in SOURCES:
        try:
            mns = fetch(mns_layer, bbox_l93)
            mnt = fetch(mnt_layer, bbox_l93)
        except GeodataError as exc:
            errors.append(str(exc))
            continue
        if mns.shape != mnt.shape:
            continue
        valid = np.isfinite(mns) & np.isfinite(mnt)
        if valid.mean() < MIN_VALID:
            errors.append(f'{label} : zone non couverte')
            continue
        mns = np.where(valid, mns, mnt)
        mnt = np.where(np.isfinite(mnt), mnt, np.nanmedian(mnt))
        mns = np.where(np.isfinite(mns), mns, mnt)
        nd = ndvi(bbox_l93, mns.shape)
        # Sans infrarouge, seule la rugosité départage ; le MNS Correl (photogrammétrie), plus bruité sur les toits,
        # demande un seuil plus haut.
        rough_max = ROUGH_MAX_M if key == 'mns_lidar' else ROUGH_MAX_CORREL_M
        cls = classify(mns, mnt, nd, rough_max)
        xmin, ymin, xmax, ymax = bbox_l93
        rows, cols = mns.shape
        ce = xmin + (np.arange(cols) + 0.5) * (xmax - xmin) / cols
        cn = ymax - (np.arange(rows) + 0.5) * (ymax - ymin) / rows
        E, N = np.meshgrid(ce, cn)
        keep = valid.ravel()
        pts = (E.ravel()[keep], N.ravel()[keep], mns.ravel()[keep], cls.ravel()[keep])
        desc = {'source': key, 'libelle': label, 'maille_m': CELL_M, 'ndvi': nd is not None,
                'points': int(keep.sum()),
                'batiment': round(float((cls == CLASS_BUILDING).mean()), 3),
                'vegetation': round(float((cls == CLASS_VEG_HIGH).mean()), 3)}
        return pts, desc
    raise LidarUnavailable('aucun modèle numérique de surface sur cette zone'
                           + (f' ({errors[-1]})' if errors else ''))
