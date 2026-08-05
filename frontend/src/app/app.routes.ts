import { Routes } from '@angular/router';
import { TheorieComponent }  from './pages/theorie/theorie.component';
import { Calcul1DComponent } from './pages/calcul-1d/calcul-1d.component';
import { ParoisComponent }   from './pages/parois/parois.component';

export const routes: Routes = [
  { path: '',          redirectTo: 'calcul-1d', pathMatch: 'full' },
  { path: 'theorie',   component: TheorieComponent },
  { path: 'calcul-1d', component: Calcul1DComponent },
  { path: 'parois',    component: ParoisComponent },
  {
    // three.js (viewer 3D) est lourd — chargé à la demande, comme la page
    // "jouer" de craft-lab (Phaser) — pas dans le bundle initial de l'app.
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
  { path: '**',        redirectTo: 'calcul-1d' },
];
