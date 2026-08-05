import { Component, OnDestroy, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { parseMeshFile } from '../../core/mesh-import';
import { Building, Job, WorkingTriangle } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';

interface ParoiModelSummary {
  id: number;
  name: string;
}

interface BuildingSummary {
  id: number;
  name: string;
  description: string;
  updated_at: string;
}

interface EnvironmentSummary {
  id: number;
  name: string;
}

const SERIES_COLOR_VARS = [
  '--series-1', '--series-2', '--series-3', '--series-4',
  '--series-5', '--series-6', '--series-7', '--series-8',
];

const POLL_INTERVAL_MS = 2000;

@Component({
  selector: 'app-batiment',
  standalone: true,
  imports: [FormsModule, RouterLink, MeshViewerComponent],
  templateUrl: './batiment.component.html',
  styleUrl: './batiment.component.scss',
})
export class BatimentComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  @ViewChild(MeshViewerComponent) viewer?: MeshViewerComponent;

  // ── Bibliothèques ────────────────────────────────────────────────────
  paroiModels = signal<ParoiModelSummary[]>([]);
  buildings = signal<BuildingSummary[]>([]);
  environments = signal<EnvironmentSummary[]>([]);

  // ── Bâtiment en cours d'édition ──────────────────────────────────────
  currentBuildingId = signal<number | null>(null);
  buildingName = '';
  buildingDescription = '';
  selectedEnvironmentId: number | null = null;
  sunVisibilityStale = signal(true);

  // ── Précalcul d'ombrage (Celery, cf. api/tasks.py) ────────────────────
  shadowJob = signal<Job | null>(null);
  private pollHandle?: ReturnType<typeof setInterval>;

  vertices = signal<number[][]>([]);
  triangles = signal<WorkingTriangle[]>([]);
  groups = signal<string[]>([]);
  groupAssignments: Record<string, number | null> = {};

  selectedIndices = signal<Set<number>>(new Set());
  bulkAssignModelId: number | null = null;

  // ── Raffinement du maillage ───────────────────────────────────────────
  refineMaxEdge = 1.0;
  refining = signal(false);

  loading = signal(false);
  saving = signal(false);
  error = signal('');
  message = signal('');

  private modelColorMap = new Map<number, string>();

  ngOnInit(): void {
    this.refreshParoiModels();
    this.refreshBuildings();
    this.refreshEnvironments();
  }

  ngOnDestroy(): void {
    this.stopPoll();
  }

  private refreshParoiModels(): void {
    this.api.getParoiModeles().subscribe({
      next: (models) => this.paroiModels.set(models as ParoiModelSummary[]),
      error: () => {},
    });
  }

  private refreshBuildings(): void {
    this.api.getBuildings().subscribe({
      next: (buildings) => this.buildings.set(buildings as BuildingSummary[]),
      error: () => {},
    });
  }

  private refreshEnvironments(): void {
    this.api.getEnvironments().subscribe({
      next: (envs) => this.environments.set(envs as EnvironmentSummary[]),
      error: () => {},
    });
  }

  // ── Import de fichier ────────────────────────────────────────────────
  async onFileSelected(evt: Event): Promise<void> {
    const input = evt.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;

    this.error.set('');
    this.message.set('');
    try {
      const parsed = await parseMeshFile(file);
      this.currentBuildingId.set(null);
      this.buildingName = file.name.replace(/\.(obj|stl)$/i, '');
      this.buildingDescription = '';
      this.vertices.set(parsed.vertices);
      this.triangles.set(parsed.triangles);
      this.groups.set(parsed.groups);
      this.groupAssignments = {};
      this.selectedIndices.set(new Set());
      this.modelColorMap.clear();
      this.message.set(
        `Fichier chargé : ${parsed.vertices.length} sommets, ${parsed.triangles.length} triangles` +
        (parsed.groups.length ? `, ${parsed.groups.length} groupe(s).` : ' (sans groupes — sélection manuelle nécessaire).'),
      );
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Échec de la lecture du fichier.");
    }
  }

  // ── Couleurs ─────────────────────────────────────────────────────────
  private colorForModel(id: number): string {
    if (!this.modelColorMap.has(id)) {
      this.modelColorMap.set(id, SERIES_COLOR_VARS[this.modelColorMap.size % SERIES_COLOR_VARS.length]);
    }
    return this.modelColorMap.get(id)!;
  }

  colorForTriangle = (index: number): string => {
    if (this.selectedIndices().has(index)) return '--accent';
    const t = this.triangles()[index];
    if (!t || t.paroi_model_id == null) return '--warning';
    return this.colorForModel(t.paroi_model_id);
  };

  modelName(id: number | null): string {
    if (id == null) return '—';
    return this.paroiModels().find(m => m.id === id)?.name ?? `#${id}`;
  }

  // ── Assignation par groupe (OBJ) ─────────────────────────────────────
  assignGroup(group: string): void {
    const modelId = this.groupAssignments[group] ?? null;
    this.triangles.update(list => list.map(t => (t.group === group ? { ...t, paroi_model_id: modelId } : t)));
    this.viewer?.repaint();
  }

  triangleCountForGroup(group: string): number {
    return this.triangles().filter(t => t.group === group).length;
  }

  // ── Sélection manuelle (clic dans le viewer, STL ou triangles isolés) ─
  onTriangleClick(index: number): void {
    this.selectedIndices.update(set => {
      const next = new Set(set);
      if (next.has(index)) next.delete(index); else next.add(index);
      return next;
    });
    this.viewer?.repaint();
  }

  clearSelection(): void {
    this.selectedIndices.set(new Set());
    this.viewer?.repaint();
  }

  assignSelection(): void {
    const sel = this.selectedIndices();
    if (sel.size === 0) return;
    this.triangles.update(list => list.map((t, i) => (sel.has(i) ? { ...t, paroi_model_id: this.bulkAssignModelId } : t)));
    this.selectedIndices.set(new Set());
    this.viewer?.repaint();
  }

  // ── Résumé ───────────────────────────────────────────────────────────
  get assignedCount(): number {
    return this.triangles().filter(t => t.paroi_model_id != null).length;
  }

  get totalCount(): number {
    return this.triangles().length;
  }

  // ── Sauvegarde / chargement ──────────────────────────────────────────
  save(): void {
    const name = this.buildingName.trim();
    if (!name || this.totalCount === 0) return;
    this.saving.set(true);
    this.error.set('');
    this.message.set('');

    const triangles = this.triangles().map(t => ({ v: t.v, group: t.group, paroi_model_id: t.paroi_model_id }));
    const id = this.currentBuildingId();
    const payload: Record<string, unknown> = {
      name, description: this.buildingDescription, triangles,
      environment_id: this.selectedEnvironmentId,
    };
    // Les sommets ne sont renvoyés que lors d'un nouvel import (nouveau
    // bâtiment, ou fichier rechargé) — sinon on les laisse tels quels côté
    // serveur (voir BuildingSerializer._build_envelope).
    if (id === null || this.vertices().length > 0) {
      payload['vertices'] = this.vertices();
    }

    const req = id === null ? this.api.createBuilding(payload) : this.api.updateBuilding(id, payload);
    req.subscribe({
      next: (res) => {
        const b = res as Building;
        this.currentBuildingId.set(b.id);
        this.vertices.set(b.envelope.vertices);
        this.triangles.set(b.envelope.triangles);
        this.sunVisibilityStale.set(b.sun_visibility_stale);
        this.saving.set(false);
        this.message.set(`Bâtiment « ${b.name} » enregistré.`);
        this.refreshBuildings();
        this.viewer?.repaint();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(err?.error?.name?.[0] ?? err?.error?.triangles?.[0] ?? "Échec de l'enregistrement.");
      },
    });
  }

  loadBuilding(summary: BuildingSummary): void {
    this.loading.set(true);
    this.error.set('');
    this.message.set('');
    this.api.getBuilding(summary.id).subscribe({
      next: (res) => {
        const b = res as Building;
        this.currentBuildingId.set(b.id);
        this.buildingName = b.name;
        this.buildingDescription = b.description;
        this.vertices.set(b.envelope.vertices);
        this.triangles.set(b.envelope.triangles);
        this.groups.set([...new Set(b.envelope.triangles.map(t => t.group).filter((g): g is string => !!g))]);
        this.groupAssignments = {};
        this.selectedIndices.set(new Set());
        this.modelColorMap.clear();
        this.selectedEnvironmentId = b.environment_id;
        this.sunVisibilityStale.set(b.sun_visibility_stale);
        this.shadowJob.set(null);
        this.stopPoll();
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Impossible de charger ce bâtiment.');
      },
    });
  }

  newBuilding(): void {
    this.currentBuildingId.set(null);
    this.buildingName = '';
    this.buildingDescription = '';
    this.vertices.set([]);
    this.triangles.set([]);
    this.groups.set([]);
    this.groupAssignments = {};
    this.selectedIndices.set(new Set());
    this.modelColorMap.clear();
    this.selectedEnvironmentId = null;
    this.sunVisibilityStale.set(true);
    this.shadowJob.set(null);
    this.stopPoll();
    this.error.set('');
    this.message.set('');
  }

  // ── Raffinement du maillage ───────────────────────────────────────────
  refineMesh(): void {
    const id = this.currentBuildingId();
    if (id === null || this.refining()) return;
    this.refining.set(true);
    this.error.set('');
    this.message.set('');

    this.api.refineBuildingMesh(id, this.refineMaxEdge).subscribe({
      next: (res) => {
        const b = res as Building;
        this.vertices.set(b.envelope.vertices);
        this.triangles.set(b.envelope.triangles);
        this.groups.set([...new Set(b.envelope.triangles.map(t => t.group).filter((g): g is string => !!g))]);
        // Les indices de triangle changent après raffinement : la sélection
        // manuelle en cours n'a plus de sens.
        this.selectedIndices.set(new Set());
        this.sunVisibilityStale.set(b.sun_visibility_stale);
        this.refining.set(false);
        this.message.set(`Maillage raffiné : ${b.envelope.triangles.length} triangles.`);
        this.viewer?.repaint();
      },
      error: (err) => {
        this.refining.set(false);
        this.error.set(err?.error?.max_edge_length?.[0] ?? err?.error?.detail ?? "Échec du raffinement.");
      },
    });
  }

  // ── Précalcul d'ombrage ──────────────────────────────────────────────
  triggerPrecompute(): void {
    const id = this.currentBuildingId();
    if (id === null) return;
    this.error.set('');
    this.api.precomputeShadows(id).subscribe({
      next: (res) => {
        this.shadowJob.set(res as Job);
        this.startPoll();
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "Échec du lancement du précalcul.");
      },
    });
  }

  private startPoll(): void {
    this.stopPoll();
    const job = this.shadowJob();
    if (!job) return;
    this.pollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.shadowJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopPoll();
            if (updated.status === 'DONE') this.sunVisibilityStale.set(false);
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopPoll(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = undefined;
    }
  }

  removeBuilding(summary: BuildingSummary): void {
    if (!confirm(`Supprimer le bâtiment « ${summary.name} » ?`)) return;
    this.api.deleteBuilding(summary.id).subscribe({
      next: () => {
        if (this.currentBuildingId() === summary.id) this.newBuilding();
        this.refreshBuildings();
      },
      error: () => this.error.set('Échec de la suppression.'),
    });
  }
}
