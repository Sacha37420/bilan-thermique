import * as THREE from 'three';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

export interface RawTriangle {
  v: [number, number, number];
  group: string | null;
  paroi_model_id: number | null;
}

export interface ParsedMesh {
  vertices: number[][];
  triangles: RawTriangle[];
  groups: string[];
}

class VertexWelder {
  private index = new Map<string, number>();
  vertices: number[][] = [];

  add(x: number, y: number, z: number): number {
    const key = `${x.toFixed(5)},${y.toFixed(5)},${z.toFixed(5)}`;
    let idx = this.index.get(key);
    if (idx === undefined) {
      idx = this.vertices.length;
      this.vertices.push([x, y, z]);
      this.index.set(key, idx);
    }
    return idx;
  }
}

function appendGeometry(
  geometry: THREE.BufferGeometry,
  group: string | null,
  welder: VertexWelder,
  triangles: RawTriangle[],
): void {
  const pos = geometry.getAttribute('position');
  if (!pos) return;

  const localToGlobal: number[] = new Array(pos.count);
  for (let i = 0; i < pos.count; i++) {
    localToGlobal[i] = welder.add(pos.getX(i), pos.getY(i), pos.getZ(i));
  }

  const index = geometry.getIndex();
  if (index) {
    for (let i = 0; i + 2 < index.count; i += 3) {
      triangles.push({
        v: [localToGlobal[index.getX(i)], localToGlobal[index.getX(i + 1)], localToGlobal[index.getX(i + 2)]],
        group,
        paroi_model_id: null,
      });
    }
  } else {
    for (let i = 0; i + 2 < pos.count; i += 3) {
      triangles.push({
        v: [localToGlobal[i], localToGlobal[i + 1], localToGlobal[i + 2]],
        group,
        paroi_model_id: null,
      });
    }
  }
}

function collectMeshes(object: THREE.Object3D, out: THREE.Mesh[]): void {
  if ((object as THREE.Mesh).isMesh) out.push(object as THREE.Mesh);
  for (const child of object.children) collectMeshes(child, out);
}

export function parseObjFile(text: string): ParsedMesh {
  const root = new OBJLoader().parse(text);
  const meshes: THREE.Mesh[] = [];
  collectMeshes(root, meshes);

  const welder = new VertexWelder();
  const triangles: RawTriangle[] = [];
  const groups = new Set<string>();

  meshes.forEach((mesh, i) => {
    const group = mesh.name?.trim() || `groupe_${i + 1}`;
    groups.add(group);
    appendGeometry(mesh.geometry as THREE.BufferGeometry, group, welder, triangles);
  });

  return { vertices: welder.vertices, triangles, groups: [...groups] };
}

export function parseStlFile(buffer: ArrayBuffer): ParsedMesh {
  const geometry = new STLLoader().parse(buffer);
  const welder = new VertexWelder();
  const triangles: RawTriangle[] = [];
  appendGeometry(geometry, null, welder, triangles);
  return { vertices: welder.vertices, triangles, groups: [] };
}

export function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

export function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export async function parseMeshFile(file: File): Promise<ParsedMesh> {
  const ext = file.name.split('.').pop()?.toLowerCase();
  if (ext === 'obj') {
    return parseObjFile(await readFileAsText(file));
  }
  if (ext === 'stl') {
    return parseStlFile(await readFileAsArrayBuffer(file));
  }
  throw new Error(`Format non pris en charge : .${ext ?? '?'} (attendu .obj ou .stl)`);
}
