import { Component, Input, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';

/**
 * Bandeau de contexte en tête de chaque onglet : parc courant › bâtiment courant, modifiables sur
 * place. Le choix vaut pour tous les onglets (ContexteService).
 */
@Component({
  selector: 'app-contexte-bar',
  standalone: true,
  imports: [RouterLink],
  template: `
    <nav class="ctx" aria-label="Parc et bâtiment courants">
      <label class="ctx-field">
        <span>Parc</span>
        <select [value]="ctx.parcId() ?? ''" (change)="parc($any($event.target).value)" aria-label="Parc courant">
          <option value="">— choisir un parc —</option>
          @for (p of ctx.parcs(); track p.id) {
            <option [value]="p.id" [selected]="p.id === ctx.parcId()">{{ p.name }} ({{ p.counts.total }})</option>
          }
          <option [value]="HORS_PARC" [selected]="ctx.parcId() === HORS_PARC">Bâtiments hors parc</option>
        </select>
      </label>
      @if (avecBatiment) {
        <span class="ctx-sep" aria-hidden="true">›</span>
        <label class="ctx-field grow">
          <span>Bâtiment</span>
          <select [value]="ctx.batimentId() ?? ''" (change)="bat($any($event.target).value)"
                  [disabled]="!ctx.parcId() || !ctx.batiments().length" aria-label="Bâtiment courant">
            <option value="">{{ !ctx.parcId() ? '— choisir d’abord un parc —' : ctx.batiments().length ? '— choisir un bâtiment —' : (ctx.chargement() ? 'chargement…' : 'aucun bâtiment dans ce parc') }}</option>
            @for (b of ctx.batiments(); track b.id) {
              <option [value]="b.id" [selected]="b.id === ctx.batimentId()">{{ b.name || b.address || ('Bâtiment ' + b.id) }}</option>
            }
          </select>
        </label>
      }
      @if (!ctx.parcId()) {
        <a class="rf-btn small" routerLink="/parc">Ouvrir ou créer un parc</a>
      }
    </nav>
  `,
  styles: [`
    .ctx { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--space-3); padding: var(--space-3) var(--space-4);
           background: var(--card); border: 1px solid var(--border); border-radius: var(--radius-md); margin-bottom: var(--space-5); }
    .ctx-field { display: flex; flex-direction: column; gap: var(--space-1); min-width: 12rem; }
    .ctx-field.grow { flex: 1 1 16rem; }
    .ctx-field > span { font-size: var(--text-xs); color: var(--text-mute); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: .04em; }
    .ctx-field select { min-height: 44px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--bg);
                        color: var(--text); padding: 0 var(--space-3); font: inherit; }
    .ctx-sep { color: var(--text-mute); padding-bottom: 12px; font-size: var(--text-lg); }
    @media (max-width: 899px) { .ctx-sep { display: none; } .ctx-field { flex: 1 1 100%; } }
  `],
})
export class ContexteBarComponent {
  protected ctx = inject(ContexteService);
  protected readonly HORS_PARC = HORS_PARC;
  @Input() avecBatiment = true;

  parc(v: string): void { this.ctx.choisirParc(v === '' ? null : Number(v)); }
  bat(v: string): void { this.ctx.choisirBatiment(v === '' ? null : Number(v)); }
}
