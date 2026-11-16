// Validation des corps (interface I8). Chaque refus porte un message FIXE :
// jamais l'entrée de l'appelant recopiée (elle peut contenir n'importe quoi).

export class HttpError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/** Vrai pour les seules URL que la page a le droit de charger (contenu en ligne). */
export function urlPermise(url) {
  return typeof url === 'string' && (url.startsWith('data:') || url.startsWith('about:'));
}

export const LIMITES = Object.freeze({
  corpsOctets: 30 * 1024 * 1024,
  figures: 40,
  dimMin: 100,
  dimMax: 3000,
  echelleMin: 1,
  echelleMax: 3,
  margeMax: 50,
  triangles: 20000,
  sommets: 60000,
});

export const FORMATS = new Set(['A4', 'A3']);
export const CAMERAS = new Set(['iso', 'sud', 'nord', 'est', 'ouest', 'dessus']);
export const TYPES = new Set(['paroi', 'vitrage', 'hors_volume']);

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isFiniteNumber = (v) => typeof v === 'number' && Number.isFinite(v);

function entier(valeur, defaut, min, max, message) {
  if (valeur === undefined || valeur === null) return defaut;
  if (!Number.isInteger(valeur) || valeur < min || valeur > max) throw new HttpError(400, message);
  return valeur;
}

function corpsObjet(corps) {
  if (!isObject(corps)) throw new HttpError(400, 'Le corps doit être un objet JSON.');
  return corps;
}

/** POST /pdf */
export function validerPdf(corps) {
  corpsObjet(corps);
  const { html, format = 'A4', paysage = false, marges_mm: marges = 14 } = corps;
  if (typeof html !== 'string' || html.length === 0) {
    throw new HttpError(400, 'Champ « html » absent ou vide.');
  }
  if (!FORMATS.has(format)) throw new HttpError(400, 'Format de page inconnu (A4 ou A3).');
  if (typeof paysage !== 'boolean') throw new HttpError(400, 'Champ « paysage » invalide.');
  if (!isFiniteNumber(marges) || marges < 0 || marges > LIMITES.margeMax) {
    throw new HttpError(400, 'Champ « marges_mm » invalide.');
  }
  return { html, format, paysage, marges_mm: marges };
}

/** POST /images */
export function validerImages(corps) {
  corpsObjet(corps);
  const { figures } = corps;
  if (!Array.isArray(figures) || figures.length === 0 || figures.length > LIMITES.figures) {
    throw new HttpError(400, 'Champ « figures » invalide (1 à 40 figures).');
  }
  for (const fig of figures) {
    if (!isObject(fig) || !Array.isArray(fig.data) || (fig.layout !== undefined && !isObject(fig.layout))) {
      throw new HttpError(400, 'Figure invalide (attendu {data: [...], layout: {...}}).');
    }
  }
  const largeur = entier(corps.largeur, 1200, LIMITES.dimMin, LIMITES.dimMax, 'Champ « largeur » invalide (100 à 3000).');
  const hauteur = entier(corps.hauteur, 700, LIMITES.dimMin, LIMITES.dimMax, 'Champ « hauteur » invalide (100 à 3000).');
  let echelle = corps.echelle;
  if (echelle === undefined || echelle === null) echelle = 2;
  if (!isFiniteNumber(echelle) || echelle < LIMITES.echelleMin || echelle > LIMITES.echelleMax) {
    throw new HttpError(400, 'Champ « echelle » invalide (1 à 3).');
  }
  return {
    figures: figures.map((f) => ({ data: f.data, layout: f.layout || {} })),
    largeur, hauteur, echelle,
  };
}

/** POST /vue3d — scène I4. Renvoie une forme compacte et sûre pour la page. */
export function validerVue3d(corps) {
  corpsObjet(corps);
  const { scene } = corps;
  if (!isObject(scene)) throw new HttpError(400, 'Champ « scene » absent ou invalide.');
  const { vertices, triangles } = scene;
  if (!Array.isArray(triangles) || triangles.length === 0) {
    throw new HttpError(400, 'Scène sans triangles.');
  }
  if (triangles.length > LIMITES.triangles) {
    throw new HttpError(413, 'Scène trop grande (20 000 triangles au plus).');
  }
  if (!Array.isArray(vertices) || vertices.length < 3 || vertices.length > LIMITES.sommets) {
    throw new HttpError(400, 'Champ « vertices » invalide.');
  }
  const pos = new Array(vertices.length * 3);
  for (let i = 0; i < vertices.length; i += 1) {
    const v = vertices[i];
    if (!Array.isArray(v) || v.length !== 3 || !v.every(isFiniteNumber)) {
      throw new HttpError(400, 'Sommet invalide (attendu [x, y, z]).');
    }
    pos[3 * i] = v[0]; pos[3 * i + 1] = v[1]; pos[3 * i + 2] = v[2];
  }
  const n = triangles.length;
  const tri = new Array(n * 3);
  for (let t = 0; t < n; t += 1) {
    const f = triangles[t];
    if (!Array.isArray(f) || f.length !== 3
        || !f.every((i) => Number.isInteger(i) && i >= 0 && i < vertices.length)) {
      throw new HttpError(400, 'Triangle invalide (indices de sommets attendus).');
    }
    tri[3 * t] = f[0]; tri[3 * t + 1] = f[1]; tri[3 * t + 2] = f[2];
  }
  let types = scene.type;
  if (types === undefined || types === null) {
    types = new Array(n).fill('paroi');
  } else if (!Array.isArray(types) || types.length !== n || !types.every((t) => TYPES.has(t))) {
    throw new HttpError(400, 'Champ « type » invalide (paroi, vitrage ou hors_volume par triangle).');
  }
  const { grandeur } = corps;
  const grandeurs = scene.grandeurs;
  if (typeof grandeur !== 'string' || !isObject(grandeurs)
      || !Object.prototype.hasOwnProperty.call(grandeurs, grandeur)) {
    throw new HttpError(400, 'Grandeur inconnue.');
  }
  const valeurs = grandeurs[grandeur];
  if (!Array.isArray(valeurs) || valeurs.length !== n
      || !valeurs.every((v) => v === null || typeof v === 'number')) {
    throw new HttpError(400, 'Valeurs de la grandeur invalides (une par triangle).');
  }
  const camera = corps.camera === undefined || corps.camera === null ? 'iso' : corps.camera;
  if (typeof camera !== 'string' || !CAMERAS.has(camera)) throw new HttpError(400, 'Caméra inconnue.');
  const largeur = entier(corps.largeur, 1200, LIMITES.dimMin, LIMITES.dimMax, 'Champ « largeur » invalide (100 à 3000).');
  const hauteur = entier(corps.hauteur, 700, LIMITES.dimMin, LIMITES.dimMax, 'Champ « hauteur » invalide (100 à 3000).');
  let unite = '';
  if (isObject(scene.unites) && typeof scene.unites[grandeur] === 'string') {
    unite = scene.unites[grandeur].slice(0, 40);
  }
  return {
    positions: pos,
    triangles: tri,
    types,
    valeurs: valeurs.map((v) => (isFiniteNumber(v) ? v : null)),
    camera, largeur, hauteur, unite,
  };
}
