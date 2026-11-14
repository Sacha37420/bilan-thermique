"""Lot 21 (refonte) — IA documentaire sur le compte de l'utilisateur : coffre, XML DPE sans IA,
extraction par outil (fournisseurs SIMULÉS : aucun test n'appelle une IA), contrôles, validation.
Patron repris d'App Builder (`CoffreClesTests`, `AlbertTests`)."""

import logging
from unittest import mock

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings

from .. import documents, facts, tasks
from ..ia import fournisseurs
from ..models import AIExtractionRun, AiKeyVault, Building, BuildingDocument, FactProposal, Job
from ..testing import client_for, patch_jwks

KEY = Fernet.generate_key().decode()
SECRET = 'sk-ant-api03-SECRET-NE-DOIT-JAMAIS-SORTIR-xyz'
DPE_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<dpe><logement><caracteristique_generale><annee_construction>1978</annee_construction>
<surface_habitable_logement>92,5</surface_habitable_logement></caracteristique_generale>
<enveloppe><mur_collection>
<mur><donnee_entree><surface_paroi_opaque>40</surface_paroi_opaque></donnee_entree><donnee_intermediaire><umur>0.5</umur></donnee_intermediaire></mur>
<mur><donnee_entree><surface_paroi_opaque>60</surface_paroi_opaque></donnee_entree><donnee_intermediaire><umur>1.0</umur></donnee_intermediaire></mur>
</mur_collection></enveloppe>
<sortie><ep_conso><classe_bilan_dpe>E</classe_bilan_dpe><ep_conso_5_usages_m2>301</ep_conso_5_usages_m2></ep_conso></sortie>
</logement></dpe>'''
AUDIT = ("AUDIT ÉNERGÉTIQUE — Maison des Tilleuls\nLes murs ont une résistance faible : coefficient U des murs mesuré à "
         "1,35 W/m².K selon le relevé.\nMenuiseries : double vitrage, Uw = 2,8 W/m².K, posées en 1998.\n"
         "Ventilation : VMC simple flux autoréglable.")


def minimal_pdf(text):
    """PDF d'une page avec du texte extractible (Helvetica), construit à la main."""
    stream = f'BT /F1 11 Tf 40 760 Td ({text}) Tj ET'.encode('latin-1')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = b'%PDF-1.4\n', []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n'.encode() + o + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return out


def fake_claude(faits, arret='outils'):
    """Réponse simulée du fournisseur : un appel à proposer_faits, puis fin."""
    calls = [fournisseurs.Reponse(texte='', appels=[fournisseurs.Appel(id='t1', nom='proposer_faits', entree={'faits': faits})],
                                  arret=arret, usage={'input_tokens': 1200, 'output_tokens': 300},
                                  message_assistant={'role': 'assistant', 'content': []}),
             fournisseurs.Reponse(texte='Terminé.', appels=[], arret='fin', usage={'input_tokens': 50, 'output_tokens': 5})]
    return mock.patch('api.ia.fournisseurs.appeler_anthropic', side_effect=calls)


@override_settings(VAULT_ENCRYPTION_KEY=KEY)
@patch_jwks()
class VaultTest(TestCase):
    def test_key_never_returned_and_encrypted_at_rest(self):
        c = client_for(sub='alice')
        self.assertEqual(c.put('/api/ai-keys/', {'claude_key': SECRET}, format='json').status_code, 204)
        r = c.get('/api/ai-keys/')
        self.assertEqual(r.json()['claude_key_configured'], True)
        self.assertNotIn('SECRET', r.content.decode())
        self.assertNotIn('SECRET', c.get('/api/ai-settings/').content.decode())
        with connection.cursor() as cur:
            cur.execute('SELECT claude_key FROM ai_key_vault')
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith('gAAAAA'))
        self.assertNotIn('SECRET', raw)
        self.assertEqual(client_for(sub='bob').get('/api/ai-keys/').json()['claude_key_configured'], False)
        c.put('/api/ai-keys/', {'claude_key': ''}, format='json')
        self.assertFalse(c.get('/api/ai-keys/').json()['claude_key_configured'])

    def test_subscription_token_refused(self):
        r = client_for(sub='alice').put('/api/ai-keys/', {'claude_key': 'sk-ant-oat01-abcdef'}, format='json')
        self.assertEqual((r.status_code, r.json()['code']), (400, 'oauth_token_refused'))
        self.assertFalse(AiKeyVault.objects.exists())

    def test_no_user_supplied_base_url(self):
        r = client_for(sub='alice').put('/api/ai-keys/', {'base_url': 'http://dev-postgres:5432'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_settings_preferences_on_server(self):
        c = client_for(sub='alice')
        r = c.put('/api/ai-settings/', {'provider': 'mistral', 'models': {'mistral': 'mistral-large-latest'}}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['preferences']['provider'], 'mistral')
        self.assertEqual(c.put('/api/ai-settings/', {'models': {'mistral': 'modele-invente'}}, format='json').status_code, 400)

    @override_settings(VAULT_ENCRYPTION_KEY='')
    def test_vault_closed(self):
        r = client_for(sub='alice').put('/api/ai-keys/', {'claude_key': SECRET}, format='json')
        self.assertEqual(r.status_code, 503)
        self.assertFalse(client_for(sub='alice').get('/api/ai-keys/').json()['vault'])


class LocalReadingTest(TestCase):
    def test_dpe_xml_read_without_ai(self):
        rows = {k: v for k, v, _ in documents.parse_dpe_xml(DPE_XML)}
        self.assertEqual(rows['identite.annee_construction'], 1978)
        self.assertEqual(rows['identite.surface_m2'], 92.5)
        self.assertEqual(rows['mur.u'], 0.8)  # (40×0,5 + 60×1,0) / 100
        self.assertEqual(rows['dpe.etiquette'], 'E')

    def test_xml_entities_refused(self):
        with self.assertRaises(documents.DocumentError):
            documents.parse_dpe_xml(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')

    def test_pdf_text_extracted_locally(self):
        pages = documents.pdf_pages(minimal_pdf('Uw = 2,8 W/m2.K double vitrage'))
        self.assertIn('Uw = 2,8', pages[0])
        with self.assertRaises(documents.DocumentError):
            documents.pdf_pages(b'%PDF-1.4 casse')

    def test_checks(self):
        pages = [AUDIT]
        self.assertEqual(documents.check('mur.u', 1.35, 1, 'coefficient U des murs mesuré à 1,35', pages, {})[1], 'ok')
        self.assertEqual(documents.check('mur.u', 1.35, 1, 'texte qui n’existe pas', pages, {})[1], 'extrait_introuvable')
        self.assertEqual(documents.check('mur.u', 40, 1, 'coefficient U des murs', pages, {})[1], 'hors_plage')
        self.assertEqual(documents.check('cle.pirate', 1, 1, 'x', pages, {})[1], 'cle_inconnue')
        chk = documents.check('baies.uw', 2.8, 1, 'Uw = 2,8 W/m².K', pages, {'baies.vitrage': 'triple'})
        self.assertEqual(chk[1], 'incoherent')


@override_settings(VAULT_ENCRYPTION_KEY=KEY)
@patch_jwks()
class ExtractionTest(TestCase):
    def setUp(self):
        jwks = patch_jwks()  # le décorateur de classe ne couvre pas setUp
        jwks.start()
        self.addCleanup(jwks.stop)
        self.b = Building.objects.create(name='b')
        self.alice = client_for(sub='alice', preferred_username='alice')
        self.alice.put('/api/ai-keys/', {'claude_key': SECRET}, format='json')

    def _upload(self, name, data, client=None):
        r = (client or self.alice).post(f'/api/batiments/{self.b.pk}/documents/', {'file': SimpleUploadedFile(name, data)},
                                        format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_xml_dpe_gives_proposals_without_ai_and_needs_validation(self):
        d = self._upload('dpe.xml', DPE_XML)
        self.assertIn('non conservé', d['note'])
        props = {p['key']: p for p in d['proposals']}
        self.assertEqual(props['mur.u']['check'], 'ok')
        self.assertNotIn('mur.u', facts.effective_values(self.b))  # rien n'entre sans clic
        r = self.alice.post(f"/api/propositions/{props['mur.u']['id']}/decision/", {'decision': 'acceptee'}, format='json')
        self.assertEqual(r.status_code, 200)
        f = facts.effective(self.b)['mur.u']
        self.assertEqual((f.value, f.method), (0.8, 'document'))
        self.assertIn('dpe.xml', f.source)

    def test_ai_pdf_extraction_with_cited_extracts(self):
        """Recette du lot : un PDF produit des propositions avec extraits cités ; aucune n'entre sans
        validation ; la clé n'apparaît dans aucune réponse ni aucun journal."""
        d = self._upload('audit.txt', AUDIT.encode())
        est = self.alice.post(f"/api/documents/{d['id']}/estimation/", {'provider': 'claude'}, format='json').json()
        self.assertTrue(est['key_configured'])
        self.assertGreater(est['tokens_estimated'], 100)
        self.assertEqual(self.alice.post(f"/api/documents/{d['id']}/extraire/", {'provider': 'claude'}, format='json').json()['code'],
                         'confirm')
        with mock.patch('api.tasks.extract_document.delay') as delay:
            r = self.alice.post(f"/api/documents/{d['id']}/extraire/", {'provider': 'claude', 'confirm': True}, format='json')
        self.assertEqual(r.status_code, 202, r.content)
        faits = [{'cle': 'mur.u', 'valeur': 1.35, 'page': 1, 'extrait': 'coefficient U des murs mesuré à 1,35 W/m².K'},
                 {'cle': 'baies.uw', 'valeur': 2.8, 'page': 1, 'extrait': 'Uw = 2,8 W/m².K'},
                 {'cle': 'ventilation.type', 'valeur': 'vmc_sf_auto', 'page': 1, 'extrait': 'VMC simple flux autoréglable'},
                 {'cle': 'toiture.u', 'valeur': 0.2, 'page': 1, 'extrait': 'isolation des combles de 30 cm'},  # inventé
                 {'cle': 'mur.u', 'valeur': 99, 'page': 1, 'extrait': 'coefficient U des murs'}]                 # absurde
        logs = []
        handler = logging.Handler()
        handler.emit = lambda rec: logs.append(rec.getMessage())
        logging.getLogger().addHandler(handler)
        try:
            with fake_claude(faits) as call, mock.patch('api.ia.fournisseurs.client_anthropic') as cli:
                tasks.extract_document.apply(args=delay.call_args.args)
        finally:
            logging.getLogger().removeHandler(handler)
        cli.assert_called_once_with(SECRET)  # la clé de l'auteur, et seulement elle
        self.assertEqual(call.call_args_list[0].args[5][0]['name'], 'proposer_faits')
        job = Job.objects.get(pk=r.json()['id'])
        self.assertEqual(job.status, Job.DONE, job.message)
        props = {(p.key, p.control) for p in FactProposal.objects.all()}
        self.assertIn(('mur.u', 'ok'), props)
        self.assertIn(('toiture.u', 'extrait_introuvable'), props)
        self.assertIn(('mur.u', 'hors_plage'), props)
        self.assertEqual(FactProposal.objects.filter(decision='rejetee').count(), 2)
        self.assertNotIn('mur.u', facts.effective_values(self.b))
        listing = self.alice.get(f'/api/batiments/{self.b.pk}/propositions/')
        everything = ' '.join([listing.content.decode(), job.message, str(job.result), ' '.join(logs),
                               str(list(AIExtractionRun.objects.values()))])
        self.assertNotIn('SECRET', everything)
        ok = FactProposal.objects.get(key='mur.u', control='ok')
        self.alice.post(f'/api/propositions/{ok.pk}/decision/', {'decision': 'acceptee'}, format='json')
        f = facts.effective(self.b)['mur.u']
        self.assertEqual((f.method, f.extract['page']), ('document', 1))
        bad = FactProposal.objects.get(key='toiture.u')
        self.assertEqual(self.alice.post(f'/api/propositions/{bad.pk}/decision/', {'decision': 'acceptee'},
                                         format='json').status_code, 400)

    def test_documents_and_keys_are_personal(self):
        d = self._upload('audit.txt', AUDIT.encode())
        bob = client_for(sub='bob')
        bob.put('/api/ai-keys/', {'claude_key': 'sk-ant-api03-bob'}, format='json')
        self.assertEqual(bob.get(f'/api/batiments/{self.b.pk}/documents/').json()['results'], [])
        self.assertEqual(bob.post(f"/api/documents/{d['id']}/extraire/", {'provider': 'claude', 'confirm': True},
                                  format='json').status_code, 404)
        # Même si un job était forgé au nom d'un autre, la tâche refuse (propriétaires différents).
        run = AIExtractionRun.objects.create(document=BuildingDocument.objects.get(pk=d['id']), owner_sub='alice',
                                             provider='claude', pages_sent=[1])
        job = Job.objects.create(kind='extract_document', owner_sub='bob')
        with mock.patch('api.ia.fournisseurs.client_anthropic') as cli:
            tasks.extract_document.apply(args=[job.pk, run.pk])
        cli.assert_not_called()
        self.assertEqual(Job.objects.get(pk=job.pk).status, Job.ERROR)

    def test_provider_error_fixed_message_and_no_key(self):
        d = self._upload('audit.txt', AUDIT.encode())
        with mock.patch('api.tasks.extract_document.delay') as delay:
            r = self.alice.post(f"/api/documents/{d['id']}/extraire/", {'provider': 'claude', 'confirm': True}, format='json')
        err = fournisseurs.ErreurFournisseur(502, 'Clé claude refusée par le fournisseur : vérifiez-la sur la page « Intelligence artificielle ».')
        with mock.patch('api.ia.fournisseurs.appeler_anthropic', side_effect=err), mock.patch('api.ia.fournisseurs.client_anthropic'):
            tasks.extract_document.apply(args=delay.call_args.args)
        job = Job.objects.get(pk=r.json()['id'])
        self.assertEqual(job.status, Job.ERROR)
        self.assertIn('refusée', job.message)
        self.assertNotIn('SECRET', job.message)
        self.assertFalse(FactProposal.objects.exists())

    def test_albert_uses_registry_url_and_auto_tool_choice(self):
        self.alice.put('/api/ai-keys/', {'albert_key': 'albert-cle-test'}, format='json')
        d = self._upload('audit.txt', AUDIT.encode())
        with mock.patch('api.tasks.extract_document.delay') as delay:
            self.alice.post(f"/api/documents/{d['id']}/extraire/", {'provider': 'albert', 'confirm': True}, format='json')
        body = {'choices': [{'message': {'content': '', 'tool_calls': [{'id': 'c1', 'function': {'name': 'proposer_faits',
                'arguments': '{"faits": [{"cle": "baies.uw", "valeur": 2.8, "page": 1, "extrait": "Uw = 2,8 W/m²"}]}'}}]},
                'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 900, 'completion_tokens': 40}}
        end = {'choices': [{'message': {'content': 'fini'}, 'finish_reason': 'stop'}], 'usage': {}}
        resp = [mock.Mock(status_code=200, json=lambda b=b: b, raise_for_status=lambda: None) for b in (body, end)]
        with mock.patch('api.ia.fournisseurs.http_requests.post', side_effect=resp) as post, \
                mock.patch('api.ia.catalogue.http_requests.get', side_effect=OSError):
            tasks.extract_document.apply(args=delay.call_args.args)
        url = post.call_args_list[0].args[0]
        self.assertEqual(url, 'https://albert.api.etalab.gouv.fr/v1/chat/completions')
        self.assertEqual(post.call_args_list[0].kwargs['json']['tool_choice'], 'auto')
        self.assertTrue(FactProposal.objects.filter(key='baies.uw', control='ok').exists())

    def test_reader_cannot_upload(self):
        from ..models import Portfolio, PortfolioMember
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='l', role='lecteur')
        b2 = Building.objects.create(name='x', portfolio=p)
        r = client_for(sub='l').post(f'/api/batiments/{b2.pk}/documents/', {'file': SimpleUploadedFile('a.txt', b'x')},
                                     format='multipart')
        self.assertEqual(r.status_code, 403)
