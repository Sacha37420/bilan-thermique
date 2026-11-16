import { inject } from '@angular/core';
import { Router, Routes } from '@angular/router';

/**
 * Navigation en six onglets (refonte du 2026-11-16) : Parc, Bâtiment, Calculs, Paramétrage, IA,
 * Rendu. Le parc et le bâtiment courants sont partagés (`ContexteService`, `?p=&b=`). Les outils
 * experts (maillage, environnement, calculs 1D/3D, parois, théorie) restent joignables depuis les
 * onglets, hors du menu.
 */

/** Ancienne fiche (`/fiche?b=&onglet=`) → onglet qui reprend la même section. */
const FICHE_VERS: Record<string, [string, string]> = {
  synthese: ['/batiment', 'synthese'], donnees: ['/batiment', 'donnees'],
  geometrie: ['/batiment', 'geometrie'], maillage: ['/batiment', 'geometrie'],
  protections: ['/batiment', 'physique'], systemes: ['/batiment', 'physique'],
  meteo: ['/batiment', 'meteo'], environnement: ['/batiment', 'environnement'], mesures: ['/batiment', 'mesures'],
  simulation: ['/calculs', 'simulation'], scenarios: ['/calculs', 'ameliorations'], calculs: ['/calculs', 'historique'],
  documents: ['/ia', ''],
};

export const routes: Routes = [
  { path: '', redirectTo: 'parc', pathMatch: 'full' },

  // ── Les six onglets ──
  { path: 'parc', loadComponent: () => import('./pages/onglet-parc/onglet-parc.component').then(m => m.OngletParcComponent) },
  { path: 'parc/rejoindre', loadComponent: () => import('./pages/parc/rejoindre.component').then(m => m.RejoindreComponent) },
  { path: 'batiment', loadComponent: () => import('./pages/onglet-batiment/onglet-batiment.component').then(m => m.OngletBatimentComponent) },
  { path: 'calculs', loadComponent: () => import('./pages/onglet-calculs/onglet-calculs.component').then(m => m.OngletCalculsComponent) },
  { path: 'parametrage', loadComponent: () => import('./pages/onglet-parametrage/onglet-parametrage.component').then(m => m.OngletParametrageComponent) },
  { path: 'ia', loadComponent: () => import('./pages/onglet-ia/onglet-ia.component').then(m => m.OngletIaComponent) },
  { path: 'rendu', loadComponent: () => import('./pages/onglet-rendu/onglet-rendu.component').then(m => m.OngletRenduComponent) },

  // ── Outils experts (hors menu) — three.js est lourd : pages chargées à la demande ──
  { path: 'maillage', loadComponent: () => import('./pages/batiment/batiment.component').then(m => m.BatimentComponent) },
  { path: 'environnement', loadComponent: () => import('./pages/environnement/environnement.component').then(m => m.EnvironnementComponent) },
  { path: 'calcul-3d', loadComponent: () => import('./pages/calcul-3d/calcul-3d.component').then(m => m.Calcul3DComponent) },
  { path: 'calcul-1d', loadComponent: () => import('./pages/calcul-1d/calcul-1d.component').then(m => m.Calcul1DComponent) },
  { path: 'parois', loadComponent: () => import('./pages/parois/parois.component').then(m => m.ParoisComponent) },
  { path: 'theorie', loadComponent: () => import('./pages/theorie/theorie.component').then(m => m.TheorieComponent) },

  // ── Anciennes adresses ──
  {
    path: 'fiche',
    redirectTo: ({ queryParams }) => {
      const [path, vue] = FICHE_VERS[String(queryParams['onglet'] ?? 'synthese')] ?? FICHE_VERS['synthese'];
      const q: Record<string, string> = {};
      if (queryParams['b']) q['b'] = String(queryParams['b']);
      if (vue) q['vue'] = vue;
      return inject(Router).createUrlTree([path], { queryParams: q });
    },
  },
  { path: 'mode-simplifie', redirectTo: 'parc', pathMatch: 'full' },
  { path: '**', redirectTo: 'parc' },
];
