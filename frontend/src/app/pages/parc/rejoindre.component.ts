import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ContexteService } from '../../core/contexte.service';

/** Acceptation d'une invitation (refonte, Lot 11) : le code vient du lien, rien n'est saisi.
 * Arrivée : l'onglet Bâtiment (bâtiment confié) ou l'onglet Parc (parc entier), contexte posé par `?p=&b=`. */
@Component({
  selector: 'app-rejoindre',
  standalone: true,
  imports: [RouterLink],
  template: `<div class="rf-page"><p class="rf-eyebrow">Parc</p><h1>Invitation</h1>
    @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> <a class="rf-btn" routerLink="/parc">Mes parcs</a> }
    @else { <p class="rf-hint" role="status">Vérification de l'invitation…</p> }</div>`,
})
export class RejoindreComponent implements OnInit {
  private rf = inject(RefonteApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private ctx = inject(ContexteService);
  error = signal('');

  ngOnInit(): void {
    const code = this.route.snapshot.queryParamMap.get('code') ?? '';
    this.rf.post<{ portfolio_id: number; building_id: number | null }>('/invitations/accepter/', { code }).subscribe({
      // Le parc rejoint n'était pas dans la liste chargée au démarrage : on la relit avant d'y aller.
      next: async r => { await this.ctx.rechargerParcs(); void (r.building_id
        ? this.router.navigate(['/batiment'], { queryParams: { p: r.portfolio_id, b: r.building_id }, replaceUrl: true })
        : this.router.navigate(['/parc'], { queryParams: { p: r.portfolio_id }, replaceUrl: true })); },
      error: e => this.error.set(e?.error?.detail ?? 'Invitation invalide.'),
    });
  }
}
