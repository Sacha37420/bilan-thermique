import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { LayerInput, LayersEditorComponent, defaultLayer } from '../../components/layers-editor/layers-editor.component';

interface ParoiModel {
  id: number;
  name: string;
  description: string;
  layers: LayerInput[];
  frame_u: number | null;
  frame_fraction: number | null;
  created_at: string;
  updated_at: string;
}

interface EditingModel {
  id: number | null;
  name: string;
  description: string;
  layers: LayerInput[];
  // Cadre de fenêtre (Lot I, optionnel) — les deux vont ensemble ou pas du tout
  // (voir ParoiModelSerializer.validate) ; hasFrame pilote juste l'affichage,
  // c'est frame_u/frame_fraction (null si !hasFrame) qui partent au serveur.
  hasFrame: boolean;
  frame_u: number;
  frame_fraction: number;
}

@Component({
  selector: 'app-parois',
  standalone: true,
  imports: [FormsModule, DatePipe, DecimalPipe, RouterLink, LayersEditorComponent],
  templateUrl: './parois.component.html',
  styleUrl: './parois.component.scss',
})
export class ParoisComponent implements OnInit {
  private api = inject(ApiService);

  models = signal<ParoiModel[]>([]);
  loading = signal(false);
  error = signal('');
  editing = signal<EditingModel | null>(null);
  saving = signal(false);

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.api.getParoiModeles().subscribe({
      next: (models) => {
        this.models.set(models as ParoiModel[]);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Impossible de charger la bibliothèque.');
        this.loading.set(false);
      },
    });
  }

  startCreate(): void {
    this.editing.set({
      id: null, name: '', description: '', layers: [defaultLayer()],
      hasFrame: false, frame_u: 2.0, frame_fraction: 0.25,
    });
  }

  startEdit(m: ParoiModel): void {
    this.editing.set({
      id: m.id,
      name: m.name,
      description: m.description,
      layers: m.layers.map(l => ({ ...l })),
      hasFrame: m.frame_u !== null && m.frame_fraction !== null,
      frame_u: m.frame_u ?? 2.0,
      frame_fraction: m.frame_fraction ?? 0.25,
    });
  }

  cancelEdit(): void {
    this.editing.set(null);
  }

  setEditingLayers(layers: LayerInput[]): void {
    const e = this.editing();
    if (e) this.editing.set({ ...e, layers });
  }

  save(): void {
    const e = this.editing();
    if (!e || !e.name.trim()) return;
    this.saving.set(true);
    this.error.set('');
    const payload = {
      name: e.name.trim(), description: e.description, layers: e.layers,
      frame_u: e.hasFrame ? e.frame_u : null,
      frame_fraction: e.hasFrame ? e.frame_fraction : null,
    };
    const req = e.id === null
      ? this.api.createParoiModele(payload)
      : this.api.updateParoiModele(e.id, payload);

    req.subscribe({
      next: () => {
        this.saving.set(false);
        this.editing.set(null);
        this.refresh();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(err?.error?.name?.[0] ?? "Échec de l'enregistrement.");
      },
    });
  }

  remove(m: ParoiModel): void {
    if (!confirm(`Supprimer le modèle « ${m.name} » ?`)) return;
    this.api.deleteParoiModele(m.id).subscribe({
      next: () => this.refresh(),
      error: () => this.error.set('Échec de la suppression.'),
    });
  }
}
