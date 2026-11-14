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
