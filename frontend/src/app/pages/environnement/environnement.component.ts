import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe, UpperCasePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { parseMeshFile } from '../../core/mesh-import';
import { EnvironmentMesh, EnvironmentObject, Job } from '../../core/building.types';
import { EnvSceneComponent } from '../../components/env-scene/env-scene.component';

interface EnvironmentSummary {
  id: number;
  name: string;
  description: string;
  n_objects: number;
  updated_at: string;
}

interface GenerateResult {
  environment_id: number;
  source: 'lidar' | 'legacy';
  warnings: string[];
}

interface FitReport {
  iou: number;
  up_axis: 'z' | 'y';
  scale: number;
  rotation_deg: number;
  shift_m: [number, number];
  degenerate_removed: number;
}

/** Lot AI — pré-assignation des parois d'après la BD TOPO (rapport serveur). */
interface MaterialsReport {
  basis: string;
  era: string | null;
  assigned: { wall: string | null; roof: string | null; floor: string | null };
  wall_alpha: number | null;
  roof_alpha: number | null;
}

const POLL_INTERVAL_MS = 2000;

/** Libellés lisibles de la provenance d'un bâtiment. */
const ORIGIN_LABELS: Record<string, string> = {
  'bdtopo+lidar': 'BD TOPO recalée sur le LiDAR, toit relevé',
  'bdtopo': 'BD TOPO seule (toit non observé par le LiDAR)',
  'lidar': 'LiDAR seul (absent de la BD TOPO)',
  'osm': 'OpenStreetMap (hauteur forfaitaire)',
  'bdtopo-osm': 'BD TOPO / OpenStreetMap',
  'ign': 'IGN RGE ALTI',
  'open-meteo': 'Open-Meteo',
};

/**
 * Lot AH — l'environnement est désormais généré automatiquement depuis le
 * LiDAR HD IGN croisé avec la BD TOPO, et enregistré tel quel, décomposé en
 * objets. Cette page sert à le relire et à l'ajuster objet par objet : retirer
 * ce qui ne doit pas masquer le soleil, restaurer, désigner le bâtiment étudié
 * parmi les bâtiments, ou le remplacer par un modèle 3D importé.
 */
@Component({
  selector: 'app-environnement',
  standalone: true,
  imports: [FormsModule, RouterLink, DecimalPipe, UpperCasePipe, EnvSceneComponent],
  templateUrl: './environnement.component.html',
  styleUrl: './environnement.component.scss',
})
export class EnvironnementComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  environments = signal<EnvironmentSummary[]>([]);
  buildings = signal<{ id: number; name: string }[]>([]);

  // ── Environnement chargé ─────────────────────────────────────────────
  env = signal<EnvironmentMesh | null>(null);
  name = '';
  description = '';
  // Sélection : un objet, ou plusieurs bâtiments en mode « sélection
  // multiple » (plusieurs emprises BD TOPO qui ne forment qu'un bâtiment).
  selectedIds = signal<number[]>([]);
  multiSelect = false;
  showTexture = true;

  loading = signal(false);
  saving = signal(false);
  busy = signal(false);
  error = signal('');
  message = signal('');
  createdBuilding = signal<{ id: number; name: string } | null>(null);
  fitReport = signal<FitReport | null>(null);
  materialsReport = signal<MaterialsReport | null>(null);

  // ── Génération ───────────────────────────────────────────────────────
  genLat = 47.3445;
  genLon = 0.6613;
  genRadius = 150;
  genVegetation = true;
  genTerrain = true;
  genBuildingId: number | null = null;
  genName = '';
  generating = signal(false);
  generateJob = signal<Job | null>(null);
  private pollHandle?: ReturnType<typeof setInterval>;

  // ── Actions sur l'objet sélectionné ──────────────────────────────────
  studyName = '';
  replaceUpAxis: 'auto' | 'z' | 'y' = 'auto';
  replaceScale: 'auto' | '1' | '0.01' | '0.001' | '0.0254' = 'auto';

  ngOnInit(): void {
    this.refresh();
    this.api.getBuildings().subscribe({
      next: (bs) => this.buildings.set(bs as { id: number; name: string }[]),
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopPoll();
  }

  private refresh(): void {
    this.api.getEnvironments().subscribe({
      next: (envs) => this.environments.set(envs as EnvironmentSummary[]),
      error: () => {},
    });
  }

  // ── Chargement / affichage ───────────────────────────────────────────
  load(id: number): void {
    this.loading.set(true);
    this.error.set('');
    this.api.getEnvironment(id).subscribe({
      next: (res) => {
        this.applyEnvironment(res as EnvironmentMesh, true);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Impossible de charger cet environnement.');
      },
    });
  }

  /** La scène (EnvSceneComponent) ne reconstruit sa géométrie que si
   * l'environnement change d'identité ou d'objets ; un changement de statut
   * la repeint seulement, la caméra ne bouge pas. */
  private applyEnvironment(e: EnvironmentMesh, resetSelection: boolean): void {
    this.env.set(e);
    this.name = e.name;
    this.description = e.description;
    if (resetSelection) this.selectedIds.set([]);
  }

  get hasObjects(): boolean {
    return (this.env()?.objects?.length ?? 0) > 0;
  }

  get selectedObjects(): EnvironmentObject[] {
    const ids = this.selectedIds();
    return (this.env()?.objects ?? []).filter(o => ids.includes(o.id));
  }

  /** L'objet détaillé dans la fiche : seulement quand un seul est choisi. */
  get selected(): EnvironmentObject | null {
    const objs = this.selectedObjects;
    return objs.length === 1 ? objs[0] : null;
  }

  get selectionIsStudiable(): boolean {
    const objs = this.selectedObjects;
    return objs.length > 0 && objs.every(o => o.kind === 'building' && o.status !== 'studied');
  }

  onObjectClick(obj: EnvironmentObject): void {
    const ids = this.selectedIds();
    if (this.multiSelect) {
      // En sélection multiple, seuls les bâtiments comptent : un clic tombé sur
      // le terrain ou un arbre entre deux bâtiments ne doit pas effacer la
      // sélection en cours (constaté en navigateur).
      if (obj.kind !== 'building' || obj.status === 'studied') return;
      this.selectedIds.set(ids.includes(obj.id) ? ids.filter(i => i !== obj.id) : [...ids, obj.id]);
    } else {
      this.selectedIds.set(ids.length === 1 && ids[0] === obj.id ? [] : [obj.id]);
    }
    this.studyName = '';
    this.fitReport.set(null);
    this.materialsReport.set(null);
    this.createdBuilding.set(null);
  }

  select(obj: EnvironmentObject): void {
    this.selectedIds.set([obj.id]);
    this.fitReport.set(null);
    this.createdBuilding.set(null);
  }

  clearSelection(): void {
    this.selectedIds.set([]);
  }

  // ── Synthèse ─────────────────────────────────────────────────────────
  count(kind: string, status?: string, origin?: string): number {
    return (this.env()?.objects ?? []).filter(o =>
      o.kind === kind && (!status || o.status === status) && (!origin || o.origin === origin)).length;
  }

  get removedObjects(): EnvironmentObject[] {
    return (this.env()?.objects ?? []).filter(o => o.status === 'removed');
  }

  get studiedObjects(): EnvironmentObject[] {
    return (this.env()?.objects ?? []).filter(o => o.status === 'studied');
  }

  studiedBuilding(obj: EnvironmentObject): { id: number; name: string } | null {
    return this.env()?.studied_buildings?.[String(obj.id)] ?? null;
  }

  originLabel(origin: string): string {
    return ORIGIN_LABELS[origin] ?? origin;
  }

  info(obj: EnvironmentObject, key: string): unknown {
    return obj.info?.[key];
  }

  num(obj: EnvironmentObject, key: string): number | null {
    const v = obj.info?.[key];
    return typeof v === 'number' ? v : null;
  }

  shiftLabel(obj: EnvironmentObject): string {
    const s = obj.info?.['shift_m'] as number[] | undefined;
    if (!s) return '—';
    return `${Math.hypot(s[0], s[1]).toFixed(1)} m (est ${s[0].toFixed(1)}, nord ${s[1].toFixed(1)})`;
  }

  // ── Actions ──────────────────────────────────────────────────────────
  setStatus(ids: number[], status: 'active' | 'removed'): void {
    const e = this.env();
    if (!e || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.api.setEnvironmentObjectsStatus(e.id, ids, status).subscribe({
      next: (res) => {
        this.busy.set(false);
        this.applyEnvironment(res as EnvironmentMesh, false);
        this.message.set(status === 'removed'
          ? `${ids.length} objet(s) retiré(s) des obstacles. L'ombrage des bâtiments liés est à recalculer.`
          : `${ids.length} objet(s) restauré(s). L'ombrage des bâtiments liés est à recalculer.`);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? 'Échec de la mise à jour.');
      },
    });
  }

  studySelected(): void {
    const e = this.env();
    const ids = this.selectedIds();
    if (!e || !ids.length || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.api.studyEnvironmentObjects(e.id, ids, this.studyName.trim()).subscribe({
      next: (res) => {
        const r = res as { building: { id: number; name: string }; environment: EnvironmentMesh;
          materials: MaterialsReport | null };
        this.busy.set(false);
        this.applyEnvironment(r.environment, false);
        this.createdBuilding.set({ id: r.building.id, name: r.building.name });
        this.materialsReport.set(r.materials);
        this.buildings.update(list => [...list, { id: r.building.id, name: r.building.name }]);
        this.message.set(ids.length > 1
          ? `Bâtiment étudié « ${r.building.name} » créé : ${ids.length} emprises réunies en une seule enveloppe, liée à cet environnement.`
          : `Bâtiment étudié « ${r.building.name} » créé, déjà lié à cet environnement.`);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? 'Échec de la création du bâtiment étudié.');
      },
    });
  }

  async replaceSelected(evt: Event): Promise<void> {
    const input = evt.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    const e = this.env();
    const obj = this.selected;
    if (!file || !e || !obj || this.busy()) return;
    this.error.set('');
    this.fitReport.set(null);
    let parsed;
    try {
      parsed = await parseMeshFile(file);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : 'Échec de la lecture du fichier.');
      return;
    }
    this.busy.set(true);
    this.api.replaceEnvironmentObject(e.id, obj.id, {
      vertices: parsed.vertices,
      triangles: parsed.triangles.map(t => ({ v: t.v, group: t.group })),
      name: this.studyName.trim() || file.name.replace(/\.(obj|stl)$/i, ''),
      up_axis: this.replaceUpAxis, scale: this.replaceScale,
    }).subscribe({
      next: (res) => {
        const r = res as { building: { id: number; name: string }; environment: EnvironmentMesh; fit: FitReport };
        this.busy.set(false);
        this.applyEnvironment(r.environment, false);
        this.fitReport.set(r.fit);
        this.createdBuilding.set({ id: r.building.id, name: r.building.name });
        this.buildings.update(list => [...list, { id: r.building.id, name: r.building.name }]);
        this.message.set(`Modèle « ${r.building.name} » placé à la place de l'objet et lié à cet environnement.`);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(err?.error?.detail ?? err?.error?.triangles?.[0] ?? "Échec du placement du modèle.");
      },
    });
  }

  saveMeta(): void {
    const e = this.env();
    const name = this.name.trim();
    if (!e || !name) return;
    this.saving.set(true);
    this.api.updateEnvironment(e.id, { name, description: this.description }).subscribe({
      next: () => {
        this.saving.set(false);
        this.message.set('Nom et description enregistrés.');
        this.refresh();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(err?.error?.name?.[0] ?? "Échec de l'enregistrement.");
      },
    });
  }

  remove(summary: EnvironmentSummary): void {
    if (!confirm(`Supprimer l'environnement « ${summary.name} » ?`)) return;
    this.api.deleteEnvironment(summary.id).subscribe({
      next: () => {
        if (this.env()?.id === summary.id) this.closeEnvironment();
        this.refresh();
      },
      error: () => this.error.set('Échec de la suppression.'),
    });
  }

  closeEnvironment(): void {
    this.env.set(null);
    this.selectedIds.set([]);
  }

  // ── Génération ───────────────────────────────────────────────────────
  generate(): void {
    this.error.set('');
    this.message.set('');
    this.generating.set(true);
    this.generateJob.set(null);
    this.api.generateEnvironment({
      lat: this.genLat, lon: this.genLon, radius_m: this.genRadius,
      include_vegetation: this.genVegetation, include_terrain: this.genTerrain,
      building_id: this.genBuildingId, name: this.genName.trim(),
    }).subscribe({
      next: (res) => {
        this.generateJob.set(res as Job);
        this.startPoll();
      },
      error: (err) => {
        this.generating.set(false);
        const e = err?.error ?? {};
        this.error.set(e.radius_m?.[0] ?? e.lat?.[0] ?? e.lon?.[0] ?? e.building_id?.[0]
          ?? e.non_field_errors?.[0] ?? e.detail ?? 'Échec du lancement de la génération.');
      },
    });
  }

  private startPoll(): void {
    this.stopPoll();
    const job = this.generateJob();
    if (!job) return;
    this.pollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.generateJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopPoll();
            this.generating.set(false);
            if (updated.status === 'DONE') {
              const r = updated.result as unknown as GenerateResult;
              this.refresh();
              this.load(r.environment_id);
            } else {
              this.error.set(updated.message || 'Échec de la génération.');
            }
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

  // ── Import d'un maillage d'un seul tenant (usage avancé) ─────────────
  async importMeshFile(evt: Event): Promise<void> {
    const input = evt.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.error.set('');
    try {
      const parsed = await parseMeshFile(file);
      this.saving.set(true);
      this.api.createEnvironment({
        name: file.name.replace(/\.(obj|stl)$/i, '') + ` — import ${new Date().toISOString().slice(0, 16)}`,
        vertices: parsed.vertices, triangles: parsed.triangles.map(t => ({ v: t.v })),
      }).subscribe({
        next: (res) => {
          this.saving.set(false);
          this.refresh();
          this.applyEnvironment(res as EnvironmentMesh, true);
          this.message.set(`Maillage importé : ${parsed.vertices.length} sommets, ${parsed.triangles.length} triangles.`);
        },
        error: (err) => {
          this.saving.set(false);
          this.error.set(err?.error?.name?.[0] ?? err?.error?.triangles ?? "Échec de l'import.");
        },
      });
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : 'Échec de la lecture du fichier.');
    }
  }
}
