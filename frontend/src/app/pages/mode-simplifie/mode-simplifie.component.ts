import { Component, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Building, BuildingCandidate, WorkingTriangle } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';
import { BuildingSearchComponent } from '../../components/building-search/building-search.component';
import { VENTILATION_PROFILES, VentilationProfile } from '../../core/ventilation-profiles';
import {
  USAGE_PROFILES, UsageProfile, UsageProfileId,
  defaultOccupationCalendar, computeThermostatSetpoints, schoolHolidayRanges,
  SCHOOL_ZONES, SchoolZone,
} from '../../core/usage-profiles';
import { Job } from '../../core/building.types';

interface ParoiModelSummary {
  id: number;
  name: string;
  is_glazing: boolean;
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
  imports: [FormsModule, DecimalPipe, RouterLink, MeshViewerComponent, BuildingSearchComponent],
  templateUrl: './mode-simplifie.component.html',
  styleUrl: './mode-simplifie.component.scss',
})
export class ModeSimplifieComponent implements OnInit {
  private api = inject(ApiService);

  @ViewChild(MeshViewerComponent) viewer?: MeshViewerComponent;

  step = signal<'recherche' | 'creation' | 'configuration' | 'environnement' | 'calcul' | 'termine'>('recherche');

  // ── Étape 1 : recherche ────────────────────────────────────────────────
  // Formulaire et appel réseau délégués au composant partagé (Lot Y,
  // components/building-search) — utilisé aussi par la page Bâtiment. Ce
  // composant n'écrit rien : il émet le candidat choisi, cette page décide.
  chosen = signal<BuildingCandidate | null>(null);

  get selectedCandidate(): BuildingCandidate | null {
    return this.chosen();
  }

  /** Nom proposé par défaut à l'étape 2. Les coordonnées plutôt que la distance
   * ou la source : `Building.name` est UNIQUE côté serveur (BuildingSerializer.
   * validate_name), et deux bâtiments cherchés depuis deux points différents
   * doivent pouvoir coexister sans que l'utilisateur ait à renommer. */
  private defaultNameFor(c: BuildingCandidate): string {
    return `Bâtiment ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)}`;
  }

  onCandidateChosen(candidate: BuildingCandidate): void {
    // Le nom proposé ne doit jamais écraser une saisie de l'utilisateur : on ne
    // le (re)pose que si le champ est vide, ou s'il contient encore le nom
    // proposé pour le candidat précédent (cas « Changer de bâtiment » sans
    // avoir renommé).
    const previous = this.chosen();
    const current = this.buildingName.trim();
    if (!current || (previous && current === this.defaultNameFor(previous))) {
      this.buildingName = this.defaultNameFor(candidate);
    }

    this.chosen.set(candidate);
    this.createError.set('');
    // Sans ce passage à 'creation', l'étape 2 reste masquée
    // (`@if (selectedCandidate && step() !== 'recherche')`) et cliquer
    // « Choisir » n'a aucun effet visible : c'est très exactement le bug qui a
    // rendu tout le mode simplifié inutilisable de sa livraison (2026-08-08) au
    // Lot W — voir to_do_bilan_thermique.md.
    this.step.set('creation');
  }

  /** Revenir au choix du bâtiment sans relancer la recherche réseau (les
   * candidats déjà extrudés sont conservés par le composant de recherche).
   * Volontairement limité à l'étape 'creation' : au-delà, le bâtiment existe
   * côté serveur et changer de candidat le laisserait orphelin. */
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

  private footprintAreaM2(c: BuildingCandidate): number {
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

  // ══ Étape 4 — Environnement voisin + ombrage ═══════════════════════════════
  // Le mode simplifié s'arrêtait au bâtiment et renvoyait sur Calcul 3D pour
  // « la météo et le calcul » — qu'il ne faisait ni l'un ni l'autre. Pire : un
  // bâtiment neuf a son ombrage marqué périmé, donc le calcul y était REFUSÉ.
  // Le parcours va désormais jusqu'au résultat, en simplifié.
  includeNeighbours = true;
  includeVegetation = false;
  envRadius = 150;
  envBusy = signal(false);
  envStatus = signal('');
  envError = signal('');
  shadowReady = signal(false);

  private poll(jobId: number, onDone: (job: Job) => void, onError: (m: string) => void): void {
    const handle = setInterval(() => {
      this.api.getJob(jobId).subscribe({
        next: (res) => {
          const job = res as Job;
          this.envStatus.set(job.message || `${job.progress}%`);
          if (job.status === 'DONE') { clearInterval(handle); onDone(job); }
          else if (job.status === 'ERROR') { clearInterval(handle); onError(job.message || 'Échec.'); }
        },
        error: () => { clearInterval(handle); onError('Suivi de la tâche interrompu.'); },
      });
    }, 2000);
  }

  /** Enchaîne, sans rien demander de plus : génération des obstacles alignés sur
   * ce bâtiment (donc sans lui-même et sans obstacle qui l'empiète) →
   * enregistrement → association → précalcul d'ombrage. Le précalcul est lancé
   * même sans voisins : sans lui, le calcul serait refusé. */
  buildEnvironmentAndShadow(): void {
    const id = this.buildingId();
    const c = this.selectedCandidate;
    if (id === null || !c || this.envBusy()) return;
    this.envBusy.set(true);
    this.envError.set('');
    this.shadowReady.set(false);

    if (!this.includeNeighbours) {
      this.envStatus.set("Ombrage : le bâtiment sur lui-même uniquement…");
      this.launchPrecompute(id);
      return;
    }

    this.envStatus.set('Recherche des bâtiments voisins…');
    this.api.generateEnvironment({
      lat: c.lat, lon: c.lon, radius_m: this.envRadius,
      include_vegetation: this.includeVegetation, building_id: id,
    }).subscribe({
      next: (res) => this.poll((res as Job).id,
        (job) => this.saveAndLinkEnvironment(id, job),
        (m) => { this.envBusy.set(false); this.envError.set(m); }),
      error: () => { this.envBusy.set(false); this.envError.set('Échec du lancement de la recherche.'); },
    });
  }

  private saveAndLinkEnvironment(buildingId: number, job: Job): void {
    const r = job.result as unknown as { vertices: number[][]; triangles: unknown[]; warnings: string[] };
    this.envWarnings.set(r.warnings ?? []);
    if (!r.triangles?.length) {
      // Aucun voisin trouvé : ce n'est pas une erreur, on passe à l'ombrage.
      this.envStatus.set('Aucun bâtiment voisin trouvé — ombrage sur le bâtiment seul.');
      this.launchPrecompute(buildingId);
      return;
    }
    this.envStatus.set('Enregistrement des obstacles…');
    this.api.createEnvironment({
      name: `Voisinage — ${this.buildingName} — ${new Date().toISOString().slice(0, 16)}`,
      vertices: r.vertices, triangles: r.triangles,
    }).subscribe({
      next: (env) => {
        this.api.updateBuilding(buildingId, { environment_id: (env as { id: number }).id }).subscribe({
          next: () => this.launchPrecompute(buildingId),
          error: () => { this.envBusy.set(false); this.envError.set("Échec de l'association des obstacles."); },
        });
      },
      error: () => { this.envBusy.set(false); this.envError.set("Échec de l'enregistrement des obstacles."); },
    });
  }

  private launchPrecompute(buildingId: number): void {
    this.envStatus.set("Calcul de l'ombrage…");
    this.api.precomputeShadows(buildingId).subscribe({
      next: (res) => this.poll((res as Job).id,
        () => {
          this.envBusy.set(false);
          this.shadowReady.set(true);
          this.envStatus.set('Ombrage calculé.');
          this.step.set('calcul');
        },
        (m) => { this.envBusy.set(false); this.envError.set(m); }),
      error: (err) => {
        this.envBusy.set(false);
        this.envError.set(err?.error?.detail ?? "Échec du lancement de l'ombrage.");
      },
    });
  }

  envWarnings = signal<string[]>([]);

  // ══ Étape 5 — Météo et calcul ═════════════════════════════════════════════
  usageProfiles = USAGE_PROFILES;
  selectedUsageProfileId: UsageProfileId = 'habitation';
  schoolZones = SCHOOL_ZONES;
  selectedZone: SchoolZone = 'C';

  /** Vrai pour les deux profils scolaires : eux seuls traitent les vacances en
   * hors-gel (le tertiaire ignore le calendrier scolaire — cadrage tranché au
   * Lot V). C'est donc la seule situation où la zone de vacances a un effet. */
  get usesSchoolHolidays(): boolean {
    return this.selectedUsageProfile?.vacancesHorsGel === true;
  }
  calcBusy = signal(false);
  calcStatus = signal('');
  calcError = signal('');
  result = signal<{ heating_kwh: number; cooling_kwh: number; t_air_mean: number; hours: number } | null>(null);

  get selectedUsageProfile(): UsageProfile | undefined {
    return this.usageProfiles.find(p => p.id === this.selectedUsageProfileId);
  }

  get surfaceRefM2(): number | null {
    const c = this.selectedCandidate;
    return c ? Math.round(this.footprintAreaM2(c)) : null;
  }

  /** Récupère une année type puis lance le calcul, sans autre réglage : tous
   * les paramètres physiques sont dérivés de ce que l'assistant connaît déjà
   * (volume réel, profil de ventilation choisi à l'étape 2, orientation de
   * chaque triangle) — voir le texte de l'étape 5 pour la liste exacte. */
  runCalculation(): void {
    const id = this.buildingId();
    const c = this.selectedCandidate;
    if (id === null || !c || this.calcBusy()) return;
    this.calcBusy.set(true);
    this.calcError.set('');
    this.result.set(null);
    this.calcStatus.set('Récupération de la météo (année type)…');

    const today = new Date();
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    const lastYear = new Date(today.getFullYear() - 1, 0, 1);

    this.api.fetchWeather({
      lat: c.lat, lon: c.lon, source: 'tmy',
      start_date: iso(lastYear), end_date: iso(new Date(today.getFullYear() - 1, 11, 31)),
    }).subscribe({
      next: (res) => this.pollCalc((res as Job).id, (job) => {
        const r = job.result as unknown as { weather: Record<string, number>[] };
        this.launchSolver(id, r.weather);
      }),
      error: () => { this.calcBusy.set(false); this.calcError.set('Échec de la récupération météo.'); },
    });
  }

  private pollCalc(jobId: number, onDone: (job: Job) => void): void {
    const handle = setInterval(() => {
      this.api.getJob(jobId).subscribe({
        next: (res) => {
          const job = res as Job;
          this.calcStatus.set(job.message || `${job.progress}%`);
          if (job.status === 'DONE') { clearInterval(handle); onDone(job); }
          else if (job.status === 'ERROR') {
            clearInterval(handle);
            this.calcBusy.set(false);
            this.calcError.set(job.message || 'Échec.');
          }
        },
        error: () => { clearInterval(handle); this.calcBusy.set(false); this.calcError.set('Suivi interrompu.'); },
      });
    }, 2000);
  }

  private launchSolver(buildingId: number, weather: Record<string, number>[]): void {
    this.calcStatus.set('Simulation heure par heure…');
    const profile = this.selectedUsageProfile!;
    const volume = this.estimatedVolumeM3 ?? 250;
    const absoluteHours = weather.map((w, i) => (w['hour_index'] as number | undefined) ?? i);
    const calendar = defaultOccupationCalendar();
    if (this.usesSchoolHolidays) {
      // L'année type PVGIS commence au 1er janvier : le premier jour du run est
      // donc le jour 1 de l'année, et les plages se posent directement.
      const nDays = Math.ceil((Math.max(...absoluteHours) + 1) / 24);
      calendar.vacances = schoolHolidayRanges(this.selectedZone, 1, nDays);
      calendar.jourDebut = 0;  // le 1er janvier d'une année type n'a pas de jour réel
    }
    const setpoints = computeThermostatSetpoints(profile, calendar, absoluteHours);

    this.api.runBuildingCalcul(buildingId, {
      dx_max: 0.02,
      h_e: 22, h_e_dynamic: true,
      interior: {
        mode: 'thermostat', h_i: 8, h_i_auto: true,
        c_air_int: Math.round(volume * 1200),
        t_min: 19, t_max: 26,
        debit_vent_m3h: this.suggestedDebitVentM3h ?? 0,
        eta_recup_vent: this.suggestedEtaRecupVent ?? 0,
        apports_internes_w: 0,
      },
      t_init: 15,
      weather: weather.map((w, i) => ({ ...w, ...setpoints[i] })),
      shadow_mode: 'precomputed',
    }).subscribe({
      next: (res) => this.pollCalc((res as Job).id, (job) => {
        this.calcBusy.set(false);
        this.result.set(job.result as unknown as {
          heating_kwh: number; cooling_kwh: number; t_air_mean: number; hours: number;
        });
        this.step.set('termine');
      }),
      error: (err) => {
        this.calcBusy.set(false);
        this.calcError.set(err?.error?.detail ?? 'Échec du lancement du calcul.');
      },
    });
  }

  get heatingPerM2(): number | null {
    const r = this.result(); const s = this.surfaceRefM2;
    return r && s ? Math.round(r.heating_kwh / s) : null;
  }

  get coolingPerM2(): number | null {
    const r = this.result(); const s = this.surfaceRefM2;
    return r && s ? Math.round(r.cooling_kwh / s) : null;
  }

  save(): void {
    const id = this.buildingId();
    if (id === null || this.assignedCount === 0) return;
    this.saving.set(true);
    this.saveError.set('');
    const triangles = this.triangles().map(t => ({ v: t.v, group: t.group, paroi_model_id: t.paroi_model_id, boundary: t.boundary }));
    this.api.updateBuilding(id, { triangles }).subscribe({
      next: () => {
        this.saving.set(false);
        // Surface de référence renseignée automatiquement : l'empreinte réelle
        // est connue, et sans elle les résultats en kWh/m² restent indisponibles.
        if (this.surfaceRefM2 !== null) {
          this.api.updateBuilding(id, { surface_ref_m2: this.surfaceRefM2 }).subscribe({ error: () => {} });
        }
        this.step.set('environnement');
      },
      error: (err) => {
        this.saving.set(false);
        this.saveError.set(err?.error?.detail ?? "Échec de l'enregistrement.");
      },
    });
  }
}
