import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { Building, Job } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';
import { VENTILATION_PROFILES } from '../../core/ventilation-profiles';
import {
  USAGE_PROFILES, UsageProfile, UsageProfileId, OccupationCalendar,
  defaultOccupationCalendar, computeThermostatSetpoints,
} from '../../core/usage-profiles';

interface BuildingSummary {
  id: number;
  name: string;
}

interface WeatherPoint {
  t_ext: number;
  sun_azimuth: number;
  sun_elevation: number;
  e_dir: number;
  e_dif: number;
  // Vent à 10 m (m/s, Lot R) — optionnel, alimenté automatiquement par le
  // fetch météo (Open-Meteo/PVGIS) ; requis heure par heure uniquement si
  // h_e dynamique est activé (voir hEDynamic ci-dessous).
  wind_m_s?: number;
  // Heure absolue de ce point (Lot AB1) : heures écoulées depuis minuit du
  // premier jour de la série, donc `% 24` = heure du jour. Alimentée par le
  // fetch météo depuis l'horodatage réel de chaque ligne, et transportée dans
  // la 7e colonne du CSV — pas dans un tableau parallèle : le fetch saute les
  // heures à donnée manquante et l'utilisateur peut supprimer des lignes, or
  // c'est très exactement ce genre d'opération qui désaligne un tableau
  // parallèle. Attachée à sa ligne, l'information reste juste quoi qu'il
  // arrive aux autres.
  hour_index?: number;
  // Consignes thermostat de cette heure (Lot V, calendrier d'occupation) —
  // fusionnées à la soumission depuis thermostatSetpoints(), jamais éditées
  // directement dans le CSV (voir generateThermostatCalendar()).
  t_min?: number;
  t_max?: number;
}

interface PlanningEntry {
  debit_vent_m3h: number;
  eta_recup_vent: number;
  apports_internes_w: number;
  // Lot J : UN SEUL planning volets pour tous les triangles ayant un
  // dispositif d'occultation (page Bâtiment) — s'applique dans TOUS les
  // modes intérieurs, contrairement aux trois champs ci-dessus.
  volets_fermes: boolean;
}

/** Bilan par poste au nœud d'air (Lot AB2) — `null` en mode « Imposée », où la
 * ligne du nœud d'air est écrasée par Dirichlet et où aucun de ces postes
 * n'agit sur la solution. */
interface EnergyBalance {
  envelope_kwh: number;
  solar_transmitted_kwh: number;
  internal_gains_kwh: number;
  ventilation_kwh: number;
  frames_kwh: number;
  hvac_kwh: number;
  storage_kwh: number;
  closure_error_kwh: number;
}

interface BuildingCalculResult {
  hours: number;
  t_air_mean: number;
  heating_kwh: number;
  cooling_kwh: number;
  flux_positive_kwh: number;
  flux_negative_kwh: number;
  balance: EnergyBalance | null;
  t_air: number[];
  envelope_flux_w: number[];
  final_exterior_surface_temp: number[];
  final_interior_surface_temp: number[];
}

const POLL_INTERVAL_MS = 2000;
const CHART_W = 960;
const CHART_H = 260;
const MARGIN = { top: 16, right: 16, bottom: 32, left: 46 };

// Échelle divergente bleu/rouge (skill dataviz) pour une grandeur continue
// (température) — distincte de la palette catégorielle des séries.
const COLD_HEX = '#2a78d6';
const HOT_HEX = '#e34948';
const MID_HEX = '#c9c7bd';

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function lerpColor(a: [number, number, number], b: [number, number, number], t: number): string {
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `#${[r, g, bl].map(x => x.toString(16).padStart(2, '0')).join('')}`;
}

@Component({
  selector: 'app-calcul-3d',
  standalone: true,
  imports: [FormsModule, RouterLink, DecimalPipe, MeshViewerComponent],
  templateUrl: './calcul-3d.component.html',
  styleUrl: './calcul-3d.component.scss',
})
export class Calcul3DComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  buildings = signal<BuildingSummary[]>([]);
  selectedBuildingId: number | null = null;
  currentBuilding = signal<Building | null>(null);
  loadingBuilding = signal(false);

  // ── Paramètres du calcul ─────────────────────────────────────────────
  dxMax = 0.02;
  hE = 22;
  // Lot R : h_e dérivé du vent (corrélation de Jürges) heure par heure plutôt
  // que la constante ci-dessus (alors ignorée) — exige wind_m_s sur CHAQUE
  // point météo (fetch auto Lot L/S l'alimente déjà ; CSV manuel : 6e colonne
  // optionnelle, voir parseWeather).
  hEDynamic = false;
  interiorMode: 'imposed' | 'free' | 'thermostat' = 'thermostat';
  hI = 8;
  // Lot R : h_i dérivé de l'orientation de chaque triangle (ISO 6946 —
  // mur/plafond/plancher) plutôt que la constante ci-dessus (alors ignorée).
  hIAuto = false;
  tInt = 19;
  cAirInt = 200000;
  tMin = 19;
  tMax = 26;
  tInit = 15;

  // ── Calendrier d'occupation (Lot V, mode thermostat uniquement) — dérive
  // t_min/t_max PAR HEURE à partir d'un profil d'usage (scolaire, tertiaire,
  // habitation, climatisés ou non) plutôt que les deux constantes ci-dessus,
  // qui restent le repli pour toute heure sans consigne générée. Résolu
  // entièrement côté client (voir core/usage-profiles.ts), fusionné dans la
  // météo à la soumission uniquement — jamais visible dans le CSV météo.
  usageProfiles = USAGE_PROFILES;
  selectedUsageProfileId: UsageProfileId | null = null;
  occupationCalendar: OccupationCalendar = defaultOccupationCalendar();
  readonly joursSemaine = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
  thermostatSetpoints = signal<{ t_min: number; t_max: number }[] | null>(null);

  get selectedUsageProfile(): UsageProfile | null {
    return this.usageProfiles.find(p => p.id === this.selectedUsageProfileId) ?? null;
  }

  addVacanceRange(): void {
    this.occupationCalendar.vacances.push({ debut: 0, fin: 0 });
  }

  removeVacanceRange(index: number): void {
    this.occupationCalendar.vacances.splice(index, 1);
  }

  /** Heure absolue de chaque point météo (Lot AB1) : `hour_index` porté par le
   * point quand la météo vient du fetch — dérivé de son horodatage réel, donc
   * juste même si des heures manquent — sinon repli sur la position dans la
   * liste, seul choix possible pour une série collée à la main. */
  private absoluteHours(): number[] {
    return this.weather().map((p, h) => p.hour_index ?? this.heureDebut + h);
  }

  /** Vrai si la série météo porte ses propres heures absolues : dans ce cas
   * `heureDebut` n'a plus d'effet (ni sur le planning côté serveur, ni sur ce
   * calendrier) et l'interface doit le dire plutôt que d'afficher un réglage
   * qui ne fait rien. */
  get weatherCarriesHours(): boolean {
    const points = this.weather();
    return points.length > 0 && points.every(p => p.hour_index !== undefined);
  }

  /** Aligne `jourDebut` (0 = lundi) sur la date de début effectivement chargée.
   * Sur une année type PVGIS, la date de repli reste le meilleur repère
   * disponible : une TMY n'a pas de vraie année, donc pas de vrai jour de la
   * semaine — c'est une convention, pas une donnée. */
  private syncCalendarStartDay(): void {
    const parsed = new Date(`${this.weatherFetchStart}T00:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return;
    // getUTCDay() : 0 = dimanche. Le calendrier attend 0 = lundi.
    this.occupationCalendar.jourDebut = (parsed.getUTCDay() + 6) % 7;
  }

  generateThermostatCalendar(): void {
    const profile = this.selectedUsageProfile;
    if (!profile || this.weather().length === 0) return;
    this.thermostatSetpoints.set(
      computeThermostatSetpoints(profile, this.occupationCalendar, this.absoluteHours()),
    );
  }
  shadowMode: 'precomputed' | 'realtime' = 'precomputed';

  // ── Association bâtiment ↔ environnement + précalcul d'ombrage (Lot AD) ──
  // Déplacés ici depuis la page Bâtiment : l'ombrage est la seule grandeur qui
  // dépend du COUPLE (bâtiment, environnement), et c'est ici que son absence
  // bloque — la page Bâtiment le proposait, Calcul 3D se contentait de refuser
  // en renvoyant l'utilisateur en arrière.
  environments = signal<{ id: number; name: string }[]>([]);
  selectedEnvironmentId: number | null = null;
  sunVisibilityStale = signal(true);
  shadowJob = signal<Job | null>(null);
  private shadowPollHandle?: ReturnType<typeof setInterval>;

  onEnvironmentChange(): void {
    const b = this.currentBuilding();
    if (!b) return;
    this.api.updateBuilding(b.id, { environment_id: this.selectedEnvironmentId }).subscribe({
      next: (res) => {
        // Changer d'environnement périme l'ombrage côté serveur : on relit
        // l'état plutôt que de le supposer.
        this.sunVisibilityStale.set((res as Building).sun_visibility_stale);
      },
      error: () => this.error.set("Échec de l'association de l'environnement."),
    });
  }

  triggerPrecompute(): void {
    const b = this.currentBuilding();
    if (!b) return;
    this.error.set('');
    this.api.precomputeShadows(b.id).subscribe({
      next: (res) => {
        this.shadowJob.set(res as Job);
        this.startShadowPoll(b.id);
      },
      error: (err) => this.error.set(err?.error?.detail ?? "Échec du lancement du précalcul."),
    });
  }

  private startShadowPoll(buildingId: number): void {
    this.stopShadowPoll();
    const job = this.shadowJob();
    if (!job) return;
    this.shadowPollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.shadowJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopShadowPoll();
            if (updated.status === 'DONE') this.sunVisibilityStale.set(false);
            else this.error.set(updated.message || "Échec du précalcul d'ombrage.");
            void buildingId;
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopShadowPoll(): void {
    if (this.shadowPollHandle) { clearInterval(this.shadowPollHandle); this.shadowPollHandle = undefined; }
  }
  // Lot K — température de sol constante, utilisée uniquement par les
  // triangles marqués 'ground' (page Bâtiment) ; sans effet sinon.
  tGround = 12;
  // Lot AB3 : résistance du sol lui-même sous les triangles « Sol ». Remplace
  // h_e pour eux — un dallage est en contact direct avec la terre, sans film
  // convectif, et depuis le Lot R son couplage se mettait à varier avec le vent.
  rGround = 0.5;

  // ── Renouvellement d'air (modes 'free'/'thermostat' uniquement) ──────
  ventilationProfiles = VENTILATION_PROFILES;
  selectedVentProfileId: string | null = null;
  volumeM3 = 250;
  debitVentM3h = 0;
  etaRecupVent = 0;

  // ── Apports internes (modes 'free'/'thermostat' uniquement) ──────────
  apportsInternesW = 0;

  // ── Planning horaire (Lot Q — variation AUTOUR des constantes ci-dessus,
  // pas un remplacement : actif, il prime heure par heure sur debit_vent_m3h/
  // eta_recup_vent/apports_internes_w constants) ─────────────────────────
  usePlanning = false;
  planningRaw = '';
  planning = signal<PlanningEntry[]>([]);
  planningError = signal('');
  heureDebut = 0;

  parsePlanning(): void {
    this.planningError.set('');
    const text = this.planningRaw.trim();
    if (!text) { this.planning.set([]); return; }
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    const entries: PlanningEntry[] = [];
    for (const line of lines) {
      const delim = line.includes(';') ? ';' : ',';
      const cells = line.split(delim).map(c => c.trim());
      if (cells.length < 3) continue;
      const nums = cells.slice(0, 3).map(c => Number(c.replace(',', '.')));
      if (nums.some(n => Number.isNaN(n))) continue;
      const [debit_vent_m3h, eta_recup_vent, apports_internes_w] = nums;
      // 4e colonne optionnelle (Lot J) : "1"/"true" -> fermé, tout le reste
      // (absent inclus) -> ouvert.
      const voletsCell = cells[3]?.toLowerCase();
      const volets_fermes = voletsCell === '1' || voletsCell === 'true';
      entries.push({ debit_vent_m3h, eta_recup_vent, apports_internes_w, volets_fermes });
    }
    if (entries.length !== 24) {
      this.planningError.set(
        `24 lignes attendues (une par heure de la journée) — ${entries.length} ligne(s) valide(s) trouvée(s).`,
      );
    }
    this.planning.set(entries);
  }

  loadPlanningExample(): void {
    // Profil "jour type" résidentiel plausible : creux nocturne, pointes
    // matin/soir, volets fermés la nuit — un point de départ à ajuster, pas
    // une vérité universelle.
    const rows: string[] = [];
    for (let h = 0; h < 24; h++) {
      const occupied = h < 7 || h >= 19; // présent tôt le matin et le soir
      const debit = this.debitVentM3h > 0 ? this.debitVentM3h * (occupied ? 1.0 : 0.5) : (occupied ? 60 : 30);
      const apports = occupied ? 400 : (h >= 7 && h < 19 ? 80 : 40);
      const voletsFermes = h < 7 || h >= 22; // fermés la nuit (avant 7h, après 22h)
      rows.push([debit.toFixed(1), this.etaRecupVent.toFixed(2), apports.toFixed(0), voletsFermes ? '1' : '0'].join(','));
    }
    this.planningRaw = rows.join('\n');
    this.parsePlanning();
  }

  // ── Aide au calcul de c_air_int (Lot P) — réutilise volumeM3 (même volume
  // d'air que le renouvellement d'air ci-dessous, pas un champ dupliqué) :
  // 1200 J/(m³·K), capacité thermique volumique usuelle de l'air à pression
  // normale.
  calcCAirFromVolume(): void {
    this.cAirInt = Math.round(this.volumeM3 * 1200);
  }

  applyVentProfile(): void {
    const profile = this.ventilationProfiles.find((p) => p.id === this.selectedVentProfileId);
    if (!profile) return;
    this.debitVentM3h = Math.round(profile.tauxRenouvellementVolH * this.volumeM3 * 10) / 10;
    this.etaRecupVent = profile.etaRecup;
  }

  ventProfileDescription(): string {
    return this.ventilationProfiles.find((p) => p.id === this.selectedVentProfileId)?.description ?? '';
  }

  // ── Météo ──────────────────────────────────────────────────────────
  weather = signal<WeatherPoint[]>([]);
  weatherRaw = '';
  weatherError = signal('');
  constWeather = { t_ext: 5, sun_azimuth: 180, sun_elevation: 0, e_dir: 0, e_dif: 0, hours: 200 };

  // ── Météo automatique (Lot L — Open-Meteo Archive ; Lot S — année type PVGIS) ──
  weatherFetchLat: number | null = null;
  weatherFetchLon: number | null = null;
  weatherFetchNorthOffset = 0;
  // 'tmy' (année type PVGIS, représentative) ou 'archive' (année réelle datée,
  // Open-Meteo) — les dates ci-dessous servent toujours de repli si PVGIS ne
  // couvre pas la zone en mode 'tmy' (voir to_do.md, Lot S).
  weatherFetchSource: 'archive' | 'tmy' = 'tmy';
  // Lot AB4 : décalage UTC -> heure locale appliqué aux heures de la série
  // (donc au planning horaire et au calendrier d'occupation), jamais à la
  // position solaire. null = détection automatique côté serveur ; rempli avec
  // la valeur effectivement utilisée après chaque récupération, donc
  // modifiable ensuite. Décalage FIXE : pas de changement d'heure.
  weatherFetchUtcOffsetH: number | null = null;
  weatherFetchStart = this.isoDateDaysAgo(7);
  weatherFetchEnd = this.isoDateDaysAgo(0);
  weatherJob = signal<Job | null>(null);
  private weatherPollHandle?: ReturnType<typeof setInterval>;
  weatherFetchError = signal('');
  // Source de la météo actuellement chargée dans weatherRaw — affichée aussi sur
  // le dashboard de résultats (to_do.md, Lot S étape 3 : ne jamais laisser la
  // source ambiguë). Vide = CSV manuel / origine inconnue, mise à jour à chaque
  // action qui remplit weatherRaw (fetch auto, exemple de démo, météo constante).
  weatherSourceLabel = signal('');
  // Capturée au lancement du calcul (submit()) — voir ce commentaire là-bas pour
  // pourquoi ce n'est pas simplement une lecture de weatherSourceLabel() dans le
  // template du dashboard.
  submittedWeatherSourceLabel = signal('');

  private isoDateDaysAgo(days: number): string {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }

  // ── Job / résultats ───────────────────────────────────────────────────
  job = signal<Job | null>(null);
  private pollHandle?: ReturnType<typeof setInterval>;
  error = signal('');

  colorMode: 'interior' | 'exterior' = 'interior';

  ngOnInit(): void {
    this.api.getBuildings().subscribe({
      next: (b) => this.buildings.set(b as BuildingSummary[]),
      error: () => {},
    });
    this.api.getEnvironments().subscribe({
      next: (e) => this.environments.set(e as { id: number; name: string }[]),
      error: () => {},
    });
  }

  ngOnDestroy(): void {
    this.stopPoll();
    this.stopWeatherPoll();
    this.stopShadowPoll();
  }

  onBuildingChange(): void {
    this.job.set(null);
    this.currentBuilding.set(null);
    this.shadowJob.set(null);
    this.stopShadowPoll();
    this.selectedEnvironmentId = null;
    this.sunVisibilityStale.set(true);
    if (this.selectedBuildingId === null) return;
    this.loadingBuilding.set(true);
    this.api.getBuilding(this.selectedBuildingId).subscribe({
      next: (b) => {
        const building = b as Building;
        this.currentBuilding.set(building);
        this.loadingBuilding.set(false);
        // Lot AD : l'association bâtiment ↔ environnement et l'état de l'ombrage
        // se pilotent désormais depuis cette page.
        this.selectedEnvironmentId = building.environment_id;
        this.sunVisibilityStale.set(building.sun_visibility_stale);
        // Pré-remplit le panneau météo auto depuis le géoréférencement du bâtiment
        // s'il existe — reste modifiable ensuite (bâtiment non géoréférencé, ou
        // météo d'un autre point que le bâtiment lui-même).
        if (building.georef_lat !== null && building.georef_lon !== null) {
          this.weatherFetchLat = building.georef_lat;
          this.weatherFetchLon = building.georef_lon;
          this.weatherFetchNorthOffset = building.georef_north_offset_deg;
        }
        // Même principe pour le renouvellement d'air (Lot T, mode simplifié) :
        // une suggestion calculée là-bas depuis un profil catalogue + le volume
        // réel du bâtiment, reprise ici comme point de départ — les deux champs
        // restent modifiables normalement, comme pour tout bâtiment.
        if (building.suggested_debit_vent_m3h !== null) {
          this.debitVentM3h = building.suggested_debit_vent_m3h;
        }
        if (building.suggested_eta_recup_vent !== null) {
          this.etaRecupVent = building.suggested_eta_recup_vent;
        }
      },
      error: () => {
        this.loadingBuilding.set(false);
        this.error.set('Impossible de charger ce bâtiment.');
      },
    });
  }

  // ── Météo : parsing / génération ─────────────────────────────────────
  parseWeather(): void {
    this.weatherError.set('');
    // Un appel direct (édition manuelle du CSV) invalide la provenance connue —
    // les appelants qui SAVENT d'où vient la série (fetch auto, démo) réaffectent
    // le label juste après cet appel, l'écrasant.
    this.weatherSourceLabel.set('');
    // Lot V : un calendrier de consignes déjà généré n'a de sens que pour la
    // LONGUEUR de série météo sur laquelle il a été calculé — invalidé à
    // chaque nouvelle météo plutôt que risquer une fusion décalée au moment
    // de la soumission.
    this.thermostatSetpoints.set(null);
    const text = this.weatherRaw.trim();
    if (!text) { this.weather.set([]); return; }
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    const points: WeatherPoint[] = [];
    for (const line of lines) {
      const delim = line.includes(';') ? ';' : ',';
      const cells = line.split(delim).map(c => c.trim());
      if (cells.length < 5) continue;
      const nums = cells.slice(0, 5).map(c => Number(c.replace(',', '.')));
      if (nums.some(n => Number.isNaN(n))) continue;
      const [t_ext, sun_azimuth, sun_elevation, e_dir, e_dif] = nums;
      const point: WeatherPoint = { t_ext, sun_azimuth, sun_elevation, e_dir, e_dif };
      // 6e colonne optionnelle (Lot R) : vent en m/s, requis heure par heure
      // uniquement si hEDynamic est activé — absente ou invalide, la ligne
      // reste valide, wind_m_s simplement non renseigné.
      if (cells.length >= 6 && cells[5] !== '') {
        // Number('') === 0 en JS (pas NaN) — la garde ci-dessus évite de
        // confondre "cellule vide" avec "vent nul".
        const wind = Number(cells[5].replace(',', '.'));
        if (!Number.isNaN(wind)) point.wind_m_s = wind;
      }
      // 7e colonne optionnelle (Lot AB1) : heure absolue depuis minuit du
      // premier jour. Même garde que pour le vent — `Number('')` vaut 0, ce qui
      // ferait passer une cellule vide pour « minuit du premier jour ».
      if (cells.length >= 7 && cells[6] !== '') {
        const hourIndex = Number(cells[6]);
        if (Number.isInteger(hourIndex) && hourIndex >= 0) point.hour_index = hourIndex;
      }
      points.push(point);
    }
    if (points.length === 0) {
      this.weatherError.set("Aucune ligne valide — format attendu : T_ext, azimuth_soleil, élévation_soleil, E_dir, E_dif[, vent_m_s][, heure_absolue]");
    }
    this.weather.set(points);
  }

  loadExample(hours: number): void {
    const rows: string[] = [];
    for (let h = 0; h < hours; h++) {
      const hourOfDay = h % 24;
      const t_ext = 8 + 8 * Math.sin(((hourOfDay - 9) / 24) * 2 * Math.PI);
      const daylight = Math.max(0, Math.sin(((hourOfDay - 6) / 12) * Math.PI));
      const sun_elevation = daylight > 0 ? 55 * daylight : -10;
      const sun_azimuth = 90 + 180 * Math.min(1, Math.max(0, (hourOfDay - 6) / 12));
      const e_dir = daylight > 0 ? 650 * daylight : 0;
      const e_dif = daylight > 0 ? 90 * daylight : 0;
      rows.push([t_ext.toFixed(1), sun_azimuth.toFixed(1), sun_elevation.toFixed(1), e_dir.toFixed(0), e_dif.toFixed(0)].join(','));
    }
    this.weatherRaw = rows.join('\n');
    this.parseWeather();
    this.weatherSourceLabel.set('Démonstration (sinusoïde synthétique)');
  }

  loadConstant(): void {
    const w = this.constWeather;
    const row = [w.t_ext, w.sun_azimuth, w.sun_elevation, w.e_dir, w.e_dif].join(',');
    this.weatherRaw = Array(Math.max(1, Math.round(w.hours))).fill(row).join('\n');
    this.parseWeather();
    this.weatherSourceLabel.set('Démonstration (météo constante)');
  }

  // ── Météo automatique (Lot L — Open-Meteo Archive ; Lot S — PVGIS TMY) ────
  get canFetchWeather(): boolean {
    return this.weatherFetchLat !== null && this.weatherFetchLon !== null &&
      !!this.weatherFetchStart && !!this.weatherFetchEnd &&
      (this.weatherJob() === null || this.weatherJob()!.status === 'DONE' || this.weatherJob()!.status === 'ERROR');
  }

  fetchWeatherFromOpenMeteo(): void {
    if (!this.canFetchWeather || this.weatherFetchLat === null || this.weatherFetchLon === null) return;
    this.weatherFetchError.set('');
    this.api.fetchWeather({
      lat: this.weatherFetchLat, lon: this.weatherFetchLon, source: this.weatherFetchSource,
      start_date: this.weatherFetchStart, end_date: this.weatherFetchEnd,
      north_offset_deg: this.weatherFetchNorthOffset,
      utc_offset_h: this.weatherFetchUtcOffsetH,
    }).subscribe({
      next: (res) => {
        this.weatherJob.set(res as Job);
        this.startWeatherPoll();
      },
      error: (err) => {
        this.weatherFetchError.set(err?.error?.detail ?? err?.error?.non_field_errors?.[0] ?? "Échec du lancement de la récupération.");
      },
    });
  }

  private startWeatherPoll(): void {
    this.stopWeatherPoll();
    const job = this.weatherJob();
    if (!job) return;
    this.weatherPollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.weatherJob.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') {
            this.stopWeatherPoll();
            if (updated.status === 'DONE') {
              const result = updated.result as unknown as {
                weather: WeatherPoint[]; source: 'pvgis-tmy' | 'open-meteo-archive';
                warning: string | null; utc_offset_h: number | null;
              };
              // Lot AB4 : on adopte le décalage effectivement utilisé (détecté
              // ou imposé) pour que le champ cesse d'être vide et reste
              // modifiable, et on aligne le jour de la semaine du calendrier
              // d'occupation sur la période réellement chargée — l'utilisateur
              // n'a plus à savoir quel jour tombait le 1er janvier.
              if (result.utc_offset_h !== null && result.utc_offset_h !== undefined) {
                this.weatherFetchUtcOffsetH = result.utc_offset_h;
              }
              this.syncCalendarStartDay();
              this.weatherRaw = result.weather
                .map(p => [
                  p.t_ext, p.sun_azimuth, p.sun_elevation, p.e_dir, p.e_dif,
                  p.wind_m_s ?? '', p.hour_index ?? '',
                ].join(','))
                .join('\n');
              this.parseWeather();
              const sourceLabel = result.source === 'pvgis-tmy'
                ? 'Année type (PVGIS TMY)'
                : `Année réelle (Open-Meteo Archive), ${this.weatherFetchStart} → ${this.weatherFetchEnd}`;
              this.weatherSourceLabel.set(result.warning ? `${sourceLabel} — ${result.warning}` : sourceLabel);
            } else {
              this.weatherFetchError.set(updated.message || 'Échec de la récupération.');
            }
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopWeatherPoll(): void {
    if (this.weatherPollHandle) { clearInterval(this.weatherPollHandle); this.weatherPollHandle = undefined; }
  }

  // ── Lancement + suivi ──────────────────────────────────────────────
  // Lot R : h_e_dynamique exige wind_m_s sur CHAQUE point météo (voir
  // BuildingCalculRequestSerializer côté backend) — vérifié ici pour bloquer
  // le bouton plutôt que de laisser échouer la requête.
  get weatherMissingWind(): number {
    return this.hEDynamic ? this.weather().filter(p => p.wind_m_s === undefined).length : 0;
  }

  get canSubmit(): boolean {
    const b = this.currentBuilding();
    return b !== null && this.weather().length > 0 && this.weatherMissingWind === 0 &&
      (!this.usePlanning || this.planning().length === 24) &&
      (this.job() === null || this.job()!.status === 'DONE' || this.job()!.status === 'ERROR');
  }

  submit(): void {
    const building = this.currentBuilding();
    if (!building || !this.canSubmit) return;
    this.error.set('');

    const interior: Record<string, unknown> = { mode: this.interiorMode, h_i: this.hI, h_i_auto: this.hIAuto };
    if (this.interiorMode === 'imposed') interior['t_int'] = this.tInt;
    if (this.interiorMode === 'free' || this.interiorMode === 'thermostat') {
      interior['c_air_int'] = this.cAirInt;
      interior['debit_vent_m3h'] = this.debitVentM3h;
      interior['eta_recup_vent'] = this.etaRecupVent;
      interior['apports_internes_w'] = this.apportsInternesW;
    }
    if (this.interiorMode === 'thermostat') { interior['t_min'] = this.tMin; interior['t_max'] = this.tMax; }

    // Calendrier d'occupation (Lot V) : fusionne t_min/t_max PAR HEURE dans la
    // météo envoyée si un calendrier a été généré pour CETTE série météo
    // exacte (longueur identique — sinon, décalage silencieux, on ignore le
    // calendrier périmé plutôt que d'envoyer des consignes désalignées).
    const calendar = this.thermostatSetpoints();
    const weatherPayload = (this.interiorMode === 'thermostat' && calendar && calendar.length === this.weather().length)
      ? this.weather().map((w, i) => ({ ...w, t_min: calendar[i].t_min, t_max: calendar[i].t_max }))
      : this.weather();

    const payload: Record<string, unknown> = {
      dx_max: this.dxMax, h_e: this.hE, h_e_dynamic: this.hEDynamic, interior, t_init: this.tInit,
      weather: weatherPayload, shadow_mode: this.shadowMode, t_ground: this.tGround, r_ground: this.rGround,
    };
    // Planning horaire (Lot Q) : n'est envoyé QUE s'il est actif et complet —
    // sinon comportement inchangé (constantes de `interior` ci-dessus).
    // Contrairement à debit_vent_m3h/apports_internes_w (ignorés hors
    // free/thermostat, voir building_solver.py), volets_fermes (Lot J)
    // s'applique dans TOUS les modes — pas de restriction sur interiorMode ici.
    if (this.usePlanning && this.planning().length === 24) {
      payload['planning'] = this.planning();
      payload['heure_debut'] = this.heureDebut;
    }

    // Capturé au moment du lancement (pas relu depuis weatherSourceLabel() dans le
    // template) : si l'utilisateur recharge une autre météo pendant que ce calcul
    // tourne, le dashboard doit continuer à afficher la source qui a VRAIMENT
    // produit ce résultat, pas celle actuellement dans le formulaire.
    this.submittedWeatherSourceLabel.set(this.weatherSourceLabel());

    this.api.runBuildingCalcul(building.id, payload).subscribe({
      next: (res) => {
        this.job.set(res as Job);
        this.startPoll();
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "Échec du lancement du calcul.");
      },
    });
  }

  private startPoll(): void {
    this.stopPoll();
    const job = this.job();
    if (!job) return;
    this.pollHandle = setInterval(() => {
      this.api.getJob(job.id).subscribe({
        next: (res) => {
          const updated = res as Job;
          this.job.set(updated);
          if (updated.status === 'DONE' || updated.status === 'ERROR') this.stopPoll();
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopPoll(): void {
    if (this.pollHandle) { clearInterval(this.pollHandle); this.pollHandle = undefined; }
  }

  get result(): BuildingCalculResult | null {
    const job = this.job();
    return job && job.status === 'DONE' ? (job.result as unknown as BuildingCalculResult) : null;
  }

  // ── Résultats normalisés (Lot M) — kWh/m²/an, seuls comparables aux seuils
  // RT2012/RE2020 déjà cités dans le catalogue de parois. Null tant que le
  // bâtiment n'a pas de surface_ref_m2 renseignée (page Bâtiment).
  get surfaceRefM2(): number | null {
    return this.currentBuilding()?.surface_ref_m2 ?? null;
  }

  get heatingKwhPerM2(): number | null {
    const r = this.result;
    const s = this.surfaceRefM2;
    return r && s ? r.heating_kwh / s : null;
  }

  get coolingKwhPerM2(): number | null {
    const r = this.result;
    const s = this.surfaceRefM2;
    return r && s ? r.cooling_kwh / s : null;
  }

  // ── Bilan par poste (Lot AB2) ────────────────────────────────────────
  // Les deux tuiles d'origine (« Flux entrant/sortant total ») ne portaient que
  // le canal « surfaces d'enveloppe » tout en s'appelant « gains »/« pertes »,
  // alors que quatre autres postes alimentent le même nœud d'air — dont le
  // solaire transmis par les vitrages, souvent le premier gain d'un bâtiment
  // vitré. Ordonné du plus au moins structurant, HVAC en dernier (c'est la
  // conséquence, pas une cause).
  get balanceRows(): { label: string; value: number; hint: string }[] {
    const b = this.result?.balance;
    if (!b) return [];
    return [
      { label: 'Surfaces d’enveloppe', value: b.envelope_kwh,
        hint: 'Conduction + convection à travers murs, toiture, plancher et vitrages.' },
      { label: 'Solaire transmis (vitrages)', value: b.solar_transmitted_kwh,
        hint: 'Rayonnement ayant traversé une paroi sans couche opaque — chauffe l’air directement.' },
      { label: 'Apports internes', value: b.internal_gains_kwh,
        hint: 'Occupants, éclairage, électroménager.' },
      { label: 'Renouvellement d’air', value: b.ventilation_kwh,
        hint: 'Infiltrations et VMC, récupération de chaleur déduite.' },
      { label: 'Cadres de fenêtre', value: b.frames_kwh,
        hint: 'Conductance directe du cadre, hors vitrage.' },
      { label: 'Chauffage / climatisation', value: b.hvac_kwh,
        hint: 'Ce qu’il a fallu fournir (positif) ou retirer (négatif) pour tenir les consignes.' },
    ];
  }

  /** Variation d'énergie stockée dans l'air et le mobilier (c_air_int) sur la
   * période : c'est ce que la somme des postes doit valoir exactement. */
  get balanceStorage(): number | null {
    return this.result?.balance?.storage_kwh ?? null;
  }

  /** Écart de bouclage. Nul à la précision numérique près par construction —
   * affiché quand même : c'est un contrôle de conservation d'énergie visible,
   * et une valeur non nulle signalerait une régression du solveur. */
  get balanceClosureError(): number | null {
    return this.result?.balance?.closure_error_kwh ?? null;
  }

  get balanceCloses(): boolean {
    const e = this.balanceClosureError;
    return e !== null && Math.abs(e) < 1e-6;
  }

  // ── Graphique T_air ──────────────────────────────────────────────────
  chartW = CHART_W;
  chartH = CHART_H;
  plotLeft = MARGIN.left;
  plotTop = MARGIN.top;
  plotWidth = CHART_W - MARGIN.left - MARGIN.right;
  plotHeight = CHART_H - MARGIN.top - MARGIN.bottom;

  get chartPath(): string {
    const r = this.result;
    if (!r) return '';
    const t = r.t_air;
    const n = t.length - 1;
    const min = Math.min(...t), max = Math.max(...t);
    const pad = (max - min) * 0.1 || 1;
    const lo = min - pad, hi = max + pad;
    const pts = t.map((v, h) => {
      const x = this.plotLeft + (n === 0 ? 0 : (h / n) * this.plotWidth);
      const y = this.plotTop + this.plotHeight - ((v - lo) / (hi - lo)) * this.plotHeight;
      return `${x},${y}`;
    });
    return 'M ' + pts.join(' L ');
  }

  // ── Couleur du viewer 3D (résultat) ───────────────────────────────────
  colorForResultTriangle = (index: number): string => {
    const r = this.result;
    if (!r) return '--border';
    const values = this.colorMode === 'interior' ? r.final_interior_surface_temp : r.final_exterior_surface_temp;
    const v = values[index];
    if (v === undefined) return '--border';
    const min = Math.min(...values), max = Math.max(...values);
    if (max - min < 0.01) return MID_HEX;
    const t = (v - min) / (max - min);
    return t < 0.5
      ? lerpColor(hexToRgb(COLD_HEX), hexToRgb(MID_HEX), t / 0.5)
      : lerpColor(hexToRgb(MID_HEX), hexToRgb(HOT_HEX), (t - 0.5) / 0.5);
  };

  resultTempRange(): { min: number; max: number } {
    const r = this.result;
    if (!r) return { min: 0, max: 0 };
    const values = this.colorMode === 'interior' ? r.final_interior_surface_temp : r.final_exterior_surface_temp;
    return { min: Math.min(...values), max: Math.max(...values) };
  }
}
