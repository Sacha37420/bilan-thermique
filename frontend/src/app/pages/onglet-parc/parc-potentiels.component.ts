import { Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { EtatCalculsService } from '../../components/resultat-carte/etat-calculs.service';
import { NIVEAUX, NIVEAU_MOTS, Niveau } from '../../components/resultat-carte/etat-calculs.types';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';
import { GrapheComponent } from '../../components/graphe/graphe.component';
import { FigureRendu } from '../../components/graphe/graphe.types';
import { ErreurApi, IndicateurCanon, RenduApiService, messageDe } from '../onglet-rendu/rendu-api.service';
import { Agregat, PotentielParcCellule, PotentielsParc, Question, Spec, specVide } from '../onglet-rendu/contrat.types';

/** Erreur d'une partie de la vue : `discret` = route pas encore livrée (501), dite en indice plutôt qu'en alerte. */
interface Erreur { texte: string; discret: boolean }
function erreurDe(e: unknown, quoi: string): Erreur {
  return e instanceof ErreurApi && e.indisponible
    ? { texte: 'Pas encore disponible sur le serveur.', discret: true }
    : { texte: messageDe(e, quoi), discret: false };
}

const RELECTURE_MS = 5000;
const NOMBRE = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const NOMBRE_FIN = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 });
const ECART = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1, signDisplay: 'exceptZero' });
const ECART_PCT = new Intl.NumberFormat('fr-FR', { style: 'percent', maximumFractionDigits: 0, signDisplay: 'exceptZero' });

/** Onglet Parc › Potentiels par bouquet : parc réel comparé aux potentiels atteignables, bâtiment par bâtiment (I5, I6). */
@Component({
  selector: 'app-parc-potentiels',
  standalone: true,
  imports: [FormsModule, RouterLink, GrapheComponent],
  styleUrls: ['./parc-commun.scss'],
  styles: [`
    .file-etat { margin: var(--space-2) 0 0; }
    .rf-kpi b { display: block; font-size: var(--text-sm); margin-bottom: var(--space-1); }
    .cel { display: grid; gap: 2px; }
    .cel .ec { font-size: var(--text-xs); font-family: var(--font-mono); color: var(--text-mute); }
    .cel .ec.mieux { color: var(--success); }
    .cel .act { font-size: var(--text-xs); color: var(--text-mute); }
    .cel .etat-c { font-size: var(--text-sm); color: var(--text-mute); }
    .cel .etat-c.ko { color: var(--danger); }
    th.num, td.num { text-align: right; }
    tfoot td { font-weight: 600; border-top: 2px solid var(--border); }
    .rf-field select { min-height: 44px; }
    a.link { display: inline-flex; align-items: center; text-decoration: none; }
    @media (max-width: 899px) { .rf-row > .rf-field { flex: 1 1 100%; } }
  `],
  template: `
    @if (!parcId()) {
      <section class="rf-card"><p class="rf-hint">Les potentiels se comparent à l’échelle d’un parc.</p></section>
    } @else {
      <section class="rf-card" aria-labelledby="pp-h">
        <div class="rf-card-head"><h2 id="pp-h">Potentiels par bouquet</h2></div>
        <p>Un bouquet est un ensemble d’améliorations appliquées à chaque bâtiment là où elles sont possibles. Chaque bouquet
          donne un potentiel atteignable, estimé aux trois niveaux de calcul ; les potentiels se refont d’eux-mêmes quand un
          bâtiment ou un bouquet change. C’est une aide à la décision : elle ne remplace pas un DPE.</p>
        <div class="rf-tabs" role="radiogroup" aria-label="Niveau de calcul des potentiels">
          @for (n of niveaux; track n) {
            <button type="button" role="radio" [class.active]="niveau() === n" [attr.aria-checked]="niveau() === n"
                    (click)="choisirNiveau(n)">{{ mots[n] }} ({{ n }})</button>
          }
        </div>
        @if (niveauServeur(); as ns) {
          @if (ns !== niveau()) {
            <p class="rf-hint" role="status">Ce serveur ne sert encore que les potentiels {{ mots[ns].toLowerCase() }} ({{ ns }}) : ce sont eux qui sont montrés.</p>
          }
        }
        @if (progression()) { <p class="rf-hint" role="status" aria-live="polite">{{ progression() }}</p> }
        @if (erreurP(); as e) {
          <p [class]="e.discret ? 'rf-hint' : 'rf-error'" [attr.role]="e.discret ? 'status' : 'alert'">{{ e.texte }}</p>
        } @else if (!donnees()) {
          <p class="rf-hint" role="status">Chargement…</p>
        }
        @if (donnees(); as d) {
          <div class="rf-row">
            @if (d.peut_calculer) {
              <button type="button" class="rf-btn primary" (click)="calculer()" [disabled]="envoi() || fileActive()"
                      title="Met tout de suite en file les potentiels du niveau choisi pour tous les bâtiments du parc">Calculer maintenant ({{ mots[niveau()].toLowerCase() }}, {{ niveau() }})</button>
            } @else {
              <p class="rf-hint">Les potentiels se calculent d’eux-mêmes ; seul un gestionnaire du parc peut en lancer un tout de suite.</p>
            }
          </div>
          @if (fileActive()) {
            <p class="rf-hint file-etat" role="status">Calcul en cours : {{ d.file.en_cours }} en cours, {{ d.file.en_attente }} en attente.
              La page se met à jour toute seule.</p>
          }
        }
        @if (erreurCalcul(); as e) {
          <p [class]="e.discret ? 'rf-hint' : 'rf-error'" [attr.role]="e.discret ? 'status' : 'alert'">{{ e.texte }}</p>
        }
      </section>

      @if (donnees(); as d) {
        <section class="rf-card" aria-labelledby="pp-chiffres">
          <div class="rf-card-head"><h2 id="pp-chiffres">Chiffres clés</h2></div>
          @if (cles().length) {
            <div class="rf-row">
              <label class="rf-field"><span>Indicateur comparé</span>
                <select [ngModel]="indicateur()" (ngModelChange)="choisirIndicateur($event)">
                  @for (c of cles(); track c) { <option [value]="c">{{ libelle(c) }}</option> }
                </select></label>
            </div>
            <div class="rf-kpis">
              @for (s of series(); track s.cle) {
                <div class="rf-kpi">
                  <b>{{ s.nom }}</b>
                  @if (agregat(s.cle); as a) {
                    <small>Médiane</small><strong>{{ nombre(a.mediane) }}</strong> <small>{{ unite() }}</small>
                    <em>moyenne {{ nombre(a.moyenne) }}@if (a.somme !== null) { · somme {{ nombre(a.somme) }} }</em>
                    <em>{{ a.n }} bâtiment{{ a.n > 1 ? 's' : '' }}</em>
                  } @else { <small>aucune valeur</small> }
                </div>
              }
            </div>
          } @else {
            <p class="rf-hint">Aucun indicateur calculé pour ce parc.</p>
          }
        </section>

        <section class="rf-card" aria-labelledby="pp-tab">
          <div class="rf-card-head"><h2 id="pp-tab">Bâtiment par bâtiment</h2></div>
          <div class="rf-table-wrap">
            <table class="rf-table">
              <thead><tr>
                <th>Bâtiment</th>
                @for (s of series(); track s.cle) { <th class="num">{{ s.nom }}</th> }
              </tr></thead>
              <tbody>
                @for (b of d.batiments; track b.id) {
                  <tr>
                    <td><a class="link" routerLink="/batiment" [queryParams]="ctx.queryParams({ b: b.id, vue: 'synthese' })">{{ b.nom || ('Bâtiment ' + b.id) }}</a></td>
                    <td class="num"><div class="cel">
                      @if (valeur(b.reel.indicateurs); as v) { <strong>{{ v }}</strong> } @else { <span class="etat-c">jamais calculé</span> }
                      @if (b.reel.indicateurs && !b.reel.a_jour) { <span><span class="rf-badge m-infere">à refaire</span></span> }
                    </div></td>
                    @for (s of bouquets(); track s.cle) {
                      <td class="num"><div class="cel">
                        @if (cellule(b.potentiels, s.cle); as c) {
                          @if (c.statut === 'fait') {
                            <strong>{{ valeur(c.indicateurs) || '—' }}</strong>
                            @if (ecart(c); as e) { <span class="ec" [class.mieux]="e.mieux">{{ e.texte }}</span> }
                            @if (!c.a_jour) { <span><span class="rf-badge m-infere">à refaire</span></span> }
                            <span class="act">{{ c.actions_appliquees }} action{{ c.actions_appliquees > 1 ? 's' : '' }} appliquée{{ c.actions_appliquees > 1 ? 's' : '' }}
                              · {{ c.actions_ecartees }} écartée{{ c.actions_ecartees > 1 ? 's' : '' }}</span>
                          } @else {
                            <span class="etat-c" [class.ko]="c.statut === 'erreur'">{{ etat(c) }}</span>
                          }
                        } @else { <span class="etat-c">jamais calculé</span> }
                      </div></td>
                    }
                  </tr>
                } @empty {
                  <tr><td [attr.colspan]="series().length + 1" class="rf-hint">Aucun bâtiment dans ce parc.</td></tr>
                }
              </tbody>
              @if (d.batiments.length && cles().length) {
                <tfoot><tr>
                  <td>Médiane</td>
                  @for (s of series(); track s.cle) { <td class="num">{{ nombre(agregat(s.cle)?.mediane) }}</td> }
                </tr></tfoot>
              }
            </table>
          </div>
          @if (cles().length) { <p class="rf-hint">Valeurs : {{ libelle(indicateur()) }}, {{ mots[niveauAffiche()].toLowerCase() }} ({{ niveauAffiche() }}). Sous chaque potentiel, l’écart au réel du bâtiment au même niveau.</p> }
        </section>
      }

      <section class="rf-card" aria-labelledby="pp-graphe">
        <div class="rf-card-head"><h2 id="pp-graphe">Graphes</h2></div>
        @if (erreurQ(); as e) {
          <p [class]="e.discret ? 'rf-hint' : 'rf-error'" [attr.role]="e.discret ? 'status' : 'alert'">{{ e.texte }}</p>
        } @else if (questions().length) {
          <div class="rf-row">
            <label class="rf-field grow"><span>Question</span>
              <select [ngModel]="questionCle()" (ngModelChange)="choisirQuestion($event)">
                @for (q of questions(); track q.cle) { <option [value]="q.cle">{{ q.question }}</option> }
              </select></label>
          </div>
          @if (erreurFig(); as e) {
            <p [class]="e.discret ? 'rf-hint' : 'rf-error'" [attr.role]="e.discret ? 'status' : 'alert'">{{ e.texte }}</p>
          } @else {
            <app-graphe [figure]="figure()" [titre]="questionCourante()?.question ?? ''" [chargement]="chargementFig()" />
          }
        } @else if (questionsLues()) {
          <p class="rf-hint">Aucune question de graphe pour un parc.</p>
        } @else { <p class="rf-hint" role="status">Chargement…</p> }
      </section>

      @if (donnees() && cles().length) {
        <section class="rf-card" aria-labelledby="pp-carte">
          <div class="rf-card-head"><h2 id="pp-carte">Carte</h2></div>
          <div class="rf-row">
            <label class="rf-field"><span>Afficher</span>
              <select [ngModel]="serieCarte()" (ngModelChange)="choisirSerieCarte($event)">
                @for (s of series(); track s.cle) { <option [value]="s.cle">{{ s.nom }}</option> }
              </select></label>
          </div>
          @if (erreurCarte(); as e) {
            <p [class]="e.discret ? 'rf-hint' : 'rf-error'" [attr.role]="e.discret ? 'status' : 'alert'">{{ e.texte }}</p>
          } @else {
            <app-graphe [figure]="figureCarte()" [titre]="libelle(indicateur())" [chargement]="chargementCarte()" [hauteur]="420" />
            <p class="rf-hint">La carte montre pour chaque bâtiment le calcul le plus fin disponible et à jour.</p>
          }
        </section>
      }
    }
  `,
})
export class ParcPotentielsComponent {
  protected ctx = inject(ContexteService);
  private api = inject(RenduApiService);
  private etats = inject(EtatCalculsService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  readonly niveaux = NIVEAUX;
  readonly mots = NIVEAU_MOTS;
  private qp = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  /** Niveau choisi (`?niveau=`, défaut horaire simplifié). */
  readonly niveau = computed<Niveau>(() => { const n = this.qp().get('niveau'); return n === 'N0' || n === 'N2' ? n : 'N1'; });
  /** Niveau effectivement rendu par le serveur (I3 : `niveau` en tête) ; null tant que rien n'est lu. */
  readonly niveauServeur = computed<Niveau | null>(() => { const d = this.donnees(); return d ? ((d.niveau as Niveau | undefined) ?? 'N1') : null; });
  readonly niveauAffiche = computed<Niveau>(() => this.niveauServeur() ?? this.niveau());
  private suiviParc = computed(() => { const id = this.parcId(); return id === null ? null : this.etats.parcs()[id] ?? null; });
  readonly progression = computed(() => {
    const r = this.suiviParc()?.etat?.resume;
    if (!r || !r.calculs_restants) return '';
    return `Calculs automatiques du parc : ${r.calculs_restants} restant${r.calculs_restants > 1 ? 's' : ''}` +
      `${r.en_cours ? `, ${r.en_cours} bâtiment${r.en_cours > 1 ? 's' : ''} en cours` : ''} — la page se met à jour toute seule.`;
  });

  readonly parcId = computed(() => { const p = this.ctx.parcId(); return p !== null && p !== HORS_PARC ? p : null; });

  readonly donnees = signal<PotentielsParc | null>(null);
  readonly erreurP = signal<Erreur | null>(null);
  readonly erreurCalcul = signal<Erreur | null>(null);
  readonly envoi = signal(false);
  readonly canon = signal<IndicateurCanon[]>([]);
  readonly indicateur = signal('');

  readonly questions = signal<Question[]>([]);
  readonly questionsLues = signal(false);
  readonly erreurQ = signal<Erreur | null>(null);
  readonly questionCle = signal('');
  readonly figure = signal<FigureRendu | null>(null);
  readonly chargementFig = signal(false);
  readonly erreurFig = signal<Erreur | null>(null);

  readonly serieCarte = signal('reel');
  readonly figureCarte = signal<FigureRendu | null>(null);
  readonly chargementCarte = signal(false);
  readonly erreurCarte = signal<Erreur | null>(null);

  private minuteur: ReturnType<typeof setTimeout> | null = null;
  private numFig = 0;
  private numCarte = 0;

  readonly fileActive = computed(() => { const f = this.donnees()?.file; return !!f && f.en_attente + f.en_cours > 0; });
  readonly bouquets = computed(() => (this.donnees()?.bouquets ?? []).map(b => ({ cle: String(b.id), nom: b.nom })));
  readonly series = computed(() => [{ cle: 'reel', nom: 'Réel' }, ...this.bouquets()]);
  readonly cles = computed(() => {
    // Lot 40 : seulement les indicateurs du catalogue (I3) — les clés techniques (p10/p90 en kWh, « hours ») et l'ancien
    // alias du DH 26 °C n'ont rien à faire dans la liste.
    const canon = this.canon();
    let brutes = Object.keys(this.donnees()?.agregats?.['reel'] ?? {}).filter(k => k !== 'degree_hours_over_26');
    if (canon.length) brutes = brutes.filter(k => canon.some(c => c.cle === k));
    const ordre = new Map(canon.map((c, i) => [c.cle, i]));
    return brutes.sort((a, b) => (ordre.get(a) ?? 1e6) - (ordre.get(b) ?? 1e6) || a.localeCompare(b));
  });
  readonly canonCourant = computed(() => this.canon().find(c => c.cle === this.indicateur()) ?? null);
  readonly unite = computed(() => this.canonCourant()?.unite ?? '');
  readonly questionCourante = computed(() => this.questions().find(q => q.cle === this.questionCle()) ?? null);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.arreterRelecture());
    this.api.indicateurs().then(l => this.canon.set(Array.isArray(l) ? l : []), () => this.canon.set([]));
    effect(() => {
      const id = this.parcId();
      this.niveau();
      untracked(() => this.ouvrirParc(id));
    });
    // Recalcul automatique (I2 parc) : suivi tant que la vue est ouverte ; un calcul fini relit les potentiels.
    effect(onCleanup => {
      const id = this.parcId();
      if (id === null) return;
      onCleanup(untracked(() => this.etats.suivreParc(id)));
    });
    let avance = -1;
    effect(() => {
      const a = this.suiviParc()?.avance ?? -1;
      untracked(() => {
        const id = this.parcId();
        if (avance >= 0 && a > avance && id !== null) void this.lire(id, false, true);
        avance = a;
      });
    });
  }

  private ouvrirParc(id: number | null): void {
    this.arreterRelecture();
    this.donnees.set(null); this.erreurP.set(null); this.erreurCalcul.set(null);
    this.figure.set(null); this.erreurFig.set(null); this.figureCarte.set(null); this.erreurCarte.set(null);
    if (id === null) return;
    void this.lire(id, true);
    void this.chargerQuestions(id);
  }

  // ── Potentiels (I5) ──
  private async lire(id: number, premiere: boolean, auto = false): Promise<void> {
    const niveau = this.niveau();
    try {
      const d = await this.api.potentielsParc(id, niveau);
      if (this.parcId() !== id || this.niveau() !== niveau) return;
      this.recevoir(d, premiere || auto);
      if (auto) this.chargerFigure();
    } catch (e) {
      if (this.parcId() !== id) return;
      this.erreurP.set(erreurDe(e, 'Potentiels du parc'));
    }
  }

  private recevoir(d: PotentielsParc, premiere: boolean): void {
    const avant = this.fileActive();
    this.donnees.set(d); this.erreurP.set(null);
    const cles = this.cles();
    let change = false;
    if (!cles.includes(this.indicateur())) {
      this.indicateur.set(cles.includes('heating_kwh_m2') ? 'heating_kwh_m2' : (cles[0] ?? ''));
      change = true;
    }
    if (!this.series().some(s => s.cle === this.serieCarte())) { this.serieCarte.set('reel'); change = true; }
    const fini = avant && !this.fileActive();
    if (premiere || change || fini) this.chargerCarte();
    if (fini) this.chargerFigure();
    this.planifier();
  }

  async calculer(): Promise<void> {
    const id = this.parcId();
    if (id === null || this.envoi()) return;
    this.envoi.set(true); this.erreurCalcul.set(null);
    try {
      const d = await this.api.calculerPotentielsParc(id, undefined, this.niveau());
      if (this.parcId() !== id) return;
      this.recevoir(d, false);
    } catch (e) {
      if (this.parcId() === id) this.erreurCalcul.set(erreurDe(e, 'Calcul des potentiels du parc'));
    } finally {
      this.envoi.set(false);
    }
  }

  private planifier(): void {
    this.arreterRelecture();
    const id = this.parcId();
    if (id === null || !this.fileActive()) return;
    this.minuteur = setTimeout(() => { this.minuteur = null; void this.lire(id, false); }, RELECTURE_MS);
  }
  private arreterRelecture(): void {
    if (this.minuteur !== null) { clearTimeout(this.minuteur); this.minuteur = null; }
  }

  choisirNiveau(n: Niveau): void {
    void this.router.navigate([], { queryParams: { niveau: n === 'N1' ? null : n }, queryParamsHandling: 'merge', replaceUrl: true });
  }

  choisirIndicateur(c: string): void { this.indicateur.set(c); this.chargerCarte(); }

  agregat(serie: string): Agregat | null { return this.donnees()?.agregats?.[serie]?.[this.indicateur()] ?? null; }

  libelle(c: string): string {
    const i = this.canon().find(x => x.cle === c);
    return i ? (i.unite ? `${i.libelle} (${i.unite})` : i.libelle) : c;
  }

  nombre(x: number | string | null | undefined): string {
    if (typeof x === 'string') return x;
    if (x === null || x === undefined || !Number.isFinite(x)) return '—';
    return (Math.abs(x) < 10 ? NOMBRE_FIN : NOMBRE).format(x);
  }

  valeur(ind: Record<string, number | string | null> | null | undefined): string {
    const v = ind?.[this.indicateur()];
    return v === null || v === undefined ? '' : this.nombre(v);
  }

  ecart(c: PotentielParcCellule): { texte: string; mieux: boolean } | null {
    const cle = this.indicateur();
    const e = c.ecarts?.[cle];
    if (typeof e !== 'number' || !Number.isFinite(e)) return null;
    const p = c.ecarts_pct?.[cle];
    const u = this.unite();
    const texte = `${ECART.format(e)}${u ? ' ' + u : ''}` + (typeof p === 'number' && Number.isFinite(p) ? ` (${ECART_PCT.format(p)})` : '');
    const sens = this.canonCourant()?.sens;
    const mieux = (sens === 'bas_est_mieux' && e < 0) || (sens === 'haut_est_mieux' && e > 0);
    return { texte, mieux };
  }

  cellule(p: Record<string, PotentielParcCellule> | null | undefined, cle: string): PotentielParcCellule | null {
    return p?.[cle] ?? null;
  }

  etat(c: PotentielParcCellule): string {
    switch (c.statut) {
      case 'en_attente': case 'en_cours': return 'en cours';
      case 'erreur': return 'erreur';
      default: return 'jamais calculé';
    }
  }

  // ── Graphes (I6.b) ──
  private async chargerQuestions(id: number): Promise<void> {
    this.questionsLues.set(false); this.erreurQ.set(null);
    try {
      const g = await this.api.gabarits();
      if (this.parcId() !== id) return;
      const qs = (g?.questions ?? []).filter(q => q.portees?.includes('parc'));
      this.questions.set(qs);
      if (!qs.some(q => q.cle === this.questionCle())) {
        this.questionCle.set((qs.find(q => q.graphe?.series !== 'reel') ?? qs[0])?.cle ?? '');
      }
      this.chargerFigure();
    } catch (e) {
      if (this.parcId() === id) this.erreurQ.set(erreurDe(e, 'Questions de graphe'));
    } finally {
      if (this.parcId() === id) this.questionsLues.set(true);
    }
  }

  choisirQuestion(c: string): void { this.questionCle.set(c); this.chargerFigure(); }

  private chargerFigure(): void {
    const id = this.parcId();
    const q = this.questionCourante();
    const n = ++this.numFig;
    this.erreurFig.set(null);
    if (id === null || !q) { this.figure.set(null); return; }
    this.chargementFig.set(true); this.figure.set(null);
    // Graphe de la question au niveau choisi — sauf s'il compare lui-même les niveaux (dimension « niveau »).
    const g = q.graphe;
    const parNiveau = [g.x, g.couleur, g.facettes].includes('niveau');
    const graphe = parNiveau ? g : { ...g, filtre: { ...(g.filtre ?? {}), niveau: [this.niveau()] } };
    this.api.figure('parc', { parc: id }, graphe).then(
      f => { if (n === this.numFig) this.figure.set(f); },
      e => { if (n === this.numFig) this.erreurFig.set(erreurDe(e, 'Graphe')); },
    ).finally(() => { if (n === this.numFig) this.chargementFig.set(false); });
  }

  // ── Carte (I6.c, bloc carte) ──
  choisirSerieCarte(s: string): void { this.serieCarte.set(s); this.chargerCarte(); }

  private chargerCarte(): void {
    const id = this.parcId();
    const indicateur = this.indicateur();
    const n = ++this.numCarte;
    this.erreurCarte.set(null);
    if (id === null || !indicateur) { this.figureCarte.set(null); return; }
    const spec: Spec = {
      ...specVide('parc'), titre: '',
      sections: [{ titre: '', blocs: [{ type: 'carte', indicateur, series: this.serieCarte() }] }],
    };
    this.chargementCarte.set(true); this.figureCarte.set(null);
    this.api.document({ spec }, { parc: id }).then(
      doc => {
        if (n !== this.numCarte) return;
        const bloc = doc?.sections?.[0]?.blocs?.[0];
        if (bloc?.indisponible) this.erreurCarte.set({ texte: bloc.indisponible, discret: true });
        else if (bloc?.figure) this.figureCarte.set(bloc.figure);
        else this.erreurCarte.set({ texte: 'Carte indisponible pour ce parc.', discret: true });
      },
      e => { if (n === this.numCarte) this.erreurCarte.set(erreurDe(e, 'Carte')); },
    ).finally(() => { if (n === this.numCarte) this.chargementCarte.set(false); });
  }
}
