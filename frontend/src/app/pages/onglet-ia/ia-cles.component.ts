import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';
import { IaReglages, McpInfo, McpTok, Fournisseur } from './ia.types';

/** Onglet IA › clés et réglages (repris de l'ancienne page IA, Lot 21) : clés chiffrées sur le serveur, jamais renvoyées. */
@Component({
  selector: 'app-ia-cles',
  standalone: true,
  imports: [FormsModule, DatePipe],
  styles: [`
    .etat { font-size: var(--text-lg); margin: 0 0 var(--space-3); }
    .grow { flex: 1 1 18rem; }
    .fourn { border-top: 1px solid var(--border); padding-top: var(--space-3); margin-top: var(--space-3); }
    .fourn h3 { margin: 0 0 var(--space-2); font-size: var(--text-base); display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }
    pre, code.long { white-space: pre-wrap; word-break: break-all; }
    details { margin-top: var(--space-3); }
    summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-weight: 600; }
  `],
  template: `
    <section class="rf-card" aria-labelledby="ia-cles">
      <div class="rf-card-head"><h2 id="ia-cles">1. Vos clés et réglages</h2></div>
      @if (absent()) {
        <p class="rf-error" role="alert">Les réglages IA sont indisponibles sur ce serveur.</p>
      } @else if (reglages(); as s) {
        <p class="etat" role="status">{{ etat() }}</p>
        <p class="rf-hint">L'IA lit les documents d'un bâtiment (DPE, audit, factures) et <strong>propose</strong> des valeurs, que vous
          validez une à une. Elle ne calcule jamais la physique. Chaque clé est la vôtre : chiffrée sur le serveur, jamais renvoyée
          au navigateur, jamais utilisée pour quelqu'un d'autre.</p>
        @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
        @if (ok()) { <p class="rf-ok" role="status">{{ ok() }}</p> }
        @if (!s.vault) { <p class="rf-error">Le coffre n'est pas configuré sur ce serveur : aucune clé ne peut être enregistrée pour l'instant.</p> }

        @for (p of s.providers; track p.id) {
          <div class="fourn">
            <h3>{{ p.label }}
              <span [class]="'rf-badge ' + (p.configured ? 'm-mesure' : 'm-defaut')">{{ p.configured ? 'clé configurée' : 'aucune clé' }}</span>
              @if (s.preferences.provider === p.id) { <span class="rf-badge m-declare">utilisée par défaut</span> }</h3>
            <div class="rf-row">
              <label class="rf-field grow"><span>{{ p.configured ? 'Remplacer la clé' : 'Clé d’API' }}</span>
                <input type="password" autocomplete="off" [(ngModel)]="cles[p.id]" [attr.aria-label]="'Clé ' + p.label" [disabled]="!s.vault"></label>
              <button type="button" class="rf-btn primary" (click)="poserCle(p, cles[p.id] || '')" [disabled]="!cles[p.id] || !s.vault">Enregistrer</button>
              @if (p.configured) { <button type="button" class="rf-btn danger" (click)="poserCle(p, '')">Supprimer la clé</button> }
            </div>
            <div class="rf-row">
              <label class="rf-field grow"><span>Modèle</span>
                <select [ngModel]="s.preferences.models[p.id]" (ngModelChange)="preferer(p.id, $event)" [attr.aria-label]="'Modèle ' + p.label" [disabled]="!s.vault">
                  @for (m of p.models; track m.id) { <option [value]="m.id">{{ m.label }}</option> }</select></label>
              @if (s.preferences.provider !== p.id && p.configured) {
                <button type="button" class="rf-btn" (click)="preferer(p.id, s.preferences.models[p.id])">Utiliser par défaut</button>
              }
            </div>
            <details><summary>Où obtenir une clé</summary><p class="rf-hint">{{ p.help }}</p></details>
          </div>
        }

        <div class="fourn">
          <h3>Avec votre abonnement : Claude Code ou Mistral Vibe</h3>
          <p class="rf-hint">Reliez votre assistant à l'app avec un jeton personnel : il lit la fiche, les résultats et les documents,
            lance les calculs et <strong>propose</strong> des valeurs ; il ne les applique qu'avec un jeton « lire et proposer » et votre accord.</p>
          <div class="rf-row">
            <label class="rf-field"><span>Nom du jeton</span><input type="text" [(ngModel)]="tokNom" maxlength="80" aria-label="Nom du jeton"></label>
            <label class="rf-field"><span>Ce qu'il permet</span><select [(ngModel)]="tokPortee" aria-label="Portée du jeton">
              <option value="lire">lire seulement</option><option value="ecrire">lire, calculer, proposer et appliquer</option></select></label>
            <label class="rf-field" style="min-width: 6rem"><span>Durée (jours, 90 au plus)</span>
              <input type="number" min="1" max="90" [(ngModel)]="tokJours" aria-label="Durée du jeton en jours"></label>
            <button type="button" class="rf-btn primary" (click)="creerJeton()" [disabled]="!tokNom.trim()">Créer un jeton</button>
          </div>
          @if (nouveauJeton()) {
            <div class="rf-card" role="status">
              <p><strong>Copiez ce jeton maintenant : il ne sera plus jamais affiché.</strong></p>
              <code class="rf-code long">{{ nouveauJeton() }}</code>
            </div>
          }
          @if (jetons().length) {
            <div class="rf-table-wrap"><table class="rf-table">
              <thead><tr><th>Jeton</th><th>Permet</th><th>Expire</th><th>Dernier usage</th><th></th></tr></thead>
              <tbody>
                @for (t of jetons(); track t.id) {
                  <tr><td>{{ t.name }} <code>{{ t.prefix }}…</code></td><td>{{ t.scope === 'ecrire' ? 'lire et proposer' : 'lire' }}</td>
                    <td>{{ t.expires_at | date:'dd/MM/yyyy' }}</td><td>{{ t.last_used_at ? (t.last_used_at | date:'dd/MM/yyyy HH:mm') : 'jamais' }}</td>
                    <td>@if (t.revoked) { <span class="rf-badge m-defaut">révoqué</span> }
                      @else { <button type="button" class="rf-btn danger" (click)="revoquer(t)" [attr.aria-label]="'Révoquer ' + t.name">Révoquer</button> }</td></tr>
                }
              </tbody></table></div>
          }
          @if (info(); as i) {
            <details><summary>Détails techniques : commandes de connexion</summary>
              <p class="rf-hint">Un jeton d'abonnement Claude (<code>sk-ant-oat…</code>) n'est pas accepté comme clé : les conditions
                d'Anthropic le réservent à Claude Code. Passez par le serveur MCP de l'app ci-dessous.</p>
              <p class="rf-hint">Adresse du serveur : <code class="long">{{ i.url }}</code></p>
              <p class="rf-hint">Claude Code :</p><pre class="rf-code">{{ avecJeton(i.claude) }}</pre>
              <p class="rf-hint">Mistral Vibe :</p><pre class="rf-code">{{ avecJeton(i.vibe) }}</pre>
            </details>
          }
        </div>
      } @else { <p class="rf-hint">Chargement…</p> }
    </section>
  `,
})
export class IaClesComponent implements OnInit {
  private rf = inject(RefonteApiService);
  reglages = signal<IaReglages | null>(null);
  absent = signal(false);
  cles: Record<string, string> = {};
  error = signal('');
  ok = signal('');
  jetons = signal<McpTok[]>([]);
  info = signal<McpInfo | null>(null);
  nouveauJeton = signal('');
  tokNom = 'Claude Code';
  tokPortee = 'lire';
  tokJours = 30;

  etat = computed(() => {
    const s = this.reglages();
    if (!s) return '';
    const ok = s.providers.filter(p => p.configured).map(p => p.label);
    if (!ok.length) return 'Aucune clé configurée : l\'IA ne peut pas encore lire vos documents (les XML de DPE se lisent sans IA).';
    const def = s.providers.find(p => p.id === s.preferences.provider);
    return `Clé configurée : ${ok.join(', ')}` + (def ? ` — utilisée par défaut : ${def.label}.` : '.');
  });

  ngOnInit(): void { this.charger(); this.chargerJetons(); }

  charger(): void {
    this.rf.get<IaReglages>('/ai-settings/').subscribe({ next: s => this.reglages.set(s), error: () => this.absent.set(true) });
  }

  chargerJetons(): void {
    this.rf.get<McpTok[]>('/mcp-tokens/').subscribe({ next: t => this.jetons.set(t), error: () => this.jetons.set([]) });
    this.rf.get<McpInfo>('/mcp/info/').subscribe({ next: i => this.info.set(i), error: () => this.info.set(null) });
  }

  creerJeton(): void {
    this.error.set(''); this.nouveauJeton.set('');
    this.rf.post<McpTok & { token: string }>('/mcp-tokens/', { name: this.tokNom, scope: this.tokPortee, days: Number(this.tokJours) })
      .subscribe({ next: t => { this.nouveauJeton.set(t.token); this.chargerJetons(); },
                   error: e => this.error.set(e?.error?.error ?? 'Jeton refusé.') });
  }

  revoquer(t: McpTok): void {
    this.rf.del(`/mcp-tokens/${t.id}/`).subscribe({ next: () => { this.nouveauJeton.set(''); this.chargerJetons(); } });
  }

  avecJeton(cmd: string): string { return this.nouveauJeton() ? cmd.replace('<JETON>', this.nouveauJeton()) : cmd; }

  poserCle(p: Fournisseur, valeur: string): void {
    this.error.set(''); this.ok.set('');
    this.rf.put(`/ai-keys/`, { [`${p.id}_key`]: valeur }).subscribe({
      next: () => { this.cles[p.id] = ''; this.ok.set(valeur ? `Clé ${p.label} enregistrée (chiffrée sur le serveur).` : `Clé ${p.label} supprimée.`); this.charger(); },
      error: e => this.error.set(e?.error?.error ?? 'Clé refusée.'),
    });
  }

  preferer(fournisseur: string, modele: string): void {
    this.rf.put<IaReglages>('/ai-settings/', { provider: fournisseur, models: { [fournisseur]: modele } }).subscribe({
      next: s => { this.reglages.set(s); this.ok.set('Préférences enregistrées.'); },
      error: e => this.error.set(e?.error?.error ?? 'Refusé.'),
    });
  }
}
