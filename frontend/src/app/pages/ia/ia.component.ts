import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Provider { id: string; label: string; help: string; configured: boolean; default_model: string;
  models: { id: string; label: string }[]; }
interface McpTok { id: number; name: string; prefix: string; scope: string; building_id: number | null;
  portfolio_id: number | null; expires_at: string; last_used_at: string | null; revoked: boolean; }
interface McpInfo { url: string; claude: string; vibe: string; }
interface Settings { providers: Provider[]; preferences: { provider: string; models: Record<string, string> }; vault: boolean; }

/** Intelligence artificielle (refonte, Lot 21) : vos clés, sur votre compte. Une clé n'est jamais renvoyée. */
@Component({
  selector: 'app-ia',
  standalone: true,
  imports: [FormsModule, DatePipe],
  templateUrl: './ia.component.html',
})
export class IaComponent implements OnInit {
  private rf = inject(RefonteApiService);
  settings = signal<Settings | null>(null);
  keys: Record<string, string> = {};
  error = signal('');
  ok = signal('');

  tokens = signal<McpTok[]>([]);
  info = signal<McpInfo | null>(null);
  newToken = signal('');
  tokName = 'Claude Code';
  tokScope = 'lire';
  tokDays = 30;

  ngOnInit(): void { this.load(); this.loadTokens(); }

  load(): void { this.rf.get<Settings>('/ai-settings/').subscribe({ next: s => this.settings.set(s) }); }

  loadTokens(): void {
    this.rf.get<McpTok[]>('/mcp-tokens/').subscribe({ next: t => this.tokens.set(t) });
    this.rf.get<McpInfo>('/mcp/info/').subscribe({ next: i => this.info.set(i) });
  }

  createToken(): void {
    this.error.set(''); this.newToken.set('');
    this.rf.post<McpTok & { token: string }>('/mcp-tokens/', { name: this.tokName, scope: this.tokScope, days: Number(this.tokDays) })
      .subscribe({ next: t => { this.newToken.set(t.token); this.loadTokens(); },
                   error: e => this.error.set(e?.error?.error ?? 'Jeton refusé.') });
  }

  revoke(t: McpTok): void {
    this.rf.del(`/mcp-tokens/${t.id}/`).subscribe({ next: () => { this.newToken.set(''); this.loadTokens(); } });
  }

  withToken(cmd: string): string { return this.newToken() ? cmd.replace('<JETON>', this.newToken()) : cmd; }

  saveKey(p: Provider, value: string): void {
    this.error.set(''); this.ok.set('');
    this.rf.put(`/ai-keys/`, { [`${p.id}_key`]: value }).subscribe({
      next: () => { this.keys[p.id] = ''; this.ok.set(value ? `Clé ${p.label} enregistrée (chiffrée sur le serveur).` : `Clé ${p.label} supprimée.`); this.load(); },
      error: e => this.error.set(e?.error?.error ?? 'Clé refusée.'),
    });
  }

  savePrefs(provider: string, model: string): void {
    this.rf.put<Settings>('/ai-settings/', { provider, models: { [provider]: model } }).subscribe({
      next: s => { this.settings.set(s); this.ok.set('Préférences enregistrées.'); },
      error: e => this.error.set(e?.error?.error ?? 'Refusé.'),
    });
  }
}
