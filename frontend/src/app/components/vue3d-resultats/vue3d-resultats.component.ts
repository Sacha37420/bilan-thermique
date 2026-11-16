import { Component, DestroyRef, computed, inject, input, linkedSignal, output, signal } from '@angular/core';
import { MeshViewerComponent, ViewerTriangle } from '../mesh-viewer/mesh-viewer.component';
import { ThemeService } from '../../core/theme.service';
import { couleurA, echelle } from '../graphe/palette';
import { GRANDEURS, ScenePartriangle } from './vue3d.types';

/** Nombre de teintes distinctes de l'échelle : le viewer résout chaque couleur une fois (cache). */
const PAS = 100;
const SANS_VALEUR = '--border';
const SELECTION = '--text';

const nf = (v: number) => v.toLocaleString('fr-FR', { maximumFractionDigits: Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2 });

/** Quantile (interpolation linéaire) d'un tableau trié. */
function quantile(tri: number[], q: number): number {
  const pos = (tri.length - 1) * q;
  const i = Math.floor(pos), f = pos - i;
  return i + 1 < tri.length ? tri[i] + (tri[i + 1] - tri[i]) * f : tri[i];
}

function nomsMois(mois: number[] | undefined): string {
  return (mois ?? []).map(m => new Date(2000, m - 1, 1).toLocaleString('fr-FR', { month: 'short' })).join(', ') || '—';
}

/**
 * Résultats N2 par triangle (contrat bt-potentiels, I4) sur le maillage réellement calculé : une grandeur à la
 * fois, échelle séquentielle ou divergente tirée des jetons, bornes robustes (percentiles 2–98), légende, et
 * panneau de toutes les grandeurs du triangle touché. Suit le thème (clair/sombre, manuel ou système).
 */
@Component({
  selector: 'app-vue3d-resultats',
  standalone: true,
  imports: [MeshViewerComponent],
  template: `
    @if (scene(); as s) {
      <div class="v3-tete">
        @if (choixGrandeur() && presentes().length > 1) {
          <label class="rf-field v3-choix">
            <span>Grandeur affichée</span>
            <select (change)="choisir($any($event.target).value)">
              @for (g of presentes(); track g.cle) { <option [value]="g.cle" [selected]="g.cle === courante()">{{ g.libelle }}</option> }
            </select>
          </label>
        }
        <p class="rf-hint v3-periodes">Hiver : {{ hiver() }} · Été : {{ ete() }}</p>
      </div>

      <div class="v3-legende" aria-hidden="true">
        <p class="v3-titre">{{ titre() }}@if (uniteAffichee()) { <span> ({{ uniteAffichee() }})</span> }</p>
        @if (bornes(); as b) {
          <div class="v3-barre" [style.background]="degrade()"></div>
          <div class="v3-bornes"><span>{{ fmt(b.lo) }}</span><span>{{ fmt(b.hi) }}</span></div>
          @if (b.ecrete) { <p class="rf-hint">Valeurs extrêmes écrêtées (2 % de chaque côté).</p> }
        } @else {
          <p class="rf-hint">Aucune valeur pour cette grandeur.</p>
        }
        <p class="v3-sans"><i class="v3-pastille"></i> Hors volume / sans valeur</p>
      </div>

      <div class="v3-scene" role="img" [attr.aria-label]="resume()" [style.height.px]="hauteur()">
        <app-mesh-viewer [vertices]="s.vertices" [triangles]="tris()" [colorForTriangle]="couleurDe()"
                         [camera]="camera()" [pickable]="true" (triangleClick)="toucher($event)" />
      </div>

      @if (detail(); as d) {
        <section class="rf-card v3-detail" aria-live="polite" aria-label="Valeurs du triangle touché">
          <div class="rf-card-head">
            <h3>Triangle n° {{ d.i }}</h3>
            <button type="button" class="rf-btn" (click)="fermer()">Fermer</button>
          </div>
          <p class="rf-hint">Paroi : <strong>{{ d.groupe }}</strong> · type : {{ d.type }}</p>
          <dl>
            @for (l of d.lignes; track l.cle) {
              <div><dt>{{ l.libelle }}</dt><dd>{{ l.valeur }}</dd></div>
            }
          </dl>
        </section>
      }
    } @else {
      <p class="rf-hint">Aucune scène à afficher.</p>
    }
  `,
  styles: [`
    :host { display: block; }
    .v3-tete { display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: flex-end; justify-content: space-between; }
    .v3-choix select { min-height: 44px; max-width: 100%; }
    .v3-periodes { margin: 0; }
    .v3-legende { margin: var(--space-3) 0; max-width: 420px; }
    .v3-titre { margin: 0 0 var(--space-2); font-weight: 600; }
    .v3-titre span { font-weight: 400; color: var(--text-mute); }
    .v3-barre { height: 12px; border-radius: var(--radius-full); border: 1px solid var(--border); }
    .v3-bornes { display: flex; justify-content: space-between; font-size: var(--text-sm); color: var(--text-mute);
      font-variant-numeric: tabular-nums; margin-bottom: var(--space-2); }
    .v3-sans { display: flex; align-items: center; gap: var(--space-2); margin: 0; font-size: var(--text-sm); color: var(--text-mute); }
    .v3-pastille { display: inline-block; width: 14px; height: 14px; border-radius: 3px; background: var(--border);
      border: 1px solid var(--text-mute); }
    .v3-scene { width: 100%; min-height: 320px; background: var(--bg); border-radius: var(--radius-md); }
    .v3-detail { margin-top: var(--space-3); }
    .v3-detail h3 { margin: 0; flex: 1 1 auto; font-size: var(--text-md, 1rem); }
    .v3-detail .rf-btn { min-height: 44px; min-width: 44px; }
    .v3-detail dl { display: grid; grid-template-columns: 1fr; gap: var(--space-1); margin: 0; }
    .v3-detail dl div { display: flex; justify-content: space-between; gap: var(--space-3); border-bottom: 1px solid var(--border);
      padding: var(--space-1) 0; }
    .v3-detail dt { color: var(--text-mute); }
    .v3-detail dd { margin: 0; font-variant-numeric: tabular-nums; text-align: right; }
    @media (min-width: 900px) { .v3-detail dl { grid-template-columns: 1fr 1fr; column-gap: var(--space-5); } }
  `],
})
export class Vue3dResultatsComponent {
  private theme = inject(ThemeService);

  readonly scene = input<ScenePartriangle | null>(null);
  readonly grandeur = input<string>('pertes_hiver_kwh_m2');
  readonly camera = input<string>('iso');
  readonly choixGrandeur = input<boolean>(true);
  readonly hauteur = input<number>(420);
  readonly libelle = input<string | null | undefined>(null);
  readonly unite = input<string | null | undefined>(null);

  /** Grandeur choisie par l'utilisateur (émise pour qui veut la suivre). */
  readonly grandeurChange = output<string>();

  /** Change quand le thème système bascule (le thème manuel est suivi par `ThemeService.theme()`). */
  private systeme = signal(0);
  /** Triangle touché ; oublié quand la scène change. */
  readonly selection = linkedSignal<number | null>(() => { this.scene(); return null; });

  /** Grandeur affichée : celle demandée, remplaçable au sélecteur, ramenée à une grandeur présente. */
  readonly choisie = linkedSignal(() => this.grandeur());
  readonly presentes = computed(() => {
    const g = this.scene()?.grandeurs ?? {};
    const connues = GRANDEURS.filter(x => Array.isArray(g[x.cle]));
    const autres = Object.keys(g).filter(k => Array.isArray(g[k]) && !GRANDEURS.some(x => x.cle === k))
      .map(k => ({ cle: k, libelle: k, echelle: 'sequentielle' as const }));
    return [...connues, ...autres];
  });
  readonly courante = computed(() => {
    const c = this.choisie(), p = this.presentes();
    return p.some(x => x.cle === c) ? c : (p[0]?.cle ?? c);
  });
  private def = computed(() => this.presentes().find(x => x.cle === this.courante()) ?? null);
  private demandee = computed(() => this.courante() === this.grandeur());

  readonly titre = computed(() => (this.demandee() && this.libelle()) || this.def()?.libelle || this.courante());
  readonly uniteAffichee = computed(() =>
    (this.demandee() && this.unite()) || this.scene()?.unites?.[this.courante()] || '');

  readonly tris = computed<ViewerTriangle[]>(() => (this.scene()?.triangles ?? []).map(t => ({ v: t })));
  readonly hiver = computed(() => nomsMois(this.scene()?.periodes?.hiver));
  readonly ete = computed(() => nomsMois(this.scene()?.periodes?.ete));

  private valeurs = computed<(number | null)[]>(() => this.scene()?.grandeurs?.[this.courante()] ?? []);

  /** Bornes robustes : percentiles 2 et 98 des valeurs présentes hors triangles « hors_volume ». */
  readonly bornes = computed(() => {
    const s = this.scene(), v = this.valeurs();
    if (!s) return null;
    const ok: number[] = [];
    v.forEach((x, i) => { if (x !== null && Number.isFinite(x) && s.type[i] !== 'hors_volume') ok.push(x); });
    if (!ok.length) return null;
    ok.sort((a, b) => a - b);
    const lo = quantile(ok, 0.02), hi = quantile(ok, 0.98);
    return { lo, hi, ecrete: lo > ok[0] || hi < ok[ok.length - 1] };
  });

  /** Échelle résolue dans le thème courant (dépend du thème : relue à chaque bascule). */
  private echelleResolue = computed(() => {
    this.theme.theme(); this.systeme();
    return echelle(this.def()?.echelle === 'divergente' ? 'divergente' : 'sequentielle');
  });
  readonly degrade = computed(() => {
    const e = this.echelleResolue();
    return `linear-gradient(to right, ${e.map(([p, c]) => `${c} ${Math.round(p * 100)}%`).join(', ')})`;
  });

  /** Couleur de chaque triangle (jeton ou couleur résolue), recalculée à chaque grandeur, scène ou thème. */
  private couleurs = computed<string[]>(() => {
    const s = this.scene(), v = this.valeurs(), b = this.bornes(), e = this.echelleResolue();
    const teintes = Array.from({ length: PAS + 1 }, (_, k) => couleurA(e, k / PAS));
    const n = s?.triangles.length ?? 0;
    const c: string[] = new Array(n);
    for (let i = 0; i < n; i++) {
      const x = v[i];
      if (!b || x === null || x === undefined || !Number.isFinite(x) || s!.type[i] === 'hors_volume') { c[i] = SANS_VALEUR; continue; }
      const t = b.hi > b.lo ? (x - b.lo) / (b.hi - b.lo) : 0.5;
      c[i] = teintes[Math.round(Math.min(1, Math.max(0, t)) * PAS)];
    }
    return c;
  });

  /** Nouvelle fonction à chaque changement (couleurs, sélection) : le viewer repeint. */
  readonly couleurDe = computed<(i: number) => string>(() => {
    const c = this.couleurs(), sel = this.selection();
    return (i: number) => (i === sel ? SELECTION : c[i] ?? SANS_VALEUR);
  });

  readonly resume = computed(() => {
    const b = this.bornes(), u = this.uniteAffichee(), t = this.titre();
    const nom = t.charAt(0).toLowerCase() + t.slice(1);
    return b
      ? `Vue 3D : ${nom}, de ${nf(b.lo)} à ${nf(b.hi)}${u ? ' ' + u : ''} ; touchez un triangle pour ses valeurs.`
      : `Vue 3D : ${nom}, aucune valeur disponible.`;
  });

  readonly detail = computed(() => {
    const i = this.selection(), s = this.scene();
    if (i === null || !s || i < 0 || i >= s.triangles.length) return null;
    const lignes = this.presentes().map(g => {
      const x = s.grandeurs[g.cle]?.[i];
      const u = s.unites?.[g.cle] ?? '';
      return { cle: g.cle, libelle: g.libelle, valeur: x === null || x === undefined || !Number.isFinite(x) ? '—' : `${nf(x)}${u ? ' ' + u : ''}` };
    });
    return { i, groupe: s.groupes?.[i] ?? '—', type: s.type?.[i] ?? '—', lignes };
  });

  constructor() {
    let mq: MediaQueryList | null = null;
    try { mq = window.matchMedia('(prefers-color-scheme: dark)'); } catch { mq = null; }
    const suivre = () => this.systeme.update(x => x + 1);
    mq?.addEventListener('change', suivre);
    inject(DestroyRef).onDestroy(() => mq?.removeEventListener('change', suivre));
  }

  fmt(v: number): string { return nf(v); }
  choisir(cle: string): void { this.choisie.set(cle); this.grandeurChange.emit(cle); }
  toucher(i: number): void { this.selection.set(i); }
  fermer(): void { this.selection.set(null); }
}
