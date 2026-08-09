import { Component, OnDestroy, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { parseMeshFile } from '../../core/mesh-import';
import { generateBoxEnvelope } from '../../core/box-generator';
import { Building, BuildingCandidate, Job, ShadingProfileId, TriangleBoundary, WorkingTriangle } from '../../core/building.types';
import { SHADING_PROFILES } from '../../core/shading-profiles';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';
import { BuildingSearchComponent } from '../../components/building-search/building-search.component';

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
  imports: [FormsModule, RouterLink, MeshViewerComponent, BuildingSearchComponent],
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

  // ── Géoréférencement (optionnel) ──────────────────────────────────────
  georefLat: number | null = null;
  georefLon: number | null = null;
  georefNorthOffset = 0;
  georefGroundZ: number | null = null;

  // ── Surface de référence (m², Lot M — normalisation kWh/m²/an du dashboard) ──
  surfaceRefM2: number | null = null;

  // ── Précalcul d'ombrage (Celery, cf. api/tasks.py) ────────────────────
  shadowJob = signal<Job | null>(null);
  private pollHandle?: ReturnType<typeof setInterval>;

  // ── Génération d'environnement pour ce bâtiment ───────────────────────
  envGenJob = signal<Job | null>(null);
  envGenerating = signal(false);
  envGenRadius = 150;
  // Lot AA — maillage de terrain ajouté aux obstacles (opt-in : c'est le plus
  // gros contributeur en triangles, 2 par maille). Le relief masque le soleil
  // rasant, ce qu'aucun bâtiment voisin ne reproduit.
  envGenTerrain = false;
  envGenTerrainSpacing = 10;
  // Lot Z — végétation (zones IGN + arbres isolés OSM). Opt-in : coût en
  // triangles, et transmittance non saisonnière (un caduc est modélisé en
  // feuilles toute l'année, ce qui surestime l'ombrage hivernal).
  envGenVegetation = false;
  // Avertissements de la dernière génération (job.result.warnings) — jamais
  // affichés jusqu'au Lot X, alors que c'est le seul canal qui dise ce qui a
  // été écarté et pourquoi : bâtiment étudié retiré de son propre
  // environnement, hauteurs estimées faute de donnée, limite de maillage
  // atteinte. Un filtrage silencieux est un filtrage invérifiable.
  envGenWarnings = signal<string[]>([]);
  private envGenPollHandle?: ReturnType<typeof setInterval>;

  vertices = signal<number[][]>([]);
  triangles = signal<WorkingTriangle[]>([]);
  groups = signal<string[]>([]);
  groupAssignments: Record<string, number | null> = {};
  // Lot K — assignation de la condition limite ('exterior_air'/'ground') par
  // groupe OBJ, même patron que groupAssignments (paroi_model_id) ci-dessus.
  groupBoundaryAssignments: Record<string, TriangleBoundary> = {};
  // Lot J — dispositif d'occultation mobile par groupe OBJ, même patron
  // (null = aucun dispositif, comme groupAssignments ci-dessus via [ngValue]).
  groupShadingAssignments: Record<string, ShadingProfileId | null> = {};
  shadingProfiles = SHADING_PROFILES;

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
    this.stopEnvGenPoll();
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
      this.groupBoundaryAssignments = {};
      this.groupShadingAssignments = {};
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

  // ── Générateur de boîte (Lot O) — alternative à l'import pour tester
  // l'outil sans modélisateur 3D externe, groupes déjà nommés pour
  // l'assignation par groupe ci-dessous. Voir core/box-generator.ts.
  boxWidth = 8;
  boxLength = 6;
  boxHeight = 3;
  boxRoof: 'flat' | 'gable' = 'flat';
  boxRoofPitchHeight = 1.5;

  generateBox(): void {
    this.error.set('');
    this.message.set('');
    const mesh = generateBoxEnvelope({
      width: this.boxWidth, length: this.boxLength, height: this.boxHeight,
      roof: this.boxRoof, roofPitchHeight: this.boxRoofPitchHeight,
    });
    this.currentBuildingId.set(null);
    this.buildingName = `Boîte ${this.boxWidth}×${this.boxLength}×${this.boxHeight}`;
    this.buildingDescription = '';
    this.vertices.set(mesh.vertices);
    this.triangles.set(mesh.triangles);
    this.groups.set(mesh.groups);
    this.groupAssignments = {};
    this.groupBoundaryAssignments = {};
    this.groupShadingAssignments = {};
    this.selectedIndices.set(new Set());
    this.modelColorMap.clear();
    this.message.set(
      `Boîte générée : ${mesh.vertices.length} sommets, ${mesh.triangles.length} triangles, ${mesh.groups.length} groupe(s).`,
    );
  }

  // ── Recherche d'un bâtiment réel (Lot Y) ─────────────────────────────
  // Troisième source de maillage, à côté de l'import OBJ/STL et du générateur
  // de boîte. L'endpoint existait déjà (Lot T) mais n'était appelé que par le
  // mode simplifié : c'était un chaînon manquant d'interface, pas une
  // fonctionnalité à écrire. Contrairement au mode simplifié, aucun parcours
  // imposé ensuite — tout l'outillage habituel s'applique tel quel.
  //
  // Plafond de parois volontairement relevé : la limite basse du mode simplifié
  // existe parce qu'il génère un menu déroulant PAR PAROI, ce qui n'est pas le
  // cas ici (assignation par groupe + sélection manuelle au clic).
  readonly searchMaxWalls = 400;

  onCandidateChosen(c: BuildingCandidate): void {
    this.error.set('');
    this.currentBuildingId.set(null);
    this.buildingName = `Bâtiment ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}`;
    this.buildingDescription = '';
    this.vertices.set(c.vertices);
    this.triangles.set(c.triangles.map(t => ({ ...t, paroi_model_id: null, shading_profile_id: null })));
    this.groups.set([...new Set(c.triangles.map(t => t.group))]);
    this.groupAssignments = {};
    this.groupBoundaryAssignments = {};
    this.groupShadingAssignments = {};
    this.selectedIndices.set(new Set());
    this.modelColorMap.clear();

    // Le vrai gain par rapport à un import OBJ : le géoréférencement est connu
    // par construction. L'empreinte est produite dans le repère est/nord réel,
    // donc la rotation nord est NULLE — ce qui supprime d'un coup la principale
    // source d'erreur silencieuse du géoréférencement.
    this.georefLat = c.lat;
    this.georefLon = c.lon;
    this.georefNorthOffset = 0;
    this.georefGroundZ = null;
    this.fetchGroundAltitude();

    this.message.set(
      `Bâtiment trouvé à ${c.distance_m} m (${c.source === 'ign' ? 'IGN BD TOPO' : 'OpenStreetMap'}) : `
      + `${c.n_walls} paroi(s), ${c.triangles.length} triangles, hauteur `
      + `${c.height_m.toFixed(1)} m${c.approx_height ? ' (estimée)' : ''}. `
      + `Géoréférencement prérempli — assignez les parois ci-dessous, puis enregistrez.`,
    );
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

  assignGroupBoundary(group: string): void {
    const boundary = this.groupBoundaryAssignments[group] ?? 'exterior_air';
    this.triangles.update(list => list.map(t => (t.group === group ? { ...t, boundary } : t)));
    this.viewer?.repaint();
  }

  assignGroupShading(group: string): void {
    const shadingProfileId = this.groupShadingAssignments[group] ?? null;
    this.triangles.update(list => list.map(t => (t.group === group ? { ...t, shading_profile_id: shadingProfileId } : t)));
    this.viewer?.repaint();
  }

  groundTriangleCount(): number {
    return this.triangles().filter(t => t.boundary === 'ground').length;
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

  // ── Checklist de progression (Lot N) — généralise le badge "ombrage
  // périmé" existant à l'ensemble du parcours. Dérivée uniquement de l'état
  // déjà chargé côté client (aucun nouvel appel réseau). La météo n'y figure
  // volontairement pas : elle n'est jamais persistée côté serveur (Lot L,
  // décision assumée), donc aucun état de "bâtiment" ne peut refléter si une
  // météo a été chargée sur la page Calcul 3D — un lien de suite (dans le
  // template) reste possible, un item coché/décoché non.
  get checklist(): { label: string; done: boolean; hint: string; anchor: string }[] {
    return [
      {
        label: 'Parois assignées',
        done: this.totalCount > 0 && this.assignedCount === this.totalCount,
        hint: this.totalCount === 0
          ? 'Importez un maillage pour commencer.'
          : `${this.assignedCount} / ${this.totalCount} triangles assignés.`,
        anchor: '#section-assignation',
      },
      {
        label: 'Géoréférencement',
        done: this.georefLat !== null && this.georefLon !== null,
        hint: 'Optionnel — nécessaire pour aligner automatiquement un environnement et une météo.',
        anchor: '#section-georef',
      },
      {
        label: 'Environnement lié',
        done: this.selectedEnvironmentId !== null,
        hint: "Optionnel — sans lui, seul l'auto-ombrage du bâtiment sur lui-même compte.",
        anchor: '#section-ombrage',
      },
      {
        label: 'Ombrage à jour',
        done: !this.sunVisibilityStale(),
        hint: this.sunVisibilityStale() ? 'Périmé ou jamais calculé — relancez le précalcul.' : 'À jour.',
        anchor: '#section-ombrage',
      },
    ];
  }

  // ── Sauvegarde / chargement ──────────────────────────────────────────
  save(): void {
    const name = this.buildingName.trim();
    if (!name || this.totalCount === 0) return;
    this.saving.set(true);
    this.error.set('');
    this.message.set('');

    const triangles = this.triangles().map(t => ({
      v: t.v, group: t.group, paroi_model_id: t.paroi_model_id, boundary: t.boundary ?? 'exterior_air',
      shading_profile_id: t.shading_profile_id ?? null,
    }));
    const id = this.currentBuildingId();
    const payload: Record<string, unknown> = {
      name, description: this.buildingDescription, triangles,
      environment_id: this.selectedEnvironmentId,
      georef_lat: this.georefLat, georef_lon: this.georefLon,
      georef_north_offset_deg: this.georefNorthOffset, georef_ground_z: this.georefGroundZ,
      surface_ref_m2: this.surfaceRefM2,
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

  loadBuilding(summary: { id: number }): void {
    this.loading.set(true);
    this.error.set('');
    this.message.set('');
    // Remis à zéro ICI (synchrone, avant la requête) et non dans le callback :
    // la génération d'environnement rappelle loadBuilding() puis repose ses
    // avertissements juste après — un reset asynchrone les effacerait, exactement
    // comme il efface déjà envGenJob (voir startEnvGenPoll).
    this.envGenWarnings.set([]);
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
        this.groupBoundaryAssignments = {};
        this.groupShadingAssignments = {};
        this.selectedIndices.set(new Set());
        this.modelColorMap.clear();
        this.selectedEnvironmentId = b.environment_id;
        this.georefLat = b.georef_lat;
        this.georefLon = b.georef_lon;
        this.georefNorthOffset = b.georef_north_offset_deg;
        this.georefGroundZ = b.georef_ground_z;
        this.surfaceRefM2 = b.surface_ref_m2;
        this.sunVisibilityStale.set(b.sun_visibility_stale);
        this.shadowJob.set(null);
        this.stopPoll();
        this.envGenJob.set(null);
        this.stopEnvGenPoll();
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
    this.groupBoundaryAssignments = {};
    this.groupShadingAssignments = {};
    this.selectedIndices.set(new Set());
    this.modelColorMap.clear();
    this.selectedEnvironmentId = null;
    this.georefLat = null;
    this.georefLon = null;
    this.georefNorthOffset = 0;
    this.georefGroundZ = null;
    this.surfaceRefM2 = null;
    this.sunVisibilityStale.set(true);
    this.shadowJob.set(null);
    this.stopPoll();
    this.envGenJob.set(null);
    this.envGenWarnings.set([]);
    this.stopEnvGenPoll();
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

  // ── Altitude du sol (Lot AA) ─────────────────────────────────────────
  // georef_ground_z était saisi à la main, sans aide. Laissé vide il vaut 0, et
  // TOUS les obstacles issus d'IGN se retrouvent alors à leur altitude NGF
  // absolue (100 m et plus) au-dessus d'un bâtiment posé à z = 0 : l'ombrage
  // est entièrement faux, sans le moindre signe visible.
  fetchingAltitude = signal(false);

  fetchGroundAltitude(): void {
    if (this.georefLat === null || this.georefLon === null || this.fetchingAltitude()) return;
    this.fetchingAltitude.set(true);
    this.error.set('');
    this.api.groundAltitude(this.georefLat, this.georefLon).subscribe({
      next: (res) => {
        const r = res as { altitude_m: number; source: string };
        this.georefGroundZ = r.altitude_m;
        this.fetchingAltitude.set(false);
        this.message.set(
          `Altitude du terrain : ${r.altitude_m} m (${r.source === 'ign' ? 'IGN RGE ALTI' : 'Open-Meteo'}). `
          + `Enregistrez le bâtiment pour la conserver.`,
        );
      },
      error: (err) => {
        this.fetchingAltitude.set(false);
        this.error.set(err?.error?.detail ?? "Échec de la récupération de l'altitude.");
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

  // ── Génération d'environnement pour ce bâtiment (aligné via georef_*) ────
  generateEnvironmentForBuilding(): void {
    const id = this.currentBuildingId();
    if (id === null || this.georefLat === null || this.georefLon === null) return;
    this.error.set('');
    this.message.set('');
    this.envGenWarnings.set([]);
    this.envGenerating.set(true);
    this.api.generateBuildingEnvironment(
      id, this.envGenRadius, this.envGenTerrain ? this.envGenTerrainSpacing : null,
      this.envGenVegetation,
    ).subscribe({
      next: (res) => {
        this.envGenJob.set(res as Job);
        this.startEnvGenPoll();
      },
      error: (err) => {
        this.envGenerating.set(false);
        this.error.set(err?.error?.detail ?? err?.error?.radius_m?.[0] ?? "Échec du lancement de la génération.");
      },
    });
  }

  private startEnvGenPoll(): void {
    this.stopEnvGenPoll();
    const job = this.envGenJob();
    const buildingId = this.currentBuildingId();
    if (!job || buildingId === null) return;
    this.envGenPollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.envGenJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopEnvGenPoll();
            this.envGenerating.set(false);
            if (updated.status === 'DONE') {
              const warnings = updated.result?.['warnings'];
              this.refreshEnvironments();
              // loadBuilding() remet l'état du bâtiment à plat : les
              // avertissements sont posés APRÈS, sinon ils seraient effacés.
              this.loadBuilding({ id: buildingId });
              this.message.set(updated.message);
              this.envGenWarnings.set(Array.isArray(warnings) ? warnings as string[] : []);
            } else {
              this.error.set(updated.message || 'Échec de la génération.');
            }
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopEnvGenPoll(): void {
    if (this.envGenPollHandle) {
      clearInterval(this.envGenPollHandle);
      this.envGenPollHandle = undefined;
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
