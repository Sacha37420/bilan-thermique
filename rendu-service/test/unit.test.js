// node --test test/unit.test.js : clé, garde d'URL, validation des corps, file, délai, HTTP.
// Aucun navigateur : le rendu est simulé.
import assert from 'node:assert/strict';
import http from 'node:http';
import { test } from 'node:test';
import {
  avecDelai, cleValide, creerGestionnaire, FileSerielle, lireCle,
} from '../app.js';
import {
  HttpError, urlPermise, validerImages, validerPdf, validerVue3d,
} from '../validation.js';


const CLE = 'k'.repeat(32);

test('lireCle : absente, courte, valide', () => {
  assert.throws(() => lireCle({}), /RENDU_SERVICE_KEY/);
  assert.throws(() => lireCle({ RENDU_SERVICE_KEY: 'x'.repeat(23) }), /24/);
  assert.equal(lireCle({ RENDU_SERVICE_KEY: 'x'.repeat(24) }), 'x'.repeat(24));
  // le message ne recopie jamais la valeur
  try { lireCle({ RENDU_SERVICE_KEY: 'secretcourt' }); } catch (e) { assert.ok(!e.message.includes('secretcourt')); }
});

test('cleValide : temps constant, égalité stricte', () => {
  assert.equal(cleValide(CLE, CLE), true);
  assert.equal(cleValide(`${CLE}x`, CLE), false);
  assert.equal(cleValide(CLE.slice(1), CLE), false);
  assert.equal(cleValide('', CLE), false);
  assert.equal(cleValide(undefined, CLE), false);
  assert.equal(cleValide(['a'], CLE), false);
});

test('garde d’URL : data: et about: seulement', () => {
  for (const u of ['data:image/png;base64,AAAA', 'about:blank', 'about:srcdoc']) assert.equal(urlPermise(u), true, u);
  for (const u of ['http://example.com/', 'https://10.0.0.1/', 'ws://127.0.0.1/', 'file:///etc/passwd',
    'blob:null/123', 'chrome://version', 'http://127.0.0.1:3000/sante', ' data:x', 'DATA:x', '', null]) {
    assert.equal(urlPermise(u), false, String(u));
  }
});

test('validerPdf', () => {
  assert.deepEqual(validerPdf({ html: '<p>x</p>' }), { html: '<p>x</p>', format: 'A4', paysage: false, marges_mm: 14 });
  assert.equal(validerPdf({ html: 'x', format: 'A3', paysage: true, marges_mm: 0 }).format, 'A3');
  for (const c of [null, [], 'x', {}, { html: '' }, { html: 3 }, { html: 'x', format: 'Letter' },
    { html: 'x', paysage: 'oui' }, { html: 'x', marges_mm: -1 }, { html: 'x', marges_mm: 51 }, { html: 'x', marges_mm: '14' }]) {
    assert.throws(() => validerPdf(c), (e) => e instanceof HttpError && e.status === 400);
  }
});

test('validerImages', () => {
  const fig = { data: [{ y: [1, 2] }], layout: {} };
  const v = validerImages({ figures: [fig] });
  assert.equal(v.largeur, 1200); assert.equal(v.hauteur, 700); assert.equal(v.echelle, 2);
  assert.equal(validerImages({ figures: [{ data: [] }] }).figures[0].layout && true, true);
  const mauvais = [
    {}, { figures: [] }, { figures: new Array(41).fill(fig) }, { figures: [{}] }, { figures: [{ data: {} }] },
    { figures: [{ data: [], layout: [] }] }, { figures: [fig], largeur: 99 }, { figures: [fig], hauteur: 3001 },
    { figures: [fig], largeur: 500.5 }, { figures: [fig], echelle: 0.5 }, { figures: [fig], echelle: 4 },
    { figures: [fig], echelle: '2' },
  ];
  for (const c of mauvais) assert.throws(() => validerImages(c), (e) => e.status === 400, JSON.stringify(c).slice(0, 60));
  assert.equal(validerImages({ figures: new Array(40).fill(fig), largeur: 3000, hauteur: 100, echelle: 3 }).figures.length, 40);
});

function boite() {
  const vertices = [[0, 0, 0], [10, 0, 0], [10, 6, 0], [0, 6, 0], [0, 0, 3], [10, 0, 3], [10, 6, 3], [0, 6, 3]];
  const triangles = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
    [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]];
  const n = triangles.length;
  return {
    repere: 'batiment', vertices, triangles, groupes: [], type: new Array(n).fill('paroi'), periodes: {},
    grandeurs: { pertes_hiver_kwh_m2: triangles.map((_, i) => i * 1.5) },
    unites: { pertes_hiver_kwh_m2: 'kWh/m²' },
  };
}

test('validerVue3d : forme valide', () => {
  const v = validerVue3d({ scene: boite(), grandeur: 'pertes_hiver_kwh_m2', camera: 'sud', largeur: 800, hauteur: 600 });
  assert.equal(v.positions.length, 24); assert.equal(v.triangles.length, 36);
  assert.equal(v.camera, 'sud'); assert.equal(v.unite, 'kWh/m²');
  assert.equal(validerVue3d({ scene: boite(), grandeur: 'pertes_hiver_kwh_m2' }).camera, 'iso');
  const s = boite(); s.grandeurs.pertes_hiver_kwh_m2[3] = null; delete s.type;
  const v2 = validerVue3d({ scene: s, grandeur: 'pertes_hiver_kwh_m2' });
  assert.equal(v2.valeurs[3], null); assert.equal(v2.types[0], 'paroi');
});

test('validerVue3d : refus', () => {
  const cas = [
    [{ scene: boite(), grandeur: 'inconnue' }, 400],
    [{ scene: boite(), grandeur: 'toString' }, 400],
    [{ scene: boite(), grandeur: 'pertes_hiver_kwh_m2', camera: 'diagonale' }, 400],
    [{ scene: boite(), grandeur: 'pertes_hiver_kwh_m2', largeur: 50 }, 400],
    [{ grandeur: 'pertes_hiver_kwh_m2' }, 400],
  ];
  const s1 = boite(); s1.triangles[0] = [0, 1, 99]; cas.push([{ scene: s1, grandeur: 'pertes_hiver_kwh_m2' }, 400]);
  const s2 = boite(); s2.vertices[0] = [0, 'a', 0]; cas.push([{ scene: s2, grandeur: 'pertes_hiver_kwh_m2' }, 400]);
  const s3 = boite(); s3.type[0] = 'toit'; cas.push([{ scene: s3, grandeur: 'pertes_hiver_kwh_m2' }, 400]);
  const s4 = boite(); s4.grandeurs.pertes_hiver_kwh_m2.pop(); cas.push([{ scene: s4, grandeur: 'pertes_hiver_kwh_m2' }, 400]);
  const s5 = boite(); s5.triangles = new Array(20001).fill([0, 1, 2]); cas.push([{ scene: s5, grandeur: 'pertes_hiver_kwh_m2' }, 413]);
  for (const [corps, status] of cas) {
    assert.throws(() => validerVue3d(corps), (e) => e instanceof HttpError && e.status === status);
  }
});

test('FileSerielle : une à la fois, au plus 4 en attente', async () => {
  const file = new FileSerielle(4);
  let actives = 0; let maxActives = 0; const ordre = [];
  const tache = (i) => async () => {
    actives += 1; maxActives = Math.max(maxActives, actives);
    await new Promise((r) => setTimeout(r, 20));
    ordre.push(i); actives -= 1; return i;
  };
  const p = [0, 1, 2, 3, 4].map((i) => file.ajouter(tache(i)));   // 1 active + 4 en attente
  await assert.rejects(file.ajouter(tache(5)), (e) => e.status === 503);
  assert.deepEqual(await Promise.all(p), [0, 1, 2, 3, 4]);
  assert.equal(maxActives, 1);
  assert.deepEqual(ordre, [0, 1, 2, 3, 4]);
});

test('avecDelai : 504 et signal levé', async () => {
  let leve = false;
  await assert.rejects(avecDelai((signal) => new Promise((resolve) => {
    signal.addEventListener('abort', () => { leve = true; resolve('tard'); });
  }), 30, 50), (e) => e.status === 504);
  assert.equal(leve, true);
  assert.equal(await avecDelai(async () => 'ok', 1000), 'ok');
});

// ── HTTP de bout en bout, rendu simulé ───────────────────────────────────────
async function serveurSimule(rendu, options = {}) {
  const journal = [];
  const srv = http.createServer(creerGestionnaire({ cle: CLE, rendu, journal: (e) => journal.push(e), ...options }));
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${srv.address().port}`;
  return { srv, base, journal };
}

const RENDU_FAUX = {
  pdf: async () => Buffer.from('%PDF-1.7 faux'),
  images: async (p) => p.figures.map(() => 'data:image/png;base64,AAAA'),
  vue3d: async () => 'data:image/png;base64,BBBB',
};

test('HTTP : routes, clé, erreurs fixes', async () => {
  const { srv, base, journal } = await serveurSimule(RENDU_FAUX);
  try {
    const sante = await fetch(`${base}/sante`);
    assert.equal(sante.status, 200); assert.deepEqual(await sante.json(), { ok: true });

    const sansCle = await fetch(`${base}/pdf`, { method: 'POST', body: '{"html":"x"}' });
    assert.equal(sansCle.status, 401);
    assert.deepEqual(await sansCle.json(), { detail: 'Clé du service de rendu absente ou invalide.' });
    const mauvaise = await fetch(`${base}/pdf`, { method: 'POST', headers: { 'X-Rendu-Cle': 'z'.repeat(32) }, body: '{}' });
    assert.equal(mauvaise.status, 401);

    const h = { 'X-Rendu-Cle': CLE, 'Content-Type': 'application/json' };
    const pdf = await fetch(`${base}/pdf`, { method: 'POST', headers: h, body: JSON.stringify({ html: '<p>x</p>' }) });
    assert.equal(pdf.status, 200); assert.equal(pdf.headers.get('content-type'), 'application/pdf');

    const img = await fetch(`${base}/images`, { method: 'POST', headers: h, body: JSON.stringify({ figures: [{ data: [] }] }) });
    assert.deepEqual(await img.json(), { images: ['data:image/png;base64,AAAA'] });

    const v = await fetch(`${base}/vue3d`, { method: 'POST', headers: h, body: JSON.stringify({ scene: boite(), grandeur: 'pertes_hiver_kwh_m2' }) });
    assert.deepEqual(await v.json(), { image: 'data:image/png;base64,BBBB' });

    const secret = '<script>SECRET-ENTREE</script>';
    const mauvaisJson = await fetch(`${base}/pdf`, { method: 'POST', headers: h, body: `{${secret}` });
    assert.equal(mauvaisJson.status, 400);
    assert.ok(!(await mauvaisJson.text()).includes('SECRET'));
    const g = await fetch(`${base}/vue3d`, { method: 'POST', headers: h, body: JSON.stringify({ scene: boite(), grandeur: secret }) });
    assert.equal(g.status, 400); assert.ok(!(await g.text()).includes('SECRET'));

    assert.equal((await fetch(`${base}/inconnue`)).status, 404);
    const m = await fetch(`${base}/pdf`);
    assert.equal(m.status, 405); assert.equal(m.headers.get('allow'), 'POST');
    assert.equal((await fetch(`${base}/sante`, { method: 'POST' })).status, 405);

    const gros = await fetch(`${base}/pdf`, { method: 'POST', headers: h, body: Buffer.alloc(30 * 1024 * 1024 + 1, 0x20) });
    assert.equal(gros.status, 413);

    // Le journal ne porte ni corps ni en-têtes.
    const texte = JSON.stringify(journal);
    assert.ok(!texte.includes('SECRET') && !texte.includes(CLE) && !texte.includes('<p>'));
  } finally {
    srv.close();
  }
});

test('HTTP : erreur interne → 503 sans pile ; délai → 504', async () => {
  const rendu = {
    ...RENDU_FAUX,
    pdf: async () => { throw new Error('/chemin/secret stack'); },
    images: (p, signal) => new Promise((resolve) => signal.addEventListener('abort', () => resolve([]))),
  };
  const { srv, base } = await serveurSimule(rendu, { delaiMs: 50 });
  try {
    const h = { 'X-Rendu-Cle': CLE };
    const r = await fetch(`${base}/pdf`, { method: 'POST', headers: h, body: JSON.stringify({ html: 'x' }) });
    assert.equal(r.status, 503);
    const t = await r.text();
    assert.ok(!t.includes('secret') && !t.includes('stack'));
    const d = await fetch(`${base}/images`, { method: 'POST', headers: h, body: JSON.stringify({ figures: [{ data: [] }] }) });
    assert.equal(d.status, 504);
  } finally {
    srv.close();
  }
});

test('HTTP : au-delà de 4 en attente → 503', async () => {
  let liberer;
  const verrou = new Promise((r) => { liberer = r; });
  const rendu = { ...RENDU_FAUX, pdf: async () => { await verrou; return Buffer.from('%PDF'); } };
  const { srv, base } = await serveurSimule(rendu);
  try {
    const h = { 'X-Rendu-Cle': CLE };
    const corps = JSON.stringify({ html: 'x' });
    const envoyer = () => fetch(`${base}/pdf`, { method: 'POST', headers: h, body: corps });
    const premiers = [];
    for (let i = 0; i < 5; i += 1) {
      premiers.push(envoyer());
      await new Promise((r) => setTimeout(r, 30));
    }
    const sixieme = await envoyer();
    assert.equal(sixieme.status, 503);
    liberer();
    const statuts = (await Promise.all(premiers)).map((r) => r.status);
    assert.deepEqual(statuts, [200, 200, 200, 200, 200]);
  } finally {
    srv.close();
  }
});
