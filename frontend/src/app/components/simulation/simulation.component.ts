import { Component, Input, OnChanges, OnDestroy, computed, inject, output, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ContexteService } from '../../core/contexte.service';
import { Catalogue, CatalogueKey } from '../../core/refonte.types';

type Level = 'N0' | 'N1' | 'N2';
interface FactUsed { value: unknown; method: string; source: string; confidence: number }
interface Decisive { key: string; influence_kwh: number; share_pct: number; method: string; confidence: number; value: unknown }
interface Prep {
  level: Level; scenario: number | null; weather: { source?: string; n_hours?: number; warning?: string | null } | null;
  inputs: Record<string, FactUsed>; engine: Record<string, unknown> | null;
  reliability: { confidence: number; completeness: number; nominal_kwh: number | null; spread_pct: number | null;
                 verdict: string | null; verdict_text: string | null; decisive: Decisive[] };
  range: { heating_kwh_m2: number; heating_kwh_m2_p10: number; heating_kwh_m2_p90: number; run: number } | null;
  estimate_s: number; blockers: string[]; can_launch: boolean; already: number | null; busy: boolean;
}
interface JobState { status: string; progress: number; message: string }

const LEVELS: { id: Level; label: string; help: string }[] = [
  { id: 'N0', label: 'Mensuel (N0)', help: 'Bilan mois par mois : un contrôle rapide, en moins d’une seconde.' },
  { id: 'N1', label: 'Horaire simplifié (N1)', help: 'Le bâtiment heure par heure, avec une fourchette (du cas favorable au cas défavorable). Le calcul de référence du parc.' },
  { id: 'N2', label: 'Détaillé par paroi (N2)', help: 'Chaque paroi simulée en 3D, avec l’ombre réelle des voisins. Le plus fin, le plus long ; réservé aux gestionnaires.' },
];
const METHOD_LABEL: Record<string, string> = { mesure: 'mesuré', document: 'document', declare: 'déclaré', infere: 'inféré', defaut: 'par défaut' };

/** Préparer une simulation — quoi, avec quelles entrées, quelle fiabilité — puis la lancer. C'est le SEUL
 *  point de lancement de l'app (onglet Calculs → Simulation, chantier bt-navigation). */
@Component({
  selector: 'app-simulation',
  standalone: true,
  imports: [DecimalPipe, RouterLink],
  templateUrl: './simulation.component.html',
  styleUrl: './simulation.component.scss',
})
export class SimulationComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  @Input() refresh = 0;
  /** Niveau imposé par la page (sélecteur de l'onglet Calculs) : le sélecteur interne est alors masqué. */
  @Input() niveau: Level | null = null;
  /** Calcul terminé (ou déjà fait avec ces entrées) : la page affiche son résultat. */
  launched = output<number>();
  private rf = inject(RefonteApiService);
  protected ctx = inject(ContexteService);
  readonly levels = LEVELS;
  readonly methodLabel = METHOD_LABEL;
  catalogue = signal<Catalogue | null>(null);
  scenarios = signal<{ id: number; name: string }[]>([]);
  level = signal<Level>('N1');
  scenario = signal<number | null>(null);
  prep = signal<Prep | null>(null);
  loading = signal(false);
  error = signal('');
  job = signal<JobState | null>(null);
  private poll: ReturnType<typeof setTimeout> | null = null;

  ngOnChanges(): void {
    if (this.niveau) this.level.set(this.niveau);
    if (!this.catalogue()) this.rf.catalogue().subscribe({ next: c => this.catalogue.set(c) });
    this.rf.get<{ results: { id: number; name: string }[] }>(`/batiments/${this.buildingId}/scenarios/`).subscribe({
      next: r => this.scenarios.set(r.results ?? []),
    });
    this.load();
  }
  ngOnDestroy(): void { if (this.poll) clearTimeout(this.poll); }

  setLevel(l: Level): void { this.level.set(l); this.load(); }
  setScenario(v: string): void { this.scenario.set(v ? Number(v) : null); this.load(); }

  load(): void {
    this.loading.set(true);
    this.error.set('');
    this.rf.get<Prep>(`/batiments/${this.buildingId}/simulation/?niveau=${this.level()}&scenario=${this.scenario() ?? ''}`).subscribe({
      next: p => { this.prep.set(p); this.loading.set(false); },
      error: e => { this.loading.set(false); this.error.set(e?.error?.detail ?? 'Préparation impossible.'); },
    });
  }

  launch(force = false): void {
    this.error.set('');
    this.rf.post<{ runs: { id: number; status: string; job: number | null }[] }>(`/batiments/${this.buildingId}/calculer/`,
      { niveaux: [this.level()], scenario: this.scenario(), force }).subscribe({
      next: r => {
        const run = r.runs?.[0];
        if (!run) return;
        if (run.status !== 'DONE' && run.job) this.follow(run.job, run.id);
        else this.launched.emit(run.id);
      },
      error: e => this.error.set(e?.error?.detail ?? 'Calcul impossible.'),
    });
  }

  private follow(jobId: number, runId: number): void {
    this.job.set({ status: 'PENDING', progress: 0, message: '' });
    const tick = () => this.rf.get<JobState>(`/jobs/${jobId}/`).subscribe({
      next: j => {
        this.job.set(j);
        if (j.status === 'DONE') { this.job.set(null); this.launched.emit(runId); }
        else if (j.status === 'ERROR') { this.job.set(null); this.error.set(j.message || 'Le calcul a échoué.'); }
        else this.poll = setTimeout(tick, 2000);
      },
      error: () => { this.job.set(null); this.error.set('Suivi du calcul impossible.'); },
    });
    tick();
  }

  // ── Présentation ──
  private keyMap = computed(() => new Map((this.catalogue()?.keys ?? []).map(k => [k.key, k] as [string, CatalogueKey])));
  label(k: string): string { return this.keyMap().get(k)?.label ?? k; }
  value(k: string, v: unknown): string {
    if (v === null || v === undefined || v === '') return '—';
    const spec = this.keyMap().get(k);
    if (spec?.choices && typeof v === 'string') return spec.choices[v] ?? v;
    if (typeof v === 'boolean') return v ? 'oui' : 'non';
    if (typeof v === 'number') {
      const s = Number.isInteger(v) ? String(v) : v.toLocaleString('fr-FR', { maximumFractionDigits: 3 });
      return spec?.unit ? `${s} ${spec.unit}` : s;
    }
    return String(v);
  }
  groups(inputs: Record<string, FactUsed>): { label: string; rows: { key: string; f: FactUsed }[] }[] {
    const cat = this.catalogue();
    const order = (cat?.keys ?? []).map(k => k.key);
    return (cat?.blocks ?? []).map(([b, l]) => ({
      label: l,
      rows: Object.keys(inputs).filter(k => this.keyMap().get(k)?.block === b)
        .sort((a, c) => order.indexOf(a) - order.indexOf(c)).map(k => ({ key: k, f: inputs[k] })),
    })).filter(g => g.rows.length);
  }
  methodCounts(inputs: Record<string, FactUsed>): { m: string; n: number }[] {
    const c: Record<string, number> = {};
    for (const f of Object.values(inputs)) c[f.method] = (c[f.method] ?? 0) + 1;
    return ['mesure', 'document', 'declare', 'infere', 'defaut'].filter(m => c[m]).map(m => ({ m, n: c[m] }));
  }
  levelHelp(): string { return LEVELS.find(l => l.id === this.level())?.help ?? ''; }
  duration(s: number): string { return s < 60 ? `≈ ${s} s` : `≈ ${Math.round(s / 60)} min`; }
  eng(path: string): unknown {
    let cur: unknown = this.prep()?.engine;
    for (const p of path.split('.')) cur = (cur as Record<string, unknown> | null)?.[p];
    return cur;
  }
  list(v: unknown): unknown[] { return Array.isArray(v) ? v : []; }
  entries(v: unknown): [string, Record<string, unknown>][] {
    return v && typeof v === 'object' ? Object.entries(v as Record<string, Record<string, unknown>>) : [];
  }
}
