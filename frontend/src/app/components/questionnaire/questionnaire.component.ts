import { Component, Input, OnChanges, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe, KeyValuePipe, NgTemplateOutlet } from '@angular/common';
import { RefonteApiService } from '../../core/refonte-api.service';

interface Question {
  id: string; label: string; block: string; kind: 'enum' | 'number' | 'int';
  choices: Record<string, string> | null; min: number | null; max: number | null;
  influence_kwh: number | null; answered: boolean; priority: boolean;
  current: Record<string, unknown>; subquestions: (Question & { depends: Record<string, string[]> })[];
}
interface Ranked { questions: Question[]; nominal_kwh: number | null; inconsistencies: { message: string; keys: string[] }[]; }
interface Saved {
  confidence_before: number; confidence_after: number; calc_error: string | null;
  before: { indicators: Record<string, number> } | null; after: { indicators: Record<string, number> } | null;
  inconsistencies: { message: string }[];
}

/** « Ce qui rendrait le résultat plus sûr » — questionnaire guidé (refonte, Lot 12) : d'abord les questions qui réduisent le plus la fourchette. */
@Component({
  selector: 'app-questionnaire',
  standalone: true,
  imports: [FormsModule, DecimalPipe, KeyValuePipe, NgTemplateOutlet],
  templateUrl: './questionnaire.component.html',
  styleUrl: './questionnaire.component.scss',
})
export class QuestionnaireComponent implements OnChanges {
  @Input({ required: true }) buildingId!: number;
  @Input() refresh = 0;
  changed = output<void>();
  private rf = inject(RefonteApiService);
  ranked = signal<Ranked | null>(null);
  answers: Record<string, unknown> = {};
  saving = signal(false);
  error = signal('');
  saved = signal<Saved | null>(null);

  ngOnChanges(): void { this.load(); }

  load(): void {
    this.rf.get<Ranked>(`/batiments/${this.buildingId}/questions/`).subscribe({ next: r => this.ranked.set(r) });
  }

  first(): Question[] { return (this.ranked()?.questions ?? []).filter(q => q.priority); }
  others(): Question[] { return (this.ranked()?.questions ?? []).filter(q => !q.priority); }

  showSub(s: Question & { depends: Record<string, string[]> }): boolean {
    return Object.entries(s.depends).every(([qid, vals]) => vals.includes(String(this.answers[qid] ?? '')));
  }

  filled(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(this.answers)) if (v !== null && v !== undefined && v !== '') out[k] = v;
    return out;
  }

  save(): void {
    const answers = this.filled();
    if (!Object.keys(answers).length) return;
    this.saving.set(true);
    this.error.set('');
    this.rf.post<Saved>(`/batiments/${this.buildingId}/questionnaire/`, { answers }).subscribe({
      next: r => { this.saved.set(r); this.saving.set(false); this.answers = {}; this.load(); this.changed.emit(); },
      error: e => { this.saving.set(false); this.error.set(e?.error?.detail ?? 'Réponses refusées.'); },
    });
  }

  hint(q: Question): string {
    const v = q.current[Object.keys(q.current)[0]];
    if (v === null || v === undefined) return '';
    if (q.kind === 'enum') return `actuel : ${q.choices?.[String(v)] ?? v}`;
    return `actuel : ${typeof v === 'number' ? Math.round(v * 100) / 100 : v}`;
  }

  kwhM2(x: { indicators: Record<string, number> } | null): number | null { return x?.indicators?.['heating_kwh_m2'] ?? null; }
}
