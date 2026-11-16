import { Component, OnInit, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { firstValueFrom } from 'rxjs';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { DocsParcLigne, EnvoiFichier } from './ia.types';

const MAX_OCTETS = 15 * 1024 * 1024;

/** Onglet IA › import pour tout le parc : plusieurs fichiers, chacun vers son bâtiment ; état des documents du parc. */
@Component({
  selector: 'app-ia-import-parc',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  styles: [`
    .file { display: inline-flex; align-items: center; position: relative; overflow: hidden; cursor: pointer; }
    .file input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
    .depot { border: 2px dashed var(--border); border-radius: var(--radius-md); padding: var(--space-5); text-align: center; background: var(--bg); }
    .depot.survol { border-color: var(--accent); background: var(--accent-tint); }
    select { min-height: 44px; max-width: 100%; }
    .ko { color: var(--danger); }
    td small { display: block; color: var(--text-mute); font-size: var(--text-xs); }
  `],
  template: `
    <section class="rf-card" aria-labelledby="ia-parc">
      <div class="rf-card-head"><h2 id="ia-parc">2. Déposer des documents pour tout le parc</h2></div>
      @if (!ctx.parcId()) {
        <p class="rf-hint">Choisissez d'abord un parc dans le bandeau.</p>
      } @else {
        <p class="rf-hint">Déposez d'un coup les DPE (PDF ou XML de l'ADEME), audits et factures de plusieurs bâtiments, puis indiquez
          pour chacun le bâtiment concerné. Rien n'entre dans une fiche sans votre validation : les valeurs lues deviennent des
          propositions, à accepter une à une dans la section 3.</p>
        <div class="depot" [class.survol]="survol()" (dragover)="$event.preventDefault(); survol.set(true)" (dragleave)="survol.set(false)"
             (drop)="deposer($event)">
          <label class="rf-btn primary file">Choisir des fichiers
            <input type="file" multiple accept=".pdf,.xml,.txt" (change)="choisir($event)" aria-label="Choisir des documents"></label>
          <p class="rf-hint">ou glissez-les ici — PDF, XML ou texte, 15 Mo au plus chacun.</p>
        </div>

        @if (fichiers().length) {
          <div class="rf-table-wrap"><table class="rf-table">
            <thead><tr><th>Fichier</th><th>Bâtiment</th><th>État</th><th></th></tr></thead>
            <tbody>
              @for (f of fichiers(); track f.cle) {
                <tr><td>{{ f.fichier.name }}<small>{{ f.fichier.size / 1024 | number: '1.0-0' }} ko</small></td>
                  <td><select [ngModel]="f.batiment" (ngModelChange)="viser(f, $event)" [disabled]="f.etat === 'envoi' || f.etat === 'ok'"
                              [attr.aria-label]="'Bâtiment pour ' + f.fichier.name">
                    <option [ngValue]="null">— choisir —</option>
                    @for (b of ctx.batiments(); track b.id) { <option [ngValue]="b.id">{{ b.name || b.address || ('Bâtiment ' + b.id) }}</option> }
                  </select></td>
                  <td>@switch (f.etat) {
                    @case ('ok') { <span class="rf-badge m-mesure">envoyé</span> <small>{{ f.message }}</small> }
                    @case ('erreur') { <span class="ko">{{ f.message }}</span> }
                    @case ('envoi') { envoi… }
                    @default { à envoyer }
                  }</td>
                  <td>@if (f.etat !== 'envoi' && f.etat !== 'ok') { <button type="button" class="rf-btn small" (click)="retirer(f)" [attr.aria-label]="'Retirer ' + f.fichier.name">Retirer</button> }</td></tr>
              }
            </tbody></table></div>
          <div class="rf-row">
            <button type="button" class="rf-btn primary" (click)="envoyer()" [disabled]="busy() || !aEnvoyer()">Envoyer {{ aEnvoyer() }} fichier(s)</button>
            @if (envoyes()) { <button type="button" class="rf-btn" (click)="nettoyer()">Effacer la liste des envoyés</button> }
          </div>
        }

        <h3>Documents déjà déposés dans le parc</h3>
        @if (ctx.horsParc()) {
          <p class="rf-hint">Le récapitulatif n'existe que pour un parc ; ouvrez chaque bâtiment dans la section 3.</p>
        } @else if (absent()) {
          <p class="rf-hint" role="status">Le récapitulatif des documents du parc n'est pas encore disponible sur le serveur.</p>
        } @else if (lignes(); as ls) {
          <div class="rf-table-wrap"><table class="rf-table">
            <thead><tr><th>Bâtiment</th><th>Documents</th><th class="num">Valeurs à valider</th><th></th></tr></thead>
            <tbody>
              @for (l of ls; track l.building_id) {
                <tr><td>{{ l.building_name }}</td>
                  <td>@if (l.documents.length) { {{ l.documents.length }}<small>{{ noms(l) }}</small> } @else { <span class="rf-hint">aucun</span> }</td>
                  <td class="num">@if (l.propositions_en_attente) { <span class="rf-badge m-infere">{{ l.propositions_en_attente }}</span> } @else { — }</td>
                  <td><button type="button" class="rf-btn small" (click)="voir.emit(l.building_id)">{{ l.propositions_en_attente ? 'Valider' : 'Ouvrir' }}</button></td></tr>
              } @empty { <tr><td colspan="4" class="rf-hint">Aucun bâtiment visible dans ce parc.</td></tr> }
            </tbody></table></div>
        } @else { <p class="rf-hint">Chargement…</p> }
      }
    </section>
  `,
})
export class IaImportParcComponent implements OnInit {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  /** Ouvrir la section 3 sur ce bâtiment. */
  voir = output<number>();
  /** Un document a été déposé (la section 3 se recharge). */
  depose = output<number>();

  fichiers = signal<EnvoiFichier[]>([]);
  lignes = signal<DocsParcLigne[] | null>(null);
  absent = signal(false);
  busy = signal(false);
  survol = signal(false);
  private n = 0;

  ngOnInit(): void { this.charger(); }

  charger(): void {
    const id = this.ctx.parcId();
    if (!id || id < 0) return;
    this.rf.get<{ results: DocsParcLigne[] }>(`/parcs/${id}/documents/`).subscribe({
      next: r => { this.lignes.set(r.results); this.absent.set(false); },
      error: e => { if (e?.status === 404 || e?.status === 405) this.absent.set(true); else this.lignes.set([]); },
    });
  }

  envoyes(): boolean { return this.fichiers().some(x => x.etat === 'ok'); }
  aEnvoyer(): number { return this.fichiers().filter(f => f.etat === 'attente' || f.etat === 'erreur').length; }

  noms(l: DocsParcLigne): string { return l.documents.slice(0, 3).map(d => d.filename).join(', ') + (l.documents.length > 3 ? '…' : ''); }

  choisir(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.ajouter(Array.from(input.files ?? []));
    input.value = '';
  }

  deposer(ev: DragEvent): void {
    ev.preventDefault();
    this.survol.set(false);
    this.ajouter(Array.from(ev.dataTransfer?.files ?? []));
  }

  private ajouter(fs: File[]): void {
    const defaut = this.ctx.batimentId();
    const bats = this.ctx.batiments();
    const neufs = fs.map(f => {
      // Bâtiment dont le nom figure dans le nom du fichier, sinon le bâtiment courant.
      const nomF = f.name.toLowerCase();
      const devine = bats.find(b => b.name && b.name.length > 2 && nomF.includes(b.name.toLowerCase()))?.id;
      const trop = f.size > MAX_OCTETS;
      return { cle: ++this.n, fichier: f, batiment: devine ?? defaut, etat: trop ? 'erreur' : 'attente',
               message: trop ? 'Fichier trop volumineux (15 Mo au plus).' : '' } as EnvoiFichier;
    });
    this.fichiers.update(l => [...l, ...neufs]);
  }

  viser(f: EnvoiFichier, b: number | null): void {
    this.fichiers.update(l => l.map(x => x.cle === f.cle ? { ...x, batiment: b, etat: x.etat === 'erreur' && x.fichier.size <= MAX_OCTETS ? 'attente' : x.etat } : x));
  }
  retirer(f: EnvoiFichier): void { this.fichiers.update(l => l.filter(x => x.cle !== f.cle)); }
  nettoyer(): void { this.fichiers.update(l => l.filter(x => x.etat !== 'ok')); }

  private maj(cle: number, p: Partial<EnvoiFichier>): void { this.fichiers.update(l => l.map(x => x.cle === cle ? { ...x, ...p } : x)); }

  /** Un fichier après l'autre (le serveur lit chaque document à la réception). */
  async envoyer(): Promise<void> {
    this.busy.set(true);
    for (const f of this.fichiers().filter(x => x.etat === 'attente' || x.etat === 'erreur')) {
      if (f.fichier.size > MAX_OCTETS) continue;
      if (f.batiment === null) { this.maj(f.cle, { etat: 'erreur', message: 'Choisissez le bâtiment.' }); continue; }
      this.maj(f.cle, { etat: 'envoi', message: '' });
      const fd = new FormData(); fd.append('file', f.fichier);
      try {
        const d = await firstValueFrom(this.rf.post<{ n_pages: number; proposals: unknown[]; note?: string | null }>(`/batiments/${f.batiment}/documents/`, fd));
        this.maj(f.cle, { etat: 'ok', message: `${d.n_pages} page(s) lue(s)` + (d.proposals?.length ? `, ${d.proposals.length} valeur(s) proposée(s)` : '') });
        this.depose.emit(f.batiment);
      } catch (e: unknown) {
        const err = e as { status?: number; error?: { detail?: string } };
        this.maj(f.cle, { etat: 'erreur', message: err?.error?.detail ?? (err?.status === 403 ? 'Lecture seule sur ce bâtiment.' : 'Document refusé.') });
      }
    }
    this.busy.set(false);
    this.charger();
  }
}
