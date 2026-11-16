import { Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { GrapheComponent } from '../../components/graphe/graphe.component';
import { FigureRendu } from '../../components/graphe/graphe.types';
import { USAGE_LABELS } from '../../core/refonte.types';
import { ChoixSeriesComponent } from './choix-series.component';
import { Bouquet, Cible, Dimension, FORMES, FormeGraphe, Graphe, Mesure, Portee, Question, Series, grapheVide } from './contrat.types';
import { RenduApiService, messageDe } from './rendu-api.service';

const AVEC_TAILLE = new Set<FormeGraphe>(['nuage', 'bulles']);

/**
 * Constructeur de graphes guidé (I6.a `GRAPHE`) : 1) choisir une question → graphe proposé par le serveur ;
 * 2) régler forme, axes, couleur, facettes, séries, filtres, agrégation. L'aperçu est la figure que le serveur
 * construit (`POST /api/rendu/figure/`), redemandée après chaque réglage.
 */
@Component({
  selector: 'app-constructeur-graphe',
  standalone: true,
  imports: [GrapheComponent, ChoixSeriesComponent],
  templateUrl: './constructeur-graphe.component.html',
  styleUrl: './constructeur-graphe.component.scss',
})
export class ConstructeurGrapheComponent {
  readonly graphe = input.required<Graphe>();
  readonly portee = input.required<Portee>();
  readonly cible = input<Cible | null>(null);
  readonly questions = input<Question[]>([]);
  readonly dimensions = input<Dimension[]>([]);
  readonly mesures = input<Mesure[]>([]);
  readonly bouquets = input<Bouquet[]>([]);
  readonly catalogueErreur = input('');
  readonly grapheChange = output<Graphe>();

  private api = inject(RenduApiService);
  readonly FORMES = FORMES;
  readonly USAGES = Object.entries(USAGE_LABELS);
  readonly questionChoisie = signal<string | null>(null);
  readonly reglagesOuverts = signal(false);

  readonly figure = signal<FigureRendu | null>(null);
  readonly figureErreur = signal('');
  readonly chargement = signal(false);
  private demande = 0;
  private minuteur?: ReturnType<typeof setTimeout>;

  readonly vierge = computed(() => { const g = this.graphe(); return !g.x && !g.y.length; });
  readonly dimsForme = computed(() => this.dimensions().filter(d => !d.formes || d.formes.includes(this.graphe().forme)));
  readonly mesForme = computed(() => this.mesures().filter(m => !m.formes || m.formes.includes(this.graphe().forme)));
  readonly avecTaille = computed(() => AVEC_TAILLE.has(this.graphe().forme));

  constructor() {
    effect(() => {
      const g = this.graphe(), c = this.cible(), p = this.portee();
      untracked(() => this.planifier(g, c, p));
    });
    inject(DestroyRef).onDestroy(() => clearTimeout(this.minuteur));
  }

  private planifier(g: Graphe, c: Cible | null, p: Portee): void {
    clearTimeout(this.minuteur);
    this.figureErreur.set('');
    if (!c) { this.figure.set(null); this.figureErreur.set(p === 'parc' ? 'Choisissez un parc pour voir l’aperçu.' : 'Choisissez un bâtiment pour voir l’aperçu.'); return; }
    if (!g.y.length) { this.figure.set(null); this.figureErreur.set('Choisissez au moins une grandeur à représenter.'); return; }
    const n = ++this.demande;
    this.chargement.set(true);
    this.minuteur = setTimeout(async () => {
      try {
        const f = await this.api.figure(p, c, g);
        if (n === this.demande) this.figure.set(f);
      } catch (e) {
        if (n === this.demande) { this.figure.set(null); this.figureErreur.set(messageDe(e, 'Aperçu du graphe')); }
      } finally {
        if (n === this.demande) this.chargement.set(false);
      }
    }, 500);
  }

  // ── Étape 1 : une question ──
  partirDe(q: Question): void {
    this.questionChoisie.set(q.cle);
    this.grapheChange.emit({ ...grapheVide(), ...structuredClone(q.graphe) });
  }
  aZero(): void {
    this.questionChoisie.set(null);
    this.reglagesOuverts.set(true);
    this.grapheChange.emit(grapheVide());
  }

  // ── Étape 2 : réglages ──
  poser<K extends keyof Graphe>(k: K, v: Graphe[K]): void { this.grapheChange.emit({ ...this.graphe(), [k]: v }); }
  dim(v: string): string | null { return v === '' ? null : v; }
  basculerY(cle: string, oui: boolean): void {
    const y = this.graphe().y.filter(x => x !== cle);
    this.poser('y', oui ? [...y, cle] : y);
  }
  series(s: Series): void { this.poser('series', s); }
  usageCoche(u: string): boolean { return (this.graphe().filtre.usage ?? []).includes(u); }
  basculerUsage(u: string, oui: boolean): void {
    const f = { ...this.graphe().filtre };
    const l = (f.usage ?? []).filter(x => x !== u);
    f.usage = oui ? [...l, u] : l;
    if (!f.usage.length) delete f.usage;
    this.poser('filtre', f);
  }
  periode(i: 0 | 1, v: string): void {
    const f = { ...this.graphe().filtre };
    const p: [number | null, number | null] = [...(f.periode ?? [null, null])] as [number | null, number | null];
    const n = Number(v);
    p[i] = v.trim() === '' || !Number.isFinite(n) ? null : Math.round(n);
    if (p[0] === null && p[1] === null) delete f.periode; else f.periode = p;
    this.poser('filtre', f);
  }
  libelleMesure(m: Mesure): string { return m.unite ? `${m.libelle} (${m.unite})` : m.libelle; }
}
