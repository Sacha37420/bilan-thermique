import { Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { AmeliorationsApiService } from './ameliorations-api.service';
import { Indicateurs, Niveau, PotentielBatiment, PotentielsBatiment } from './ameliorations.types';
import { EtatCalculsService } from '../resultat-carte/etat-calculs.service';
import {
  NIVEAUX, NIVEAU_MOTS, STATUT_CALCUL_AIDE, STATUT_CALCUL_CLASSE, STATUT_CALCUL_MOTS, StatutCalcul, statutDe,
} from '../resultat-carte/etat-calculs.types';

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
const MAX_PAR_APPEL = 4;
const STATUTS: Record<string, string> = { en_attente: 'en attente', en_cours: 'en cours', fait: 'calculé', erreur: 'erreur' };

interface RunMini { indicators: Indicateurs; up_to_date?: boolean | null; created_at?: string; status?: string }
type Derniers = Partial<Record<Niveau, RunMini | null>>;

/** Une ligne de la grille : l'état actuel ou un bouquet. */
interface Ligne { cle: string; etat: 'reel' | number; nom: string }

/**
 * Potentiels du bâtiment (I5 / I3 bt-trois-niveaux) : l'état actuel comparé à chaque bouquet d'améliorations,
 * aux trois niveaux de calcul (mensuel, horaire simplifié, détaillé par paroi). Les potentiels se calculent
 * d'eux-mêmes (recalcul automatique) ; un gestionnaire peut aussi en lancer un tout de suite.
 * `compact` : la grille seule (onglet Bâtiment) ; sinon grille, détail par niveau, actions et lancements.
 */
@Component({
  selector: 'app-potentiels',
  standalone: true,
  imports: [DecimalPipe, DatePipe],
  templateUrl: './potentiels.component.html',
  styleUrls: ['./potentiels.component.scss', '../resultat-carte/etat-badges.scss'],
})
export class PotentielsComponent {
  readonly buildingId = input.required<number>();
  /** Incrémenté par la page quand un calcul se termine ou qu'une donnée change : relit les potentiels. */
  readonly refresh = input(0);
  readonly compact = input(false);

  private api = inject(AmeliorationsApiService);
  private rf = inject(RefonteApiService);
  private etats = inject(EtatCalculsService);
  readonly MAX = MAX_PAR_APPEL;
  readonly niveaux = NIVEAUX;
  readonly mots = NIVEAU_MOTS;
  readonly statutMots = STATUT_CALCUL_MOTS;
  readonly aides = STATUT_CALCUL_AIDE;
  readonly classes = STATUT_CALCUL_CLASSE;

  data = signal<PotentielsBatiment | null>(null);
  derniers = signal<Derniers>({});
  erreur = signal('');
  busy = signal(false);
  coches = signal<number[]>([]);
  indicateur = signal('heating_kwh_m2');
  niveauDetail = signal<Niveau>('N1');
  private minuteur: ReturnType<typeof setTimeout> | null = null;
  private demande = 0;
  private etat = computed(() => this.etats.batiments()[this.buildingId()]?.etat ?? null);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.arreter());
    let dernierId: number | null = null;
    effect(() => {
      const id = this.buildingId();
      this.refresh();
      untracked(() => {
        if (id !== dernierId) { dernierId = id; this.data.set(null); this.derniers.set({}); this.coches.set([]); this.erreur.set(''); }
        this.charger();
      });
    });
  }

  private arreter(): void { if (this.minuteur) { clearTimeout(this.minuteur); this.minuteur = null; } }

  charger(): void {
    const id = this.buildingId();
    const n = ++this.demande;
    this.api.potentielsBatiment(id).subscribe({
      next: r => { if (n === this.demande) this.recu(r); },
      error: e => { if (n === this.demande) this.echec(e, 'Potentiels indisponibles.'); },
    });
    // Le réel aux trois niveaux (la réponse des potentiels n'en porte qu'un) : derniers calculs du bâtiment.
    this.rf.get<Derniers>(`/batiments/${id}/resultat/`).subscribe({
      next: l => { if (n === this.demande) this.derniers.set(l); },
      error: () => { if (n === this.demande) this.derniers.set({}); },
    });
  }

  private recu(r: PotentielsBatiment): void {
    this.data.set(r);
    this.erreur.set('');
    this.arreter();
    if (!this.coches().length) {
      const ids = [...new Set(r.bouquets.filter(b => b.niveau === 'N1' && !b.a_jour).map(b => b.bouquet.id))];
      this.coches.set(ids.slice(0, MAX_PAR_APPEL));
    }
    if (!this.indicateurs().some(i => i.k === this.indicateur()) && this.indicateurs().length) this.indicateur.set(this.indicateurs()[0].k);
    // Un calcul lancé à la main (réponse « en cours ») : relu toutes les 5 s jusqu'à sa fin.
    if (r.bouquets.some(b => b.statut === 'en_attente' || b.statut === 'en_cours')) {
      this.minuteur = setTimeout(() => this.charger(), 5000);
    }
  }
  private echec(e: { error?: { detail?: string }; status?: number }, repli: string): void {
    this.busy.set(false);
    this.erreur.set(typeof e?.error?.detail === 'string' ? e.error.detail : repli);
  }

  // ── Grille : lignes × niveaux ──
  readonly entrees = computed(() => this.data()?.bouquets ?? []);
  readonly lignes = computed<Ligne[]>(() => {
    const vus = new Map<number, Ligne>();
    for (const b of this.entrees()) if (!vus.has(b.bouquet.id)) vus.set(b.bouquet.id, { cle: `b${b.bouquet.id}`, etat: b.bouquet.id, nom: b.bouquet.nom });
    return [{ cle: 'reel', etat: 'reel', nom: 'État actuel' }, ...vus.values()];
  });
  readonly bouquets = computed(() => this.lignes().filter(l => l.etat !== 'reel'));
  readonly indicateurs = computed(() => {
    const d = this.data();
    if (!d) return [];
    const sources = [d.reel.indicateurs, ...d.bouquets.map(b => b.indicateurs), ...NIVEAUX.map(n => this.derniers()[n]?.indicators ?? null)];
    return INDICS.filter(i => sources.some(s => s && typeof s[i.k] === 'number'));
  });
  readonly indicCourant = computed(() => INDICS.find(i => i.k === this.indicateur()) ?? INDICS[0]);
  readonly n2EnCours = computed(() => this.entrees().some(b => b.niveau === 'N2' && (b.statut === 'en_attente' || b.statut === 'en_cours')));
  readonly trop = computed(() => this.coches().length > MAX_PAR_APPEL);

  entree(bouquet: number, n: Niveau): PotentielBatiment | null {
    return this.entrees().find(b => b.bouquet.id === bouquet && b.niveau === n) ?? null;
  }
  /** Indicateurs du réel à un niveau : la réponse des potentiels si c'est son niveau, sinon le dernier calcul. */
  reel(n: Niveau): Indicateurs | null {
    const d = this.data();
    if (d && d.reel.niveau === n && d.reel.indicateurs) return d.reel.indicateurs;
    return this.derniers()[n]?.indicators ?? null;
  }
  indicateursDe(l: Ligne, n: Niveau): Indicateurs | null {
    return l.etat === 'reel' ? this.reel(n) : this.entree(l.etat, n)?.indicateurs ?? null;
  }

  /** État d'une case : suivi automatique (I2) d'abord, sinon déduit de la réponse des potentiels. */
  statut(l: Ligne, n: Niveau): StatutCalcul | null {
    const c = statutDe(this.etat(), l.etat, n);
    if (c) return c.statut;
    if (l.etat === 'reel') {
      const r = this.derniers()[n];
      if (!r) return 'jamais';
      return r.up_to_date === false ? 'a_refaire' : 'a_jour';
    }
    const e = this.entree(l.etat, n);
    if (!e || e.statut === null) return 'jamais';
    if (e.statut === 'en_attente') return 'en_file';
    if (e.statut === 'en_cours') return 'en_cours';
    if (e.statut === 'erreur') return 'erreur';
    return e.a_jour ? 'a_jour' : 'a_refaire';
  }

  val(ind: Record<string, unknown> | null | undefined, k: string): number | null {
    const x = ind?.[k];
    return typeof x === 'number' ? x : null;
  }
  ecart(l: Ligne, n: Niveau, k: string): number | null {
    if (l.etat === 'reel') return null;
    const x = this.entree(l.etat, n)?.ecarts?.[k];
    return typeof x === 'number' ? x : null;
  }
  pct(l: Ligne, n: Niveau, k: string): number | null {
    if (l.etat === 'reel') return null;
    return this.pctDe(this.entree(l.etat, n), k);
  }
  pctDe(b: PotentielBatiment | null, k: string): number | null {
    const x = b?.ecarts_pct?.[k];
    return typeof x === 'number' ? Math.round(x * 100) : null;
  }
  /** Classe meilleur/pire parmi les bouquets d'un même niveau ayant une valeur (au moins deux). */
  rang(l: Ligne, n: Niveau, k: string): 'best' | 'worst' | '' {
    if (l.etat === 'reel') return '';
    const v = this.val(this.indicateursDe(l, n), k);
    if (v === null) return '';
    const vs = this.bouquets().map(b => this.val(this.indicateursDe(b, n), k)).filter((x): x is number => x !== null);
    if (vs.length < 2) return '';
    const min = Math.min(...vs), max = Math.max(...vs);
    if (min === max) return '';
    return v === min ? 'best' : v === max ? 'worst' : '';
  }

  // ── Détail d'un niveau ──
  readonly colonnesDetail = computed(() => this.entrees().filter(b => b.niveau === this.niveauDetail()));
  /** Actions d'un bouquet (les mêmes à tous les niveaux) : celles de la première entrée qui en porte. */
  actionsDe(bouquet: number): PotentielBatiment['actions'] {
    const es = this.entrees().filter(b => b.bouquet.id === bouquet);
    return (es.find(b => b.niveau === 'N1' && b.actions.length) ?? es.find(b => b.actions.length))?.actions ?? [];
  }
  messageDe(bouquet: number): string {
    return this.entrees().filter(b => b.bouquet.id === bouquet && b.statut === 'erreur' && b.message)
      .map(b => `${NIVEAU_MOTS[b.niveau]} : ${b.message}`).join(' · ');
  }
  statutEntree(b: PotentielBatiment): string { return b.statut === null ? 'pas encore calculé' : STATUTS[b.statut] ?? b.statut; }
  cle(b: PotentielBatiment): string { return `${b.bouquet.id}-${b.niveau}`; }

  // ── Lancements à la main (gestionnaire) ──
  coche(id: number): boolean { return this.coches().includes(id); }
  basculer(id: number, on: boolean): void {
    this.coches.update(c => on ? [...c.filter(x => x !== id), id] : c.filter(x => x !== id));
  }
  calculer(niveau: 'N0' | 'N1'): void {
    const ids = this.coches();
    if (!ids.length || ids.length > MAX_PAR_APPEL) return;
    this.busy.set(true); this.erreur.set('');
    this.api.calculerBatiment(this.buildingId(), ids, niveau).subscribe({
      next: r => { this.busy.set(false); this.recu(r); this.etats.relireBatiment(this.buildingId()); },
      error: e => this.echec(e, 'Calcul des potentiels impossible.'),
    });
  }
  calculerN2(id: number): void {
    this.busy.set(true); this.erreur.set('');
    this.api.calculerBatiment(this.buildingId(), [id], 'N2').subscribe({
      next: r => { this.busy.set(false); this.recu(r); this.etats.relireBatiment(this.buildingId()); },
      error: e => this.echec(e, 'Calcul détaillé impossible.'),
    });
  }
}
