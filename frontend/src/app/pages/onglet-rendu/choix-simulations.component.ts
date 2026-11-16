import { Component, computed, input, output } from '@angular/core';

export type Niveau = 'N0' | 'N1' | 'N2';
export const NIVEAUX_SIM: Niveau[] = ['N0', 'N1', 'N2'];
export const MOTS_SIM: Record<Niveau, string> = { N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi' };

/**
 * Simulations dont un bloc affiche les valeurs (lot 45) : aucune cochée = la plus fine à jour pour chaque bâtiment ;
 * une = cette simulation seulement ; plusieurs = les valeurs de chacune côte à côte, pour comparer une même grandeur.
 * `unique` (carte) : une seule à la fois.
 */
@Component({
  selector: 'app-choix-simulations',
  standalone: true,
  template: `
    <fieldset class="cs">
      <legend>{{ unique() ? 'Simulation affichée' : 'Simulations affichées' }}</legend>
      @for (n of niveaux; track n) {
        <label class="check"><input [type]="unique() ? 'radio' : 'checkbox'" [name]="nom()" [checked]="choisis().includes(n)"
          (change)="basculer(n, $any($event.target).checked)" />{{ mots[n] }} ({{ n }})</label>
      }
      @if (unique()) {
        <label class="check"><input type="radio" [name]="nom()" [checked]="!choisis().length" (change)="niveauxChange.emit([])" />La plus fine à jour</label>
      }
      <p class="rf-hint">{{ phrase() }}</p>
    </fieldset>
  `,
  styles: [`
    .cs { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-2) var(--space-3);
          display: flex; flex-wrap: wrap; column-gap: var(--space-4); margin: 0 0 var(--space-3); }
    .cs legend { color: var(--text-mute); font-size: var(--text-xs); padding: 0 var(--space-1); }
    .cs p { flex-basis: 100%; margin: 0 0 var(--space-1); }
    .check { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 44px; cursor: pointer; font-size: var(--text-sm); }
    .check input { width: 20px; height: 20px; accent-color: var(--accent); }
  `],
})
export class ChoixSimulationsComponent {
  readonly niveaux_ = input<Niveau[] | undefined>(undefined, { alias: 'niveaux' });
  readonly unique = input(false);
  readonly nom = input('sim');
  /** Ce que deviennent plusieurs simulations dans ce bloc (« une colonne par simulation »…). */
  readonly comparaison = input('une colonne par simulation');
  readonly niveauxChange = output<Niveau[]>();

  readonly niveaux = NIVEAUX_SIM;
  readonly mots = MOTS_SIM;
  readonly choisis = computed(() => NIVEAUX_SIM.filter(n => (this.niveaux_() ?? []).includes(n)));
  readonly phrase = computed(() => {
    const c = this.choisis();
    if (!c.length) return 'Aucune cochée : pour chaque bâtiment, la simulation la plus fine disponible et à jour (N2, sinon N1, sinon N0).';
    if (c.length === 1) return `Valeurs de la simulation ${MOTS_SIM[c[0]].toLowerCase()} (${c[0]}) seulement, à jour ou non.`;
    return `Valeurs de ${c.join(', ')} côte à côte : ${this.comparaison()}.`;
  });

  basculer(n: Niveau, oui: boolean): void {
    if (this.unique()) { this.niveauxChange.emit(oui ? [n] : []); return; }
    const cur = this.choisis().filter(x => x !== n);
    this.niveauxChange.emit(NIVEAUX_SIM.filter(x => (oui ? [...cur, n] : cur).includes(x)));
  }
}
