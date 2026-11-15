import { Component, Input, OnChanges, computed, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';

interface Day { i: number; min: number; mean: number; max: number }
const W = 800, H = 220, L = 48, R = 12, T = 12, B = 28;
const MONTH_START = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

/** Série horaire résumée par jour (Lot 24) : moyenne en trait, plage min–max en bande, réticule au survol.
 *  Une seule grandeur, un seul axe ; le titre la nomme (pas de légende pour une série). */
@Component({
  selector: 'app-serie-jour',
  standalone: true,
  imports: [DecimalPipe],
  templateUrl: './serie-jour.component.html',
  styleUrl: './serie-jour.component.scss',
})
export class SerieJourComponent implements OnChanges {
  @Input({ required: true }) values: number[] = [];
  @Input() title = '';
  @Input() unit = '';
  /** Échelle appliquée aux valeurs (ex. 0,001 pour des W affichés en kW). */
  @Input() scale = 1;
  /** 'band' : moyenne + plage ; 'max' : maximum journalier seul (puissance). */
  @Input() mode: 'band' | 'max' = 'band';
  readonly W = W; readonly H = H; readonly L = L; readonly R = R; readonly T = T; readonly B = B;
  days = signal<Day[]>([]);
  hover = signal<Day | null>(null);

  ngOnChanges(): void {
    const v = (this.values ?? []).map(x => x * this.scale);
    const out: Day[] = [];
    for (let d = 0; d * 24 < v.length; d++) {
      const s = v.slice(d * 24, d * 24 + 24).filter(x => Number.isFinite(x));
      if (!s.length) continue;
      out.push({ i: d, min: Math.min(...s), max: Math.max(...s), mean: s.reduce((a, b) => a + b, 0) / s.length });
    }
    this.days.set(out);
    this.hover.set(null);
  }

  private range = computed(() => {
    const d = this.days();
    if (!d.length) return { lo: 0, hi: 1 };
    let lo = Math.min(...d.map(x => this.mode === 'max' ? Math.min(0, x.max) : x.min));
    let hi = Math.max(...d.map(x => x.max));
    if (hi - lo < 1e-6) { hi += 1; lo -= this.mode === 'max' ? 0 : 1; }
    const step = niceStep((hi - lo) / 4);
    return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
  });

  x(i: number): number { const n = Math.max(1, this.days().length - 1); return L + (i / n) * (W - L - R); }
  y(v: number): number { const r = this.range(); return T + (1 - (v - r.lo) / (r.hi - r.lo)) * (H - T - B); }

  ticks = computed(() => {
    const r = this.range() as { lo: number; hi: number; step: number };
    const out: number[] = [];
    for (let v = r.lo; v <= r.hi + 1e-9; v += r.step) out.push(Math.round(v * 1000) / 1000);
    return out;
  });

  months = computed(() => {
    const n = this.days().length;
    if (n < 60) return [] as { x: number; label: string }[];
    return MONTH_START.filter(d => d < n).map((d, k) => ({ x: this.x(d), label: MONTHS[k] }));
  });

  line = computed(() => this.days().map((d, k) => `${k ? 'L' : 'M'}${this.x(k).toFixed(1)},${this.y(this.mode === 'max' ? d.max : d.mean).toFixed(1)}`).join(''));
  band = computed(() => {
    const d = this.days();
    if (this.mode === 'max' || !d.length) return '';
    const up = d.map((p, k) => `${k ? 'L' : 'M'}${this.x(k).toFixed(1)},${this.y(p.max).toFixed(1)}`).join('');
    const down = d.map((p, k) => [k, p] as const).reverse().map(([k, p]) => `L${this.x(k).toFixed(1)},${this.y(p.min).toFixed(1)}`).join('');
    return up + down + 'Z';
  });

  monthly = computed(() => {
    const d = this.days();
    if (d.length < 60) return [];
    return MONTH_START.map((s, k) => {
      const e = MONTH_START[k + 1] ?? 366;
      const m = d.filter(x => x.i >= s && x.i < e);
      if (!m.length) return null;
      return { label: MONTHS[k], min: Math.min(...m.map(x => x.min)), mean: m.reduce((a, x) => a + x.mean, 0) / m.length,
               max: Math.max(...m.map(x => x.max)) };
    }).filter(Boolean) as { label: string; min: number; mean: number; max: number }[];
  });

  onMove(ev: MouseEvent, svg: Element): void {
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const n = this.days().length;
    if (!n) return;
    const k = Math.round(((px - L) / (W - L - R)) * (n - 1));
    this.hover.set(this.days()[Math.min(n - 1, Math.max(0, k))]);
  }

  hoverX(): number { const h = this.hover(); return h ? this.x(this.days().indexOf(h)) : 0; }
  tipLeft(): number { return (this.hoverX() / W) * 100; }
  dayLabel(i: number): string {
    if (this.days().length < 360) return `jour ${i + 1}`;
    let m = 0;
    while (m < 11 && MONTH_START[m + 1] <= i) m++;
    return `${i - MONTH_START[m] + 1} ${MONTHS[m]}`;
  }
}

function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}
