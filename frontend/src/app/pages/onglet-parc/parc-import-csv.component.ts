import { Component, inject, output, signal } from '@angular/core';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';

/** Import d'une liste de bâtiments (CSV) dans le parc courant — gestionnaire du parc. */
@Component({
  selector: 'app-parc-import-csv',
  standalone: true,
  styleUrl: './parc-commun.scss',
  template: `
    <p class="rf-hint">Un fichier CSV (séparateur virgule ou point-virgule, 2 Mo au plus), une ligne par bâtiment. Chaque bâtiment
      est ensuite identifié et décrit automatiquement depuis les données ouvertes, puis calculé.</p>
    <details>
      <summary>Colonnes reconnues</summary>
      <div class="rf-table-wrap"><table class="rf-table">
        <thead><tr><th>Colonne</th><th>Contenu</th><th></th></tr></thead>
        <tbody>
          <tr><td><code>adresse</code></td><td>Adresse postale complète (« 12 rue de la Paix 75002 Paris »)</td><td rowspan="3">une des trois au moins</td></tr>
          <tr><td><code>rnb</code></td><td>Identifiant du Référentiel National des Bâtiments</td></tr>
          <tr><td><code>lat</code> + <code>lon</code></td><td>Position en degrés décimaux (WGS84)</td></tr>
          <tr><td><code>nom</code></td><td>Nom affiché dans le tableau</td><td>facultatif</td></tr>
          <tr><td><code>usage</code></td><td>maison, logement_collectif, bureau, enseignement, commerce, sante, autre</td><td>facultatif</td></tr>
          <tr><td><code>surface</code></td><td>Surface chauffée (m²)</td><td>facultatif</td></tr>
          <tr><td><code>annee</code></td><td>Année de construction</td><td>facultatif</td></tr>
          <tr><td><code>energie</code></td><td>Énergie de chauffage</td><td>facultatif</td></tr>
          <tr><td><code>conso</code></td><td>Consommation annuelle connue (kWh)</td><td>facultatif</td></tr>
        </tbody></table></div>
    </details>
    <div class="rf-row">
      <label class="rf-btn primary file">{{ busy() ? 'Import en cours…' : 'Choisir un fichier CSV' }}
        <input type="file" accept=".csv,text/csv" (change)="onFile($event)" [disabled]="busy()" aria-label="Importer un fichier CSV"></label>
    </div>
    @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
    @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }
  `,
})
export class ParcImportCsvComponent {
  private ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  imported = output<void>();
  error = signal('');
  info = signal('');
  busy = signal(false);

  onFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    const id = this.ctx.parcId();
    if (!file || !id || id < 0) return;
    this.error.set(''); this.info.set('');
    if (file.size > 2 * 1024 * 1024) { this.error.set('Fichier trop volumineux (2 Mo au plus).'); input.value = ''; return; }
    this.busy.set(true);
    void file.text().then(text => {
      this.rf.post<{ created: number; errors: { line: number; error: string }[] }>(`/parcs/${id}/importer/`, { csv: text }).subscribe({
        next: r => {
          this.busy.set(false); input.value = '';
          this.info.set(`${r.created} bâtiment(s) importé(s), analyse lancée.` +
            (r.errors.length ? ` ${r.errors.length} ligne(s) écartée(s) : ` + r.errors.slice(0, 3).map(e => `ligne ${e.line} (${e.error})`).join(', ') : ''));
          void this.ctx.rafraichirBatiments(); void this.ctx.rechargerParcs();
          this.imported.emit();
        },
        error: e => { this.busy.set(false); input.value = ''; this.error.set(e?.error?.detail ?? 'Import refusé.'); },
      });
    });
  }
}
