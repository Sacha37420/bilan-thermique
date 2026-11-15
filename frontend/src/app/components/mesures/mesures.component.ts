import { Component, Input, OnChanges, OnDestroy, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe, DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Meas { id: number; kind: string; energy: string; label: string; period_start: string; period_end: string; n_values: number; }
interface Crit { cv_rmse_pct: number | null; nmbe_pct: number | null; ok: boolean; }
interface Report {
  ok: boolean; reason?: string; before: Crit; after?: Crit; weather?: string;
  parameters?: { key: string; label: string; from: number; to: number }[];
  measured: number[]; model_before: number[]; model_after?: number[]; bills?: { start: string }[];
}
interface Rank { key: string; label: string; mu_star: number; sigma: number; method: string; }
const KIND: Record<string, string> = { facture: 'Factures', courbe: 'Courbe de charge', sonde: 'Sonde', test: 'Test' };
const ENERGY: Record<string, string> = { gaz: 'Gaz', electricite: 'Électricité', fioul: 'Fioul', bois: 'Bois', reseau: 'Réseau de chaleur' };

/** Mesures et calage (refonte, Lot 17). Les mesures sont personnelles ; le calage est une proposition. */
@Component({
  selector: 'app-mesures',
  standalone: true,
  imports: [FormsModule, DecimalPipe, DatePipe],
  templateUrl: './mesures.component.html',
  styleUrl: './mesures.component.scss',
})
export class MesuresComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  private api = inject(ApiService);
  kinds = KIND; energies = ENERGY;
  list = signal<Meas[]>([]);
  ranking = signal<Rank[] | null>(null);
  report = signal<Report | null>(null);
  jobId = signal<number | null>(null);
  jobMsg = signal('');
  error = signal('');
  ok = signal('');
  energy = 'gaz';
  rows: { start: string; end: string; kwh: number | null }[] = [{ start: '', end: '', kwh: null }];
  n50: number | null = null;
  calMeasure: number | null = null;
  private timer?: ReturnType<typeof setInterval>;

  ngOnChanges(): void { this.load(); }
  ngOnDestroy(): void { clearInterval(this.timer); }

  load(): void {
    this.rf.get<{ results: Meas[] }>(`/batiments/${this.buildingId}/mesures/`).subscribe({ next: r => {
      this.list.set(r.results);
      if (!this.calMeasure) this.calMeasure = r.results.find(m => m.kind === 'facture' || m.kind === 'courbe')?.id ?? null;
    } });
  }

  addRow(): void { this.rows.push({ start: '', end: '', kwh: null }); }
  removeRow(i: number): void { this.rows.splice(i, 1); }

  private post(body: Record<string, unknown>, msg: string): void {
    this.error.set(''); this.ok.set('');
    this.rf.post(`/batiments/${this.buildingId}/mesures/`, body).subscribe({
      next: () => { this.ok.set(msg); this.load(); this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Mesure refusée.'),
    });
  }

  saveBills(): void {
    const rows = this.rows.filter(r => r.start && r.end && r.kwh !== null);
    this.post({ kind: 'facture', energy: this.energy, rows, label: 'factures saisies' }, `${rows.length} facture(s) enregistrée(s).`);
  }

  onCurve(ev: Event, kind: 'courbe' | 'sonde'): void {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0];
    if (!f) return;
    f.text().then(csv => { this.post({ kind, energy: this.energy, csv, label: f.name }, `« ${f.name} » importé.`); input.value = ''; });
  }

  saveN50(): void { if (this.n50) this.post({ kind: 'test', n50: this.n50, label: 'saisi' }, 'Test d’étanchéité enregistré (valeur mesurée).'); }

  remove(m: Meas): void { this.rf.del(`/mesures/${m.id}/`).subscribe({ next: () => this.load() }); }

  sensitivity(): void {
    this.ranking.set(null);
    this.rf.post<{ ranking: Rank[] }>(`/batiments/${this.buildingId}/sensibilite/`, {}).subscribe({
      next: r => this.ranking.set(r.ranking), error: e => this.error.set(e?.error?.detail ?? 'Sensibilité impossible.'),
    });
  }

  maxMu(): number { return Math.max(1, ...(this.ranking() ?? []).map(r => r.mu_star)); }

  calibrate(): void {
    if (!this.calMeasure) return;
    this.report.set(null); this.error.set('');
    this.rf.post<{ id: number; message: string }>(`/batiments/${this.buildingId}/calage/`, { measurement_id: this.calMeasure }).subscribe({
      next: j => {
        this.jobId.set(j.id); this.jobMsg.set('Calage en cours…');
        clearInterval(this.timer);
        this.timer = setInterval(() => this.api.getJob(j.id).subscribe({ next: x => {
          const job = x as { status: string; message: string; result: Report | null };
          this.jobMsg.set(job.message);
          if (job.status === 'DONE' || job.status === 'ERROR') { clearInterval(this.timer); if (job.result) this.report.set(job.result); }
        } }), 2000);
      },
      error: e => this.error.set(e?.error?.detail ?? 'Calage refusé.'),
    });
  }

  apply(): void {
    this.rf.post(`/batiments/${this.buildingId}/calage/appliquer/`, { job_id: this.jobId() }).subscribe({
      next: () => { this.ok.set('Valeurs calées enregistrées (« inféré par calage »).'); this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Refusé.'),
    });
  }

  barsMax(r: Report): number { return Math.max(1, ...r.measured, ...r.model_before, ...(r.model_after ?? [])); }
}
