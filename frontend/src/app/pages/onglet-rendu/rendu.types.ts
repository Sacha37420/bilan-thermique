/** Onglet Rendu : fiche d'identité (contrat bt-navigation §2.6) et libellés du rendu de parc (§2.3). */
import { Approfondissement, Method } from '../../core/refonte.types';

export interface FaitIdentite { valeur: unknown; methode: Method | string; libelle: string; unite: string; }
export type BlocFaits = Record<string, FaitIdentite>;
export type Indicateurs = Record<string, unknown> & { computed_at?: string; a_jour?: boolean };

export interface FicheIdentite {
  batiment: { id: number; name: string; address: string; rnb_id: string; lat: number | null; lon: number | null; portfolio: number | null };
  identite: BlocFaits;
  enveloppe: BlocFaits | Record<string, BlocFaits>;
  systemes: BlocFaits | Record<string, BlocFaits>;
  meteo: { jeu: string; type: string } | null;
  environnement: { id: number; objets: number } | null;
  resultats: Partial<Record<'N0' | 'N1' | 'N2', Indicateurs | null>>;
  ameliorations: { id: number; name: string; bouquets: unknown[]; dernier: Indicateurs | null }[];
  confiance: number; completude: number;
  approfondissement?: Approfondissement;
  sources: { etape: string; ok: boolean; source: string; date: string | null }[];
}

/** Repli si les réglages de rendu ne sont pas lisibles (hors parc, route absente) — mêmes défauts que le serveur. */
export const RENDU_DEFAUTS: Record<string, unknown> = {
  'parc.titre': '', 'parc.indicateur': 'heating_kwh_m2',
  'parc.colonnes': ['usage', 'surface_m2', 'heating_kwh_m2', 'confidence', 'approfondissement', 'priority'],
  'parc.tri': 'name', 'batiment.titre': '',
  'batiment.sections': ['identite', 'geometrie', 'enveloppe', 'systemes', 'meteo', 'environnement', 'resultats', 'ameliorations', 'sources'],
  'batiment.niveau_prefere': 'N1',
};

export const COLONNES: Record<string, string> = {
  heating_kwh_m2: 'Besoin de chauffage (kWh/m²·an)', heating_kwh: 'Besoin de chauffage (kWh/an)',
  heating_kw_max: 'Puissance maximale (kW)', hours_over_28: 'Heures au-dessus de 28 °C',
  final_kwh_m2: 'Énergie finale (kWh/m²·an)', primary_kwh_m2: 'Énergie primaire (kWh/m²·an)',
  confidence: 'Confiance', completeness: 'Complétude', priority: 'Priorité',
  surface_m2: 'Surface', annee: 'Année', usage: 'Usage', dpe: 'DPE affiché',
  model_label: 'Étiquette du modèle', approfondissement: 'Approfondissement', status: 'État',
};
export const INDICATEURS = ['heating_kwh_m2', 'heating_kwh', 'heating_kw_max', 'hours_over_28', 'final_kwh_m2',
  'primary_kwh_m2', 'confidence', 'completeness', 'priority'];
/** Indicateurs qui s'additionnent d'un bâtiment à l'autre (les ratios au m² et les scores, non). */
export const ADDITIFS = new Set(['heating_kwh', 'heating_kw_max', 'surface_m2']);
/** Indicateurs pour lesquels « plus haut » est mieux (échelle de couleur inversée). */
export const HAUT_EST_BIEN = new Set(['confidence', 'completeness']);
export const TRIS: Record<string, string> = { name: 'Nom', ...Object.fromEntries(INDICATEURS.map(k => [k, COLONNES[k]])) };

export const SECTIONS: Record<string, string> = {
  identite: 'Identité', geometrie: 'Géométrie', enveloppe: 'Enveloppe', systemes: 'Systèmes', meteo: 'Météo',
  environnement: 'Environnement', resultats: 'Résultats', ameliorations: 'Améliorations', sources: 'Sources',
};
export const NIVEAUX: Record<'N0' | 'N1' | 'N2', string> = { N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi' };
