import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DatePipe, DecimalPipe, KeyValuePipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { DocumentsComponent } from '../../components/documents/documents.component';
import { ScenariosComponent } from '../../components/scenarios/scenarios.component';
import { MesuresComponent } from '../../components/mesures/mesures.component';
import { SystemesComponent } from '../../components/systemes/systemes.component';
import { GeometrieComponent } from '../../components/geometrie/geometrie.component';
import { ProtectionsComponent } from '../../components/protections/protections.component';
import { MeteoCarteComponent } from '../../components/meteo-carte/meteo-carte.component';
import { QuestionnaireComponent } from '../../components/questionnaire/questionnaire.component';
import { ResultatCarteComponent } from '../../components/resultat-carte/resultat-carte.component';
import { CalculsComponent } from '../../components/calculs/calculs.component';
import { SimulationComponent } from '../../components/simulation/simulation.component';
import { Catalogue, CatalogueKey, Fact, Fiche, METHOD_LABELS, Method } from '../../core/refonte.types';

interface BuildingSummary { id: number; name: string; }
interface BuildingInfo {
  id: number; name: string; address: string; rnb_id: string; pipeline_status: string;
  pipeline: { steps?: Record<string, { ok: boolean; error?: string }>; horizon_deg?: Record<string, number>;
    geometry?: { emprise_m2: number; mitoyennete: number }; sources?: string[] };
}

/** Fiche d'un bâtiment (refonte, Lot 8) : chaque valeur avec sa provenance ; corriger = poser une
 * surcharge ; « revenir à l'automatique » = la retirer. Les cartes d'affinage s'y ajoutent lot par lot. */
@Component({
  selector: 'app-fiche',
  standalone: true,
  imports: [FormsModule, RouterLink, DatePipe, DecimalPipe, KeyValuePipe, ResultatCarteComponent, QuestionnaireComponent, MeteoCarteComponent, ProtectionsComponent, GeometrieComponent, SystemesComponent, MesuresComponent, ScenariosComponent, DocumentsComponent, CalculsComponent, SimulationComponent],
  templateUrl: './fiche.component.html',
  styleUrl: './fiche.component.scss',
})
export class FicheComponent implements OnInit {
  private api = inject(ApiService);
  private rf = inject(RefonteApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  buildings = signal<BuildingSummary[]>([]);
  buildingId: number | null = null;
  catalogue = signal<Catalogue | null>(null);
  fiche = signal<Fiche | null>(null);
  error = signal('');
  saving = signal(false);
  ficheVersion = signal(0);
  /** Calcul à ouvrir dans l'onglet Calculs (Lot 25 : fin d'une simulation). */
  openRun = signal<number | null>(null);
  tab = signal('synthese');
  tabs: [string, string][] = [['synthese', 'Synthèse'], ['simulation', 'Simulation'], ['meteo', 'Météo'], ['geometrie', 'Géométrie'], ['protections', 'Protections solaires'], ['systemes', 'Systèmes'], ['mesures', 'Mesures et calage'], ['scenarios', 'Scénarios'], ['calculs', 'Calculs'], ['documents', 'Documents'],
    ['donnees', 'Données et provenance']];

  showRun(id: number): void { this.openRun.set(id); this.setTab('calculs'); this.ficheVersion.update(v => v + 1); }

  setTab(t: string): void {
    this.tab.set(t);
    void this.router.navigate([], { queryParams: { onglet: t }, queryParamsHandling: 'merge', replaceUrl: true });
  }
  methodLabels = METHOD_LABELS;

  editing = signal<string | null>(null);
  editValue: unknown = null;
  editMethod: Method = 'declare';
  editSource = '';
  history = signal<{ key: string; rows: Fact[] } | null>(null);

  // ── Lot 9 : fiche initiale automatique ──
  building = signal<BuildingInfo | null>(null);
  initAddress = '';
  initJob = signal<{ id: number; status: string; progress: number; message: string } | null>(null);
  private initTimer?: ReturnType<typeof setInterval>;
  stepLabels: Record<string, string> = { A1: 'Identification (BAN, RNB)', A2: 'Fiche BDNB et DPE',
    A3: 'Géométrie, mitoyenneté, masques (BD TOPO)', A5: 'Compositions par archétype' };

  initFromAddress(): void {
    const a = this.initAddress.trim();
    if (a.length < 3) return;
    this.error.set('');
    this.rf.post<{ building_id: number; job: { id: number; status: string; progress: number; message: string } }>(
      '/batiments/initialiser/', { address: a }).subscribe({
      next: r => { this.initJob.set(r.job); this.watchInit(r.job.id, r.building_id); },
      error: e => this.error.set(e?.error?.detail ?? e?.error?.non_field_errors?.[0] ?? 'Initialisation refusée.'),
    });
  }

  reinit(): void {
    if (this.buildingId === null) return;
    this.rf.post<{ building_id: number; job: { id: number; status: string; progress: number; message: string } }>(
      `/batiments/${this.buildingId}/initialiser/`, {}).subscribe({
      next: r => { this.initJob.set(r.job); this.watchInit(r.job.id, r.building_id); },
      error: e => this.error.set(e?.error?.detail ?? 'Initialisation refusée.'),
    });
  }

  private watchInit(jobId: number, buildingId: number): void {
    clearInterval(this.initTimer);
    this.initTimer = setInterval(() => {
      this.api.getJob(jobId).subscribe({ next: j => {
        const job = j as { id: number; status: string; progress: number; message: string };
        this.initJob.set(job);
        if (job.status === 'DONE' || job.status === 'ERROR') {
          clearInterval(this.initTimer);
          this.api.getBuildings().subscribe({ next: bs => this.buildings.set(bs as BuildingSummary[]) });
          if (this.buildingId === buildingId) this.load();
          else void this.router.navigate([], { queryParams: { b: buildingId } });
        }
      } });
    }, 1500);
  }

  stepsOf(b: BuildingInfo): { key: string; ok: boolean; error?: string }[] {
    const steps = b.pipeline?.steps ?? {};
    return Object.keys(steps).map(k => ({ key: k, ok: steps[k].ok, error: steps[k].error }));
  }

  blocks = computed(() => {
    const cat = this.catalogue();
    if (!cat) return [];
    return cat.blocks.map(([id, label]) => ({ id, label, keys: cat.keys.filter(k => k.block === id) }));
  });

  ngOnInit(): void {
    this.rf.catalogue().subscribe({ next: c => this.catalogue.set(c) });
    this.api.getBuildings().subscribe({ next: bs => this.buildings.set(bs as BuildingSummary[]) });
    this.route.queryParamMap.subscribe(p => {
      const t = p.get('onglet');
      if (t && this.tabs.some(x => x[0] === t)) this.tab.set(t);
      const b = Number(p.get('b'));
      if (b === this.buildingId && this.fiche()) return;
      this.buildingId = Number.isFinite(b) && b > 0 ? b : null;
      this.load();
    });
  }

  onBuildingChange(): void {
    void this.router.navigate([], { queryParams: { b: this.buildingId }, queryParamsHandling: 'merge' });
  }

  load(): void {
    this.fiche.set(null);
    this.editing.set(null);
    this.history.set(null);
    this.building.set(null);
    if (this.buildingId === null) return;
    this.api.getBuilding(this.buildingId).subscribe({ next: b => this.building.set(b as BuildingInfo) });
    this.rf.fiche(this.buildingId).subscribe({
      next: f => this.fiche.set(f),
      error: () => this.error.set('Fiche introuvable.'),
    });
  }

  fact(key: string): Fact | undefined { return this.fiche()?.facts[key]; }

  display(k: CatalogueKey, v: unknown): string {
    if (v === null || v === undefined || v === '') return '—';
    if (k.kind === 'enum') return k.choices?.[String(v)] ?? String(v);
    if (k.kind === 'bool') return v ? 'oui' : 'non';
    if (typeof v === 'number') {
      const s = Math.abs(v) >= 100 ? v.toFixed(0) : (Math.round(v * 1000) / 1000).toString().replace('.', ',');
      return k.unit ? `${s} ${k.unit}` : s;
    }
    return String(v);
  }

  gaugeClass(x: number): string { return x < 0.35 ? 'low' : x < 0.65 ? 'mid' : 'high'; }

  startEdit(k: CatalogueKey): void {
    const f = this.fact(k.key);
    this.editing.set(k.key);
    this.editValue = f?.value ?? (k.kind === 'bool' ? false : null);
    this.editMethod = 'declare';
    this.editSource = '';
    this.error.set('');
  }

  save(k: CatalogueKey): void {
    if (this.buildingId === null) return;
    let value: unknown = this.editValue;
    if ((k.kind === 'number' || k.kind === 'int') && value !== null && value !== '') value = Number(value);
    if (value === '') value = null;
    this.saving.set(true);
    this.rf.declare(this.buildingId, { key: k.key, value, method: this.editMethod, source: this.editSource || 'saisie' }).subscribe({
      next: f => { this.fiche.set(f); this.editing.set(null); this.saving.set(false); this.ficheVersion.update(v => v + 1); },
      error: e => { this.error.set(e?.error?.detail ?? 'Valeur refusée.'); this.saving.set(false); },
    });
  }

  backToAuto(f: Fact): void {
    this.rf.retire(f.id).subscribe({
      next: fi => { this.fiche.set(fi); this.ficheVersion.update(v => v + 1); },
      error: e => this.error.set(e?.error?.detail ?? 'Impossible de retirer cette valeur.'),
    });
  }

  onQuestionnaire(): void {
    this.ficheVersion.update(v => v + 1);
    if (this.buildingId !== null) this.rf.fiche(this.buildingId).subscribe({ next: f => this.fiche.set(f) });
  }

  showHistory(k: CatalogueKey): void {
    if (this.buildingId === null) return;
    if (this.history()?.key === k.key) { this.history.set(null); return; }
    this.rf.history(this.buildingId, k.key).subscribe({ next: r => this.history.set({ key: k.key, rows: r.results }) });
  }
}
