import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { EtatCalculsService } from './etat-calculs.service';
import {
  CalculEtat, EtatCalculs, NIVEAUX, NIVEAU_MOTS, Niveau, STATUT_CALCUL_AIDE, SuiviEtat, STATUT_CALCUL_CLASSE, STATUT_CALCUL_MOTS,
  heureDe, statutDe,
} from './etat-calculs.types';

/**
 * Bandeau « État des calculs » d'un bâtiment (I2) : ce qui est à jour, prévu, en cours ou en erreur, pour l'état
 * actuel et chaque bouquet, aux trois niveaux. Se relit tout seul (≥ 5 s) tant que quelque chose est prévu ou
 * tourne ; s'arrête quand tout est à jour ou quand on quitte la vue. « Recalculer » pour un gestionnaire.
 * `avance` est émis chaque fois qu'un calcul se termine : la page relit ses résultats.
 */
@Component({
  selector: 'app-etat-calculs',
  standalone: true,
  styleUrl: './etat-badges.scss',
  styles: [`
    :host { display: block; }
    .bandeau { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-3); }
    .bandeau .phrase { flex: 1 1 18rem; margin: 0; }
    .bandeau .rf-btn { margin-left: auto; }
    .prog { flex-basis: 100%; height: 8px; border-radius: var(--radius-full); background: var(--border); overflow: hidden; }
    .prog i { display: block; height: 100%; background: var(--accent); transition: width .4s; }
    details summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-size: var(--text-sm); }
    .grille th[scope=row] { text-transform: none; letter-spacing: 0; font-size: var(--text-sm); color: var(--text); font-weight: 600; }
    .grille td small { display: block; color: var(--text-mute); font-size: var(--text-xs); }
    @media (max-width: 899px) { .bandeau .rf-btn { margin-left: 0; flex: 1 1 100%; } }
  `],
  template: `
    <section class="rf-card" aria-labelledby="ec-titre" [attr.aria-busy]="suivi()?.actif ?? false">
      <div class="rf-card-head"><h2 id="ec-titre">État des calculs</h2></div>
      @let s = suivi();
      @if (!s || s.disponible === null && !s.erreur) {
        <p class="rf-hint" role="status">Lecture de l’état des calculs…</p>
      } @else if (s.disponible === false) {
        <p class="rf-hint" role="status">Le suivi des calculs automatiques n’est pas encore disponible sur ce serveur :
          les résultats ci-dessous sont ceux des derniers calculs faits.</p>
      } @else if (s.erreur && !s.etat) {
        <p class="rf-error" role="alert">{{ s.erreur }}</p>
      }
      @if (etat(); as e) {
        <div class="bandeau" role="status" aria-live="polite">
          <p class="phrase">
            @switch (e.statut) {
              @case ('planifie') {
                <span class="rf-badge st m-declare">recalcul prévu</span>
                Recalcul prévu @if (heure(e.prevu_pour); as h) { à {{ h }} } @else { sous peu }
                @if (e.cause) { — suite à : {{ e.cause }} }.
                @if (e.calculs_restants) { {{ e.calculs_restants }} calcul{{ e.calculs_restants > 1 ? 's' : '' }} à refaire. }
              }
              @case ('en_cours') {
                <span class="rf-badge st m-document">en cours</span>
                Calculs en cours : {{ e.calculs_restants }} restant{{ e.calculs_restants > 1 ? 's' : '' }}.
                @if (enCours(); as c) { En ce moment : {{ c.libelle }}, {{ mots[c.niveau].toLowerCase() }} ({{ c.niveau }}). }
                Les résultats se mettent à jour d’eux-mêmes.
              }
              @case ('erreur') {
                <span class="rf-badge st m-erreur">erreur</span>
                Le dernier recalcul a échoué@if (e.message) { : {{ e.message }} } @else { . }
              }
              @default {
                @if (aRefaire()) {
                  <span class="rf-badge st m-infere">à refaire</span>
                  {{ aRefaire() }} calcul{{ aRefaire() > 1 ? 's' : '' }} à refaire : les données ont changé depuis.
                  @if (!e.peut_recalculer) { Un gestionnaire du bâtiment peut les relancer. }
                } @else {
                  <span class="rf-badge st m-mesure">à jour</span> Tous les calculs sont à jour.
                }
              }
            }
            @if (desactives().length) { <small class="rf-hint"> Désactivé dans les réglages du parc : {{ desactives().join(', ') }}.</small> }
          </p>
          @if (e.peut_recalculer) {
            <button type="button" class="rf-btn" (click)="recalculer()" [disabled]="envoi()"
                    title="Refait tout de suite les calculs qui ne sont pas à jour (état actuel et bouquets)">
              {{ envoi() ? 'Envoi…' : 'Recalculer' }}</button>
          }
          @if (total()) {
            <div class="prog" role="progressbar" aria-label="Part des calculs à jour" aria-valuemin="0" aria-valuemax="100"
                 [attr.aria-valuenow]="avancement()"><i [style.width.%]="avancement()"></i></div>
          }
        </div>
        @if (message()) { <p class="rf-error" role="alert">{{ message() }}</p> }
        @if (lignes().length) {
          <details>
            <summary>Détail : état actuel et bouquets, niveau par niveau</summary>
            <div class="rf-table-wrap">
              <table class="rf-table grille">
                <thead><tr><th scope="col">Calcul</th>
                  @for (n of niveaux; track n) { <th scope="col">{{ mots[n] }} ({{ n }})</th> }</tr></thead>
                <tbody>
                  @for (l of lignes(); track l.cle) {
                    <tr><th scope="row">{{ l.libelle }}</th>
                      @for (n of niveaux; track n) {
                        @let c = cellule(e, l.etat, n);
                        <td>@if (c) {
                          <span [class]="'rf-badge st ' + classes[c.statut]" [title]="aides[c.statut]">{{ statutMots[c.statut] }}</span>
                          @if (c.calcule_le && c.statut !== 'en_cours') { <small>{{ jour(c.calcule_le) }}</small> }
                        } @else { <span class="rf-hint">—</span> }</td>
                      }
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </details>
        }
      }
    </section>
  `,
})
export class EtatCalculsComponent {
  readonly buildingId = input.required<number>();
  /** Incrémenté par la page après une modification : relit l'état tout de suite (un recalcul vient d'être prévu). */
  readonly refresh = input(0);
  readonly avance = output<number>();

  private svc = inject(EtatCalculsService);
  readonly niveaux = NIVEAUX;
  readonly mots = NIVEAU_MOTS;
  readonly statutMots = STATUT_CALCUL_MOTS;
  readonly aides = STATUT_CALCUL_AIDE;
  readonly classes = STATUT_CALCUL_CLASSE;
  readonly envoi = signal(false);
  readonly message = signal('');

  readonly suivi = computed<SuiviEtat<EtatCalculs> | null>(() => this.svc.batiments()[this.buildingId()] ?? null);
  readonly etat = computed<EtatCalculs | null>(() => this.suivi()?.etat ?? null);
  readonly enCours = computed(() => this.etat()?.calculs.find(c => c.statut === 'en_cours') ?? null);
  readonly aRefaire = computed(() => this.etat()?.calculs.filter(c => c.statut === 'a_refaire' || c.statut === 'erreur').length ?? 0);
  readonly total = computed(() => this.etat()?.calculs.filter(c => c.statut !== 'desactive').length ?? 0);
  readonly avancement = computed(() => {
    const t = this.total();
    const ok = this.etat()?.calculs.filter(c => c.statut === 'a_jour').length ?? 0;
    return t ? Math.round((ok / t) * 100) : 0;
  });
  /** Niveaux entièrement désactivés (ex. N2 coupé par le réglage `calcul.n2`), en mots. */
  readonly desactives = computed(() => {
    const cs = this.etat()?.calculs ?? [];
    return NIVEAUX.filter(n => { const l = cs.filter(c => c.niveau === n); return l.length > 0 && l.every(c => c.statut === 'desactive'); })
      .map(n => `${NIVEAU_MOTS[n].toLowerCase()} (${n})`);
  });
  readonly lignes = computed(() => {
    const vus = new Map<string, { cle: string; etat: 'reel' | number; libelle: string }>();
    for (const c of this.etat()?.calculs ?? []) {
      const cle = String(c.etat);
      if (!vus.has(cle)) vus.set(cle, { cle, etat: c.etat, libelle: c.etat === 'reel' ? (c.libelle || 'État actuel') : (c.libelle || `Bouquet ${c.etat}`) });
    }
    return [...vus.values()].sort((a, b) => (a.etat === 'reel' ? -1 : b.etat === 'reel' ? 1 : 0));
  });

  private dernierAvance = -1;

  constructor() {
    effect(onCleanup => {
      const id = this.buildingId();
      const liberer = untracked(() => this.svc.suivreBatiment(id));
      this.dernierAvance = -1;
      onCleanup(liberer);
    });
    effect(() => {
      const r = this.refresh();
      if (r > 0) untracked(() => this.svc.relireBatiment(this.buildingId()));
    });
    effect(() => {
      const a = this.suivi()?.avance ?? 0;
      untracked(() => {
        if (this.dernierAvance >= 0 && a !== this.dernierAvance) this.avance.emit(a);
        this.dernierAvance = a;
      });
    });
  }

  cellule(e: EtatCalculs, etat: 'reel' | number, n: Niveau): CalculEtat | null { return statutDe(e, etat, n); }
  heure(iso: string | null): string { return heureDe(iso); }
  jour(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  async recalculer(): Promise<void> {
    this.envoi.set(true); this.message.set('');
    const m = await this.svc.recalculer(this.buildingId());
    this.envoi.set(false);
    this.message.set(m);
  }
}
