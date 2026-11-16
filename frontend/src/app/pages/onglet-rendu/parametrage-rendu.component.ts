import { Component, inject, output, signal } from '@angular/core';
import { ContexteService } from '../../core/contexte.service';
import { EditeurBlocComponent } from './editeur-bloc.component';
import { ChoixSimulationsComponent } from './choix-simulations.component';
import { grapheAvecNiveaux } from './choix-niveau.component';
import { Bloc, Modele, NiveauSim, Spec, TYPES_BLOC, TypeBloc, blocNeuf } from './contrat.types';
import { RenduEtatService } from './rendu-etat.service';

/**
 * Page « Paramétrage du rendu » : choisir un modèle (gabarit du lab ou modèle du parc), régler le document
 * (titre, mise en page), ses sections et ses blocs, construire ses graphes. Un gestionnaire du parc enregistre ;
 * sinon le réglage n'est qu'un aperçu local.
 */
@Component({
  selector: 'app-parametrage-rendu',
  standalone: true,
  imports: [EditeurBlocComponent, ChoixSimulationsComponent],
  templateUrl: './parametrage-rendu.component.html',
  styleUrl: './parametrage-rendu.component.scss',
})
export class ParametrageRenduComponent {
  protected etat = inject(RenduEtatService);
  protected ctx = inject(ContexteService);
  readonly voirRendu = output<void>();

  readonly TYPES_BLOC = TYPES_BLOC;
  readonly ouvert = signal<string | null>(null);
  readonly nouveauNom = signal('');
  readonly confirmerSuppression = signal(false);
  /** Simulations à poser sur tous les blocs qui en affichent (réglage du document, lot 45). */
  readonly simulationsDoc = signal<NiveauSim[]>([]);
  readonly simulationsInfo = signal('');

  private sims(n?: NiveauSim[]): string { return n?.length ? ` · ${n.join(', ')}` : ''; }

  /** Pose les simulations choisies sur chaque chiffre clé, tableau, carte et graphe du document. */
  appliquerSimulations(): void {
    const n = this.simulationsDoc();
    let compte = 0;
    this.etat.modifier(s => {
      for (const sec of s.sections) {
        sec.blocs = sec.blocs.map(b => {
          if (b.type === 'graphe') { compte++; return { ...b, graphe: grapheAvecNiveaux(b.graphe, n) }; }
          if (b.type !== 'indicateurs' && b.type !== 'tableau' && b.type !== 'carte') return b;
          compte++;
          const { niveaux: _a, ...reste } = b;
          // Une carte ne montre qu'une simulation : la plus fine des choisies.
          const v = b.type === 'carte' ? n.slice(-1) : n;
          return (v.length ? { ...reste, niveaux: v } : reste) as Bloc;
        });
      }
    });
    this.simulationsInfo.set(compte ? `Appliqué à ${compte} bloc${compte > 1 ? 's' : ''}.` : 'Aucun bloc ne montre de résultat de simulation.');
  }

  libelleType(t: TypeBloc): string { return TYPES_BLOC.find(x => x.cle === t)?.libelle ?? t; }
  resumeBloc(b: Bloc): string {
    switch (b.type) {
      case 'texte': return b.contenu.slice(0, 80) || 'vide';
      case 'graphe': return b.graphe.titre || (b.graphe.y.length ? `${b.graphe.y.length} grandeur(s)` : 'à régler');
      case 'indicateurs': return `${b.cles.length} chiffre(s)${this.sims(b.niveaux)}`;
      case 'tableau': return `${b.colonnes.length} colonne(s), ${b.limite} lignes au plus${this.sims(b.niveaux)}`;
      case 'carte': return (b.indicateur || 'à régler') + this.sims(b.niveaux);
      case 'vue3d': return b.calcul === 'dernier_N2' ? 'dernier calcul détaillé' : `calcul n° ${b.calcul}`;
      case 'fiche': return `${b.sections.length} partie(s)`;
      default: return '';
    }
  }

  choisirModele(v: string): void {
    if (v === '__vide') { this.etat.documentVide(); return; }
    const id = Number(v);
    const { lab, parc } = this.etat.modeles();
    const m = [...parc, ...lab].find(x => x.id === id);
    if (m) this.etat.choisirModele(m);
  }
  origine(m: Modele | null): string {
    if (!m) return 'Document sans modèle';
    return m.portfolio === null ? 'Gabarit du lab' : 'Modèle du parc';
  }

  // ── Document ──
  mep<K extends keyof Spec['mise_en_page']>(k: K, v: Spec['mise_en_page'][K]): void { this.etat.modifier(s => { s.mise_en_page[k] = v; }); }
  titre(v: string): void { this.etat.modifier(s => { s.titre = v.slice(0, 200); }); }

  // ── Sections ──
  ajouterSection(): void { this.etat.modifier(s => { s.sections.push({ titre: `Section ${s.sections.length + 1}`, blocs: [] }); }); }
  titreSection(i: number, v: string): void { this.etat.modifier(s => { s.sections[i].titre = v.slice(0, 200); }); }
  deplacerSection(i: number, d: -1 | 1): void {
    this.etat.modifier(s => { const j = i + d; if (j < 0 || j >= s.sections.length) return; [s.sections[i], s.sections[j]] = [s.sections[j], s.sections[i]]; });
    this.ouvert.set(null);
  }
  supprimerSection(i: number): void { this.etat.modifier(s => { s.sections.splice(i, 1); }); this.ouvert.set(null); }

  // ── Blocs ──
  ajouterBloc(i: number, t: string): void {
    if (!t) return;
    let k = 0;
    this.etat.modifier(s => { s.sections[i].blocs.push(blocNeuf(t as TypeBloc)); k = s.sections[i].blocs.length - 1; });
    this.ouvert.set(`${i}-${k}`);
  }
  majBloc(i: number, k: number, b: Bloc): void { this.etat.modifier(s => { s.sections[i].blocs[k] = b; }); }
  deplacerBloc(i: number, k: number, d: -1 | 1): void {
    this.etat.modifier(s => { const l = s.sections[i].blocs, j = k + d; if (j < 0 || j >= l.length) return; [l[k], l[j]] = [l[j], l[k]]; });
    this.ouvert.set(null);
  }
  supprimerBloc(i: number, k: number): void { this.etat.modifier(s => { s.sections[i].blocs.splice(k, 1); }); this.ouvert.set(null); }
  basculer(cle: string): void { this.ouvert.update(o => (o === cle ? null : cle)); }

  // ── Enregistrement ──
  async enregistrer(): Promise<void> { await this.etat.enregistrer(null); }
  async enregistrerSous(): Promise<void> {
    const n = this.nouveauNom().trim();
    if (!n) return;
    await this.etat.enregistrer(n);
    this.nouveauNom.set('');
  }
  async supprimer(): Promise<void> { this.confirmerSuppression.set(false); await this.etat.supprimer(); }
}
