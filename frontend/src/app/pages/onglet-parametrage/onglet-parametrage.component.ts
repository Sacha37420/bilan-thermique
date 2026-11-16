import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DecimalPipe } from '@angular/common';
import { toSignal } from '@angular/core/rxjs-interop';
import { firstValueFrom, map } from 'rxjs';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { USAGE_PROFILES } from '../../core/usage-profiles';
import { VENTILATION_PROFILES } from '../../core/ventilation-profiles';
import { SHADING_PROFILES } from '../../core/shading-profiles';
import { BouquetsEditeurComponent } from '../../components/scenarios/bouquets-editeur.component';
import { CatalogueProtectionsV2 } from '../../components/scenarios/ameliorations.types';
import { EntreeReglage, MODELES, ModeleCalcul, Reglages, ValeurReglage, egal, messageErreur } from './reglages.types';

interface Couche { e: number; lam: number; }
interface ParoiModele { id: number; name: string; description: string; layers: Couche[]; is_glazing?: boolean;
  frame_u?: number | null; frame_fraction?: number | null; }
/** Ligne du catalogue des protections, normalisée (I1 v2, ou ancienne forme `kinds`). */
interface Protection { cle: string; famille: string; libelle: string; description: string; }
interface AncienCatalogue { kinds?: { kind: string; family: string; label: string }[]; }

/** Colonnes du tableau des archétypes (clés `archetypes.<période>.<champ>`). */
const ARCH_COLS: { champ: string; libelle: string }[] = [
  { champ: 'mur_u', libelle: 'Murs (U)' }, { champ: 'toiture_u', libelle: 'Toiture (U)' },
  { champ: 'plancher_u', libelle: 'Plancher bas (U)' }, { champ: 'baies_uw', libelle: 'Fenêtres (Uw)' },
  { champ: 'n50', libelle: 'Fuites d’air (n50)' },
];

/**
 * Onglet Paramétrage (chantier bt-navigation) : pour chaque modèle de calcul, les valeurs que le
 * calcul suppose (isolants, améliorations, menuiseries, archétypes par période, ventilation,
 * rendements), avec le défaut du lab et la valeur retenue par le parc. Le gestionnaire du parc les
 * modifie ; tout reste réversible (D20).
 */
@Component({
  selector: 'app-onglet-parametrage',
  standalone: true,
  imports: [ContexteBarComponent, RouterLink, DecimalPipe, BouquetsEditeurComponent],
  templateUrl: './onglet-parametrage.component.html',
  styleUrl: './onglet-parametrage.component.scss',
})
export class OngletParametrageComponent {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  readonly MODELES = MODELES;
  readonly ARCH_COLS = ARCH_COLS;
  readonly usages = USAGE_PROFILES;
  readonly ventilations = VENTILATION_PROFILES;
  readonly ombrages = SHADING_PROFILES;

  readonly modele = toSignal(this.route.queryParamMap.pipe(map(q => {
    const m = q.get('modele');
    return (m === 'N0' || m === 'N1' || m === 'N2' ? m : 'N1') as ModeleCalcul;
  })), { initialValue: 'N1' as ModeleCalcul });
  readonly modeleInfo = computed(() => MODELES.find(m => m.id === this.modele())!);

  readonly reglages = signal<Reglages | null>(null);
  /** Surcharges en cours d'édition (clé absente = défaut du lab). */
  readonly brouillon = signal<Record<string, ValeurReglage>>({});
  readonly chargement = signal(false);
  readonly erreur = signal('');
  readonly info = signal('');
  readonly enregistrement = signal(false);
  /** Défauts montrés sans parc : lus via un autre parc, jamais modifiables ici. */
  readonly defautsSeuls = signal(false);

  readonly parois = signal<ParoiModele[] | null>(null);
  readonly paroisErreur = signal('');
  readonly protections = signal<Protection[] | null>(null);
  readonly protectionsErreur = signal('');
  /** Libellés des familles de protections, servis par le catalogue (I1 v2). */
  readonly famillesProtection = signal<Record<string, string>>({});

  readonly sansParc = computed(() => this.ctx.parcId() === null || this.ctx.parcId() === HORS_PARC);
  readonly modifiable = computed(() => !this.defautsSeuls() && !!this.reglages()?.peut_modifier);
  readonly modifie = computed(() => !egal(this.brouillon(), this.reglages()?.surcharges ?? {}));
  readonly nbSurcharges = computed(() => Object.keys(this.brouillon()).length);

  private entrees = computed(() => (this.reglages()?.schema ?? []).filter(e => e.modeles.includes(this.modele())));
  /** Groupes (ordre du serveur), archétypes mis à part dans un tableau. */
  readonly groupes = computed(() => {
    const out: { nom: string; entrees: EntreeReglage[] }[] = [];
    for (const e of this.entrees()) {
      if (e.cle.startsWith('archetypes.')) continue;
      let g = out.find(x => x.nom === e.groupe);
      if (!g) { g = { nom: e.groupe, entrees: [] }; out.push(g); }
      g.entrees.push(e);
    }
    return out;
  });
  readonly archetypes = computed(() => {
    const rows: { periode: string; slug: string; cells: Record<string, EntreeReglage> }[] = [];
    for (const e of this.entrees()) {
      if (!e.cle.startsWith('archetypes.')) continue;
      const [, slug, champ] = e.cle.split('.');
      let r = rows.find(x => x.slug === slug);
      if (!r) { r = { periode: e.groupe.replace(/^Archétype\s*/, ''), slug, cells: {} }; rows.push(r); }
      r.cells[champ] = e;
    }
    return rows;
  });
  readonly archNonApplique = computed(() => this.archetypes().some(r => Object.values(r.cells).some(c => !c.applique)));

  private chargeDe: number | null | undefined = undefined;

  constructor() {
    effect(() => {
      const id = this.ctx.parcId();
      const parcs = this.ctx.parcs().map(p => p.id);
      untracked(() => void this.charger(id, parcs));
    });
    effect(() => {
      if (this.modele() === 'N2') untracked(() => this.chargerN2());
    });
  }

  private async charger(id: number | null, parcs: number[]): Promise<void> {
    const reel = id !== null && id !== HORS_PARC;
    const source = reel ? id : (parcs[0] ?? null);
    const cle = reel ? id : (source === null ? null : -source);
    if (cle === this.chargeDe) return;
    this.chargeDe = cle;
    this.erreur.set(''); this.info.set('');
    this.defautsSeuls.set(!reel);
    if (source === null) { this.reglages.set(null); this.brouillon.set({}); return; }
    this.chargement.set(true);
    try {
      const r = await firstValueFrom(this.rf.get<Reglages>(`/parcs/${source}/reglages/parametrage/`));
      if (this.chargeDe !== cle) return;
      this.reglages.set(r);
      this.brouillon.set(reel ? { ...r.surcharges } : {});
    } catch (e) {
      if (this.chargeDe !== cle) return;
      this.reglages.set(null);
      this.erreur.set(messageErreur(e as never, 'Réglages de calcul'));
    } finally {
      this.chargement.set(false);
    }
  }

  private chargerN2(): void {
    if (this.parois() === null) {
      this.rf.get<ParoiModele[] | { results: ParoiModele[] }>('/paroi-modeles/').subscribe({
        next: r => this.parois.set(Array.isArray(r) ? r : r.results ?? []),
        error: e => this.paroisErreur.set(messageErreur(e, 'Bibliothèque des parois')),
      });
    }
    if (this.protections() === null) {
      this.rf.get<CatalogueProtectionsV2 | AncienCatalogue>('/protections/catalogue/').subscribe({
        next: r => {
          if ('modeles' in r && Array.isArray(r.modeles)) {
            this.famillesProtection.set(Object.fromEntries((r.familles ?? []).map(f => [f.cle, f.libelle])));
            this.protections.set(r.modeles.map(m => ({ cle: m.cle, famille: m.famille, libelle: m.libelle, description: m.description ?? '' })));
          } else {
            // Ancienne forme (avant I1 v2) : pas de libellés de famille servis, la clé est montrée telle quelle.
            this.protections.set(((r as AncienCatalogue).kinds ?? []).map(k => ({ cle: k.kind, famille: k.family, libelle: k.label, description: '' })));
          }
        },
        error: e => this.protectionsErreur.set(messageErreur(e, 'Catalogue des protections')),
      });
    }
  }

  choisirModele(m: ModeleCalcul): void {
    void this.router.navigate([], { queryParams: { modele: m }, queryParamsHandling: 'merge', replaceUrl: true });
  }

  // ── Valeurs ──
  defaut(e: EntreeReglage): ValeurReglage { return this.reglages()?.defauts[e.cle] ?? null; }
  valeur(e: EntreeReglage): ValeurReglage {
    const b = this.brouillon();
    return e.cle in b ? b[e.cle] : this.defaut(e);
  }
  estModifie(e: EntreeReglage): boolean { return e.cle in this.brouillon(); }

  poser(e: EntreeReglage, v: ValeurReglage): void {
    this.info.set('');
    this.brouillon.update(b => {
      const n = { ...b };
      if (egal(v, this.defaut(e))) delete n[e.cle]; else n[e.cle] = v;
      return n;
    });
  }
  saisirNombre(e: EntreeReglage, brut: string): void {
    const t = brut.trim().replace(',', '.');
    if (t === '') { if (e.nullable) this.poser(e, null); return; }
    const v = Number(t);
    if (!Number.isFinite(v)) return;
    this.poser(e, e.type === 'int' ? Math.round(v) : v);
  }
  basculerListe(e: EntreeReglage, choix: string, coche: boolean): void {
    const cur = (this.valeur(e) as string[] | null) ?? [];
    this.poser(e, coche ? [...cur.filter(x => x !== choix), choix] : cur.filter(x => x !== choix));
  }
  remettre(e: EntreeReglage): void { this.poser(e, this.defaut(e)); }
  horsBornes(e: EntreeReglage): boolean {
    const v = this.valeur(e);
    if (typeof v !== 'number') return false;
    return (e.min !== null && v < e.min) || (e.max !== null && v > e.max);
  }
  readonly invalide = computed(() => {
    this.brouillon();
    return (this.reglages()?.schema ?? []).some(e => this.horsBornes(e));
  });

  affiche(e: EntreeReglage, v: ValeurReglage): string {
    if (v === null || v === undefined || v === '') return '—';
    if (e.type === 'bool') return v ? 'oui' : 'non';
    if (e.type === 'enum') return e.choix?.[v as string] ?? String(v);
    if (e.type === 'list') return (v as string[]).map(x => e.choix?.[x] ?? x).join(', ') || 'aucune';
    if (typeof v === 'number') return v.toLocaleString('fr-FR', { maximumFractionDigits: 3 });
    return String(v);
  }
  bornes(e: EntreeReglage): string {
    if (e.min === null && e.max === null) return '';
    const f = (x: number | null) => x === null ? '…' : x.toLocaleString('fr-FR');
    return `de ${f(e.min)} à ${f(e.max)}${e.unite ? ' ' + e.unite : ''}`;
  }
  choixDe(e: EntreeReglage): [string, string][] { return Object.entries(e.choix ?? {}); }
  coche(e: EntreeReglage, c: string): boolean { return ((this.valeur(e) as string[] | null) ?? []).includes(c); }
  pas(e: EntreeReglage): string { return e.type === 'int' ? '1' : 'any'; }

  // ── Enregistrement ──
  async enregistrer(): Promise<void> {
    const id = this.ctx.parcId();
    if (!this.modifiable() || id === null || this.invalide()) return;
    this.enregistrement.set(true); this.erreur.set(''); this.info.set('');
    try {
      const r = await firstValueFrom(this.rf.put<Reglages>(`/parcs/${id}/reglages/parametrage/`, { surcharges: this.brouillon() }));
      const frais = r?.schema ? r : await firstValueFrom(this.rf.get<Reglages>(`/parcs/${id}/reglages/parametrage/`));
      this.reglages.set(frais);
      this.brouillon.set({ ...frais.surcharges });
      this.info.set('Réglages enregistrés. Ils s’appliqueront aux prochains calculs et relances des bâtiments du parc.');
    } catch (e) {
      this.erreur.set(messageErreur(e as never, 'Enregistrement'));
    } finally {
      this.enregistrement.set(false);
    }
  }
  toutRemettre(): void {
    if (!this.nbSurcharges()) return;
    if (!confirm('Remettre toutes les valeurs du parc aux défauts du lab ? Il faudra ensuite enregistrer.')) return;
    this.brouillon.set({});
    this.info.set('');
  }
  annuler(): void { this.brouillon.set({ ...(this.reglages()?.surcharges ?? {}) }); this.info.set(''); }

  // ── N2 ──
  /** U indicatif d'un modèle opaque : 1 / (Rsi + Σ e/λ + Rse), Rsi + Rse = 0,17 m²·K/W. */
  uParoi(p: ParoiModele): number | null {
    if (p.is_glazing || !p.layers?.length) return null;
    let r = 0.17;
    for (const c of p.layers) { if (!(c.lam > 0)) return null; r += c.e / c.lam; }
    return 1 / r;
  }
  epaisseurCm(p: ParoiModele): number { return (p.layers ?? []).reduce((s, c) => s + (c.e || 0), 0) * 100; }
}
