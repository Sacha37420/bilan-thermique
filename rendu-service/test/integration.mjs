// Essai d'intégration, joué DANS l'image, sans réseau :
//   docker run --rm --network none -e RENDU_SERVICE_KEY=<clé de test> \
//     [-v <dossier>:/sortie] bilan-thermique-rendu:essai node test/integration.mjs
//
// Démarre le vrai serveur (vrai Chromium) sur 127.0.0.1:3000 et l'appelle.
// « Canaris » : un serveur HTTP/WebSocket (127.0.0.1:3901) et un port UDP STUN
// (127.0.0.1:3478) comptent tout ce qui les atteint — ils ne doivent RIEN recevoir.
// La boucle locale est le seul réseau d'un conteneur --network none : c'est donc
// là que l'isolement se prouve. Les fichiers produits vont dans /sortie s'il existe.
import assert from 'node:assert/strict';
import dgram from 'node:dgram';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import zlib from 'node:zlib';
import { chromium } from 'playwright';
import { demarrer } from '../server.js';
import { ARGS_CHROMIUM } from '../renderer.js';

const CLE = process.env.RENDU_SERVICE_KEY;
const SORTIE = fs.existsSync('/sortie') ? '/sortie' : null;
const BASE = 'http://127.0.0.1:3000';
const CANARI = 'http://127.0.0.1:3901';
const resultats = [];
let echecs = 0;

async function etape(nom, fn) {
  const t0 = Date.now();
  try {
    const info = await fn();
    resultats.push(`OK   ${nom} (${Date.now() - t0} ms)${info ? ` — ${info}` : ''}`);
  } catch (err) {
    echecs += 1;
    resultats.push(`ÉCHEC ${nom} (${Date.now() - t0} ms) — ${err && err.message}`);
  }
}

function ecrire(nom, contenu) {
  if (SORTIE) fs.writeFileSync(path.join(SORTIE, nom), contenu);
}

// ── canaris ──────────────────────────────────────────────────────────────────
const canari = { http: [], ws: 0, udp: 0 };
const srvCanari = http.createServer((req, res) => { canari.http.push(req.url); res.end('atteint'); });
srvCanari.on('upgrade', (req, socket) => { canari.ws += 1; socket.destroy(); });
srvCanari.on('connect', (req, socket) => { canari.http.push(`CONNECT ${req.url}`); socket.destroy(); });
await new Promise((r) => srvCanari.listen(3901, '127.0.0.1', r));
const udp = dgram.createSocket('udp4');
udp.on('message', () => { canari.udp += 1; });
await new Promise((r) => udp.bind(3478, '127.0.0.1', r));

const bloquees = [];
const service = await demarrer({
  port: 3000, hote: '127.0.0.1', surBloque: (u) => bloquees.push(u), journal: () => {},
});

// ── PNG : dimensions et nombre de couleurs distinctes ───────────────────────
function lirePng(buf) {
  assert.equal(buf.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'signature PNG');
  let o = 8; let largeur = 0; let hauteur = 0; let type = 0; let profondeur = 0; const idat = [];
  while (o < buf.length) {
    const n = buf.readUInt32BE(o); const t = buf.toString('latin1', o + 4, o + 8);
    const d = buf.subarray(o + 8, o + 8 + n);
    if (t === 'IHDR') { largeur = d.readUInt32BE(0); hauteur = d.readUInt32BE(4); profondeur = d[8]; type = d[9]; }
    if (t === 'IDAT') idat.push(d);
    o += 12 + n;
  }
  assert.equal(profondeur, 8, 'profondeur 8 bits');
  const canaux = { 2: 3, 6: 4 }[type];
  assert.ok(canaux, `type de couleur ${type}`);
  const brut = zlib.inflateSync(Buffer.concat(idat));
  const ligne = largeur * canaux;
  const prec = Buffer.alloc(ligne); const cour = Buffer.alloc(ligne);
  const couleurs = new Set();
  for (let y = 0; y < hauteur; y += 1) {
    const f = brut[y * (ligne + 1)];
    const src = brut.subarray(y * (ligne + 1) + 1, (y + 1) * (ligne + 1));
    for (let i = 0; i < ligne; i += 1) {
      const a = i >= canaux ? cour[i - canaux] : 0; const b = prec[i]; const c = i >= canaux ? prec[i - canaux] : 0;
      let p;
      if (f === 0) p = 0; else if (f === 1) p = a; else if (f === 2) p = b; else if (f === 3) p = (a + b) >> 1;
      else { const pa = Math.abs(b - c); const pb = Math.abs(a - c); const pc = Math.abs(a + b - 2 * c); p = pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      cour[i] = (src[i] + p) & 0xff;
    }
    if (y % 4 === 0) for (let x = 0; x < largeur; x += 4) couleurs.add(cour.readUIntBE(x * canaux, 3));
    cour.copy(prec);
  }
  return { largeur, hauteur, couleurs: couleurs.size };
}
const dataUrlVersPng = (u) => Buffer.from(u.replace(/^data:image\/png;base64,/, ''), 'base64');

async function appeler(chemin, corps, { cle = CLE, methode = 'POST' } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (cle) headers['X-Rendu-Cle'] = cle;
  return fetch(`${BASE}${chemin}`, { method: methode, headers, body: corps === undefined ? undefined : JSON.stringify(corps) });
}

// ── documents d'essai ───────────────────────────────────────────────────────
const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:";
const FIGS = `
  const f1 = [{type:'bar', x:['Murs','Toiture','Plancher','Vitrages','Ponts'], y:[42,31,12,27,9], marker:{color:'#3b5ba0'}}];
  const f2 = [{type:'scatter', mode:'lines', x:[...Array(24).keys()], y:[...Array(24).keys()].map(h => 22 + 6*Math.sin((h-9)/24*2*Math.PI)), line:{color:'#2a175e'}}];
  const lay = (t) => ({title:{text:t}, width:640, height:340, margin:{l:50,r:20,t:50,b:40}, font:{family:'sans-serif'}});
`;
const ESSAIS_RESEAU = `
  const essai = (p) => Promise.race([p, new Promise((r) => setTimeout(() => r('délai'), 3000))]);
  const ws = (u) => essai(new Promise((r) => { try { const w = new WebSocket(u); w.onopen = () => r('OUVERT'); w.onerror = () => r('refus'); } catch (e) { r('refus'); } }));
  const fe = (u) => essai(fetch(u).then(() => 'ATTEINT', () => 'refus'));
`;
const HTML_CONTRAT = `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${CSP}">
<title>Bilan thermique — essai</title>
<style>
  body { font-family: sans-serif; color: #222; }
  h1 { font-size: 22pt; } h2 { font-size: 15pt; border-bottom: 1px solid #999; }
  .saut { break-before: page; }
  .fig { width: 640px; height: 340px; }
  img.vue { width: 480px; border: 1px solid #ccc; }
</style></head><body>
<h1 id="sommaire">Bilan thermique — essai de rendu</h1>
<nav><p>Sommaire</p><ul>
  <li><a href="#pertes">Pertes d’hiver</a></li><li><a href="#gains">Gains d’été</a></li><li><a href="#vue">Vue 3D</a></li>
</ul></nav>
<h2 id="pertes">Pertes d’hiver</h2>
<p>Répartition des déperditions par poste (kWh/an).</p>
<div id="fig1" class="fig"></div>
<p>Image externe (doit échouer) : <img src="http://example.com/x.png" alt="externe"> <img src="${CANARI}/img.png" alt="canari"></p>
<section class="saut"><h2 id="gains">Gains d’été</h2><div id="fig2" class="fig"></div></section>
<section class="saut"><h2 id="vue">Vue 3D</h2><canvas id="c3d" data-rendu-3d width="480" height="320"></canvas>
<p><a href="#sommaire">Retour au sommaire</a></p></section>
<script>
${FIGS}
${ESSAIS_RESEAU}
window.renduDemarrer = async function () {
  await Plotly.newPlot('fig1', f1, lay('Pertes par poste'), {staticPlot: true});
  await Plotly.newPlot('fig2', f2, lay('Température intérieure'), {staticPlot: true});
  const c = document.getElementById('c3d');
  const r = new THREE.WebGLRenderer({canvas: c, preserveDrawingBuffer: true});
  r.setClearColor(0xffffff, 1);
  const s = new THREE.Scene(); s.add(new THREE.AmbientLight(0xffffff, 0.6));
  const l = new THREE.DirectionalLight(0xffffff, 0.8); l.position.set(3, -2, 5); s.add(l);
  s.add(new THREE.Mesh(new THREE.BoxGeometry(2, 1.4, 1), new THREE.MeshLambertMaterial({color: 0x6ba5d6})));
  const cam = new THREE.PerspectiveCamera(40, 1.5, 0.1, 100); cam.up.set(0, 0, 1); cam.position.set(4, -4, 3); cam.lookAt(0, 0, 0);
  r.render(s, cam);
  const img = document.createElement('img'); img.className = 'vue'; img.src = c.toDataURL('image/png'); c.replaceWith(img);
  window.__essais = await Promise.all([fe('http://10.0.0.1/'), fe('${CANARI}/fetch'), ws('ws://10.0.0.1/'), ws('ws://127.0.0.1:3901/ws')]);
  window.renduPret = true;
};
</script></body></html>`;

// Document hostile, SANS CSP : seules les protections du service tiennent.
const HTML_HOSTILE = `<!doctype html><html><head><meta charset="utf-8"><title>hostile</title>
<link rel="stylesheet" href="${CANARI}/style.css"><link rel="prefetch" href="${CANARI}/prefetch">
<style>@import url("${CANARI}/import.css"); @font-face { font-family: X; src: url("${CANARI}/font.woff2"); } body { font-family: X; background: url("${CANARI}/fond.png"); }</style>
</head><body><h1 id="a">Hostile</h1><p>texte</p>
<img src="${CANARI}/img.png"><iframe src="${CANARI}/iframe"></iframe>
<script src="${CANARI}/script.js"></script>
<script>
${ESSAIS_RESEAU}
window.renduDemarrer = async function () {
  const r = [];
  r.push(await fe('${CANARI}/fetch'));
  r.push(await ws('ws://127.0.0.1:3901/ws'));
  r.push(await essai(new Promise((ok) => { const w = new Worker('data:text/javascript,' + encodeURIComponent(
    "fetch('${CANARI}/worker').then(()=>postMessage('ATTEINT'),()=>postMessage('refus'));" +
    "try{const s=new WebSocket('ws://127.0.0.1:3901/wsworker');s.onerror=()=>{};}catch(e){}")); w.onmessage = (e) => ok(e.data); w.onerror = () => ok('worker refusé'); })));
  r.push(await essai(new Promise((ok) => { const e = new EventSource('${CANARI}/sse'); e.onerror = () => { e.close(); ok('refus'); }; e.onopen = () => ok('OUVERT'); })));
  try { navigator.sendBeacon('${CANARI}/beacon', 'x'); } catch (e) {}
  try { window.open('${CANARI}/popup'); } catch (e) {}
  try { const x = new XMLHttpRequest(); x.open('GET', '${CANARI}/xhr', false); x.send(); r.push('XHR ' + x.status); } catch (e) { r.push('refus'); }
  try {
    const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:3478'}]});
    pc.createDataChannel('d'); await pc.setLocalDescription(await pc.createOffer());
    await new Promise((ok) => setTimeout(ok, 2500)); pc.close(); r.push('webrtc essayé');
  } catch (e) { r.push('webrtc refusé'); }
  try { if ('serviceWorker' in navigator) await navigator.serviceWorker.register('data:text/javascript,1'); r.push('SW'); } catch (e) { r.push('sw refusé'); }
  window.__essais = r;
  window.renduPret = true;
};
</script></body></html>`;

function boite({ hors = false } = {}) {
  const vertices = [[0, 0, 0], [10, 0, 0], [10, 6, 0], [0, 6, 0], [0, 0, 3], [10, 0, 3], [10, 6, 3], [0, 6, 3]];
  const triangles = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
    [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]];
  const type = new Array(12).fill('paroi');
  type[4] = 'vitrage';
  const valeurs = [12, 14, 60, 58, 95, 90, 40, 42, 22, 25, null, 33];
  if (hors) {
    // un bâtiment voisin translucide à l'ouest
    const b = vertices.length;
    vertices.push([-14, 0, 0], [-4, 0, 0], [-4, 6, 0], [-14, 6, 0], [-14, 0, 6], [-4, 0, 6], [-4, 6, 6], [-14, 6, 6]);
    for (const [i, j, k] of triangles.slice()) { triangles.push([i + b, j + b, k + b]); type.push('hors_volume'); valeurs.push(null); }
  }
  return {
    repere: 'batiment', vertices, triangles, groupes: [], type, periodes: {},
    grandeurs: { pertes_hiver_kwh_m2: valeurs, gains_ete_kwh: valeurs.map((v) => (v === null ? null : v * 3)) },
    unites: { pertes_hiver_kwh_m2: 'kWh/m²' },
  };
}

function analysePdf(buf) {
  const t = buf.toString('latin1');
  const pages = (t.match(/\/Type\s*\/Page(?![s\w])/g) || []).length;
  const utf16 = (s) => `<FEFF${Buffer.from(s, 'utf16le').swap16().toString('hex').toUpperCase()}>`;
  const titre = (s) => t.includes(`(${s})`) || t.toUpperCase().includes(utf16(s));
  return {
    entete: t.startsWith('%PDF-'),
    pages,
    outlines: /\/Outlines/.test(t),
    titres: ['Pertes d’hiver', 'Gains d’été', 'Vue 3D'].filter(titre),
    liens: (t.match(/\/Subtype\s*\/Link/g) || []).length,
    internes: (t.match(/\/Dest\b|\/S\s*\/GoTo/g) || []).length,
    externes: (t.match(/\/URI\s*\(/g) || []).length,
    balise: /\/StructTreeRoot/.test(t),
  };
}

try {
  await etape('GET /sante sans clé', async () => {
    const r = await fetch(`${BASE}/sante`);
    assert.equal(r.status, 200); assert.deepEqual(await r.json(), { ok: true });
  });
  await etape('Chromium', async () => service.rendu.version());

  await etape('sans clé / mauvaise clé → 401', async () => {
    for (const cle of [null, 'x'.repeat(40), `${CLE}x`]) {
      const r = await appeler('/pdf', { html: '<p>x</p>' }, { cle });
      assert.equal(r.status, 401);
      assert.deepEqual(await r.json(), { detail: 'Clé du service de rendu absente ou invalide.' });
    }
  });

  await etape('POST /pdf (document du contrat)', async () => {
    const avant = bloquees.length;
    const r = await appeler('/pdf', { html: HTML_CONTRAT, format: 'A4', paysage: false, marges_mm: 14 });
    assert.equal(r.status, 200, `statut ${r.status}`);
    assert.equal(r.headers.get('content-type'), 'application/pdf');
    const buf = Buffer.from(await r.arrayBuffer());
    ecrire('essai-contrat.pdf', buf);
    const a = analysePdf(buf);
    assert.ok(a.entete, '%PDF'); assert.ok(a.pages >= 3, `pages ${a.pages}`);
    assert.ok(a.outlines, '/Outlines'); assert.equal(a.titres.length, 3, `titres ${a.titres}`);
    assert.ok(a.liens >= 4, `liens ${a.liens}`); assert.ok(a.internes >= 4, `internes ${a.internes}`);
    return `${buf.length} o, ${a.pages} pages, signets ${a.titres.join(' | ')}, ${a.liens} liens (${a.internes} internes), balisé=${a.balise}, refus vus par la route=${bloquees.length - avant}`;
  });

  await etape('POST /pdf A3 paysage', async () => {
    const r = await appeler('/pdf', { html: HTML_CONTRAT, format: 'A3', paysage: true, marges_mm: 10 });
    assert.equal(r.status, 200);
    const buf = Buffer.from(await r.arrayBuffer());
    const m = buf.toString('latin1').match(/\/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)/);
    assert.ok(m && Number(m[1]) > Number(m[2]) && Math.abs(Number(m[1]) - 1190.5) < 2, `MediaBox ${m && m.slice(1)}`);
    return `MediaBox ${m[1]} × ${m[2]} pt`;
  });

  await etape('POST /pdf (document hostile sans CSP)', async () => {
    const avant = bloquees.length;
    const r = await appeler('/pdf', { html: HTML_HOSTILE });
    assert.equal(r.status, 200, `statut ${r.status}`);
    const buf = Buffer.from(await r.arrayBuffer());
    ecrire('essai-hostile.pdf', buf);
    assert.ok(analysePdf(buf).entete);
    const vues = bloquees.slice(avant);
    assert.ok(vues.length >= 5, `refus vus ${vues.length}`);
    assert.ok(vues.every((u) => !u.startsWith('data:') && !u.startsWith('about:')));
    return `${vues.length} requêtes vues par la route, toutes abandonnées`;
  });

  await etape('POST /images (2 figures 1200×700 ×2)', async () => {
    const fig = (y) => ({ data: [{ type: 'scatter', y, mode: 'lines+markers' }], layout: { title: { text: 'Essai' } } });
    const r = await appeler('/images', { figures: [fig([1, 3, 2, 4]), { data: [{ type: 'bar', x: ['a', 'b'], y: [2, 5] }] }], largeur: 1200, hauteur: 700, echelle: 2 });
    assert.equal(r.status, 200, `statut ${r.status}`);
    const { images } = await r.json();
    assert.equal(images.length, 2);
    const infos = images.map((u, i) => {
      assert.ok(u.startsWith('data:image/png;base64,'));
      const png = dataUrlVersPng(u); ecrire(`essai-figure-${i + 1}.png`, png);
      const p = lirePng(png);
      assert.equal(p.largeur, 2400); assert.equal(p.hauteur, 1400); assert.ok(p.couleurs > 5, `couleurs ${p.couleurs}`);
      return `${p.largeur}×${p.hauteur}, ${p.couleurs} couleurs`;
    });
    return infos.join(' ; ');
  });

  await etape('POST /images refus (41 figures, échelle 4, largeur 50)', async () => {
    const f = { data: [] };
    for (const corps of [{ figures: new Array(41).fill(f) }, { figures: [f], echelle: 4 }, { figures: [f], largeur: 50 }]) {
      const r = await appeler('/images', corps);
      assert.equal(r.status, 400);
    }
  });

  await etape('POST /vue3d boîte 12 triangles (iso)', async () => {
    const r = await appeler('/vue3d', { scene: boite(), grandeur: 'pertes_hiver_kwh_m2', camera: 'iso', largeur: 1200, hauteur: 700 });
    assert.equal(r.status, 200, `statut ${r.status}`);
    const { image } = await r.json();
    const png = dataUrlVersPng(image); ecrire('essai-vue3d-iso.png', png);
    const p = lirePng(png);
    assert.equal(p.largeur, 1200); assert.equal(p.hauteur, 700); assert.ok(p.couleurs > 20, `couleurs ${p.couleurs}`);
    return `${p.largeur}×${p.hauteur}, ${p.couleurs} couleurs distinctes (échantillon)`;
  });

  await etape('POST /vue3d toutes caméras + hors_volume', async () => {
    const out = [];
    for (const camera of ['sud', 'nord', 'est', 'ouest', 'dessus']) {
      const r = await appeler('/vue3d', { scene: boite({ hors: true }), grandeur: 'gains_ete_kwh', camera, largeur: 800, hauteur: 500 });
      assert.equal(r.status, 200, `${camera} ${r.status}`);
      const png = dataUrlVersPng((await r.json()).image); ecrire(`essai-vue3d-${camera}.png`, png);
      const p = lirePng(png); assert.ok(p.couleurs > 10, `${camera} couleurs ${p.couleurs}`);
      out.push(`${camera}:${p.couleurs}`);
    }
    return out.join(' ');
  });

  await etape('POST /vue3d grandeur / caméra inconnues → 400 ; 20 001 triangles → 413', async () => {
    let r = await appeler('/vue3d', { scene: boite(), grandeur: 'inconnue' });
    assert.equal(r.status, 400); assert.deepEqual(await r.json(), { detail: 'Grandeur inconnue.' });
    r = await appeler('/vue3d', { scene: boite(), grandeur: 'pertes_hiver_kwh_m2', camera: 'diagonale' });
    assert.equal(r.status, 400); assert.deepEqual(await r.json(), { detail: 'Caméra inconnue.' });
    const s = boite(); s.triangles = new Array(20001).fill([0, 1, 2]);
    r = await appeler('/vue3d', { scene: s, grandeur: 'pertes_hiver_kwh_m2' });
    assert.equal(r.status, 413);
  });

  await etape('POST /vue3d 20 000 triangles', async () => {
    const vertices = []; const triangles = []; const N = 100;   // grille 100×100 → 20 000 triangles
    for (let j = 0; j <= N; j += 1) for (let i = 0; i <= N; i += 1) vertices.push([i / 5, j / 5, Math.sin(i / 9) * Math.cos(j / 11) * 2]);
    for (let j = 0; j < N; j += 1) for (let i = 0; i < N; i += 1) {
      const a = j * (N + 1) + i; triangles.push([a, a + 1, a + N + 2], [a, a + N + 2, a + N + 1]);
    }
    const v = triangles.map((_, k) => k % 997);
    const t0 = Date.now();
    const r = await appeler('/vue3d', { scene: { vertices, triangles, type: triangles.map(() => 'paroi'), grandeurs: { solaire_ete_kwh_m2: v }, unites: {} }, grandeur: 'solaire_ete_kwh_m2', camera: 'iso', largeur: 1600, hauteur: 1000 });
    assert.equal(r.status, 200, `statut ${r.status}`);
    ecrire('essai-vue3d-20000.png', dataUrlVersPng((await r.json()).image));
    return `${Date.now() - t0} ms`;
  });

  await etape('erreurs : JSON invalide 400, 404, 405, 413 (> 30 Mo)', async () => {
    let r = await fetch(`${BASE}/pdf`, { method: 'POST', headers: { 'X-Rendu-Cle': CLE }, body: '{pas du json' });
    assert.equal(r.status, 400);
    assert.equal((await fetch(`${BASE}/autre`)).status, 404);
    assert.equal((await fetch(`${BASE}/vue3d`)).status, 405);
    r = await fetch(`${BASE}/pdf`, { method: 'POST', headers: { 'X-Rendu-Cle': CLE }, body: Buffer.alloc(31 * 1024 * 1024, 0x20) });
    assert.equal(r.status, 413);
  });

  await etape('file : 6 requêtes simultanées → 5 servies, 1 × 503', async () => {
    const rs = await Promise.all(new Array(6).fill(0).map(() => appeler('/vue3d', { scene: boite(), grandeur: 'pertes_hiver_kwh_m2', largeur: 400, hauteur: 300 })));
    const st = rs.map((x) => x.status).sort();
    assert.deepEqual(st, [200, 200, 200, 200, 200, 503]);
    return st.join(',');
  });

  await etape('défense en profondeur : Chromium des mêmes options SANS route', async () => {
    // Prouve que les couches de lancement et de contexte tiennent seules.
    const b = await chromium.launch({ headless: true, args: [...ARGS_CHROMIUM] });
    try {
      const ctx = await b.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
      const page = await ctx.newPage();
      await page.setContent(HTML_HOSTILE, { waitUntil: 'load' });
      await page.evaluate(() => window.renduDemarrer());
      const essais = await page.evaluate(() => window.__essais);
      assert.ok(!essais.some((e) => /ATTEINT|OUVERT|XHR 200/.test(String(e))), JSON.stringify(essais));
      await ctx.close();
      // et sans `offline` : reste le proxy mort + DNS impossible
      const ctx2 = await b.newContext({ serviceWorkers: 'block' });
      const p2 = await ctx2.newPage();
      await p2.setContent(HTML_HOSTILE, { waitUntil: 'load' });
      await p2.evaluate(() => window.renduDemarrer());
      const essais2 = await p2.evaluate(() => window.__essais);
      assert.ok(!essais2.some((e) => /ATTEINT|OUVERT|XHR 200/.test(String(e))), JSON.stringify(essais2));
      await ctx2.close();
      return `offline: ${JSON.stringify(essais)} ; sans offline: ${JSON.stringify(essais2)}`;
    } finally {
      await b.close();
    }
  });

  await etape('canaris : rien n’a atteint la boucle locale (HTTP, WebSocket, UDP)', async () => {
    assert.equal(canari.http.length, 0, `HTTP ${JSON.stringify(canari.http)}`);
    assert.equal(canari.ws, 0, `WS ${canari.ws}`);
    assert.equal(canari.udp, 0, `UDP ${canari.udp}`);
    const hotes = [...new Set(bloquees.map((u) => { try { return new URL(u).host; } catch { return '?'; } }))];
    return `0 HTTP, 0 WS, 0 UDP ; ${bloquees.length} requêtes abandonnées au total (hôtes : ${hotes.join(', ')})`;
  });
} finally {
  await service.fermer();
  srvCanari.close(); udp.close();
}

process.stdout.write(`${resultats.join('\n')}\n\n${echecs === 0 ? 'TOUT VERT' : `${echecs} ÉCHEC(S)`}\n`);
if (SORTIE) fs.writeFileSync(path.join(SORTIE, 'resultats.txt'), `${resultats.join('\n')}\n`);
process.exit(echecs === 0 ? 0 : 1);
