import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

interface EnvWindow {
  __env?: { apiUrl?: string };
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  private get base(): string {
    return (window as unknown as EnvWindow).__env?.apiUrl
      ?? 'http://localhost:8099';
  }

  getMe(): Observable<unknown> {
    return this.http.get(`${this.base}/api/me/`);
  }

  getDepartments(): Observable<unknown[]> {
    return this.http.get<unknown[]>(`${this.base}/api/departments/`);
  }

  getUsers(): Observable<unknown[]> {
    return this.http.get<unknown[]>(`${this.base}/api/users/`);
  }

  runCalcul1D(payload: unknown): Observable<unknown> {
    return this.http.post(`${this.base}/api/calcul-1d/`, payload);
  }

  getParoiModeles(): Observable<unknown[]> {
    return this.http.get<unknown[]>(`${this.base}/api/paroi-modeles/`);
  }

  createParoiModele(payload: unknown): Observable<unknown> {
    return this.http.post(`${this.base}/api/paroi-modeles/`, payload);
  }

  updateParoiModele(id: number, payload: unknown): Observable<unknown> {
    return this.http.patch(`${this.base}/api/paroi-modeles/${id}/`, payload);
  }

  deleteParoiModele(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/paroi-modeles/${id}/`);
  }

  getBuildings(): Observable<unknown[]> {
    return this.http.get<unknown[]>(`${this.base}/api/batiments/`);
  }

  getBuilding(id: number): Observable<unknown> {
    return this.http.get(`${this.base}/api/batiments/${id}/`);
  }

  createBuilding(payload: unknown): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/`, payload);
  }

  updateBuilding(id: number, payload: unknown): Observable<unknown> {
    return this.http.patch(`${this.base}/api/batiments/${id}/`, payload);
  }

  deleteBuilding(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/batiments/${id}/`);
  }

  refineBuildingMesh(buildingId: number, maxEdgeLength: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/${buildingId}/affiner-maillage/`, { max_edge_length: maxEdgeLength });
  }

  searchNearbyBuildings(
    payload: { lat: number; lon: number; radius_m?: number; max_walls?: number | null },
  ): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/rechercher/`, payload);
  }

  precomputeShadows(buildingId: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/${buildingId}/precalcul-ombrage/`, {});
  }

  /** Lot AA — altitude du terrain en un point (IGN RGE ALTI, repli mondial
   * Open-Meteo). Synchrone : un point, un appel. */
  groundAltitude(lat: number, lon: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/altitude/`, { lat, lon });
  }

  getJob(id: number): Observable<unknown> {
    return this.http.get(`${this.base}/api/jobs/${id}/`);
  }

  getEnvironments(): Observable<unknown[]> {
    return this.http.get<unknown[]>(`${this.base}/api/environnements/`);
  }

  getEnvironment(id: number): Observable<unknown> {
    return this.http.get(`${this.base}/api/environnements/${id}/`);
  }

  createEnvironment(payload: unknown): Observable<unknown> {
    return this.http.post(`${this.base}/api/environnements/`, payload);
  }

  updateEnvironment(id: number, payload: unknown): Observable<unknown> {
    return this.http.patch(`${this.base}/api/environnements/${id}/`, payload);
  }

  deleteEnvironment(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/environnements/${id}/`);
  }

  /** Générateur d'environnement UNIQUE (Lot AD, refondu au Lot AH) : LiDAR HD
   * IGN × BD TOPO, repli BD TOPO / OpenStreetMap hors couverture. Le job
   * ENREGISTRE l'environnement : `job.result.environment_id`. `building_id` :
   * bâtiment de référence optionnel — son repère est repris et l'objet qui lui
   * correspond est marqué « bâtiment étudié » au lieu d'être un obstacle. */
  generateEnvironment(payload: {
    lat: number; lon: number; radius_m: number;
    include_vegetation?: boolean; include_terrain?: boolean;
    terrain_spacing_m?: number | null; building_id?: number | null; name?: string;
  }): Observable<unknown> {
    return this.http.post(`${this.base}/api/environnements/generer/`, payload);
  }

  /** Lot AH — retirer / restaurer des objets d'un environnement. */
  setEnvironmentObjectsStatus(envId: number, ids: number[], status: 'active' | 'removed'): Observable<unknown> {
    return this.http.patch(`${this.base}/api/environnements/${envId}/objets/`, { ids, status });
  }

  /** Lot AH/AI — un bâtiment de l'environnement (ou plusieurs emprises qui n'en
   * forment qu'un : enveloppe fusionnée) devient LE bâtiment étudié, parois
   * pré-assignées d'après la BD TOPO. */
  studyEnvironmentObjects(envId: number, ids: number[], name: string): Observable<unknown> {
    return this.http.post(`${this.base}/api/environnements/${envId}/etudier/`, { ids, name });
  }

  /** Lot AI — orthophoto IGN de l'environnement, pour la texture de la vue 3D. */
  getEnvironmentOrthophoto(envId: number): Observable<Blob> {
    return this.http.get(`${this.base}/api/environnements/${envId}/orthophoto/`, { responseType: 'blob' });
  }

  /** Lot AH — remplace un bâtiment de l'environnement par un modèle importé,
   * placé automatiquement sur son emprise. */
  replaceEnvironmentObject(envId: number, objId: number, payload: {
    vertices: number[][]; triangles: { v: [number, number, number]; group: string | null }[];
    name?: string; up_axis?: 'auto' | 'z' | 'y'; scale?: 'auto' | '1' | '0.01' | '0.001' | '0.0254';
  }): Observable<unknown> {
    return this.http.post(`${this.base}/api/environnements/${envId}/objets/${objId}/remplacer/`, payload);
  }

  runBuildingCalcul(buildingId: number, payload: unknown): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/${buildingId}/calcul-3d/`, payload);
  }

  fetchWeather(payload: {
    lat: number; lon: number; source?: 'archive' | 'tmy';
    start_date: string; end_date: string; north_offset_deg?: number;
    // Lot AB4 : null/absent = détection automatique du fuseau côté serveur.
    utc_offset_h?: number | null;
  }): Observable<unknown> {
    return this.http.post(`${this.base}/api/meteo/recuperer/`, payload);
  }
}
