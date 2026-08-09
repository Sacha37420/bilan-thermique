import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { parseMeshFile } from '../../core/mesh-import';
import { EnvironmentMesh, Job } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';

interface EnvironmentSummary {
  id: number;
  name: string;
  description: string;
  updated_at: string;
}

interface GenerateEnvironmentResult {
  vertices: number[][];
  triangles: { v: [number, number, number]; k?: number | null }[];
  warnings: string[];
  stats: { buildings_used: number; buildings_ign: number; buildings_osm: number; buildings_skipped: number };
}

const POLL_INTERVAL_MS = 2000;

@Component({
  selector: 'app-environnement',
  standalone: true,
  imports: [FormsModule, RouterLink, MeshViewerComponent],
  templateUrl: './environnement.component.html',
  styleUrl: './environnement.component.scss',
})
export class EnvironnementComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  environments = signal<EnvironmentSummary[]>([]);
  currentId = signal<number | null>(null);
  name = '';
  description = '';

  vertices = signal<number[][]>([]);
  // `k` (transmittance, Lot Z) est conservé sur chaque triangle : c'est ce qui
  // distingue un obstacle végétal d'un bâtiment, à l'affichage comme au calcul.
  triangles = signal<{ v: [number, number, number]; k?: number | null }[]>([]);

  loading = signal(false);
  saving = signal(false);
  error = signal('');
  message = signal('');

  genLat = 48.8566;
  genLon = 2.3522;
  genRadius = 150;
  // Lot Z : manquait sur cette page à la livraison du lot — la génération
  // autonome ne produisait que des bâtiments.
  genVegetation = false;
  generating = signal(false);
  generateJob = signal<Job | null>(null);
  private pollHandle?: ReturnType<typeof setInterval>;

  // Un maillage d'environnement peut mélanger bâtiments (opaques) et végétation
  // (atténuante). Les afficher de la même couleur les rendait indiscernables —
  // signalé à l'usage : « je ne repère pas de végétation ».
  colorForTriangle = (index: number): string => {
    const k = this.triangles()[index]?.k;
    return k !== null && k !== undefined && k > 0 ? '--success' : '--text-mute';
  };

  get vegetationTriangleCount(): number {
    return this.triangles().filter(t => t.k !== null && t.k !== undefined && t.k > 0).length;
  }

  ngOnInit(): void {
    this.refresh();
  }

  ngOnDestroy(): void {
    this.stopGeneratePoll();
  }

  private refresh(): void {
    this.api.getEnvironments().subscribe({
      next: (envs) => this.environments.set(envs as EnvironmentSummary[]),
      error: () => {},
    });
  }

  async onFileSelected(evt: Event): Promise<void> {
    const input = evt.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;

    this.error.set('');
    this.message.set('');
    try {
      const parsed = await parseMeshFile(file);
      this.currentId.set(null);
      this.name = file.name.replace(/\.(obj|stl)$/i, '');
      this.description = '';
      this.vertices.set(parsed.vertices);
      this.triangles.set(parsed.triangles.map(t => ({ v: t.v })));
      this.message.set(`Fichier chargé : ${parsed.vertices.length} sommets, ${parsed.triangles.length} triangles.`);
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : "Échec de la lecture du fichier.");
    }
  }

  save(): void {
    const name = this.name.trim();
    if (!name || this.triangles().length === 0) return;
    this.saving.set(true);
    this.error.set('');
    this.message.set('');

    const id = this.currentId();
    const payload: Record<string, unknown> = { name, description: this.description, triangles: this.triangles() };
    if (id === null || this.vertices().length > 0) {
      payload['vertices'] = this.vertices();
    }

    const req = id === null ? this.api.createEnvironment(payload) : this.api.updateEnvironment(id, payload);
    req.subscribe({
      next: (res) => {
        const e = res as EnvironmentMesh;
        this.currentId.set(e.id);
        this.vertices.set(e.envelope.vertices);
        this.triangles.set(e.envelope.triangles);
        this.saving.set(false);
        this.message.set(`Environnement « ${e.name} » enregistré.`);
        this.refresh();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(err?.error?.name?.[0] ?? "Échec de l'enregistrement.");
      },
    });
  }

  load(summary: EnvironmentSummary): void {
    this.loading.set(true);
    this.error.set('');
    this.message.set('');
    this.api.getEnvironment(summary.id).subscribe({
      next: (res) => {
        const e = res as EnvironmentMesh;
        this.currentId.set(e.id);
        this.name = e.name;
        this.description = e.description;
        this.vertices.set(e.envelope.vertices);
        this.triangles.set(e.envelope.triangles);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Impossible de charger cet environnement.');
      },
    });
  }

  newEnvironment(): void {
    this.currentId.set(null);
    this.name = '';
    this.description = '';
    this.vertices.set([]);
    this.triangles.set([]);
    this.error.set('');
    this.message.set('');
  }

  remove(summary: EnvironmentSummary): void {
    if (!confirm(`Supprimer l'environnement « ${summary.name} » ?`)) return;
    this.api.deleteEnvironment(summary.id).subscribe({
      next: () => {
        if (this.currentId() === summary.id) this.newEnvironment();
        this.refresh();
      },
      error: () => this.error.set('Échec de la suppression (peut-être encore lié à un bâtiment).'),
    });
  }

  // ── Génération automatique depuis IGN (BD TOPO) / OpenStreetMap ──────────────
  generateFromCoordinates(): void {
    this.error.set('');
    this.message.set('');
    this.generating.set(true);
    this.generateJob.set(null);

    this.api.generateEnvironment({
      lat: this.genLat, lon: this.genLon, radius_m: this.genRadius,
      include_vegetation: this.genVegetation,
    }).subscribe({
      next: (res) => {
        this.generateJob.set(res as Job);
        this.startGeneratePoll();
      },
      error: (err) => {
        this.generating.set(false);
        this.error.set(
          err?.error?.radius_m?.[0] ?? err?.error?.lat?.[0] ?? err?.error?.lon?.[0]
          ?? err?.error?.detail ?? "Échec du lancement de la génération.",
        );
      },
    });
  }

  private startGeneratePoll(): void {
    this.stopGeneratePoll();
    const job = this.generateJob();
    if (!job) return;
    this.pollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.generateJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopGeneratePoll();
            this.generating.set(false);
            if (updated.status === 'DONE') {
              this.applyGeneratedResult(updated.result as unknown as GenerateEnvironmentResult);
            } else {
              this.error.set(updated.message || 'Échec de la génération.');
            }
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopGeneratePoll(): void {
    if (this.pollHandle) {
      clearInterval(this.pollHandle);
      this.pollHandle = undefined;
    }
  }

  private applyGeneratedResult(result: GenerateEnvironmentResult): void {
    this.currentId.set(null);
    this.name = `Environnement (${this.genLat.toFixed(4)}, ${this.genLon.toFixed(4)})`;
    this.description = '';
    this.vertices.set(result.vertices);
    this.triangles.set(result.triangles);

    const stats = result.stats;
    const parts = [`${stats.buildings_used} bâtiment(s)`, `${result.triangles.length} triangles`];
    if (stats.buildings_ign > 0) parts.push(`IGN : ${stats.buildings_ign}`);
    if (stats.buildings_osm > 0) parts.push(`OSM : ${stats.buildings_osm}`);
    let msg = `Généré — ${parts.join(', ')}.`;
    if (result.warnings.length > 0) msg += ' ' + result.warnings.join(' ');
    this.message.set(msg);
  }
}
