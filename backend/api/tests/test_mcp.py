"""Lot 22 (refonte) — serveur MCP et jetons personnels `btt_`. Patron repris d'App Builder (`McpTests`).
Aucun Claude Code réel : les appels JSON-RPC sont faits par le client de test."""

import json
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .. import facts
from ..models import Building, BuildingAccess, FactProposal, McpToken, Portfolio, PortfolioMember
from ..testing import client_for, patch_jwks, synthetic_year
from .test_calage import seeded
from .test_ia import AUDIT

YEAR = synthetic_year()


def mcp(token, method, params=None, ident=1):
    c = APIClient()
    body = {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}
    return c.post('/api/mcp/', body, format='json', HTTP_AUTHORIZATION=f'Bearer {token}')


def call(token, tool, **args):
    r = mcp(token, 'tools/call', {'name': tool, 'arguments': args})
    assert r.status_code == 200, r.content
    res = r.json()['result']
    return json.loads(res['content'][0]['text']), res['isError']


class McpTestBase(TestCase):
    def setUp(self):
        jwks = patch_jwks()  # le décorateur de classe ne couvre pas setUp
        jwks.start()
        self.addCleanup(jwks.stop)
        tmy = mock.patch('api.calc_service.tmy_weather', return_value={'series': YEAR, 'source': 'année type'})
        tmy.start()
        self.addCleanup(tmy.stop)
        self.alice = client_for(sub='alice', preferred_username='alice')
        self.b = seeded()

    def token(self, client=None, **body):
        r = (client or self.alice).post('/api/mcp-tokens/', {'name': 'Claude Code', **body}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()['token'], r.json()['id']


@patch_jwks()
class TokenTest(McpTestBase):
    def test_token_shown_once_hashed_and_listed(self):
        tok, tid = self.token(scope='ecrire', days=10)
        self.assertTrue(tok.startswith('btt_'))
        j = McpToken.objects.get(pk=tid)
        self.assertNotIn(tok, j.token_hash)
        self.assertEqual(j.owner_sub, 'alice')
        listing = self.alice.get('/api/mcp-tokens/').content.decode()
        self.assertNotIn(tok, listing)
        self.assertIn(tok[:12], listing)
        self.assertEqual(client_for(sub='bob').get('/api/mcp-tokens/').json(), [])

    def test_bounds(self):
        for body in ({'days': 91}, {'days': 0}, {'scope': 'admin'}, {'name': ''}, {'building_id': 999999},
                     {'building_id': self.b.pk, 'portfolio_id': 1}):
            r = self.alice.post('/api/mcp-tokens/', {'name': 'x', **body}, format='json')
            self.assertEqual(r.status_code, 400, body)

    def test_info_has_no_real_token(self):
        self.token()
        info = self.alice.get('/api/mcp/info/').json()
        self.assertIn('<JETON>', info['claude'])
        self.assertTrue(info['url'].endswith('/api/mcp/'))


@patch_jwks()
class AuthTest(McpTestBase):
    def test_expired_revoked_unknown_refused_same_message(self):
        tok, tid = self.token()
        self.assertEqual(mcp(tok, 'ping').status_code, 200)
        McpToken.objects.filter(pk=tid).update(expires_at=timezone.now() - timedelta(seconds=1))
        r1 = mcp(tok, 'ping')
        tok2, tid2 = self.token()
        self.assertEqual(self.alice.delete(f'/api/mcp-tokens/{tid2}/').status_code, 204)
        r2 = mcp(tok2, 'ping')
        r3 = mcp('btt_inconnu', 'ping')
        for r in (r1, r2, r3):
            self.assertEqual(r.status_code, 401)
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(r2.json(), r3.json())

    def test_other_users_token_cannot_be_revoked(self):
        _tok, tid = self.token()
        self.assertEqual(client_for(sub='bob').delete(f'/api/mcp-tokens/{tid}/').status_code, 404)
        self.assertIsNone(McpToken.objects.get(pk=tid).revoked_at)

    def test_keycloak_token_refused_on_mcp_and_btt_refused_elsewhere(self):
        tok, _ = self.token()
        r = self.alice.post('/api/mcp/', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, format='json')
        self.assertEqual(r.status_code, 401)
        c = APIClient()
        # Serrure Keycloak existante : un `btt_` n'est pas un JWT, refusé (403 : elle ne pose pas d'en-tête WWW-Authenticate).
        self.assertIn(c.get('/api/batiments/', HTTP_AUTHORIZATION=f'Bearer {tok}').status_code, (401, 403))
        self.assertIn(c.get('/api/mcp-tokens/', HTTP_AUTHORIZATION=f'Bearer {tok}').status_code, (401, 403))

    def test_get_not_allowed(self):
        tok, _ = self.token()
        self.assertEqual(APIClient().get('/api/mcp/', HTTP_AUTHORIZATION=f'Bearer {tok}').status_code, 405)


@patch_jwks()
class ToolsTest(McpTestBase):
    def test_initialize_and_tool_list_depend_on_scope(self):
        lire, _ = self.token(scope='lire')
        ecrire, _ = self.token(scope='ecrire')
        r = mcp(lire, 'initialize', {'protocolVersion': '2025-06-18'}).json()['result']
        self.assertEqual(r['serverInfo']['name'], 'bilan-thermique')
        self.assertIn('appliquer_propositions', r['instructions'])
        names_l = {t['name'] for t in mcp(lire, 'tools/list').json()['result']['tools']}
        names_e = {t['name'] for t in mcp(ecrire, 'tools/list').json()['result']['tools']}
        self.assertIn('lire_fiche', names_l)
        self.assertNotIn('appliquer_propositions', names_l)
        self.assertNotIn('lancer_calcul', names_l)
        self.assertLessEqual({'proposer_faits', 'lancer_calcul', 'comparer_scenarios', 'appliquer_propositions'}, names_e)
        out, err = call(lire, 'lancer_calcul', batiment_id=self.b.pk)
        self.assertTrue(err)
        self.assertEqual(out['error'], 'portee')

    def test_read_fiche_and_compute(self):
        tok, _ = self.token(scope='ecrire')
        out, err = call(tok, 'lire_fiche', batiment_id=self.b.pk)
        self.assertFalse(err)
        self.assertEqual(out['facts']['mur.u']['method'], 'defaut')
        out, err = call(tok, 'lancer_calcul', batiment_id=self.b.pk, niveaux=['N1'])
        self.assertFalse(err, out)
        self.assertEqual(out['runs'][0]['level'], 'N1')
        self.assertIn('heating_kwh', out['runs'][0]['indicators'])
        res, _ = call(tok, 'lire_resultats', batiment_id=self.b.pk)
        self.assertTrue(res['N1']['up_to_date'])
        self.assertIsNone(res['N0'])
        cmp_, err = call(tok, 'comparer_scenarios', batiment_id=self.b.pk, scenarios=[None])
        self.assertFalse(err, cmp_)
        self.assertEqual(cmp_['rows'][0]['scenario_name'], 'État actuel')

    def test_propose_then_apply_only_with_controls(self):
        """Recette du lot : depuis Claude Code, lire un document, proposer, appliquer après accord."""
        r = self.alice.post(f'/api/batiments/{self.b.pk}/documents/', {'file': SimpleUploadedFile('audit.txt', AUDIT.encode())},
                            format='multipart')
        doc = r.json()['id']
        tok, _ = self.token(scope='ecrire', building_id=self.b.pk)
        docs, _ = call(tok, 'lister_documents', batiment_id=self.b.pk)
        self.assertEqual(docs[0]['id'], doc)
        text, _ = call(tok, 'lire_document', document_id=doc)
        self.assertIn('1,35', text['pages'][0]['texte'])
        out, err = call(tok, 'proposer_faits', document_id=doc, faits=[
            {'cle': 'mur.u', 'valeur': 1.35, 'page': 1, 'extrait': 'coefficient U des murs mesuré à 1,35 W/m².K'},
            {'cle': 'toiture.u', 'valeur': 0.2, 'page': 1, 'extrait': 'isolation des combles de 30 cm'}])
        self.assertFalse(err, out)
        props = {p['key']: p for p in out['propositions']}
        self.assertEqual(props['mur.u']['check'], 'ok')
        self.assertEqual(props['toiture.u']['decision'], 'rejetee')
        self.assertEqual(facts.effective(self.b)['mur.u'].method, 'defaut')   # rien n'entre sans accord
        res, err = call(tok, 'appliquer_propositions', ids=[props['mur.u']['id'], props['toiture.u']['id'], 999999])
        self.assertFalse(err)
        self.assertEqual([p['key'] for p in res['appliquees']], ['mur.u'])
        self.assertEqual(len(res['refus']), 2)
        f = facts.effective(self.b)['mur.u']
        self.assertEqual((f.value, f.method, f.author_sub), (1.35, 'document', 'alice'))
        self.assertIn('via MCP', f.source)
        self.assertEqual(FactProposal.objects.get(pk=props['mur.u']['id']).run.provider, 'mcp')


@patch_jwks()
class ScopeTest(McpTestBase):
    def test_token_bound_to_building_sees_only_it(self):
        other = Building.objects.create(name='autre')
        tok, _ = self.token(building_id=self.b.pk)
        listed, _ = call(tok, 'lister_batiments')
        self.assertEqual([x['id'] for x in listed], [self.b.pk])
        out, err = call(tok, 'lire_fiche', batiment_id=other.pk)
        self.assertTrue(err)
        self.assertEqual(out['error'], 'introuvable')

    def test_token_acts_with_owner_rights_only(self):
        """Un jeton n'ouvre que ce que son titulaire voit : bâtiment d'un parc d'autrui invisible,
        document personnel d'autrui illisible, lecteur sans écriture."""
        p = Portfolio.objects.create(name='P', owner_sub='g')
        PortfolioMember.objects.create(portfolio=p, sub='g', role='gestionnaire_parc')
        hidden = Building.objects.create(name='privé', portfolio=p)
        tok, _ = self.token(scope='ecrire')
        self.assertNotIn(hidden.pk, [x['id'] for x in call(tok, 'lister_batiments')[0]])
        self.assertEqual(call(tok, 'lire_fiche', batiment_id=hidden.pk)[0]['error'], 'introuvable')
        bob = client_for(sub='bob', preferred_username='bob')
        r = bob.post(f'/api/batiments/{self.b.pk}/documents/', {'file': SimpleUploadedFile('a.txt', AUDIT.encode())},
                     format='multipart')
        self.assertEqual(call(tok, 'lire_document', document_id=r.json()['id'])[0]['error'], 'introuvable')
        self.assertEqual(call(tok, 'lister_documents', batiment_id=self.b.pk)[0], [])
        # Lecteur d'un parc : lit, ne calcule pas, même avec un jeton « ecrire ».
        PortfolioMember.objects.create(portfolio=p, sub='alice', role='lecteur')
        self.assertFalse(call(tok, 'lire_fiche', batiment_id=hidden.pk)[1])
        self.assertEqual(call(tok, 'lancer_calcul', batiment_id=hidden.pk)[0]['error'], 'lecture_seule')
        BuildingAccess.objects.create(building=hidden, sub='alice', role='occupant')
        out, _ = call(tok, 'lancer_calcul', batiment_id=hidden.pk, niveaux=['N0'])
        self.assertNotEqual(out.get('error'), 'lecture_seule')   # occupant : le calcul est permis (ici fiche vide)

    def test_token_of_removed_member_loses_access(self):
        p = Portfolio.objects.create(name='P', owner_sub='g')
        m = PortfolioMember.objects.create(portfolio=p, sub='alice', role='gestionnaire_parc')
        b = Building.objects.create(name='x', portfolio=p)
        tok, _ = self.token(portfolio_id=p.pk)
        self.assertEqual([x['id'] for x in call(tok, 'lister_batiments')[0]], [b.pk])
        m.delete()
        self.assertEqual(call(tok, 'lister_batiments')[0], [])
