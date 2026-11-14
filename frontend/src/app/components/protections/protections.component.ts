import { Component, Input, OnChanges, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Kind { kind: string; family: string; label: string; params?: string[]; }
interface Device { id: number; kind: string; orientation: string; mode: string; label: string; params: Record<string, number>;
  control: { mode?: string; hours?: number[]; irradiance_w_m2?: number; t_ext_min_c?: number; position?: number }; }
interface EffectRow { label: string; device_id: number | null; heating_kwh: number; hours_over_26: number; hours_over_28: number;
  degree_hours_26: number; t_op_max: number; solar_by_orientation_kwh: Record<string, number>; }

const PARAM_LABELS: Record<string, string> = {
  depth_m: 'Profondeur (m)', gap_m: 'Hauteur au-dessus de la baie (m)', window_height_m: 'Hauteur de baie (m)',
  window_width_m: 'Largeur de baie (m)', extension_m: 'Débordement latéral (m)', fin_depth_m: 'Profondeur des joues (m)', g: 'Facteur g',
};
const ORIENT: Record<string, string> = { N: 'nord', E: 'est', S: 'sud', O: 'ouest', all: 'toutes' };

/** Protections solaires (refonte, Lot 14) : fixes, mobiles, vitrage, végétation ; effet été / hiver. */
@Component({
  selector: 'app-protections',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './protections.component.html',
  styleUrl: './protections.component.scss',
})
export class ProtectionsComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  kinds = signal<Kind[]>([]);
  devices = signal<Device[]>([]);
  effect = signal<EffectRow[] | null>(null);
  error = signal('');
  busy = signal(false);
  paramLabels = PARAM_LABELS;
  orient = ORIENT;
  form = { kind: 'casquette', orientation: 'S', mode: 'parametrique', params: {} as Record<string, number | null>,
           ctl: 'auto', hours: '13-18', irradiance: 200, t_min: 20 };

  ngOnChanges(): void {
    if (!this.kinds().length) this.rf.get<{ kinds: Kind[] }>('/protections/catalogue/').subscribe({ next: c => this.kinds.set(c.kinds) });
    this.load();
  }

  load(): void {
    this.rf.get<{ results: Device[] }>(`/batiments/${this.buildingId}/protections/`).subscribe({ next: r => this.devices.set(r.results) });
  }

  current(): Kind | undefined { return this.kinds().find(k => k.kind === this.form.kind); }
  label(kind: string): string { return this.kinds().find(k => k.kind === kind)?.label ?? kind; }

  hoursList(): number[] {
    const out: number[] = [];
    for (const part of this.form.hours.split(',')) {
      const [a, b] = part.split('-').map(x => parseInt(x.trim(), 10));
      if (Number.isFinite(a)) for (let h = a; h <= (Number.isFinite(b) ? b : a); h++) if (h >= 0 && h <= 23) out.push(h);
    }
    return out;
  }

  add(): void {
    const k = this.current();
    if (!k) return;
    const params: Record<string, number> = {};
    for (const p of k.params ?? []) { const v = this.form.params[p]; if (v !== null && v !== undefined && `${v}` !== '') params[p] = Number(v); }
    const body: Record<string, unknown> = { kind: k.kind, orientation: this.form.orientation, params,
      mode: k.family === 'fixe' ? this.form.mode : 'parametrique' };
    if (k.family === 'mobile') body['control'] = this.form.ctl === 'auto'
      ? { mode: 'auto', irradiance_w_m2: this.form.irradiance, t_ext_min_c: this.form.t_min }
      : { mode: 'planning', hours: this.hoursList() };
    this.error.set('');
    this.rf.post(`/batiments/${this.buildingId}/protections/`, body).subscribe({
      next: () => { this.load(); this.effect.set(null); this.changed.emit(); },
      error: e => this.error.set(this.msg(e)),
    });
  }

  remove(d: Device): void {
    this.rf.del(`/protections/${d.id}/`).subscribe({ next: () => { this.load(); this.effect.set(null); this.changed.emit(); } });
  }

  evaluate(): void {
    this.busy.set(true);
    this.rf.post<{ rows: EffectRow[] }>(`/batiments/${this.buildingId}/protections/effet/`, {}).subscribe({
      next: r => { this.effect.set(r.rows); this.busy.set(false); },
      error: e => { this.busy.set(false); this.error.set(this.msg(e)); },
    });
  }

  summary(d: Device): string {
    const p = Object.entries(d.params || {}).map(([k, v]) => `${(PARAM_LABELS[k] ?? k).replace(/ \(.*\)/, '')} ${v}`).join(', ');
    const c = d.control?.mode === 'auto' ? `automatique > ${d.control.irradiance_w_m2} W/m²` :
      d.control?.hours?.length ? `fermé ${d.control.hours[0]}–${d.control.hours[d.control.hours.length - 1] + 1} h` : '';
    return [p, c, d.mode === 'geometrique' ? 'géométrique (N2)' : ''].filter(Boolean).join(' · ');
  }

  private msg(e: { error?: Record<string, unknown> }): string {
    const err = e?.error ?? {};
    const first = Object.values(err)[0];
    return (err['detail'] as string) ?? (Array.isArray(first) ? String(first[0]) : 'Refusé.');
  }
}
