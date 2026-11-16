/** Réglages d'un parc (contrat bt-navigation §2.3) — partagés par les onglets Paramétrage et Rendu. */

export type ModeleCalcul = 'N0' | 'N1' | 'N2';
export type ValeurReglage = number | boolean | string | string[] | null;

export interface EntreeReglage {
  cle: string; libelle: string; aide: string;
  type: 'number' | 'int' | 'bool' | 'enum' | 'text' | 'list';
  unite: string; min: number | null; max: number | null;
  choix: Record<string, string> | null;
  groupe: string; modeles: ModeleCalcul[]; applique: boolean; nullable?: boolean;
}

export interface Reglages {
  section: 'orchestration' | 'parametrage' | 'rendu';
  peut_modifier: boolean;
  schema: EntreeReglage[];
  defauts: Record<string, ValeurReglage>;
  surcharges: Record<string, ValeurReglage>;
  valeurs: Record<string, ValeurReglage>;
}

export const MODELES: { id: ModeleCalcul; libelle: string; phrase: string }[] = [
  { id: 'N0', libelle: 'Mensuel',
    phrase: 'Bilan des pertes et des apports mois par mois : rapide, il donne un ordre de grandeur du besoin de chauffage sur l’année.' },
  { id: 'N1', libelle: 'Horaire simplifié',
    phrase: 'Simulation heure par heure sur une année avec un bâtiment simplifié : besoin de chauffage avec sa fourchette, puissance maximale et heures de surchauffe.' },
  { id: 'N2', libelle: 'Détaillé par paroi',
    phrase: 'Chaque paroi est décrite couche par couche et chaque protection solaire est prise en compte : le plus précis, lancé bâtiment par bâtiment par un gestionnaire.' },
];

export function egal(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** Surcharges = seulement les valeurs qui diffèrent du défaut (D20 : tout reste réversible). */
export function diffDefauts(valeurs: Record<string, ValeurReglage>, defauts: Record<string, ValeurReglage>): Record<string, ValeurReglage> {
  const out: Record<string, ValeurReglage> = {};
  for (const [k, v] of Object.entries(valeurs)) if (k in defauts && !egal(v, defauts[k])) out[k] = v;
  return out;
}

/** Message lisible d'une erreur HTTP (404 = route pas encore servie). */
export function messageErreur(e: { status?: number; error?: { detail?: string } } | null | undefined, quoi: string): string {
  if (e?.status === 404) return `${quoi} : pas encore disponible sur le serveur (la route n’existe pas encore).`;
  if (e?.status === 403) return `${quoi} : vous n’avez pas le droit de faire cela.`;
  if (e?.status === 400 && e.error?.detail) return e.error.detail;
  return `${quoi} : le serveur n’a pas répondu correctement.`;
}
