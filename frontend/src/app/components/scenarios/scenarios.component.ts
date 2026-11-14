import { Component, Input, OnChanges, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Bundle { key: string; label: string; vigilance: string[]; }
interface Sc { id: number; name: string; bundles: string[]; differences: { label: string; value: unknown; unit: string }[];
  vigilance: { bundle: string; message: string }[]; }
interface Row { scenario: number | null; scenario_name: string; weather: number | null; weather_label: string;
  indicators: Record<string, number | string>; error?: string; }
interface Ds { id: number; kind: string; label: string; }

/** Scénarios et comparateur (refonte, Lot 18). */
@Component({
  selector: 'app-scenarios',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './scenarios.component.html',
  styleUrl: './scenarios.component.scss',
})
export class ScenariosComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  private rf = inject(RefonteApiService);
  bundles = signal<Bundle[]>([]);
  list = signal<Sc[]>([]);
  datasets = signal<Ds[]>([]);
  rows = signal<Row[] | null>(null);
  vigilance = signal<{ bundle: string; message: string }[]>([]);
  error = signal('');
  busy = signal(false);
  name = '';
  picked: Record<string, boolean> = {};
  selected: Record<number, boolean> = {};
  futureId: number | null = null;

  ngOnChanges(): void {
    this.rf.get<{ bundles: Bundle[] }>('/scenarios/bouquets/').subscribe({ next: r => this.bundles.set(r.bundles) });
    this.rf.get<{ results: Ds[] }>(`/meteo/jeux/?batiment=${this.buildingId}`).subscribe({ next: r => this.datasets.set(r.results) });
    this.load();
  }

  load(): void { this.rf.get<{ results: Sc[] }>(`/batiments/${this.buildingId}/scenarios/`).subscribe({ next: r => this.list.set(r.results) }); }

  label(k: string): string { return this.bundles().find(b => b.key === k)?.label ?? k; }

  create(): void {
    const bundles = Object.keys(this.picked).filter(k => this.picked[k]);
    this.error.set('');
    this.rf.post<Sc>(`/batiments/${this.buildingId}/scenarios/`, { name: this.name || bundles.map(b => this.label(b)).join(' + ').slice(0, 140), bundles }).subscribe({
      next: s => { this.name = ''; this.picked = {}; this.selected[s.id] = true; this.load(); },
      error: e => this.error.set(e?.error?.detail ?? 'Variante refusée.'),
    });
  }

  remove(s: Sc): void { this.rf.del(`/scenarios/${s.id}/`).subscribe({ next: () => this.load() }); }

  compare(): void {
    const ids: (number | null)[] = [null, ...this.list().filter(s => this.selected[s.id]).map(s => s.id)].slice(0, 5);
    const meteos = this.futureId ? [this.futureId] : [];
    this.busy.set(true); this.error.set('');
    const body: Record<string, unknown> = { scenarios: ids };
    if (meteos.length) body['meteos'] = [...meteos];
    const run = (extra: Row[]) => this.rf.post<{ rows: Row[]; vigilance: { bundle: string; message: string }[] }>(
      `/batiments/${this.buildingId}/comparer/`, { scenarios: ids }).subscribe({
      next: r => { this.rows.set([...r.rows, ...extra]); this.vigilance.set(r.vigilance); this.busy.set(false); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Comparaison impossible.'); },
    });
    if (meteos.length) {
      this.rf.post<{ rows: Row[] }>(`/batiments/${this.buildingId}/comparer/`, body).subscribe({
        next: r => run(r.rows.filter(x => x.weather !== null)),
        error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Comparaison impossible.'); },
      });
    } else run([]);
  }

  max(k: string): number { return Math.max(1, ...(this.rows() ?? []).map(r => Number(r.indicators?.[k] ?? 0))); }
  n(r: Row, k: string): number { return Number(r.indicators?.[k] ?? 0); }
  futures(): Ds[] { return this.datasets().filter(d => d.kind === 'tracc'); }
}
