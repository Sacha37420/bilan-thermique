import { Component, Input, OnChanges, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe, KeyValuePipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { Catalogue, Fiche } from '../../core/refonte.types';

interface Energy {
  generator: string; energy: string; final_kwh: number; primary_kwh: number; co2_kg: number; cost_eur: number;
  final_kwh_m2: number; primary_kwh_m2: number; co2_kg_m2: number; cop_seasonal?: number;
  by_usage_kwh: Record<string, number>; by_energy_kwh: Record<string, number>;
  dpe_label_model: string; dpe_label_declared?: string; dpe_ep_declared?: number; dpe_gap_pct?: number;
}
const KEYS = ['systeme.generateur', 'systeme.emetteurs', 'systeme.regulation', 'systeme.ecs'];
const USAGE: Record<string, string> = { chauffage: 'Chauffage', ecs: 'Eau chaude', refroidissement: 'Refroidissement', eclairage: 'Éclairage', auxiliaires: 'Auxiliaires' };
const ENERGY: Record<string, string> = { electricite: 'Électricité', gaz: 'Gaz', fioul: 'Fioul', bois: 'Bois', reseau: 'Réseau de chaleur' };

/** Systèmes et consommations (refonte, Lot 16). */
@Component({
  selector: 'app-systemes',
  standalone: true,
  imports: [FormsModule, DecimalPipe, KeyValuePipe],
  templateUrl: './systemes.component.html',
  styleUrl: './systemes.component.scss',
})
export class SystemesComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  @Input() refresh = 0;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  catalogue = signal<Catalogue | null>(null);
  energy = signal<Energy | null>(null);
  form: Record<string, string> = {};
  efficiency: number | null = null;
  error = signal('');
  busy = signal(false);
  keys = KEYS; usageLabels = USAGE; energyLabels = ENERGY;

  ngOnChanges(): void {
    this.rf.catalogue().subscribe({ next: c => this.catalogue.set(c) });
    this.rf.fiche(this.buildingId).subscribe({ next: (f: Fiche) => {
      for (const k of KEYS) this.form[k] = (f.facts[k]?.value as string) ?? '';
      this.efficiency = (f.facts['systeme.rendement_generation']?.value as number) ?? null;
    } });
    this.loadEnergy();
  }

  loadEnergy(): void {
    this.rf.get<Record<string, { results: { energy?: Energy } } | null>>(`/batiments/${this.buildingId}/resultat/`).subscribe({
      next: r => this.energy.set(r['N1']?.results?.energy ?? null),
    });
  }

  spec(k: string) { return this.catalogue()?.keys.find(x => x.key === k); }

  save(): void {
    this.busy.set(true);
    this.error.set('');
    const items: { key: string; value: unknown }[] = KEYS.filter(k => this.form[k]).map(k => ({ key: k, value: this.form[k] }));
    if (this.efficiency) items.push({ key: 'systeme.rendement_generation', value: Number(this.efficiency) });
    let left = items.length;
    const done = () => {
      this.rf.post(`/batiments/${this.buildingId}/calculer/`, { niveaux: ['N0', 'N1'] }).subscribe({
        next: () => { this.busy.set(false); this.loadEnergy(); this.changed.emit(); },
        error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Calcul impossible.'); },
      });
    };
    if (!left) { done(); return; }
    for (const it of items) this.rf.declare(this.buildingId, { ...it, source: 'systèmes' }).subscribe({
      next: () => { if (--left === 0) done(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Valeur refusée.'); },
    });
  }

  share(e: Energy, k: string): number {
    const v = e.by_usage_kwh[k] ?? 0;
    return e.final_kwh ? Math.round((v / e.final_kwh) * 100) : 0;
  }
}
