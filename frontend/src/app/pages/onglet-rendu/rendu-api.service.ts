import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { apiUrl } from '../../core/env';
import { FigureRendu } from '../../components/graphe/graphe.types';
import { ScenePartriangle } from '../../components/vue3d-resultats/vue3d.types';
import {
  BouquetsParc, Cible, Dimension, DocumentResolu, ExportRendu, Gabarits, Graphe, Mesure, Modele, ModelesParc, Portee,
  PotentielsBatiment, PotentielsParc, Spec,
} from './contrat.types';

/** I3 : `GET /api/indicateurs/`. */
export interface IndicateurCanon {
  cle: string; libelle: string; unite: string; niveaux: string[]; additif: boolean;
  sens: 'bas_est_mieux' | 'haut_est_mieux'; famille: 'energie' | 'confort' | 'donnees';
}

/** Erreur d'API rendue en mots fixes ; `indisponible` : la route existe mais répond 501 (pas encore livrée). */
export class ErreurApi extends Error {
  constructor(message: string, readonly statut: number, readonly indisponible = false) { super(message); }
}

export function messageDe(e: unknown, quoi: string): string {
  return e instanceof ErreurApi ? e.message : `${quoi} : erreur inattendue.`;
}

/**
 * Client des routes consommées par le secteur F (contrat bt-potentiels v3) : I4 (triangles), I5 (potentiels),
 * I6 (rendu), I7 (bouquets). Toutes sous l'API de l'app (le jeton y est joint par l'intercepteur).
 */
@Injectable({ providedIn: 'root' })
export class RenduApiService {
  private http = inject(HttpClient);
  private get b(): string { return `${apiUrl()}/api`; }

  private async appel<T>(p: Promise<T>, quoi: string): Promise<T> {
    try { return await p; } catch (e) { throw this.traduire(e, quoi); }
  }
  private traduire(e: unknown, quoi: string): ErreurApi {
    if (!(e instanceof HttpErrorResponse)) return new ErreurApi(`${quoi} : erreur inattendue.`, 0);
    if (e.status === 501) return new ErreurApi(`${quoi} : pas encore disponible sur le serveur.`, 501, true);
    if (e.status === 0) return new ErreurApi(`${quoi} : serveur injoignable.`, 0);
    if (e.status === 403) return new ErreurApi(typeof e.error?.detail === 'string' ? e.error.detail : `${quoi} : action réservée à un gestionnaire.`, 403);
    if (e.status === 404) return new ErreurApi(typeof e.error?.detail === 'string' ? e.error.detail : `${quoi} : introuvable.`, 404);
    const d = e.error?.detail;
    return new ErreurApi(typeof d === 'string' && d.length < 300 ? d : `${quoi} : refusé (${e.status}).`, e.status);
  }
  private get<T>(chemin: string, quoi: string): Promise<T> { return this.appel(firstValueFrom(this.http.get<T>(`${this.b}${chemin}`)), quoi); }
  private post<T>(chemin: string, corps: unknown, quoi: string): Promise<T> { return this.appel(firstValueFrom(this.http.post<T>(`${this.b}${chemin}`, corps)), quoi); }

  // ── I4 ──
  triangles(runId: number): Promise<ScenePartriangle> { return this.get(`/calculs/${runId}/triangles/`, 'Résultats par triangle'); }
  /** Derniers calculs du bâtiment par niveau (route existante, lot 23) : sert à trouver le dernier N2. */
  resultat(batiment: number): Promise<Record<'N0' | 'N1' | 'N2', { id: number; status: string; created_at: string; up_to_date?: boolean | null } | null> & { can_n2?: boolean }> {
    return this.get(`/batiments/${batiment}/resultat/`, 'Derniers calculs');
  }

  // ── I3 ── liste canonique des indicateurs (producteur P)
  indicateurs(): Promise<IndicateurCanon[]> { return this.get('/indicateurs/', 'Liste des indicateurs'); }
  private i3?: Promise<IndicateurCanon[]>;
  /** I3 lu une fois par session (liste canonique, ne change pas sans déploiement) ; [] si indisponible. */
  indicateursMemo(): Promise<IndicateurCanon[]> {
    this.i3 ??= this.indicateurs().catch(() => { this.i3 = undefined; return [] as IndicateurCanon[]; });
    return this.i3;
  }

  // ── I5 ──
  potentielsBatiment(id: number): Promise<PotentielsBatiment> { return this.get(`/batiments/${id}/potentiels/`, 'Potentiels'); }
  calculerPotentielsBatiment(id: number, corps: { bouquets?: number[]; niveau: 'N1' | 'N2' }): Promise<PotentielsBatiment> {
    return this.post(`/batiments/${id}/potentiels/`, corps, 'Calcul des potentiels');
  }
  potentielsParc(id: number): Promise<PotentielsParc> { return this.get(`/parcs/${id}/potentiels/`, 'Potentiels du parc'); }
  calculerPotentielsParc(id: number, bouquets?: number[]): Promise<PotentielsParc> {
    return this.post(`/parcs/${id}/potentiels/`, bouquets ? { bouquets, niveau: 'N1' } : { niveau: 'N1' }, 'Calcul des potentiels du parc');
  }

  // ── I7 ──
  bouquetsParc(id: number): Promise<BouquetsParc> { return this.get(`/parcs/${id}/bouquets/`, 'Bouquets'); }

  // ── I6 ──
  dimensions(): Promise<Dimension[]> { return this.get('/rendu/dimensions/', 'Dimensions des graphes'); }
  mesures(): Promise<Mesure[]> { return this.get('/rendu/mesures/', 'Mesures des graphes'); }
  gabarits(): Promise<Gabarits> { return this.get('/rendu/gabarits/', 'Gabarits du lab'); }
  figure(portee: Portee, cible: Cible, graphe: Graphe): Promise<FigureRendu> {
    return this.post('/rendu/figure/', { portee, cible, graphe }, 'Graphe');
  }
  document(source: { spec: Spec } | { modele_id: number }, cible: Cible): Promise<DocumentResolu> {
    return this.post('/rendu/document/', { ...source, cible }, 'Document');
  }
  modelesParc(id: number): Promise<ModelesParc> { return this.get(`/parcs/${id}/rapports/`, 'Modèles du parc'); }
  creerModele(parc: number, m: { nom: string; portee: Portee; spec: Spec }): Promise<Modele> {
    return this.post(`/parcs/${parc}/rapports/`, m, 'Enregistrement du modèle');
  }
  modifierModele(id: number, m: { nom?: string; spec?: Spec; par_defaut?: boolean }): Promise<Modele> {
    return this.appel(firstValueFrom(this.http.patch<Modele>(`${this.b}/rapports/${id}/`, m)), 'Enregistrement du modèle');
  }
  supprimerModele(id: number): Promise<unknown> {
    return this.appel(firstValueFrom(this.http.delete(`${this.b}/rapports/${id}/`)), 'Suppression du modèle');
  }
  exports(): Promise<ExportRendu[]> { return this.get('/rendu/exports/', 'Exports'); }
  lancerExport(source: { spec: Spec } | { modele_id: number }, cible: Cible, format: 'html' | 'docx' | 'pdf'): Promise<ExportRendu> {
    return this.post('/rendu/exports/', { ...source, cible, format }, 'Export');
  }
  etatExport(id: number): Promise<ExportRendu> { return this.get(`/rendu/exports/${id}/`, 'Export'); }
  fichierExport(id: number): Promise<Blob> {
    return this.appel(firstValueFrom(this.http.get(`${this.b}/rendu/exports/${id}/fichier`, { responseType: 'blob' })), 'Téléchargement');
  }
}

/** Télécharge un blob sous un nom donné (lien d'objet libéré aussitôt). */
export function telecharger(blob: Blob, nom: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = nom; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
