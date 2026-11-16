import {
  Component, DestroyRef, ElementRef, computed, effect, inject, input, signal, untracked, viewChild,
} from '@angular/core';
import { ThemeService } from '../../core/theme.service';
import { FigureRendu, resumeDe, titreDe } from './graphe.types';
import { figureAuTheme } from './palette';

// plotly.js (≈ 3,5 Mo) n'entre jamais dans le bundle initial : chargé au premier graphe affiché, une fois.
type PlotlyApi = {
  react: (el: HTMLElement, data: unknown[], layout: unknown, config: unknown) => Promise<unknown>;
  purge: (el: HTMLElement) => void;
  downloadImage: (el: HTMLElement, opts: unknown) => Promise<unknown>;
  Plots: { resize: (el: HTMLElement) => void };
};
let plotlyPromesse: Promise<PlotlyApi> | null = null;
function chargerPlotly(): Promise<PlotlyApi> {
  plotlyPromesse ??= import('plotly.js-dist-min').then(m => ((m as unknown as { default?: PlotlyApi }).default ?? m) as unknown as PlotlyApi);
  return plotlyPromesse;
}

/**
 * Un graphe construit par le serveur (I6.b) : `Plotly.react` sur la figure reçue, aux couleurs du thème
 * Foyer (clair / sombre, relu à chaque bascule). Accessible : titre en légende de figure, résumé textuel
 * servi par le serveur, zone de dessin nommée.
 */
@Component({
  selector: 'app-graphe',
  standalone: true,
  template: `
    <figure class="graphe">
      @if (titreAffiche()) { <figcaption class="g-titre">{{ titreAffiche() }}</figcaption> }
      @if (erreur()) {
        <p class="rf-hint" role="status">{{ erreur() }}</p>
      } @else if (chargement() && !figure()) {
        <p class="rf-hint" role="status">Construction du graphe…</p>
      } @else if (figure()?.vide) {
        <p class="rf-hint" role="status">{{ resume() || 'Aucune donnée pour ce graphe.' }}</p>
      } @else if (figure()) {
        <div #zone class="g-zone" role="img" [attr.aria-label]="(titreAffiche() || 'Graphe') + (resume() ? ' — ' + resume() : '')"
             [style.height.px]="hauteur()"></div>
        @if (echecPlotly()) { <p class="rf-error" role="alert">{{ echecPlotly() }}</p> }
        @if (resume()) { <p class="g-resume">{{ resume() }}</p> }
        @if (telechargeable()) {
          <button type="button" class="rf-btn small no-print" (click)="image()" [disabled]="!pret()">Enregistrer l’image</button>
        }
      }
    </figure>
  `,
  styles: [`
    :host { display: block; }
    .graphe { margin: 0; display: grid; gap: var(--space-2); }
    .g-titre { font-weight: 600; font-size: var(--text-base); color: var(--text); }
    .g-zone { width: 100%; min-height: 220px; }
    .g-resume { margin: 0; font-size: var(--text-sm); color: var(--text-mute); max-width: 80ch; }
    .graphe .rf-btn { justify-self: start; }
    @media print { .no-print { display: none !important; } }
  `],
})
export class GrapheComponent {
  readonly figure = input<FigureRendu | null>(null);
  /** Titre à afficher ; à défaut, celui de la figure. */
  readonly titre = input('');
  readonly hauteur = input(360);
  readonly chargement = input(false);
  /** Message à la place du graphe (route indisponible, refus…). */
  readonly erreur = input('');
  readonly telechargeable = input(true);

  private theme = inject(ThemeService);
  private zone = viewChild<ElementRef<HTMLDivElement>>('zone');
  readonly pret = signal(false);
  readonly echecPlotly = signal('');
  /** Bascule du thème système (sans choix manuel) : relue comme une bascule manuelle. */
  private systeme = signal(0);

  readonly titreAffiche = computed(() => this.titre() || titreDe(this.figure()));
  readonly resume = computed(() => resumeDe(this.figure()));

  private plotly: PlotlyApi | null = null;
  private dessine: HTMLElement | null = null;
  private obs?: ResizeObserver;

  constructor() {
    const mq = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    const surSysteme = () => this.systeme.update(x => x + 1);
    mq?.addEventListener('change', surSysteme);

    effect(() => {
      const el = this.zone()?.nativeElement;
      const f = this.figure();
      this.theme.theme(); this.systeme();
      if (!el || !f || f.vide) { untracked(() => this.liberer()); return; }
      untracked(() => void this.dessiner(el, f));
    });

    inject(DestroyRef).onDestroy(() => {
      mq?.removeEventListener('change', surSysteme);
      this.liberer();
    });
  }

  private async dessiner(el: HTMLElement, f: FigureRendu): Promise<void> {
    try {
      const P = this.plotly ?? (this.plotly = await chargerPlotly());
      if (this.zone()?.nativeElement !== el || this.figure() !== f) return;   // remplacé entre-temps
      // Couleurs lues après la pose de [data-theme] (la bascule l'écrit dans le même tour).
      await new Promise(r => requestAnimationFrame(r));
      const { data, layout } = figureAuTheme(f);
      await P.react(el, data, layout, { responsive: true, displaylogo: false, displayModeBar: false });
      if (this.dessine !== el) {
        this.liberer();
        this.dessine = el;
        this.obs = new ResizeObserver(() => { if (this.dessine) P.Plots.resize(this.dessine); });
        this.obs.observe(el);
      }
      this.echecPlotly.set('');
      this.pret.set(true);
    } catch {
      this.echecPlotly.set('Le graphe n’a pas pu être dessiné.');
    }
  }

  private liberer(): void {
    this.obs?.disconnect(); this.obs = undefined;
    if (this.dessine && this.plotly) this.plotly.purge(this.dessine);
    this.dessine = null;
    this.pret.set(false);
  }

  image(): void {
    if (!this.dessine || !this.plotly) return;
    const nom = (this.titreAffiche() || 'graphe').toLowerCase().normalize('NFD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'graphe';
    void this.plotly.downloadImage(this.dessine, { format: 'png', filename: nom, width: 1200, height: 700, scale: 2 });
  }
}
