import { Component, DestroyRef, Input, OnChanges, computed, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { AmeliorationsApiService } from './ameliorations-api.service';
import { PotentielBatiment, PotentielsBatiment } from './ameliorations.types';

/** Présentation seulement (libellé, unité) : on n'affiche que les clés présentes dans la réponse. Plus bas = mieux. */
const INDICS: { k: string; libelle: string; unite: string }[] = [
  { k: 'heating_kwh_m2', libelle: 'Besoin de chauffage', unite: 'kWh/m²·an' },
  { k: 'primary_kwh_m2', libelle: 'Énergie primaire', unite: 'kWh/m²·an' },
  { k: 'co2_kg_m2', libelle: 'Émissions', unite: 'kg CO₂/m²·an' },
  { k: 'cost_eur', libelle: 'Coût de l’énergie', unite: '€/an' },
  { k: 'dh_re2020', libelle: 'Degrés-heures d’inconfort (RE2020)', unite: '°C·h' },
  { k: 'heures_inconfort', libelle: 'Heures d’inconfort', unite: 'h' },
  { k: 'hours_over_28', libelle: 'Heures trop chaudes', unite: 'h > 28 °C' },
];
const MAX_N1 = 4;
const STATUTS: Record<string, string> = { en_attente: 'en attente', en_cours: 'en cours', fait: 'calculé', erreur: 'erreur' };

/** Potentiels du bâtiment (I5) : état actuel comparé à chaque bouquet d'améliorations. */
@Component({
  selector: 'app-potentiels',
  standalone: true,
  imports: [DecimalPipe, DatePipe],
  templateUrl: './potentiels.component.html',
  styleUrl: './potentiels.component.scss',
})
export class PotentielsComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  private api = inject(AmeliorationsApiService);
  readonly MAX = MAX_N1;

  data = signal<PotentielsBatiment | null>(null);
  erreur = signal('');
  busy = signal(false);
  coches = signal<number[]>([]);
  private minuteur: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.arreter());
  }

  ngOnChanges(): void {
    this.data.set(null); this.coches.set([]); this.erreur.set('');
    this.charger();
  }

  private arreter(): void { if (this.minuteur) { clearTimeout(this.minuteur); this.minuteur = null; } }

  charger(): void {
    const id = this.buildingId;
    this.api.potentielsBatiment(id).subscribe({
      next: r => { if (id === this.buildingId) this.recu(r); },
      error: e => this.echec(e, 'Potentiels indisponibles.'),
    });
  }

  private recu(r: PotentielsBatiment): void {
    this.data.set(r);
    this.arreter();
    if (r.bouquets.some(b => b.statut === 'en_attente' || b.statut === 'en_cours')) {
      this.minuteur = setTimeout(() => this.charger(), 5000);
    }
  }
  private echec(e: { error?: { detail?: string } }, repli: string): void {
    this.busy.set(false);
    this.erreur.set(e?.error?.detail ?? repli);
  }

  colonnes = computed(() => this.data()?.bouquets ?? []);
  /** Une entrée N1 par bouquet : c'est elle qu'on coche. */
  n1 = computed(() => this.colonnes().filter(b => b.niveau === 'N1'));
  indicateurs = computed(() => {
    const d = this.data();
    if (!d) return [];
    const sources = [d.reel.indicateurs, ...d.bouquets.map(b => b.indicateurs)];
    return INDICS.filter(i => sources.some(s => s && typeof s[i.k] === 'number'));
  });
  n2EnCours = computed(() => this.colonnes().some(b => b.niveau === 'N2' && (b.statut === 'en_attente' || b.statut === 'en_cours')));
  trop = computed(() => this.coches().length > MAX_N1);

  val(ind: Record<string, unknown> | null | undefined, k: string): number | null {
    const x = ind?.[k];
    return typeof x === 'number' ? x : null;
  }
  /** Classe meilleur/pire parmi les bouquets ayant une valeur (au moins deux). */
  rang(b: PotentielBatiment, k: string): 'best' | 'worst' | '' {
    const v = this.val(b.indicateurs, k);
    if (v === null) return '';
    const vs = this.colonnes().map(c => this.val(c.indicateurs, k)).filter((x): x is number => x !== null);
    if (vs.length < 2) return '';
    const min = Math.min(...vs), max = Math.max(...vs);
    if (min === max) return '';
    return v === min ? 'best' : v === max ? 'worst' : '';
  }
  pct(b: PotentielBatiment, k: string): number | null {
    const x = b.ecarts_pct?.[k];
    return typeof x === 'number' ? Math.round(x * 100) : null;
  }
  statut(b: PotentielBatiment): string { return b.statut === null ? 'pas encore calculé' : STATUTS[b.statut] ?? b.statut; }
  cle(b: PotentielBatiment): string { return `${b.bouquet.id}-${b.niveau}`; }

  coche(id: number): boolean { return this.coches().includes(id); }
  basculer(id: number, on: boolean): void {
    this.coches.update(c => on ? [...c.filter(x => x !== id), id] : c.filter(x => x !== id));
  }

  calculerN1(): void {
    const ids = this.coches();
    if (!ids.length || ids.length > MAX_N1) return;
    this.busy.set(true); this.erreur.set('');
    this.api.calculerBatiment(this.buildingId, ids, 'N1').subscribe({
      next: r => { this.busy.set(false); this.coches.set([]); this.recu(r); },
      error: e => this.echec(e, 'Calcul des potentiels impossible.'),
    });
  }
  calculerN2(id: number): void {
    this.busy.set(true); this.erreur.set('');
    this.api.calculerBatiment(this.buildingId, [id], 'N2').subscribe({
      next: r => { this.busy.set(false); this.recu(r); },
      error: e => this.echec(e, 'Calcul détaillé impossible.'),
    });
  }
}
