import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { RefonteApiService } from '../../core/refonte-api.service';
import { ContexteService } from '../../core/contexte.service';
import { ContexteBarComponent } from '../../components/contexte-bar/contexte-bar.component';
import { SimulationComponent } from '../../components/simulation/simulation.component';
import { ResultatCarteComponent } from '../../components/resultat-carte/resultat-carte.component';
import { ScenariosComponent } from '../../components/scenarios/scenarios.component';
import { PotentielsComponent } from '../../components/scenarios/potentiels.component';
import { CalculsComponent } from '../../components/calculs/calculs.component';
import { MOTEURS_REPLI, Moteur, QUAND, TypeSimulation, VUES_CALCULS, VueCalculs } from './moteurs';

const TYPES: TypeSimulation[] = ['N0', 'N1', 'N2', '1D', '3D'];

/**
 * Onglet « Calculs » (chantier bt-navigation) : le catalogue des simulations et leur documentation,
 * puis, pour le bâtiment courant, LE point de lancement unique (une simulation à la fois, avec son
 * résultat), les améliorations comparées à l'état actuel, et l'historique des calculs.
 */
@Component({
  selector: 'app-onglet-calculs',
  standalone: true,
  imports: [RouterLink, ContexteBarComponent, SimulationComponent, ResultatCarteComponent, ScenariosComponent, PotentielsComponent, CalculsComponent],
  templateUrl: './onglet-calculs.component.html',
  styleUrl: './onglet-calculs.component.scss',
})
export class OngletCalculsComponent {
  protected ctx = inject(ContexteService);
  private rf = inject(RefonteApiService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  readonly vues = VUES_CALCULS;
  readonly quand = QUAND;
  readonly niveaux: ('N0' | 'N1' | 'N2')[] = ['N0', 'N1', 'N2'];

  private params = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  vue = computed<VueCalculs>(() => {
    const v = this.params().get('vue');
    if (VUES_CALCULS.some(x => x.id === v)) return v as VueCalculs;
    return this.ctx.batimentId() !== null ? 'simulation' : 'catalogue';
  });
  type = computed<TypeSimulation>(() => {
    const t = this.params().get('type') as TypeSimulation | null;
    return t && TYPES.includes(t) ? t : 'N1';
  });
  /** Niveau de la fiche (N0/N1/N2) quand la simulation choisie en est un, sinon null (outil expert). */
  niveau = computed(() => (['N0', 'N1', 'N2'] as string[]).includes(this.type()) ? this.type() as 'N0' | 'N1' | 'N2' : null);
  calculOuvert = computed(() => {
    const n = Number(this.params().get('calcul'));
    return Number.isInteger(n) && n > 0 ? n : null;
  });

  moteurs = signal<Moteur[]>(MOTEURS_REPLI);
  /** Vrai si le catalogue vient du serveur ; faux = texte de repli (route pas encore livrée). */
  moteursServeur = signal<boolean | null>(null);
  /** Résultat relu après chaque lancement. */
  version = signal(0);
  dernierCalcul = signal<number | null>(null);

  constructor() {
    this.rf.get<{ results: Moteur[] }>('/calculs/moteurs/').subscribe({
      next: r => {
        if (r?.results?.length) { this.moteurs.set(r.results); this.moteursServeur.set(true); }
        else this.moteursServeur.set(false);
      },
      error: () => this.moteursServeur.set(false),
    });
  }

  moteur(id: TypeSimulation): Moteur | undefined { return this.moteurs().find(m => m.id === id); }
  libelle(id: TypeSimulation): string { return this.moteur(id)?.libelle ?? MOTEURS_REPLI.find(m => m.id === id)?.libelle ?? id; }
  /** Libellé en mots, sigle entre parenthèses (jamais le sigle seul). */
  nom(id: TypeSimulation): string { return `${this.libelle(id)} (${id})`; }

  setVue(v: VueCalculs): void {
    void this.router.navigate([], { queryParams: { vue: v, calcul: null }, queryParamsHandling: 'merge' });
  }
  choisir(t: TypeSimulation): void {
    this.dernierCalcul.set(null);
    void this.router.navigate([], { queryParams: { vue: 'simulation', type: t, calcul: null }, queryParamsHandling: 'merge' });
  }

  onLance(runId: number): void {
    this.dernierCalcul.set(runId);
    this.version.update(v => v + 1);
    void this.ctx.rafraichirBatiments();
  }
}
