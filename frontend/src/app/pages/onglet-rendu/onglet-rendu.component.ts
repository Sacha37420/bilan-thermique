import { Component, computed, effect, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { ContexteService } from '../../core/contexte.service';
import { ApercuRenduComponent } from './apercu-rendu.component';
import { ParametrageRenduComponent } from './parametrage-rendu.component';
import { Portee } from './contrat.types';
import { RenduEtatService } from './rendu-etat.service';

type Page = 'parametrage' | 'rendu';

/**
 * Onglet Rendu (chantier bt-potentiels, lot 39) en deux pages : « Paramétrage du rendu » (sections, blocs,
 * constructeur de graphes guidé, modèles) et « Rendu » (aperçu du document construit par le serveur, exports
 * HTML / Word / PDF). Portée : le parc courant ou le bâtiment courant (fiche d'identité).
 * URL : `?vue=parametrage|rendu&portee=parc|batiment` ; les anciennes adresses `?vue=parc|batiment` ouvrent le rendu.
 */
@Component({
  selector: 'app-onglet-rendu',
  standalone: true,
  imports: [ContexteBarComponent, ParametrageRenduComponent, ApercuRenduComponent],
  providers: [RenduEtatService],
  templateUrl: './onglet-rendu.component.html',
  styleUrl: './onglet-rendu.component.scss',
})
export class OngletRenduComponent {
  protected ctx = inject(ContexteService);
  protected etat = inject(RenduEtatService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  private q = toSignal(this.route.queryParamMap.pipe(map(m => ({ vue: m.get('vue'), portee: m.get('portee') }))),
    { initialValue: { vue: null as string | null, portee: null as string | null } });
  readonly page = computed<Page>(() => (this.q().vue === 'parametrage' ? 'parametrage' : 'rendu'));
  readonly portee = computed<Portee>(() => {
    const { vue, portee } = this.q();
    if (portee === 'batiment' || portee === 'parc') return portee;
    return vue === 'batiment' ? 'batiment' : 'parc';
  });

  constructor() {
    effect(() => this.etat.portee.set(this.portee()));
  }

  aller(page: Page, portee: Portee = this.portee()): void {
    void this.router.navigate([], { queryParams: { vue: page, portee }, queryParamsHandling: 'merge', replaceUrl: true });
  }
}
