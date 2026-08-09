import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { BuildingCandidate } from '../../core/building.types';

/**
 * Recherche d'un bâtiment réel par coordonnées (IGN BD TOPO / OpenStreetMap) —
 * `POST /api/batiments/rechercher/`, qui renvoie chaque candidat DÉJÀ extrudé en
 * enveloppe groupée (`sol`/`toiture`/`mur_1..N`) avec ses `boundary`.
 *
 * Composant partagé (Lot Y) entre le mode simplifié, qui l'a introduit au Lot T,
 * et la page Bâtiment : dupliquer ce formulaire ferait diverger les deux à la
 * première évolution — et le mode simplifié vient précisément de montrer (Lot W)
 * qu'un parcours non exercé pourrit en silence.
 *
 * Le composant ne fait QUE chercher et laisser choisir : il n'écrit rien, ne
 * crée aucun bâtiment. Chaque page décide de ce qu'elle fait du candidat.
 */
@Component({
  selector: 'app-building-search',
  standalone: true,
  imports: [FormsModule, DecimalPipe],
  templateUrl: './building-search.component.html',
  styleUrl: './building-search.component.scss',
})
export class BuildingSearchComponent {
  private api = inject(ApiService);

  /** Plafond de parois d'un candidat. Le mode simplifié génère un menu
   * déroulant PAR PAROI, d'où sa limite basse (30) ; la page Bâtiment assigne
   * par groupe avec sélection multiple et dispose du sélecteur manuel au clic,
   * la contrainte n'y a pas lieu d'être. */
  @Input() maxWalls: number | null = null;
  /** Coordonnées de départ du formulaire (géoréférencement déjà connu). */
  @Input() set initialLat(value: number | null) { if (value !== null) this.searchLat = value; }
  @Input() set initialLon(value: number | null) { if (value !== null) this.searchLon = value; }

  @Output() candidateChosen = new EventEmitter<BuildingCandidate>();

  searchLat: number | null = null;
  searchLon: number | null = null;
  searchRadius = 50;

  searching = signal(false);
  searchError = signal('');
  candidates = signal<BuildingCandidate[]>([]);
  nSkippedTooComplex = signal(0);
  selectedIndex = signal<number | null>(null);

  get selected(): BuildingCandidate | null {
    const i = this.selectedIndex();
    return i === null ? null : this.candidates()[i] ?? null;
  }

  search(): void {
    if (this.searchLat === null || this.searchLon === null) return;
    this.searching.set(true);
    this.searchError.set('');
    this.candidates.set([]);
    this.selectedIndex.set(null);

    this.api.searchNearbyBuildings({
      lat: this.searchLat, lon: this.searchLon, radius_m: this.searchRadius,
      max_walls: this.maxWalls,
    }).subscribe({
      next: (res) => {
        const r = res as { candidates: BuildingCandidate[]; n_skipped_too_complex: number };
        this.candidates.set(r.candidates);
        this.nSkippedTooComplex.set(r.n_skipped_too_complex);
        this.searching.set(false);
        if (r.candidates.length === 0) {
          this.searchError.set(
            r.n_skipped_too_complex > 0
              ? "Bâtiment(s) trouvé(s) mais trop complexe(s) — empreinte à trop de parois pour ce mode."
              : "Aucun bâtiment trouvé à cet endroit — élargissez le rayon ou vérifiez les coordonnées.",
          );
        }
      },
      error: (err) => {
        this.searching.set(false);
        this.searchError.set(err?.error?.detail ?? 'Échec de la recherche.');
      },
    });
  }

  choose(index: number): void {
    const candidate = this.candidates()[index];
    if (!candidate) return;
    this.selectedIndex.set(index);
    this.candidateChosen.emit(candidate);
  }

  sourceLabel(candidate: BuildingCandidate): string {
    return candidate.source === 'ign' ? 'IGN BD TOPO' : 'OpenStreetMap';
  }
}
