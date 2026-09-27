import { Component, OnInit, WritableSignal, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Building, EnvironmentMesh, EnvironmentObject, Triangle } from '../../core/building.types';
import { FacadePanelComponent } from '../../components/facade-panel/facade-panel.component';
import { EnvSceneComponent } from '../../components/env-scene/env-scene.component';
import { VENTILATION_PROFILES, VentilationProfile } from '../../core/ventilation-profiles';
import {
  USAGE_PROFILES, UsageProfile, UsageProfileId,
  defaultOccupationCalendar, computeThermostatSetpoints, computeOccupancy,
  schoolHolidayRanges, SCHOOL_ZONES, SchoolZone, CLOSED_VENTILATION_FRACTION,
} from '../../core/usage-profiles';
import { Job } from '../../core/building.types';

interface ParoiModelSummary {
  id: number;
  name: string;
  is_glazing: boolean;
}

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
  imports: [FormsModule, DecimalPipe, RouterLink, FacadePanelComponent, EnvSceneComponent],
  templateUrl: './mode-simplifie.component.html',
  styleUrl: './mode-simplifie.component.scss',
})
export class ModeSimplifieComponent implements OnInit {
  private api = inject(ApiService);


  step = signal<'quartier' | 'selection' | 'configuration' | 'environnement' | 'calcul' | 'termine'>('quartier');

  // ── Étape 1 : le quartier en 3D (Lot AI) ───────────────────────────────
  // Plutôt que de chercher UN candidat BD TOPO extrudé à toit plat, on
  // reconstruit tout le quartier (LiDAR HD × BD TOPO : toitures relevées,
  // arbres, relief) et l'utilisateur y désigne son bâtiment en cliquant.
  genLat = 47.3445;
  genLon = 0.6613;
  envRadius = 120;
  includeVegetation = true;
  environments = signal<{ id: number; name: string; n_objects: number }[]>([]);
  reuseEnvId: number | null = null;
  quartierBusy = signal(false);
  quartierStatus = signal('');
  quartierError = signal('');
  env = signal<EnvironmentMesh | null>(null);

  /** Point du monde réel du bâtiment (météo) : l'origine de l'environnement. */
  get site(): { lat: number; lon: number } | null {
    const e = this.env();
    return e && e.georef_lat !== null && e.georef_lon !== null ? { lat: e.georef_lat, lon: e.georef_lon } : null;
  }

  generateQuartier(): void {
    if (this.quartierBusy()) return;
    this.quartierBusy.set(true);
    this.quartierError.set('');
    this.quartierStatus.set('Reconstruction du quartier (LiDAR HD × BD TOPO)…');
    this.api.generateEnvironment({
      lat: this.genLat, lon: this.genLon, radius_m: this.envRadius,
      include_vegetation: this.includeVegetation, include_terrain: true,
      name: `Quartier ${this.genLat.toFixed(5)}, ${this.genLon.toFixed(5)}`,
    }).subscribe({
      next: (res) => this.poll((res as Job).id, this.quartierStatus,
        (job) => this.openEnvironment((job.result as unknown as { environment_id: number }).environment_id),
        (m) => { this.quartierBusy.set(false); this.quartierError.set(m); }),
      error: (err) => {
        this.quartierBusy.set(false);
        const e = err?.error ?? {};
        this.quartierError.set(e.radius_m?.[0] ?? e.lat?.[0] ?? e.lon?.[0] ?? e.detail ?? 'Échec du lancement.');
      },
    });
  }

  openEnvironment(id: number): void {
    this.quartierBusy.set(true);
    this.quartierStatus.set('Chargement de la vue 3D…');
    this.api.getEnvironment(id).subscribe({
      next: (res) => {
        this.env.set(res as EnvironmentMesh);
        this.selectedIds.set([]);
        this.quartierBusy.set(false);
        this.quartierStatus.set('');
        this.step.set('selection');
      },
      error: () => { this.quartierBusy.set(false); this.quartierError.set("Impossible d'ouvrir cet environnement."); },
    });
  }

  backToQuartier(): void {
    if (this.step() !== 'selection') return;
    this.step.set('quartier');
  }

  // ── Étape 2 : choisir le bâtiment dans la vue 3D ───────────────────────
  // Un clic ajoute ou retire un bâtiment : plusieurs emprises BD TOPO qui ne
  // forment qu'un seul bâtiment réel sont réunies en UNE enveloppe côté
  // serveur (murs intérieurs supprimés — voir observed_env.merge_objects).
  selectedIds = signal<number[]>([]);

  get selectedBuildings(): EnvironmentObject[] {
    const ids = this.selectedIds();
    return (this.env()?.objects ?? []).filter(o => ids.includes(o.id));
  }

  onObjectClick(obj: EnvironmentObject): void {
    if (this.step() !== 'selection' || obj.kind !== 'building' || obj.status === 'studied') return;
    const ids = this.selectedIds();
    this.selectedIds.set(ids.includes(obj.id) ? ids.filter(i => i !== obj.id) : [...ids, obj.id]);
    if (!this.buildingName.trim() || this.buildingName === this.autoName) {
      this.autoName = this.proposedName();
      this.buildingName = this.autoName;
    }
  }

  private autoName = '';

  private proposedName(): string {
    const b = this.selectedBuildings;
    if (!b.length) return '';
    const label = b.length > 1 ? `${b.length} bâtiments réunis` : (b[0].label || 'Bâtiment');
    return `${label} — ${this.env()?.name ?? ''}`.slice(0, 140);
  }

  buildingName = '';
  // Défaut vérifié en réel (2026-08-08) : le raffinement (geometry.refine_envelope)
  // propage la subdivision à tout le maillage connecté (murs/toiture/sol partagent
  // des arêtes dans un volume étanche) — pas de raffinement "murs seulement".
  // 2,0 m donne ~128 triangles/mur, largement assez fin pour un taux de vitrage
  // à quelques % près, avec une marge confortable.
  maxEdgeLength = 2.0;
  creating = signal(false);
  createError = signal('');
  buildingId = signal<number | null>(null);
  materials = signal<{ basis: string; assigned: { wall: string | null; roof: string | null; floor: string | null } } | null>(null);

  // Renouvellement d'air (entrée simplifiée) — un unique profil catalogue
  // appliqué au volume RÉEL du bâtiment, calculé sur son enveloppe une fois
  // créée (voir measureEnvelope). Reste une SUGGESTION pour Calcul 3D.
  ventilationProfiles = VENTILATION_PROFILES;
  selectedVentProfileId: string | null = null;

  get selectedVentProfile(): VentilationProfile | null {
    return this.ventilationProfiles.find(p => p.id === this.selectedVentProfileId) ?? null;
  }

  /** Mesurés sur l'enveloppe créée : emprise au sol (aire des triangles `sol`)
   * et volume EXACT d'un volume 2,5D, Σ aire projetée × hauteur au-dessus du
   * plancher de chaque triangle de toiture — juste aussi pour un toit en pente. */
  footprintM2 = signal<number | null>(null);
  volumeM3 = signal<number | null>(null);

  get estimatedVolumeM3(): number | null {
    return this.volumeM3();
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
    this.api.getEnvironments().subscribe({
      next: (envs) => this.environments.set(
        (envs as { id: number; name: string; n_objects: number }[]).filter(e => e.n_objects > 0)),
      error: () => {},
    });
  }

  createFromSelection(): void {
    const e = this.env();
    const ids = this.selectedIds();
    if (!e || !ids.length || !this.buildingName.trim() || this.creating()) return;
    this.creating.set(true);
    this.createError.set('');
    this.api.studyEnvironmentObjects(e.id, ids, this.buildingName.trim()).subscribe({
      next: (res) => {
        const r = res as { building: Building; environment: EnvironmentMesh;
          materials: { basis: string; assigned: { wall: string | null; roof: string | null; floor: string | null } } | null };
        this.buildingId.set(r.building.id);
        this.buildingName = r.building.name;
        this.env.set(r.environment);
        this.materials.set(r.materials);
        this.loadBuilding(r.building.id);
      },
      error: (err) => {
        this.creating.set(false);
        this.createError.set(err?.error?.detail ?? err?.error?.name?.[0] ?? 'Échec de la création du bâtiment.');
      },
    });
  }

  // ══ Étape 3 — Façades et vrais vitrages (Lot AK) ══════════════════════════
  // Plus de « proportion de petits triangles » : les baies sont détectées sur
  // les photos de rue (components/facade-panel) et insérées comme de vrais
  // rectangles de vitrage ; le bâtiment n'est subdivisé qu'ENSUITE, pour la
  // finesse de l'ombrage.
  building = signal<Building | null>(null);
  refineNote = signal('');
  continuing = signal(false);

  private loadBuilding(id: number): void {
    this.api.getBuilding(id).subscribe({
      next: (res) => {
        const b = res as Building;
        this.building.set(b);
        this.measureEnvelope(b.envelope.triangles, b.envelope.vertices);
        // Suggestion de ventilation, désormais connue du volume réel.
        if (this.suggestedDebitVentM3h !== null) {
          this.api.updateBuilding(id, {
            suggested_debit_vent_m3h: this.suggestedDebitVentM3h,
            suggested_eta_recup_vent: this.suggestedEtaRecupVent,
          }).subscribe({ error: () => {} });
        }
        this.creating.set(false);
        this.step.set('configuration');
      },
      error: () => {
        this.creating.set(false);
        this.createError.set('Échec du chargement du bâtiment créé.');
      },
    });
  }

  onFacadeBuildingChange(b: Building): void {
    this.building.set(b);
  }

  get hasGlazing(): boolean {
    return (this.building()?.envelope.triangles ?? []).some(t => (t.group ?? '').startsWith('vitrage_'));
  }

  /** Subdivision (finesse de l'ombrage), surface de référence, puis étape 4. */
  continueToShadow(): void {
    const id = this.buildingId();
    if (id === null || this.continuing()) return;
    this.continuing.set(true);
    this.createError.set('');
    if (this.surfaceRefM2 !== null) {
      this.api.updateBuilding(id, { surface_ref_m2: this.surfaceRefM2 }).subscribe({ error: () => {} });
    }
    this.refineWithFallback(id, this.maxEdgeLength);
  }

  /** Subdivision à la maille demandée ; si le bâtiment est trop grand pour la
   * limite de triangles (un collectif de 70 m ne passe pas à 2 m), la maille
   * est élargie automatiquement par paliers plutôt que de renvoyer
   * l'utilisateur régler lui-même un paramètre qu'il ne connaît pas. */
  private refineWithFallback(id: number, edge: number): void {
    this.api.refineBuildingMesh(id, edge).subscribe({
      next: (res) => {
        this.refineNote.set(edge > this.maxEdgeLength
          ? `Bâtiment trop grand pour une maille de ${this.maxEdgeLength} m : subdivisé à ${edge} m.` : '');
        this.building.set(res as Building);
        this.continuing.set(false);
        this.step.set('environnement');
      },
      error: (err) => {
        const next = Math.round(edge * 1.5 * 10) / 10;
        if (err?.status === 400 && next <= 8) {
          this.refineWithFallback(id, next);
          return;
        }
        this.continuing.set(false);
        this.createError.set(err?.error?.detail ?? 'Échec de la subdivision du maillage.');
      },
    });
  }

  private measureEnvelope(tris: Triangle[], vertices: number[][]): void {
    let floor = 0;
    let volume = 0;
    const base = Math.min(...vertices.map(v => v[2]));
    for (const t of tris) {
      if (t.group === 'sol') floor += t.area;
      else if ((t.group ?? '').startsWith('toiture')) {
        const zc = (vertices[t.v[0]][2] + vertices[t.v[1]][2] + vertices[t.v[2]][2]) / 3;
        volume += t.area * Math.abs(t.normal[2]) * (zc - base);
      }
    }
    this.footprintM2.set(Math.round(floor));
    this.volumeM3.set(Math.round(volume));
  }


  // ══ Étape 4 — Environnement voisin + ombrage ═══════════════════════════════
  // Le mode simplifié s'arrêtait au bâtiment et renvoyait sur Calcul 3D pour
  // « la météo et le calcul » — qu'il ne faisait ni l'un ni l'autre. Pire : un
  // bâtiment neuf a son ombrage marqué périmé, donc le calcul y était REFUSÉ.
  // Le parcours va désormais jusqu'au résultat, en simplifié.
  includeNeighbours = true;
  envBusy = signal(false);
  envStatus = signal('');
  envError = signal('');
  shadowReady = signal(false);

  private poll(jobId: number, status: WritableSignal<string>, onDone: (job: Job) => void,
               onError: (m: string) => void): void {
    const handle = setInterval(() => {
      this.api.getJob(jobId).subscribe({
        next: (res) => {
          const job = res as Job;
          status.set(job.message || `${job.progress}%`);
          if (job.status === 'DONE') { clearInterval(handle); onDone(job); }
          else if (job.status === 'ERROR') { clearInterval(handle); onError(job.message || 'Échec.'); }
        },
        error: () => { clearInterval(handle); onError('Suivi de la tâche interrompu.'); },
      });
    }, 2000);
  }

  /** L'environnement est déjà lié au bâtiment (il en est issu) : il ne reste
   * que le précalcul d'ombrage. Sans voisinage, on le délie d'abord — le
   * bâtiment ne se fait alors de l'ombre qu'à lui-même. */
  buildEnvironmentAndShadow(): void {
    const id = this.buildingId();
    if (id === null || this.envBusy()) return;
    this.envBusy.set(true);
    this.envError.set('');
    this.shadowReady.set(false);
    const environmentId = this.includeNeighbours ? (this.env()?.id ?? null) : null;
    this.api.updateBuilding(id, { environment_id: environmentId }).subscribe({
      next: () => this.launchPrecompute(id),
      error: () => { this.envBusy.set(false); this.envError.set("Échec de l'association du voisinage."); },
    });
  }

  private launchPrecompute(buildingId: number): void {
    this.envStatus.set("Calcul de l'ombrage…");
    this.api.precomputeShadows(buildingId).subscribe({
      next: (res) => this.poll((res as Job).id, this.envStatus,
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
    return this.footprintM2();
  }

  /** Récupère une année type puis lance le calcul, sans autre réglage : tous
   * les paramètres physiques sont dérivés de ce que l'assistant connaît déjà
   * (volume réel, profil de ventilation choisi à l'étape 2, orientation de
   * chaque triangle) — voir le texte de l'étape 5 pour la liste exacte. */
  runCalculation(): void {
    const id = this.buildingId();
    const c = this.site;
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
    // Lot AG : la ventilation suit la même occupation que le thermostat. Sans
    // ça, une école en vacances restait ventilée au débit d'occupation pendant
    // que ses consignes passaient en hors gel — or le débit est de loin le
    // premier poste de déperdition d'un bâtiment fermé.
    const occupancy = computeOccupancy(profile, calendar, absoluteHours);
    const debit = this.suggestedDebitVentM3h ?? 0;
    const eta = this.suggestedEtaRecupVent ?? 0;
    const planning = Array.from({ length: 24 }, () => ({
      debit_vent_m3h: debit, eta_recup_vent: eta, apports_internes_w: 0, volets_fermes: false,
    }));
    const planningFerme = Array.from({ length: 24 }, () => ({
      debit_vent_m3h: Math.round(debit * CLOSED_VENTILATION_FRACTION * 10) / 10,
      eta_recup_vent: eta, apports_internes_w: 0, volets_fermes: false,
    }));

    this.api.runBuildingCalcul(buildingId, {
      dx_max: 0.02,
      h_e: 22, h_e_dynamic: true,
      interior: {
        mode: 'thermostat', h_i: 8, h_i_auto: true,
        c_air_int: Math.round(volume * 1200),
        t_min: 19, t_max: 26,
        debit_vent_m3h: debit,
        eta_recup_vent: eta,
        apports_internes_w: 0,
      },
      t_init: 15,
      weather: weather.map((w, i) => ({ ...w, ...setpoints[i], occupied: occupancy[i] })),
      planning, planning_ferme: planningFerme, heure_debut: 0,
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
}
