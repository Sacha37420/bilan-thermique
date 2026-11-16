import { Component, Input, OnChanges, OnDestroy, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ContexteService } from '../../core/contexte.service';
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

/** Libellés en mots des niveaux de calcul (contrat bt-navigation §2.1). */
export const NIVEAU_MOTS: Record<'N0' | 'N1' | 'N2', string> = {
  N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi',
};

/**
 * Résultat d'un bâtiment, en lecture seule (chantier bt-navigation) : on ne lance plus rien d'ici,
 * le seul point de lancement est l'onglet Calculs → Simulation.
 * `niveau` null : résumé (N1 en tête, N0 en contrôle, N2 s'il existe) avec lien vers Calculs ;
 * `niveau` posé : résultat de cette seule simulation.
 */
@Component({
  selector: 'app-resultat-carte',
  standalone: true,
  imports: [DecimalPipe, DatePipe, RouterLink],
  templateUrl: './resultat-carte.component.html',
  styleUrl: './resultat-carte.component.scss',
})
export class ResultatCarteComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  @Input() scenarioId: number | null = null;
  /** Incrémenté par la page quand la fiche change ou qu'un calcul se termine : relit le résultat. */
  @Input() refresh = 0;
  @Input() niveau: 'N0' | 'N1' | 'N2' | null = null;
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  readonly mots = NIVEAU_MOTS;
  readonly niveaux = ['N0', 'N1', 'N2'] as const;
  latest = signal<Latest | null>(null);
  months = MONTHS;
  balanceLabels = BALANCE;
  /** N2 en cours (tâche de fond) : suivi affiché, sans bouton. */
  n2Job = signal<JobState | null>(null);
  private poll: ReturnType<typeof setTimeout> | null = null;

  ngOnChanges(): void { this.load(); this.resumeN2(); }
  ngOnDestroy(): void { this.stopPoll(); }

  load(): void {
    const q = this.scenarioId ? `?scenario=${this.scenarioId}` : '';
    this.rf.get<Latest>(`/batiments/${this.buildingId}/resultat/${q}`).subscribe({
      next: l => this.latest.set(l), error: () => this.latest.set(null),
    });
  }

  /** Run principal affiché : celui du niveau demandé, ou N1 (repli N0) en résumé. */
  main(): RunCompact | null {
    const l = this.latest();
    if (!l) return null;
    if (this.niveau) return l[this.niveau];
    return l.N1 ?? l.N0 ?? null;
  }

  private resumeN2(): void {
    this.stopPoll();
    this.n2Job.set(null);
    if (this.niveau && this.niveau !== 'N2') return;
    this.rf.get<{ results: RunCompact[] }>(
      `/batiments/${this.buildingId}/calculs/?niveau=N2&scenario=${this.scenarioId ?? ''}`).subscribe({
      next: r => {
        const run = r.results?.[0];
        if (run && (run.status === 'PENDING' || run.status === 'RUNNING') && run.job) this.follow(run.job);
      },
    });
  }

  private follow(jobId: number): void {
    this.n2Job.set({ status: 'PENDING', progress: 0, message: 'En attente du calculateur…' });
    const tick = () => this.rf.get<JobState>(`/jobs/${jobId}/`).subscribe({
      next: j => {
        this.n2Job.set(j);
        if (j.status === 'DONE' || j.status === 'ERROR') { this.n2Job.set(null); this.load(); }
        else this.poll = setTimeout(tick, 2000);
      },
      error: () => this.n2Job.set(null),
    });
    tick();
  }

  private stopPoll(): void {
    if (this.poll) { clearTimeout(this.poll); this.poll = null; }
  }

  num(r: RunCompact | null | undefined, k: string): number | null {
    const v = r?.indicators?.[k];
    return typeof v === 'number' ? v : null;
  }

  bars(r: RunCompact): { m: string; v: number; h: number }[] {
    const m = r.results?.monthly_heating_kwh ?? [];
    const max = Math.max(1, ...m);
    return m.map((v, i) => ({ m: MONTHS[i] ?? String(i + 1), v, h: Math.round((v / max) * 100) }));
  }

  balance(r: RunCompact): { k: string; label: string; v: number }[] {
    const b = r.results?.balance ?? {};
    return Object.keys(BALANCE).filter(k => b[k] !== undefined).map(k => ({ k, label: BALANCE[k], v: b[k] }));
  }

  /** Écart en % d'un niveau par rapport au run principal. */
  gapTo(other: RunCompact | null | undefined): number | null {
    const a = this.num(this.main(), 'heating_kwh'), b = this.num(other, 'heating_kwh');
    return a && b ? Math.round((b / a - 1) * 100) : null;
  }
}
