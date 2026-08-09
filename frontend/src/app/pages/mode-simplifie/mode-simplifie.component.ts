import { Component, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Building, WorkingTriangle } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';
import { VENTILATION_PROFILES, VentilationProfile } from '../../core/ventilation-profiles';

interface ParoiModelSummary {
  id: number;
  name: string;
  is_glazing: boolean;
}

interface Candidate {
  lat: number;
  lon: number;
  distance_m: number;
  height_m: number;
  approx_height: boolean;
  source: 'ign' | 'osm';
  n_walls: number;
  vertices: number[][];
  triangles: { v: [number, number, number]; group: string; boundary: 'ground' | 'exterior_air' }[];
}

interface GroupConfig {
  opaqueModelId: number | null;
  glazingModelId: number | null;
  tauxVitragePct: number;
}

const GLAZING_COLOR = '--accent';
const OPAQUE_COLOR = '--text-mute';
const UNASSIGNED_COLOR = '--warning';

/** Mode simplifié (Lot T) — point d'entrée pédagogique vers la méthode complète :
 * recherche d'un bâtiment réel (IGN/OSM, même mécanisme que la génération
 * d'environnement), configuration d'un taux de vitrage par paroi plutôt qu'un
 * import de maillage + assignation manuelle triangle par triangle. Le
 * `Building` produit est un Building ordinaire, éditable ensuite via les pages
 * Bâtiment/Calcul 3D habituelles — ce mode ne remplace rien, il raccourcit le
 * point de départ. Voir to_do_bilan_thermique.md, Lot T, pour le détail des
 * simplifications assumées (pas de cadre de fenêtre, pas de vraie disposition
 * de fenêtres — juste une proportion de petits triangles).
 */
@Component({
  selector: 'app-mode-simplifie',
  standalone: true,
  imports: [FormsModule, DecimalPipe, RouterLink, MeshViewerComponent],
  templateUrl: './mode-simplifie.component.html',
  styleUrl: './mode-simplifie.component.scss',
})
export class ModeSimplifieComponent implements OnInit {
  private api = inject(ApiService);

  @ViewChild(MeshViewerComponent) viewer?: MeshViewerComponent;

  step = signal<'recherche' | 'creation' | 'configuration' | 'termine'>('recherche');

  // ── Étape 1 : recherche ────────────────────────────────────────────────
  searchLat: number | null = null;
  searchLon: number | null = null;
  searchRadius = 50;
  searching = signal(false);
  searchError = signal('');
  candidates = signal<Candidate[]>([]);
  nSkippedTooComplex = signal(0);
  selectedCandidateIndex = signal<number | null>(null);

  get selectedCandidate(): Candidate | null {
    const i = this.selectedCandidateIndex();
    return i === null ? null : this.candidates()[i] ?? null;
  }

  search(): void {
    if (this.searchLat === null || this.searchLon === null) return;
    this.searching.set(true);
    this.searchError.set('');
    this.candidates.set([]);
    this.selectedCandidateIndex.set(null);
    this.api.searchNearbyBuildings({ lat: this.searchLat, lon: this.searchLon, radius_m: this.searchRadius }).subscribe({
      next: (res) => {
        const r = res as { candidates: Candidate[]; n_skipped_too_complex: number };
        this.candidates.set(r.candidates);
        this.nSkippedTooComplex.set(r.n_skipped_too_complex);
        this.searching.set(false);
        if (r.candidates.length === 0) {
          this.searchError.set(
            r.n_skipped_too_complex > 0
              ? "Bâtiment(s) trouvé(s) mais trop complexe(s) pour ce mode — essayez l'import manuel (page Bâtiment)."
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

  /** Nom proposé par défaut à l'étape 2. Les coordonnées plutôt que la distance
   * ou la source : `Building.name` est UNIQUE côté serveur (BuildingSerializer.
   * validate_name), et deux bâtiments cherchés depuis deux points différents
   * doivent pouvoir coexister sans que l'utilisateur ait à renommer. */
  private defaultNameFor(c: Candidate): string {
    return `Bâtiment ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}`;
  }

  selectCandidate(i: number): void {
    const candidate = this.candidates()[i];
    if (!candidate) return;

    // Le nom proposé ne doit jamais écraser une saisie de l'utilisateur : on ne
    // le (re)pose que si le champ est vide, ou s'il contient encore le nom
    // proposé pour le candidat précédent (cas « Changer de bâtiment » sans
    // avoir renommé).
    const previous = this.selectedCandidate;
    const current = this.buildingName.trim();
    if (!current || (previous && current === this.defaultNameFor(previous))) {
      this.buildingName = this.defaultNameFor(candidate);
    }

    this.selectedCandidateIndex.set(i);
    this.createError.set('');
    // Sans ce passage à 'creation', l'étape 2 reste masquée
    // (`@if (selectedCandidate && step() !== 'recherche')`) et cliquer
    // « Choisir » n'a aucun effet visible : c'est très exactement le bug qui a
    // rendu tout le mode simplifié inutilisable de sa livraison (2026-08-08) au
    // Lot W — voir to_do_bilan_thermique.md.
    this.step.set('creation');
  }

  /** Revenir au choix du bâtiment sans relancer la recherche réseau (les
   * candidats déjà extrudés sont conservés). Volontairement limité à l'étape
   * 'creation' : au-delà, le bâtiment existe côté serveur et changer de
   * candidat laisserait un `Building` orphelin. */
  backToSearch(): void {
    if (this.step() !== 'creation') return;
    this.createError.set('');
    this.step.set('recherche');
  }

  // ── Étape 2 : création + subdivision fine ───────────────────────────────
  buildingName = '';
  // Défaut vérifié en réel (2026-08-08) : le raffinement (geometry.refine_envelope)
  // propage la subdivision à tout le maillage connecté (murs/toiture/sol partagent
  // des arêtes dans un volume extrudé étanche) — pas de raffinement "murs
  // seulement". 0,6 m produisait 16384 triangles pour un petit pavillon (bien
  // au-delà de MAX_TOTAL_DOF du solveur une fois chaque triangle maillé en
  // profondeur) ; 2,0 m donne ~128 triangles/mur, largement assez fin pour un
  // taux de vitrage à quelques % près, avec une marge confortable.
  maxEdgeLength = 2.0;
  creating = signal(false);
  createError = signal('');
  buildingId = signal<number | null>(null);

  // Renouvellement d'air (entrée simplifiée) — un unique profil catalogue
  // (frontend/src/app/core/ventilation-profiles.ts, déjà utilisé par Calcul 3D)
  // appliqué au volume RÉEL du bâtiment trouvé (empreinte × hauteur, exact —
  // pas une estimation manuelle comme sur Calcul 3D). Optionnel : sans profil
  // choisi, aucune suggestion n'est envoyée, comme pour tout bâtiment créé
  // sans passer par ce mode. Reste une SUGGESTION, jamais utilisée telle
  // quelle par le solveur — voir Building.suggested_debit_vent_m3h.
  ventilationProfiles = VENTILATION_PROFILES;
  selectedVentProfileId: string | null = null;

  get selectedVentProfile(): VentilationProfile | null {
    return this.ventilationProfiles.find(p => p.id === this.selectedVentProfileId) ?? null;
  }

  private footprintAreaM2(c: Candidate): number {
    let area = 0;
    for (const t of c.triangles) {
      if (t.group !== 'sol') continue;
      const [i, j, k] = t.v;
      const a = c.vertices[i], b = c.vertices[j], p = c.vertices[k];
      const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
      const vx = p[0] - a[0], vy = p[1] - a[1], vz = p[2] - a[2];
      const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
      area += 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
    }
    return area;
  }

  get estimatedVolumeM3(): number | null {
    const c = this.selectedCandidate;
    return c ? Math.round(this.footprintAreaM2(c) * c.height_m) : null;
  }

  get suggestedDebitVentM3h(): number | null {
    const profile = this.selectedVentProfile;
    const volume = this.estimatedVolumeM3;
    if (!profile || volume === null) return null;
    return Math.round(profile.tauxRenouvellementVolH * volume * 10) / 10;
  }

  get suggestedEtaRecupVent(): number | null {
    return this.selectedVentProfile?.etaRecup ?? null;
  }

  vertices = signal<number[][]>([]);
  triangles = signal<WorkingTriangle[]>([]);
  groups = signal<string[]>([]);
  groupConfig: Record<string, GroupConfig> = {};

  paroiModels = signal<ParoiModelSummary[]>([]);
  get opaqueModels(): ParoiModelSummary[] {
    return this.paroiModels().filter(m => !m.is_glazing);
  }
  get glazingModels(): ParoiModelSummary[] {
    return this.paroiModels().filter(m => m.is_glazing);
  }

  ngOnInit(): void {
    this.api.getParoiModeles().subscribe({
      next: (models) => this.paroiModels.set(models as ParoiModelSummary[]),
      error: () => {},
    });
  }

  createAndRefine(): void {
    const c = this.selectedCandidate;
    if (!c || !this.buildingName.trim()) return;
    this.creating.set(true);
    this.createError.set('');

    const payload = {
      name: this.buildingName.trim(),
      vertices: c.vertices,
      triangles: c.triangles.map(t => ({ v: t.v, group: t.group, paroi_model_id: null, boundary: t.boundary })),
      georef_lat: c.lat, georef_lon: c.lon, georef_north_offset_deg: 0,
      suggested_debit_vent_m3h: this.suggestedDebitVentM3h,
      suggested_eta_recup_vent: this.suggestedEtaRecupVent,
    };

    this.api.createBuilding(payload).subscribe({
      next: (res) => {
        const building = res as Building;
        this.buildingId.set(building.id);
        this.api.refineBuildingMesh(building.id, this.maxEdgeLength).subscribe({
          next: () => this.loadRefinedBuilding(building.id),
          error: (err) => {
            this.creating.set(false);
            this.createError.set(err?.error?.detail ?? "Échec de la subdivision — augmentez la taille de maille.");
          },
        });
      },
      error: (err) => {
        this.creating.set(false);
        this.createError.set(err?.error?.name?.[0] ?? "Échec de la création du bâtiment.");
      },
    });
  }

  private loadRefinedBuilding(id: number): void {
    this.api.getBuilding(id).subscribe({
      next: (res) => {
        const building = res as Building;
        this.vertices.set(building.envelope.vertices);
        this.triangles.set(building.envelope.triangles);
        const groupNames = [...new Set(building.envelope.triangles.map(t => t.group).filter((g): g is string => !!g))];
        this.groups.set(groupNames);
        this.groupConfig = {};
        for (const g of groupNames) {
          this.groupConfig[g] = {
            opaqueModelId: null, glazingModelId: null,
            tauxVitragePct: g === 'sol' || g === 'toiture' ? 0 : 20,
          };
        }
        this.actualGlazingPct.set({});
        this.creating.set(false);
        this.step.set('configuration');
      },
      error: () => {
        this.creating.set(false);
        this.createError.set('Échec du rechargement du bâtiment subdivisé.');
      },
    });
  }

  // ── Étape 3 : configuration par paroi + assignation proportionnelle ─────
  triangleCountForGroup(group: string): number {
    return this.triangles().filter(t => t.group === group).length;
  }

  // Taux de vitrage RÉELLEMENT obtenu par paroi (%), calculé une fois par
  // génération d'assignation plutôt qu'à chaque cycle de détection de
  // changement. Mesuré en AIRE et non en nombre de triangles : c'est l'aire qui
  // compte physiquement, et deux triangles d'un même groupe n'ont pas
  // forcément la même aire (le raffinement, geometry.refine_envelope, ne
  // descend pas partout au même niveau — cascade documentée au Lot T). Une
  // paroi absente du dict = aucun vitrage demandé.
  actualGlazingPct = signal<Record<string, number>>({});

  generateAssignment(): void {
    const updated = this.triangles().map(t => ({ ...t }));
    for (const group of this.groups()) {
      const cfg = this.groupConfig[group];
      if (!cfg || cfg.opaqueModelId === null) continue;
      const indices: number[] = [];
      updated.forEach((t, i) => { if (t.group === group) indices.push(i); });

      for (const i of indices) updated[i] = { ...updated[i], paroi_model_id: cfg.opaqueModelId };

      if (cfg.glazingModelId !== null && cfg.tauxVitragePct > 0) {
        const ratio = Math.min(cfg.tauxVitragePct, 95) / 100;
        // Répartition par accumulateur (Bresenham) plutôt qu'un « un triangle
        // sur N » avec N = round(1/ratio) entier : ce pas entier ne pouvait
        // atteindre que les taux 1/N, et l'arrondi rendait deux consignes
        // différentes indiscernables — 30 % et 40 % donnaient TOUS DEUX ≈ 33 %
        // (1/0,4 vaut exactement 2,5, et Math.round arrondit au supérieur).
        // Mesuré en navigateur au Lot W : 40 % demandés → 37,5 % obtenus sur
        // une paroi de 16 triangles. L'accumulateur pose exactement
        // ⌊n·ratio⌋ triangles, également répartis : c'est le plus proche
        // atteignable pour un maillage donné, quel que soit le taux.
        // `Math.round` et non `Math.floor` dans l'accumulateur : il pose
        // round(n·ratio) triangles au lieu de ⌊n·ratio⌋, donc le comptage le
        // PLUS PROCHE du taux demandé et non systématiquement celui du dessous
        // (sur une paroi de 16 triangles, 10 % donne 2 triangles = 12,5 % et
        // non 1 = 6,25 %). La répartition reste également espacée.
        indices.forEach((triIdx, pos) => {
          if (Math.round((pos + 1) * ratio) > Math.round(pos * ratio)) {
            updated[triIdx] = { ...updated[triIdx], paroi_model_id: cfg.glazingModelId };
          }
        });
      }
    }
    this.triangles.set(updated);
    this.actualGlazingPct.set(this.measureGlazingPct(updated));
    this.viewer?.repaint();
  }

  /** Même avec une répartition optimale (voir generateAssignment), un taux
   * quelconque reste inatteignable sur un nombre FINI de triangles d'aires
   * inégales : c'est la valeur mesurée ici qui entrera dans le calcul, pas le
   * taux saisi. À afficher, donc, plutôt que de laisser croire à une consigne
   * exacte — d'autant que l'écart grandit quand la paroi est peu maillée. */
  private measureGlazingPct(triangles: WorkingTriangle[]): Record<string, number> {
    const obtained: Record<string, number> = {};
    for (const group of this.groups()) {
      const glazingId = this.groupConfig[group]?.glazingModelId ?? null;
      if (glazingId === null) continue;
      let total = 0;
      let glazed = 0;
      for (const t of triangles) {
        if (t.group !== group) continue;
        const area = t.area ?? 0;
        total += area;
        if (t.paroi_model_id === glazingId) glazed += area;
      }
      if (total > 0) obtained[group] = (glazed / total) * 100;
    }
    return obtained;
  }

  colorForTriangle = (index: number): string => {
    const t = this.triangles()[index];
    if (!t || t.paroi_model_id === null) return UNASSIGNED_COLOR;
    const model = this.paroiModels().find(m => m.id === t.paroi_model_id);
    return model?.is_glazing ? GLAZING_COLOR : OPAQUE_COLOR;
  };

  get allGroupsConfigured(): boolean {
    return this.groups().every(g => this.groupConfig[g]?.opaqueModelId !== null);
  }

  get assignedCount(): number {
    return this.triangles().filter(t => t.paroi_model_id !== null).length;
  }

  saving = signal(false);
  saveError = signal('');

  save(): void {
    const id = this.buildingId();
    if (id === null || this.assignedCount === 0) return;
    this.saving.set(true);
    this.saveError.set('');
    const triangles = this.triangles().map(t => ({ v: t.v, group: t.group, paroi_model_id: t.paroi_model_id, boundary: t.boundary }));
    this.api.updateBuilding(id, { triangles }).subscribe({
      next: () => {
        this.saving.set(false);
        this.step.set('termine');
      },
      error: (err) => {
        this.saving.set(false);
        this.saveError.set(err?.error?.detail ?? "Échec de l'enregistrement.");
      },
    });
  }
}
