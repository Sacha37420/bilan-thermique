import { Component, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ContexteService } from '../../core/contexte.service';
import { APPROFONDISSEMENT_LABELS, USAGE_LABELS } from '../../core/refonte.types';
import { Building } from '../../core/building.types';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { ResultatCarteComponent, NIVEAU_MOTS } from '../../components/resultat-carte/resultat-carte.component';
import { QuestionnaireComponent } from '../../components/questionnaire/questionnaire.component';
import { GeometrieComponent } from '../../components/geometrie/geometrie.component';
import { ProtectionsComponent } from '../../components/protections/protections.component';
import { SystemesComponent } from '../../components/systemes/systemes.component';
import { MeteoCarteComponent } from '../../components/meteo-carte/meteo-carte.component';
import { MesuresComponent } from '../../components/mesures/mesures.component';
import { FacadePanelComponent } from '../../components/facade-panel/facade-panel.component';
import { BatimentComponent } from '../batiment/batiment.component';
import { EnvironnementComponent } from '../environnement/environnement.component';
import { DonneesComponent } from './donnees.component';
import { BLOCS_PHYSIQUE, BuildingInfo, ParoiModelSummary, STEP_LABELS, VUES_BATIMENT, VueBatiment } from './onglet-batiment.types';

/**
 * Onglet « Bâtiment » (chantier bt-navigation) : le bâtiment courant du bandeau, vu sous toutes ses
 * faces — synthèse, données et provenance, géométrie (un seul endroit), physique (enveloppe,
 * systèmes, protections), météo (seul endroit où on la choisit), environnement et façades, mesures.
 * On n'y lance aucun calcul : la synthèse renvoie vers l'onglet Calculs.
 */
@Component({
  selector: 'app-onglet-batiment',
  standalone: true,
  imports: [RouterLink, DecimalPipe, ContexteBarComponent, ResultatCarteComponent, QuestionnaireComponent,
    GeometrieComponent, ProtectionsComponent, SystemesComponent, MeteoCarteComponent, MesuresComponent,
    FacadePanelComponent, BatimentComponent, EnvironnementComponent, DonneesComponent],
  templateUrl: './onglet-batiment.component.html',
  styleUrl: './onglet-batiment.component.scss',
})
export class OngletBatimentComponent implements OnDestroy {
  protected ctx = inject(ContexteService);
  private api = inject(ApiService);
  private rf = inject(RefonteApiService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  readonly vues = VUES_BATIMENT;
  readonly stepLabels = STEP_LABELS;
  readonly usageLabels = USAGE_LABELS;
  readonly approLabels = APPROFONDISSEMENT_LABELS;
  readonly mots = NIVEAU_MOTS;
  readonly blocsPhysique = BLOCS_PHYSIQUE;

  private params = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  vue = computed<VueBatiment>(() => {
    const v = this.params().get('vue');
    return (VUES_BATIMENT.some(x => x.id === v) ? v : 'synthese') as VueBatiment;
  });
  cle = computed(() => this.params().get('cle'));

  /** Détail du bâtiment courant (chaîne automatique, façades, géoréférencement). */
  info = signal<BuildingInfo | null>(null);
  paroiModels = signal<ParoiModelSummary[]>([]);
  /** Incrémenté à chaque changement des données : relit résultat, questionnaire, systèmes. */
  version = signal(0);
  initJob = signal<{ id: number; status: string; progress: number; message: string } | null>(null);
  error = signal('');
  private initTimer?: ReturnType<typeof setInterval>;
  private paroisChargees = false;

  constructor() {
    effect(() => {
      const id = this.ctx.batimentId();
      this.info.set(null);
      this.initJob.set(null);
      this.error.set('');
      clearInterval(this.initTimer);
      if (id !== null) this.chargerInfo(id);
    });
    effect(() => {
      if (this.vue() === 'environnement' && !this.paroisChargees) {
        this.paroisChargees = true;
        this.api.getParoiModeles().subscribe({ next: m => this.paroiModels.set(m as ParoiModelSummary[]) });
      }
    });
  }

  ngOnDestroy(): void { clearInterval(this.initTimer); }

  private chargerInfo(id: number): void {
    this.api.getBuilding(id).subscribe({
      next: b => { if (this.ctx.batimentId() === id) this.info.set(b as BuildingInfo); },
      error: () => this.error.set('Ce bâtiment est introuvable ou ne vous est plus accessible.'),
    });
  }

  setVue(v: VueBatiment): void {
    void this.router.navigate([], { queryParams: { vue: v, cle: null }, queryParamsHandling: 'merge' });
  }

  /** Données modifiées (correction, questionnaire, météo, géométrie…) : tout ce qui en dépend se relit. */
  onChanged(): void {
    this.version.update(v => v + 1);
    void this.ctx.rafraichirBatiments();
  }

  /** Façades : le panneau émet le bâtiment mis à jour. */
  onFacades(b: Building): void { this.info.set(b as unknown as BuildingInfo); this.onChanged(); }

  building(): Building | null { return this.info() as unknown as Building | null; }

  stepsOf(b: BuildingInfo): { key: string; ok: boolean; error?: string }[] {
    const steps = b.pipeline?.steps ?? {};
    return Object.keys(steps).map(k => ({ key: k, ok: steps[k].ok, error: steps[k].error }));
  }

  gaugeClass(x: number): string { return x < 0.35 ? 'low' : x < 0.65 ? 'mid' : 'high'; }

  reinit(): void {
    const id = this.ctx.batimentId();
    if (id === null) return;
    this.error.set('');
    this.rf.post<{ building_id: number; job: { id: number; status: string; progress: number; message: string } }>(
      `/batiments/${id}/initialiser/`, {}).subscribe({
      next: r => { this.initJob.set(r.job); this.suivreInit(r.job.id, id); },
      error: e => this.error.set(e?.error?.detail ?? 'Relance refusée.'),
    });
  }

  private suivreInit(jobId: number, id: number): void {
    clearInterval(this.initTimer);
    this.initTimer = setInterval(() => {
      this.api.getJob(jobId).subscribe({ next: j => {
        const job = j as { id: number; status: string; progress: number; message: string };
        this.initJob.set(job);
        if (job.status === 'DONE' || job.status === 'ERROR') {
          clearInterval(this.initTimer);
          if (this.ctx.batimentId() === id) { this.chargerInfo(id); this.onChanged(); }
        }
      } });
    }, 1500);
  }
}
