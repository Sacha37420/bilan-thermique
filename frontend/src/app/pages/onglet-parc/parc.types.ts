/** Types propres à l'onglet Parc (chantier bt-navigation). */

export type VueParc = 'batiments' | 'ajouter' | 'import-partage' | 'orchestrateur';
export const VUES_PARC: [VueParc, string][] = [
  ['batiments', 'Bâtiments'], ['ajouter', 'Ajouter des bâtiments'],
  ['import-partage', 'Import et partage'], ['orchestrateur', 'Sources des données'],
];

export interface Members {
  members: { id: number; name: string; role: string; me: boolean }[];
  accesses: { id: number; name: string; role: string; building_id: number; building: string }[];
  invitations: { id: number; prefix: string; role: string; building_id: number | null; uses: number; max_uses: number; active: boolean }[];
}

/** Un rôle = un nom court + une phrase qui dit ce qu'il permet. `gestionnaire_bat` (membre du parc sans
 * bâtiment confié) n'est plus proposé à l'invitation : il doublait « gestionnaire d'un bâtiment ». */
export interface RoleInfo { id: string; nom: string; phrase: string; surBatiment: boolean; }
export const ROLES: RoleInfo[] = [
  { id: 'gestionnaire_parc', nom: 'Gestionnaire du parc', surBatiment: false,
    phrase: 'Voit et modifie tous les bâtiments, ajoute ou retire des bâtiments, règle les sources et partage le parc.' },
  { id: 'lecteur', nom: 'Lecteur du parc', surBatiment: false,
    phrase: 'Voit tous les bâtiments et leurs résultats, sans rien pouvoir modifier.' },
  { id: 'gestionnaire', nom: 'Responsable d’un bâtiment', surBatiment: true,
    phrase: 'Ne voit que le bâtiment choisi ; le complète, lance ses calculs, y compris le calcul détaillé, et peut le partager.' },
  { id: 'occupant', nom: 'Occupant d’un bâtiment', surBatiment: true,
    phrase: 'Ne voit que le bâtiment choisi ; peut compléter ses informations et lancer les calculs simples.' },
];
export const ROLE_PHRASE: Record<string, { nom: string; phrase: string }> = {
  ...Object.fromEntries(ROLES.map(r => [r.id, { nom: r.nom, phrase: r.phrase }])),
  gestionnaire_bat: { nom: 'Membre sans bâtiment', phrase: 'Membre du parc qui ne voit que les bâtiments qui lui sont confiés un par un.' },
};

export interface InitJob { id: number; status: string; progress: number; message: string; }

/** Réglages de parc (contrat § 2.3). */
export interface ReglageEntree {
  cle: string; libelle: string; aide: string; type: 'number' | 'int' | 'bool' | 'enum' | 'text' | 'list';
  unite: string; min: number | null; max: number | null; choix: Record<string, string> | null;
  groupe: string; modeles: ('N0' | 'N1' | 'N2')[]; applique: boolean; nullable?: boolean;
}
export interface Reglages {
  section: string; peut_modifier: boolean; schema: ReglageEntree[];
  defauts: Record<string, unknown>; surcharges: Record<string, unknown>; valeurs: Record<string, unknown>;
}

/** Source d'un groupe d'orchestration, en mots (le schéma ne la porte pas). */
export const SOURCE_DU_GROUPE: Record<string, string> = {
  'Identification': 'Base Adresse Nationale, Référentiel National des Bâtiments (RNB)',
  'Fiche nationale': 'BDNB (CSTB) et DPE de l’ADEME',
  'Géométrie': 'BD TOPO (IGN)',
  'Compositions': 'Archétypes TABULA-FR',
  'Météo': 'PVGIS (année type) ou Open-Meteo (année réelle)',
  'Calcul automatique': 'Moteurs de calcul de l’app',
  'Environnement': 'LiDAR HD, BD TOPO, orthophotos et CoSIA (IGN)',
  'Façades': 'Photos de rue Panoramax',
};

export const MODELE_MOTS: Record<string, string> = { N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi' };

/** Étapes de la chaîne automatique (compte rendu `Building.pipeline.steps`). */
export const ETAPES: Record<string, string> = {
  A1: 'Identification (adresse, RNB)', A2: 'Fiche nationale BDNB et DPE', A3: 'Géométrie, mitoyenneté, masques (BD TOPO)',
  A5: 'Compositions par archétype', A6: 'Environnement observé et géométrie croisée (LiDAR HD)', A7: 'Météo par défaut',
  A8: 'Calculs automatiques N0 / N1',
};

export interface BatimentPipeline {
  id: number; name: string; pipeline_status: string;
  pipeline: { steps?: Record<string, { ok: boolean; error?: string; skipped?: boolean; message?: string }>;
              sources?: string[]; date?: string } | null;
}
