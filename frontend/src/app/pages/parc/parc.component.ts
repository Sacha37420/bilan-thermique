import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DecimalPipe, KeyValuePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { RefonteApiService } from '../../core/refonte-api.service';
import { apiUrl, labEnv } from '../../core/env';
import { ParcRow, PortfolioInfo, ROLE_LABELS, USAGE_LABELS } from '../../core/refonte.types';
import { CarteComponent, MapPoint, MapPolygon } from '../../components/carte/carte.component';

type SortKey = 'name' | 'usage' | 'surface_m2' | 'heating_kwh_m2' | 'hours_over_28' | 'confidence' | 'completeness' | 'priority' | 'dpe';
interface Members {
  members: { id: number; name: string; role: string; me: boolean }[];
  accesses: { id: number; name: string; role: string; building_id: number; building: string }[];
  invitations: { id: number; prefix: string; role: string; building_id: number | null; uses: number; max_uses: number; active: boolean }[];
}

/** Parcs (refonte, Lot 11) : import, analyse automatique, tableau priorisé, export, partage par invitation. */
@Component({
  selector: 'app-parc',
  standalone: true,
  imports: [FormsModule, RouterLink, DecimalPipe, KeyValuePipe, CarteComponent],
  templateUrl: './parc.component.html',
  styleUrl: './parc.component.scss',
})
export class ParcComponent implements OnInit, OnDestroy {
  private rf = inject(RefonteApiService);
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  portfolios = signal<PortfolioInfo[]>([]);
  current = signal<PortfolioInfo | null>(null);
  rows = signal<ParcRow[]>([]);
  error = signal('');
  info = signal('');
  newName = '';
  usageLabels = USAGE_LABELS;
  roleLabels = ROLE_LABELS;

  sortKey = signal<SortKey>('priority');
  sortDesc = signal(true);
  filterUsage = signal('');
  filterStatus = signal('');
  search = signal('');

  members = signal<Members | null>(null);
  inviteRole = 'lecteur';
  inviteBuilding: number | null = null;
  inviteCode = signal('');
  private timer?: ReturnType<typeof setInterval>;

  // ── Lot 19 : carte du parc ──
  view_ = signal<'table' | 'carte'>('table');
  colorBy = signal<'besoin' | 'confiance' | 'chaleur' | 'etiquette'>('besoin');
  area = signal<(MapPolygon & { lat: number; lon: number })[]>([]);
  picked = signal<Set<number | string>>(new Set());
  private bbox: [number, number, number, number] | null = null;

  private tok(n: string): string { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

  colorOf(r: ParcRow): string {
    const ok = this.tok('--success'), mid = this.tok('--warning'), bad = this.tok('--danger'), none = this.tok('--text-mute');
    switch (this.colorBy()) {
      case 'besoin': return r.heating_kwh_m2 === null ? none : r.heating_kwh_m2 <= 80 ? ok : r.heating_kwh_m2 <= 180 ? mid : bad;
      case 'confiance': return r.confidence < 0.35 ? bad : r.confidence < 0.65 ? mid : ok;
      case 'chaleur': return r.hours_over_28 === null ? none : r.hours_over_28 < 50 ? ok : r.hours_over_28 < 300 ? mid : bad;
      default: { const l = r.model_label ?? r.dpe; return !l ? none : 'AB'.includes(l) ? ok : 'CD'.includes(l) ? mid : bad; }
    }
  }

  legend(): [string, string][] {
    const ok = this.tok('--success'), mid = this.tok('--warning'), bad = this.tok('--danger');
    switch (this.colorBy()) {
      case 'besoin': return [[ok, '≤ 80 kWh/m²'], [mid, '80–180'], [bad, '> 180']];
      case 'confiance': return [[bad, '< 35 %'], [mid, '35–65 %'], [ok, '> 65 %']];
      case 'chaleur': return [[ok, '< 50 h > 28 °C'], [mid, '50–300 h'], [bad, '> 300 h']];
      default: return [[ok, 'A–B'], [mid, 'C–D'], [bad, 'E–G']];
    }
  }

  mapPoints(): MapPoint[] {
    return this.view().filter(r => r.lat !== null && r.lon !== null).map(r => ({
      id: r.id, lat: r.lat as number, lon: r.lon as number, color: this.colorOf(r), label: r.name,
      html: `<strong>${this.esc(r.name)}</strong><br>${r.heating_kwh_m2 !== null ? Math.round(r.heating_kwh_m2) + ' kWh/m²·an' : 'non calculé'}`
        + ` · confiance ${Math.round(r.confidence * 100)} %<br><a href="fiche?b=${r.id}">Ouvrir la fiche</a>`,
    }));
  }

  private esc(s: string): string { return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string)); }

  onBbox(b: [number, number, number, number]): void { this.bbox = b; }

  loadArea(): void {
    if (!this.bbox) return;
    const [lat_min, lon_min, lat_max, lon_max] = this.bbox;
    this.error.set('');
    this.rf.post<{ results: { id: number; lat: number; lon: number; ring: [number, number][]; area_m2: number }[] }>(
      '/carte/batiments/', { lat_min, lon_min, lat_max, lon_max }).subscribe({
      next: r => { this.area.set(r.results.map(x => ({ id: x.id, ring: x.ring, lat: x.lat, lon: x.lon, label: `${x.area_m2} m²` }))); this.picked.set(new Set()); },
      error: e => this.error.set(e?.error?.detail ?? 'Emprises indisponibles.'),
    });
  }

  polys(): MapPolygon[] { const p = this.picked(); return this.area().map(a => ({ ...a, selected: p.has(a.id) })); }

  togglePoly(g: MapPolygon): void {
    const s = new Set(this.picked());
    if (s.has(g.id)) s.delete(g.id); else s.add(g.id);
    this.picked.set(s);
  }

  addPicked(): void {
    const p = this.current();
    if (!p) return;
    const points = this.area().filter(a => this.picked().has(a.id)).map(a => ({ lat: a.lat, lon: a.lon }));
    this.rf.post<{ created: number[] }>(`/parcs/${p.id}/ajouter/`, { points }).subscribe({
      next: r => { this.info.set(`${r.created.length} bâtiment(s) ajouté(s) depuis la carte, analyse lancée.`); this.area.set([]); this.refresh(); },
      error: e => this.error.set(e?.error?.detail ?? 'Ajout refusé.'),
    });
  }

  view = computed(() => {
    const k = this.sortKey(), d = this.sortDesc() ? -1 : 1, u = this.filterUsage(), st = this.filterStatus();
    const q = this.search().trim().toLowerCase();
    return this.rows()
      .filter(r => (!u || r.usage === u) && (!st || r.status === st) &&
        (!q || r.name.toLowerCase().includes(q) || (r.address || '').toLowerCase().includes(q)))
      .sort((a, b) => {
        const va = a[k] as number | string | null, vb = b[k] as number | string | null;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return (va < vb ? -1 : va > vb ? 1 : 0) * d;
      });
  });

  isManager = computed(() => this.current()?.role === 'gestionnaire_parc');
  progress = computed(() => {
    const c = this.current()?.counts;
    if (!c || !c.total) return 0;
    return Math.round(((c.complet + c.incomplet) / c.total) * 100);
  });

  ngOnInit(): void {
    this.loadList();
    this.route.queryParamMap.subscribe(p => {
      const id = Number(p.get('p'));
      if (id > 0) this.open(id); else { this.current.set(null); this.rows.set([]); }
    });
    this.timer = setInterval(() => {
      const c = this.current()?.counts;
      if (c && (c.en_attente || c.en_cours)) this.refresh();
    }, 5000);
  }

  ngOnDestroy(): void { clearInterval(this.timer); }

  loadList(): void {
    this.rf.get<{ results: PortfolioInfo[] }>('/parcs/').subscribe({ next: r => this.portfolios.set(r.results) });
  }

  select(id: number): void { void this.router.navigate([], { queryParams: { p: id } }); }

  open(id: number): void {
    this.members.set(null);
    this.inviteCode.set('');
    this.refresh(id);
  }

  refresh(id = this.current()?.id): void {
    if (!id) return;
    this.rf.get<{ portfolio: PortfolioInfo; rows: ParcRow[] }>(`/parcs/${id}/tableau/`).subscribe({
      next: r => {
        const wasBusy = !!(this.current()?.counts.en_attente || this.current()?.counts.en_cours);
        this.current.set(r.portfolio); this.rows.set(r.rows);
        if (wasBusy !== !!(r.portfolio.counts.en_attente || r.portfolio.counts.en_cours) || !wasBusy) this.loadList();
      },
      error: () => this.error.set('Parc introuvable.'),
    });
  }

  create(): void {
    const name = this.newName.trim();
    if (!name) return;
    this.rf.post<PortfolioInfo>('/parcs/', { name }).subscribe({
      next: p => { this.newName = ''; this.loadList(); this.select(p.id); },
      error: e => this.error.set(e?.error?.name?.[0] ?? e?.error?.detail ?? 'Création refusée.'),
    });
  }

  onFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    const p = this.current();
    if (!file || !p) return;
    if (file.size > 2 * 1024 * 1024) { this.error.set('Fichier trop volumineux (2 Mo au plus).'); return; }
    this.error.set('');
    file.text().then(text => {
      this.rf.post<{ created: number; errors: { line: number; error: string }[] }>(`/parcs/${p.id}/importer/`, { csv: text }).subscribe({
        next: r => {
          this.info.set(`${r.created} bâtiment(s) importé(s), analyse lancée.` +
            (r.errors.length ? ` ${r.errors.length} ligne(s) écartée(s) : ` + r.errors.slice(0, 3).map(e => `ligne ${e.line} (${e.error})`).join(', ') : ''));
          input.value = '';
          this.refresh();
        },
        error: e => this.error.set(e?.error?.detail ?? 'Import refusé.'),
      });
    });
  }

  launch(all = false): void {
    const p = this.current();
    if (!p) return;
    this.rf.post(`/parcs/${p.id}/lancer/`, { tout: all }).subscribe({ next: () => { this.info.set('Analyse lancée.'); this.refresh(); } });
  }

  exportCsv(): void {
    const p = this.current();
    if (!p) return;
    this.http.get(`${apiUrl()}/api/parcs/${p.id}/export.csv`, { responseType: 'blob' }).subscribe({
      next: blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `parc-${p.id}.csv`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
    });
  }

  sort(k: SortKey): void {
    if (this.sortKey() === k) this.sortDesc.update(d => !d); else { this.sortKey.set(k); this.sortDesc.set(k !== 'name' && k !== 'usage'); }
  }

  arrow(k: SortKey): string { return this.sortKey() === k ? (this.sortDesc() ? '↓' : '↑') : ''; }

  gauge(x: number): string { return x < 0.35 ? 'low' : x < 0.65 ? 'mid' : 'high'; }

  loadMembers(): void {
    const p = this.current();
    if (!p) return;
    this.rf.get<Members>(`/parcs/${p.id}/membres/`).subscribe({ next: m => this.members.set(m) });
  }

  invite(): void {
    const p = this.current();
    if (!p) return;
    const onBuilding = this.inviteRole === 'gestionnaire' || this.inviteRole === 'occupant';
    this.rf.post<{ code: string }>(`/parcs/${p.id}/invitations/`,
      { role: this.inviteRole, building_id: onBuilding ? this.inviteBuilding : null }).subscribe({
      next: r => { this.inviteCode.set(r.code); this.loadMembers(); },
      error: e => this.error.set(e?.error?.non_field_errors?.[0] ?? e?.error?.detail ?? 'Invitation refusée.'),
    });
  }

  inviteLink(): string {
    const base = labEnv().appUrl ?? (window.location.origin + '/');
    return `${base.replace(/\/?$/, '/')}parc/rejoindre?code=${encodeURIComponent(this.inviteCode())}`;
  }

  removeMember(id: number, type: 'membre' | 'acces' | 'invitation'): void {
    const p = this.current();
    if (!p) return;
    this.rf.del(`/parcs/${p.id}/membres/${id}/?type=${type}`).subscribe({
      next: () => this.loadMembers(), error: e => this.error.set(e?.error?.detail ?? 'Refusé.'),
    });
  }
}
