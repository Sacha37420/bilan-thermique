/** Types propres à l'onglet Bâtiment (chantier bt-navigation). */

export type VueBatiment = 'synthese' | 'donnees' | 'geometrie' | 'physique' | 'meteo' | 'environnement' | 'mesures';

export const VUES_BATIMENT: { id: VueBatiment; label: string }[] = [
  { id: 'synthese', label: 'Synthèse' },
  { id: 'donnees', label: 'Données et provenance' },
  { id: 'geometrie', label: 'Géométrie' },
  { id: 'physique', label: 'Enveloppe et systèmes' },
  { id: 'meteo', label: 'Météo' },
  { id: 'environnement', label: 'Environnement et façades' },
  { id: 'mesures', label: 'Factures et mesures' },
];

/** Blocs du catalogue montrés dans la vue « Enveloppe et systèmes » (les systèmes ont leur carte). */
export const BLOCS_PHYSIQUE = ['enveloppe', 'menuiseries', 'ventilation'];

export interface BuildingInfo {
  id: number; name: string; address: string; rnb_id: string; pipeline_status: string;
  georef_lat: number | null; georef_lon: number | null; surface_ref_m2: number | null; environment_id: number | null;
  pipeline: { steps?: Record<string, { ok: boolean; error?: string }>; horizon_deg?: Record<string, number>;
    geometry?: { emprise_m2: number; mitoyennete: number }; sources?: string[] };
}

export interface ParoiModelSummary { id: number; name: string; is_glazing: boolean }

export const STEP_LABELS: Record<string, string> = {
  A1: 'Identification (BAN, RNB)', A2: 'Fiche nationale BDNB et DPE existant',
  A3: 'Géométrie, mitoyenneté, masques (BD TOPO)', A5: 'Compositions par archétype (TABULA)',
  A6: 'Environnement observé et géométrie croisée (LiDAR HD × BD TOPO)', A7: 'Météo par défaut', A8: 'Calculs automatiques N0 / N1',
};
