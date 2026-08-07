import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DecimalPipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { Building, Job } from '../../core/building.types';
import { MeshViewerComponent } from '../../components/mesh-viewer/mesh-viewer.component';
import { VENTILATION_PROFILES } from '../../core/ventilation-profiles';

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
}

interface BuildingCalculResult {
  hours: number;
  t_air_mean: number;
  heating_kwh: number;
  cooling_kwh: number;
  flux_positive_kwh: number;
  flux_negative_kwh: number;
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
  interiorMode: 'imposed' | 'free' | 'thermostat' = 'thermostat';
  hI = 8;
  tInt = 19;
  cAirInt = 200000;
  tMin = 19;
  tMax = 26;
  tInit = 15;
  shadowMode: 'precomputed' | 'realtime' = 'precomputed';
  // Lot K — température de sol constante, utilisée uniquement par les
  // triangles marqués 'ground' (page Bâtiment) ; sans effet sinon.
  tGround = 12;

  // ── Renouvellement d'air (modes 'free'/'thermostat' uniquement) ──────
  ventilationProfiles = VENTILATION_PROFILES;
  selectedVentProfileId: string | null = null;
  volumeM3 = 250;
  debitVentM3h = 0;
  etaRecupVent = 0;

  // ── Apports internes (modes 'free'/'thermostat' uniquement) ──────────
  apportsInternesW = 0;

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

  // ── Météo automatique (Lot L — Open-Meteo Archive + position solaire réelle) ──
  weatherFetchLat: number | null = null;
  weatherFetchLon: number | null = null;
  weatherFetchNorthOffset = 0;
  weatherFetchStart = this.isoDateDaysAgo(7);
  weatherFetchEnd = this.isoDateDaysAgo(0);
  weatherJob = signal<Job | null>(null);
  private weatherPollHandle?: ReturnType<typeof setInterval>;
  weatherFetchError = signal('');

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
  }

  ngOnDestroy(): void {
    this.stopPoll();
    this.stopWeatherPoll();
  }

  onBuildingChange(): void {
    this.job.set(null);
    this.currentBuilding.set(null);
    if (this.selectedBuildingId === null) return;
    this.loadingBuilding.set(true);
    this.api.getBuilding(this.selectedBuildingId).subscribe({
      next: (b) => {
        const building = b as Building;
        this.currentBuilding.set(building);
        this.loadingBuilding.set(false);
        // Pré-remplit le panneau météo auto depuis le géoréférencement du bâtiment
        // s'il existe — reste modifiable ensuite (bâtiment non géoréférencé, ou
        // météo d'un autre point que le bâtiment lui-même).
        if (building.georef_lat !== null && building.georef_lon !== null) {
          this.weatherFetchLat = building.georef_lat;
          this.weatherFetchLon = building.georef_lon;
          this.weatherFetchNorthOffset = building.georef_north_offset_deg;
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
      points.push({ t_ext, sun_azimuth, sun_elevation, e_dir, e_dif });
    }
    if (points.length === 0) {
      this.weatherError.set("Aucune ligne valide — format attendu : T_ext, azimuth_soleil, élévation_soleil, E_dir, E_dif");
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
  }

  loadConstant(): void {
    const w = this.constWeather;
    const row = [w.t_ext, w.sun_azimuth, w.sun_elevation, w.e_dir, w.e_dif].join(',');
    this.weatherRaw = Array(Math.max(1, Math.round(w.hours))).fill(row).join('\n');
    this.parseWeather();
  }

  // ── Météo automatique (Lot L) ──────────────────────────────────────────
  get canFetchWeather(): boolean {
    return this.weatherFetchLat !== null && this.weatherFetchLon !== null &&
      !!this.weatherFetchStart && !!this.weatherFetchEnd &&
      (this.weatherJob() === null || this.weatherJob()!.status === 'DONE' || this.weatherJob()!.status === 'ERROR');
  }

  fetchWeatherFromOpenMeteo(): void {
    if (!this.canFetchWeather || this.weatherFetchLat === null || this.weatherFetchLon === null) return;
    this.weatherFetchError.set('');
    this.api.fetchWeather({
      lat: this.weatherFetchLat, lon: this.weatherFetchLon,
      start_date: this.weatherFetchStart, end_date: this.weatherFetchEnd,
      north_offset_deg: this.weatherFetchNorthOffset,
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
              const points = (updated.result as unknown as { weather: WeatherPoint[] }).weather;
              this.weatherRaw = points
                .map(p => [p.t_ext, p.sun_azimuth, p.sun_elevation, p.e_dir, p.e_dif].join(','))
                .join('\n');
              this.parseWeather();
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
  get canSubmit(): boolean {
    const b = this.currentBuilding();
    return b !== null && this.weather().length > 0 &&
      (this.job() === null || this.job()!.status === 'DONE' || this.job()!.status === 'ERROR');
  }

  submit(): void {
    const building = this.currentBuilding();
    if (!building || !this.canSubmit) return;
    this.error.set('');

    const interior: Record<string, unknown> = { mode: this.interiorMode, h_i: this.hI };
    if (this.interiorMode === 'imposed') interior['t_int'] = this.tInt;
    if (this.interiorMode === 'free' || this.interiorMode === 'thermostat') {
      interior['c_air_int'] = this.cAirInt;
      interior['debit_vent_m3h'] = this.debitVentM3h;
      interior['eta_recup_vent'] = this.etaRecupVent;
      interior['apports_internes_w'] = this.apportsInternesW;
    }
    if (this.interiorMode === 'thermostat') { interior['t_min'] = this.tMin; interior['t_max'] = this.tMax; }

    const payload = {
      dx_max: this.dxMax, h_e: this.hE, interior, t_init: this.tInit, weather: this.weather(),
      shadow_mode: this.shadowMode, t_ground: this.tGround,
    };

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
