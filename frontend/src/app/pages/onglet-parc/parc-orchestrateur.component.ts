import { Component, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { BatimentPipeline, ETAPES, MODELE_MOTS, ReglageEntree, Reglages, SOURCE_DU_GROUPE } from './parc.types';

type Filtre = '' | 'N0' | 'N1' | 'N2';

/** Onglet Parc › Sources des données (orchestrateur, contrat § 2.3) : pour chaque donnée, sa source et son mode. */
@Component({
  selector: 'app-parc-orchestrateur',
  standalone: true,
  imports: [FormsModule, DatePipe],
  styleUrl: './parc-commun.scss',
  styles: [`
    .groupe th { background: var(--bg); text-transform: none; letter-spacing: 0; }
    .groupe small { display: block; font-weight: 400; color: var(--text-mute); }
    td.centre { text-align: center; font-family: var(--font-mono); }
    .val input[type=number], .val input[type=text], .val select { min-height: 44px; min-width: 9rem; max-width: 100%; }
    .val input[type=checkbox] { width: 22px; height: 22px; }
    .val label { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 44px; cursor: pointer; }
    td small { display: block; color: var(--text-mute); font-size: var(--text-xs); }
    .actions { position: sticky; bottom: 0; background: var(--card); padding-top: var(--space-3); }
  `],
  template: `
    @if (ctx.horsParc()) {
      <p class="rf-hint" role="status">Les bâtiments hors parc suivent toujours les réglages par défaut. Ouvrez un parc pour régler ses sources.</p>
    } @else {
      @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
      @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }

      <section class="rf-card" aria-labelledby="or-h">
        <div class="rf-card-head"><h2 id="or-h">D'où viennent les données du parc</h2></div>
        <p class="rf-hint">Pour chaque donnée : la source consultée et la façon de l'utiliser. Ces choix valent pour tous les bâtiments
          de ce parc lors de leur prochaine analyse automatique.</p>
        @if (absent()) {
          <p class="rf-hint" role="status">Ces réglages ne sont pas encore disponibles sur le serveur.</p>
        } @else if (reglages(); as r) {
          <nav class="rf-tabs" aria-label="Filtrer par simulation">
            @for (f of filtres; track f[0]) {
              <button type="button" [class.active]="filtre() === f[0]" (click)="filtre.set(f[0])">{{ f[1] }}</button>
            }
          </nav>
          <div class="rf-table-wrap"><table class="rf-table">
            <thead><tr><th>Donnée</th><th>Mode</th>
              @for (m of niveaux; track m) { <th class="centre" [title]="'Utilisé par le calcul ' + m + ' (' + mots[m] + ')'">{{ m }}</th> }
              <th></th></tr></thead>
            @for (g of groupes(); track g.nom) {
              <tbody>
                <tr class="groupe"><th [attr.colspan]="3 + niveaux.length">{{ g.nom }}<small>Source : {{ source(g.nom) }}</small></th></tr>
                @for (e of g.entrees; track e.cle) {
                  <tr>
                    <td><span class="aide" [title]="e.aide">{{ e.libelle }}</span>@if (e.unite) { <small>en {{ e.unite }}</small> }</td>
                    <td class="val">
                      @if (r.peut_modifier) {
                        @switch (e.type) {
                          @case ('bool') { <label><input type="checkbox" [ngModel]="!!valeur(e)" (ngModelChange)="poser(e, $event)" [attr.aria-label]="e.libelle"> {{ valeur(e) ? 'activé' : 'désactivé' }}</label> }
                          @case ('enum') { <select [ngModel]="valeur(e)" (ngModelChange)="poser(e, $event)" [attr.aria-label]="e.libelle">
                            @for (c of choix(e); track c[0]) { <option [value]="c[0]">{{ c[1] }}</option> }</select> }
                          @case ('list') { <input type="text" [ngModel]="liste(e)" (ngModelChange)="poserListe(e, $event)" [attr.aria-label]="e.libelle" placeholder="valeurs séparées par des virgules"> }
                          @case ('text') { <input type="text" maxlength="150" [ngModel]="valeur(e)" (ngModelChange)="poser(e, $event)" [attr.aria-label]="e.libelle"> }
                          @default { <input type="number" [attr.min]="e.min" [attr.max]="e.max" [step]="e.type === 'int' ? 1 : 'any'"
                                       [ngModel]="valeur(e)" (ngModelChange)="poserNombre(e, $event)" [attr.aria-label]="e.libelle"
                                       [placeholder]="e.nullable ? 'automatique' : ''"> }
                        }
                      } @else { {{ affiche(e) }} }
                      @if (modifie(e)) { <small>réglage par défaut : {{ afficheDefaut(e) }}</small> }
                    </td>
                    @for (m of niveaux; track m) { <td class="centre">{{ e.modeles.includes(m) ? '●' : '' }}</td> }
                    <td>@if (!e.applique) { <span class="rf-badge m-infere" title="Montré pour information : le serveur ne s'en sert pas encore">pas encore pris en compte</span> }
                      @else if (modifie(e)) { <span class="rf-badge m-declare">modifié</span> }</td>
                  </tr>
                }
              </tbody>
            } @empty { <tbody><tr><td [attr.colspan]="3 + niveaux.length" class="rf-hint">Aucune donnée pour cette simulation.</td></tr></tbody> }
          </table></div>
          @if (r.peut_modifier) {
            <div class="rf-row actions">
              <button type="button" class="rf-btn primary" (click)="enregistrer()" [disabled]="!sale() || busy()">Enregistrer</button>
              <button type="button" class="rf-btn" (click)="annuler()" [disabled]="!sale() || busy()">Annuler les changements</button>
              <button type="button" class="rf-btn danger" (click)="defaut()" [disabled]="busy() || !nbSurcharges()">Revenir aux réglages par défaut</button>
            </div>
          } @else { <p class="rf-hint">Lecture seule : seul un gestionnaire du parc modifie ces réglages.</p> }
        } @else { <p class="rf-hint">Chargement…</p> }
      </section>

      <section class="rf-card" aria-labelledby="or-bat">
        <div class="rf-card-head"><h2 id="or-bat">Compte rendu pour un bâtiment</h2></div>
        @if (!ctx.batimentId()) {
          <p class="rf-hint">Choisissez un bâtiment dans le bandeau pour voir, étape par étape, d'où viennent ses données.</p>
        } @else if (bat(); as b) {
          <p>{{ b.name }}@if (b.pipeline?.date) { — dernière analyse le {{ b.pipeline?.date | date: 'dd/MM/yyyy à HH:mm' }} }</p>
          @if (etapes(b).length) {
            <ul class="steps">
              @for (s of etapes(b); track s.cle) {
                <li><span [class]="'rf-badge ' + (s.saute ? 'm-defaut' : s.ok ? 'm-mesure' : 'm-infere')">{{ s.saute ? 'sautée' : s.ok ? 'faite' : 'échec' }}</span>
                  <strong>{{ s.libelle }}</strong>@if (s.message) { <span class="rf-hint">— {{ s.message }}</span> }</li>
              }
            </ul>
          } @else { <p class="rf-hint">Ce bâtiment n'a pas encore été analysé automatiquement.</p> }
          @if (b.pipeline?.sources?.length) {
            <p class="rf-hint">Sources consultées : {{ b.pipeline?.sources?.join(' · ') }}</p>
          }
        } @else { <p class="rf-hint">Chargement…</p> }
      </section>
    }
  `,
})
export class ParcOrchestrateurComponent implements OnInit {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);

  niveaux: ('N0' | 'N1' | 'N2')[] = ['N0', 'N1', 'N2'];
  mots = MODELE_MOTS;
  filtres: [Filtre, string][] = [['', 'Toutes les simulations'], ['N0', 'N0 — Mensuel'], ['N1', 'N1 — Horaire simplifié'], ['N2', 'N2 — Détaillé par paroi']];
  filtre = signal<Filtre>('');
  reglages = signal<Reglages | null>(null);
  brouillon = signal<Record<string, unknown>>({});
  absent = signal(false);
  error = signal('');
  info = signal('');
  busy = signal(false);
  bat = signal<BatimentPipeline | null>(null);

  constructor() {
    effect(() => {
      const b = this.ctx.batimentId();
      untracked(() => this.chargerBatiment(b));
    });
  }

  ngOnInit(): void { this.charger(); }

  groupes = computed(() => {
    const r = this.reglages();
    if (!r) return [];
    const f = this.filtre();
    const out: { nom: string; entrees: ReglageEntree[] }[] = [];
    for (const e of r.schema) {
      if (f && !e.modeles.includes(f)) continue;
      let g = out.find(x => x.nom === e.groupe);
      if (!g) { g = { nom: e.groupe || 'Autres', entrees: [] }; out.push(g); }
      g.entrees.push(e);
    }
    return out;
  });

  sale = computed(() => JSON.stringify(this.brouillon()) !== JSON.stringify(this.reglages()?.surcharges ?? {}));
  nbSurcharges = computed(() => Object.keys(this.reglages()?.surcharges ?? {}).length + Object.keys(this.brouillon()).length);

  private url(): string | null {
    const id = this.ctx.parcId();
    return id && id > 0 ? `/parcs/${id}/reglages/orchestration/` : null;
  }

  charger(): void {
    const u = this.url();
    if (!u) return;
    this.rf.get<Reglages>(u).subscribe({
      next: r => { this.reglages.set(r); this.brouillon.set({ ...r.surcharges }); this.absent.set(false); },
      error: e => { if (e?.status === 404) this.absent.set(true); else this.error.set(e?.error?.detail ?? 'Réglages indisponibles.'); },
    });
  }

  private chargerBatiment(id: number | null): void {
    this.bat.set(null);
    if (!id) return;
    this.rf.get<BatimentPipeline>(`/batiments/${id}/`).subscribe({ next: b => { if (this.ctx.batimentId() === id) this.bat.set(b); } });
  }

  source(g: string): string { return SOURCE_DU_GROUPE[g] ?? g; }
  choix(e: ReglageEntree): [string, string][] { return Object.entries(e.choix ?? {}); }

  valeur(e: ReglageEntree): unknown {
    const b = this.brouillon();
    return e.cle in b ? b[e.cle] : this.reglages()?.defauts[e.cle];
  }
  modifie(e: ReglageEntree): boolean { return e.cle in this.brouillon(); }
  liste(e: ReglageEntree): string { const v = this.valeur(e); return Array.isArray(v) ? v.join(', ') : String(v ?? ''); }

  private montre(e: ReglageEntree, v: unknown): string {
    if (v === null || v === undefined || v === '') return e.nullable ? 'automatique' : '—';
    if (e.type === 'bool') return v ? 'activé' : 'désactivé';
    if (e.type === 'enum') return e.choix?.[String(v)] ?? String(v);
    if (Array.isArray(v)) return v.join(', ');
    return `${v}${e.unite ? ' ' + e.unite : ''}`;
  }
  affiche(e: ReglageEntree): string { return this.montre(e, this.valeur(e)); }
  afficheDefaut(e: ReglageEntree): string { return this.montre(e, this.reglages()?.defauts[e.cle]); }

  poser(e: ReglageEntree, v: unknown): void {
    this.info.set('');
    const d = this.reglages()?.defauts[e.cle];
    this.brouillon.update(b => {
      const n = { ...b };
      if (JSON.stringify(v) === JSON.stringify(d)) delete n[e.cle]; else n[e.cle] = v;
      return n;
    });
  }
  poserNombre(e: ReglageEntree, v: unknown): void {
    if (v === null || v === '' || v === undefined) { this.poser(e, e.nullable ? null : this.reglages()?.defauts[e.cle]); return; }
    const n = Number(v);
    if (Number.isFinite(n)) this.poser(e, e.type === 'int' ? Math.round(n) : n);
  }
  poserListe(e: ReglageEntree, v: string): void {
    this.poser(e, String(v).split(',').map(x => x.trim()).filter(Boolean));
  }

  annuler(): void { this.brouillon.set({ ...(this.reglages()?.surcharges ?? {}) }); this.info.set(''); }

  enregistrer(): void { this.envoyer(this.brouillon(), 'Réglages enregistrés. Ils s’appliquent à la prochaine analyse de chaque bâtiment.'); }
  defaut(): void { this.envoyer({}, 'Le parc suit de nouveau les réglages par défaut.'); }

  private envoyer(surcharges: Record<string, unknown>, ok: string): void {
    const u = this.url();
    if (!u) return;
    this.error.set(''); this.info.set(''); this.busy.set(true);
    this.rf.put<Reglages>(u, { surcharges }).subscribe({
      next: r => { this.busy.set(false); this.reglages.set(r); this.brouillon.set({ ...r.surcharges }); this.info.set(ok); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? (e?.status === 403 ? 'Seul un gestionnaire du parc modifie ces réglages.' : 'Réglages refusés.')); },
    });
  }

  etapes(b: BatimentPipeline): { cle: string; libelle: string; ok: boolean; saute: boolean; message: string }[] {
    const steps = b.pipeline?.steps ?? {};
    return Object.keys(steps).sort().map(k => ({
      cle: k, libelle: ETAPES[k] ?? k, ok: steps[k].ok, saute: !!steps[k].skipped,
      message: steps[k].error ?? (steps[k].skipped ? (steps[k].message ?? 'désactivée par les réglages du parc') : ''),
    }));
  }
}
