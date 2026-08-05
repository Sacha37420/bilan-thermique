import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { parseMeshFile } from '../../core/mesh-import';
import { EnvironmentMesh } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';

interface EnvironmentSummary {
  id: number;
  name: string;
  description: string;
  updated_at: string;
}

@Component({
  selector: 'app-environnement',
  standalone: true,
  imports: [FormsModule, RouterLink, MeshViewerComponent],
  templateUrl: './environnement.component.html',
  styleUrl: './environnement.component.scss',
})
export class EnvironnementComponent implements OnInit {
  private api = inject(ApiService);

  environments = signal<EnvironmentSummary[]>([]);
  currentId = signal<number | null>(null);
  name = '';
  description = '';

  vertices = signal<number[][]>([]);
  triangles = signal<{ v: [number, number, number] }[]>([]);

  loading = signal(false);
  saving = signal(false);
  error = signal('');
  message = signal('');

  colorForTriangle = (): string => '--text-mute';

  ngOnInit(): void {
    this.refresh();
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
}
