import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ContexteService } from '../../core/contexte.service';
import { ErreurApi, RenduApiService } from '../../pages/onglet-rendu/rendu-api.service';
import { Vue3dResultatsComponent } from './vue3d-resultats.component';
import { ScenePartriangle } from './vue3d.types';

type Etat =
  | { k: 'chargement' }
  | { k: 'aucun'; peutLancer: boolean }
  | { k: 'en_cours' }
  | { k: 'echec'; message: string }
  | { k: 'erreur'; message: string }
  | { k: 'indispo' }
  | { k: 'pret'; id: number; date: string; aJour: boolean | null; scene: ScenePartriangle };

function dateFr(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * Dernier calcul détaillé par paroi (N2) d'un bâtiment, en 3D (contrat bt-potentiels, I4) : retrouve le dernier
 * N2 du bâtiment, puis ses résultats par triangle. Toute erreur est rendue en mots ; aucune promesse n'est
 * laissée rejetée.
 */
@Component({
  selector: 'app-vue3d-batiment',
  standalone: true,
  imports: [RouterLink, Vue3dResultatsComponent],
  template: `
    @switch (etat().k) {
      @case ('chargement') { <p class="rf-hint" role="status">Chargement des résultats par paroi…</p> }
      @case ('aucun') {
        <p>Aucun calcul détaillé par paroi (N2) pour ce bâtiment.</p>
        @if (peutLancer()) {
          <a class="rf-btn primary v3b-lien" routerLink="/calculs"
             [queryParams]="ctx.queryParams({ vue: 'simulation', type: 'N2' })">Lancer un calcul détaillé</a>
        } @else {
          <p class="rf-hint">Réservé à un gestionnaire du bâtiment.</p>
        }
      }
      @case ('en_cours') {
        <p role="status">Le calcul détaillé (N2) est en cours.</p>
        <button type="button" class="rf-btn" (click)="actualiser()">Actualiser</button>
      }
      @case ('echec') {
        <p class="rf-error" role="alert">{{ message() }}</p>
        <button type="button" class="rf-btn" (click)="actualiser()">Actualiser</button>
      }
      @case ('erreur') {
        <p class="rf-error" role="alert">{{ message() }}</p>
        <button type="button" class="rf-btn" (click)="actualiser()">Réessayer</button>
      }
      @case ('indispo') { <p class="rf-hint" role="status">Les résultats par triangle ne sont pas encore disponibles sur le serveur.</p> }
      @case ('pret') {
        @if (pret(); as p) {
          <p class="rf-hint v3b-meta">Calcul détaillé (N2) du {{ p.date }}
            @if (p.aJour === true) { <span class="rf-badge m-mesure">à jour</span> }
            @else if (p.aJour === false) { <span class="rf-badge m-infere">calculé avant les dernières données</span> }
            · <a routerLink="/calculs" [queryParams]="ctx.queryParams({ vue: 'historique', calcul: p.id })">Voir ce calcul</a>
          </p>
          <app-vue3d-resultats [scene]="p.scene" />
        }
      }
    }
  `,
  styles: [`
    :host { display: block; }
    .v3b-lien, .rf-btn { min-height: 44px; display: inline-flex; align-items: center; }
    .v3b-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
    .v3b-meta a { min-height: 44px; display: inline-flex; align-items: center; }
  `],
})
export class Vue3dBatimentComponent {
  protected ctx = inject(ContexteService);
  private api = inject(RenduApiService);

  readonly buildingId = input.required<number>();
  readonly etat = signal<Etat>({ k: 'chargement' });
  /** Numéro de la dernière demande : une réponse arrivée pour un ancien bâtiment est ignorée. */
  private jeton = 0;

  constructor() {
    effect(() => {
      const id = this.buildingId();
      untracked(() => void this.charger(id));
    });
  }

  actualiser(): void { void this.charger(this.buildingId()); }

  peutLancer(): boolean { const e = this.etat(); return e.k === 'aucun' && e.peutLancer; }
  message(): string { const e = this.etat(); return e.k === 'echec' || e.k === 'erreur' ? e.message : ''; }
  pret(): Extract<Etat, { k: 'pret' }> | null { const e = this.etat(); return e.k === 'pret' ? e : null; }

  private async charger(id: number): Promise<void> {
    const n = ++this.jeton;
    const vivant = () => n === this.jeton;
    this.etat.set({ k: 'chargement' });
    try {
      const r = await this.api.resultat(id);
      if (!vivant()) return;
      const n2 = r?.N2 ?? null;
      if (!n2) { this.etat.set({ k: 'aucun', peutLancer: !!r?.can_n2 }); return; }
      if (n2.status === 'PENDING' || n2.status === 'RUNNING') { this.etat.set({ k: 'en_cours' }); return; }
      if (n2.status === 'ERROR') { this.etat.set({ k: 'echec', message: 'Le dernier calcul détaillé (N2) a échoué. Relancez-le depuis l’onglet Calculs.' }); return; }
      if (n2.status !== 'DONE') { this.etat.set({ k: 'en_cours' }); return; }
      const scene = await this.api.triangles(n2.id);
      if (!vivant()) return;
      this.etat.set({ k: 'pret', id: n2.id, date: dateFr(n2.created_at), aJour: n2.up_to_date ?? null, scene });
    } catch (e) {
      if (!vivant()) return;
      if (e instanceof ErreurApi && e.indisponible) { this.etat.set({ k: 'indispo' }); return; }
      this.etat.set({ k: 'erreur', message: e instanceof ErreurApi ? e.message : 'Résultats par paroi : erreur inattendue.' });
    }
  }
}
