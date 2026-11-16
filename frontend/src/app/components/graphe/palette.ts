import { FigureRendu, PaletteRendu } from './graphe.types';

/**
 * Couleurs des graphes et des vues 3D, tirées des jetons Foyer (`src/styles.scss`) : aucune couleur en dur.
 *
 * Jetons de données attendus (demandés à l'architecte, chantier bt-potentiels) :
 *   --serie-1 … --serie-8        palette catégorielle (ordre fixe, validée daltonisme)
 *   --seq-bas, --seq-haut        échelle séquentielle (une teinte, faible → fort)
 *   --div-froid, --div-neutre, --div-chaud   échelle divergente (deux pôles, milieu gris)
 * Tant qu'ils manquent, repli sur des jetons existants (jamais une valeur hexadécimale).
 */
const SERIES_REPLI = ['--accent', '--warning', '--success', '--danger', '--accent-deep', '--text-mute'];

export function jeton(nom: string): string {
  try { return getComputedStyle(document.documentElement).getPropertyValue(nom).trim(); } catch { return ''; }
}
function premier(...noms: string[]): string {
  for (const n of noms) { const v = jeton(n); if (v) return v; }
  return '';
}

export function couleursSeries(): string[] {
  const s: string[] = [];
  for (let i = 1; i <= 8; i++) { const v = jeton(`--serie-${i}`); if (v) s.push(v); }
  return s.length ? s : SERIES_REPLI.map(jeton).filter(Boolean);
}

/** Échelle continue : liste de [position 0..1, couleur]. */
export function echelle(p: 'sequentielle' | 'divergente'): [number, string][] {
  if (p === 'sequentielle') {
    return [[0, premier('--seq-bas', '--accent-tint')], [1, premier('--seq-haut', '--accent-deep')]];
  }
  return [[0, premier('--div-froid', '--accent')], [0.5, premier('--div-neutre', '--border')],
          [1, premier('--div-chaud', '--danger')]];
}

// ── Interpolation (couleurs résolues depuis les jetons : hex ou rgb()) ──
export function versRgb(c: string): [number, number, number] | null {
  const s = c.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(s);
  if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) return [0, 1, 2].map(i => parseInt(m![1][i] + m![1][i], 16)) as [number, number, number];
  m = /^rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/i.exec(s);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** Couleur à la position t (0..1) d'une échelle. */
export function couleurA(e: [number, string][], t: number): string {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  for (let i = 1; i < e.length; i++) {
    if (x <= e[i][0]) {
      const [p0, c0] = e[i - 1], [p1, c1] = e[i];
      const a = versRgb(c0), b = versRgb(c1);
      if (!a || !b) return c0;
      const u = p1 > p0 ? (x - p0) / (p1 - p0) : 0;
      return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * u)).join(', ')})`;
    }
  }
  return e[e.length - 1][1];
}

function couleursDiscretes(p: PaletteRendu, n: number): string[] {
  if (p === 'categorielle') return couleursSeries();
  const e = echelle(p);
  const k = Math.max(n, 2);
  return Array.from({ length: k }, (_, i) => couleurA(e, i / (k - 1)));
}

type Obj = Record<string, unknown>;
const estObj = (x: unknown): x is Obj => !!x && typeof x === 'object' && !Array.isArray(x);
function sous(o: Obj, k: string): Obj { if (!estObj(o[k])) o[k] = {}; return o[k] as Obj; }

const TYPES_ECHELLE = new Set(['heatmap', 'contour', 'choropleth', 'choroplethmapbox', 'histogram2d', 'histogram2dcontour', 'surface']);

/**
 * Copie de la figure aux couleurs du thème courant (contrat I6.g) : palette[i % n] sur `marker.color` et
 * `line.color` quand ils manquent ; échelle remplacée (`coloraxis`, `colorscale`) quand la palette n'est pas
 * catégorielle ; fond, texte, axes et grilles depuis les jetons.
 */
export function figureAuTheme(f: FigureRendu): { data: Obj[]; layout: Obj } {
  const data = structuredClone(f.data ?? []) as Obj[];
  const layout = structuredClone(f.layout ?? {}) as Obj;
  const p: PaletteRendu = (f.layout?.meta?.palette as PaletteRendu) ?? 'categorielle';
  const pal = couleursDiscretes(p, data.length);
  const cs = p === 'categorielle' ? null : echelle(p);
  const texte = jeton('--text'), mute = jeton('--text-mute'), bord = jeton('--border'), carte = jeton('--card');

  data.forEach((t, i) => {
    const c = pal[i % pal.length];
    const type = String(t['type'] ?? 'scatter');
    if (type === 'pie') {
      const m = sous(t, 'marker');
      if (m['colors'] === undefined) {
        const n = Array.isArray(t['labels']) ? (t['labels'] as unknown[]).length : Array.isArray(t['values']) ? (t['values'] as unknown[]).length : 1;
        const pc = couleursDiscretes(p, n);
        m['colors'] = Array.from({ length: n }, (_, k) => pc[k % pc.length]);
      }
      sous(m, 'line')['color'] = carte;
      return;
    }
    if (TYPES_ECHELLE.has(type)) {
      if (cs) t['colorscale'] = cs;
      return;
    }
    const m = sous(t, 'marker');
    if (Array.isArray(m['color'])) {
      if (cs && !m['coloraxis']) m['colorscale'] = cs;
    } else if (m['color'] === undefined) {
      m['color'] = c;
    }
    if (type === 'scatter' || type === 'scattergl' || type === 'scatterpolar' || type === 'scattergeo' || type === 'box' || type === 'violin') {
      const l = sous(t, 'line');
      if (l['color'] === undefined) l['color'] = c;
    }
    if (type === 'parcoords') {
      const l = sous(t, 'line');
      if (cs) l['colorscale'] = cs;
      if (l['color'] === undefined) l['color'] = c;
    }
  });

  for (const k of Object.keys(layout)) {
    if (/^coloraxis\d*$/.test(k) && estObj(layout[k]) && cs) (layout[k] as Obj)['colorscale'] = cs;
    if (/^[xy]axis\d*$/.test(k) && estObj(layout[k])) {
      const a = layout[k] as Obj;
      a['gridcolor'] ??= bord; a['linecolor'] ??= bord; a['zerolinecolor'] ??= bord;
      sous(a, 'tickfont')['color'] ??= mute;
      if (typeof a['title'] === 'string') a['title'] = { text: a['title'] };
      if (estObj(a['title'])) sous(a['title'] as Obj, 'font')['color'] ??= mute;
    }
  }
  if (!Object.keys(layout).some(k => /^[xy]axis/.test(k)) && !data.some(t => ['pie', 'scatterpolar', 'scattergeo', 'choropleth', 'parcoords', 'barpolar'].includes(String(t['type'])))) {
    for (const k of ['xaxis', 'yaxis']) {
      const a = sous(layout, k);
      a['gridcolor'] = bord; a['linecolor'] = bord; a['zerolinecolor'] = bord; sous(a, 'tickfont')['color'] = mute;
    }
  }
  layout['paper_bgcolor'] = 'rgba(0,0,0,0)';
  layout['plot_bgcolor'] = 'rgba(0,0,0,0)';
  const font = sous(layout, 'font');
  font['color'] = texte;
  font['family'] = jeton('--font-ui') || undefined;
  layout['separators'] ??= ', ';
  const leg = sous(layout, 'legend');
  sous(leg, 'font')['color'] = texte;
  const hl = sous(layout, 'hoverlabel');
  hl['bgcolor'] = carte; hl['bordercolor'] = bord; sous(hl, 'font')['color'] = texte;
  if (estObj(layout['polar'])) {
    const po = layout['polar'] as Obj;
    po['bgcolor'] = 'rgba(0,0,0,0)';
    for (const ax of ['radialaxis', 'angularaxis']) { const a = sous(po, ax); a['gridcolor'] ??= bord; a['linecolor'] ??= bord; }
  }
  if (estObj(layout['geo'])) {
    const g = layout['geo'] as Obj;
    g['bgcolor'] = 'rgba(0,0,0,0)';
    g['landcolor'] ??= jeton('--bg'); g['coastlinecolor'] ??= bord; g['countrycolor'] ??= bord;
    g['subunitcolor'] ??= bord; g['lakecolor'] ??= carte; g['showocean'] ??= false;
  }
  for (const t of data) {
    if (estObj(t['colorbar'])) sous(t['colorbar'] as Obj, 'tickfont')['color'] = mute;
    if (estObj(t['marker']) && estObj((t['marker'] as Obj)['colorbar'])) sous((t['marker'] as Obj)['colorbar'] as Obj, 'tickfont')['color'] = mute;
  }
  // Le titre est porté par la légende de figure (accessible) : pas en double dans le dessin.
  delete layout['title'];
  const marge = sous(layout, 'margin');
  marge['t'] ??= 24;
  return { data, layout };
}
