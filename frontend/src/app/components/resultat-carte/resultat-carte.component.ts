import { Component, Input, OnChanges, OnDestroy, inject, signal, output } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

export interface RunCompact {
  id: number; level: 'N0' | 'N1' | 'N2'; status: string; created_at: string; up_to_date?: boolean | null;
  indicators: Record<string, number | string | null>;
  weather: { source?: string; n_hours?: number; warning?: string | null };
  results: { monthly_heating_kwh?: number[]; balance?: Record<string, number>; t_op_max?: number;
             inputs_summary?: Record<string, number> };
  settings?: { engine?: string; mode?: string };
  from_fiche?: boolean;
  duration_s: number | null;
  job?: number | null;
}
type Latest = Record<'N0' | 'N1' | 'N2', RunCompact | null> & { can_n2?: boolean };
interface JobState { status: string; progress: number; message: string }

const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
const BALANCE: Record<string, string> = {
  transmission_kwh: 'Pertes par les parois', ventilation_kwh: 'Pertes par l’air',
  solar_windows_kwh: 'Apports solaires (baies)', solar_opaque_kwh: 'Apports solaires (parois)', internal_kwh: 'Apports internes',
};

/** Résultat courant d'un bâtiment (refonte, Lot 10) : N1 en tête, N0 en contrôle, provenance du calcul. */
@Component({
  selector: 'app-resultat-carte',
  standalone: true,
  imports: [DecimalPipe, DatePipe],
  templateUrl: './resultat-carte.component.html',
  styleUrl: './resultat-carte.component.scss',
})
export class ResultatCarteComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  @Input() scenarioId: number | null = null;
  /** Incrémenté par la page quand la fiche change : relit l'état « à jour / périmé ». */
  @Input() refresh = 0;
  computed = output<void>();
  /** Ouvrir l'onglet Simulation (Lot 25). */
  prepare = output<void>();
  private rf = inject(RefonteApiService);
  latest = signal<Latest | null>(null);
  busy = signal(false);
  error = signal('');
  months = MONTHS;
  balanceLabels = BALANCE;
  /** N2 en cours (Lot 23) : tâche de fond suivie par son job. */
  n2Job = signal<JobState | null>(null);
  n2Error = signal('');
  private poll: ReturnType<typeof setTimeout> | null = null;

  ngOnChanges(): void { this.load(); this.resumeN2(); }
  ngOnDestroy(): void { this.stopPoll(); }

  load(): void {
    const q = this.scenarioId ? `?scenario=${this.scenarioId}` : '';
    this.rf.get<Latest>(`/batiments/${this.buildingId}/resultat/${q}`).subscribe({
      next: l => this.latest.set(l), error: () => this.latest.set(null),
    });
  }

  /** Reprend le suivi d'un N2 lancé avant d'arriver sur la page. */
  private resumeN2(): void {
    this.stopPoll();
    this.n2Job.set(null);
    this.rf.get<{ results: RunCompact[] }>(
      `/batiments/${this.buildingId}/calculs/?niveau=N2&scenario=${this.scenarioId ?? ''}`).subscribe({
      next: r => {
        const run = r.results?.[0];
        if (run && (run.status === 'PENDING' || run.status === 'RUNNING') && run.job) this.follow(run.job);
      },
    });
  }

  computeN2(force = false): void {
    this.n2Error.set('');
    this.rf.post<{ runs: RunCompact[] }>(`/batiments/${this.buildingId}/calculer/`,
      { niveaux: ['N2'], scenario: this.scenarioId, force }).subscribe({
      next: r => {
        const run = r.runs?.[0];
        if (run && run.status !== 'DONE' && run.job) this.follow(run.job);
        else { this.load(); this.computed.emit(); }
      },
      error: e => this.n2Error.set(e?.error?.detail ?? 'Calcul N2 impossible.'),
    });
  }

  private follow(jobId: number): void {
    this.stopPoll();
    this.n2Job.set({ status: 'PENDING', progress: 0, message: 'En attente du calculateur…' });
    const tick = () => this.rf.get<JobState>(`/jobs/${jobId}/`).subscribe({
      next: j => {
        this.n2Job.set(j);
        if (j.status === 'DONE') { this.n2Job.set(null); this.load(); this.computed.emit(); }
        else if (j.status === 'ERROR') { this.n2Job.set(null); this.n2Error.set(j.message || 'Le N2 a échoué.'); }
        else this.poll = setTimeout(tick, 2000);
      },
      error: () => { this.n2Job.set(null); this.n2Error.set('Suivi du calcul impossible.'); },
    });
    tick();
  }

  private stopPoll(): void {
    if (this.poll) { clearTimeout(this.poll); this.poll = null; }
  }

  n2Gap(): number | null {
    const a = this.num(this.latest()?.N1, 'heating_kwh'), b = this.num(this.latest()?.N2, 'heating_kwh');
    return a && b ? Math.round((b / a - 1) * 100) : null;
  }

  compute(force = false): void {
    this.busy.set(true);
    this.error.set('');
    this.rf.post(`/batiments/${this.buildingId}/calculer/`, { niveaux: ['N0', 'N1'], scenario: this.scenarioId, force }).subscribe({
      next: () => { this.busy.set(false); this.load(); this.computed.emit(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Calcul impossible.'); },
    });
  }

  num(r: RunCompact | null | undefined, k: string): number | null {
    const v = r?.indicators?.[k];
    return typeof v === 'number' ? v : null;
  }

  bars(r: RunCompact): { m: string; v: number; h: number }[] {
    const m = r.results.monthly_heating_kwh ?? [];
    const max = Math.max(1, ...m);
    return m.map((v, i) => ({ m: MONTHS[i] ?? String(i + 1), v, h: Math.round((v / max) * 100) }));
  }

  balance(r: RunCompact): { k: string; label: string; v: number }[] {
    const b = r.results.balance ?? {};
    return Object.keys(BALANCE).filter(k => b[k] !== undefined).map(k => ({ k, label: BALANCE[k], v: b[k] }));
  }

  gap(): number | null {
    const a = this.num(this.latest()?.N1, 'heating_kwh'), b = this.num(this.latest()?.N0, 'heating_kwh');
    return a && b ? Math.round((b / a - 1) * 100) : null;
  }
}
