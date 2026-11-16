import { Component, OnDestroy, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ContexteService } from '../../core/contexte.service';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ApiService } from '../../core/api.service';
import { CarteComponent, MapPoint, MapPolygon } from '../../components/carte/carte.component';
import { ParcImportCsvComponent } from './parc-import-csv.component';
import { InitJob } from './parc.types';

/** Onglet Parc › Ajouter : par adresse (chaîne automatique suivie), par la carte, par un fichier CSV. */
@Component({
  selector: 'app-parc-ajouter',
  standalone: true,
  imports: [FormsModule, CarteComponent, ParcImportCsvComponent],
  styleUrl: './parc-commun.scss',
  template: `
    @if (!ctx.estGestionnaire()) {
      <p class="rf-hint" role="status">Seul un gestionnaire du parc peut y ajouter des bâtiments.</p>
    } @else {
      @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> }
      @if (info()) { <p class="rf-ok" role="status">{{ info() }}</p> }

      <section class="rf-card" aria-labelledby="aj-adr">
        <div class="rf-card-head"><h2 id="aj-adr">Par son adresse</h2></div>
        <p class="rf-hint">L'adresse est retrouvée dans la Base Adresse Nationale, puis le bâtiment est identifié, décrit depuis les
          données ouvertes et calculé, sans autre saisie. Comptez une à deux minutes.</p>
        <div class="rf-row">
          <label class="rf-field grow"><span>Adresse</span>
            <input type="text" [(ngModel)]="adresse" placeholder="12 rue de la Paix 75002 Paris" (keyup.enter)="initialiser()" [disabled]="suivi()"></label>
          <button type="button" class="rf-btn primary" (click)="initialiser()" [disabled]="adresse.trim().length < 3 || suivi()">Créer le bâtiment</button>
        </div>
        @if (job(); as j) {
          <div role="status">
            <div class="prog" role="progressbar" [attr.aria-valuenow]="j.progress" aria-valuemin="0" aria-valuemax="100" aria-label="Avancement"><i [style.width.%]="j.progress"></i></div>
            <p class="rf-hint">{{ j.status === 'DONE' ? 'Terminé.' : j.status === 'ERROR' ? 'Analyse interrompue : ' + (j.message || 'erreur') : (j.message || 'Analyse en cours…') }}</p>
            @if (nouveau() && (j.status === 'DONE' || j.status === 'ERROR')) {
              <button type="button" class="rf-btn" (click)="ouvrir()">Ouvrir le bâtiment</button>
            }
          </div>
        }
      </section>

      @if (!ctx.horsParc()) {
        <section class="rf-card" aria-labelledby="aj-carte">
          <div class="rf-card-head"><h2 id="aj-carte">Sur la carte</h2></div>
          <p class="rf-hint">Placez la carte sur le quartier, affichez les bâtiments de la zone, cliquez sur ceux à ajouter.</p>
          <app-carte [points]="existants()" [polygons]="polys()" (bboxChanged)="bbox = $event" (polygonClicked)="toggle($event)"
                     ariaLabel="Carte pour choisir des bâtiments à ajouter" />
          <div class="rf-row map-actions">
            <button type="button" class="rf-btn" (click)="chargerZone()">Afficher les bâtiments de la zone</button>
            @if (zone().length) {
              <span class="rf-hint">{{ zone().length }} bâtiment(s) dans la zone : cliquez pour sélectionner.</span>
              <button type="button" class="rf-btn primary" (click)="ajouterChoisis()" [disabled]="!choisis().size">Ajouter {{ choisis().size }} bâtiment(s) au parc</button>
            }
          </div>
        </section>

        <section class="rf-card" aria-labelledby="aj-csv">
          <div class="rf-card-head"><h2 id="aj-csv">Depuis un fichier</h2></div>
          <app-parc-import-csv />
        </section>
      } @else {
        <p class="rf-hint">L'ajout par la carte ou par un fichier se fait dans un parc.</p>
      }
    }
  `,
})
export class ParcAjouterComponent implements OnDestroy {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private api = inject(ApiService);
  private router = inject(Router);

  adresse = '';
  error = signal('');
  info = signal('');
  job = signal<InitJob | null>(null);
  nouveau = signal<number | null>(null);
  suivi = signal(false);
  zone = signal<(MapPolygon & { lat: number; lon: number })[]>([]);
  choisis = signal<Set<number | string>>(new Set());
  bbox: [number, number, number, number] | null = null;
  private timer?: ReturnType<typeof setInterval>;

  ngOnDestroy(): void { clearInterval(this.timer); }

  initialiser(): void {
    const a = this.adresse.trim();
    if (a.length < 3 || this.suivi()) return;
    this.error.set(''); this.info.set(''); this.job.set(null); this.nouveau.set(null);
    const parc = this.ctx.parcId();
    const body: Record<string, unknown> = { address: a };
    if (parc !== null && parc > 0) body['portfolio'] = parc;
    this.rf.post<{ building_id: number; job: InitJob }>('/batiments/initialiser/', body).subscribe({
      next: r => { this.job.set(r.job); this.nouveau.set(r.building_id); this.suivre(r.job.id); },
      error: e => this.error.set(e?.status === 403 ? 'Seul un gestionnaire du parc peut y créer un bâtiment.'
        : e?.error?.detail ?? e?.error?.non_field_errors?.[0] ?? e?.error?.address?.[0] ?? 'Création refusée.'),
    });
  }

  private suivre(jobId: number): void {
    clearInterval(this.timer);
    this.suivi.set(true);
    void this.ctx.rafraichirBatiments();
    this.timer = setInterval(() => {
      this.api.getJob(jobId).subscribe({
        next: x => {
          const j = x as InitJob;
          this.job.set(j);
          if (j.status === 'DONE' || j.status === 'ERROR') {
            clearInterval(this.timer); this.suivi.set(false);
            if (j.status === 'DONE') { this.info.set(`« ${this.adresse.trim()} » ajouté au parc.`); this.adresse = ''; }
            void this.ctx.rafraichirBatiments(); void this.ctx.rechargerParcs();
          }
        },
        error: () => { clearInterval(this.timer); this.suivi.set(false); this.error.set('Suivi de l’analyse impossible ; le bâtiment a peut-être été créé.'); },
      });
    }, 1500);
  }

  ouvrir(): void {
    const id = this.nouveau();
    if (!id) return;
    this.ctx.choisirBatiment(id);
    void this.router.navigate(['/batiment'], { queryParams: this.ctx.queryParams() });
  }

  existants(): MapPoint[] {
    return this.ctx.batiments().filter(r => r.lat !== null && r.lon !== null)
      .map(r => ({ id: r.id, lat: r.lat as number, lon: r.lon as number, label: (r.name || r.address) + ' (déjà dans le parc)' }));
  }

  chargerZone(): void {
    if (!this.bbox) return;
    const [lat_min, lon_min, lat_max, lon_max] = this.bbox;
    this.error.set('');
    this.rf.post<{ results: { id: number; lat: number; lon: number; ring: [number, number][]; area_m2: number }[] }>(
      '/carte/batiments/', { lat_min, lon_min, lat_max, lon_max }).subscribe({
      next: r => { this.zone.set(r.results.map(x => ({ id: x.id, ring: x.ring, lat: x.lat, lon: x.lon, label: `${x.area_m2} m² au sol` }))); this.choisis.set(new Set()); },
      error: e => this.error.set(e?.error?.detail ?? 'Bâtiments de la zone indisponibles (zoomez davantage).'),
    });
  }

  polys(): MapPolygon[] { const p = this.choisis(); return this.zone().map(a => ({ ...a, selected: p.has(a.id) })); }

  toggle(g: MapPolygon): void {
    const s = new Set(this.choisis());
    if (s.has(g.id)) s.delete(g.id); else s.add(g.id);
    this.choisis.set(s);
  }

  ajouterChoisis(): void {
    const id = this.ctx.parcId();
    if (!id || id < 0) return;
    const points = this.zone().filter(a => this.choisis().has(a.id)).map(a => ({ lat: a.lat, lon: a.lon }));
    this.rf.post<{ created: number[] }>(`/parcs/${id}/ajouter/`, { points }).subscribe({
      next: r => { this.info.set(`${r.created.length} bâtiment(s) ajouté(s) depuis la carte, analyse lancée.`); this.zone.set([]);
        void this.ctx.rafraichirBatiments(); void this.ctx.rechargerParcs(); },
      error: e => this.error.set(e?.error?.detail ?? 'Ajout refusé.'),
    });
  }
}
