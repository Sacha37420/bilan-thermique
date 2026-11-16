/** Résultats N2 par triangle (contrat bt-potentiels, I4 — producteur P) : `GET /api/calculs/<id>/triangles/`. */
export interface ScenePartriangle {
  repere: 'batiment' | string;
  vertices: [number, number, number][];
  triangles: [number, number, number][];
  groupes: string[];
  type: ('paroi' | 'vitrage' | 'hors_volume' | string)[];
  periodes: { hiver: number[]; ete: number[] };
  grandeurs: Partial<Record<GrandeurTriangle, (number | null)[]>> & Record<string, (number | null)[]>;
  unites: Record<string, string>;
}

export type GrandeurTriangle =
  | 'pertes_hiver_kwh_m2' | 'pertes_hiver_kwh' | 'gains_ete_kwh_m2' | 'gains_ete_kwh'
  | 'solaire_ete_kwh_m2' | 't_si_hiver_moy_c' | 't_si_ete_max_c';

/** Libellés écran des grandeurs du contrat I4 (noms figés par le contrat). */
export const GRANDEURS: { cle: GrandeurTriangle; libelle: string; echelle: 'sequentielle' | 'divergente' }[] = [
  { cle: 'pertes_hiver_kwh_m2', libelle: 'Pertes d’hiver par m²', echelle: 'sequentielle' },
  { cle: 'pertes_hiver_kwh', libelle: 'Pertes d’hiver (total du triangle)', echelle: 'sequentielle' },
  { cle: 'gains_ete_kwh_m2', libelle: 'Gains d’été par m²', echelle: 'sequentielle' },
  { cle: 'gains_ete_kwh', libelle: 'Gains d’été (total du triangle)', echelle: 'sequentielle' },
  { cle: 'solaire_ete_kwh_m2', libelle: 'Solaire absorbé en été par m²', echelle: 'sequentielle' },
  { cle: 't_si_hiver_moy_c', libelle: 'Température de surface intérieure, moyenne d’hiver', echelle: 'divergente' },
  { cle: 't_si_ete_max_c', libelle: 'Température de surface intérieure, maximum d’été', echelle: 'divergente' },
];

export const CAMERAS = ['iso', 'sud', 'nord', 'est', 'ouest', 'dessus'] as const;
export type Camera = typeof CAMERAS[number] | string;
