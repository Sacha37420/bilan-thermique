import { Component, ElementRef, ViewChild, computed, inject, signal } from '@angular/core';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { DocumentsComponent } from '../../components/documents/documents.component';
import { ContexteService } from '../../core/contexte.service';
import { IaClesComponent } from './ia-cles.component';
import { IaImportParcComponent } from './ia-import-parc.component';

/** Onglet IA (chantier bt-navigation) : clés et réglages, documents pour tout le parc, documents du bâtiment courant. */
@Component({
  selector: 'app-onglet-ia',
  standalone: true,
  imports: [ContexteBarComponent, DocumentsComponent, IaClesComponent, IaImportParcComponent],
  styles: [`.ancre { scroll-margin-top: var(--space-6); }`],
  template: `
    <section class="rf-page">
      <app-contexte-bar [avecBatiment]="true" />
      <p class="rf-eyebrow">Intelligence artificielle</p>
      <h1>Lire les documents avec votre IA</h1>
      <p class="rf-sub">Votre IA lit les documents (DPE, audits, factures) et propose des valeurs pour la fiche des bâtiments.
        Elle ne calcule rien : chaque valeur n'entre dans une fiche que si vous l'acceptez.</p>

      <app-ia-cles />

      @for (k of [ctx.parcId()]; track k) {
        <app-ia-import-parc #parc (voir)="voir($event)" (depose)="depose($event)" />
      }

      <div #bat class="ancre">
        @if (ctx.batimentId(); as b) {
          @for (k of [cle()]; track k) {
            <app-documents [buildingId]="b" [titre]="'3. Documents d’un bâtiment' + (nomBatiment() ? ' : ' + nomBatiment() : '')" (changed)="parc?.charger()" />
          }
        } @else {
          <section class="rf-card" aria-labelledby="ia-bat">
            <div class="rf-card-head"><h2 id="ia-bat">3. Documents d'un bâtiment</h2></div>
            <p class="rf-hint">Choisissez un bâtiment dans le bandeau (ou « Valider » dans le tableau ci-dessus) pour déposer ses documents,
              les faire lire par votre IA et valider les valeurs proposées.</p>
          </section>
        }
      </div>
    </section>
  `,
})
export class OngletIaComponent {
  protected ctx = inject(ContexteService);
  @ViewChild('bat') bat?: ElementRef<HTMLElement>;
  @ViewChild('parc') parc?: IaImportParcComponent;
  private version = signal(0);

  nomBatiment = computed(() => { const b = this.ctx.batiment(); return b ? (b.name || b.address) : ''; });
  cle = computed(() => `${this.ctx.batimentId()}-${this.version()}`);

  voir(id: number): void {
    this.ctx.choisirBatiment(id);
    setTimeout(() => this.bat?.nativeElement.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  depose(id: number): void { if (id === this.ctx.batimentId()) this.version.update(v => v + 1); }
}
