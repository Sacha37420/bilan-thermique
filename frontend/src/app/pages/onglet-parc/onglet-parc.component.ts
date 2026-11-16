import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { PortfolioInfo } from '../../core/refonte.types';
import { ParcBatimentsComponent } from './parc-batiments.component';
import { ParcAjouterComponent } from './parc-ajouter.component';
import { ParcPartageComponent } from './parc-partage.component';
import { ParcOrchestrateurComponent } from './parc-orchestrateur.component';
import { ROLE_PHRASE, VUES_PARC, VueParc } from './parc.types';

/** Onglet Parc (chantier bt-navigation) : choisir ou créer un parc, puis ses bâtiments, l'ajout, le partage, les sources. */
@Component({
  selector: 'app-onglet-parc',
  standalone: true,
  imports: [FormsModule, ContexteBarComponent, ParcBatimentsComponent, ParcAjouterComponent, ParcPartageComponent, ParcOrchestrateurComponent],
  styleUrls: ['./parc-commun.scss'],
  styles: [`
    .parcs { list-style: none; padding: 0; margin: 0; display: grid; gap: var(--space-3); grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr)); }
    .parcs button { width: 100%; min-height: 44px; text-align: left; background: var(--bg); color: var(--text); border: 1px solid var(--border);
      border-radius: var(--radius-sm); padding: var(--space-3) var(--space-4); font: inherit; cursor: pointer; display: grid; gap: var(--space-1); }
    .parcs button:hover, .parcs button:focus-visible { border-color: var(--accent); background: var(--accent-tint); }
    .parcs strong { font-size: var(--text-base); }
    .parcs small { color: var(--text-mute); font-size: var(--text-sm); }
    .tete { display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; justify-content: space-between; margin-bottom: var(--space-4); }
    .tete h1 { margin: 0; }
  `],
  template: `
    <section class="rf-page">
      <app-contexte-bar [avecBatiment]="!!ctx.parcId()" />

      @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }

      @if (!ctx.parcId() || creer()) {
        <p class="rf-eyebrow">Parc</p>
        <h1>Vos parcs de bâtiments</h1>
        <p class="rf-sub">Un parc regroupe des bâtiments analysés ensemble : chacun est identifié et décrit automatiquement depuis les
          données ouvertes, puis calculé. Choisissez un parc ou créez-en un.</p>

        <section class="rf-card" aria-labelledby="np-h">
          <div class="rf-card-head"><h2 id="np-h">Nouveau parc</h2>
            @if (ctx.parcId()) { <button type="button" class="rf-btn" (click)="creer.set(false)">Revenir au parc</button> }</div>
          <div class="rf-row">
            <label class="rf-field grow"><span>Nom du parc</span>
              <input type="text" [(ngModel)]="nom" maxlength="150" placeholder="Écoles de la commune" (keyup.enter)="creerParc()"></label>
            <button type="button" class="rf-btn primary" (click)="creerParc()" [disabled]="!nom.trim()">Créer le parc</button>
          </div>
          <p class="rf-hint">Vous en serez gestionnaire : vous pourrez y ajouter des bâtiments et le partager.</p>
        </section>

        <section class="rf-card" aria-labelledby="lp-h">
          <div class="rf-card-head"><h2 id="lp-h">Parcs existants</h2></div>
          <ul class="parcs">
            @for (p of ctx.parcs(); track p.id) {
              <li><button type="button" (click)="ouvrir(p.id)">
                <strong>{{ p.name }}</strong>
                <small>{{ resume(p) }}</small>
                <small>Votre rôle : {{ role(p) }}</small>
              </button></li>
            } @empty { <li class="rf-hint">Aucun parc pour l'instant.</li> }
            <li><button type="button" (click)="ouvrir(HORS_PARC)">
              <strong>Bâtiments hors parc</strong>
              <small>Bâtiments créés avant les parcs, visibles de tous les membres de l'app.</small>
            </button></li>
          </ul>
        </section>
      } @else {
        <div class="tete">
          <div><p class="rf-eyebrow">Parc</p><h1>{{ ctx.parc()?.name }}</h1>
            @if (!ctx.horsParc() && ctx.parc(); as p) { <p class="rf-hint">Votre rôle : <span class="aide" [title]="phraseRole(p)">{{ role(p) }}</span></p> }</div>
          <button type="button" class="rf-btn" (click)="creer.set(true)">Nouveau parc</button>
        </div>

        <nav class="rf-tabs" aria-label="Sections du parc">
          @for (v of vues; track v[0]) {
            <button type="button" [class.active]="vue() === v[0]" (click)="choisirVue(v[0])" [attr.aria-current]="vue() === v[0] ? 'page' : null">{{ v[1] }}</button>
          }
        </nav>

        @for (k of [ctx.parcId()]; track k) {
          @switch (vue()) {
            @case ('ajouter') { <app-parc-ajouter /> }
            @case ('import-partage') { <app-parc-partage /> }
            @case ('orchestrateur') { <app-parc-orchestrateur /> }
            @default { <app-parc-batiments /> }
          }
        }
      }
    </section>
  `,
})
export class OngletParcComponent {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  protected readonly HORS_PARC = HORS_PARC;
  vues = VUES_PARC;
  vue = signal<VueParc>('batiments');
  creer = signal(false);
  error = signal('');
  nom = '';

  constructor() {
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe(q => {
      const v = q.get('vue') as VueParc | null;
      this.vue.set(v && VUES_PARC.some(x => x[0] === v) ? v : 'batiments');
    });
  }

  choisirVue(v: VueParc): void {
    void this.router.navigate([], { queryParams: { vue: v === 'batiments' ? null : v }, queryParamsHandling: 'merge', replaceUrl: true });
  }

  ouvrir(id: number): void {
    this.creer.set(false);
    this.ctx.choisirParc(id);
  }

  creerParc(): void {
    const name = this.nom.trim();
    if (!name) return;
    this.error.set('');
    this.rf.post<PortfolioInfo>('/parcs/', { name }).subscribe({
      next: async p => {
        this.nom = '';
        await this.ctx.rechargerParcs();
        this.creer.set(false);
        // Une seule navigation : le contexte lit `?p=` à son arrivée (deux navigations successives se marchent dessus).
        void this.router.navigate([], { queryParams: { p: p.id, b: null, vue: 'ajouter' }, queryParamsHandling: 'merge', replaceUrl: true });
      },
      error: e => this.error.set(e?.error?.name?.[0] ?? e?.error?.detail ?? 'Création refusée.'),
    });
  }

  resume(p: PortfolioInfo): string {
    const c = p.counts;
    if (!c.total) return 'Aucun bâtiment';
    const analyses = c.complet + c.incomplet;
    return `${c.total} bâtiment${c.total > 1 ? 's' : ''}, ${analyses} analysé${analyses > 1 ? 's' : ''}` +
      (c.incomplet ? `, ${c.incomplet} à vérifier` : '') + (c.en_attente + c.en_cours ? `, ${c.en_attente + c.en_cours} en cours` : '');
  }

  role(p: PortfolioInfo): string { return p.role ? (ROLE_PHRASE[p.role]?.nom ?? p.role) : 'accès à certains bâtiments seulement'; }
  phraseRole(p: PortfolioInfo): string { return p.role ? (ROLE_PHRASE[p.role]?.phrase ?? '') : 'Vous ne voyez que les bâtiments qui vous sont confiés.'; }
}
