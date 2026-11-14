import { AfterViewInit, Component, ElementRef, Input, OnChanges, OnDestroy, ViewChild, output } from '@angular/core';
import * as L from 'leaflet';

export interface MapPoint { id: number | string; lat: number; lon: number; color?: string; label?: string; html?: string; selected?: boolean; }
export interface MapPolygon { id: number | string; ring: [number, number][]; selected?: boolean; label?: string; }

/** Fond IGN (Géoplateforme, sans clé) : plan v2 et orthophotographies. Les tuiles sont des images publiques ;
 * aucun jeton n'y est joint (`tokenBases()` ne contient que l'API de l'app). */
const IGN = (layer: string, fmt: string) =>
  `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=${layer}&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=${fmt}`;

function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Carte (refonte, Lot 19 — U4). Couleurs : jetons Foyer lus à l'exécution (Leaflet ne lit pas les variables CSS). */
@Component({
  selector: 'app-carte',
  standalone: true,
  template: `<div #host class="map" role="application" [attr.aria-label]="ariaLabel"></div>`,
  styles: [`:host { display: block; } .map { width: 100%; height: var(--map-h, 420px); border-radius: var(--radius-sm); border: 1px solid var(--border); z-index: 0; }`],
})
export class CarteComponent implements AfterViewInit, OnChanges, OnDestroy {
  @ViewChild('host', { static: true }) host!: ElementRef<HTMLDivElement>;
  @Input() points: MapPoint[] = [];
  @Input() polygons: MapPolygon[] = [];
  @Input() center: [number, number] | null = null;
  @Input() zoom = 16;
  @Input() fit = true;
  @Input() ariaLabel = 'Carte';
  clicked = output<{ lat: number; lon: number }>();
  pointClicked = output<MapPoint>();
  polygonClicked = output<MapPolygon>();
  bboxChanged = output<[number, number, number, number]>();
  private map?: L.Map;
  private layer = L.layerGroup();
  private fitted = false;

  ngAfterViewInit(): void {
    this.map = L.map(this.host.nativeElement, { zoomControl: true, attributionControl: true })
      .setView(this.center ?? [46.6, 2.4], this.center ? this.zoom : 6);
    const plan = L.tileLayer(IGN('GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2', 'image/png'), { maxZoom: 19, attribution: '© IGN Géoplateforme' });
    const ortho = L.tileLayer(IGN('ORTHOIMAGERY.ORTHOPHOTOS', 'image/jpeg'), { maxZoom: 19, attribution: '© IGN Géoplateforme' });
    plan.addTo(this.map);
    L.control.layers({ 'Plan IGN': plan, 'Photographies aériennes': ortho }).addTo(this.map);
    this.layer.addTo(this.map);
    this.map.on('click', (e: L.LeafletMouseEvent) => this.clicked.emit({ lat: e.latlng.lat, lon: e.latlng.lng }));
    const emit = () => {
      const b = this.map!.getBounds();
      this.bboxChanged.emit([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]);
    };
    this.map.on('moveend', emit);
    this.draw();
    setTimeout(emit);  // zone initiale (aucun « moveend » à l'ouverture)
  }

  ngOnChanges(): void { this.draw(); }
  ngOnDestroy(): void { this.map?.remove(); }

  getBounds(): [number, number, number, number] | null {
    if (!this.map) return null;
    const b = this.map.getBounds();
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
  }

  private draw(): void {
    if (!this.map) return;
    this.layer.clearLayers();
    const accent = token('--accent', '#2a6df4'), mute = token('--text-mute', '#5b6172');
    for (const g of this.polygons) {
      const poly = L.polygon(g.ring, { color: g.selected ? accent : mute, weight: g.selected ? 3 : 1, fillOpacity: g.selected ? 0.35 : 0.12 });
      if (g.label) poly.bindTooltip(g.label);
      poly.on('click', (e: L.LeafletMouseEvent) => { L.DomEvent.stopPropagation(e); this.polygonClicked.emit(g); });
      poly.addTo(this.layer);
    }
    for (const p of this.points) {
      const m = L.circleMarker([p.lat, p.lon], { radius: p.selected ? 10 : 8, color: token('--card', '#fff'), weight: 2,
        fillColor: p.color ?? accent, fillOpacity: 0.95 });
      if (p.html) m.bindPopup(p.html); else if (p.label) m.bindTooltip(p.label);
      m.on('click', (e: L.LeafletMouseEvent) => { L.DomEvent.stopPropagation(e); this.pointClicked.emit(p); });
      m.addTo(this.layer);
    }
    const all: [number, number][] = [...this.points.map(p => [p.lat, p.lon] as [number, number]), ...this.polygons.flatMap(g => g.ring)];
    if (this.fit && !this.fitted && all.length) {
      const b = L.latLngBounds(all);
      this.map.fitBounds(b.pad(0.2), { maxZoom: 17 });
      this.fitted = true;
    } else if (this.center && !this.points.length && !this.fitted) {
      this.map.setView(this.center, this.zoom);
      this.fitted = true;
    }
  }
}
