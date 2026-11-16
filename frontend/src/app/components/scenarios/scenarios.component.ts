import { Component, Input, OnChanges, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';

interface Bundle { key: string; label: string; vigilance: string[]; }
interface Sc { id: number; name: string; bundles: string[]; differences: { label: string; value: unknown; unit: string }[];
  vigilance: { bundle: string; message: string }[]; }
interface Row { scenario: number | null; scenario_name: string; weather: number | null; weather_label: string;
  indicators: Record<string, number | string>; error?: string; }
interface Ds { id: number; kind: string; label: string; }

/** Indicateurs du comparateur : clé, libellé, unité, décimales, infobulle. Plus bas = mieux, pour tous. */
const CMP: { k: string; label: string; unit: string; help: string }[] = [
  { k: 'heating_kwh_m2', label: 'Besoin de chauffage', unit: 'kWh/m²·an', help: 'Chaleur à fournir au bâtiment sur une année, par m² chauffé.' },
  { k: 'primary_kwh_m2', label: 'Énergie primaire (estimation)', unit: 'kWh/m²·an', help: 'Énergie prélevée à la source, rendements des systèmes compris. Estimation du modèle, pas un DPE.' },
  { k: 'co2_kg_m2', label: 'Émissions', unit: 'kg CO₂/m²·an', help: 'Gaz à effet de serre liés aux consommations.' },
  { k: 'cost_eur', label: 'Coût de l’énergie', unit: '€/an', help: 'Facture estimée aux tarifs de référence.' },
  { k: 'hours_over_28', label: 'Heures trop chaudes', unit: 'h > 28 °C', help: 'Heures de l’année où la température ressentie dépasse 28 °C.' },
];

/** Améliorations (bouquets de travaux) et comparateur état actuel ↔ amélioration (chantier bt-navigation). */
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
  private ctx = inject(ContexteService);
  readonly cmp = CMP;
  bundles = signal<Bundle[]>([]);
  list = signal<Sc[]>([]);
  datasets = signal<Ds[]>([]);
  rows = signal<Row[] | null>(null);
  vigilance = signal<{ bundle: string; message: string }[]>([]);
  error = signal('');
  busy = signal(false);
  name = '';
  picked: Record<string, boolean> = {};
  /** Amélioration comparée à l'état actuel (une à la fois : un seul comparateur). */
  choisi = signal<number | null>(null);
  futureId: number | null = null;

  ngOnChanges(): void {
    this.rows.set(null);
    this.choisi.set(null);
    this.rf.get<{ bundles: Bundle[] }>(this.urlBouquets()).subscribe({ next: r => this.bundles.set(r.bundles) });
    this.rf.get<{ results: Ds[] }>(`/meteo/jeux/?batiment=${this.buildingId}`).subscribe({ next: r => this.datasets.set(r.results) });
    this.load();
  }

  /** Bouquets avec les réglages du parc (épaisseurs, bouquets désactivés), sinon les défauts. */
  private urlBouquets(): string {
    const p = this.ctx.parcId();
    return p && p !== HORS_PARC ? `/scenarios/bouquets/?parc=${p}` : '/scenarios/bouquets/';
  }

  load(): void {
    this.rf.get<{ results: Sc[] }>(`/batiments/${this.buildingId}/scenarios/`).subscribe({
      next: r => {
        this.list.set(r.results);
        const c = this.choisi();
        if (c === null || !r.results.some(s => s.id === c)) this.choisi.set(r.results[0]?.id ?? null);
      },
    });
  }

  label(k: string): string { return this.bundles().find(b => b.key === k)?.label ?? k; }
  choix(): Sc | null { return this.list().find(s => s.id === this.choisi()) ?? null; }
  choisir(id: number): void { if (id !== this.choisi()) { this.choisi.set(id); this.rows.set(null); } }

  create(): void {
    const bundles = Object.keys(this.picked).filter(k => this.picked[k]);
    if (!bundles.length) { this.error.set('Cochez au moins un bouquet de travaux.'); return; }
    this.error.set('');
    this.rf.post<Sc>(`/batiments/${this.buildingId}/scenarios/`, { name: this.name || bundles.map(b => this.label(b)).join(' + ').slice(0, 140), bundles }).subscribe({
      next: s => { this.name = ''; this.picked = {}; this.choisi.set(s.id); this.rows.set(null); this.load(); },
      error: e => this.error.set(e?.error?.detail ?? 'Amélioration refusée.'),
    });
  }

  remove(s: Sc): void {
    if (!confirm(`Supprimer l'amélioration « ${s.name} » ? Ses calculs ne seront plus comparables.`)) return;
    this.rf.del(`/scenarios/${s.id}/`).subscribe({ next: () => { if (this.choisi() === s.id) { this.choisi.set(null); this.rows.set(null); } this.load(); } });
  }

  compare(): void {
    const id = this.choisi();
    if (id === null) return;
    const scenarios = [null, id];
    this.busy.set(true); this.error.set('');
    const fail = (e: { error?: { detail?: string } }) => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Comparaison impossible.'); };
    this.rf.post<{ rows: Row[]; vigilance: { bundle: string; message: string }[] }>(`/batiments/${this.buildingId}/comparer/`, { scenarios }).subscribe({
      next: r => {
        this.vigilance.set(r.vigilance ?? []);
        const base = r.rows.filter(x => x.weather === null || x.weather === undefined);
        if (!this.futureId) { this.rows.set(base); this.busy.set(false); return; }
        this.rf.post<{ rows: Row[] }>(`/batiments/${this.buildingId}/comparer/`, { scenarios, meteos: [this.futureId] }).subscribe({
          next: f => { this.rows.set([...base, ...f.rows.filter(x => x.weather !== null && x.weather !== undefined)]); this.busy.set(false); },
          error: fail,
        });
      },
      error: fail,
    });
  }

  /** Lignes (état actuel, amélioration) pour une météo donnée (null = météo du bâtiment). */
  pair(weather: number | null): { a: Row | null; b: Row | null } {
    const rs = (this.rows() ?? []).filter(r => (r.weather ?? null) === weather);
    return { a: rs.find(r => r.scenario === null) ?? null, b: rs.find(r => r.scenario !== null) ?? null };
  }
  /** Météos présentes dans la comparaison (la météo du bâtiment d'abord, puis le climat futur). */
  weathers = computed(() => {
    const w = new Set<number | null>([null]);
    for (const r of this.rows() ?? []) w.add(r.weather ?? null);
    return [...w];
  });
  weatherLabel(w: number | null): string {
    if (w === null) return 'Météo du bâtiment';
    return (this.rows() ?? []).find(r => r.weather === w)?.weather_label ?? 'Climat futur';
  }

  v(r: Row | null, k: string): number | null {
    const x = r?.indicators?.[k];
    return typeof x === 'number' ? x : x !== undefined && x !== null && x !== '' && !isNaN(Number(x)) ? Number(x) : null;
  }
  delta(a: Row | null, b: Row | null, k: string): number | null {
    const x = this.v(a, k), y = this.v(b, k);
    return x === null || y === null ? null : y - x;
  }
  pct(a: Row | null, b: Row | null, k: string): number | null {
    const x = this.v(a, k), d = this.delta(a, b, k);
    return x && d !== null ? Math.round((d / x) * 100) : null;
  }
  futures(): Ds[] { return this.datasets().filter(d => d.kind === 'tracc'); }
}
