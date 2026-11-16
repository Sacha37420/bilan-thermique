import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { labEnv } from '../../core/env';
import { ParcImportCsvComponent } from './parc-import-csv.component';
import { Members, ROLES, ROLE_PHRASE } from './parc.types';

interface Bat { id: number; name: string; address: string; portfolio?: number | null; }

/** Onglet Parc › Import et partage : CSV, rattacher un bâtiment hors parc ; membres, rôles expliqués, invitations. */
@Component({
  selector: 'app-parc-partage',
  standalone: true,
  imports: [FormsModule, ParcImportCsvComponent],
  styleUrl: './parc-commun.scss',
  template: `
    @if (!ctx.estGestionnaire() || ctx.horsParc()) {
      <p class="rf-hint" role="status">{{ ctx.horsParc() ? 'Les bâtiments hors parc ne se partagent pas : rangez-les d’abord dans un parc.'
        : 'Seul un gestionnaire du parc importe des bâtiments et partage le parc.' }}</p>
    } @else {
      @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
      @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }

      <section class="rf-card" aria-labelledby="ip-imp">
        <div class="rf-card-head"><h2 id="ip-imp">Importer des bâtiments</h2></div>
        <h3>Depuis un fichier CSV</h3>
        <app-parc-import-csv />
        <h3>Ranger un bâtiment hors parc dans ce parc</h3>
        <p class="rf-hint">Les bâtiments créés avant les parcs sont visibles de tous. Les ranger ici les réserve aux membres de ce parc.</p>
        @if (horsParc().length) {
          <div class="rf-row">
            <label class="rf-field grow"><span>Bâtiment hors parc</span>
              <select [(ngModel)]="aRattacher" aria-label="Bâtiment hors parc à rattacher"><option [ngValue]="null">— choisir —</option>
                @for (b of horsParc(); track b.id) { <option [ngValue]="b.id">{{ b.name || b.address || ('Bâtiment ' + b.id) }}</option> }</select></label>
            <button type="button" class="rf-btn primary" (click)="rattacher()" [disabled]="aRattacher === null">Ranger dans ce parc</button>
          </div>
        } @else { <p class="rf-hint">{{ horsParcCharge() ? 'Aucun bâtiment hors parc.' : 'Chargement…' }}</p> }
      </section>

      <section class="rf-card" aria-labelledby="ip-part">
        <div class="rf-card-head"><h2 id="ip-part">Partager le parc</h2></div>
        <p class="rf-hint">Il n'y a pas d'annuaire : on invite par un lien à usage unique, valable 7 jours, que la personne ouvre avec
          son compte du lab. Chaque rôle permet :</p>
        <ul class="roles">
          @for (r of roles; track r.id) { <li><strong>{{ r.nom }}</strong>{{ r.phrase }}</li> }
        </ul>
        <div class="rf-row">
          <label class="rf-field"><span>Rôle</span>
            <select [ngModel]="role()" (ngModelChange)="role.set($event)" aria-label="Rôle de la personne invitée">
              @for (r of roles; track r.id) { <option [value]="r.id">{{ r.nom }}</option> }</select></label>
          @if (surBatiment()) {
            <label class="rf-field grow"><span>Bâtiment</span>
              <select [(ngModel)]="batiment" aria-label="Bâtiment confié"><option [ngValue]="null">— choisir —</option>
                @for (b of ctx.batiments(); track b.id) { <option [ngValue]="b.id">{{ b.name || b.address }}</option> }</select></label>
          }
          <button type="button" class="rf-btn primary" (click)="inviter()" [disabled]="surBatiment() && batiment === null">Créer le lien d'invitation</button>
        </div>
        @if (code()) {
          <p class="rf-hint">Lien à transmettre (affiché une seule fois) :</p>
          <input type="text" class="rf-input code" readonly [value]="lien()" aria-label="Lien d'invitation" (focus)="$any($event.target).select()">
        }

        @if (membres(); as m) {
          <h3>Qui a accès</h3>
          <ul class="mlist">
            @for (x of m.members; track x.id) {
              <li><span>{{ x.name }}{{ x.me ? ' (vous)' : '' }} — <span class="aide" [title]="phrase(x.role)">{{ nom(x.role) }}</span></span>
                @if (!x.me) { <button type="button" class="rf-btn small danger" (click)="retirer(x.id, 'membre')">Retirer</button> }</li>
            }
            @for (x of m.accesses; track x.id) {
              <li><span>{{ x.name }} — <span class="aide" [title]="phrase(x.role)">{{ nom(x.role) }}</span> « {{ x.building }} »</span>
                <button type="button" class="rf-btn small danger" (click)="retirer(x.id, 'acces')">Retirer</button></li>
            }
          </ul>
          @if (m.invitations.length) {
            <h3>Liens d'invitation</h3>
            <ul class="mlist">
              @for (i of m.invitations; track i.id) {
                <li><code>{{ i.prefix }}…</code> — {{ nom(i.role) }} · utilisé {{ i.uses }}/{{ i.max_uses }}
                  @if (i.active) { <button type="button" class="rf-btn small danger" (click)="retirer(i.id, 'invitation')">Révoquer</button> } @else { <span class="rf-hint">(inactif)</span> }</li>
              }
            </ul>
          }
        } @else { <p class="rf-hint">Chargement des membres…</p> }
      </section>
    }
  `,
})
export class ParcPartageComponent implements OnInit {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);

  roles = ROLES;
  role = signal('lecteur');
  batiment: number | null = null;
  aRattacher: number | null = null;
  error = signal('');
  info = signal('');
  code = signal('');
  membres = signal<Members | null>(null);
  horsParc = signal<Bat[]>([]);
  horsParcCharge = signal(false);
  surBatiment = computed(() => ROLES.find(r => r.id === this.role())?.surBatiment ?? false);

  ngOnInit(): void { this.charger(); }

  /** Rechargé à chaque changement de parc par le parent (`@if` sur parcId). */
  charger(): void {
    const id = this.ctx.parcId();
    if (!id || id < 0 || !this.ctx.estGestionnaire()) return;
    this.rf.get<Members>(`/parcs/${id}/membres/`).subscribe({
      next: m => this.membres.set(m), error: () => this.error.set('Membres indisponibles.') });
    void this.chargerHorsParc();
  }

  private async chargerHorsParc(): Promise<void> {
    try {
      const all = await firstValueFrom(this.rf.get<Bat[]>('/batiments/'));
      // Contrat v1.1 : `portfolio` présent ; sans lui on ne peut rien affirmer, on n'affiche rien.
      this.horsParc.set(all.filter(b => 'portfolio' in b && (b.portfolio === null)));
    } catch { this.horsParc.set([]); }
    this.horsParcCharge.set(true);
  }

  nom(r: string): string { return ROLE_PHRASE[r]?.nom ?? r; }
  phrase(r: string): string { return ROLE_PHRASE[r]?.phrase ?? ''; }

  rattacher(): void {
    const id = this.ctx.parcId();
    if (!id || id < 0 || this.aRattacher === null) return;
    this.error.set(''); this.info.set('');
    this.rf.post(`/parcs/${id}/rattacher/`, { building_id: this.aRattacher }).subscribe({
      next: () => { this.info.set('Bâtiment rangé dans ce parc.'); this.aRattacher = null;
        void this.chargerHorsParc(); void this.ctx.rafraichirBatiments(); void this.ctx.rechargerParcs(); },
      error: e => this.error.set(e?.error?.detail ?? 'Rattachement refusé.'),
    });
  }

  inviter(): void {
    const id = this.ctx.parcId();
    if (!id || id < 0) return;
    this.error.set(''); this.code.set('');
    this.rf.post<{ code: string }>(`/parcs/${id}/invitations/`, { role: this.role(), building_id: this.surBatiment() ? this.batiment : null }).subscribe({
      next: r => { this.code.set(r.code); this.charger(); },
      error: e => this.error.set(e?.error?.non_field_errors?.[0] ?? e?.error?.detail ?? 'Invitation refusée.'),
    });
  }

  lien(): string {
    const base = labEnv().appUrl ?? (window.location.origin + '/');
    return `${base.replace(/\/?$/, '/')}parc/rejoindre?code=${encodeURIComponent(this.code())}`;
  }

  retirer(mid: number, type: 'membre' | 'acces' | 'invitation'): void {
    const id = this.ctx.parcId();
    if (!id || id < 0) return;
    this.rf.del(`/parcs/${id}/membres/${mid}/?type=${type}`).subscribe({
      next: () => this.charger(), error: e => this.error.set(e?.error?.detail ?? 'Refusé.'),
    });
  }
}
