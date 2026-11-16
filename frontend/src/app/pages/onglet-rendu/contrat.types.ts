/**
 * Formes des réponses consommées par le secteur F (chantier bt-potentiels, contrat v3), recopiées du contrat —
 * aucun import depuis un dossier d'un autre secteur.
 *   I5 (A) potentiels · I6 (R) document, figures, exports, modèles · I7 (A) bouquets.
 */
import { FigureRendu } from '../../components/graphe/graphe.types';
import { ScenePartriangle } from '../../components/vue3d-resultats/vue3d.types';

export type Indicateurs3 = Record<string, number | string | null>;
export type StatutPotentiel = 'en_attente' | 'en_cours' | 'fait' | 'erreur' | null;

// ════════ I5 — potentiels ════════
export interface ActionRendue {
  cle: string; libelle: string; appliquee: boolean; raison: string | null;
  parametres: Record<string, unknown>; effets: string[]; avertissements?: string[];
}
export interface ReelPotentiel {
  niveau: string; a_jour: boolean; indicateurs: Indicateurs3 | null; run_id?: number | null; calcule_le?: string | null;
}
export interface PotentielBatiment {
  bouquet: { id: number; nom: string; objectif: string };
  niveau: 'N0' | 'N1' | 'N2'; statut: StatutPotentiel; a_jour: boolean; calcule_le: string | null; scenario_id: number | null;
  indicateurs: Indicateurs3 | null; ecarts: Record<string, number>; ecarts_pct?: Record<string, number>;
  actions: ActionRendue[]; message?: string; run_id?: number | null;
}
export interface PotentielsBatiment {
  peut_calculer: boolean; peut_calculer_n2: boolean;
  reel: ReelPotentiel; bouquets: PotentielBatiment[];
}
export interface Agregat { somme: number | null; moyenne: number | null; mediane: number | null; n: number }
export interface PotentielParcCellule {
  statut: StatutPotentiel; a_jour: boolean; calcule_le: string | null; indicateurs: Indicateurs3 | null;
  ecarts: Record<string, number>; ecarts_pct?: Record<string, number>; actions_appliquees: number; actions_ecartees: number;
}
export interface PotentielsParc {
  /** bt-trois-niveaux I3 : niveau des potentiels et agrégats rendus (absent sur un serveur antérieur : N1). */
  niveau?: 'N0' | 'N1' | 'N2';
  peut_calculer: boolean; file: { en_attente: number; en_cours: number };
  bouquets: { id: number; nom: string; objectif: string }[];
  batiments: { id: number; nom: string; reel: ReelPotentiel; potentiels: Record<string, PotentielParcCellule> }[];
  agregats: Record<string, Record<string, Agregat>>;
}

// ════════ I7 — bouquets ════════
export interface Bouquet {
  id: number; nom: string; description: string; objectif: 'energie' | 'confort' | 'libre'; elements: unknown[];
  ordre: number; par_defaut: boolean; portfolio: number | null; modifiable: boolean; modifie_le: string;
}
export interface BouquetsParc { peut_ecrire: boolean; bouquets: Bouquet[] }

// ════════ I6 — document de rendu ════════
export type Portee = 'parc' | 'batiment';
export type Series = 'reel' | 'bouquets' | 'reel_et_bouquets' | number[];
export type FormeGraphe = 'barres' | 'barres_empilees' | 'barres_groupees' | 'courbes' | 'aires' | 'nuage' | 'bulles' | 'boite'
  | 'violon' | 'histogramme' | 'camembert' | 'radar' | 'carte_chaleur' | 'cascade' | 'parallele';
export const FORMES: { cle: FormeGraphe; libelle: string }[] = [
  { cle: 'barres', libelle: 'Barres' }, { cle: 'barres_groupees', libelle: 'Barres groupées' },
  { cle: 'barres_empilees', libelle: 'Barres empilées' }, { cle: 'courbes', libelle: 'Courbes' },
  { cle: 'aires', libelle: 'Aires' }, { cle: 'nuage', libelle: 'Nuage de points' }, { cle: 'bulles', libelle: 'Bulles' },
  { cle: 'boite', libelle: 'Boîte à moustaches' }, { cle: 'violon', libelle: 'Violon' }, { cle: 'histogramme', libelle: 'Histogramme' },
  { cle: 'camembert', libelle: 'Camembert' }, { cle: 'radar', libelle: 'Radar' }, { cle: 'carte_chaleur', libelle: 'Carte de chaleur' },
  { cle: 'cascade', libelle: 'Cascade' }, { cle: 'parallele', libelle: 'Coordonnées parallèles' },
];

export interface Graphe {
  forme: FormeGraphe; x: string | null; y: string[]; couleur: string | null; taille: string | null; facettes: string | null;
  series: Series; filtre: { usage?: string[]; periode?: [number | null, number | null]; niveau?: ('N0' | 'N1' | 'N2')[]; [k: string]: unknown };
  agregation: 'somme' | 'moyenne' | 'mediane' | 'aucune'; titre: string; axe_y_log: boolean;
}

/** Simulations affichées par un bloc (lot 45) : absent ou vide = la plus fine à jour. */
export type NiveauSim = 'N0' | 'N1' | 'N2';

export type Bloc =
  | { type: 'texte'; contenu: string }
  | { type: 'indicateurs'; cles: string[]; series: Series; niveaux?: NiveauSim[] }
  | { type: 'tableau'; source: SourceTableau; colonnes: string[]; tri: string; ordre: 'asc' | 'desc'; limite: number; niveaux?: NiveauSim[] }
  | { type: 'graphe'; graphe: Graphe }
  | { type: 'carte'; indicateur: string; series: string | number; niveaux?: NiveauSim[] }
  | { type: 'vue3d'; calcul: string; grandeur: string; camera: string }
  | { type: 'fiche'; sections: string[] }
  | { type: 'saut_de_page' };
export type TypeBloc = Bloc['type'];
export type SourceTableau = 'batiments' | 'potentiels' | 'actions' | 'faits' | 'calculs';
export const SOURCES: { cle: SourceTableau; libelle: string }[] = [
  { cle: 'batiments', libelle: 'Bâtiments' }, { cle: 'potentiels', libelle: 'Potentiels' }, { cle: 'actions', libelle: 'Actions' },
  { cle: 'faits', libelle: 'Données (faits)' }, { cle: 'calculs', libelle: 'Calculs' },
];
export const SECTIONS_FICHE: { cle: string; libelle: string }[] = [
  { cle: 'identite', libelle: 'Identité' }, { cle: 'geometrie', libelle: 'Géométrie' }, { cle: 'enveloppe', libelle: 'Enveloppe' },
  { cle: 'systemes', libelle: 'Systèmes' }, { cle: 'meteo', libelle: 'Météo' }, { cle: 'environnement', libelle: 'Environnement' },
  { cle: 'resultats', libelle: 'Résultats' }, { cle: 'potentiels', libelle: 'Potentiels par bouquet' },
  { cle: 'ameliorations', libelle: 'Améliorations' }, { cle: 'sources', libelle: 'Sources' },
];
export const TYPES_BLOC: { cle: TypeBloc; libelle: string }[] = [
  { cle: 'texte', libelle: 'Texte' }, { cle: 'indicateurs', libelle: 'Chiffres clés' }, { cle: 'tableau', libelle: 'Tableau' },
  { cle: 'graphe', libelle: 'Graphe' }, { cle: 'carte', libelle: 'Carte' }, { cle: 'vue3d', libelle: 'Vue 3D (N2)' },
  { cle: 'fiche', libelle: 'Fiche d’identité' }, { cle: 'saut_de_page', libelle: 'Saut de page' },
];

export interface Spec {
  version: 1; portee: Portee; titre: string;
  mise_en_page: { format: 'A4' | 'A3'; orientation: 'portrait' | 'paysage'; sommaire: boolean; numeros: boolean };
  sections: { titre: string; blocs: Bloc[] }[];
}

export interface Dimension { cle: string; libelle: string; unite: string | null; portees: Portee[]; type?: 'categorie' | 'ordonnee' | 'temps'; formes?: FormeGraphe[] }
export interface Mesure { cle: string; libelle: string; unite: string; portees: Portee[]; famille?: string; formes?: FormeGraphe[] }

export interface Modele { id: number; nom: string; portee: Portee; spec: Spec; par_defaut: boolean; portfolio: number | null; modifiable: boolean }
export interface Question { cle: string; question: string; portees: Portee[]; graphe: Graphe }
export interface Gabarits { modeles: Modele[]; questions: Question[] }
export interface ModelesParc { peut_ecrire: boolean; modeles: Modele[] }

export type Cible = { parc: number } | { batiment: number };

// ── Document résolu (I6.g) ──
export interface LigneFiche { libelle: string; valeur: string | number | boolean | null; unite: string; source: string }
export type BlocResolu = { type: TypeBloc; indisponible?: string } & {
  html?: string;
  colonnes?: ({ serie: string; libelle: string } | { cle: string; libelle: string; unite: string })[];
  lignes?: unknown[];
  total_lignes?: number;
  figure?: FigureRendu;
  calcul_id?: number; grandeur?: string; libelle?: string; unite?: string; camera?: string; scene?: ScenePartriangle;
  sections?: { cle: string; titre: string; elements: LigneFiche[] }[];
  potentiels?: PotentielsBatiment;
};
export interface LigneIndicateur { cle: string; libelle: string; unite: string; valeurs: (number | null)[]; ecarts: (number | null)[] }
export interface DocumentResolu {
  titre: string; mise_en_page: Spec['mise_en_page'];
  sections: { titre: string; ancre: string; blocs: BlocResolu[] }[];
}

export type StatutExport = 'en_attente' | 'en_cours' | 'fait' | 'erreur';
export interface ExportRendu {
  id: number; format: 'html' | 'docx' | 'pdf'; statut: StatutExport; message?: string; taille?: number;
  nom_fichier?: string; cree_le: string; expire_le?: string;
}

export function grapheVide(): Graphe {
  return { forme: 'barres', x: null, y: [], couleur: null, taille: null, facettes: null, series: 'reel', filtre: {},
           agregation: 'moyenne', titre: '', axe_y_log: false };
}
export function specVide(portee: Portee): Spec {
  return { version: 1, portee, titre: '', mise_en_page: { format: 'A4', orientation: 'portrait', sommaire: true, numeros: true },
           sections: [{ titre: 'Section 1', blocs: [] }] };
}
export function blocNeuf(t: TypeBloc): Bloc {
  switch (t) {
    case 'texte': return { type: 'texte', contenu: '' };
    case 'indicateurs': return { type: 'indicateurs', cles: [], series: 'reel_et_bouquets' };
    case 'tableau': return { type: 'tableau', source: 'batiments', colonnes: [], tri: '', ordre: 'desc', limite: 50 };
    case 'graphe': return { type: 'graphe', graphe: grapheVide() };
    case 'carte': return { type: 'carte', indicateur: '', series: 'reel' };
    case 'vue3d': return { type: 'vue3d', calcul: 'dernier_N2', grandeur: 'pertes_hiver_kwh_m2', camera: 'iso' };
    case 'fiche': return { type: 'fiche', sections: SECTIONS_FICHE.map(s => s.cle) };
    default: return { type: 'saut_de_page' };
  }
}
