import { Routes } from '@angular/router';
import { TheorieComponent }  from './pages/theorie/theorie.component';
import { Calcul1DComponent } from './pages/calcul-1d/calcul-1d.component';
import { ParoisComponent }   from './pages/parois/parois.component';

export const routes: Routes = [
  { path: '',          redirectTo: 'calcul-1d', pathMatch: 'full' },
  { path: 'theorie',   component: TheorieComponent },
  { path: 'calcul-1d', component: Calcul1DComponent },
  { path: 'parois',    component: ParoisComponent },
  { path: '**',        redirectTo: 'calcul-1d' },
];
