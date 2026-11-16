/** Catalogue des simulations (contrat bt-navigation §2.4) et repli statique si l'API ne le sert pas encore. */

export type TypeSimulation = 'N0' | 'N1' | 'N2' | '1D' | '3D';
export type VueCalculs = 'catalogue' | 'simulation' | 'ameliorations' | 'historique';

export const VUES_CALCULS: { id: VueCalculs; label: string; avecBatiment: boolean }[] = [
  { id: 'catalogue', label: 'Les simulations', avecBatiment: false },
  { id: 'simulation', label: 'Lancer et voir le résultat', avecBatiment: true },
  { id: 'ameliorations', label: 'Améliorations', avecBatiment: true },
  { id: 'historique', label: 'Historique', avecBatiment: true },
];

export interface Moteur {
  id: TypeSimulation; libelle: string; resume: string; methode: string; hypotheses: string[];
  entrees: string[]; sorties: string[]; duree: string; qui: string; limites: string[]; version: string;
  expert_route: string | null;
}

/** « Quand l'utiliser » : conseil d'écran, absent du contrat §2.4 (texte du front). */
export const QUAND: Record<TypeSimulation, string> = {
  N0: 'Pour un premier ordre de grandeur, ou pour contrôler un autre calcul.',
  N1: 'Le calcul de référence : comparer des bâtiments, chiffrer une amélioration, avec une fourchette.',
  N2: 'Quand l’ombre des voisins, la forme du bâtiment ou une paroi particulière comptent ; avant une décision importante.',
  '1D': 'Pour comprendre une seule paroi (mur, toiture) : températures dans l’épaisseur, déphasage, condensation.',
  '3D': 'Pour un expert : réglages avancés du calcul détaillé (planning, régime libre ou imposé, vent).',
};

/** Repli minimal si `GET calculs/moteurs/` n'existe pas encore (backend écrit en parallèle). */
export const MOTEURS_REPLI: Moteur[] = [
  { id: 'N0', libelle: 'Mensuel', resume: 'Bilan mois par mois des pertes et des apports.', methode: 'Bilan mensuel de type 3CL.',
    hypotheses: [], entrees: ['Données du bâtiment', 'Météo choisie'], sorties: ['Besoin de chauffage par mois et par an'],
    duree: 'moins d’une seconde', qui: 'Tout membre qui voit le bâtiment', limites: ['Pas de confort d’été'], version: '', expert_route: null },
  { id: 'N1', libelle: 'Horaire simplifié', resume: 'Le bâtiment heure par heure, avec une fourchette.', methode: 'Modèle à une zone, heure par heure (5R1C).',
    hypotheses: [], entrees: ['Données du bâtiment', 'Météo choisie'], sorties: ['Besoin de chauffage avec fourchette', 'Puissance maximale', 'Heures trop chaudes'],
    duree: 'une à deux secondes', qui: 'Tout membre qui voit le bâtiment', limites: [], version: '', expert_route: null },
  { id: 'N2', libelle: 'Détaillé par paroi', resume: 'Chaque paroi simulée en 3D, avec l’ombre réelle des voisins.', methode: 'Éléments finis par triangle de l’enveloppe.',
    hypotheses: [], entrees: ['Maillage 3D', 'Parois', 'Environnement', 'Météo choisie'], sorties: ['Besoin de chauffage', 'Puissance maximale', 'Heures trop chaudes'],
    duree: 'quelques secondes à quelques minutes', qui: 'Gestionnaires du bâtiment, un bâtiment à la fois', limites: [], version: '', expert_route: null },
  { id: '1D', libelle: 'Une paroi en détail', resume: 'Températures dans l’épaisseur d’une paroi, heure par heure.', methode: 'Éléments finis 1D dans une paroi multicouche.',
    hypotheses: [], entrees: ['Couches de la paroi', 'Météo'], sorties: ['Températures dans la paroi', 'Flux'],
    duree: 'quelques secondes', qui: 'Tout membre', limites: ['Une seule paroi, pas le bâtiment'], version: '', expert_route: '/calcul-1d' },
  { id: '3D', libelle: 'Calcul 3D avancé', resume: 'Le calcul détaillé avec tous ses réglages.', methode: 'Éléments finis par triangle, couplés au nœud d’air.',
    hypotheses: [], entrees: ['Maillage 3D', 'Réglages du formulaire'], sorties: ['Séries horaires complètes'],
    duree: 'quelques minutes', qui: 'Experts', limites: [], version: '', expert_route: '/calcul-3d' },
];
