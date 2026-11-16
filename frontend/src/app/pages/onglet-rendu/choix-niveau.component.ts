import { Component, computed, input, output } from '@angular/core';
import { Graphe } from './contrat.types';

type Niveau = 'N0' | 'N1' | 'N2';
type Mode = 'auto' | Niveau | 'comparer';

const MOTS: Record<Niveau, string> = { N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi' };

/**
 * Niveau de calcul d'un graphe (bt-trois-niveaux, I5) — réglé par le filtre `niveau` et la dimension `niveau`
 * du GRAPHE (I6.a), rien de plus : « le plus fin à jour » (défaut du serveur : N2, sinon N1, sinon N0), un niveau
 * imposé, ou les trois comparés (dimension `niveau` en couleur, ou en petits graphes si la couleur est prise).
 */
@Component({
  selector: 'app-choix-niveau',
  standalone: true,
  template: `
    <fieldset class="cn">
      <legend>Niveau de calcul</legend>
      @for (o of options; track o[0]) {
        <label class="check"><input type="radio" [name]="nom()" [checked]="mode() === o[0]" (change)="choisir(o[0])" />{{ o[1] }}</label>
      }
      @if (mode() === 'comparer') {
        <p class="rf-hint">Les niveaux sont distingués par
          {{ graphe().couleur === 'niveau' ? 'la couleur' : graphe().facettes === 'niveau' ? 'un petit graphe par niveau' : 'l’abscisse' }}.</p>
      } @else if (mode() === 'auto') {
        <p class="rf-hint">Pour chaque bâtiment, le calcul le plus fin disponible et à jour ; le graphe dit lequel dans sa légende.</p>
      }
    </fieldset>
  `,
  styles: [`
    .cn { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-2) var(--space-3);
          display: flex; flex-wrap: wrap; column-gap: var(--space-4); margin: 0 0 var(--space-3); }
    .cn legend { color: var(--text-mute); font-size: var(--text-xs); padding: 0 var(--space-1); }
    .cn p { flex-basis: 100%; margin: 0 0 var(--space-1); }
    .check { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 44px; cursor: pointer; font-size: var(--text-sm); }
    .check input { width: 20px; height: 20px; accent-color: var(--accent); }
  `],
})
export class ChoixNiveauComponent {
  readonly graphe = input.required<Graphe>();
  readonly nom = input('niveau');
  readonly grapheChange = output<Graphe>();

  readonly options: [Mode, string][] = [
    ['auto', 'Le plus fin à jour'], ['N0', `${MOTS.N0} (N0)`], ['N1', `${MOTS.N1} (N1)`], ['N2', `${MOTS.N2} (N2)`],
    ['comparer', 'Comparer les trois niveaux'],
  ];

  readonly mode = computed<Mode>(() => {
    const g = this.graphe();
    if ([g.x, g.couleur, g.facettes].includes('niveau')) return 'comparer';
    const f = g.filtre.niveau;
    return Array.isArray(f) && f.length === 1 ? f[0] : 'auto';
  });

  choisir(m: Mode): void {
    const g = this.graphe();
    const filtre = { ...g.filtre };
    delete filtre.niveau;
    // Quitter « comparer » : la dimension `niveau` est retirée de là où elle était.
    const sans = (d: string | null) => (d === 'niveau' ? null : d);
    let suite: Graphe = { ...g, x: sans(g.x), couleur: sans(g.couleur), facettes: sans(g.facettes), filtre };
    if (m === 'N0' || m === 'N1' || m === 'N2') suite = { ...suite, filtre: { ...filtre, niveau: [m] } };
    else if (m === 'comparer') {
      // En couleur si elle est libre, sinon un petit graphe par niveau (remplace d'éventuelles facettes).
      suite = suite.couleur ? { ...suite, facettes: 'niveau' } : { ...suite, couleur: 'niveau' };
    }
    this.grapheChange.emit(suite);
  }
}
