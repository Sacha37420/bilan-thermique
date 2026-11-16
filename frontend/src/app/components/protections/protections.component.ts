import { Component, Input, OnChanges, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe, NgTemplateOutlet } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { Amelioration, Catalogue, EffectRow, Groupe, Modele, Parametre, Protection, Valeur } from './protections.types';

const ORIENT: Partial<Record<string, string>> = { N: 'nord', E: 'est', S: 'sud', O: 'ouest', all: 'toutes' };
const PILOTAGE: Partial<Record<string, string>> = { fixe: 'toujours dans la position choisie', planning: 'horaires', auto: 'automatique' };
/** Groupes de paramètres de pilotage propres à un mode (les autres valent toujours). */
const GROUPE_MODE: Record<string, string> = { Planning: 'planning', Automatique: 'auto' };

interface Form {
  kind: string; orientation: string; mode: string; label: string; scenario: number | null;
  params: Record<string, Valeur>; control: Record<string, Valeur>;
}

function grouper(ps: Parametre[]): Groupe[] {
  const out: Groupe[] = [];
  for (const p of ps) {
    const nom = p.groupe || 'Réglages';
    let g = out.find(x => x.nom === nom);
    if (!g) out.push(g = { nom, parametres: [] });
    g.parametres.push(p);
  }
  return out;
}

/** Protections solaires : modèles du catalogue serveur, tous leurs paramètres, pilotage, état actuel ou amélioration. */
@Component({
  selector: 'app-protections',
  standalone: true,
  imports: [FormsModule, DecimalPipe, NgTemplateOutlet],
  templateUrl: './protections.component.html',
  styleUrl: './protections.component.scss',
})
export class ProtectionsComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  catalogue = signal<Catalogue | null>(null);
  devices = signal<Protection[]>([]);
  ameliorations = signal<Amelioration[]>([]);
  effect = signal<EffectRow[] | null>(null);
  error = signal('');
  busy = signal(false);
  saving = signal(false);
  editing = signal<number | null>(null);
  open = signal(false);
  form = signal<Form>({ kind: '', orientation: 'S', mode: 'parametrique', label: '', scenario: null, params: {}, control: {} });
  orient = ORIENT;
  pilotageLabel = PILOTAGE;

  modele = computed(() => this.model(this.form().kind));
  groupes = computed(() => grouper(this.modele()?.parametres ?? []));
  groupesPilotage = computed(() => {
    const mode = this.form().control['mode'] as string;
    return grouper((this.modele()?.pilotage.parametres ?? []).filter(p => !GROUPE_MODE[p.groupe] || GROUPE_MODE[p.groupe] === mode));
  });
  famillesModeles = computed(() => {
    const c = this.catalogue();
    return c ? c.familles.map(f => ({ ...f, modeles: c.modeles.filter(m => m.famille === f.cle) })).filter(f => f.modeles.length) : [];
  });
  attachables = computed(() => this.ameliorations().filter(a => !a.genere));

  ngOnChanges(): void {
    if (!this.catalogue()) this.rf.get<Catalogue>('/protections/catalogue/').subscribe({
      next: c => { this.catalogue.set(c); if (!this.form().kind && c.modeles.length) this.setKind(c.modeles[0].cle); },
      error: e => this.error.set(this.msg(e)),
    });
    this.load();
    this.rf.get<{ results: Amelioration[] }>(`/batiments/${this.buildingId}/scenarios/`).subscribe({
      next: r => this.ameliorations.set(r.results), error: () => this.ameliorations.set([]),
    });
  }

  load(): void {
    this.rf.get<{ results: Protection[] }>(`/batiments/${this.buildingId}/protections/`).subscribe({
      next: r => this.devices.set(r.results), error: e => this.error.set(this.msg(e)),
    });
  }

  model(kind: string): Modele | undefined { return this.catalogue()?.modeles.find(m => m.cle === kind); }
  label(kind: string): string { return this.model(kind)?.libelle ?? kind; }

  private defaults(ps: Parametre[], stored: Record<string, Valeur> = {}): Record<string, Valeur> {
    const out: Record<string, Valeur> = {};
    for (const p of ps) out[p.cle] = p.cle in stored ? stored[p.cle] : p.defaut;
    return out;
  }

  /** Changement de modèle : paramètres et pilotage remis aux valeurs par défaut du catalogue. */
  setKind(kind: string): void {
    const m = this.model(kind);
    const control = m?.pilotage.modes.length ? { mode: m.pilotage.modes.includes('planning') ? 'planning' : m.pilotage.modes[0],
      ...this.defaults(m.pilotage.parametres) } : {};
    this.form.update(f => ({ ...f, kind, mode: m?.modes[0] ?? 'parametrique',
      params: this.defaults(m?.parametres ?? []), control }));
  }

  patch(part: Partial<Form>): void { this.form.update(f => ({ ...f, ...part })); }
  setParam(cle: string, v: Valeur): void { this.form.update(f => ({ ...f, params: { ...f.params, [cle]: v } })); }
  setControl(cle: string, v: Valeur): void { this.form.update(f => ({ ...f, control: { ...f.control, [cle]: v } })); }

  niveaux(p: Parametre): string { return p.niveaux.length && p.niveaux.length < 3 ? p.niveaux.join(' · ') : ''; }
  appKeys(m: Modele): string[] { return Object.keys(m.application ?? {}); }

  startAdd(): void {
    this.editing.set(null);
    this.patch({ label: '', scenario: null, orientation: 'S' });
    this.setKind(this.form().kind || this.catalogue()?.modeles[0]?.cle || '');
    this.open.set(true);
  }

  edit(d: Protection): void {
    const m = this.model(d.kind);
    this.editing.set(d.id);
    this.form.set({ kind: d.kind, orientation: d.orientation, mode: d.mode, label: d.label ?? '', scenario: d.scenario,
      params: this.defaults(m?.parametres ?? [], d.params ?? {}),
      control: m?.pilotage.modes.length ? { mode: (d.control?.['mode'] as string) ?? m.pilotage.modes[0],
        ...this.defaults(m.pilotage.parametres, d.control ?? {}) } : {} });
    this.error.set('');
    this.open.set(true);
  }

  cancel(): void { this.editing.set(null); this.open.set(false); this.error.set(''); }

  private clean(ps: Parametre[], vals: Record<string, Valeur>): Record<string, Valeur> {
    const out: Record<string, Valeur> = {};
    for (const p of ps) {
      let v = vals[p.cle];
      if (p.type === 'number' || p.type === 'int') {
        v = v === null || v === undefined || `${v}` === '' ? null : Number(v);
        if (v !== null && p.type === 'int') v = Math.round(v as number);
        if (v !== null && !Number.isFinite(v as number)) v = null;
      } else if (p.type === 'bool') v = !!v;
      out[p.cle] = v ?? null;
    }
    return out;
  }

  submit(): void {
    const m = this.modele();
    if (!m) return;
    const f = this.form();
    const body: Record<string, unknown> = { kind: m.cle, orientation: f.orientation, label: f.label.trim(),
      scenario: f.scenario, params: this.clean(m.parametres, f.params) };
    if (m.modes.includes(f.mode)) body['mode'] = f.mode;
    if (m.pilotage.modes.length) body['control'] = { mode: f.control['mode'], ...this.clean(m.pilotage.parametres, f.control) };
    const id = this.editing();
    const req = id === null ? this.rf.post(`/batiments/${this.buildingId}/protections/`, body) : this.rf.patch(`/protections/${id}/`, body);
    this.error.set('');
    this.saving.set(true);
    req.subscribe({
      next: () => { this.saving.set(false); this.cancel(); this.done(); },
      error: e => { this.saving.set(false); this.error.set(this.msg(e)); },
    });
  }

  attach(d: Protection, scenario: number | null): void {
    this.error.set('');
    this.rf.patch(`/protections/${d.id}/`, { scenario }).subscribe({ next: () => this.done(), error: e => { this.error.set(this.msg(e)); this.load(); } });
  }

  remove(d: Protection): void {
    this.error.set('');
    this.rf.del(`/protections/${d.id}/`).subscribe({
      next: () => { if (this.editing() === d.id) this.cancel(); this.done(); }, error: e => this.error.set(this.msg(e)),
    });
  }

  private done(): void { this.load(); this.effect.set(null); this.changed.emit(); }

  evaluate(): void {
    this.busy.set(true);
    this.error.set('');
    this.rf.post<{ rows: EffectRow[] }>(`/batiments/${this.buildingId}/protections/effet/`, {}).subscribe({
      next: r => { this.effect.set(r.rows); this.busy.set(false); },
      error: e => { this.busy.set(false); this.error.set(this.msg(e)); },
    });
  }

  where(d: Protection): string {
    if (d.scenario === null) return 'état actuel';
    const a = this.ameliorations().find(x => x.id === d.scenario);
    return a ? `amélioration « ${a.name} »` : 'amélioration';
  }

  private fmt(p: Parametre, v: Valeur): string {
    if (p.type === 'bool') return v ? 'oui' : 'non';
    if (p.type === 'enum') return p.choix?.find(c => c.valeur === v)?.libelle ?? String(v);
    return `${String(v).replace('.', ',')}${p.unite ? ' ' + p.unite : ''}`;
  }

  /** Réglages qui diffèrent du catalogue, et pilotage. */
  summary(d: Protection): string {
    const m = this.model(d.kind);
    if (!m) return '';
    const diff = m.parametres.filter(p => d.params?.[p.cle] !== undefined && d.params[p.cle] !== p.defaut)
      .map(p => `${p.libelle} ${this.fmt(p, d.params[p.cle])}`);
    const mode = d.control?.['mode'] as string | undefined;
    return [diff.length ? diff.join(', ') : 'réglages par défaut', mode ? `pilotage ${PILOTAGE[mode] ?? mode}` : '',
      d.mode === 'geometrique' ? 'géométrique (N2)' : ''].filter(Boolean).join(' · ');
  }

  rowLabel(r: EffectRow): string {
    if (r.label === 'sans') return 'Sans protection';
    if (r.label === 'toutes') return 'Toutes les protections';
    const [k, o] = r.label.split(' ');
    return `${this.label(k)} (${ORIENT[o] ?? o})`;
  }

  private msg(e: { error?: Record<string, unknown> }): string {
    const err = e?.error ?? {};
    const first = Object.values(err)[0];
    return (err['detail'] as string) ?? (Array.isArray(first) ? String(first[0]) : 'Refusé.');
  }
}
