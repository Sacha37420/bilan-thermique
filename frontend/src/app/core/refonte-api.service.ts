import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { apiUrl } from './env';
import { Catalogue, Fact, Fiche } from './refonte.types';

/** Routes de la refonte (parc, fiche, affinage) — toutes sous l'API de l'app (`tokenBases()`). */
@Injectable({ providedIn: 'root' })
export class RefonteApiService {
  private http = inject(HttpClient);
  private get b(): string { return `${apiUrl()}/api`; }

  catalogue(): Observable<Catalogue> { return this.http.get<Catalogue>(`${this.b}/fiche/catalogue/`); }
  fiche(id: number, scenario?: number | null): Observable<Fiche> {
    const q = scenario ? `?scenario=${scenario}` : '';
    return this.http.get<Fiche>(`${this.b}/batiments/${id}/fiche/${q}`);
  }
  declare(id: number, body: { key: string; value: unknown; method?: string; source?: string; scenario?: number | null }):
    Observable<Fiche & { fact: Fact }> {
    return this.http.post<Fiche & { fact: Fact }>(`${this.b}/batiments/${id}/faits/`, body);
  }
  retire(factId: number): Observable<Fiche> { return this.http.delete<Fiche>(`${this.b}/faits/${factId}/`); }
  history(id: number, key: string): Observable<{ results: Fact[] }> {
    return this.http.get<{ results: Fact[] }>(`${this.b}/batiments/${id}/faits/?cle=${encodeURIComponent(key)}`);
  }
  get<T>(path: string): Observable<T> { return this.http.get<T>(`${this.b}${path}`); }
  post<T>(path: string, body: unknown): Observable<T> { return this.http.post<T>(`${this.b}${path}`, body); }
  patch<T>(path: string, body: unknown): Observable<T> { return this.http.patch<T>(`${this.b}${path}`, body); }
  put<T>(path: string, body: unknown): Observable<T> { return this.http.put<T>(`${this.b}${path}`, body); }
  del<T>(path: string): Observable<T> { return this.http.delete<T>(`${this.b}${path}`); }
}
