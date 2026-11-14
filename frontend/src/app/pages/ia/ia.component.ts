import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Provider { id: string; label: string; help: string; configured: boolean; default_model: string;
  models: { id: string; label: string }[]; }
interface Settings { providers: Provider[]; preferences: { provider: string; models: Record<string, string> }; vault: boolean; }

/** Intelligence artificielle (refonte, Lot 21) : vos clés, sur votre compte. Une clé n'est jamais renvoyée. */
@Component({
  selector: 'app-ia',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './ia.component.html',
})
export class IaComponent implements OnInit {
  private rf = inject(RefonteApiService);
  settings = signal<Settings | null>(null);
  keys: Record<string, string> = {};
  error = signal('');
  ok = signal('');

  ngOnInit(): void { this.load(); }

  load(): void { this.rf.get<Settings>('/ai-settings/').subscribe({ next: s => this.settings.set(s) }); }

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
