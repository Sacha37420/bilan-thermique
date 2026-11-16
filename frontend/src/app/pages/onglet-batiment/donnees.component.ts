import { Component, Input, OnChanges, SimpleChanges, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe, DecimalPipe, KeyValuePipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { Catalogue, CatalogueKey, Fact, Fiche, METHOD_LABELS, Method } from '../../core/refonte.types';

/**
 * Faits d'un bâtiment par bloc du catalogue, chacun avec sa provenance (reprise de l'onglet
 * « Données » de l'ancienne fiche). Corriger = poser une surcharge ; « Revenir à l'automatique » la
 * retire (D20). `blocs` restreint l'affichage (vue Physique) ; `cle` ouvre directement une ligne.
 */
@Component({
  selector: 'app-bt-donnees',
  standalone: true,
  imports: [FormsModule, DatePipe, DecimalPipe, KeyValuePipe],
  templateUrl: './donnees.component.html',
  styleUrl: './donnees.component.scss',
})
export class DonneesComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  /** Blocs du catalogue à montrer (null = tous). */
  @Input() blocs: string[] | null = null;
  /** Clé à ouvrir en édition à l'arrivée (`?cle=`). */
  @Input() cle: string | null = null;
  @Input() refresh = 0;
  changed = output<void>();

  private rf = inject(RefonteApiService);
  readonly methodLabels = METHOD_LABELS;
  catalogue = signal<Catalogue | null>(null);
  fiche = signal<Fiche | null>(null);
  error = signal('');
  saving = signal(false);
  editing = signal<string | null>(null);
  editValue: unknown = null;
  editMethod: Method = 'declare';
  editSource = '';
  history = signal<{ key: string; rows: Fact[] } | null>(null);
  private cleOuverte: string | null = null;

  blocks = computed(() => {
    const cat = this.catalogue();
    if (!cat) return [];
    return cat.blocks.filter(([id]) => !this.blocs || this.blocs.includes(id))
      .map(([id, label]) => ({ id, label, keys: cat.keys.filter(k => k.block === id) }));
  });

  ngOnChanges(ch: SimpleChanges): void {
    if (!this.catalogue()) this.rf.catalogue().subscribe({ next: c => { this.catalogue.set(c); this.ouvrirCle(); } });
    if (ch['buildingId']) { this.editing.set(null); this.history.set(null); this.cleOuverte = null; }
    this.load();
  }

  load(): void {
    this.rf.fiche(this.buildingId).subscribe({
      next: f => { this.fiche.set(f); this.ouvrirCle(); },
      error: () => this.error.set('Données introuvables pour ce bâtiment.'),
    });
  }

  /** `?cle=mur.u` : ouvre la ligne en édition et l'amène à l'écran (une seule fois par clé). */
  private ouvrirCle(): void {
    const key = this.cle;
    if (!key || key === this.cleOuverte || !this.catalogue() || !this.fiche()) return;
    const k = this.catalogue()!.keys.find(x => x.key === key);
    if (!k) return;
    this.cleOuverte = key;
    this.startEdit(k);
    setTimeout(() => document.getElementById(this.rowId(key))?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
  }

  rowId(key: string): string { return 'cle-' + key.replace(/[^a-z0-9_-]/gi, '-'); }

  fact(key: string): Fact | undefined { return this.fiche()?.facts[key]; }

  display(k: CatalogueKey, v: unknown): string {
    if (v === null || v === undefined || v === '') return '—';
    if (k.kind === 'enum') return k.choices?.[String(v)] ?? String(v);
    if (k.kind === 'bool') return v ? 'oui' : 'non';
    if (typeof v === 'number') {
      const s = Math.abs(v) >= 100 ? v.toFixed(0) : (Math.round(v * 1000) / 1000).toString().replace('.', ',');
      return k.unit ? `${s} ${k.unit}` : s;
    }
    return String(v);
  }

  startEdit(k: CatalogueKey): void {
    const f = this.fact(k.key);
    this.editing.set(k.key);
    this.editValue = f?.value ?? (k.kind === 'bool' ? false : null);
    this.editMethod = 'declare';
    this.editSource = '';
    this.error.set('');
  }

  save(k: CatalogueKey): void {
    let value: unknown = this.editValue;
    if ((k.kind === 'number' || k.kind === 'int') && value !== null && value !== '') value = Number(value);
    if (value === '') value = null;
    this.saving.set(true);
    this.rf.declare(this.buildingId, { key: k.key, value, method: this.editMethod, source: this.editSource || 'saisie' }).subscribe({
      next: f => { this.fiche.set(f); this.editing.set(null); this.saving.set(false); this.changed.emit(); },
      error: e => { this.error.set(e?.error?.detail ?? 'Valeur refusée.'); this.saving.set(false); },
    });
  }

  backToAuto(f: Fact): void {
    this.rf.retire(f.id).subscribe({
      next: fi => { this.fiche.set(fi); this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Impossible de retirer cette valeur.'),
    });
  }

  showHistory(k: CatalogueKey): void {
    if (this.history()?.key === k.key) { this.history.set(null); return; }
    this.rf.history(this.buildingId, k.key).subscribe({ next: r => this.history.set({ key: k.key, rows: r.results }) });
  }
}
