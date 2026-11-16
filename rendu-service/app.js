// Serveur HTTP de l'interface I8 (node:http seul). Aucun contenu de corps n'est
// journalisé ; toute erreur rend un message fixe, jamais l'entrée ni une pile.

import crypto from 'node:crypto';
import { HttpError, LIMITES, validerImages, validerPdf, validerVue3d } from './validation.js';

export const DELAI_MS = 60_000;
export const ATTENTE_MAX = 4;
export const CLE_MIN = 24;

const MSG_CLE = 'Clé du service de rendu absente ou invalide.';

/** Comparaison à temps constant (empreintes de même longueur, quelle que soit l'entrée). */
export function cleValide(fournie, attendue) {
  if (typeof fournie !== 'string' || fournie.length === 0) return false;
  const a = crypto.createHash('sha256').update(fournie, 'utf8').digest();
  const b = crypto.createHash('sha256').update(attendue, 'utf8').digest();
  return crypto.timingSafeEqual(a, b);
}

/** Lit la clé depuis l'environnement ; lève si absente ou trop courte. */
export function lireCle(env = process.env) {
  const cle = env.RENDU_SERVICE_KEY;
  if (typeof cle !== 'string' || cle.length < CLE_MIN) {
    throw new Error(`RENDU_SERVICE_KEY absente ou trop courte (${CLE_MIN} caractères au moins).`);
  }
  return cle;
}

/** File sérialisée : une tâche à la fois, au plus `attenteMax` en attente. */
export class FileSerielle {
  constructor(attenteMax = ATTENTE_MAX) {
    this.attenteMax = attenteMax;
    this.attente = [];
    this.occupe = false;
  }

  ajouter(tache) {
    if (this.occupe && this.attente.length >= this.attenteMax) {
      return Promise.reject(new HttpError(503, 'Service de rendu saturé, réessayer plus tard.'));
    }
    return new Promise((resolve, reject) => {
      this.attente.push({ tache, resolve, reject });
      this.suivante();
    });
  }

  suivante() {
    if (this.occupe) return;
    const item = this.attente.shift();
    if (!item) return;
    this.occupe = true;
    Promise.resolve()
      .then(item.tache)
      .then(item.resolve, item.reject)
      .finally(() => { this.occupe = false; this.suivante(); });
  }
}

/**
 * Exécute `travail(signal)` avec un délai ; au-delà, le signal est levé (le rendu
 * ferme son contexte), on laisse au travail `graceMs` pour s'arrêter — la file
 * reste ainsi vraiment sérialisée —, puis 504.
 */
export async function avecDelai(travail, delaiMs, graceMs = 10_000) {
  const ctrl = new AbortController();
  let minuteur;
  let promesse;
  const delai = new Promise((_, reject) => {
    minuteur = setTimeout(async () => {
      ctrl.abort();
      let grace;
      await Promise.race([
        promesse.catch(() => {}),
        new Promise((r) => { grace = setTimeout(r, graceMs); }),
      ]);
      clearTimeout(grace);
      reject(new HttpError(504, 'Délai de rendu dépassé.'));
    }, delaiMs);
  });
  promesse = Promise.resolve().then(() => travail(ctrl.signal));
  promesse.catch(() => {});   // un rejet tardif après le délai n'est pas une erreur non gérée
  // Un résultat arrivé après le dépassement (pendant la grâce) reste un 504.
  const resultat = promesse.then((v) => {
    if (ctrl.signal.aborted) throw new HttpError(504, 'Délai de rendu dépassé.');
    return v;
  }, (err) => {
    throw ctrl.signal.aborted ? new HttpError(504, 'Délai de rendu dépassé.') : err;
  });
  resultat.catch(() => {});
  try {
    return await Promise.race([resultat, delai]);
  } finally {
    clearTimeout(minuteur);
  }
}

function lireCorps(req, limite) {
  return new Promise((resolve, reject) => {
    const annonce = Number(req.headers['content-length']);
    if (Number.isFinite(annonce) && annonce > limite) {
      reject(new HttpError(413, 'Corps trop volumineux (30 Mo au plus).'));
      req.resume();
      return;
    }
    const morceaux = [];
    let total = 0;
    let fini = false;
    req.on('data', (m) => {
      if (fini) return;
      total += m.length;
      if (total > limite) {
        fini = true;
        reject(new HttpError(413, 'Corps trop volumineux (30 Mo au plus).'));
        req.resume();
        return;
      }
      morceaux.push(m);
    });
    req.on('end', () => {
      if (fini) return;
      fini = true;
      resolve(Buffer.concat(morceaux));
    });
    req.on('error', () => {
      if (fini) return;
      fini = true;
      reject(new HttpError(400, 'Corps illisible.'));
    });
  });
}

function json(res, status, objet) {
  const corps = Buffer.from(JSON.stringify(objet));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': corps.length,
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(corps);
}

const ROUTES = {
  '/sante': 'GET',
  '/pdf': 'POST',
  '/images': 'POST',
  '/vue3d': 'POST',
};

/**
 * Fabrique le gestionnaire de requêtes.
 * @param {{cle: string, rendu: {pdf, images, vue3d}, delaiMs?, attenteMax?, journal?}} options
 */
export function creerGestionnaire({ cle, rendu, delaiMs = DELAI_MS, attenteMax = ATTENTE_MAX, journal = () => {} }) {
  if (typeof cle !== 'string' || cle.length < CLE_MIN) throw new Error('clé de service invalide');
  const file = new FileSerielle(attenteMax);

  return async function gestionnaire(req, res) {
    const debut = Date.now();
    let chemin = '?';
    let status = 500;
    try {
      chemin = new URL(req.url, 'http://rendu.invalid').pathname;
      const methode = ROUTES[chemin];
      if (!methode) throw new HttpError(404, 'Route inconnue.');
      if (req.method !== methode) {
        res.setHeader('Allow', methode);
        throw new HttpError(405, 'Méthode non permise.');
      }
      if (chemin === '/sante') {
        status = 200;
        json(res, 200, { ok: true });
        return;
      }
      if (!cleValide(req.headers['x-rendu-cle'], cle)) throw new HttpError(401, MSG_CLE);

      const brut = await lireCorps(req, LIMITES.corpsOctets);
      let corps;
      try {
        corps = JSON.parse(brut.toString('utf8'));
      } catch {
        throw new HttpError(400, 'Corps JSON invalide.');
      }

      if (chemin === '/pdf') {
        const p = validerPdf(corps);
        const pdf = await file.ajouter(() => avecDelai((signal) => rendu.pdf(p, signal), delaiMs));
        status = 200;
        res.writeHead(200, {
          'Content-Type': 'application/pdf',
          'Content-Length': pdf.length,
          'Cache-Control': 'no-store',
        });
        res.end(pdf);
      } else if (chemin === '/images') {
        const p = validerImages(corps);
        const images = await file.ajouter(() => avecDelai((signal) => rendu.images(p, signal), delaiMs));
        status = 200;
        json(res, 200, { images });
      } else {
        const p = validerVue3d(corps);
        const image = await file.ajouter(() => avecDelai((signal) => rendu.vue3d(p, signal), delaiMs));
        status = 200;
        json(res, 200, { image });
      }
    } catch (err) {
      if (err instanceof HttpError) {
        status = err.status;
        if (!res.headersSent) json(res, err.status, { detail: err.detail });
      } else {
        status = 503;
        if (!res.headersSent) json(res, 503, { detail: 'Rendu indisponible.' });
        journal({ evenement: 'erreur', type: err && err.name ? String(err.name) : 'Error' });
      }
    } finally {
      // Méthode, chemin connu, statut, durée : rien d'autre (ni corps, ni en-têtes).
      // Le healthcheck réussi (toutes les 30 s) n'est pas journalisé.
      if (!(chemin === '/sante' && status === 200)) journal({ methode: req.method, chemin: ROUTES[chemin] ? chemin : '(autre)', status, ms: Date.now() - debut });
    }
  };
}
