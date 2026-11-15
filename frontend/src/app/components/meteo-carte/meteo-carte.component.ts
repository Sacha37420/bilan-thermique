import { Component, Input, OnChanges, OnDestroy, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Dataset {
  id: number; kind: string; label: string; stats: Record<string, number>;
  meta: Record<string, unknown>; quality: Record<string, unknown>;
}
interface Station { id: string; name: string; distance_km: number; alt: number; has_radiation: boolean; department: string; warning: string | null; }
interface CompareRow { dataset: Dataset; weather_stats?: Record<string, number>; indicators?: Record<string, number>; error?: string; }
interface JobLite { id: number; status: string; message: string; progress: number; result: Record<string, unknown> | null; }

const KIND: Record<string, string> = { tmy: 'année type', reel: 'réanalyse', station: 'station', tracc: 'climat futur', import: 'import' };

/** Météo du bâtiment (refonte, Lot 13) : choisir, construire, importer, comparer. */
@Component({
  selector: 'app-meteo-carte',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './meteo-carte.component.html',
  styleUrl: './meteo-carte.component.scss',
})
export class MeteoCarteComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  private api = inject(ApiService);
  kinds = KIND;
  datasets = signal<Dataset[]>([]);
  selected = signal<number | null>(null);
  compareIds = signal<Set<number>>(new Set());
  compareRows = signal<CompareRow[] | null>(null);
  stations = signal<Station[] | null>(null);
  job = signal<JobLite | null>(null);
  error = signal('');
  busy = signal(false);
  start = ''; end = ''; scenario = '+2.7'; station: Station | null = null; periodStart = ''; periodEnd = '';
  private timer?: ReturnType<typeof setInterval>;

  ngOnChanges(): void { this.load(); }
  ngOnDestroy(): void { clearInterval(this.timer); }

  load(): void {
    this.rf.get<{ results: Dataset[]; selected: number | null }>(`/meteo/jeux/?batiment=${this.buildingId}`).subscribe({
      next: r => { this.datasets.set(r.results); this.selected.set(r.selected); },
    });
  }

  use(id: number | null): void {
    this.rf.put<{ selected: number | null }>(`/batiments/${this.buildingId}/meteo/`, { dataset_id: id }).subscribe({
      next: r => { this.selected.set(r.selected); this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Refusé.'),
    });
  }

  toggle(id: number): void {
    const s = new Set(this.compareIds());
    if (s.has(id)) s.delete(id); else if (s.size < 4) s.add(id);
    this.compareIds.set(s);
  }

  private follow(job: JobLite, done: (j: JobLite) => void): void {
    this.job.set(job);
    clearInterval(this.timer);
    this.timer = setInterval(() => this.api.getJob(job.id).subscribe({ next: x => {
      const j = x as JobLite;
      this.job.set(j);
      if (j.status === 'DONE' || j.status === 'ERROR') { clearInterval(this.timer); if (j.status === 'DONE') done(j); }
    } }), 1500);
  }

  build(type: string): void {
    this.error.set('');
    const body: Record<string, unknown> = { type, batiment: this.buildingId };
    if (type === 'reel' || type === 'station') { body['start'] = this.start; body['end'] = this.end; }
    if (type === 'station') body['station'] = this.station;
    if (type === 'tracc') body['scenario'] = this.scenario;
    this.rf.post<JobLite>('/meteo/jeux/', body).subscribe({
      next: j => this.follow(j, () => this.load()),
      error: e => this.error.set(e?.error?.detail ?? 'Paramètres refusés.'),
    });
  }

  searchStations(): void {
    this.rf.post<JobLite>('/meteo/stations/', { batiment: this.buildingId }).subscribe({
      next: j => this.follow(j, d => this.stations.set((d.result?.['stations'] as Station[]) ?? [])),
      error: e => this.error.set(e?.error?.detail ?? 'Recherche refusée.'),
    });
  }

  onFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0];
    if (!f) return;
    const format = f.name.toLowerCase().endsWith('.epw') ? 'epw' : 'csv';
    f.text().then(content => this.rf.post<Dataset>('/meteo/importer/', { format, content, batiment: this.buildingId, label: f.name }).subscribe({
      next: () => { input.value = ''; this.load(); },
      error: e => this.error.set(e?.error?.detail ?? 'Fichier refusé.'),
    }));
  }

  compare(): void {
    const ids = [...this.compareIds()];
    if (!ids.length) return;
    this.busy.set(true);
    this.rf.post<{ rows: CompareRow[] }>(`/batiments/${this.buildingId}/comparer-meteo/`,
      { jeux: ids, start: this.periodStart || null, end: this.periodEnd || null }).subscribe({
      next: r => { this.compareRows.set(r.rows); this.busy.set(false); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Comparaison impossible.'); },
    });
  }
}
