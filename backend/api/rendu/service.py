"""Client du service de rendu (I8, conteneur `bilan-thermique-rendu`) : PDF, images des figures, images 3D.

Réglages : `RENDU_SERVICE_URL` (défaut `http://bilan-thermique-rendu:3000`), `RENDU_SERVICE_KEY` (`.env` de l'app),
lus dans `settings` s'ils y sont, sinon dans l'environnement. Toute erreur devient `RenduServiceError` au
message **fixe** (jamais l'URL ni la réponse du service).
"""

import base64

import requests
from decouple import config
from django.conf import settings

DELAI_S = 75  # le service coupe à 60 s ; marge pour la file


class RenduServiceError(RuntimeError):
    pass


def _reglage(nom, defaut=''):
    v = getattr(settings, nom, None)
    return v if v not in (None, '') else config(nom, default=defaut)


def _post(chemin, corps, binaire=False):
    url = _reglage('RENDU_SERVICE_URL', 'http://bilan-thermique-rendu:3000').rstrip('/')
    cle = _reglage('RENDU_SERVICE_KEY', '')
    if not cle:
        raise RenduServiceError('Service de rendu non configuré.')
    try:
        r = requests.post(url + chemin, json=corps, headers={'X-Rendu-Cle': cle}, timeout=DELAI_S)
    except requests.RequestException:
        raise RenduServiceError('Service de rendu injoignable.') from None
    if r.status_code != 200:
        raise RenduServiceError('Le service de rendu a refusé le document.' if r.status_code in (400, 413)
                                else 'Le service de rendu n’a pas répondu à temps.' if r.status_code in (503, 504)
                                else 'Erreur du service de rendu.')
    if binaire:
        if not r.content.startswith(b'%PDF'):
            raise RenduServiceError('Erreur du service de rendu.')
        return r.content
    try:
        return r.json()
    except ValueError:
        raise RenduServiceError('Erreur du service de rendu.') from None


def pdf(html, fmt='A4', paysage=False, marges_mm=14):
    return _post('/pdf', {'html': html, 'format': fmt, 'paysage': bool(paysage), 'marges_mm': marges_mm}, binaire=True)


def _png(data_url):
    if not isinstance(data_url, str) or not data_url.startswith('data:image/png;base64,'):
        raise RenduServiceError('Erreur du service de rendu.')
    raw = base64.b64decode(data_url.split(',', 1)[1])
    if not raw.startswith(b'\x89PNG'):
        raise RenduServiceError('Erreur du service de rendu.')
    return raw


def images(figures, largeur=1200, hauteur=700, echelle=2):
    """[octets PNG], dans l'ordre des figures (par paquets de 40)."""
    out = []
    for i in range(0, len(figures), 40):
        rep = _post('/images', {'figures': figures[i:i + 40], 'largeur': largeur, 'hauteur': hauteur, 'echelle': echelle})
        imgs = rep.get('images') if isinstance(rep, dict) else None
        if not isinstance(imgs, list) or len(imgs) != len(figures[i:i + 40]):
            raise RenduServiceError('Erreur du service de rendu.')
        out += [_png(x) for x in imgs]
    return out


def vue3d(scene, grandeur, camera='iso', largeur=1200, hauteur=800):
    rep = _post('/vue3d', {'scene': scene, 'grandeur': grandeur, 'camera': camera, 'largeur': largeur,
                           'hauteur': hauteur})
    return _png(rep.get('image') if isinstance(rep, dict) else None)
