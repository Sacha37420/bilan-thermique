import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { DecimalPipe, KeyValuePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { apiUrl } from '../../core/env';
import { APPROFONDISSEMENT_LABELS, ModeleUtilise, ParcRow, USAGE_LABELS } from '../../core/refonte.types';
import { CarteComponent, MapPoint } from '../../components/carte/carte.component';

type SortKey = 'name' | 'usage' | 'surface_m2' | 'heating_kwh_m2' | 'hours_over_28' | 'confidence' | 'priority' | 'dpe' | 'appro';
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
  styleUrl: './parc-commun.scss',
  template: `
    @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
    @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }

    <section class="rf-card" aria-labelledby="pb-etat">
      <div class="rf-card-head"><h2 id="pb-etat">{{ ctx.parc()?.name }}</h2></div>
      <p class="etat">{{ etat() }}</p>
      <div class="prog" role="progressbar" [attr.aria-valuenow]="avancement()" aria-valuemin="0" aria-valuemax="100"
           aria-label="Part des bâtiments analysés"><i [style.width.%]="avancement()"></i></div>
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
              <th>Calculs faits</th>
              <th class="num"><button type="button" (click)="sort('heating_kwh_m2')" title="Besoin de chauffage par m² et par an (calcul horaire simplifié), avec sa fourchette probable">Besoin de chauffage {{ arrow('heating_kwh_m2') }}</button></th>
              <th class="num"><button type="button" (click)="sort('hours_over_28')" title="Heures par an où l'intérieur dépasse 28 °C sans climatisation">Heures &gt; 28 °C {{ arrow('hours_over_28') }}</button></th>
              <th><button type="button" (click)="sort('dpe')" title="Étiquette du DPE officiel s'il existe (ce n'est pas un DPE calculé par l'app)">DPE {{ arrow('dpe') }}</button></th>
              <th class="num"><button type="button" (click)="sort('priority')" title="Priorité = besoin × (0,5 + 0,5 × confiance) : un bâtiment gourmand et bien connu passe devant">Priorité {{ arrow('priority') }}</button></th>
            </tr></thead>
            <tbody>
              @for (r of view(); track r.id) {
                <tr class="clic" [class.courant]="r.id === ctx.batimentId()" tabindex="0" (click)="ouvrir(r.id)" (keydown.enter)="ouvrir(r.id)">
                  <td><button type="button" class="link" (click)="$event.stopPropagation(); ouvrir(r.id)">{{ r.name || r.address || ('Bâtiment ' + r.id) }}</button>
                    <small>{{ r.address }}@if (usageLabels[r.usage ?? '']) { · {{ usageLabels[r.usage ?? ''] }} }@if (r.surface_m2) { · {{ r.surface_m2 | number: '1.0-0' }} m² }
                      @if (r.status !== 'complet') { · <span [class.ko]="r.status === 'incomplet'">{{ statutMots[r.status] }}</span> }</small></td>
                  <td><span class="rf-gauge" [title]="'Confiance ' + (r.confidence * 100 | number: '1.0-0') + ' %, complétude ' + (r.completeness * 100 | number: '1.0-0') + ' %'">
                    <span [class]="'rf-gauge-bar ' + gauge(r.confidence)"><i [style.width.%]="r.confidence * 100"></i></span>{{ r.confidence * 100 | number: '1.0-0' }} %</span></td>
                  <td>@if (r.approfondissement; as a) {
                      <span [class]="'rf-badge ' + (apClasse[a.niveau] || 'm-defaut')" [title]="a.detail || apLabels[a.niveau]">{{ a.libelle || apLabels[a.niveau] }}</span>
                    } @else { <span class="rf-hint">—</span> }</td>
                  <td>@if (r.modeles?.length) {
                      <span class="pastilles">@for (m of r.modeles; track m.niveau) {
                        <span class="pastille" [class.ok]="m.a_jour" [class.perime]="!m.a_jour" [title]="titreModele(m)"><b>{{ m.niveau }}</b> {{ m.libelle }}{{ m.a_jour ? '' : ' · ancien' }}</span>
                      }</span>
                    } @else { <span class="rf-hint">aucun</span> }</td>
                  <td class="num">@if (r.heating_kwh_m2 !== null) { <strong>{{ r.heating_kwh_m2 | number: '1.0-0' }}</strong> kWh/m²
                    @if (r.p10 !== null) { <small>entre {{ r.p10 | number: '1.0-0' }} et {{ r.p90 | number: '1.0-0' }}</small> } } @else { — }</td>
                  <td class="num">{{ r.hours_over_28 ?? '—' }}</td>
                  <td>@if (r.dpe) { <span class="dpe" [attr.data-l]="r.dpe">{{ r.dpe }}</span> } @else { — }</td>
                  <td class="num">{{ r.priority === null ? '—' : (r.priority | number: '1.0-0') }}</td>
                </tr>
              } @empty { <tr><td colspan="8" class="rf-hint">{{ ctx.chargement() ? 'Chargement…' : 'Aucun bâtiment. Ajoutez-en depuis « Ajouter des bâtiments ».' }}</td></tr> }
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
  private timer?: ReturnType<typeof setInterval>;

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
    const val = (r: ParcRow): number | string | null =>
      k === 'appro' ? (r.approfondissement ? APPRO_RANG[r.approfondissement.niveau] ?? 0 : null) : (r[k] as number | string | null);
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
      case 'besoin': return r.heating_kwh_m2 === null ? none : r.heating_kwh_m2 <= 80 ? ok : r.heating_kwh_m2 <= 180 ? mid : bad;
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

  points(): MapPoint[] {
    return this.view().filter(r => r.lat !== null && r.lon !== null).map(r => ({
      id: r.id, lat: r.lat as number, lon: r.lon as number, color: this.colorOf(r),
      label: `${r.name || r.address} — ${r.heating_kwh_m2 !== null ? Math.round(r.heating_kwh_m2) + ' kWh/m²·an' : 'non calculé'}`,
      selected: r.id === this.ctx.batimentId(),
    }));
  }
}
