import { Component, Input, OnChanges, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

type Pt = [number, number];
interface GeoPayload {
  auto_footprint_xy: Pt[] | null; auto: Record<string, number>;
  edit: { footprint_xy: Pt[]; eave_height_m: number; levels: number; roof_type: string; roof_pitch_deg: number } | null;
  diff: { changes: { label: string; auto: number; edit: number; unit: string }[]; author: string; at: string; note: string | null;
          envelope_regenerated: boolean } | null;
  current: { eave_height_m: number | null; levels: number | null; roof_type: string; roof_pitch_deg: number; ratios: Record<string, number | null> };
  neighbours: Pt[][]; has_manual_parois: boolean;
}

/** Géométrie (refonte, Lot 15) : l'édition est une surcharge de la géométrie automatique. */
@Component({
  selector: 'app-geometrie',
  standalone: true,
  imports: [FormsModule, DecimalPipe, DatePipe],
  templateUrl: './geometrie.component.html',
  styleUrl: './geometrie.component.scss',
})
export class GeometrieComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  data = signal<GeoPayload | null>(null);
  pts = signal<Pt[]>([]);
  sel = signal<number | null>(null);
  error = signal('');
  ok = signal('');
  eave = 6; levels = 2; roof = 'terrasse'; pitch = 35; regen = false;
  ratios: Record<string, number> = { N: 15, E: 15, S: 15, O: 15 };
  private drag: number | null = null;

  area = computed(() => { const p = this.pts(); let a = 0; for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; a += x1 * y2 - x2 * y1; } return Math.abs(a) / 2; });
  perimeter = computed(() => { const p = this.pts(); let l = 0; for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; l += Math.hypot(x2 - x1, y2 - y1); } return l; });
  view = computed(() => {
    const all = [...this.pts(), ...(this.data()?.neighbours ?? []).flat()];
    const own = this.pts().length ? this.pts() : all;
    if (!own.length) return { x: -10, y: -10, s: 20 };
    const xs = own.map(p => p[0]), ys = own.map(p => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const half = Math.max(8, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 0.8;
    return { x: cx - half, y: -(cy + half), s: 2 * half };
  });

  ngOnChanges(): void { this.load(); }

  load(): void {
    this.rf.get<GeoPayload>(`/batiments/${this.buildingId}/geometrie/`).subscribe({ next: d => this.set(d) });
  }

  private set(d: GeoPayload): void {
    this.data.set(d);
    this.pts.set((d.edit?.footprint_xy ?? d.auto_footprint_xy ?? []).map(p => [p[0], p[1]] as Pt));
    this.eave = d.edit?.eave_height_m ?? d.current.eave_height_m ?? 6;
    this.levels = d.edit?.levels ?? d.current.levels ?? Math.max(1, Math.round(this.eave / 3));
    this.roof = d.edit?.roof_type ?? d.current.roof_type ?? 'terrasse';
    this.pitch = d.edit?.roof_pitch_deg || d.current.roof_pitch_deg || 35;
    for (const o of ['N', 'E', 'S', 'O']) this.ratios[o] = Math.round((d.current.ratios[o] ?? 0.15) * 100);
    this.regen = !d.has_manual_parois;
  }

  poly(p: Pt[]): string { return p.map(q => `${q[0]},${-q[1]}`).join(' '); }
  mids(): { x: number; y: number; i: number }[] {
    const p = this.pts();
    return p.map((q, i) => { const r = p[(i + 1) % p.length]; return { x: (q[0] + r[0]) / 2, y: (q[1] + r[1]) / 2, i }; });
  }

  private toLocal(ev: PointerEvent, el: Element): Pt {
    const svg = el as SVGSVGElement;
    const m = svg.getScreenCTM();
    if (!m) return [0, 0];
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    return [Math.round(pt.x * 10) / 10, Math.round(-pt.y * 10) / 10];
  }

  down(i: number, ev: PointerEvent): void { this.drag = i; this.sel.set(i); (ev.target as Element).setPointerCapture(ev.pointerId); }
  move(ev: PointerEvent, svg: Element): void {
    if (this.drag === null) return;
    const p = [...this.pts()];
    p[this.drag] = this.toLocal(ev, svg);
    this.pts.set(p);
  }
  up(): void { this.drag = null; }
  insert(i: number): void {
    const p = [...this.pts()];
    const m = this.mids()[i];
    p.splice(i + 1, 0, [Math.round(m.x * 10) / 10, Math.round(m.y * 10) / 10]);
    this.pts.set(p);
    this.sel.set(i + 1);
  }
  removeSel(): void {
    const i = this.sel();
    if (i === null || this.pts().length <= 3) return;
    this.pts.set(this.pts().filter((_, k) => k !== i));
    this.sel.set(null);
  }
  nudge(dx: number, dy: number): void {
    const i = this.sel();
    if (i === null) return;
    const p = [...this.pts()];
    p[i] = [Math.round((p[i][0] + dx) * 10) / 10, Math.round((p[i][1] + dy) * 10) / 10];
    this.pts.set(p);
  }

  save(): void {
    this.error.set(''); this.ok.set('');
    this.rf.post<GeoPayload>(`/batiments/${this.buildingId}/geometrie/`, {
      footprint_xy: this.pts(), eave_height_m: this.eave, levels: this.levels, roof_type: this.roof,
      roof_pitch_deg: this.roof === 'terrasse' ? 0 : this.pitch, regenerate_envelope: this.regen,
    }).subscribe({
      next: d => { this.set(d); this.ok.set('Géométrie enregistrée' + (d.diff?.envelope_regenerated ? ', enveloppe régénérée et vérifiée.' : '.')); this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Géométrie refusée.'),
    });
  }

  reset(): void {
    this.rf.del<GeoPayload>(`/batiments/${this.buildingId}/geometrie/`).subscribe({
      next: d => { this.set(d); this.ok.set('Retour à la géométrie automatique.'); this.changed.emit(); },
    });
  }

  saveRatios(): void {
    const calls = ['N', 'E', 'S', 'O'].map(o => this.rf.declare(this.buildingId, { key: `baies.ratio_${o}`, value: this.ratios[o] / 100, source: 'géométrie : taux de vitrage' }));
    let done = 0;
    for (const c of calls) c.subscribe({ next: () => { if (++done === 4) { this.ok.set('Taux de vitrage enregistrés.'); this.changed.emit(); } },
      error: e => this.error.set(e?.error?.detail ?? 'Refusé.') });
  }
}
