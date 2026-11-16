/** Formes des réponses d'API du chantier bt-potentiels (CONTRAT v3 : I1, I5, I5.b, I7.a, I7.b, I7.c). */

export type Niveau = 'N0' | 'N1' | 'N2';
export type Objectif = 'energie' | 'confort' | 'libre';
export type StatutPotentiel = 'en_attente' | 'en_cours' | 'fait' | 'erreur';
export type Indicateurs = Record<string, number | string | null>;

/** Paramètre (forme I1, reprise par I7.a). */
export interface Parametre {
  cle: string; libelle: string; unite: string;
  type: 'number' | 'int' | 'enum' | 'bool';
  min: number | null; max: number | null; pas: number | null;
  defaut: number | string | boolean | null;
  choix: Record<string, string> | null;
  groupe: string; aide: string; niveaux: Niveau[];
}
export type ValeurParametre = number | string | boolean | null;

// ── I7.a — catalogue des actions ──
export interface Famille { cle: string; libelle: string; }
export interface Condition { code: string; libelle: string; bloquante: boolean; }
export interface Action {
  cle: string; famille: string; libelle: string; description: string;
  indicateurs_pertinents: string[];
  parametres: Parametre[];
  conditions: Condition[];
  effets: string;
  cibles: string[];
  application: Partial<Record<Niveau, string>>;
  vigilance: string[];
  /** Action coupée par le parc (`?parc=`) : montrée, non ajoutable. */
  desactivee?: boolean;
}
export interface CatalogueActions { familles: Famille[]; actions: Action[]; }

// ── I7.b — éléments d'un bouquet ──
export interface ElementAction { action: string; parametres: Record<string, ValeurParametre>; }
export interface ElementUneParmi { une_parmi: ElementAction[]; }
export type ElementBouquet = ElementAction | ElementUneParmi;
export function estUneParmi(e: ElementBouquet): e is ElementUneParmi { return 'une_parmi' in e; }

// ── I7.c — bouquet servi ──
export interface Bouquet {
  id: number; nom: string; description: string; objectif: Objectif;
  elements: ElementBouquet[]; ordre: number;
  par_defaut: boolean; portfolio: number | null; modifiable: boolean; modifie_le: string;
}
export interface BouquetsParc { peut_ecrire: boolean; bouquets: Bouquet[]; }
export interface BouquetCorps { nom: string; description?: string; objectif: Objectif; elements: ElementBouquet[]; }
export type BouquetPatch = Partial<BouquetCorps & { ordre: number }>;

// ── I5 / I5.b — potentiels ──
export interface ActionRendue {
  cle: string; libelle: string; appliquee: boolean; raison: string | null;
  parametres: Record<string, ValeurParametre>; effets: string[];
  avertissements?: string[];
}
export interface Reel {
  niveau: Niveau; a_jour: boolean; indicateurs: Indicateurs | null;
  run_id: number | null; calcule_le: string | null;
}
export interface PotentielBatiment {
  bouquet: { id: number; nom: string; objectif: Objectif };
  niveau: Niveau; statut: StatutPotentiel | null; a_jour: boolean; calcule_le: string | null;
  scenario_id: number | null; run_id: number | null;
  indicateurs: Indicateurs | null;
  ecarts: Record<string, number>; ecarts_pct: Record<string, number>;
  actions: ActionRendue[];
  message?: string;
}
export interface PotentielsBatiment {
  peut_calculer: boolean; peut_calculer_n2: boolean;
  reel: Reel;
  bouquets: PotentielBatiment[];
}

export interface Agregat { somme: number | null; moyenne: number | null; mediane: number | null; n: number; }
export interface PotentielParcBatiment {
  statut: StatutPotentiel | null; a_jour: boolean; calcule_le: string | null;
  indicateurs: Indicateurs | null;
  ecarts: Record<string, number>; ecarts_pct: Record<string, number>;
  actions_appliquees: number; actions_ecartees: number;
}
export interface PotentielsParc {
  peut_calculer: boolean;
  file: { en_attente: number; en_cours: number };
  bouquets: { id: number; nom: string; objectif: Objectif }[];
  batiments: { id: number; nom: string; reel: Reel; potentiels: Record<string, PotentielParcBatiment> }[];
  agregats: Record<string, Record<string, Agregat>>;
}

// ── I1 v2 — catalogue des protections (seul ce que l'écran lit) ──
export interface CatalogueProtectionsV2 {
  version: 2;
  familles: Famille[];
  modeles: { cle: string; famille: string; libelle: string; description: string }[];
}

/** Libellés de l'enum `objectif` (I7.c). */
export const OBJECTIFS: { cle: Objectif; libelle: string }[] = [
  { cle: 'energie', libelle: 'Économiser l’énergie' },
  { cle: 'confort', libelle: 'Minimiser les DH' },
  { cle: 'libre', libelle: 'Libre' },
];
