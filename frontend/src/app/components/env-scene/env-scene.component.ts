import {
  Component, EventEmitter, Input, OnChanges, OnDestroy, Output, SimpleChanges, ViewChild, inject, signal,
} from '@angular/core';
import { ApiService } from '../../core/api.service';
import { EnvironmentMesh, EnvironmentObject } from '../../core/building.types';
import { MeshViewerComponent, ViewerTexture } from '../mesh-viewer/mesh-viewer.component';

type SceneTriangle = { v: [number, number, number] };

/** Teinte de façade selon le PREMIER matériau des murs (code fichiers fonciers). */
const WALL_TINTS: Record<string, string> = {
  '1': '--mat-pierre', '2': '--mat-pierre', '3': '--mat-beton', '4': '--mat-brique',
  '5': '--mat-enduit', '6': '--mat-bois',
};

/**
 * Lot AI — vue 3D d'un environnement décomposé en objets, partagée par la
 * page Environnement et le mode simplifié : orthophoto IGN drapée sur les
 * toitures et le terrain, façades teintées selon leur matériau BD TOPO,
 * arbres en vert, objets retirés en pâle, bâtiment étudié et sélection en
 * surbrillance. Émet l'objet cliqué ; la politique de sélection (simple ou
 * multiple) appartient à la page.
 */
@Component({
  selector: 'app-env-scene',
  standalone: true,
  imports: [MeshViewerComponent],
  template: `
    <app-mesh-viewer
      [vertices]="vertices"
      [triangles]="triangles"
      [colorForTriangle]="colorForTriangle"
      [texture]="texture()"
      [texturedTriangle]="texturedTriangle"
      [tintForTexturedTriangle]="tintForTexturedTriangle"
      [pickable]="pickable"
      (triangleClick)="onTriangleClick($event)"
    />
  `,
  styles: [':host { display: block; width: 100%; height: 100%; }'],
})
export class EnvSceneComponent implements OnChanges, OnDestroy {
  private api = inject(ApiService);

  @Input() env: EnvironmentMesh | null = null;
  @Input() selectedIds: number[] = [];
  @Input() pickable = true;
  /** Masquer l'orthophoto (plus lisible pour certains usages). */
  @Input() showTexture = true;

  @Output() objectClick = new EventEmitter<EnvironmentObject>();

  @ViewChild(MeshViewerComponent) viewer?: MeshViewerComponent;

  vertices: number[][] = [];
  triangles: SceneTriangle[] = [];
  // Signal : l'orthophoto arrive dans un callback HTTP, et l'app est sans
  // zone.js — une simple affectation n'y déclencherait aucun rendu.
  texture = signal<ViewerTexture | null>(null);
  private owner = new Int32Array(0);
  private isRoof = new Uint8Array(0);
  private signature = '';
  private objectUrl: string | null = null;
  private textureEnvId: number | null = null;

  ngOnChanges(changes: SimpleChanges): void {
    const e = this.env;
    const sig = e ? `${e.id}:${e.objects?.length ?? 0}:${e.envelope?.triangles?.length ?? 0}` : '';
    if (changes['env'] && sig !== this.signature) {
      this.signature = sig;
      this.rebuild();
    } else {
      this.viewer?.repaint();
    }
    if (changes['env'] || changes['showTexture']) this.refreshTexture();
  }

  ngOnDestroy(): void {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
  }

  repaint(): void {
    this.viewer?.repaint();
  }

  private rebuild(): void {
    const e = this.env;
    const vertices: number[][] = [];
    const triangles: SceneTriangle[] = [];
    const owner: number[] = [];
    const roof: number[] = [];
    if (e?.objects?.length) {
      e.objects.forEach((obj, index) => {
        const offset = vertices.length;
        for (const v of obj.vertices) vertices.push(v);
        for (const t of obj.triangles) {
          triangles.push({ v: [t.v[0] + offset, t.v[1] + offset, t.v[2] + offset] });
          owner.push(index);
          roof.push(obj.kind === 'terrain' || (obj.kind === 'building' && (t.group ?? '').startsWith('toiture')) ? 1 : 0);
        }
      });
    } else if (e) {
      for (const v of e.envelope.vertices) vertices.push(v);
      for (const t of e.envelope.triangles) {
        triangles.push({ v: t.v });
        owner.push(-1);
        roof.push(0);
      }
    }
    this.owner = Int32Array.from(owner);
    this.isRoof = Uint8Array.from(roof);
    this.vertices = vertices;
    this.triangles = triangles;
  }

  private refreshTexture(): void {
    const e = this.env;
    if (!e || !e.ortho || !this.showTexture) {
      this.clearTexture();
      return;
    }
    if (this.textureEnvId === e.id && this.texture()) return;
    const ortho = e.ortho;
    this.textureEnvId = e.id;
    this.api.getEnvironmentOrthophoto(e.id).subscribe({
      next: (blob) => {
        if (this.env?.id !== e.id) return;
        if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
        this.objectUrl = URL.createObjectURL(blob);
        this.texture.set({
          url: this.objectUrl,
          uv: (x, y) => [ortho.u[0] * x + ortho.u[1] * y + ortho.u[2], ortho.v[0] * x + ortho.v[1] * y + ortho.v[2]],
        });
      },
      // Sans orthophoto, la scène reste lisible en couleurs unies.
      error: () => { this.textureEnvId = null; },
    });
  }

  private clearTexture(): void {
    this.texture.set(null);
    this.textureEnvId = null;
  }

  private objectOf(index: number): EnvironmentObject | null {
    const i = this.owner[index];
    return i >= 0 ? this.env?.objects[i] ?? null : null;
  }

  private statusTint(obj: EnvironmentObject): string | null {
    if (this.selectedIds.includes(obj.id)) return '--danger';
    if (obj.status === 'studied') return '--accent';
    if (obj.status === 'removed') return '--border';
    return null;
  }

  texturedTriangle = (index: number): boolean => this.isRoof[index] === 1;

  tintForTexturedTriangle = (index: number): string | null => {
    const obj = this.objectOf(index);
    return obj ? this.statusTint(obj) : null;
  };

  colorForTriangle = (index: number): string => {
    const obj = this.objectOf(index);
    if (!obj) return '--text-mute';
    const tint = this.statusTint(obj);
    if (tint) return tint;
    if (obj.kind === 'vegetation') return '--success';
    if (obj.kind === 'terrain') return '--success-tint';
    const code = String(obj.info?.['mat_murs'] ?? '').trim();
    const first = [...code].find(ch => ch !== '0');
    return (first && WALL_TINTS[first]) || (obj.origin === 'lidar' ? '--mat-enduit' : '--mat-inconnu');
  };

  onTriangleClick(index: number): void {
    const obj = this.objectOf(index);
    if (obj) this.objectClick.emit(obj);
  }
}
