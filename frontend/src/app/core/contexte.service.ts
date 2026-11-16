import { Injectable, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { firstValueFrom, filter } from 'rxjs';
import { RefonteApiService } from './refonte-api.service';
import { ParcRow, PortfolioInfo } from './refonte.types';

/** Pseudo-parc : bâtiments rattachés à aucun parc (bâtiments « communs », d'avant la refonte). */
export const HORS_PARC = -1;

const K_PARC = 'bt-parc';
const K_BAT = 'bt-batiment';

function lire(k: string): number | null {
  try {
    const v = Number(localStorage.getItem(k));
    return Number.isFinite(v) && v !== 0 ? v : null;
  } catch { return null; }
}
function ecrire(k: string, v: number | null): void {
  try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, String(v)); } catch { /* stockage indisponible */ }
}

/** Ligne réduite pour un bâtiment hors parc (on ne dispose que de la fiche du bâtiment). */
function ligneHorsParc(b: Record<string, unknown>): ParcRow {
  return {
    id: b['id'] as number, name: (b['name'] as string) ?? '', address: (b['address'] as string) ?? '',
    rnb_id: (b['rnb_id'] as string) ?? '', status: (b['pipeline_status'] as string) ?? '',
    lat: (b['georef_lat'] as number | null) ?? null, lon: (b['georef_lon'] as number | null) ?? null,
    usage: null, surface_m2: (b['surface_ref_m2'] as number | null) ?? null, annee: null,
    heating_kwh_m2: null, p10: null, p90: null, heating_kwh: null, heating_kw_max: null, hours_over_28: null,
    dpe: null, dpe_ep_kwh_m2: null, final_kwh_m2: null, primary_kwh_m2: null, model_label: null,
    confidence: (b['confidence'] as number) ?? 0, completeness: (b['completeness'] as number) ?? 0,
    level: null, computed_at: null, priority: null, errors: {},
  };
}

/**
 * Parc et bâtiment courants, partagés par tous les onglets (refonte de la navigation, 2026-11-16).
 * L'URL (`?p=`, `?b=`) l'emporte à l'arrivée ; sinon le dernier choix (localStorage). Tout choix
 * réécrit l'URL, pour qu'un lien copié rouvre le même bâtiment.
 */
@Injectable({ providedIn: 'root' })
export class ContexteService {
  private rf = inject(RefonteApiService);
  private router = inject(Router);

  readonly parcs = signal<PortfolioInfo[]>([]);
  readonly parcId = signal<number | null>(lire(K_PARC));
  readonly batiments = signal<ParcRow[]>([]);
  readonly batimentId = signal<number | null>(lire(K_BAT));
  readonly chargement = signal(false);
  readonly erreur = signal('');

  readonly parc = computed(() => {
    const id = this.parcId();
    if (id === HORS_PARC) {
      return { id: HORS_PARC, name: 'Bâtiments hors parc', description: '', created_at: '', role: 'gestionnaire_parc',
               counts: { total: this.batiments().length, en_attente: 0, en_cours: 0, complet: 0, incomplet: 0, jamais: 0 } } as PortfolioInfo;
    }
    return this.parcs().find(p => p.id === id) ?? null;
  });
  readonly batiment = computed(() => this.batiments().find(b => b.id === this.batimentId()) ?? null);
  readonly estGestionnaire = computed(() => this.parc()?.role === 'gestionnaire_parc');
  readonly horsParc = computed(() => this.parcId() === HORS_PARC);

  private parcsCharges = false;
  private batimentsDe: number | null = null;

  constructor() {
    this.lireUrl();
    this.router.events.pipe(filter(e => e instanceof NavigationEnd)).subscribe(() => this.lireUrl());
    void this.rechargerParcs();
  }

  /** Lit `?p=`/`?b=` de l'URL courante ; sans eux, réécrit l'URL avec le contexte mémorisé. */
  private lireUrl(): void {
    const q = this.router.parseUrl(this.router.url).queryParams;
    const p = Number(q['p']);
    const b = Number(q['b']);
    if (q['p'] !== undefined && Number.isFinite(p) && p !== 0 && p !== this.parcId()) {
      this.parcId.set(p); ecrire(K_PARC, p);
      if (!(q['b'] !== undefined && b > 0)) { this.batimentId.set(null); ecrire(K_BAT, null); }
      void this.rafraichirBatiments();
    }
    if (q['b'] !== undefined && b > 0 && b !== this.batimentId()) {
      this.batimentId.set(b); ecrire(K_BAT, b);
    }
  }

  async rechargerParcs(): Promise<void> {
    try {
      const r = await firstValueFrom(this.rf.get<{ results: PortfolioInfo[] }>('/parcs/'));
      this.parcs.set(r.results);
      this.parcsCharges = true;
      const id = this.parcId();
      // Parc mémorisé disparu (supprimé, accès retiré) : on l'oublie.
      if (id !== null && id !== HORS_PARC && !r.results.some(p => p.id === id)) {
        this.choisirParc(null);
        return;
      }
      if (this.batimentsDe !== id) await this.rafraichirBatiments();
    } catch {
      this.erreur.set('Impossible de charger la liste des parcs.');
    }
  }

  choisirParc(id: number | null): void {
    if (id === this.parcId()) return;
    this.parcId.set(id); ecrire(K_PARC, id);
    this.batiments.set([]);
    this.batimentId.set(null); ecrire(K_BAT, null);
    this.ecrireUrl();
    void this.rafraichirBatiments();
  }

  choisirBatiment(id: number | null): void {
    this.batimentId.set(id); ecrire(K_BAT, id);
    this.ecrireUrl();
  }

  /** Recharge les bâtiments du parc courant (tableau du parc, ou bâtiments hors parc). */
  async rafraichirBatiments(): Promise<void> {
    const id = this.parcId();
    this.batimentsDe = id;
    if (id === null) { this.batiments.set([]); return; }
    this.chargement.set(true);
    try {
      if (id === HORS_PARC) {
        const all = await firstValueFrom(this.rf.get<Record<string, unknown>[]>('/batiments/'));
        if (this.parcId() !== id) return;
        this.batiments.set(all.filter(b => b['portfolio'] === null || b['portfolio'] === undefined).map(ligneHorsParc));
      } else {
        const r = await firstValueFrom(this.rf.get<{ portfolio: PortfolioInfo; rows: ParcRow[] }>(`/parcs/${id}/tableau/`));
        if (this.parcId() !== id) return;
        this.batiments.set(r.rows);
        this.parcs.update(ps => ps.map(p => (p.id === id ? r.portfolio : p)));
      }
      const b = this.batimentId();
      if (b !== null && !this.batiments().some(x => x.id === b)) await this.retrouverParcDe(b);
    } catch {
      this.erreur.set('Impossible de charger les bâtiments du parc.');
    } finally {
      this.chargement.set(false);
    }
  }

  /** Bâtiment demandé (lien `?b=`) hors du parc courant : on bascule sur son parc s'il est visible. */
  private async retrouverParcDe(b: number): Promise<void> {
    try {
      const bat = await firstValueFrom(this.rf.get<{ portfolio?: number | null }>(`/batiments/${b}/`));
      const p = bat.portfolio ?? HORS_PARC;
      if (p !== this.parcId()) {
        this.parcId.set(p); ecrire(K_PARC, p);
        this.ecrireUrl();
        await this.rafraichirBatiments();
        return;
      }
    } catch { /* bâtiment invisible ou supprimé */ }
    if (!this.batiments().some(x => x.id === b)) { this.batimentId.set(null); ecrire(K_BAT, null); this.ecrireUrl(); }
  }

  /** Paramètres d'URL à joindre à un lien vers un autre onglet. */
  queryParams(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return { p: this.parcId() ?? undefined, b: this.batimentId() ?? undefined, ...extra };
  }

  private ecrireUrl(): void {
    void this.router.navigate([], {
      queryParams: { p: this.parcId() ?? null, b: this.batimentId() ?? null },
      queryParamsHandling: 'merge', replaceUrl: true,
    });
  }

  get parcsPrets(): boolean { return this.parcsCharges; }
}

/** Petite aide pour `ActivatedRoute` : sous-vue `?vue=` avec repli. */
export function vueDe(route: ActivatedRoute, defaut: string): string {
  return route.snapshot.queryParamMap.get('vue') || defaut;
}
