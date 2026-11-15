import { Routes } from '@angular/router';
import { TheorieComponent }  from './pages/theorie/theorie.component';
import { Calcul1DComponent } from './pages/calcul-1d/calcul-1d.component';
import { ParoisComponent }   from './pages/parois/parois.component';

export const routes: Routes = [
  { path: '',          redirectTo: 'parc', pathMatch: 'full' },
  { path: 'theorie',   component: TheorieComponent },
  { path: 'calcul-1d', component: Calcul1DComponent },
  { path: 'parois',    component: ParoisComponent },
  // Lot 27 (refonte) : le mode simplifié est couvert par les parcs (adresse → fiche automatique → calcul).
  { path: 'mode-simplifie', redirectTo: 'parc', pathMatch: 'full' },
  // three.js (viewer 3D) est lourd — chargé à la demande, comme la page
  // "jouer" de craft-lab (Phaser) — pas dans le bundle initial de l'app.
  {
    path: 'batiment',
    loadComponent: () => import('./pages/batiment/batiment.component').then(m => m.BatimentComponent),
  },
  {
    path: 'environnement',
    loadComponent: () => import('./pages/environnement/environnement.component').then(m => m.EnvironnementComponent),
  },
  {
    path: 'calcul-3d',
    loadComponent: () => import('./pages/calcul-3d/calcul-3d.component').then(m => m.Calcul3DComponent),
  },
  // ── Refonte : parc, fiche, affinage ──
  { path: 'parc', loadComponent: () => import('./pages/parc/parc.component').then(m => m.ParcComponent) },
  { path: 'parc/rejoindre', loadComponent: () => import('./pages/parc/rejoindre.component').then(m => m.RejoindreComponent) },
  { path: 'ia', loadComponent: () => import('./pages/ia/ia.component').then(m => m.IaComponent) },
  { path: 'fiche', loadComponent: () => import('./pages/fiche/fiche.component').then(m => m.FicheComponent) },
  { path: '**',        redirectTo: 'calcul-1d' },
];
