import {
  Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, ViewChild, inject, signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe, KeyValuePipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { Building, FacadeEntry, Job } from '../../core/building.types';
import { MeshViewerComponent, ViewerTexture } from '../mesh-viewer/mesh-viewer.component';

interface ParoiModelSummary { id: number; name: string; is_glazing: boolean }

/**
 * Lot AK — façades du bâtiment d'après les photos de rue Panoramax : analyse
 * (prise de vue recalée, texture redressée, baies détectées), vérification
 * dans la vue 3D (texture ou couleurs : vitrages, portes, murs), puis
 * intégration des VRAIS vitrages dans le maillage. Partagé par le mode
 * simplifié et la page Bâtiment ; émet le bâtiment mis à jour.
 */
@Component({
  selector: 'app-facade-panel',
  standalone: true,
  imports: [FormsModule, DecimalPipe, KeyValuePipe, MeshViewerComponent],
  templateUrl: './facade-panel.component.html',
  styleUrl: './facade-panel.component.scss',
})
export class FacadePanelComponent implements OnChanges, OnDestroy {
  private api = inject(ApiService);

  @Input({ required: true }) building!: Building;
  @Input() paroiModels: ParoiModelSummary[] = [];
  @Output() buildingChange = new EventEmitter<Building>();

  @ViewChild(MeshViewerComponent) viewer?: MeshViewerComponent;

  showTexture = true;
  textures = signal<ViewerTexture[]>([]);
  private textureGroups: string[] = [];
  private objectUrls: string[] = [];
  private loadedFor = '';

  busy = signal(false);
  status = signal('');
  error = signal('');
  report = signal<Record<string, { source: string; n_openings: number; glazed_ratio: number }> | null>(null);

  glazingModelId: number | null = null;
  wallModelId: number | null = null;
  fallbackPct = 20;
  useDetection: Record<string, boolean> = {};

  get facades(): FacadeEntry[] {
    const f = this.building?.facades?.facades ?? {};
    return Object.values(f).sort((a, b) => this.num(a.group) - this.num(b.group));
  }

  get glazingModels(): ParoiModelSummary[] {
    return this.paroiModels.filter(m => m.is_glazing);
  }

  get opaqueModels(): ParoiModelSummary[] {
    return this.paroiModels.filter(m => !m.is_glazing);
  }

  get analysed(): boolean {
    return this.facades.length > 0;
  }

  get hasGlazing(): boolean {
    return (this.building?.envelope?.triangles ?? []).some(t => (t.group ?? '').startsWith('vitrage_'));
  }

  get producers(): string {
    return [...new Set(this.facades.map(f => f.pano?.producer).filter((p): p is string => !!p))].join(', ');
  }

  private num(group: string): number {
    return Number(group.split('_')[1]) || 0;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['building'] && !changes['paroiModels']) return;
    if (this.glazingModelId === null) {
      this.glazingModelId = (this.glazingModels.find(m => /double/i.test(m.name)) ?? this.glazingModels[0])?.id ?? null;
    }
    for (const f of this.facades) {
      if (!(f.group in this.useDetection)) {
        // Vue sur photo : la détection fait foi, même sans baie (pignon aveugle).
        this.useDetection[f.group] = f.status === 'analysee';
      }
    }
    this.report.set(this.building?.facades?.applied?.report ?? null);
    this.loadTextures();
    this.viewer?.repaint();
  }

  ngOnDestroy(): void {
    this.objectUrls.forEach(u => URL.revokeObjectURL(u));
  }

  // ── Textures ───────────────────────────────────────────────────────────
  private loadTextures(): void {
    const withTex = this.facades.filter(f => f.texture_key);
    const key = `${this.building?.id}:${withTex.map(f => f.group + f.texture_key).join(',')}`;
    if (key === this.loadedFor) return;
    this.loadedFor = key;
    this.objectUrls.forEach(u => URL.revokeObjectURL(u));
    this.objectUrls = [];
    this.textures.set([]);
    this.textureGroups = [];
    const loaded: { group: string; tex: ViewerTexture }[] = [];
    let pending = withTex.length;
    for (const f of withTex) {
      this.api.getFacadeTexture(this.building.id, f.group).subscribe({
        next: (blob) => {
          const url = URL.createObjectURL(blob);
          this.objectUrls.push(url);
          const o = f.plane.origin, u = f.plane.u, W = f.plane.width, H = f.plane.height;
          loaded.push({
            group: f.group,
            tex: {
              url,
              uv: (p) => [((p[0] - o[0]) * u[0] + (p[1] - o[1]) * u[1]) / W, (p[2] - o[2]) / H],
            },
          });
          if (--pending === 0) this.publish(loaded);
        },
        error: () => { if (--pending === 0) this.publish(loaded); },
      });
    }
  }

  private publish(loaded: { group: string; tex: ViewerTexture }[]): void {
    this.textureGroups = loaded.map(l => l.group);
    this.textures.set(loaded.map(l => l.tex));
  }

  /** Façade d'un triangle : mur_k, vitrage_k et porte_k appartiennent à la
   * façade mur_k (les murs mitoyens n'en ont pas). */
  private facadeOf(index: number): string | null {
    const g = this.building?.envelope?.triangles?.[index]?.group ?? '';
    const m = /^(mur|vitrage|porte)_(\d+)$/.exec(g);
    return m ? `mur_${m[2]}` : null;
  }

  textureIndexFor = (index: number): number => {
    if (!this.showTexture) return -1;
    const f = this.facadeOf(index);
    return f ? this.textureGroups.indexOf(f) : -1;
  };

  tintFor = (_index: number): string | null => null;

  colorForTriangle = (index: number): string => {
    const g = this.building?.envelope?.triangles?.[index]?.group ?? '';
    if (g.startsWith('vitrage_')) return '--accent';
    if (g.startsWith('porte_')) return '--warning';
    if (g.startsWith('mur_')) return g.endsWith('_mitoyen') ? '--border' : '--mat-enduit';
    if (g.startsWith('toiture')) return '--mat-inconnu';
    return '--border';
  };

  toggleTexture(): void {
    // Le paquet de triangles texturés change : reconstruction de la géométrie.
    this.textures.set([...this.textures()]);
  }

  statusLabel(f: FacadeEntry): string {
    return f.status === 'analysee' ? 'vue sur photo' : f.status === 'masquee' ? 'masquée (végétation)' : 'aucune photo';
  }

  count(f: FacadeEntry, label: 'window' | 'door'): number {
    return f.openings.filter(o => o.label === label).length;
  }

  detectedRatio(f: FacadeEntry): number {
    const glass = f.openings.filter(o => o.label === 'window')
      .reduce((a, o) => a + (o.s1 - o.s0) * (o.t1 - o.t0), 0);
    return f.area > 0 ? glass / f.area : 0;
  }

  // ── Actions ────────────────────────────────────────────────────────────
  analyse(): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.status.set('Lancement de l’analyse…');
    this.api.analyseFacades(this.building.id).subscribe({
      next: (res) => this.poll((res as Job).id),
      error: (err) => { this.busy.set(false); this.error.set(err?.error?.detail ?? 'Échec du lancement.'); },
    });
  }

  private poll(jobId: number): void {
    const handle = setInterval(() => {
      this.api.getJob(jobId).subscribe({
        next: (res) => {
          const job = res as Job;
          this.status.set(job.message || `${job.progress}%`);
          if (job.status === 'DONE' || job.status === 'ERROR') {
            clearInterval(handle);
            if (job.status === 'ERROR') {
              this.busy.set(false);
              this.error.set(job.message || 'Échec de l’analyse.');
              return;
            }
            this.api.getBuilding(this.building.id).subscribe({
              next: (b) => { this.busy.set(false); this.buildingChange.emit(b as Building); },
              error: () => { this.busy.set(false); this.error.set('Rechargement impossible.'); },
            });
          }
        },
        error: () => { clearInterval(handle); this.busy.set(false); this.error.set('Suivi interrompu.'); },
      });
    }, 3000);
  }

  apply(): void {
    if (this.busy() || this.glazingModelId === null) return;
    this.busy.set(true);
    this.error.set('');
    this.api.applyFacades(this.building.id, {
      glazing_model_id: this.glazingModelId, wall_model_id: this.wallModelId,
      fallback_ratio: this.fallbackPct / 100, use_detection: this.useDetection,
    }).subscribe({
      next: (res) => {
        const r = res as { building: Building };
        this.busy.set(false);
        this.status.set('Vitrages intégrés au maillage.');
        this.buildingChange.emit(r.building);
      },
      error: (err) => { this.busy.set(false); this.error.set(err?.error?.detail ?? 'Échec de l’intégration.'); },
    });
  }
}
