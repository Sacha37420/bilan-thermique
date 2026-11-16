import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { RefonteApiService } from '../../core/refonte-api.service';
import {
  Bouquet, BouquetCorps, BouquetPatch, BouquetsParc, CatalogueActions, PotentielsBatiment, PotentielsParc,
} from './ameliorations.types';

/** Routes I5 (potentiels) et I7 (actions, bouquets) du chantier bt-potentiels. */
@Injectable({ providedIn: 'root' })
export class AmeliorationsApiService {
  private rf = inject(RefonteApiService);

  actions(parcId?: number | null): Observable<CatalogueActions> {
    return this.rf.get<CatalogueActions>(parcId ? `/ameliorations/actions/?parc=${parcId}` : '/ameliorations/actions/');
  }

  bouquetsParc(id: number): Observable<BouquetsParc> { return this.rf.get<BouquetsParc>(`/parcs/${id}/bouquets/`); }
  creerBouquet(parcId: number, body: BouquetCorps): Observable<Bouquet> {
    return this.rf.post<Bouquet>(`/parcs/${parcId}/bouquets/`, body);
  }
  copierBouquet(parcId: number, id: number, nom?: string): Observable<Bouquet> {
    return this.rf.post<Bouquet>(`/parcs/${parcId}/bouquets/`, nom ? { copie_de: id, nom } : { copie_de: id });
  }
  modifierBouquet(id: number, patch: BouquetPatch): Observable<Bouquet> { return this.rf.patch<Bouquet>(`/bouquets/${id}/`, patch); }
  supprimerBouquet(id: number): Observable<void> { return this.rf.del<void>(`/bouquets/${id}/`); }

  potentielsBatiment(id: number): Observable<PotentielsBatiment> {
    return this.rf.get<PotentielsBatiment>(`/batiments/${id}/potentiels/`);
  }
  calculerBatiment(id: number, bouquets: number[], niveau: 'N0' | 'N1' | 'N2'): Observable<PotentielsBatiment> {
    return this.rf.post<PotentielsBatiment>(`/batiments/${id}/potentiels/`, { bouquets, niveau });
  }
  potentielsParc(id: number): Observable<PotentielsParc> { return this.rf.get<PotentielsParc>(`/parcs/${id}/potentiels/`); }
  calculerParc(id: number, bouquets?: number[]): Observable<PotentielsParc> {
    return this.rf.post<PotentielsParc>(`/parcs/${id}/potentiels/`, bouquets ? { bouquets, niveau: 'N1' } : { niveau: 'N1' });
  }
}
