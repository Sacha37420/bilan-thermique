import { Component, computed, inject, input, signal } from '@angular/core';
import { IndicateurCanon, RenduApiService } from './rendu-api.service';
import { PotentielBatiment, PotentielsBatiment } from './contrat.types';

const OBJECTIFS: Record<string, string> = { energie: 'Économiser l’énergie', confort: 'Confort d’été', libre: 'Libre' };
/** Indicateurs montrés pour chaque potentiel quand le serveur ne sert pas d'écart sur eux. */
const PRINCIPAUX = ['heating_kwh_m2', 'primary_kwh_m2', 'dh_re2020', 'heures_inconfort', 'cooling_kwh_m2'];

/**
 * Potentiels d'un bâtiment par bouquet (I5 bâtiment, servi dans le bloc « fiche » du document) : l'état réel,
 * puis pour chaque bouquet ses indicateurs, ses écarts au réel et les actions appliquées ou écartées (avec leur
 * raison). Lecture seule : les potentiels se calculent dans l'onglet Calculs.
 */
@Component({
  selector: 'app-potentiels-bouquets',
  standalone: true,
  templateUrl: './potentiels-bouquets.component.html',
  styleUrl: './potentiels-bouquets.component.scss',
})
export class PotentielsBouquetsComponent {
  readonly potentiels = input.required<PotentielsBatiment>();
  private api = inject(RenduApiService);
  private i3 = signal<IndicateurCanon[]>([]);
  readonly OBJECTIFS = OBJECTIFS;
  readonly mots: Record<string, string> = { N0: 'Mensuel', N1: 'Horaire simplifié', N2: 'Détaillé par paroi' };

  constructor() { void this.api.indicateursMemo().then(l => this.i3.set(l)); }

  /** Une entrée par bouquet et par niveau ; N0, N1 puis N2. */
  readonly entrees = computed(() => [...this.potentiels().bouquets].sort((a, b) =>
    (a.bouquet.id - b.bouquet.id) || a.niveau.localeCompare(b.niveau)));

  cles(p: PotentielBatiment): string[] {
    const e = Object.keys(p.ecarts ?? {});
    const ind = p.indicateurs ?? {};
    const extra = PRINCIPAUX.filter(k => !e.includes(k) && typeof ind[k] === 'number');
    const ordre = this.i3().map(x => x.cle);
    const rang = (k: string) => { const i = ordre.indexOf(k); return i < 0 ? 999 : i; };
    return [...e, ...extra].sort((a, b) => rang(a) - rang(b));
  }
  libelle(k: string): string { return this.i3().find(x => x.cle === k)?.libelle ?? k; }
  unite(k: string): string { return this.i3().find(x => x.cle === k)?.unite ?? ''; }
  bonSens(k: string, ecart: number): boolean | null {
    const s = this.i3().find(x => x.cle === k)?.sens;
    if (!s || !ecart) return null;
    return s === 'bas_est_mieux' ? ecart < 0 : ecart > 0;
  }
  val(x: unknown): string {
    return typeof x === 'number' && Number.isFinite(x) ? x.toLocaleString('fr-FR', { maximumFractionDigits: Math.abs(x) < 10 ? 1 : 0 }) : '—';
  }
  ecart(x: number | undefined): string {
    if (x === undefined || !Number.isFinite(x)) return '';
    return `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toLocaleString('fr-FR', { maximumFractionDigits: Math.abs(x) < 10 ? 1 : 0 })}`;
  }
  pct(x: number | undefined): string {
    if (x === undefined || !Number.isFinite(x)) return '';
    return `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.round(Math.abs(x) * 100)} %`;
  }
  appliquees(p: PotentielBatiment) { return p.actions.filter(a => a.appliquee); }
  ecartees(p: PotentielBatiment) { return p.actions.filter(a => !a.appliquee); }
  date(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  }
  statut(p: PotentielBatiment): string {
    switch (p.statut) {
      case null: return 'jamais calculé';
      case 'en_attente': case 'en_cours': return 'calcul en cours';
      case 'erreur': return 'échec du calcul';
      default: return p.a_jour ? 'à jour' : 'calculé avant les dernières données';
    }
  }
}
