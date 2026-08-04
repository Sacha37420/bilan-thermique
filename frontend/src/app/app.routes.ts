import { Routes } from '@angular/router';
import { HomeComponent }     from './pages/home/home.component';
import { ProfileComponent }  from './pages/profile/profile.component';
import { TheorieComponent }  from './pages/theorie/theorie.component';
import { Calcul1DComponent } from './pages/calcul-1d/calcul-1d.component';

export const routes: Routes = [
  { path: '',         component: HomeComponent },
  { path: 'theorie',  component: TheorieComponent },
  { path: 'calcul-1d', component: Calcul1DComponent },
  { path: 'profile',  component: ProfileComponent },
  { path: '**',       redirectTo: '' },
];
