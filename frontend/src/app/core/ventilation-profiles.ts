/**
 * Profils de ventilation typiques par génération de bâtiment — ordres de grandeur
 * pédagogiques (pas une table réglementaire officielle), même esprit que les valeurs
 * "U indicatif" du catalogue de parois (backend/api/management/commands/seed_paroi_catalogue.py) :
 * à ajuster projet par projet, pas à prendre comme référence certifiée.
 *
 * N'a de sens que côté 3D (Calcul 3D) : le taux de renouvellement (vol/h) se combine
 * au volume RÉEL du bâtiment pour obtenir un débit (m³/h) — le solveur 1D ne modélise
 * qu'une paroi notionnelle de 1 m², sans volume de bâtiment à multiplier.
 */

export interface VentilationProfile {
  id: string;
  label: string;
  description: string;
  tauxRenouvellementVolH: number;
  etaRecup: number;
}

export const VENTILATION_PROFILES: VentilationProfile[] = [
  {
    id: 'avant-1948',
    label: 'Avant 1948 — bâti ancien, sans VMC',
    description:
      "Renouvellement d'air par infiltrations parasites uniquement (pas d'étanchéité recherchée à l'époque). Valeur indicative, très dépendante de l'état réel de l'enveloppe.",
    tauxRenouvellementVolH: 1.2,
    etaRecup: 0,
  },
  {
    id: '1948-1974',
    label: '1948-1974 — avant la première réglementation thermique',
    description: "Ventilation naturelle par grilles/conduits, sans extraction mécanique.",
    tauxRenouvellementVolH: 0.8,
    etaRecup: 0,
  },
  {
    id: '1974-2000',
    label: '1974-2000 — RT1974/1982/1988, VMC simple flux autoréglable',
    description:
      "VMC simple flux généralisée après l'arrêté du 24/03/1982 — débits fixes, non modulés selon l'occupation.",
    tauxRenouvellementVolH: 0.6,
    etaRecup: 0,
  },
  {
    id: '2000-2012',
    label: '2000-2012 — RT2005/2012, VMC simple flux hygroréglable',
    description:
      "Débits modulés selon l'humidité/l'occupation (type A ou B de VMC hygroréglable) — valeur moyenne indicative.",
    tauxRenouvellementVolH: 0.5,
    etaRecup: 0,
  },
  {
    id: 're2020',
    label: 'RE2020 — VMC double flux avec récupération de chaleur',
    description:
      "Débit nominal proche de la génération précédente, mais pertes nettes fortement réduites par la récupération de chaleur sur l'air extrait.",
    tauxRenouvellementVolH: 0.5,
    etaRecup: 0.75,
  },
];
