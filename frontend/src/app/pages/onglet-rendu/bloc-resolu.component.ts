import { Component, computed, input } from '@angular/core';
import { GrapheComponent } from '../../components/graphe/graphe.component';
import { Vue3dResultatsComponent } from '../../components/vue3d-resultats/vue3d-resultats.component';
import { PotentielsBouquetsComponent } from './potentiels-bouquets.component';
import { BlocResolu, LigneFiche, LigneIndicateur } from './contrat.types';

/** Un bloc du document résolu par le serveur (I6.g), affiché tel quel : aucune donnée recalculée ici. */
@Component({
  selector: 'app-bloc-resolu',
  standalone: true,
  imports: [GrapheComponent, Vue3dResultatsComponent, PotentielsBouquetsComponent],
  template: `
    @let b = bloc();
    @if (b.indisponible) {
      <p class="indispo" role="note">{{ b.indisponible }}</p>
    } @else {
      @switch (b.type) {
        @case ('texte') { <div class="texte" [innerHTML]="b.html ?? ''"></div> }
        @case ('indicateurs') {
          <div class="rf-table-wrap"><table class="rf-table">
            <thead><tr><th>Indicateur</th>@for (c of colsSerie(); track c.serie) { <th class="num">{{ c.libelle }}</th> }</tr></thead>
            <tbody>
              @for (l of lignesInd(); track l.cle) {
                <tr><td>{{ l.libelle }}@if (l.unite) { <small> ({{ l.unite }})</small> }</td>
                  @for (v of l.valeurs; track $index; let j = $index) {
                    <td class="num">{{ nombre(v) }}@if (l.ecarts[j] !== null && l.ecarts[j] !== undefined) { <small>{{ signe(l.ecarts[j]) }}</small> }</td>
                  }</tr>
              }
            </tbody>
          </table></div>
        }
        @case ('tableau') {
          <div class="rf-table-wrap"><table class="rf-table">
            <thead><tr>@for (c of colsTableau(); track c.cle) { <th>{{ c.libelle }}@if (c.unite) { <small> ({{ c.unite }})</small> }</th> }</tr></thead>
            <tbody>
              @for (l of lignesTableau(); track $index) { <tr>@for (v of l; track $index) { <td [class.num]="estNombre(v)">{{ cellule(v) }}</td> }</tr> }
            </tbody>
          </table></div>
          @if ((b.total_lignes ?? 0) > lignesTableau().length) {
            <p class="rf-hint">{{ lignesTableau().length }} lignes affichées sur {{ b.total_lignes }}.</p>
          }
        }
        @case ('graphe') { <app-graphe [figure]="b.figure ?? null" /> }
        @case ('carte') { <app-graphe [figure]="b.figure ?? null" [hauteur]="420" /> }
        @case ('vue3d') {
          @if (b.scene) {
            @defer (on viewport) {
              <app-vue3d-resultats [scene]="b.scene" [grandeur]="b.grandeur || 'pertes_hiver_kwh_m2'" [camera]="b.camera || 'iso'"
                                   [libelle]="b.libelle" [unite]="b.unite" />
            } @placeholder { <p class="rf-hint">La vue 3D se charge quand elle apparaît à l’écran…</p> }
          }
        }
        @case ('fiche') {
          @for (s of b.sections ?? []; track s.cle) {
            @if (s.cle !== 'potentiels') {
              <section class="fiche-s">
                <h4>{{ s.titre }}</h4>
                <table class="rf-table faits"><tbody>
                  @for (e of s.elements; track $index) {
                    <tr><th scope="row">{{ e.libelle }}</th><td>{{ valeurFiche(e) }}</td><td class="src">{{ e.source }}</td></tr>
                  }
                </tbody></table>
              </section>
            } @else if (b.potentiels) {
              <section class="fiche-s"><h4>{{ s.titre }}</h4><app-potentiels-bouquets [potentiels]="b.potentiels" /></section>
            }
          }
          @if (b.potentiels && !aSectionPotentiels()) {
            <section class="fiche-s"><h4>Potentiels par bouquet</h4><app-potentiels-bouquets [potentiels]="b.potentiels" /></section>
          }
        }
        @case ('saut_de_page') { <div class="saut" aria-hidden="true"></div> }
      }
    }
  `,
  styles: [`
    :host { display: block; break-inside: avoid; }
    .indispo { margin: 0; padding: var(--space-2) var(--space-3); border: 1px dashed var(--border); border-radius: var(--radius-sm);
               color: var(--text-mute); font-size: var(--text-sm); }
    .texte { max-width: 80ch; }
    .texte :is(p, ul, ol) { margin: 0 0 var(--space-2); }
    .texte ul, .texte ol { padding-left: var(--space-5); }
    .texte a { color: var(--accent); }
    td small, th small { color: var(--text-mute); font-weight: 400; }
    td.num small { display: block; font-size: var(--text-xs); }
    .fiche-s { margin-bottom: var(--space-4); break-inside: avoid; }
    .fiche-s h4 { margin: 0 0 var(--space-2); font-size: var(--text-base); }
    .faits th[scope=row] { text-transform: none; letter-spacing: 0; color: var(--text); font-weight: 500; font-size: var(--text-sm); width: 45%; }
    .faits .src { color: var(--text-mute); font-size: var(--text-xs); text-align: right; }
    .saut { border-top: 1px dashed var(--border); margin: var(--space-2) 0; }
    @media print { :host:has(.saut) { break-after: page; } .saut { border: 0; margin: 0; } }
  `],
})
export class BlocResoluComponent {
  readonly bloc = input.required<BlocResolu>();

  readonly colsSerie = computed(() => (this.bloc().colonnes ?? []) as { serie: string; libelle: string }[]);
  readonly lignesInd = computed(() => (this.bloc().lignes ?? []) as LigneIndicateur[]);
  readonly colsTableau = computed(() => (this.bloc().colonnes ?? []) as { cle: string; libelle: string; unite: string }[]);
  readonly lignesTableau = computed(() => (this.bloc().lignes ?? []) as unknown[][]);
  readonly aSectionPotentiels = computed(() => (this.bloc().sections ?? []).some(s => s.cle === 'potentiels'));

  nombre(v: number | null | undefined): string {
    return typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('fr-FR', { maximumFractionDigits: Math.abs(v) < 10 ? 2 : 0 }) : '—';
  }
  signe(v: number | null | undefined): string {
    if (typeof v !== 'number' || !Number.isFinite(v)) return '';
    return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('fr-FR', { maximumFractionDigits: Math.abs(v) < 10 ? 1 : 0 })}`;
  }
  estNombre(v: unknown): boolean { return typeof v === 'number'; }
  cellule(v: unknown): string {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'number') return this.nombre(v);
    if (typeof v === 'boolean') return v ? 'oui' : 'non';
    return String(v);
  }
  valeurFiche(e: LigneFiche): string {
    const v = this.cellule(e.valeur);
    return v !== '—' && e.unite && typeof e.valeur === 'number' ? `${v} ${e.unite}` : v;
  }
}
