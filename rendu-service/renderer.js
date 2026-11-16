// Rendu par Chromium (Playwright), SANS AUCUN accès réseau sortant.
//
// Couches (page.route seul ne voit ni WebSocket, ni Workers, ni redirections) :
//   1. context.route('**/*') : toute requête autre que data:/about: est abandonnée ;
//   2. context.routeWebSocket : tout WebSocket est fermé sans jamais joindre de serveur ;
//   3. contexte `offline: true` (émulation réseau coupée, workers compris),
//      `serviceWorkers: 'block'`, `acceptDownloads: false` ;
//   4. lancement : tout le trafic vers un proxy mort (127.0.0.1:9), boucle locale
//      comprise (`<-loopback>`), résolution DNS impossible (`MAP * ~NOTFOUND`),
//      WebRTC sans UDP non mandaté, aucun trafic de fond (mises à jour, métriques…) ;
//   5. le conteneur lui-même : réseau interne, aucun port publié.
// Un nouveau contexte par requête, toujours fermé (aussi au dépassement de délai).

import { chromium } from 'playwright';
import { HttpError, urlPermise } from './validation.js';

export const ARGS_CHROMIUM = Object.freeze([
  '--proxy-server=http://127.0.0.1:9',
  '--proxy-bypass-list=<-loopback>',
  '--host-resolver-rules=MAP * ~NOTFOUND',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-domain-reliability',
  '--disable-sync',
  '--disable-breakpad',
  '--no-pings',
  '--dns-prefetch-disable',
  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--disable-dev-shm-usage',
  // WebGL logiciel (pas de GPU dans le conteneur) — nécessaire aux vues 3D.
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
]);

const PAGE_VIDE = '<!doctype html><html><head><meta charset="utf-8">'
  + '<style>html,body{margin:0;padding:0;background:#fff}</style></head><body></body></html>';

const PIED = '<div style="width:100%;text-align:center;font-size:8px;color:#808080;'
  + 'font-family:sans-serif;">page <span class="pageNumber"></span> / <span class="totalPages"></span></div>';

/**
 * @param {{libs: {plotly, three, vue3d}, surBloque?: (url) => void, delaiMs?: number}} options
 *   surBloque : notification de chaque requête refusée (essais) — jamais journalisée.
 */
export function creerRendu({ libs, surBloque = () => {}, delaiMs = 60_000 }) {
  let navigateur = null;
  let lancement = null;

  async function obtenirNavigateur() {
    if (navigateur && navigateur.isConnected()) return navigateur;
    if (!lancement) {
      lancement = chromium.launch({ headless: true, args: [...ARGS_CHROMIUM] })
        .then((b) => {
          navigateur = b;
          b.on('disconnected', () => { if (navigateur === b) navigateur = null; });
          return b;
        })
        .finally(() => { lancement = null; });
    }
    return lancement;
  }

  async function avecPage(signal, { largeur = 1200, hauteur = 900 } = {}, travail) {
    const b = await obtenirNavigateur();
    const context = await b.newContext({
      offline: true,
      serviceWorkers: 'block',
      acceptDownloads: false,
      javaScriptEnabled: true,
      bypassCSP: false,
      viewport: { width: largeur, height: hauteur },
      deviceScaleFactor: 1,
      locale: 'fr-FR',
      timezoneId: 'Europe/Paris',
      permissions: [],
    });
    const fermer = () => context.close().catch(() => {});
    signal.addEventListener('abort', fermer, { once: true });
    try {
      await context.route('**/*', async (route) => {
        const url = route.request().url();
        if (urlPermise(url)) {
          await route.continue().catch(() => {});
          return;
        }
        try { surBloque(url); } catch { /* notification seulement */ }
        await route.abort('blockedbyclient').catch(() => {});
      });
      await context.routeWebSocket(() => true, (ws) => {
        try { surBloque(ws.url()); } catch { /* notification seulement */ }
        ws.close({ code: 1008, reason: 'refusé' }).catch(() => {});
      });
      const page = await context.newPage();
      page.setDefaultTimeout(delaiMs);
      page.on('dialog', (d) => d.dismiss().catch(() => {}));
      if (signal.aborted) throw new HttpError(504, 'Délai de rendu dépassé.');
      return await travail(page);
    } finally {
      signal.removeEventListener('abort', fermer);
      await fermer();
    }
  }

  return {
    obtenirNavigateur,

    async fermer() {
      const b = navigateur;
      navigateur = null;
      if (b) await b.close().catch(() => {});
    },

    async version() {
      return (await obtenirNavigateur()).version();
    },

    /** POST /pdf → Buffer */
    pdf(p, signal) {
      return avecPage(signal, {}, async (page) => {
        await page.setContent(p.html, { waitUntil: 'load' });
        const etat = await page.evaluate(() => ({
          plotly: typeof window.Plotly !== 'undefined',
          besoin3d: !!document.querySelector('[data-rendu-3d]'),
          three: typeof window.THREE !== 'undefined',
          demarrer: typeof window.renduDemarrer === 'function',
        }));
        try {
          if (!etat.plotly) await page.addScriptTag({ content: libs.plotly });
          if (etat.besoin3d && !etat.three) await page.addScriptTag({ content: libs.three });
        } catch {
          throw new HttpError(400, 'Bibliothèques refusées par le document (CSP).');
        }
        if (etat.demarrer) {
          const ok = await page.evaluate(async () => {
            try { await window.renduDemarrer(); return true; } catch { return false; }
          });
          if (!ok) throw new HttpError(400, 'Le document n’a pas pu être rendu.');
          await page.waitForFunction(() => window.renduPret === true, null, { polling: 100 });
        }
        await page.emulateMedia({ media: 'print' });
        const marge = `${p.marges_mm}mm`;
        return page.pdf({
          format: p.format,
          landscape: p.paysage,
          margin: { top: marge, right: marge, bottom: marge, left: marge },
          printBackground: true,
          preferCSSPageSize: false,
          outline: true,
          tagged: true,
          displayHeaderFooter: true,
          headerTemplate: '<span></span>',
          footerTemplate: PIED,
        });
      });
    },

    /** POST /images → ["data:image/png;base64,…"] */
    images(p, signal) {
      return avecPage(signal, {}, async (page) => {
        await page.setContent(PAGE_VIDE, { waitUntil: 'load' });
        await page.addScriptTag({ content: libs.plotly });
        const sortie = [];
        for (const fig of p.figures) {
          if (signal.aborted) throw new HttpError(504, 'Délai de rendu dépassé.');
          const url = await page.evaluate(async ({ fig, opts }) => {
            try {
              return await window.Plotly.toImage(fig, opts);
            } catch {
              return null;
            }
          }, { fig, opts: { format: 'png', width: p.largeur, height: p.hauteur, scale: p.echelle } });
          if (typeof url !== 'string' || !url.startsWith('data:image/png;base64,')) {
            throw new HttpError(400, 'Figure Plotly impossible à rendre.');
          }
          sortie.push(url);
        }
        return sortie;
      });
    },

    /** POST /vue3d → "data:image/png;base64,…" */
    vue3d(p, signal) {
      return avecPage(signal, { largeur: p.largeur, hauteur: p.hauteur }, async (page) => {
        await page.setContent(PAGE_VIDE, { waitUntil: 'load' });
        await page.addScriptTag({ content: libs.three });
        await page.addScriptTag({ content: libs.vue3d });
        const url = await page.evaluate((params) => {
          try { return window.renduVue3d(params); } catch { return null; }
        }, p);
        if (typeof url !== 'string' || !url.startsWith('data:image/png;base64,')) {
          throw new Error('rendu 3D impossible');   // → 503 (WebGL indisponible)
        }
        return url;
      });
    },
  };
}
