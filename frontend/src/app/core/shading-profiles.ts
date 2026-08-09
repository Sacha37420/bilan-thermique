import { ShadingProfileId } from './building.types';

/**
 * Catalogue de dispositifs d'occultation mobile (Lot J) — affichage seulement,
 * toujours considérés ENTIÈREMENT fermés quand actifs (pas de position
 * intermédiaire). Les valeurs physiques (résistance ajoutée, fraction
 * transmise) vivent uniquement côté backend
 * (building_solver.SHADING_PROFILES) — un seul point de vérité, ce fichier ne
 * fait que nommer les mêmes identifiants pour le sélecteur de la page
 * Bâtiment.
 */

export interface ShadingProfile {
  id: ShadingProfileId;
  label: string;
  description: string;
}

export const SHADING_PROFILES: ShadingProfile[] = [
  {
    id: 'volet-roulant',
    label: 'Volet roulant (PVC/alu, usuel)',
    description:
      "Quasi étanche à l'air (résistance ajoutée ≈ 0,20 m²·K/W) et totalement opaque — bloque tout le rayonnement solaire, direct comme diffus, quand fermé.",
  },
  {
    id: 'store-exterieur',
    label: 'Store extérieur (toile/brise-soleil orientable, usuel)',
    description:
      "Isole peu (écran, jeu d'air — résistance ajoutée ≈ 0,08 m²·K/W) mais réduit fortement le rayonnement direct même fermé ; laisse filtrer une partie du diffus (pas une paroi solide).",
  },
];
