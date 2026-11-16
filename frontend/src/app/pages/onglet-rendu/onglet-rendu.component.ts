import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { firstValueFrom, map } from 'rxjs';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { CarteComponent, MapPoint } from '../../components/carte/carte.component';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ThemeService } from '../../core/theme.service';
import { apiUrl } from '../../core/env';
import { APPROFONDISSEMENT_LABELS, METHOD_LABELS, NiveauApprofondissement, ParcRow, USAGE_LABELS } from '../../core/refonte.types';
import { Reglages, ValeurReglage, diffDefauts, egal, messageErreur } from '../onglet-parametrage/reglages.types';
import {
  ADDITIFS, BlocFaits, COLONNES, FaitIdentite, FicheIdentite, HAUT_EST_BIEN, INDICATEURS, Indicateurs, NIVEAUX,
  RENDU_DEFAUTS, SECTIONS, TRIS,
} from './rendu.types';

type Vue = 'parc' | 'batiment';
type Niveau = 'N0' | 'N1' | 'N2';

const NIVEAUX_APP: NiveauApprofondissement[] = ['automatique', 'complete', 'documente', 'mesure', 'expert'];
const CLES_GEOMETRIE = ['hauteur', 'emprise', 'niveaux', 'mitoyennete', 'surface', 'orientation', 'compacite', 'volume'];

function hexVersRgb(c: string): [number, number, number] | null {
  const s = c.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(s);
  if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) return [0, 1, 2].map(i => parseInt(m![1][i] + m![1][i], 16)) as [number, number, number];
  m = /^rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i.exec(s);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}
function melange(a: string, b: string): string {
  const x = hexVersRgb(a), y = hexVersRgb(b);
  if (!x || !y) return a;
  return `rgb(${x.map((v, i) => Math.round((v + y[i]) / 2)).join(', ')})`;
}
function jeton(nom: string): string { return getComputedStyle(document.documentElement).getPropertyValue(nom).trim(); }
function mediane(v: number[]): number | null {
  if (!v.length) return null;
  const s = [...v].sort((a, b) => a - b), k = Math.floor(s.length / 2);
  return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
}
function quantile(s: number[], q: number): number {
  const p = (s.length - 1) * q, b = Math.floor(p);
  return s[b + 1] !== undefined ? s[b] + (p - b) * (s[b + 1] - s[b]) : s[b];
}

/**
 * Onglet Rendu (chantier bt-navigation) : synthèse imprimable du parc (chiffres clés, carte colorée,
 * classement, tableau) et fiche d'identité d'un bâtiment. La mise en page est un réglage du parc,
 * enregistré par son gestionnaire ; chacun peut l'ajuster localement pour son aperçu.
 */
@Component({
  selector: 'app-onglet-rendu',
  standalone: true,
  imports: [ContexteBarComponent, CarteComponent, RouterLink],
  templateUrl: './onglet-rendu.component.html',
  styleUrl: './onglet-rendu.component.scss',
})
export class OngletRenduComponent {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private theme = inject(ThemeService);

  readonly COLONNES = COLONNES;
  readonly SECTIONS = SECTIONS;
  readonly NIVEAUX = NIVEAUX;
  readonly METHOD_LABELS = METHOD_LABELS as Record<string, string>;
  readonly ordreSections = Object.keys(SECTIONS);
  readonly niveauxListe: Niveau[] = ['N0', 'N1', 'N2'];
  readonly aujourdhui = new Date().toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });

  readonly vue = toSignal(this.route.queryParamMap.pipe(map(q => (q.get('vue') === 'batiment' ? 'batiment' : 'parc') as Vue)),
    { initialValue: 'parc' as Vue });

  // ── Réglages de rendu ──
  readonly reglages = signal<Reglages | null>(null);
  readonly valeurs = signal<Record<string, ValeurReglage>>({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>) });
  readonly reglagesErreur = signal('');
  readonly info = signal('');
  readonly enregistrement = signal(false);
  readonly peutEnregistrer = computed(() => !!this.reglages()?.peut_modifier && !this.ctx.horsParc());
  readonly modifie = computed(() => !egal(this.valeurs(), this.reglages()?.valeurs ?? RENDU_DEFAUTS));
  private chargeDe: number | null | undefined = undefined;

  choix(cle: string, repli: Record<string, string>): [string, string][] {
    const e = this.reglages()?.schema.find(x => x.cle === cle);
    return Object.entries(e?.choix ?? repli);
  }
  v<T = string>(cle: string): T { return this.valeurs()[cle] as T; }
  poser(cle: string, val: ValeurReglage): void { this.info.set(''); this.valeurs.update(x => ({ ...x, [cle]: val })); }
  basculer(cle: string, c: string, oui: boolean): void {
    const cur = (this.v<string[]>(cle) ?? []).filter(x => x !== c);
    this.poser(cle, oui ? [...cur, c] : cur);
  }
  a(cle: string, c: string): boolean { return (this.v<string[]>(cle) ?? []).includes(c); }

  // ── Fiche d'identité ──
  readonly fiche = signal<FicheIdentite | null>(null);
  readonly ficheErreur = signal('');
  readonly ficheChargement = signal(false);
  private ficheDe: number | null = null;

  constructor() {
    effect(() => {
      const id = this.ctx.parcId();
      untracked(() => void this.chargerReglages(id));
    });
    effect(() => {
      const b = this.ctx.batimentId();
      if (this.vue() === 'batiment') untracked(() => void this.chargerFiche(b));
    });
  }

  private async chargerReglages(id: number | null): Promise<void> {
    if (id === this.chargeDe) return;
    this.chargeDe = id;
    this.reglagesErreur.set(''); this.info.set('');
    if (id === null || id === HORS_PARC) {
      this.reglages.set(null);
      this.valeurs.set({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>) });
      return;
    }
    try {
      const r = await firstValueFrom(this.rf.get<Reglages>(`/parcs/${id}/reglages/rendu/`));
      if (this.chargeDe !== id) return;
      this.reglages.set(r);
      this.valeurs.set({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>), ...r.valeurs });
    } catch (e) {
      if (this.chargeDe !== id) return;
      this.reglages.set(null);
      this.valeurs.set({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>) });
      this.reglagesErreur.set(messageErreur(e as never, 'Réglage du rendu') + ' Mise en page par défaut.');
    }
  }

  async enregistrer(): Promise<void> {
    const r = this.reglages(), id = this.ctx.parcId();
    if (!r || !this.peutEnregistrer() || id === null) return;
    this.enregistrement.set(true); this.info.set(''); this.reglagesErreur.set('');
    try {
      const surcharges = diffDefauts(this.valeurs(), r.defauts);
      const n = await firstValueFrom(this.rf.put<Reglages>(`/parcs/${id}/reglages/rendu/`, { surcharges }));
      const frais = n?.schema ? n : await firstValueFrom(this.rf.get<Reglages>(`/parcs/${id}/reglages/rendu/`));
      this.reglages.set(frais);
      this.valeurs.set({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>), ...frais.valeurs });
      this.info.set('Mise en page enregistrée pour tout le parc.');
    } catch (e) {
      this.reglagesErreur.set(messageErreur(e as never, 'Enregistrement'));
    } finally {
      this.enregistrement.set(false);
    }
  }
  revenir(): void {
    this.valeurs.set({ ...(RENDU_DEFAUTS as Record<string, ValeurReglage>), ...(this.reglages()?.valeurs ?? {}) });
    this.info.set('');
  }

  private async chargerFiche(b: number | null): Promise<void> {
    if (b === this.ficheDe && (this.fiche() || this.ficheErreur())) return;
    this.ficheDe = b;
    this.fiche.set(null); this.ficheErreur.set('');
    if (b === null) return;
    this.ficheChargement.set(true);
    try {
      const f = await firstValueFrom(this.rf.get<FicheIdentite>(`/batiments/${b}/identite/`));
      if (this.ficheDe === b) this.fiche.set(f);
    } catch (e) {
      if (this.ficheDe === b) this.ficheErreur.set(messageErreur(e as never, 'Fiche d’identité'));
    } finally {
      if (this.ficheDe === b) this.ficheChargement.set(false);
    }
  }

  choisirVue(v: Vue): void {
    void this.router.navigate([], { queryParams: { vue: v }, queryParamsHandling: 'merge', replaceUrl: true });
  }
  imprimer(): void { window.print(); }

  exportCsv(): void {
    const id = this.ctx.parcId();
    if (id === null || id === HORS_PARC) return;
    this.http.get(`${apiUrl()}/api/parcs/${id}/export.csv`, { responseType: 'blob' }).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const el = document.createElement('a');
        el.href = url; el.download = `parc-${id}.csv`; el.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
      error: e => this.reglagesErreur.set(messageErreur(e, 'Export CSV')),
    });
  }

  // ════════ Rendu de parc ════════
  num(r: ParcRow, k: string): number | null {
    const x = (r as unknown as Record<string, unknown>)[k];
    return typeof x === 'number' && Number.isFinite(x) ? x : null;
  }

  readonly indicateur = computed(() => this.v<string>('parc.indicateur') || 'heating_kwh_m2');
  readonly titreParc = computed(() => this.v<string>('parc.titre') || titreParDefaut(this.ctx.parc()?.name ?? ''));
  readonly titreDefaut = computed(() => titreParDefaut(this.ctx.parc()?.name ?? ''));

  readonly chiffres = computed(() => {
    const rows = this.ctx.batiments();
    return [...INDICATEURS, 'surface_m2'].map(k => {
      const vals = rows.map(r => this.num(r, k)).filter((x): x is number => x !== null);
      const somme = vals.reduce((s, x) => s + x, 0);
      return { cle: k, libelle: COLONNES[k], n: vals.length, somme: ADDITIFS.has(k) && vals.length ? somme : null,
               moyenne: vals.length ? somme / vals.length : null, mediane: mediane(vals) };
    }).filter(c => c.n > 0);
  });
  readonly kpis = computed(() => {
    const rows = this.ctx.batiments();
    const surf = rows.reduce((s, r) => s + (r.surface_m2 ?? 0), 0);
    const besoin = rows.reduce((s, r) => s + (r.heating_kwh ?? 0), 0);
    const calc = rows.filter(r => r.heating_kwh_m2 !== null).length;
    const ind = this.chiffres().find(c => c.cle === this.indicateur());
    return { n: rows.length, surf, besoin, calc, ind };
  });

  readonly parApprofondissement = computed(() => {
    const rows = this.ctx.batiments();
    const out = NIVEAUX_APP.map(n => ({ cle: n, libelle: APPROFONDISSEMENT_LABELS[n], n: rows.filter(r => r.approfondissement?.niveau === n).length }));
    const sans = rows.filter(r => !r.approfondissement).length;
    if (sans) out.push({ cle: 'automatique', libelle: 'Non évalué', n: sans });
    return out.filter(x => x.n > 0).map(x => ({ ...x, pct: rows.length ? (100 * x.n) / rows.length : 0 }));
  });
  readonly parConfiance = computed(() => {
    const rows = this.ctx.batiments();
    const cl = [
      { cle: 'low', libelle: 'Faible (moins de 35 %)', n: rows.filter(r => r.confidence < 0.35).length },
      { cle: 'mid', libelle: 'Moyenne (35 à 65 %)', n: rows.filter(r => r.confidence >= 0.35 && r.confidence < 0.65).length },
      { cle: 'high', libelle: 'Bonne (65 % et plus)', n: rows.filter(r => r.confidence >= 0.65).length },
    ];
    return cl.map(x => ({ ...x, pct: rows.length ? (100 * x.n) / rows.length : 0 }));
  });

  /** Cinq couleurs, du meilleur au pire, tirées des jetons Foyer (relues à chaque changement de thème). */
  private palette = computed(() => {
    this.theme.theme();
    const ok = jeton('--success') || 'green', wa = jeton('--warning') || 'orange', da = jeton('--danger') || 'red';
    return { classes: [ok, melange(ok, wa), wa, melange(wa, da), da], vide: jeton('--text-mute') || 'gray' };
  });
  /** Échelle en 5 classes de même effectif (quintiles) de l'indicateur choisi. */
  readonly echelle = computed(() => {
    const k = this.indicateur();
    const s = this.ctx.batiments().map(r => this.num(r, k)).filter((x): x is number => x !== null).sort((a, b) => a - b);
    const pal = this.palette();
    if (!s.length) return { seuils: [] as number[], classes: [] as { de: number; a: number; couleur: string }[], vide: pal.vide };
    const seuils = [0.2, 0.4, 0.6, 0.8].map(q => quantile(s, q));
    const bornes = [s[0], ...seuils, s[s.length - 1]];
    const couleurs = HAUT_EST_BIEN.has(k) ? [...pal.classes].reverse() : pal.classes;
    return { seuils, classes: couleurs.map((c, i) => ({ de: bornes[i], a: bornes[i + 1], couleur: c })), vide: pal.vide };
  });
  private couleur(v: number | null): string {
    const e = this.echelle();
    if (v === null || !e.classes.length) return e.vide;
    const i = e.seuils.findIndex(s => v <= s);
    return e.classes[i === -1 ? 4 : i].couleur;
  }
  readonly points = computed<MapPoint[]>(() => {
    const k = this.indicateur();
    return this.ctx.batiments().filter(r => r.lat !== null && r.lon !== null).map(r => ({
      id: r.id, lat: r.lat!, lon: r.lon!, color: this.couleur(this.num(r, k)),
      label: `${r.name || r.address || 'Bâtiment ' + r.id} — ${this.cellule(r, k)}`,
    }));
  });
  readonly sansPosition = computed(() => this.ctx.batiments().filter(r => r.lat === null || r.lon === null).length);

  readonly trie = computed(() => {
    const t = this.v<string>('parc.tri') || 'name';
    const rows = [...this.ctx.batiments()];
    if (t === 'name') return rows.sort((a, b) => (a.name || a.address).localeCompare(b.name || b.address, 'fr'));
    return rows.sort((a, b) => (this.num(b, t) ?? -Infinity) - (this.num(a, t) ?? -Infinity));
  });
  /** Classement : selon le tri choisi (selon l'indicateur principal si le tri est alphabétique). */
  readonly critereClassement = computed(() => { const t = this.v<string>('parc.tri'); return !t || t === 'name' ? this.indicateur() : t; });
  readonly classement = computed(() => {
    const k = this.critereClassement();
    return this.ctx.batiments().filter(r => this.num(r, k) !== null)
      .sort((a, b) => this.num(b, k)! - this.num(a, k)!).slice(0, 10);
  });
  readonly colonnes = computed(() => (this.v<string[]>('parc.colonnes') ?? []).filter(c => c in COLONNES));
  readonly tris = TRIS;

  fmt(v: number | null, dec = 0): string {
    return v === null ? '—' : v.toLocaleString('fr-FR', { maximumFractionDigits: dec, minimumFractionDigits: 0 });
  }
  valeurIndic(k: string, v: number | null): string {
    if (v === null) return '—';
    if (k === 'confidence' || k === 'completeness') return `${this.fmt(v * 100)} %`;
    if (k === 'heating_kwh' ) return v >= 10000 ? `${this.fmt(v / 1000, 1)} MWh/an` : `${this.fmt(v)} kWh/an`;
    if (k === 'heating_kw_max') return `${this.fmt(v, 1)} kW`;
    if (k === 'hours_over_28') return `${this.fmt(v)} h`;
    if (k === 'surface_m2') return `${this.fmt(v)} m²`;
    if (k === 'priority') return this.fmt(v, 1);
    return `${this.fmt(v)} kWh/m²`;
  }
  cellule(r: ParcRow, k: string): string {
    switch (k) {
      case 'usage': return r.usage ? USAGE_LABELS[r.usage] ?? r.usage : '—';
      case 'annee': return r.annee ? String(r.annee) : '—';
      case 'dpe': return r.dpe ?? '—';
      case 'model_label': return r.model_label ?? '—';
      case 'status': return r.status || '—';
      case 'approfondissement': return r.approfondissement?.libelle ?? '—';
      default: return this.valeurIndic(k, this.num(r, k));
    }
  }
  estNum(k: string): boolean { return !['usage', 'dpe', 'model_label', 'status', 'approfondissement'].includes(k); }
  lienBatiment(r: ParcRow): Record<string, unknown> { return { p: this.ctx.parcId() ?? undefined, b: r.id }; }

  // ════════ Fiche d'identité ════════
  readonly titreBatiment = computed(() => {
    const f = this.fiche();
    return this.v<string>('batiment.titre') || (f ? (f.batiment.name || f.batiment.address || `Bâtiment ${f.batiment.id}`) : '');
  });
  readonly niveauPrefere = computed(() => (this.v<string>('batiment.niveau_prefere') || 'N1') as Niveau);
  readonly sectionsVues = computed(() => {
    const s = this.v<string[]>('batiment.sections') ?? [];
    return new Set(s);
  });
  readonly pointFiche = computed<MapPoint[]>(() => {
    const b = this.fiche()?.batiment;
    return b && b.lat !== null && b.lon !== null ? [{ id: b.id, lat: b.lat, lon: b.lon, selected: true, label: b.name || b.address }] : [];
  });
  readonly centreFiche = computed<[number, number] | null>(() => {
    const b = this.fiche()?.batiment;
    return b && b.lat !== null && b.lon !== null ? [b.lat, b.lon] : null;
  });

  /** Faits « identité » hors géométrie, et faits de géométrie (même bloc côté serveur). */
  private estGeo(cle: string): boolean { return cle.startsWith('geometrie.') || CLES_GEOMETRIE.some(m => cle.includes(m)); }
  readonly faitsIdentite = computed(() => Object.entries(this.fiche()?.identite ?? {}).filter(([k]) => !this.estGeo(k)));
  readonly faitsGeometrie = computed(() => Object.entries(this.fiche()?.identite ?? {}).filter(([k]) => this.estGeo(k)));

  /** Bloc plat `{cle: fait}` ou imbriqué `{bloc: {cle: fait}}` → groupes à afficher. */
  groupesDe(bloc: BlocFaits | Record<string, BlocFaits> | undefined | null): { nom: string; faits: [string, FaitIdentite][] }[] {
    const entries = Object.entries(bloc ?? {});
    if (!entries.length) return [];
    const plat = entries.every(([, v]) => v && typeof v === 'object' && 'valeur' in (v as object));
    const NOMS: Record<string, string> = { mur: 'Murs', murs: 'Murs', toiture: 'Toiture', plancher: 'Plancher bas', baies: 'Fenêtres',
      ventilation: 'Ventilation', chauffage: 'Chauffage', ecs: 'Eau chaude', generateur: 'Production de chaleur' };
    const nom = (k: string) => NOMS[k] ?? (k.charAt(0).toUpperCase() + k.slice(1));
    if (!plat) return entries.map(([g, faits]) => ({ nom: nom(g), faits: Object.entries(faits as BlocFaits) }));
    const out: { nom: string; faits: [string, FaitIdentite][] }[] = [];
    for (const [k, f] of entries as [string, FaitIdentite][]) {
      const g = k.split('.')[0];
      let x = out.find(o => o.nom === nom(g));
      if (!x) { x = { nom: nom(g), faits: [] }; out.push(x); }
      x.faits.push([k, f]);
    }
    return out;
  }
  readonly enveloppe = computed(() => this.groupesDe(this.fiche()?.enveloppe));
  readonly systemes = computed(() => this.groupesDe(this.fiche()?.systemes));

  valeurFait(f: FaitIdentite): string {
    if (f.texte) return f.texte;            // Lot 34 : rendu en mots par le serveur (libellés des choix, %, unités)
    const v = f.valeur;
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'boolean') return v ? 'oui' : 'non';
    if (typeof v === 'number') return `${v.toLocaleString('fr-FR', { maximumFractionDigits: 2 })}${f.unite ? ' ' + f.unite : ''}`;
    if (Array.isArray(v)) return v.join(', ');
    if (typeof v === 'object') return JSON.stringify(v);
    return `${v}${f.unite ? ' ' + f.unite : ''}`;
  }
  methodeLib(m: string): string { return this.METHOD_LABELS[m] ?? m; }
  readonly TOITS: Record<string, string> = { terrasse: 'terrasse', '2pans': 'deux pans', '4pans': 'quatre pans' };
  /** Horizon par façade en mots : « N 12° · E 0° … ». */
  horizonTxt(h: Record<string, number> | null | undefined): string {
    if (!h) return '';
    return ['N', 'E', 'S', 'O'].filter(o => h[o] !== undefined).map(o => `${o} ${this.fmt(h[o])}°`).join(' · ');
  }

  /** Niveaux dans l'ordre d'affichage : le modèle préféré d'abord. */
  readonly niveauxOrdonnes = computed(() => {
    const p = this.niveauPrefere();
    return [p, ...this.niveauxListe.filter(n => n !== p)];
  });
  res(n: Niveau): Indicateurs | null { return this.fiche()?.resultats?.[n] ?? null; }
  limitesDe(i: Indicateurs): string[] { const l = i['limites']; return Array.isArray(l) ? (l as string[]) : []; }
  nb(i: Indicateurs | null, k: string): number | null {
    const x = i?.[k];
    return typeof x === 'number' && Number.isFinite(x) ? x : null;
  }
  /** Phrases en mots d'un résultat. */
  phrases(i: Indicateurs): { lib: string; txt: string; fort?: boolean }[] {
    const out: { lib: string; txt: string; fort?: boolean }[] = [];
    const h = this.nb(i, 'heating_kwh_m2'), lo = this.nb(i, 'heating_kwh_m2_p10'), hi = this.nb(i, 'heating_kwh_m2_p90');
    if (h !== null) out.push({ lib: 'Besoin de chauffage', fort: true,
      txt: `${this.fmt(h)} kWh par m² et par an` + (lo !== null && hi !== null ? ` (vraisemblablement entre ${this.fmt(lo)} et ${this.fmt(hi)})` : '') });
    const t = this.nb(i, 'heating_kwh');
    if (t !== null) out.push({ lib: 'Soit sur l’année', txt: this.valeurIndic('heating_kwh', t) });
    const p = this.nb(i, 'heating_kw_max');
    if (p !== null) out.push({ lib: 'Puissance de chauffage au plus froid', txt: `${this.fmt(p, 1)} kW` });
    const s = this.nb(i, 'hours_over_28');
    if (s !== null) out.push({ lib: 'Surchauffe', txt: s ? `${this.fmt(s)} heures par an au-dessus de 28 °C` : 'aucune heure au-dessus de 28 °C' });
    const f = this.nb(i, 'final_kwh_m2');
    if (f !== null) out.push({ lib: 'Énergie finale (chauffage)', txt: `${this.fmt(f)} kWh par m² et par an` });
    const e = this.nb(i, 'primary_kwh_m2');
    if (e !== null) out.push({ lib: 'Énergie primaire (chauffage)', txt: `${this.fmt(e)} kWh par m² et par an` });
    const d = i['dpe_label_model'];
    if (typeof d === 'string' && d) out.push({ lib: 'Étiquette indicative du modèle', txt: `${d} (pas une étiquette DPE officielle)` });
    return out;
  }
  dateFr(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  }
  /** Référence pour comparer les améliorations : le modèle préféré, sinon le premier disponible. */
  readonly reference = computed(() => {
    for (const n of this.niveauxOrdonnes()) { const h = this.nb(this.res(n), 'heating_kwh_m2'); if (h !== null) return { n, h }; }
    return null;
  });
  ecart(i: Indicateurs | null): string {
    const ref = this.reference(), h = this.nb(i, 'heating_kwh_m2');
    if (!ref || h === null || !ref.h) return '—';
    const pct = (100 * (h - ref.h)) / ref.h;
    return `${pct > 0 ? '+' : '−'}${this.fmt(Math.abs(pct))} %`;
  }
  ecartBon(i: Indicateurs | null): boolean {
    const ref = this.reference(), h = this.nb(i, 'heating_kwh_m2');
    return !!ref && h !== null && h < ref.h;
  }
  bouquets(b: unknown[]): string {
    return (b ?? []).map(x => typeof x === 'string' ? x : (x as { label?: string; name?: string })?.label ?? (x as { name?: string })?.name ?? '').filter(Boolean).join(', ') || '—';
  }
  gauge(x: number): string { return x < 0.35 ? 'low' : x < 0.65 ? 'mid' : 'high'; }
  pct(x: number | null | undefined): string { return x === null || x === undefined ? '—' : `${Math.round(x * 100)} %`; }
}

/** « Parc <nom> », sans doubler le mot si le nom commence déjà par « parc ». */
function titreParDefaut(nom: string): string {
  return /^parc\b/i.test(nom.trim()) ? nom.trim() : `Parc ${nom}`.trim();
}
