/**
 * Figure Plotly telle que le serveur la construit (contrat bt-potentiels v2, I6.b / I6.g).
 * Le navigateur n'assemble aucune donnée : il pose seulement les couleurs du thème (palette.ts).
 */
export type PaletteRendu = 'categorielle' | 'sequentielle' | 'divergente';

export interface FigureRendu {
  data: Record<string, unknown>[];
  layout: Record<string, unknown> & { meta?: { palette?: PaletteRendu; resume?: string; [k: string]: unknown } };
  /** true : aucune donnée ; `layout.meta.resume` dit pourquoi. */
  vide?: boolean;
}

/** Titre d'une figure (`layout.title` en chaîne ou en `{text}`), sans balises. */
export function titreDe(f: FigureRendu | null | undefined): string {
  const t = f?.layout?.['title'] as string | { text?: string } | undefined;
  const s = typeof t === 'string' ? t : t?.text ?? '';
  return s.replace(/<[^>]*>/g, '').trim();
}

export function resumeDe(f: FigureRendu | null | undefined): string {
  const r = f?.layout?.meta?.resume;
  return typeof r === 'string' ? r : '';
}
