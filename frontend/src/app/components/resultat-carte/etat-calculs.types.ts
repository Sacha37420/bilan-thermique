/**
 * État des calculs automatiques (chantier bt-trois-niveaux, contrat v1 — I2, recopié du contrat).
 *   GET  /api/batiments/<id>/calculs/etat/        → EtatCalculs
 *   POST /api/batiments/<id>/calculs/recalculer/  {} → 202 EtatCalculs (gestionnaire)
 *   GET  /api/parcs/<id>/calculs/etat/            → EtatParc
 */

export type Niveau = 'N0' | 'N1' | 'N2';
export const NIVEAUX: Niveau[] = ['N0', 'N1', 'N2'];

export type StatutRecalcul = 'inactif' | 'planifie' | 'en_cours' | 'erreur';
export type StatutCalcul = 'a_jour' | 'a_refaire' | 'en_file' | 'en_cours' | 'erreur' | 'desactive' | 'jamais';

export interface CalculEtat {
  /** « reel » ou l'identifiant d'un bouquet. */
  etat: 'reel' | number;
  libelle: string;
  niveau: Niveau;
  statut: StatutCalcul;
  run_id: number | null;
  calcule_le: string | null;
}

export interface EtatCalculs {
  statut: StatutRecalcul;
  cause: string;
  prevu_pour: string | null;
  message: string;
  peut_recalculer: boolean;
  calculs: CalculEtat[];
  calculs_restants: number;
}

export interface ResumeParc {
  batiments: number; a_jour: number; planifies: number; en_cours: number; erreurs: number; calculs_restants: number;
}
export interface EtatParc {
  resume: ResumeParc;
  batiments: { id: number; statut: StatutRecalcul; calculs_restants: number }[];
}

/** Suivi côté écran : `disponible` faux = la route répond 501 (pas encore livrée sur ce serveur). */
export interface SuiviEtat<T> {
  disponible: boolean | null;
  etat: T | null;
  erreur: string;
  /** Incrémenté chaque fois qu'un calcul se termine (les vues qui en dépendent se relisent). */
  avance: number;
  /** Relecture périodique en cours. */
  actif: boolean;
}

/** Libellés en mots des niveaux de calcul (vocabulaire de l'app : jamais le sigle seul). */
export const NIVEAU_MOTS: Record<Niveau, string> = {
  N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi',
};

export const STATUT_CALCUL_MOTS: Record<StatutCalcul, string> = {
  a_jour: 'à jour', a_refaire: 'à refaire', en_file: 'en file d’attente', en_cours: 'en cours', erreur: 'erreur',
  desactive: 'désactivé', jamais: 'pas encore calculé',
};
export const STATUT_CALCUL_AIDE: Record<StatutCalcul, string> = {
  a_jour: 'Calculé avec les données actuelles du bâtiment.',
  a_refaire: 'Les données ont changé depuis ce calcul ; il n’est pas encore prévu de le refaire.',
  en_file: 'Prévu : il sera refait de lui-même, après les calculs déjà en attente.',
  en_cours: 'Le calcul tourne en ce moment.',
  erreur: 'Le dernier essai a échoué.',
  desactive: 'Ce niveau de calcul est coupé dans les réglages du parc.',
  jamais: 'Aucun calcul de ce niveau pour l’instant.',
};
/** Classe de pastille (tokens Foyer `m-*` des badges). */
export const STATUT_CALCUL_CLASSE: Record<StatutCalcul, string> = {
  a_jour: 'm-mesure', a_refaire: 'm-infere', en_file: 'm-declare', en_cours: 'm-document', erreur: 'm-erreur',
  desactive: 'm-defaut', jamais: 'm-defaut',
};

/** Vrai si quelque chose est encore prévu ou tourne : la relecture périodique continue. */
export function etatActif(e: EtatCalculs | null): boolean {
  if (!e) return false;
  return e.statut === 'planifie' || e.statut === 'en_cours' || e.calculs.some(c => c.statut === 'en_file' || c.statut === 'en_cours');
}
export function parcActif(e: EtatParc | null): boolean {
  if (!e) return false;
  const r = e.resume;
  return r.planifies + r.en_cours > 0 || r.calculs_restants > 0;
}

/** Statut d'un calcul donné (réel ou bouquet, niveau) dans l'état ; null si l'état ne le cite pas. */
export function statutDe(e: EtatCalculs | null, etat: 'reel' | number, niveau: Niveau): CalculEtat | null {
  return e?.calculs.find(c => String(c.etat) === String(etat) && c.niveau === niveau) ?? null;
}

/** Heure lisible d'une date ISO (« 14 h 32 »), vide si absente ou invalide. */
export function heureDe(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}
