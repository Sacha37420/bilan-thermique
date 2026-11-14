import { Component, Input, OnChanges, OnDestroy, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Doc { id: number; kind: string; filename: string; size_bytes: number; n_pages: number; pages_with_text: number[];
  stored: boolean; note?: string | null; }
interface Prop { id: number; key: string; label: string; value: unknown; unit: string; page: number | null; extract: string;
  check: string; check_message: string; decision: string; document: string; provider: string; }
interface Estimate { provider: string; provider_label: string; model: string; pages: number[]; tokens_estimated: number;
  fits: boolean; key_configured: boolean; warning: string; }
interface Prov { id: string; label: string; configured: boolean; }

const CHECK_LABEL: Record<string, string> = { ok: 'contrôle passé', hors_plage: 'hors plage plausible',
  incoherent: 'incohérent avec la fiche', extrait_introuvable: 'extrait introuvable dans le document', cle_inconnue: 'donnée inconnue' };

/** Documents d'un bâtiment (refonte, Lot 21) : dépôt, lecture, extraction par l'IA de l'auteur, propositions à valider. */
@Component({
  selector: 'app-documents',
  standalone: true,
  imports: [FormsModule, DecimalPipe, RouterLink],
  templateUrl: './documents.component.html',
})
export class DocumentsComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) buildingId!: number;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  private api = inject(ApiService);
  checkLabel = CHECK_LABEL;
  docs = signal<Doc[]>([]);
  props = signal<Prop[]>([]);
  providers = signal<Prov[]>([]);
  storage = signal(false);
  estimate = signal<Estimate | null>(null);
  estimateDoc = signal<number | null>(null);
  provider = 'claude';
  jobMsg = signal('');
  busy = signal(false);
  error = signal('');
  ok = signal('');
  private timer?: ReturnType<typeof setInterval>;

  ngOnChanges(): void { this.load(); }
  ngOnDestroy(): void { clearInterval(this.timer); }

  load(): void {
    this.rf.get<{ results: Doc[]; storage: boolean }>(`/batiments/${this.buildingId}/documents/`).subscribe({ next: r => {
      this.docs.set(r.results); this.storage.set(r.storage); } });
    this.rf.get<{ results: Prop[] }>(`/batiments/${this.buildingId}/propositions/`).subscribe({ next: r => this.props.set(r.results) });
    this.rf.get<{ providers: Prov[]; preferences: { provider: string } }>('/ai-settings/').subscribe({ next: s => {
      this.providers.set(s.providers); this.provider = s.preferences.provider; } });
  }

  pending(): Prop[] { return this.props().filter(p => p.decision === 'en_attente'); }
  decided(): Prop[] { return this.props().filter(p => p.decision !== 'en_attente'); }
  acceptable(p: Prop): boolean { return p.check === 'ok' || p.check === 'incoherent'; }
  show(v: unknown): string { return typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : String(v); }

  upload(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const f = input.files?.[0];
    if (!f) return;
    this.error.set(''); this.ok.set(''); this.busy.set(true);
    const fd = new FormData(); fd.append('file', f);
    this.rf.post<Doc & { proposals: Prop[] }>(`/batiments/${this.buildingId}/documents/`, fd).subscribe({
      next: d => { this.busy.set(false); input.value = '';
        this.ok.set(`« ${d.filename} » lu : ${d.n_pages} page(s)` + (d.proposals.length ? `, ${d.proposals.length} valeur(s) proposée(s) sans IA.` : '.')
          + (d.note ? ' ' + d.note : '')); this.load(); },
      error: e => { this.busy.set(false); this.error.set(e?.error?.detail ?? 'Document refusé.'); },
    });
  }

  remove(d: Doc): void {
    this.rf.del(`/documents/${d.id}/`).subscribe({ next: () => { this.estimate.set(null); this.load(); } });
  }

  askEstimate(d: Doc): void {
    this.error.set(''); this.estimate.set(null); this.estimateDoc.set(d.id);
    this.rf.post<Estimate>(`/documents/${d.id}/estimation/`, { provider: this.provider }).subscribe({
      next: e => this.estimate.set(e), error: e => this.error.set(e?.error?.detail ?? 'Estimation impossible.') });
  }

  extract(): void {
    const e = this.estimate(); const id = this.estimateDoc();
    if (!e || !id) return;
    this.error.set(''); this.busy.set(true);
    this.rf.post<{ id: number }>(`/documents/${id}/extraire/`, { provider: e.provider, model: e.model, pages: e.pages, confirm: true }).subscribe({
      next: j => { this.estimate.set(null); this.jobMsg.set('Lecture par l’IA en cours…'); clearInterval(this.timer);
        this.timer = setInterval(() => this.api.getJob(j.id).subscribe({ next: x => {
          const job = x as { status: string; message: string };
          this.jobMsg.set(job.message || 'Lecture en cours…');
          if (job.status === 'DONE' || job.status === 'ERROR') { clearInterval(this.timer); this.busy.set(false); this.load(); }
        } }), 1500); },
      error: er => { this.busy.set(false); this.error.set(er?.error?.detail ?? 'Envoi refusé.'); },
    });
  }

  decide(p: Prop, decision: 'acceptee' | 'rejetee'): void {
    this.error.set('');
    this.rf.post<Prop>(`/propositions/${p.id}/decision/`, { decision }).subscribe({
      next: () => { this.load(); if (decision === 'acceptee') this.changed.emit(); },
      error: e => this.error.set(e?.error?.detail ?? 'Décision refusée.') });
  }
}
