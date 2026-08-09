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

  generateBuildingEnvironment(
    buildingId: number, radiusM: number, terrainSpacingM: number | null = null,
  ): Observable<unknown> {
    return this.http.post(`${this.base}/api/batiments/${buildingId}/generer-environnement/`, {
      radius_m: radiusM, terrain_spacing_m: terrainSpacingM,
    });
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

  generateEnvironment(payload: { lat: number; lon: number; radius_m: number }): Observable<unknown> {
    return this.http.post(`${this.base}/api/environnements/generer/`, payload);
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
