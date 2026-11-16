import { Component, computed, inject, input, output } from '@angular/core';
import { GRANDEURS, CAMERAS } from '../../components/vue3d-resultats/vue3d.types';
import { ChoixSeriesComponent } from './choix-series.component';
import { ChoixSimulationsComponent } from './choix-simulations.component';
import { ConstructeurGrapheComponent } from './constructeur-graphe.component';
import { Bloc, Graphe, NiveauSim, SECTIONS_FICHE, SOURCES, Series } from './contrat.types';
import { RenduEtatService } from './rendu-etat.service';

const NOMS_CAMERA: Record<string, string> = { iso: 'En biais', sud: 'Depuis le sud', nord: 'Depuis le nord', est: 'Depuis l’est', ouest: 'Depuis l’ouest', dessus: 'Du dessus' };

/** Réglages d'un bloc du document (I6.a `BLOC`), selon son type. */
@Component({
  selector: 'app-editeur-bloc',
  standalone: true,
  imports: [ChoixSeriesComponent, ChoixSimulationsComponent, ConstructeurGrapheComponent],
  templateUrl: './editeur-bloc.component.html',
  styleUrl: './editeur-bloc.component.scss',
})
export class EditeurBlocComponent {
  readonly bloc = input.required<Bloc>();
  readonly id = input('b');
  readonly blocChange = output<Bloc>();
  protected etat = inject(RenduEtatService);

  readonly SOURCES = SOURCES;
  readonly SECTIONS_FICHE = SECTIONS_FICHE;
  readonly GRANDEURS = GRANDEURS;
  readonly CAMERAS = CAMERAS;
  readonly NOMS_CAMERA = NOMS_CAMERA;

  /** Colonnes possibles d'un tableau : dimensions puis mesures de la portée. */
  readonly colonnesPossibles = computed(() => [
    ...this.etat.dimensionsPortee().map(d => ({ cle: d.cle, libelle: d.libelle })),
    ...this.etat.mesuresPortee().map(m => ({ cle: m.cle, libelle: m.unite ? `${m.libelle} (${m.unite})` : m.libelle })),
  ]);

  libelleColonne(cle: string): string { return this.colonnesPossibles().find(c => c.cle === cle)?.libelle ?? cle; }

  // Accès typés par type de bloc (le gabarit est découpé par @switch).
  b<T extends Bloc['type']>(_t: T): Extract<Bloc, { type: T }> { return this.bloc() as Extract<Bloc, { type: T }>; }

  poser(champ: string, v: unknown): void { this.blocChange.emit({ ...this.bloc(), [champ]: v } as Bloc); }
  liste(champ: string, cle: string, oui: boolean): void {
    const cur = ((this.bloc() as unknown as Record<string, unknown>)[champ] as string[] | undefined ?? []).filter(x => x !== cle);
    this.poser(champ, oui ? [...cur, cle] : cur);
  }
  coche(champ: string, cle: string): boolean {
    return (((this.bloc() as unknown as Record<string, unknown>)[champ] as string[] | undefined) ?? []).includes(cle);
  }
  graphe(g: Graphe): void { this.poser('graphe', g); }
  series(s: Series): void { this.poser('series', s); }
  /** Simulations du bloc : vide ⇒ champ retiré (la plus fine à jour, comme un document d'avant). */
  niveaux(n: NiveauSim[]): void {
    const { niveaux: _ancien, ...reste } = this.bloc() as Bloc & { niveaux?: NiveauSim[] };
    this.blocChange.emit((n.length ? { ...reste, niveaux: n } : reste) as Bloc);
  }
  /** Le serveur attend « reel » ou l'identifiant (nombre) du bouquet ; un `<select>` rend du texte. */
  serieCarte(v: string): void { this.poser('series', v === 'reel' ? 'reel' : Number(v)); }
  entier(v: string, min: number, max: number, repli: number): number {
    const n = Math.round(Number(v));
    return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : repli;
  }
  calcul(v: string): void {
    const t = v.trim();
    this.poser('calcul', t === '' || t === 'dernier_N2' || !/^\d+$/.test(t) ? 'dernier_N2' : Number(t));
  }
}
