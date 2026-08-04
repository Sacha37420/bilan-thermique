import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';

interface LayerInput {
  e: number;
  lam: number;
  rho: number;
  c: number;
  tau: number;
  r: number;
  alpha: number;
}

interface WeatherPoint {
  t_ext: number;
  h_s: number;
  theta_i: number;
  e_dir: number;
  e_dif: number;
}

interface CalculResult {
  x: number[];
  layer_boundary_nodes: number[];
  layer_boundaries_x: number[];
  has_air_node: boolean;
  n_wall_nodes: number;
  temperatures: number[][];
}

interface Series {
  nodeIndex: number;
  label: string;
  colorVar: string;
  path: string;
  lastPoint: { x: number; y: number; value: number };
}

const SERIES_COLOR_VARS = [
  '--series-1', '--series-2', '--series-3', '--series-4',
  '--series-5', '--series-6', '--series-7', '--series-8',
];

const CHART_W = 960;
const CHART_H = 340;
const MARGIN = { top: 16, right: 92, bottom: 32, left: 46 };

@Component({
  selector: 'app-calcul-1d',
  standalone: true,
  imports: [FormsModule, DecimalPipe, RouterLink],
  templateUrl: './calcul-1d.component.html',
  styleUrl: './calcul-1d.component.scss',
})
export class Calcul1DComponent {
  private api = inject(ApiService);

  // ── Paroi ──────────────────────────────────────────────────────────
  layers = signal<LayerInput[]>([
    { e: 0.015, lam: 0.87, rho: 1800, c: 1000, tau: 0, r: 0.3, alpha: 0.7 },
    { e: 0.20,  lam: 1.05, rho: 1400, c: 1000, tau: 0, r: 0.9, alpha: 0.1 },
    { e: 0.10,  lam: 0.035, rho: 20,  c: 1030, tau: 0, r: 0.9, alpha: 0.1 },
    { e: 0.013, lam: 0.25, rho: 850, c: 1000, tau: 0, r: 0.9, alpha: 0.1 },
  ]);

  addLayer(): void {
    this.layers.update(list => [...list, { e: 0.05, lam: 0.5, rho: 800, c: 1000, tau: 0, r: 0.9, alpha: 0.1 }]);
  }

  removeLayer(i: number): void {
    this.layers.update(list => list.filter((_, idx) => idx !== i));
  }

  layerSumWarning(l: LayerInput): boolean {
    return Math.abs(l.tau + l.r + l.alpha - 1) > 0.01;
  }

  // ── Maillage ───────────────────────────────────────────────────────
  dxMax = 0.02;
  wallTiltDeg = 90;

  // ── Conditions aux limites ────────────────────────────────────────
  hE = 25;
  interiorMode: 'imposed' | 'free' = 'imposed';
  tInt = 19;
  hI = 8;
  cAirInt = 50000;
  tInit = 18;

  // ── Météo ──────────────────────────────────────────────────────────
  weather = signal<WeatherPoint[]>([]);
  weatherRaw = '';
  weatherError = signal('');

  parseWeather(): void {
    this.weatherError.set('');
    const text = this.weatherRaw.trim();
    if (!text) {
      this.weather.set([]);
      return;
    }
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    const points: WeatherPoint[] = [];
    for (const line of lines) {
      const delim = line.includes(';') ? ';' : ',';
      const cells = line.split(delim).map(c => c.trim());
      if (cells.length < 5) continue;
      const nums = cells.slice(0, 5).map(c => Number(c.replace(',', '.')));
      if (nums.some(n => Number.isNaN(n))) continue; // ligne d'en-tête ou invalide, ignorée
      const [t_ext, h_s, theta_i, e_dir, e_dif] = nums;
      points.push({ t_ext, h_s, theta_i, e_dir, e_dif });
    }
    if (points.length === 0) {
      this.weatherError.set("Aucune ligne valide reconnue — format attendu par ligne : T_ext, h_s, theta_i, E_dir, E_dif");
    }
    this.weather.set(points);
  }

  loadExample(hours: number): void {
    const rows: string[] = [];
    for (let h = 0; h < hours; h++) {
      const hourOfDay = h % 24;
      const t_ext = 10 + 6 * Math.sin(((hourOfDay - 9) / 24) * 2 * Math.PI);
      const daylight = Math.max(0, Math.sin(((hourOfDay - 6) / 12) * Math.PI));
      const h_s = daylight > 0 ? 60 * daylight : -10;
      const e_dir = daylight > 0 ? 700 * daylight : 0;
      const e_dif = daylight > 0 ? 100 * daylight : 0;
      const theta_i = 30;
      rows.push([t_ext.toFixed(1), h_s.toFixed(1), theta_i.toFixed(1), e_dir.toFixed(0), e_dif.toFixed(0)].join(','));
    }
    this.weatherRaw = rows.join('\n');
    this.parseWeather();
  }

  // Météo constante : mêmes 5 valeurs répétées sur N heures — pratique pour
  // tester un régime établi (comparaison à une solution analytique, par ex.).
  constWeather = { t_ext: 10, h_s: 0, theta_i: 90, e_dir: 0, e_dif: 0, hours: 200 };

  loadConstant(): void {
    const w = this.constWeather;
    const row = [w.t_ext, w.h_s, w.theta_i, w.e_dir, w.e_dif].join(',');
    this.weatherRaw = Array(Math.max(1, Math.round(w.hours))).fill(row).join('\n');
    this.parseWeather();
  }

  // ── Calcul ─────────────────────────────────────────────────────────
  loading = signal(false);
  error = signal('');
  result = signal<CalculResult | null>(null);
  visibleNodes = signal<Set<number>>(new Set());
  hoverHour = signal<number | null>(null);

  canSubmit = computed(() => this.layers().length > 0 && this.weather().length > 0 && !this.loading());

  submit(): void {
    if (!this.canSubmit()) return;
    this.error.set('');
    this.loading.set(true);

    const interior = this.interiorMode === 'imposed'
      ? { mode: 'imposed', h_i: this.hI, t_int: this.tInt }
      : { mode: 'free', h_i: this.hI, c_air_int: this.cAirInt };

    const payload = {
      layers: this.layers(),
      dx_max: this.dxMax,
      h_e: this.hE,
      wall_tilt_deg: this.wallTiltDeg,
      interior,
      t_init: this.tInit,
      weather: this.weather(),
    };

    this.api.runCalcul1D(payload).subscribe({
      next: (res) => {
        const r = res as CalculResult;
        this.result.set(r);
        this.visibleNodes.set(new Set(r.layer_boundary_nodes));
        this.loading.set(false);
      },
      error: (err) => {
        const detail = err?.error?.detail ?? this.firstFieldError(err?.error) ?? "Le calcul a échoué.";
        this.error.set(detail);
        this.loading.set(false);
      },
    });
  }

  private firstFieldError(body: unknown): string | null {
    if (!body || typeof body !== 'object') return null;
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      if (Array.isArray(value) && value.length > 0) return `${key} : ${value[0]}`;
    }
    return null;
  }

  // ── Nœuds sélectionnables (interfaces de couches + nœud d'air) ─────
  nodeOptions = computed(() => {
    const r = this.result();
    if (!r) return [];
    const n = r.layer_boundary_nodes.length;
    const opts = r.layer_boundary_nodes.map((nodeIndex, i) => ({
      nodeIndex,
      label:
        i === 0 ? 'Extérieur (surface)' :
        i === n - 1 ? 'Intérieur (surface)' :
        `Interface couche ${i}/${i + 1}`,
      colorVar: SERIES_COLOR_VARS[i % SERIES_COLOR_VARS.length],
    }));
    if (r.has_air_node) {
      opts.push({
        nodeIndex: r.n_wall_nodes,
        label: 'Air intérieur',
        colorVar: SERIES_COLOR_VARS[opts.length % SERIES_COLOR_VARS.length],
      });
    }
    return opts;
  });

  toggleNode(nodeIndex: number): void {
    this.visibleNodes.update(set => {
      const next = new Set(set);
      if (next.has(nodeIndex)) next.delete(nodeIndex); else next.add(nodeIndex);
      return next;
    });
  }

  // ── Graphique ──────────────────────────────────────────────────────
  chartW = CHART_W;
  chartH = CHART_H;
  plotLeft = MARGIN.left;
  plotTop = MARGIN.top;
  plotWidth = CHART_W - MARGIN.left - MARGIN.right;
  plotHeight = CHART_H - MARGIN.top - MARGIN.bottom;

  private yDomain = computed<[number, number]>(() => {
    const r = this.result();
    const visible = this.visibleNodes();
    if (!r) return [0, 1];
    let min = Infinity, max = -Infinity;
    for (const row of r.temperatures) {
      for (const idx of visible) {
        const v = row[idx];
        if (v === undefined) continue;
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * 0.08;
    return [min - pad, max + pad];
  });

  yTicks = computed(() => {
    const [lo, hi] = this.yDomain();
    const n = 5;
    const ticks: number[] = [];
    for (let i = 0; i <= n; i++) ticks.push(lo + ((hi - lo) * i) / n);
    return ticks;
  });

  private hourCount = computed(() => (this.result()?.temperatures.length ?? 1) - 1);

  xTicks = computed(() => {
    const n = this.hourCount();
    const r = this.result();
    if (!r) return [];
    const step = Math.max(1, Math.ceil(n / 8));
    const ticks: { hour: number; x: number }[] = [];
    for (let h = 0; h <= n; h += step) {
      ticks.push({ hour: h, x: this.xForHour(h) });
    }
    return ticks;
  });

  private xForHour(h: number): number {
    const n = this.hourCount();
    return this.plotLeft + (n === 0 ? 0 : (h / n) * this.plotWidth);
  }

  yForTick(t: number): number {
    return this.yForTemp(t);
  }

  private yForTemp(t: number): number {
    const [lo, hi] = this.yDomain();
    const frac = hi === lo ? 0.5 : (t - lo) / (hi - lo);
    return this.plotTop + this.plotHeight - frac * this.plotHeight;
  }

  series = computed<Series[]>(() => {
    const r = this.result();
    const visible = this.visibleNodes();
    if (!r) return [];
    return this.nodeOptions()
      .filter(opt => visible.has(opt.nodeIndex))
      .map(opt => {
        const pts = r.temperatures.map((row, h) => `${this.xForHour(h)},${this.yForTemp(row[opt.nodeIndex])}`);
        const last = r.temperatures[r.temperatures.length - 1][opt.nodeIndex];
        return {
          nodeIndex: opt.nodeIndex,
          label: opt.label,
          colorVar: opt.colorVar,
          path: 'M ' + pts.join(' L '),
          lastPoint: { x: this.xForHour(this.hourCount()), y: this.yForTemp(last), value: last },
        };
      });
  });

  onChartHover(evt: MouseEvent): void {
    const rect = (evt.currentTarget as Element).getBoundingClientRect();
    const scaleX = this.chartW / rect.width;
    const px = (evt.clientX - rect.left) * scaleX;
    const n = this.hourCount();
    const frac = (px - this.plotLeft) / this.plotWidth;
    const hour = Math.round(frac * n);
    if (hour < 0 || hour > n) { this.hoverHour.set(null); return; }
    this.hoverHour.set(hour);
  }

  onChartLeave(): void {
    this.hoverHour.set(null);
  }

  hoverX = computed(() => {
    const h = this.hoverHour();
    return h === null ? null : this.xForHour(h);
  });

  hoverRows = computed(() => {
    const h = this.hoverHour();
    const r = this.result();
    if (h === null || !r) return [];
    const row = r.temperatures[h];
    return this.series().map(s => ({ label: s.label, colorVar: s.colorVar, value: row[s.nodeIndex] }));
  });

  // ── Table & export ────────────────────────────────────────────────
  tablePreviewLimit = 100;

  tableRows = computed(() => {
    const r = this.result();
    if (!r) return [];
    return r.temperatures.slice(0, this.tablePreviewLimit).map((row, h) => ({ hour: h, values: row }));
  });

  downloadCsv(): void {
    const r = this.result();
    if (!r) return;
    const nodeLabels = ['heure', ...this.nodeOptions().map(o => o.label)];
    const lines = [nodeLabels.join(';')];
    r.temperatures.forEach((row, h) => {
      const cells = [String(h), ...this.nodeOptions().map(o => row[o.nodeIndex].toFixed(2))];
      lines.push(cells.join(';'));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bilan-thermique-resultats.csv';
    a.click();
    URL.revokeObjectURL(url);
  }
}
