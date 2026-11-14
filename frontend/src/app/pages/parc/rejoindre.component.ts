import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { RefonteApiService } from '../../core/refonte-api.service';

/** Acceptation d'une invitation (refonte, Lot 11) : le code vient du lien, rien n'est saisi. */
@Component({
  selector: 'app-rejoindre',
  standalone: true,
  imports: [RouterLink],
  template: `<div class="rf-page"><p class="rf-eyebrow">Parc</p><h1>Invitation</h1>
    @if (error()) { <p class="rf-error" role="alert">{{ error() }}</p> <a routerLink="/parc">Mes parcs</a> }
    @else { <p class="rf-hint" role="status">Vérification de l'invitation…</p> }</div>`,
})
export class RejoindreComponent implements OnInit {
  private rf = inject(RefonteApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  error = signal('');

  ngOnInit(): void {
    const code = this.route.snapshot.queryParamMap.get('code') ?? '';
    this.rf.post<{ portfolio_id: number; building_id: number | null }>('/invitations/accepter/', { code }).subscribe({
      next: r => void (r.building_id
        ? this.router.navigate(['/fiche'], { queryParams: { b: r.building_id }, replaceUrl: true })
        : this.router.navigate(['/parc'], { queryParams: { p: r.portfolio_id }, replaceUrl: true })),
      error: e => this.error.set(e?.error?.detail ?? 'Invitation invalide.'),
    });
  }
}
