/**
 * Calendrier d'occupation pour le mode thermostat (Lot V) — dérive t_min/t_max
 * par heure à partir d'un profil d'usage et d'un calendrier (jour de la
 * semaine du premier point météo, jours ouvrés, plages de vacances), puis les
 * fusionne dans la série météo au moment de la soumission (voir
 * calcul-3d.component.ts). Résolu entièrement côté client, comme les profils
 * de ventilation (Lot G) — le backend ne reçoit que deux nombres optionnels
 * par heure, jamais de date calendaire (voir building_solver.py, Lot V).
 *
 * Valeurs indicatives usuelles françaises (GTB/GTC, ADEME), pas une table
 * réglementaire officielle — même esprit que le catalogue de parois.
 */

export type UsageProfileId =
  | 'scolaire' | 'scolaire-clim' | 'tertiaire' | 'tertiaire-clim' | 'habitation' | 'habitation-clim';

export interface UsageProfile {
  id: UsageProfileId;
  label: string;
  description: string;
  // Vacances scolaires traitées comme "hors gel" — uniquement pertinent pour
  // les bâtiments scolaires (le tertiaire ignore le calendrier scolaire).
  vacancesHorsGel: boolean;
  // Week-end traité comme "hors gel" — vrai pour scolaire/tertiaire (climatisés
  // ou non), faux pour l'habitation (occupée en continu, pas de "fermeture").
  weekendHorsGel: boolean;
  // Plafond en journée normale : 100°C (pas de climatisation) ou la consigne
  // clim usuelle (bâtiments climatisés).
  tMaxJour: number;
}

// Paliers communs à tous les profils — seul tMaxJour (consigne clim) varie.
const HORS_GEL = { t_min: 7.0, t_max: 100.0 };
const REDUIT = { t_min: 16.0, t_max: 100.0 };
const T_MIN_JOUR = 19.0;
const T_MAX_CLIM = 26.0;

// Frontière jour/nuit — même convention que l'exemple de planning existant
// (Lot Q, loadPlanningExample) pour rester cohérent dans toute l'app.
const HEURE_DEBUT_JOUR = 7;
const HEURE_FIN_JOUR = 19;

export const USAGE_PROFILES: UsageProfile[] = [
  {
    id: 'scolaire',
    label: 'Scolaire',
    description: "Hors gel (7°C) pendant les vacances scolaires ET les week-ends, réduit (16°C) la nuit, consignes normales en journée (19°C, pas de climatisation).",
    vacancesHorsGel: true, weekendHorsGel: true, tMaxJour: 100.0,
  },
  {
    id: 'scolaire-clim',
    label: 'Scolaire climatisé',
    description: "Identique au scolaire, avec une climatisation en journée normale (consigne 26°C) — toujours hors gel (pas de clim) pendant vacances et week-ends.",
    vacancesHorsGel: true, weekendHorsGel: true, tMaxJour: T_MAX_CLIM,
  },
  {
    id: 'tertiaire',
    label: 'Tertiaire',
    description: "Hors gel (7°C) le week-end SEULEMENT (les vacances scolaires n'ont pas d'effet particulier), réduit (16°C) la nuit, consignes normales en journée (19°C, pas de climatisation).",
    vacancesHorsGel: false, weekendHorsGel: true, tMaxJour: 100.0,
  },
  {
    id: 'tertiaire-clim',
    label: 'Tertiaire climatisé',
    description: "Identique au tertiaire, avec une climatisation en journée normale (consigne 26°C) — hors gel le week-end seulement.",
    vacancesHorsGel: false, weekendHorsGel: true, tMaxJour: T_MAX_CLIM,
  },
  {
    id: 'habitation',
    label: 'Habitation',
    description: "Occupation continue (pas de notion de fermeture week-end/vacances) — seul le contraste jour/nuit compte : 19°C le jour, 16°C réduit la nuit, pas de climatisation.",
    vacancesHorsGel: false, weekendHorsGel: false, tMaxJour: 100.0,
  },
  {
    id: 'habitation-clim',
    label: 'Habitation climatisée',
    description: "Identique à l'habitation, avec une climatisation en journée (consigne 26°C) — pas de climatisation nocturne (fenêtres/ventilation naturelle supposées suffire).",
    vacancesHorsGel: false, weekendHorsGel: false, tMaxJour: T_MAX_CLIM,
  },
];

function setpointsFor(profile: UsageProfile, isWeekend: boolean, isVacation: boolean, hourOfDay: number): { t_min: number; t_max: number } {
  if (profile.vacancesHorsGel && isVacation) return HORS_GEL;
  if (profile.weekendHorsGel && isWeekend) return HORS_GEL;
  if (hourOfDay >= HEURE_DEBUT_JOUR && hourOfDay < HEURE_FIN_JOUR) {
    return { t_min: T_MIN_JOUR, t_max: profile.tMaxJour };
  }
  return REDUIT;
}

export interface OccupationCalendar {
  jourDebut: number; // 0=lundi..6=dimanche — jour du PREMIER point météo
  joursOuvres: boolean[]; // longueur 7 (index 0=lundi..6=dimanche), true = jour ouvré
  // Plages de vacances en INDICE DE JOUR relatif au début du run (jour 0 =
  // premier jour simulé), bornes incluses — pas de vraie date calendaire.
  vacances: { debut: number; fin: number }[];
}

export function defaultOccupationCalendar(): OccupationCalendar {
  return {
    jourDebut: 0, // lundi par défaut
    joursOuvres: [true, true, true, true, true, false, false], // lun-ven ouvrés, sam-dim week-end
    vacances: [],
  };
}

/** Une entrée {t_min, t_max} par heure de la série météo (même longueur que
 * `absoluteHours`), à fusionner dans chaque point météo avant soumission.
 *
 * `absoluteHours` (Lot AB1) : heure absolue de CHAQUE point — `hour_index` du
 * point météo quand il vient du fetch (dérivé de son horodatage réel), sinon
 * `heureDebut + position`. Ce paramètre remplace l'ancien couple
 * (heureDebut, nHours), qui reconstruisait l'heure à partir de la POSITION dans
 * la liste : le fetch saute les heures à donnée manquante, donc dès le premier
 * trou la position ne correspondait plus à l'heure réelle et tout le calendrier
 * (jour/nuit, week-end, vacances) dérivait pour le reste du run — silencieusement,
 * `n_missing` n'apparaissant que dans le message du job. */
export function computeThermostatSetpoints(
  profile: UsageProfile, calendar: OccupationCalendar, absoluteHours: number[],
): { t_min: number; t_max: number }[] {
  return absoluteHours.map(absoluteHour => {
    const hourOfDay = ((absoluteHour % 24) + 24) % 24;
    const dayIdx = Math.floor(absoluteHour / 24);
    const weekday = (((calendar.jourDebut + dayIdx) % 7) + 7) % 7;
    const isWeekend = !calendar.joursOuvres[weekday];
    const isVacation = calendar.vacances.some(r => dayIdx >= r.debut && dayIdx <= r.fin);
    return setpointsFor(profile, isWeekend, isVacation, hourOfDay);
  });
}
