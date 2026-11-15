import { Component, Input, OnChanges, computed, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe, UpperCasePipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { Catalogue, CatalogueKey } from '../../core/refonte.types';
import { SerieJourComponent } from '../serie-jour/serie-jour.component';

type Level = 'N0' | 'N1' | 'N2';
interface FactUsed { value: unknown; method: string; source: string; confidence: number }
export interface RunRow {
  id: number; level: Level; status: string; message: string; created_at: string; duration_s: number | null;
  scenario: number | null; scenario_name: string | null; up_to_date: boolean | null; from_fiche: boolean; mode: string;
  indicators: Record<string, number | string | null>; weather: { source?: string; n_hours?: number; warning?: string | null };
  settings: Record<string, unknown>; job: number | null;
}
interface RunDetail extends RunRow {
  results: Record<string, unknown> | null;
  inputs: (Record<string, unknown> & { facts?: Record<string, FactUsed> }) | null;
}
interface Diff { key: string; a: unknown; b: unknown }
interface Comparison {
  a: RunRow; b: RunRow; facts: { key: string; a: FactUsed | null; b: FactUsed | null }[]; inputs: Diff[];
  indicators: { key: string; a: number; b: number; delta: number; pct: number | null }[];
  weather: { a?: string; b?: string } | null;
}

export const LEVEL_LABEL: Record<Level, string> = { N0: 'N0 — mensuel', N1: 'N1 — horaire réduit', N2: 'N2 — 3D par paroi' };
const METHOD_LABEL: Record<string, string> = { mesure: 'mesuré', document: 'document', declare: 'déclaré', infere: 'inféré', defaut: 'par défaut' };
const INDICATORS: [string, string, string][] = [
  ['heating_kwh_m2', 'Besoin de chauffage', 'kWh/m²·an'], ['heating_kwh', 'Besoin de chauffage', 'kWh/an'],
  ['heating_kw_max', 'Puissance maximale', 'kW'], ['cooling_kwh', 'Besoin de froid', 'kWh/an'],
  ['hours_over_26', 'Heures > 26 °C', 'h'], ['hours_over_28', 'Heures > 28 °C', 'h'],
  ['degree_hours_over_26', 'Degrés-heures > 26 °C', '°C·h'], ['t_air_mean', 'Température d’air moyenne', '°C'],
  ['final_kwh_m2', 'Énergie finale', 'kWh/m²·an'], ['primary_kwh_m2', 'Énergie primaire', 'kWh/m²·an'],
  ['co2_kg_m2', 'Émissions', 'kg CO₂/m²·an'], ['cost_eur', 'Coût', '€/an'],
  ['confidence', 'Confiance de la fiche', ''], ['completeness', 'Complétude de la fiche', ''],
];
const BALANCE: Record<string, string> = {
  transmission_kwh: 'Pertes par les parois', ventilation_kwh: 'Air (ventilation, infiltrations)',
  solar_windows_kwh: 'Apports solaires (baies)', solar_opaque_kwh: 'Apports solaires (parois)', internal_kwh: 'Apports internes',
  envelope_kwh: 'Enveloppe (net, vers l’air)', solar_transmitted_kwh: 'Soleil transmis par les vitrages',
  internal_gains_kwh: 'Apports internes', frames_kwh: 'Cadres de fenêtres', hvac_kwh: 'Chauffage / froid',
  storage_kwh: 'Stockage dans l’air', closure_error_kwh: 'Écart de fermeture du bilan',
};

/** Onglet « Calculs » de la fiche (Lot 24) : tous les calculs, le détail de chacun (entrées figées et
 *  résultats complets), la comparaison de deux calculs. */
@Component({
  selector: 'app-calculs',
  standalone: true,
  imports: [DatePipe, DecimalPipe, UpperCasePipe, SerieJourComponent],
  templateUrl: './calculs.component.html',
  styleUrl: './calculs.component.scss',
})
export class CalculsComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  @Input() refresh = 0;
  /** Calcul à ouvrir d'emblée (fin d'une simulation, Lot 25). */
  @Input() openId: number | null = null;
  private opened: number | null = null;
  private rf = inject(RefonteApiService);
  readonly levelLabel = LEVEL_LABEL;
  readonly methodLabel = METHOD_LABEL;
  readonly indicatorDefs = INDICATORS;
  catalogue = signal<Catalogue | null>(null);
  runs = signal<RunRow[]>([]);
  level = signal<'' | Level>('');
  detail = signal<RunDetail | null>(null);
  picked = signal<number[]>([]);
  comparison = signal<Comparison | null>(null);
  error = signal('');
  loading = signal(false);

  ngOnChanges(): void {
    if (!this.catalogue()) this.rf.catalogue().subscribe({ next: c => this.catalogue.set(c) });
    this.load();
    if (this.openId && this.openId !== this.opened) { this.opened = this.openId; this.open(this.openId); }
  }

  load(): void {
    const q = this.level() ? `?niveau=${this.level()}` : '';
    this.rf.get<{ results: RunRow[] }>(`/batiments/${this.buildingId}/calculs/${q}`).subscribe({
      next: r => this.runs.set(r.results ?? []), error: () => this.error.set('Liste des calculs indisponible.'),
    });
  }

  setLevel(v: string): void { this.level.set(v as '' | Level); this.load(); }

  open(id: number): void {
    this.comparison.set(null);
    this.loading.set(true);
    this.rf.get<RunDetail>(`/calculs/${id}/`).subscribe({
      next: d => { this.detail.set(d); this.loading.set(false); },
      error: () => { this.loading.set(false); this.error.set('Calcul introuvable.'); },
    });
  }

  close(): void { this.detail.set(null); this.comparison.set(null); }

  togglePick(id: number): void {
    const p = this.picked();
    this.picked.set(p.includes(id) ? p.filter(x => x !== id) : [...p, id].slice(-2));
  }

  compare(): void {
    const [a, b] = this.picked();
    if (a === undefined || b === undefined) return;
    this.detail.set(null);
    this.rf.get<Comparison>(`/calculs/${a}/comparer/${b}/`).subscribe({
      next: c => this.comparison.set(c), error: e => this.error.set(e?.error?.detail ?? 'Comparaison impossible.'),
    });
  }

  // ── Présentation ──
  private keyMap = computed(() => new Map((this.catalogue()?.keys ?? []).map(k => [k.key, k] as [string, CatalogueKey])));
  keyOf(k: string): CatalogueKey | undefined { return this.keyMap().get(k); }
  label(k: string): string { return this.keyOf(k)?.label ?? k; }

  value(k: string, v: unknown): string {
    if (v === null || v === undefined || v === '') return '—';
    const spec = this.keyOf(k);
    if (spec?.choices && typeof v === 'string') return spec.choices[v] ?? v;
    if (typeof v === 'boolean') return v ? 'oui' : 'non';
    if (typeof v === 'number') {
      const s = Number.isInteger(v) ? String(v) : v.toLocaleString('fr-FR', { maximumFractionDigits: 3 });
      return spec?.unit ? `${s} ${spec.unit}` : s;
    }
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }

  /** Faits utilisés, groupés par famille du catalogue (ordre du catalogue). */
  factGroups(facts: Record<string, FactUsed> | undefined): { block: string; label: string; rows: { key: string; f: FactUsed }[] }[] {
    if (!facts) return [];
    const cat = this.catalogue();
    const order = (cat?.keys ?? []).map(k => k.key);
    const blocks = cat?.blocks ?? [];
    const out = blocks.map(([b, l]) => ({ block: b, label: l, rows: [] as { key: string; f: FactUsed }[] }));
    const other = { block: 'autres', label: 'Autres', rows: [] as { key: string; f: FactUsed }[] };
    const keys = Object.keys(facts).sort((a, b) => (order.indexOf(a) + 1 || 999) - (order.indexOf(b) + 1 || 999));
    for (const k of keys) {
      const g = out.find(x => x.block === this.keyOf(k)?.block) ?? other;
      g.rows.push({ key: k, f: facts[k] });
    }
    return [...out, other].filter(g => g.rows.length);
  }

  num(r: { indicators: Record<string, unknown> } | null, k: string): number | null {
    const v = r?.indicators?.[k];
    return typeof v === 'number' ? v : null;
  }

  indicatorRows(r: RunRow): { label: string; unit: string; v: number; pct: boolean }[] {
    return INDICATORS.filter(([k]) => this.num(r, k) !== null)
      .map(([k, label, unit]) => ({ label, unit, v: this.num(r, k)!, pct: k === 'confidence' || k === 'completeness' }));
  }

  indicatorLabel(k: string): string {
    const d = INDICATORS.find(x => x[0] === k);
    return d ? `${d[1]}${d[2] ? ' (' + d[2] + ')' : ''}` : k;
  }

  balance(d: RunDetail): { label: string; v: number }[] {
    const b = (d.results?.['balance'] ?? {}) as Record<string, number>;
    return Object.keys(b).filter(k => typeof b[k] === 'number').map(k => ({ label: BALANCE[k] ?? k, v: b[k] }));
  }

  monthly(d: RunDetail): number[] { return (d.results?.['monthly_heating_kwh'] as number[]) ?? []; }
  monthMax(d: RunDetail): number { return Math.max(1, ...this.monthly(d)); }
  readonly monthNames = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

  /** Série horaire d'une grandeur ; le N2 de Calcul 3D commence par l'état initial (une valeur de plus). */
  series(d: RunDetail, k: string): number[] {
    const s = (d.results?.[k] as number[] | undefined) ?? [];
    const h = d.results?.['hours'] as number | undefined;
    return h && s.length === h + 1 ? s.slice(1) : s;
  }
  tempKey(d: RunDetail): string { return this.series(d, 't_op').length ? 't_op' : 't_air'; }

  inp(d: RunDetail, path: string): unknown {
    let cur: unknown = d.inputs;
    for (const p of path.split('.')) cur = (cur as Record<string, unknown> | null)?.[p];
    return cur;
  }
  entries(o: unknown): [string, unknown][] {
    return o && typeof o === 'object' ? Object.entries(o as Record<string, unknown>) : [];
  }
  scalar(v: unknown): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') return v.toLocaleString('fr-FR', { maximumFractionDigits: 3 });
    if (typeof v === 'boolean') return v ? 'oui' : 'non';
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }
  pct(v: unknown): string { return typeof v === 'number' ? `${Math.round(v * 100)} %` : '—'; }
  readonly orientations = ['N', 'E', 'S', 'O'];
}
