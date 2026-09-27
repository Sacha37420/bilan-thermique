export type TriangleBoundary = 'exterior_air' | 'ground';

// Lot J : dispositif d'occultation mobile éventuellement installé sur ce
// triangle, toujours considéré ENTIÈREMENT fermé quand actif (voir
// core/shading-profiles.ts pour le catalogue affiché ; les valeurs physiques
// elles-mêmes vivent uniquement côté backend, building_solver.SHADING_PROFILES).
export type ShadingProfileId = 'volet-roulant' | 'store-exterieur';

export interface Triangle {
  v: [number, number, number];
  group: string | null;
  paroi_model_id: number | null;
  boundary: TriangleBoundary;
  shading_profile_id: ShadingProfileId | null;
  area: number;
  normal: [number, number, number];
  tilt_deg: number;
  azimuth_deg: number;
}

/** Triangle en cours d'édition côté client : la géométrie (area/normal/...)
 * n'existe que pour un triangle déjà passé par le serveur (import initial ou
 * rechargement) — jamais recalculée localement. */
export interface WorkingTriangle {
  v: [number, number, number];
  group: string | null;
  paroi_model_id: number | null;
  boundary?: TriangleBoundary;
  shading_profile_id?: ShadingProfileId | null;
  area?: number;
  normal?: [number, number, number];
  tilt_deg?: number;
  azimuth_deg?: number;
}

/** Candidat renvoyé par POST /api/batiments/rechercher/ : un bâtiment réel
 * (IGN BD TOPO / OpenStreetMap) DÉJÀ extrudé en enveloppe groupée. Partagé
 * (Lot Y) entre le mode simplifié et la page Bâtiment via
 * components/building-search. */
export interface BuildingCandidate {
  lat: number;
  lon: number;
  distance_m: number;
  height_m: number;
  approx_height: boolean;
  source: 'ign' | 'osm';
  n_walls: number;
  vertices: number[][];
  triangles: { v: [number, number, number]; group: string; boundary: TriangleBoundary }[];
}

export interface Envelope {
  vertices: number[][];
  triangles: Triangle[];
}

export interface Building {
  id: number;
  name: string;
  description: string;
  envelope: Envelope;
  environment_id: number | null;
  georef_lat: number | null;
  georef_lon: number | null;
  georef_north_offset_deg: number;
  georef_ground_z: number | null;
  surface_ref_m2: number | null;
  suggested_debit_vent_m3h: number | null;
  suggested_eta_recup_vent: number | null;
  sun_visibility_stale: boolean;
  created_at: string;
  updated_at: string;
}

export interface SimpleTriangle {
  v: [number, number, number];
}

export interface SimpleEnvelope {
  vertices: number[][];
  triangles: SimpleTriangle[];
}

/** Lot AH — objet d'un environnement généré : bâtiment, arbre/massif ou
 * terrain, avec son propre maillage (repère de l'environnement). */
export type EnvObjectKind = 'building' | 'vegetation' | 'terrain';
export type EnvObjectStatus = 'active' | 'removed' | 'studied';

export interface EnvironmentObject {
  id: number;
  kind: EnvObjectKind;
  status: EnvObjectStatus;
  /** 'bdtopo+lidar' | 'bdtopo' | 'lidar' | 'osm' | … */
  origin: string;
  label: string;
  reason: string | null;
  building_id: number | null;
  info: Record<string, unknown>;
  footprint: number[][][] | null;
  vertices: number[][];
  triangles: { v: [number, number, number]; group?: string; boundary?: string }[];
  k: number | null;
}

export interface EnvironmentGeneration {
  source?: 'lidar' | 'legacy';
  radius_m?: number;
  lidar?: { tiles?: string[]; acquisition?: string[]; classification?: string[] };
  stats?: Record<string, unknown>;
  warnings?: string[];
  generated_at?: string;
}

export interface EnvironmentMesh {
  id: number;
  name: string;
  description: string;
  envelope: SimpleEnvelope;
  georef_lat: number | null;
  georef_lon: number | null;
  georef_north_offset_deg: number;
  georef_ground_z: number | null;
  objects: EnvironmentObject[];
  generation: EnvironmentGeneration;
  /** id d'objet → bâtiment étudié qui en est issu (null s'il a été supprimé). */
  studied_buildings: Record<string, { id: number; name: string } | null>;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: number;
  kind: string;
  status: 'PENDING' | 'RUNNING' | 'DONE' | 'ERROR';
  progress: number;
  message: string;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
