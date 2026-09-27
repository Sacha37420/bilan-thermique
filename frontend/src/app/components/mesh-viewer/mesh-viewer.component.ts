import {
  AfterViewInit, Component, ElementRef, EventEmitter, Input, OnChanges,
  OnDestroy, Output, SimpleChanges, ViewChild,
} from '@angular/core';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

/** Le viewer n'a besoin que des indices de sommets — WorkingTriangle,
 * Triangle et SimpleTriangle (building.types.ts) le satisfont tous. */
export interface ViewerTriangle {
  v: [number, number, number];
}

function resolveColor(varNameOrHex: string): THREE.Color {
  if (varNameOrHex.startsWith('--')) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(varNameOrHex).trim();
    return new THREE.Color(value || '#888888');
  }
  return new THREE.Color(varNameOrHex);
}

/** Un environnement observé compte jusqu'à 60 000 triangles pour une poignée de
 * couleurs distinctes : résoudre chaque variable CSS une seule fois par
 * construction/repeinte (getComputedStyle par triangle coûtait des secondes).
 * Recréé à chaque passe pour suivre un changement de thème. */
function colorResolver(): (key: string) => THREE.Color {
  const cache = new Map<string, THREE.Color>();
  return (key: string) => {
    let c = cache.get(key);
    if (!c) {
      c = resolveColor(key);
      cache.set(key, c);
    }
    return c;
  };
}

/** Au-delà de ce déplacement (px) entre appui et relâchement, le geste est une
 * rotation de caméra, pas un clic de sélection. */
const CLICK_TOLERANCE_PX = 5;

@Component({
  selector: 'app-mesh-viewer',
  standalone: true,
  imports: [],
  templateUrl: './mesh-viewer.component.html',
  styleUrl: './mesh-viewer.component.scss',
})
export class MeshViewerComponent implements AfterViewInit, OnChanges, OnDestroy {
  @Input() vertices: number[][] = [];
  @Input() triangles: ViewerTriangle[] = [];
  /** Retourne une couleur (hex "#rrggbb" ou nom de variable CSS "--xxx") pour un triangle. */
  @Input() colorForTriangle: (index: number) => string = () => '--border';
  @Input() pickable = false;

  @Output() triangleClick = new EventEmitter<number>();

  @ViewChild('host', { static: true }) private hostRef!: ElementRef<HTMLDivElement>;

  private renderer?: THREE.WebGLRenderer;
  private scene?: THREE.Scene;
  private camera?: THREE.PerspectiveCamera;
  private controls?: OrbitControls;
  private mesh?: THREE.Mesh;
  private geometry?: THREE.BufferGeometry;
  private resizeObserver?: ResizeObserver;
  private raycaster = new THREE.Raycaster();
  private frameHandle = 0;

  ngAfterViewInit(): void {
    this.initScene();
    this.buildGeometry();
    this.animate();

    this.resizeObserver = new ResizeObserver(() => this.onResize());
    this.resizeObserver.observe(this.hostRef.nativeElement);

    this.hostRef.nativeElement.addEventListener('pointerdown', this.onPointerDown);
    this.hostRef.nativeElement.addEventListener('pointerup', this.onPointerUp);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.scene) return;
    if (changes['vertices'] || changes['triangles']) {
      this.buildGeometry();
    }
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.frameHandle);
    this.resizeObserver?.disconnect();
    this.hostRef.nativeElement.removeEventListener('pointerdown', this.onPointerDown);
    this.hostRef.nativeElement.removeEventListener('pointerup', this.onPointerUp);
    this.controls?.dispose();
    this.geometry?.dispose();
    (this.mesh?.material as THREE.Material | undefined)?.dispose();
    this.renderer?.dispose();
  }

  /** À appeler par le parent après un changement d'assignation/sélection (sans reconstruire la géométrie). */
  repaint(): void {
    if (!this.geometry || !this.triangles.length) return;
    const colorAttr = this.geometry.getAttribute('color') as THREE.BufferAttribute;
    const color = colorResolver();
    for (let i = 0; i < this.triangles.length; i++) {
      const c = color(this.colorForTriangle(i));
      for (let k = 0; k < 3; k++) {
        colorAttr.setXYZ(i * 3 + k, c.r, c.g, c.b);
      }
    }
    colorAttr.needsUpdate = true;
  }

  private initScene(): void {
    const host = this.hostRef.nativeElement;
    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(50, host.clientWidth / Math.max(host.clientHeight, 1), 0.01, 10000);
    this.camera.position.set(8, -12, 8);
    this.camera.up.set(0, 0, 1);

    // Toile transparente : le fond est celui de la carte (token --bg), qui suit
    // le thème clair/sombre — opaque, elle restait noire en thème clair.
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(host.clientWidth, host.clientHeight);
    host.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(10, -20, 30);
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.3);
    fill.position.set(-10, 15, -10);
    this.scene.add(fill);

    this.scene.add(new THREE.GridHelper(20, 20, 0x888888, 0xcccccc).rotateX(Math.PI / 2));
  }

  private buildGeometry(): void {
    if (!this.scene) return;
    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.geometry?.dispose();
      (this.mesh.material as THREE.Material).dispose();
      this.mesh = undefined;
    }
    if (!this.triangles.length || !this.vertices.length) return;

    const positions = new Float32Array(this.triangles.length * 9);
    const colors = new Float32Array(this.triangles.length * 9);
    const color = colorResolver();

    this.triangles.forEach((tri, i) => {
      for (let k = 0; k < 3; k++) {
        const p = this.vertices[tri.v[k]];
        positions[i * 9 + k * 3 + 0] = p[0];
        positions[i * 9 + k * 3 + 1] = p[1];
        positions[i * 9 + k * 3 + 2] = p[2];
      }
      const c = color(this.colorForTriangle(i));
      for (let k = 0; k < 3; k++) {
        colors[i * 9 + k * 3 + 0] = c.r;
        colors[i * 9 + k * 3 + 1] = c.g;
        colors[i * 9 + k * 3 + 2] = c.b;
      }
    });

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    this.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    this.geometry.computeVertexNormals();

    const material = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    this.mesh = new THREE.Mesh(this.geometry, material);
    this.scene.add(this.mesh);

    this.frameCamera();
  }

  private frameCamera(): void {
    if (!this.geometry || !this.camera || !this.controls) return;
    this.geometry.computeBoundingSphere();
    const sphere = this.geometry.boundingSphere;
    if (!sphere) return;
    const center = sphere.center;
    const radius = Math.max(sphere.radius, 0.5);
    this.controls.target.copy(center);
    // ≈ 1,9 rayon : la sphère englobante remplit la vue (champ de 50°). À 3,2 rayons
    // (valeur d'origine), un quartier entier (environnement observé) n'occupait
    // plus qu'un quart de la largeur.
    this.camera.position.copy(center).add(new THREE.Vector3(radius * 0.95, -radius * 1.3, radius * 1.0));
    this.camera.near = radius / 100;
    this.camera.far = radius * 100;
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  private onResize(): void {
    if (!this.renderer || !this.camera) return;
    const host = this.hostRef.nativeElement;
    const w = Math.max(host.clientWidth, 1);
    const h = Math.max(host.clientHeight, 1);
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private downAt: { x: number; y: number } | null = null;

  private onPointerDown = (evt: PointerEvent): void => {
    this.downAt = { x: evt.clientX, y: evt.clientY };
  };

  /** Sélection au RELÂCHEMENT, et seulement si le pointeur n'a presque pas
   * bougé : auparavant, commencer à faire tourner la vue sélectionnait déjà le
   * triangle sous le pointeur. */
  private onPointerUp = (evt: PointerEvent): void => {
    const down = this.downAt;
    this.downAt = null;
    if (!down || Math.hypot(evt.clientX - down.x, evt.clientY - down.y) > CLICK_TOLERANCE_PX) return;
    if (!this.pickable || !this.mesh || !this.camera) return;
    const rect = this.hostRef.nativeElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((evt.clientX - rect.left) / rect.width) * 2 - 1,
      -((evt.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObject(this.mesh, false);
    if (hits.length > 0 && hits[0].faceIndex !== undefined && hits[0].faceIndex !== null) {
      this.triangleClick.emit(hits[0].faceIndex);
    }
  };

  private animate = (): void => {
    this.frameHandle = requestAnimationFrame(this.animate);
    this.controls?.update();
    if (this.renderer && this.scene && this.camera) {
      this.renderer.render(this.scene, this.camera);
    }
  };
}
