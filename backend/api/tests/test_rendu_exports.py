"""Lot 38 (secteur R) — exports HTML (autonome, interactif hors ligne), PDF (service I8 simulé ici, éprouvé en réel
à part), DOCX (python-docx, relu) ; fichiers transitoires réservés à leur auteur ; client du service de rendu."""

import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .. import testing
from ..models import ExportRendu
from ..rendu import exports, gabarits, service
from .test_rendu_fixtures import HOSTILE, RenduMixin, png, run

_NO_AUTO_ENV = testing.without_auto_environment()
_TMP = tempfile.mkdtemp(prefix='rendu-essai-')

try:
    import docx  # noqa: F401
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()
    shutil.rmtree(_TMP, ignore_errors=True)


def fiche():
    return next(m for m in gabarits.assurer() if m.nom == 'Fiche d’identité')


def synthese():
    return next(m for m in gabarits.assurer() if m.nom == 'Synthèse de parc')


@override_settings(RENDU_EXPORTS_DIR=_TMP, RENDU_SERVICE_KEY='cle-de-test-tres-longue-123456', RENDU_SERVICE_URL='http://rendu.test:3000')
class ExportsTest(RenduMixin, TestCase):
    def setUp(self):
        super().setUp()
        run(self.b1, 'N2')                                   # une vue 3D résolue (I4 simulé)
        p = mock.patch('api.rendu.taches.rendu_export.delay', side_effect=lambda pk: exports.executer(pk))
        p.start()
        self.addCleanup(p.stop)

    def exporter(self, client, fmt, cible, modele):
        with self.captureOnCommitCallbacks(execute=True):
            r = client.post('/api/rendu/exports/', {'modele_id': modele.pk, 'cible': cible, 'format': fmt}, format='json')
        return r

    def fichier(self, client, e):
        r = client.get(f'/api/rendu/exports/{e}/fichier')
        return r, b''.join(r.streaming_content) if r.status_code == 200 else b''

    def test_html_autonome(self):
        r = self.exporter(self.g, 'html', {'batiment': self.b1.pk}, fiche())
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(set(r.json()), {'id', 'statut', 'format', 'cree_le'})
        eid = r.json()['id']
        d = self.g.get(f'/api/rendu/exports/{eid}/').json()
        self.assertEqual(set(d), {'id', 'format', 'statut', 'message', 'taille', 'nom_fichier', 'cree_le', 'expire_le'})
        self.assertEqual(d['statut'], 'fait', d)
        rf, html = self.fichier(self.g, eid)
        self.assertEqual(rf['Content-Type'], 'text/html; charset=utf-8')
        self.assertIn('attachment', rf['Content-Disposition'])
        self.assertEqual(rf['Content-Security-Policy'], "sandbox; default-src 'none'")
        self.assertEqual(len(html), d['taille'])
        h = html.decode('utf-8')
        self.assertIn('plotly.js v2.35.2', h)                                     # embarqué
        self.assertIn("REVISION = '180'", h)                                      # three.js : vue 3D présente
        self.assertIn("default-src 'none'", h)                                    # CSP du document
        from ..rendu import export_html
        hors_libs = h.replace(export_html._lire(export_html.PLOTLY), '').replace(export_html._lire(export_html.THREE), '')
        self.assertLess(len(hors_libs), len(h) - 4_000_000)
        for motif in ('http://', 'https://', '<script src', '<link', '@import', 'url('):
            self.assertNotIn(motif, hors_libs)                                  # aucune ressource externe
        self.assertIn('<a href="#s1">', h)                                         # sommaire cliquable
        self.assertIn('@page', h)
        self.assertIn('data-rendu-figure=', h)
        self.assertIn('data-rendu-3d=', h)
        # Données hostiles : jamais une balise active.
        self.assertNotIn(HOSTILE, h)
        self.assertNotIn('<img src=x', h)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', h)
        # Couleurs d'impression posées à l'export (pas dans la figure servie à l'écran).
        self.assertIn('#0072B2', h)
        if os.environ.get('RENDU_DUMP'):
            print('\n@@HTML_SIZE@@' + str(len(html)) + '@@FIN@@')
        self.assertEqual(self.g.get('/api/rendu/exports/').json()[0]['id'], eid)

    def test_export_d_autrui_refuse(self):
        eid = self.exporter(self.g, 'html', {'parc': self.p.pk}, synthese()).json()['id']
        self.assertEqual(self.l.get(f'/api/rendu/exports/{eid}/').status_code, 404)
        r, _ = self.fichier(self.l, eid)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.l.get('/api/rendu/exports/').json(), [])
        self.assertEqual(self.o.post('/api/rendu/exports/', {'modele_id': synthese().pk, 'cible': {'parc': self.p.pk},
                                                             'format': 'html'}, format='json').status_code, 404)
        self.assertEqual(self.g.post('/api/rendu/exports/', {'modele_id': synthese().pk, 'cible': {'parc': self.p.pk},
                                                             'format': 'odt'}, format='json').status_code, 400)

    def test_expiration_et_purge(self):
        eid = self.exporter(self.g, 'html', {'parc': self.p.pk}, synthese()).json()['id']
        e = ExportRendu.objects.get(pk=eid)
        chemin = exports._dir(eid)
        self.assertTrue(chemin.is_dir())
        self.assertEqual(e.expire_le - e.cree_le > timedelta(hours=23, minutes=59), True)
        ExportRendu.objects.filter(pk=eid).update(expire_le=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.g.get(f'/api/rendu/exports/{eid}/').status_code, 404)
        self.assertEqual(self.g.get('/api/rendu/exports/').json(), [])            # liste : purge
        self.assertFalse(ExportRendu.objects.filter(pk=eid).exists())
        self.assertFalse(chemin.exists())

    def test_pdf_par_le_service(self):
        with mock.patch('api.rendu.service.pdf', return_value=b'%PDF-1.7 essai') as pdf:
            eid = self.exporter(self.g, 'pdf', {'parc': self.p.pk}, synthese()).json()['id']
        d = self.g.get(f'/api/rendu/exports/{eid}/').json()
        self.assertEqual(d['statut'], 'fait')
        html, fmt, paysage, marges = pdf.call_args[0]
        self.assertNotIn('plotly.js v2.35.2', html)               # le service fournit ses bibliothèques
        self.assertIn('window.renduDemarrer', html)
        self.assertEqual((fmt, paysage, marges), ('A4', True, 14))
        r, data = self.fichier(self.g, eid)
        self.assertEqual((r['Content-Type'], data), ('application/pdf', b'%PDF-1.7 essai'))

    def test_pdf_service_injoignable_message_fixe(self):
        with mock.patch('api.rendu.service.requests.post', side_effect=service.requests.ConnectionError('http://secret')):
            eid = self.exporter(self.g, 'pdf', {'parc': self.p.pk}, synthese()).json()['id']
        d = self.g.get(f'/api/rendu/exports/{eid}/').json()
        self.assertEqual((d['statut'], d['message']), ('erreur', 'Service de rendu injoignable.'))
        r, _ = self.fichier(self.g, eid)
        self.assertEqual(r.status_code, 409)

    def test_docx(self):
        if not HAS_DOCX:
            self.skipTest('python-docx absent de cette image')
        import docx
        figs = []

        def images(f, w, h, s):
            figs.extend(f)
            return [png()] * len(f)
        with mock.patch('api.rendu.service.images', side_effect=images), \
                mock.patch('api.rendu.service.vue3d', return_value=png(60, 40)) as v3:
            eid = self.exporter(self.g, 'docx', {'batiment': self.b1.pk}, fiche()).json()['id']
        d = self.g.get(f'/api/rendu/exports/{eid}/').json()
        self.assertEqual(d['statut'], 'fait', d)
        self.assertTrue(d['nom_fichier'].endswith('.docx'))
        r, data = self.fichier(self.g, eid)
        doc = docx.Document(io.BytesIO(data))
        styles = [p.style.name for p in doc.paragraphs]
        titres = [p.text for p in doc.paragraphs if p.style.name == 'Heading 1']
        self.assertEqual(titres, [s['titre'] for s in fiche().spec['sections']])
        self.assertIn('Title', styles)
        self.assertIn('Heading 2', styles)
        self.assertIn('Caption', styles)
        self.assertGreater(len(doc.tables), 5)
        self.assertEqual(doc.tables[0].rows[0].cells[0].text, 'Donnée')
        self.assertEqual(len(doc.inline_shapes), len(figs) + v3.call_count)
        self.assertEqual(v3.call_count, 2)
        for f in figs:                                            # thème d'impression posé avant l'image
            self.assertEqual(f['layout']['paper_bgcolor'], '#FFFFFF')
        xml = zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml').decode()
        self.assertIn('TOC \\o "1-3" \\h \\z \\u', xml)            # sommaire actualisable
        self.assertIn('w:bookmarkStart', xml)
        self.assertIn('w:anchor="s1"', xml)                        # liens internes vers les signets
        self.assertIn('SEQ Figure', xml)
        self.assertIn('w:tblHeader', xml)
        settings_xml = zipfile.ZipFile(io.BytesIO(data)).read('word/settings.xml').decode()
        self.assertIn('w:updateFields', settings_xml)
        self.assertNotIn('<img src=x', xml)
        if os.environ.get('RENDU_DUMP'):
            print('\n@@DOCX_SIZE@@' + str(len(data)) + '@@FIN@@')


@override_settings(RENDU_SERVICE_URL='http://rendu.test:3000')
class ServiceClientTest(TestCase):
    def test_sans_cle(self):
        with override_settings(RENDU_SERVICE_KEY=''), mock.patch.dict(os.environ, {'RENDU_SERVICE_KEY': ''}):
            with self.assertRaisesMessage(service.RenduServiceError, 'Service de rendu non configuré.'):
                service.pdf('<html></html>')

    @override_settings(RENDU_SERVICE_KEY='cle-de-test-tres-longue-123456')
    def test_cle_en_tete_et_reponses(self):
        class R:
            status_code = 200
            content = b'%PDF-1.4'

            def json(self):
                return {}
        with mock.patch('api.rendu.service.requests.post', return_value=R()) as post:
            self.assertEqual(service.pdf('<p>x</p>', 'A3', True, 10), b'%PDF-1.4')
        url, = post.call_args[0]
        self.assertEqual(url, 'http://rendu.test:3000/pdf')
        self.assertEqual(post.call_args[1]['headers'], {'X-Rendu-Cle': 'cle-de-test-tres-longue-123456'})
        self.assertEqual(post.call_args[1]['json'], {'html': '<p>x</p>', 'format': 'A3', 'paysage': True, 'marges_mm': 10})
        R.status_code = 401
        with mock.patch('api.rendu.service.requests.post', return_value=R()):
            with self.assertRaisesMessage(service.RenduServiceError, 'Erreur du service de rendu.'):
                service.pdf('x')
        R.status_code, R.content = 200, b'<html>pas un pdf'
        with mock.patch('api.rendu.service.requests.post', return_value=R()):
            with self.assertRaises(service.RenduServiceError):
                service.pdf('x')

    @override_settings(RENDU_SERVICE_KEY='cle-de-test-tres-longue-123456')
    def test_images_png_verifiees(self):
        import base64
        ok = 'data:image/png;base64,' + base64.b64encode(png()).decode()

        class R:
            status_code = 200

            def __init__(self, imgs):
                self.imgs = imgs

            def json(self):
                return {'images': self.imgs}
        with mock.patch('api.rendu.service.requests.post', return_value=R([ok, ok])):
            self.assertEqual(len(service.images([{}, {}])), 2)
        with mock.patch('api.rendu.service.requests.post', return_value=R(['data:text/html,<b>'])):
            with self.assertRaises(service.RenduServiceError):
                service.images([{}])


__all__ = ['json']
