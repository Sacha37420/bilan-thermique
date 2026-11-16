import { Component, computed, input, output } from '@angular/core';
import { Graphe } from './contrat.types';
import { ChoixSimulationsComponent, NIVEAUX_SIM, Niveau } from './choix-simulations.component';

/**
 * Simulations d'un graphe (bt-trois-niveaux I5, lot 45) — réglées par le filtre `niveau` et la dimension `niveau` du
 * GRAPHE (I6.a), rien de plus : aucune cochée = la plus fine à jour (défaut du serveur) ; une = filtre sur ce niveau ;
 * plusieurs = filtre sur ces niveaux **et** dimension `niveau` (en couleur si elle est libre, sinon un petit graphe par
 * niveau, sauf si l'abscisse la porte déjà).
 */
@Component({
  selector: 'app-choix-niveau',
  standalone: true,
  imports: [ChoixSimulationsComponent],
  template: `
    <app-choix-simulations [niveaux]="choisis()" [nom]="nom()" [comparaison]="comparaison()" (niveauxChange)="choisir($event)" />
  `,
})
export class ChoixNiveauComponent {
  readonly graphe = input.required<Graphe>();
  readonly nom = input('niveau');
  readonly grapheChange = output<Graphe>();

  private readonly dimension = computed(() => { const g = this.graphe(); return [g.x, g.couleur, g.facettes].includes('niveau'); });

  readonly choisis = computed<Niveau[]>(() => {
    const f = this.graphe().filtre.niveau;
    if (Array.isArray(f) && f.length) return NIVEAUX_SIM.filter(n => f.includes(n));
    return this.dimension() ? [...NIVEAUX_SIM] : [];
  });

  readonly comparaison = computed(() => {
    const g = this.graphe();
    return g.x === 'niveau' ? 'une barre par simulation en abscisse'
      : g.facettes === 'niveau' ? 'un petit graphe par simulation' : 'une couleur par simulation';
  });

  choisir(niveaux: Niveau[]): void { this.grapheChange.emit(grapheAvecNiveaux(this.graphe(), niveaux)); }
}

/** Graphe réglé pour ces simulations (voir la classe) ; sert aussi au réglage « tout le document ». */
export function grapheAvecNiveaux(g: Graphe, niveaux: Niveau[]): Graphe {
  const dimension = [g.x, g.couleur, g.facettes].includes('niveau');
  const filtre = { ...g.filtre };
  delete filtre.niveau;
  if (niveaux.length >= 2) {
    // Plusieurs simulations : garder la dimension si elle est déjà posée, sinon en couleur (ou en petits graphes).
    let suite: Graphe = { ...g, filtre: niveaux.length === 3 ? filtre : { ...filtre, niveau: niveaux } };
    if (!dimension) suite = suite.couleur ? { ...suite, facettes: 'niveau' } : { ...suite, couleur: 'niveau' };
    return suite;
  }
  // Zéro ou une : la dimension `niveau` est retirée de là où elle était.
  const sans = (d: string | null) => (d === 'niveau' ? null : d);
  const suite: Graphe = { ...g, x: sans(g.x), couleur: sans(g.couleur), facettes: sans(g.facettes), filtre };
  return niveaux.length ? { ...suite, filtre: { ...filtre, niveau: niveaux } } : suite;
}
