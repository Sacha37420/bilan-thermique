import { Component, OnDestroy, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { DecimalPipe, KeyValuePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { apiUrl } from '../../core/env';
import { APPROFONDISSEMENT_LABELS, ModeleUtilise, ParcRow, USAGE_LABELS } from '../../core/refonte.types';
import { CarteComponent, MapPoint } from '../../components/carte/carte.component';
import { EtatCalculsService } from '../../components/resultat-carte/etat-calculs.service';
import { NIVEAUX, NIVEAU_MOTS, Niveau, StatutRecalcul } from '../../components/resultat-carte/etat-calculs.types';
import { LigneParc, NiveauLigne } from './parc.types';

type SortKey = 'name' | 'usage' | 'surface_m2' | 'confidence' | 'priority' | 'dpe' | 'appro' | 'calculs'
  | 'besoin' | 'dh' | 'ep' | 'h28' | 'besoin_N0' | 'besoin_N1' | 'besoin_N2' | 'dh_N1' | 'dh_N2';
/** Niveau affiché dans le tableau : un niveau, ou les trois côte à côte. */
type Affichage = Niveau | 'trois';
const CALCUL_MOTS: Record<StatutRecalcul, string> = { inactif: 'à jour', planifie: 'recalcul prévu', en_cours: 'en cours', erreur: 'erreur' };
const CALCUL_CLASSE: Record<StatutRecalcul, string> = { inactif: 'm-mesure', planifie: 'm-declare', en_cours: 'm-document', erreur: 'm-erreur' };
const CALCUL_RANG: Record<StatutRecalcul, number> = { erreur: 3, en_cours: 2, planifie: 1, inactif: 0 };
const APPRO_RANG: Record<string, number> = { automatique: 0, complete: 1, documente: 2, mesure: 3, expert: 4 };
const APPRO_CLASSE: Record<string, string> = { automatique: 'm-defaut', complete: 'm-declare', documente: 'm-document', mesure: 'm-mesure', expert: 'm-mesure' };
export const STATUT_MOTS: Record<string, string> = {
  complet: 'analysé', incomplet: 'à vérifier', en_attente: 'en attente', en_cours: 'analyse en cours', '': 'jamais analysé',
};

/** Onglet Parc › Bâtiments : état du parc en mots, tableau (confiance, approfondissement, modèles utilisés), carte. */
@Component({
  selector: 'app-parc-batiments',
  standalone: true,
  imports: [FormsModule, DecimalPipe, KeyValuePipe, CarteComponent],
  styleUrls: ['./parc-commun.scss', '../../components/resultat-carte/etat-badges.scss'],
  styles: [`
    .niv-sel { margin-bottom: var(--space-3); }
    .calc { display: grid; gap: var(--space-1); justify-items: start; }
    th.num button { text-align: right; }
    .prog.calculs i { background: var(--accent); }
    .sortable td small.ko { color: var(--warning); }
  `],
  template: `
    @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
    @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }

    <section class="rf-card" aria-labelledby="pb-etat">
      <div class="rf-card-head"><h2 id="pb-etat">{{ ctx.parc()?.name }}</h2></div>
      <p class="etat">{{ etat() }}</p>
      <div class="prog" role="progressbar" [attr.aria-valuenow]="avancement()" aria-valuemin="0" aria-valuemax="100"
           aria-label="Part des bâtiments analysés"><i [style.width.%]="avancement()"></i></div>
      @if (suiviParc(); as sp) {
        @if (sp.etat; as e) {
          <p class="rf-hint" role="status" aria-live="polite">{{ phraseCalculs() }}</p>
          @if (e.resume.batiments) {
            <div class="prog calculs" role="progressbar" [attr.aria-valuenow]="avancementCalculs()" aria-valuemin="0" aria-valuemax="100"
                 aria-label="Part des bâtiments dont tous les calculs sont à jour"><i [style.width.%]="avancementCalculs()"></i></div>
          }
        } @else if (sp.disponible === false) {
          <p class="rf-hint">Suivi des calculs automatiques pas encore disponible sur ce serveur.</p>
        }
      }
      <div class="rf-row">
        @if (ctx.estGestionnaire() && !ctx.horsParc()) {
          <button type="button" class="rf-btn" (click)="lancer(false)" title="Relance l'analyse automatique des bâtiments à vérifier ou jamais analysés">Relancer les bâtiments à vérifier</button>
          <button type="button" class="rf-btn" (click)="lancer(true)" title="Rejoue l'analyse automatique et les calculs de tous les bâtiments du parc">Tout recalculer</button>
          <button type="button" class="rf-btn" (click)="exportCsv()">Exporter le tableau (CSV)</button>
        }
        @if (ctx.horsParc()) {
          <p class="rf-hint">Bâtiments créés avant les parcs, visibles de tous les membres de l'app. Pour les ranger dans un parc,
            ouvrez ce parc puis « Import et partage ».</p>
        }
      </div>
    </section>

    <section class="rf-card" aria-labelledby="pb-liste">
      <div class="rf-card-head"><h2 id="pb-liste">Bâtiments</h2>
        <nav class="rf-tabs" aria-label="Affichage">
          <button type="button" [class.active]="mode() === 'table'" (click)="mode.set('table')">Tableau</button>
          <button type="button" [class.active]="mode() === 'carte'" (click)="mode.set('carte')">Carte</button></nav></div>
      <div class="rf-row filters">
        <label class="rf-field"><span>Recherche</span><input type="text" [ngModel]="search()" (ngModelChange)="search.set($event)" placeholder="nom ou adresse"></label>
        <label class="rf-field"><span>Usage</span>
          <select [ngModel]="filterUsage()" (ngModelChange)="filterUsage.set($event)"><option value="">tous</option>
            @for (u of usageLabels | keyvalue; track u.key) { <option [value]="u.key">{{ u.value }}</option> }</select></label>
        <label class="rf-field"><span>Analyse</span>
          <select [ngModel]="filterStatus()" (ngModelChange)="filterStatus.set($event)"><option value="">toutes</option>
            @for (s of statutMots | keyvalue; track s.key) { <option [value]="s.key">{{ s.value }}</option> }</select></label>
      </div>

      <div class="rf-tabs niv-sel" role="radiogroup" aria-label="Niveau de calcul affiché">
        @for (a of affichages; track a[0]) {
          <button type="button" role="radio" [class.active]="affichage() === a[0]" [attr.aria-checked]="affichage() === a[0]"
                  (click)="choisirAffichage(a[0])">{{ a[1] }}</button>
        }
      </div>
      @if (mode() === 'carte') {
        <div class="rf-row">
          <label class="rf-field"><span>Couleur selon</span>
            <select [ngModel]="colorBy()" (ngModelChange)="colorBy.set($event)">
              <option value="besoin">besoin de chauffage</option><option value="confiance">confiance</option>
              <option value="chaleur">heures au-dessus de 28 °C</option><option value="etiquette">étiquette énergie</option></select></label>
          <ul class="legend">@for (l of legend(); track l[1]) { <li><i [style.background]="l[0]"></i>{{ l[1] }}</li> }</ul>
        </div>
        <app-carte [points]="points()" (pointClicked)="ouvrir(+$event.id)" ariaLabel="Carte des bâtiments du parc" />
        <p class="rf-hint">Cliquez sur un bâtiment pour l'ouvrir.</p>
      } @else {
        <div class="rf-table-wrap">
          <table class="rf-table sortable">
            <thead><tr>
              <th><button type="button" (click)="sort('name')">Bâtiment {{ arrow('name') }}</button></th>
              <th><button type="button" (click)="sort('confidence')" title="Ce que vaut le chiffre : part des données connues et leur provenance">Confiance {{ arrow('confidence') }}</button></th>
              <th><button type="button" (click)="sort('appro')" title="Jusqu'où le bâtiment a été décrit : données ouvertes seules, valeurs déclarées, documents lus, mesures, enveloppe experte">Approfondissement {{ arrow('appro') }}</button></th>
              <th><button type="button" (click)="sort('calculs')" title="État des calculs automatiques du bâtiment, et niveaux déjà calculés">Calculs {{ arrow('calculs') }}</button></th>
              @if (affichage() === 'trois') {
                @for (n of niveaux; track n) {
                  <th class="num"><button type="button" (click)="sort($any('besoin_' + n))" [title]="'Besoin de chauffage par m² et par an — ' + mots[n].toLowerCase()">Besoin {{ mots[n].toLowerCase() }} ({{ n }}) {{ arrow($any('besoin_' + n)) }}</button></th>
                }
                <th class="num"><button type="button" (click)="sort('dh_N1')" title="Degrés-heures d'inconfort d'été (RE2020), horaire simplifié">DH horaire simplifié (N1) {{ arrow('dh_N1') }}</button></th>
                <th class="num"><button type="button" (click)="sort('dh_N2')" title="Degrés-heures d'inconfort d'été (RE2020), détaillé par paroi">DH détaillé par paroi (N2) {{ arrow('dh_N2') }}</button></th>
              } @else {
                <th class="num"><button type="button" (click)="sort('besoin')" [title]="'Besoin de chauffage par m² et par an — ' + motsNiveau() + (affichage() === 'N1' ? ', avec sa fourchette probable' : '')">Besoin de chauffage {{ arrow('besoin') }}</button></th>
                <th class="num"><button type="button" (click)="sort('dh')" title="Degrés-heures d'inconfort d'été (RE2020) ; le bilan mensuel ne le calcule pas">Inconfort d’été (DH) {{ arrow('dh') }}</button></th>
                <th class="num"><button type="button" (click)="sort('ep')" title="Énergie primaire estimée par le modèle (pas un DPE)">Énergie primaire {{ arrow('ep') }}</button></th>
                <th class="num"><button type="button" (click)="sort('h28')" title="Heures par an où l'intérieur dépasse 28 °C sans climatisation">Heures &gt; 28 °C {{ arrow('h28') }}</button></th>
              }
              <th><button type="button" (click)="sort('dpe')" title="Étiquette du DPE officiel s'il existe (ce n'est pas un DPE calculé par l'app)">DPE {{ arrow('dpe') }}</button></th>
              <th class="num"><button type="button" (click)="sort('priority')" title="Priorité = besoin × (0,5 + 0,5 × confiance) : un bâtiment gourmand et bien connu passe devant">Priorité {{ arrow('priority') }}</button></th>
            </tr></thead>
            <tbody>
              @for (r of view(); track r.id) {
                @let nv = niv(r, niveauCourant());
                <tr class="clic" [class.courant]="r.id === ctx.batimentId()" tabindex="0" (click)="ouvrir(r.id)" (keydown.enter)="ouvrir(r.id)">
                  <td><button type="button" class="link" (click)="$event.stopPropagation(); ouvrir(r.id)">{{ r.name || r.address || ('Bâtiment ' + r.id) }}</button>
                    <small>{{ r.address }}@if (usageLabels[r.usage ?? '']) { · {{ usageLabels[r.usage ?? ''] }} }@if (r.surface_m2) { · {{ r.surface_m2 | number: '1.0-0' }} m² }
                      @if (r.status !== 'complet') { · <span [class.ko]="r.status === 'incomplet'">{{ statutMots[r.status] }}</span> }</small></td>
                  <td><span class="rf-gauge" [title]="'Confiance ' + (r.confidence * 100 | number: '1.0-0') + ' %, complétude ' + (r.completeness * 100 | number: '1.0-0') + ' %'">
                    <span [class]="'rf-gauge-bar ' + gauge(r.confidence)"><i [style.width.%]="r.confidence * 100"></i></span>{{ r.confidence * 100 | number: '1.0-0' }} %</span></td>
                  <td>@if (r.approfondissement; as a) {
                      <span [class]="'rf-badge ' + (apClasse[a.niveau] || 'm-defaut')" [title]="a.detail || apLabels[a.niveau]">{{ a.libelle || apLabels[a.niveau] }}</span>
                    } @else { <span class="rf-hint">—</span> }</td>
                  <td><div class="calc">
                    @if (statutCalcul(r); as sc) {
                      @let aRefaire = sc.statut === 'inactif' && sc.restants > 0;
                      <span [class]="'rf-badge st ' + (aRefaire ? 'm-infere' : calculClasse[sc.statut])" [title]="titreCalcul(sc.statut, sc.restants)">{{ aRefaire ? 'à refaire' : calculMots[sc.statut] }}@if (sc.restants) { · {{ sc.restants }} restant{{ sc.restants > 1 ? 's' : '' }} }</span>
                    }
                    @if (r.modeles?.length) {
                      <span class="pastilles">@for (m of r.modeles; track m.niveau) {
                        <span class="pastille" [class.ok]="m.a_jour" [class.perime]="!m.a_jour" [title]="titreModele(m)"><b>{{ m.niveau }}</b> {{ m.libelle }}{{ m.a_jour ? '' : ' · ancien' }}</span>
                      }</span>
                    } @else { <span class="rf-hint">aucun</span> }</div></td>
                  @if (affichage() === 'trois') {
                    @for (n of niveaux; track n) {
                      @let x = niv(r, n);
                      <td class="num">@if (x?.heating_kwh_m2 != null) { <strong>{{ x!.heating_kwh_m2 | number: '1.0-0' }}</strong>
                        @if (x!.a_jour === false) { <small class="ko">à refaire</small> } } @else { — }</td>
                    }
                    <td class="num">{{ fmt(niv(r, 'N1')?.dh_re2020) }}</td>
                    <td class="num">{{ fmt(niv(r, 'N2')?.dh_re2020) }}</td>
                  } @else {
                    <td class="num">@if (nv?.heating_kwh_m2 != null) { <strong>{{ nv!.heating_kwh_m2 | number: '1.0-0' }}</strong> kWh/m²
                      @if (nv!.heating_kwh_m2_p10 != null) { <small>entre {{ nv!.heating_kwh_m2_p10 | number: '1.0-0' }} et {{ nv!.heating_kwh_m2_p90 | number: '1.0-0' }}</small> }
                      @if (nv!.a_jour === false) { <small class="ko">à refaire</small> } } @else { — }</td>
                    <td class="num">@if (affichage() === 'N0') { <small>non calculé</small> } @else { {{ fmt(nv?.dh_re2020) }} }</td>
                    <td class="num">{{ fmt(nv?.primary_kwh_m2) }}</td>
                    <td class="num">{{ fmt(nv?.hours_over_28) }}</td>
                  }
                  <td>@if (r.dpe) { <span class="dpe" [attr.data-l]="r.dpe">{{ r.dpe }}</span> } @else { — }</td>
                  <td class="num">{{ r.priority === null ? '—' : (r.priority | number: '1.0-0') }}</td>
                </tr>
              } @empty { <tr><td [attr.colspan]="affichage() === 'trois' ? 11 : 10" class="rf-hint">{{ ctx.chargement() ? 'Chargement…' : 'Aucun bâtiment. Ajoutez-en depuis « Ajouter des bâtiments ».' }}</td></tr> }
            </tbody>
          </table>
        </div>
        <p class="rf-hint">Cliquez sur une ligne pour ouvrir le bâtiment. Survolez un titre de colonne ou une pastille pour son explication.</p>
      }
    </section>
  `,
})
export class ParcBatimentsComponent implements OnInit, OnDestroy {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private http = inject(HttpClient);
  private router = inject(Router);

  usageLabels = USAGE_LABELS;
  statutMots = STATUT_MOTS;
  apLabels = APPROFONDISSEMENT_LABELS;
  apClasse = APPRO_CLASSE;
  error = signal('');
  info = signal('');
  mode = signal<'table' | 'carte'>('table');
  colorBy = signal<'besoin' | 'confiance' | 'chaleur' | 'etiquette'>('besoin');
  sortKey = signal<SortKey>('priority');
  sortDesc = signal(true);
  filterUsage = signal('');
  filterStatus = signal('');
  search = signal('');
  affichage = signal<Affichage>('N1');
  readonly niveaux = NIVEAUX;
  readonly mots = NIVEAU_MOTS;
  readonly calculMots = CALCUL_MOTS;
  readonly calculClasse = CALCUL_CLASSE;
  readonly affichages: [Affichage, string][] = [
    ['N0', 'Mensuel (N0)'], ['N1', 'Horaire simplifié (N1)'], ['N2', 'Détaillé par paroi (N2)'], ['trois', 'Les trois côte à côte'],
  ];
  private etats = inject(EtatCalculsService);
  private timer?: ReturnType<typeof setInterval>;

  /** Niveau utilisé pour la carte, le tri par défaut et les colonnes « un niveau » (N1 en mode « les trois »). */
  readonly niveauCourant = computed<Niveau>(() => { const a = this.affichage(); return a === 'trois' ? 'N1' : a; });
  readonly motsNiveau = computed(() => NIVEAU_MOTS[this.niveauCourant()].toLowerCase());
  readonly suiviParc = computed(() => { const id = this.ctx.parcId(); return id !== null && !this.ctx.horsParc() ? this.etats.parcs()[id] ?? null : null; });
  readonly phraseCalculs = computed(() => {
    const r = this.suiviParc()?.etat?.resume;
    if (!r) return '';
    if (!r.batiments) return 'Aucun calcul à suivre.';
    let s = `Calculs : ${r.a_jour} bâtiment${r.a_jour > 1 ? 's' : ''} à jour sur ${r.batiments}`;
    if (r.planifies) s += `, ${r.planifies} recalcul${r.planifies > 1 ? 's' : ''} prévu${r.planifies > 1 ? 's' : ''}`;
    if (r.en_cours) s += `, ${r.en_cours} en cours`;
    if (r.erreurs) s += `, ${r.erreurs} en erreur`;
    if (r.calculs_restants) s += ` — ${r.calculs_restants} calcul${r.calculs_restants > 1 ? 's' : ''} restant${r.calculs_restants > 1 ? 's' : ''} (le tableau se met à jour de lui-même)`;
    return s + '.';
  });
  readonly avancementCalculs = computed(() => {
    const r = this.suiviParc()?.etat?.resume;
    return r && r.batiments ? Math.round((r.a_jour / r.batiments) * 100) : 0;
  });

  private compte = computed(() => {
    const rows = this.ctx.batiments();
    const n = (s: string) => rows.filter(r => r.status === s).length;
    return { total: rows.length, complet: n('complet'), incomplet: n('incomplet'), attente: n('en_attente') + n('en_cours') };
  });

  etat = computed(() => {
    const c = this.compte();
    if (!c.total) return this.ctx.chargement() ? 'Chargement des bâtiments…' : 'Ce parc ne contient encore aucun bâtiment.';
    const analyses = c.complet + c.incomplet;
    let s = `${analyses} bâtiment${analyses > 1 ? 's' : ''} analysé${analyses > 1 ? 's' : ''} sur ${c.total}`;
    if (c.incomplet) s += `, ${c.incomplet} à vérifier (analyse automatique incomplète)`;
    if (c.attente) s += `, ${c.attente} en cours d'analyse`;
    const sansCalcul = this.ctx.batiments().filter(r => r.heating_kwh_m2 === null).length;
    if (sansCalcul && !c.attente) s += `, ${sansCalcul} sans résultat de calcul`;
    return s + '.';
  });

  avancement = computed(() => {
    const c = this.compte();
    return c.total ? Math.round(((c.complet + c.incomplet) / c.total) * 100) : 0;
  });

  view = computed(() => {
    const k = this.sortKey(), d = this.sortDesc() ? -1 : 1, u = this.filterUsage(), st = this.filterStatus();
    const q = this.search().trim().toLowerCase();
    const val = (r: ParcRow): number | string | null => this.valeurTri(r, k);
    return this.ctx.batiments()
      .filter(r => (!u || r.usage === u) && (!st || r.status === st) &&
        (!q || (r.name || '').toLowerCase().includes(q) || (r.address || '').toLowerCase().includes(q)))
      .slice()
      .sort((a, b) => {
        const va = val(a), vb = val(b);
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return (va < vb ? -1 : va > vb ? 1 : 0) * d;
      });
  });

  constructor() {
    // Progression des calculs du parc (I2) : suivie tant que la vue est ouverte ; un calcul fini relit le tableau.
    effect(onCleanup => {
      const id = this.ctx.parcId();
      if (id === null || this.ctx.horsParc()) return;
      const liberer = untracked(() => this.etats.suivreParc(id));
      onCleanup(liberer);
    });
    let avance = -1;
    effect(() => {
      const a = this.suiviParc()?.avance ?? -1;
      untracked(() => { if (avance >= 0 && a > avance) void this.ctx.rafraichirBatiments(); avance = a; });
    });
  }

  choisirAffichage(a: Affichage): void {
    this.affichage.set(a);
    const k = this.sortKey();
    if (a === 'trois' && ['besoin', 'dh', 'ep', 'h28'].includes(k)) this.sortKey.set(k === 'dh' ? 'dh_N1' : 'besoin_N1');
    else if (a !== 'trois' && k.includes('_N')) this.sortKey.set(k.startsWith('dh') ? 'dh' : 'besoin');
  }

  /** Valeurs d'un niveau pour une ligne (I4 `niveaux`) ; sans `niveaux` (serveur antérieur), N1 depuis les champs actuels. */
  niv(r: ParcRow, n: Niveau): NiveauLigne | null {
    const l = (r as LigneParc).niveaux?.[n];
    if (l) return l;
    if (n !== 'N1' || (r as LigneParc).niveaux) return null;
    return { heating_kwh_m2: r.heating_kwh_m2, heating_kwh_m2_p10: r.p10, heating_kwh_m2_p90: r.p90, primary_kwh_m2: r.primary_kwh_m2,
             dh_re2020: null, hours_over_28: r.hours_over_28, a_jour: null, run_id: null };
  }

  /** État des calculs d'un bâtiment : suivi du parc (le plus frais), sinon la ligne du tableau (I4 `calculs`). */
  statutCalcul(r: ParcRow): { statut: StatutRecalcul; restants: number } | null {
    const e = this.suiviParc()?.etat?.batiments.find(b => b.id === r.id);
    if (e) return { statut: e.statut, restants: e.calculs_restants };
    const c = (r as LigneParc).calculs;
    return c ? { statut: c.statut, restants: c.restants } : null;
  }
  titreCalcul(s: StatutRecalcul, n: number): string {
    switch (s) {
      case 'planifie': return `Une donnée a changé : ${n} calcul${n > 1 ? 's' : ''} seront refaits d'eux-mêmes sous peu.`;
      case 'en_cours': return `Calculs en cours : ${n} restant${n > 1 ? 's' : ''}.`;
      case 'erreur': return 'Le dernier recalcul a échoué : ouvrez le bâtiment pour le détail.';
      default: return n ? `${n} calcul${n > 1 ? 's' : ''} à refaire (recalcul automatique coupé ?).` : 'Tous les calculs sont à jour.';
    }
  }

  fmt(x: number | null | undefined): string {
    return x === null || x === undefined ? '—' : Math.round(x).toLocaleString('fr-FR');
  }

  private valeurTri(r: ParcRow, k: SortKey): number | string | null {
    const n = this.niveauCourant();
    switch (k) {
      case 'appro': return r.approfondissement ? APPRO_RANG[r.approfondissement.niveau] ?? 0 : null;
      case 'calculs': { const c = this.statutCalcul(r); return c ? CALCUL_RANG[c.statut] * 1000 + c.restants : null; }
      case 'besoin': return this.niv(r, n)?.heating_kwh_m2 ?? null;
      case 'dh': return this.niv(r, n)?.dh_re2020 ?? null;
      case 'ep': return this.niv(r, n)?.primary_kwh_m2 ?? null;
      case 'h28': return this.niv(r, n)?.hours_over_28 ?? null;
      case 'besoin_N0': case 'besoin_N1': case 'besoin_N2': return this.niv(r, k.slice(-2) as Niveau)?.heating_kwh_m2 ?? null;
      case 'dh_N1': case 'dh_N2': return this.niv(r, k.slice(-2) as Niveau)?.dh_re2020 ?? null;
      default: return r[k] as number | string | null;
    }
  }

  ngOnInit(): void {
    // Analyse en cours : on rafraîchit le tableau tant qu'il reste des bâtiments en attente.
    this.timer = setInterval(() => { if (this.compte().attente) void this.ctx.rafraichirBatiments(); }, 5000);
  }
  ngOnDestroy(): void { clearInterval(this.timer); }

  ouvrir(id: number): void {
    this.ctx.choisirBatiment(id);
    void this.router.navigate(['/batiment'], { queryParams: this.ctx.queryParams() });
  }

  titreModele(m: ModeleUtilise): string {
    const date = m.computed_at ? new Date(m.computed_at).toLocaleDateString('fr-FR') : '';
    return `${m.niveau} — ${m.libelle}${date ? ', calculé le ' + date : ''} : ` +
      (m.a_jour ? 'à jour avec la fiche actuelle.' : 'la fiche a changé depuis ; ce résultat est à refaire.');
  }

  lancer(tout: boolean): void {
    const id = this.ctx.parcId();
    if (!id || this.ctx.horsParc()) return;
    this.error.set(''); this.info.set('');
    this.rf.post(`/parcs/${id}/lancer/`, { tout }).subscribe({
      next: () => { this.info.set(tout ? 'Recalcul de tout le parc lancé.' : 'Analyse relancée pour les bâtiments à vérifier.'); void this.ctx.rafraichirBatiments(); },
      error: e => this.error.set(e?.error?.detail ?? 'Lancement refusé.'),
    });
  }

  exportCsv(): void {
    const id = this.ctx.parcId();
    if (!id || this.ctx.horsParc()) return;
    this.http.get(`${apiUrl()}/api/parcs/${id}/export.csv`, { responseType: 'blob' }).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `parc-${id}.csv`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
      error: () => this.error.set('Export impossible.'),
    });
  }

  sort(k: SortKey): void {
    if (this.sortKey() === k) this.sortDesc.update(d => !d); else { this.sortKey.set(k); this.sortDesc.set(k !== 'name' && k !== 'usage'); }
  }
  arrow(k: SortKey): string { return this.sortKey() === k ? (this.sortDesc() ? '↓' : '↑') : ''; }
  gauge(x: number): string { return x < 0.35 ? 'low' : x < 0.65 ? 'mid' : 'high'; }

  // ── Carte ──
  private tok(n: string): string { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

  private colorOf(r: ParcRow): string {
    const ok = this.tok('--success'), mid = this.tok('--warning'), bad = this.tok('--danger'), none = this.tok('--text-mute');
    switch (this.colorBy()) {
      case 'besoin': { const h = this.niv(r, this.niveauCourant())?.heating_kwh_m2 ?? null; return h === null ? none : h <= 80 ? ok : h <= 180 ? mid : bad; }
      case 'confiance': return r.confidence < 0.35 ? bad : r.confidence < 0.65 ? mid : ok;
      case 'chaleur': return r.hours_over_28 === null ? none : r.hours_over_28 < 50 ? ok : r.hours_over_28 < 300 ? mid : bad;
      default: { const l = r.model_label ?? r.dpe; return !l ? none : 'AB'.includes(l) ? ok : 'CD'.includes(l) ? mid : bad; }
    }
  }

  legend(): [string, string][] {
    const ok = this.tok('--success'), mid = this.tok('--warning'), bad = this.tok('--danger');
    switch (this.colorBy()) {
      case 'besoin': return [[ok, '≤ 80 kWh/m²'], [mid, '80–180'], [bad, '> 180']];
      case 'confiance': return [[bad, '< 35 %'], [mid, '35–65 %'], [ok, '> 65 %']];
      case 'chaleur': return [[ok, '< 50 h au-dessus de 28 °C'], [mid, '50–300 h'], [bad, '> 300 h']];
      default: return [[ok, 'A–B'], [mid, 'C–D'], [bad, 'E–G']];
    }
  }

  private etiquette(r: ParcRow): string {
    const h = this.niv(r, this.niveauCourant())?.heating_kwh_m2 ?? null;
    return `${r.name || r.address} — ${h !== null ? Math.round(h) + ' kWh/m²·an (' + this.motsNiveau() + ')' : 'non calculé (' + this.motsNiveau() + ')'}`;
  }

  points(): MapPoint[] {
    return this.view().filter(r => r.lat !== null && r.lon !== null).map(r => ({
      id: r.id, lat: r.lat as number, lon: r.lon as number, color: this.colorOf(r),
      label: this.etiquette(r),
      selected: r.id === this.ctx.batimentId(),
    }));
  }
}
