import { Component, computed, input, output } from '@angular/core';
import { Bouquet, Series } from './contrat.types';

type Mode = 'reel' | 'bouquets' | 'reel_et_bouquets' | 'certains';

/** Choix des séries d'un bloc (I6.a `SERIES`) : réel, tous les bouquets, les deux, ou certains bouquets. */
@Component({
  selector: 'app-choix-series',
  standalone: true,
  template: `
    <fieldset class="cs">
      <legend>{{ legende() }}</legend>
      @for (o of options; track o[0]) {
        <label class="check"><input type="radio" [name]="nom()" [checked]="mode() === o[0]" (change)="choisir(o[0])" />{{ o[1] }}</label>
      }
      @if (mode() === 'certains') {
        <div class="cs-b">
          @for (b of bouquets(); track b.id) {
            <label class="check"><input type="checkbox" [checked]="coche(b.id)" (change)="basculer(b.id, $any($event.target).checked)" />{{ b.nom }}</label>
          } @empty { <p class="rf-hint">Aucun bouquet disponible pour ce parc.</p> }
        </div>
      }
    </fieldset>
  `,
  styles: [`
    .cs { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-2) var(--space-3);
          display: flex; flex-wrap: wrap; column-gap: var(--space-4); }
    .cs legend { color: var(--text-mute); font-size: var(--text-xs); padding: 0 var(--space-1); }
    .cs-b { flex-basis: 100%; display: flex; flex-wrap: wrap; column-gap: var(--space-4); padding-left: var(--space-4); }
    .check { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 44px; cursor: pointer; font-size: var(--text-sm); }
    .check input { width: 20px; height: 20px; accent-color: var(--accent); }
  `],
})
export class ChoixSeriesComponent {
  readonly valeur = input.required<Series>();
  readonly bouquets = input<Bouquet[]>([]);
  readonly legende = input('Séries comparées');
  readonly nom = input('series');
  readonly seriesChange = output<Series>();

  readonly options: [Mode, string][] = [
    ['reel', 'État réel'], ['bouquets', 'Tous les bouquets'], ['reel_et_bouquets', 'Réel et bouquets'], ['certains', 'Certains bouquets'],
  ];
  readonly mode = computed<Mode>(() => { const v = this.valeur(); return Array.isArray(v) ? 'certains' : v; });

  coche(id: number): boolean { const v = this.valeur(); return Array.isArray(v) && v.includes(id); }
  choisir(m: Mode): void {
    if (m === 'certains') this.seriesChange.emit(this.bouquets().slice(0, 1).map(b => b.id));
    else this.seriesChange.emit(m);
  }
  basculer(id: number, oui: boolean): void {
    const v = Array.isArray(this.valeur()) ? (this.valeur() as number[]).filter(x => x !== id) : [];
    this.seriesChange.emit(oui ? [...v, id] : v);
  }
}
