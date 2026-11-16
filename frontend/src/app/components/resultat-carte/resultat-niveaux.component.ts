import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { RunCompact } from './resultat-carte.component';
import { EtatCalculsService } from './etat-calculs.service';
import {
  NIVEAUX, NIVEAU_MOTS, Niveau, STATUT_CALCUL_AIDE, STATUT_CALCUL_CLASSE, STATUT_CALCUL_MOTS, StatutCalcul, statutDe,
} from './etat-calculs.types';

type Latest = Record<Niveau, RunCompact | null> & { can_n2?: boolean };

/**
 * Les trois niveaux de calcul côte à côte pour l'état actuel (ou une amélioration) : besoin, inconfort d'été (DH),
 * énergie, avec l'état de chaque calcul (I2) — à jour, en file, en cours, erreur, désactivé. Lecture seule ;
 * relu quand la page incrémente `refresh` (calcul terminé, donnée modifiée).
 */
@Component({
  selector: 'app-resultat-niveaux',
  standalone: true,
  imports: [DecimalPipe, DatePipe, RouterLink],
  styleUrl: './etat-badges.scss',
  styles: [`
    :host { display: block; }
    .niveaux { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); }
    @media (max-width: 899px) { .niveaux { grid-template-columns: 1fr; } }
    .niv { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--space-3); background: var(--bg);
           display: flex; flex-direction: column; gap: var(--space-2); }
    .niv.vide { color: var(--text-mute); }
    .niv h3 { margin: 0; font-size: var(--text-base); display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
    .niv h3 small { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--text-mute); font-weight: 400; }
    .niv dl { margin: 0; display: grid; grid-template-columns: 1fr auto; gap: var(--space-1) var(--space-3); font-size: var(--text-sm); }
    .niv dt { color: var(--text-mute); }
    .niv dd { margin: 0; text-align: right; font-family: var(--font-mono); }
    .niv dd strong { font-size: var(--text-lg); }
    .niv dd small { display: block; color: var(--text-mute); font-size: var(--text-xs); }
    .niv .pied { margin-top: auto; font-size: var(--text-xs); color: var(--text-mute); }
    .niv a { min-height: 44px; display: inline-flex; align-items: center; }
  `],
  template: `
    <section class="rf-card" aria-labelledby="rn-titre">
      <div class="rf-card-head">
        <h2 id="rn-titre">{{ titre() }}</h2>
        <a class="rf-btn" routerLink="/calculs" [queryParams]="ctx.queryParams({ vue: 'simulation', type: 'N1' })">Voir dans Calculs</a>
      </div>
      <p class="rf-hint">Les trois façons de simuler le bâtiment, de la plus rapide à la plus fine. Elles se refont d’elles-mêmes
        quand une donnée du bâtiment change. Aucune n’est un diagnostic de performance énergétique (DPE).</p>
      @if (erreur()) { <p class="rf-error" role="alert">{{ erreur() }}</p> }
      @if (latest(); as l) {
        <div class="niveaux">
          @for (n of niveaux; track n) {
            @let r = l[n];
            @let st = statut(n, r);
            <article class="niv" [class.vide]="!r" [attr.aria-labelledby]="'rn-' + n">
              <h3 [id]="'rn-' + n">{{ mots[n] }} <small>({{ n }})</small>
                @if (st) { <span [class]="'rf-badge st ' + classes[st]" [title]="aides[st]">{{ statutMots[st] }}</span> }</h3>
              @if (r) {
                <dl>
                  <dt>Besoin de chauffage</dt>
                  <dd>@if (num(r, 'heating_kwh_m2') !== null) { <strong>{{ num(r, 'heating_kwh_m2') | number: '1.0-0' }}</strong> kWh/m²·an
                      @if (num(r, 'heating_kwh_m2_p10') !== null) { <small>entre {{ num(r, 'heating_kwh_m2_p10') | number: '1.0-0' }} et {{ num(r, 'heating_kwh_m2_p90') | number: '1.0-0' }}</small> }
                      @if (n !== 'N1' && ecart(r); as e) { <small>{{ e > 0 ? '+' : '' }}{{ e }} % par rapport à l’horaire simplifié</small> }
                    } @else { — }</dd>
                  @if (num(r, 'heating_kwh') !== null) { <dt>Besoin total</dt><dd>{{ num(r, 'heating_kwh') | number: '1.0-0' }} kWh/an</dd> }
                  <dt title="Degrés-heures d’inconfort d’été, méthode RE2020">Inconfort d’été (DH)</dt>
                  <dd>@if (num(r, 'dh_re2020') !== null) { {{ num(r, 'dh_re2020') | number: '1.0-0' }} °C·h }
                      @else if (n === 'N0') { <small>pas calculé par le bilan mensuel</small> } @else { — }</dd>
                  @if (num(r, 'hours_over_28') !== null) { <dt>Heures &gt; 28 °C</dt><dd>{{ num(r, 'hours_over_28') | number: '1.0-0' }} h</dd> }
                  @if (num(r, 'primary_kwh_m2') !== null) {
                    <dt title="Estimation du modèle, pas un DPE">Énergie primaire</dt><dd>{{ num(r, 'primary_kwh_m2') | number: '1.0-0' }} kWh/m²·an</dd>
                  }
                  @if (num(r, 'heating_kw_max') !== null) { <dt>Puissance maximale</dt><dd>{{ num(r, 'heating_kw_max') | number: '1.1-1' }} kW</dd> }
                </dl>
                <p class="pied">Calculé le {{ r.created_at | date: 'dd/MM/yyyy HH:mm' }}@if (r.weather.source) { · météo {{ r.weather.source }} }
                  · <a routerLink="/calculs" [queryParams]="ctx.queryParams({ vue: 'simulation', type: n })">détail</a></p>
              } @else {
                <p class="rf-hint">@switch (st) {
                  @case ('desactive') { Ce niveau est désactivé dans les réglages du parc. }
                  @case ('en_file') { Prévu : il sera calculé de lui-même. }
                  @case ('en_cours') { Calcul en cours… }
                  @case ('erreur') { Le calcul a échoué ; il sera retenté à la prochaine modification. }
                  @default { Pas encore calculé. }
                }</p>
              }
            </article>
          }
        </div>
      } @else if (!erreur()) { <p class="rf-hint" role="status">Chargement des résultats…</p> }
    </section>
  `,
})
export class ResultatNiveauxComponent {
  readonly buildingId = input.required<number>();
  readonly scenarioId = input<number | null>(null);
  readonly refresh = input(0);
  readonly titre = input('Résultats aux trois niveaux');

  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private etats = inject(EtatCalculsService);
  readonly niveaux = NIVEAUX;
  readonly mots = NIVEAU_MOTS;
  readonly statutMots = STATUT_CALCUL_MOTS;
  readonly aides = STATUT_CALCUL_AIDE;
  readonly classes = STATUT_CALCUL_CLASSE;

  readonly latest = signal<Latest | null>(null);
  readonly erreur = signal('');
  private demande = 0;
  private etat = computed(() => this.etats.batiments()[this.buildingId()]?.etat ?? null);

  constructor() {
    effect(() => {
      const id = this.buildingId(), sc = this.scenarioId();
      this.refresh();
      untracked(() => this.charger(id, sc));
    });
  }

  private charger(id: number, sc: number | null): void {
    const n = ++this.demande;
    this.rf.get<Latest>(`/batiments/${id}/resultat/${sc ? `?scenario=${sc}` : ''}`).subscribe({
      next: l => { if (n === this.demande) { this.latest.set(l); this.erreur.set(''); } },
      error: () => { if (n === this.demande) { this.latest.set(null); this.erreur.set('Résultats indisponibles pour ce bâtiment.'); } },
    });
  }

  /** État du calcul : celui du suivi automatique (I2) s'il le connaît, sinon déduit du dernier calcul. */
  statut(n: Niveau, r: RunCompact | null): StatutCalcul | null {
    if (this.scenarioId() === null) {
      const c = statutDe(this.etat(), 'reel', n);
      if (c) return c.statut;
    }
    if (!r) return null;
    if (r.status === 'PENDING' || r.status === 'RUNNING') return 'en_cours';
    if (r.status === 'ERROR') return 'erreur';
    return r.up_to_date === false ? 'a_refaire' : r.up_to_date === true ? 'a_jour' : null;
  }

  num(r: RunCompact | null | undefined, k: string): number | null {
    const v = r?.indicators?.[k];
    return typeof v === 'number' ? v : null;
  }

  /** Écart en % du besoin de chauffage par rapport à l'horaire simplifié (N1), le calcul de référence. */
  ecart(r: RunCompact): number | null {
    const a = this.num(this.latest()?.N1, 'heating_kwh_m2'), b = this.num(r, 'heating_kwh_m2');
    return a && b ? Math.round((b / a - 1) * 100) : null;
  }
}
