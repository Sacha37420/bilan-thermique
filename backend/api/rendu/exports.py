"""Exports transitoires (I6.d) : HTML, DOCX, PDF d'un document résolu, réservés à leur auteur, 24 h.

Le document est **résolu au moment de la demande**, avec les droits de la personne qui la fait (la tâche de fond
n'a pas d'utilisateur : elle ne relit aucune donnée, elle met en forme ce qui a été résolu). Fichiers dans
`RENDU_EXPORTS_DIR` (volume `rendu-exports`, non `external`, partagé par le backend et le worker), un dossier par
export ; purge des expirés à chaque demande, à chaque liste et à chaque tâche (`purger`).
"""

import json
import re
import shutil
import unicodedata
from datetime import timedelta
from pathlib import Path

from decouple import config
from django.conf import settings
from django.utils import timezone

from ..models import ExportRendu, Job

DUREE = timedelta(hours=24)
TYPES = {'html': 'text/html; charset=utf-8', 'pdf': 'application/pdf',
         'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
MAX_PAR_PERSONNE = 20     # exports vivants par personne
MESSAGES = {'en_attente': 'En file d’attente.', 'en_cours': 'Mise en forme en cours.', 'fait': 'Prêt.',
            'erreur': 'L’export a échoué.'}


def dossier():
    d = getattr(settings, 'RENDU_EXPORTS_DIR', None) or config('RENDU_EXPORTS_DIR', default='/data/rendu-exports')
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dir(export_id):
    return dossier() / str(int(export_id))


def nom_fichier(titre, fmt, quand=None):
    s = unicodedata.normalize('NFKD', titre or 'rendu').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')[:60] or 'rendu'
    return f"{s}-{(quand or timezone.now()).strftime('%Y%m%d-%H%M')}.{fmt}"


def purger():
    n = 0
    for e in ExportRendu.objects.filter(expire_le__lt=timezone.now()):
        shutil.rmtree(_dir(e.pk), ignore_errors=True)
        e.delete()
        n += 1
    return n


def vivants(owner_sub):
    return ExportRendu.objects.filter(owner_sub=owner_sub, expire_le__gte=timezone.now()).order_by('-cree_le')


def creer(owner_sub, doc, spec, cible, fmt, modele=None, sous_titre=''):
    # Lot 40 (parcours réel) : le document porte des dates (calcul d'un potentiel, I5) — json.dumps seul échouait, et
    # l'export restait « en file d'attente » à jamais. Sérialisé AVANT toute écriture en base, avec l'encodeur de Django.
    from django.core.serializers.json import DjangoJSONEncoder
    payload = json.dumps({'doc': doc, 'sous_titre': sous_titre}, cls=DjangoJSONEncoder)
    now = timezone.now()
    job = Job.objects.create(kind='rendu_export', status=Job.PENDING, owner_sub=owner_sub,
                             params={'format': fmt}, message=MESSAGES['en_attente'])
    e = ExportRendu.objects.create(owner_sub=owner_sub, modele=modele, spec=spec, cible=cible, format=fmt,
                                   statut='en_attente', message=MESSAGES['en_attente'], job=job,
                                   expire_le=now + DUREE)
    d = _dir(e.pk)
    d.mkdir(parents=True, exist_ok=True)
    (d / 'document.json').write_text(payload, encoding='utf-8')
    return e


def _fin(e, statut, message, chemin='', taille=0):
    e.statut, e.message, e.chemin, e.taille = statut, message, chemin, taille
    e.save(update_fields=['statut', 'message', 'chemin', 'taille'])
    if e.job_id:
        Job.objects.filter(pk=e.job_id).update(status=Job.DONE if statut == 'fait' else Job.ERROR,
                                               progress=100, message=message[:500], updated_at=timezone.now())


def executer(export_id):
    """Met en forme un export (tâche de fond). Ne lève pas : l'échec est écrit dans l'export, message fixe."""
    from . import export_html, service
    e = ExportRendu.objects.filter(pk=export_id).first()
    if e is None or e.statut not in ('en_attente', 'en_cours'):
        return
    e.statut, e.message = 'en_cours', MESSAGES['en_cours']
    e.save(update_fields=['statut', 'message'])
    if e.job_id:
        Job.objects.filter(pk=e.job_id).update(status=Job.RUNNING, message=MESSAGES['en_cours'])
    d = _dir(e.pk)
    try:
        payload = json.loads((d / 'document.json').read_text(encoding='utf-8'))
        doc, sous_titre = payload['doc'], payload.get('sous_titre', '')
        if e.format == 'html':
            data = export_html.rendre(doc, sous_titre).encode('utf-8')
        elif e.format == 'pdf':
            mp = doc['mise_en_page']
            data = service.pdf(export_html.rendre(doc, sous_titre, embarquer=False), mp['format'],
                               mp['orientation'] == 'paysage', export_html.MARGES_MM)
        else:
            from . import export_docx          # python-docx : importé seulement pour un export Word
            data = export_docx.rendre(doc, sous_titre)
    except service.RenduServiceError as exc:
        _fin(e, 'erreur', str(exc))
        return
    except Exception:  # noqa: BLE001 — message fixe, jamais la pile
        import logging
        logging.getLogger(__name__).exception('export de rendu %s', e.pk)
        _fin(e, 'erreur', MESSAGES['erreur'])
        return
    nom = nom_fichier(doc.get('titre'), e.format, e.cree_le)
    (d / nom).write_bytes(data)
    (d / 'document.json').unlink(missing_ok=True)
    _fin(e, 'fait', MESSAGES['fait'], nom, len(data))


def fichier(e):
    if e.statut != 'fait' or not e.chemin or '/' in e.chemin or e.chemin.startswith('.'):
        return None
    p = _dir(e.pk) / e.chemin
    return p if p.is_file() else None


def public(e):
    return {'id': e.pk, 'format': e.format, 'statut': e.statut, 'message': e.message or MESSAGES.get(e.statut, ''),
            'taille': e.taille, 'nom_fichier': e.chemin or nom_fichier('rendu', e.format, e.cree_le),
            'cree_le': e.cree_le, 'expire_le': e.expire_le}
