/** Types de la refonte (parc, fiche, affinage). */

export type Method = 'mesure' | 'document' | 'declare' | 'infere' | 'defaut';

export interface CatalogueKey {
  key: string; label: string; block: string; kind: 'number' | 'int' | 'enum' | 'bool';
  unit: string; min: number | null; max: number | null; choices: Record<string, string> | null;
  weight: number; help: string;
}

export interface Catalogue {
  keys: CatalogueKey[];
  blocks: [string, string][];
  methods: Method[];
  user_methods: Method[];
}

export interface Fact {
  id: number; key: string; value: unknown; unit: string; method: Method; source: string;
  confidence: number; created_at: string; scenario: number | null; author: string; mine: boolean;
  extract: { document_id?: number; page?: number; texte?: string };
  automatic?: { value: unknown; method: Method; source: string } | null;
  retired?: boolean; superseded?: boolean;
}

export interface Fiche {
  building: number; scenario: number | null; facts: Record<string, Fact>;
  confidence: number; completeness: number; fiche_hash: string;
}

export const METHOD_LABELS: Record<Method, string> = {
  mesure: 'mesuré', document: 'document', declare: 'déclaré', infere: 'inféré', defaut: 'défaut',
};

export interface PortfolioInfo {
  id: number; name: string; description: string; created_at: string;
  role: 'gestionnaire_parc' | 'gestionnaire_bat' | 'lecteur' | null;
  counts: { total: number; en_attente: number; en_cours: number; complet: number; incomplet: number; jamais: number };
}

export interface ParcRow {
  id: number; name: string; address: string; rnb_id: string; status: string; lat: number | null; lon: number | null;
  usage: string | null; surface_m2: number | null; annee: number | null;
  heating_kwh_m2: number | null; p10: number | null; p90: number | null; heating_kwh: number | null;
  heating_kw_max: number | null; hours_over_28: number | null; dpe: string | null; dpe_ep_kwh_m2: number | null;
  final_kwh_m2: number | null; primary_kwh_m2: number | null; model_label: string | null;
  confidence: number; completeness: number; level: string | null; computed_at: string | null; priority: number | null;
  errors: Record<string, string>;
}

export const USAGE_LABELS: Record<string, string> = {
  maison: 'Maison', logement_collectif: 'Collectif', bureau: 'Bureaux', enseignement: 'Enseignement',
  commerce: 'Commerce', sante: 'Santé', autre: 'Autre',
};
export const ROLE_LABELS: Record<string, string> = {
  gestionnaire_parc: 'Gestionnaire de parc', gestionnaire_bat: 'Gestionnaire de bâtiment', lecteur: 'Lecteur',
  gestionnaire: 'Gestionnaire du bâtiment', occupant: 'Occupant',
};
