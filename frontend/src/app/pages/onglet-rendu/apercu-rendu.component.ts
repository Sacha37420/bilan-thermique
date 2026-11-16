import { Component, DestroyRef, effect, inject, output, signal, untracked } from '@angular/core';
import { BlocResoluComponent } from './bloc-resolu.component';
import { DocumentResolu, ExportRendu } from './contrat.types';
import { RenduApiService, messageDe, telecharger } from './rendu-api.service';
import { RenduEtatService } from './rendu-etat.service';

const FORMATS: { cle: 'html' | 'docx' | 'pdf'; libelle: string; aide: string }[] = [
  { cle: 'html', libelle: 'HTML', aide: 'page autonome, graphes et vue 3D interactifs, lisible hors connexion' },
  { cle: 'docx', libelle: 'Word', aide: 'titres, sommaire, vrais tableaux, figures en images' },
  { cle: 'pdf', libelle: 'PDF', aide: 'imprimable, avec signets, sommaire et numéros de page' },
];

/**
 * Page « Rendu » : aperçu du document tel que le serveur le construit (I6.c) — le même que celui des exports —
 * et exports HTML / Word / PDF (I6.d) avec suivi du statut et téléchargement.
 */
@Component({
  selector: 'app-apercu-rendu',
  standalone: true,
  imports: [BlocResoluComponent],
  templateUrl: './apercu-rendu.component.html',
  styleUrl: './apercu-rendu.component.scss',
})
export class ApercuRenduComponent {
  protected etat = inject(RenduEtatService);
  private api = inject(RenduApiService);
  readonly parametrer = output<void>();
  readonly FORMATS = FORMATS;

  readonly doc = signal<DocumentResolu | null>(null);
  readonly docErreur = signal('');
  readonly chargement = signal(false);
  private demande = 0;

  readonly exports = signal<ExportRendu[]>([]);
  readonly exportsErreur = signal('');
  readonly lancement = signal<string | null>(null);
  /** Exports lancés depuis cette page : téléchargés d'eux-mêmes quand ils sont prêts. */
  private auto = new Set<number>();
  private minuteur?: ReturnType<typeof setInterval>;

  constructor() {
    effect(() => {
      const src = this.etat.source(), c = this.etat.cible();
      untracked(() => void this.charger(src, c));
    });
    void this.chargerExports();
    inject(DestroyRef).onDestroy(() => clearInterval(this.minuteur));
  }

  async charger(src = this.etat.source(), c = this.etat.cible()): Promise<void> {
    const n = ++this.demande;
    this.docErreur.set('');
    if (!src || !c) { this.doc.set(null); this.chargement.set(false); return; }
    this.chargement.set(true);
    try {
      const d = await this.api.document(src, c);
      if (n === this.demande) this.doc.set(d);
    } catch (e) {
      if (n === this.demande) { this.doc.set(null); this.docErreur.set(messageDe(e, 'Aperçu du document')); }
    } finally {
      if (n === this.demande) this.chargement.set(false);
    }
  }

  aller(ancre: string): void {
    document.getElementById(`rendu-${ancre}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  imprimer(): void { window.print(); }

  // ── Exports ──
  private async chargerExports(): Promise<void> {
    try {
      this.exports.set(await this.api.exports());
      this.exportsErreur.set('');
      this.suivre();
    } catch (e) {
      this.exportsErreur.set(messageDe(e, 'Exports'));
    }
  }

  async exporter(f: 'html' | 'docx' | 'pdf'): Promise<void> {
    const src = this.etat.source(), c = this.etat.cible();
    if (!src || !c) return;
    this.lancement.set(f); this.exportsErreur.set('');
    try {
      const x = await this.api.lancerExport(src, c, f);
      this.auto.add(x.id);
      this.exports.update(l => [x, ...l.filter(y => y.id !== x.id)]);
      this.suivre();
    } catch (e) {
      this.exportsErreur.set(messageDe(e, 'Export'));
    } finally {
      this.lancement.set(null);
    }
  }

  private enCours(): ExportRendu[] { return this.exports().filter(x => x.statut === 'en_attente' || x.statut === 'en_cours'); }

  private suivre(): void {
    if (this.minuteur || !this.enCours().length) return;
    this.minuteur = setInterval(() => void this.relire(), 2000);
  }

  private async relire(): Promise<void> {
    const l = this.enCours();
    if (!l.length) { clearInterval(this.minuteur); this.minuteur = undefined; return; }
    for (const x of l) {
      try {
        const n = await this.api.etatExport(x.id);
        this.exports.update(t => t.map(y => (y.id === n.id ? n : y)));
        if (n.statut === 'fait' && this.auto.delete(n.id)) void this.telecharger(n);
        if (n.statut === 'erreur') this.auto.delete(n.id);
      } catch {
        this.exports.update(t => t.map(y => (y.id === x.id ? { ...y, statut: 'erreur', message: 'Suivi de l’export impossible.' } : y)));
        this.auto.delete(x.id);
      }
    }
  }

  async telecharger(x: ExportRendu): Promise<void> {
    try {
      const blob = await this.api.fichierExport(x.id);
      telecharger(blob, x.nom_fichier || `rendu-${x.id}.${x.format}`);
    } catch (e) {
      this.exportsErreur.set(messageDe(e, 'Téléchargement'));
    }
  }

  libelleFormat(f: string): string { return FORMATS.find(x => x.cle === f)?.libelle ?? f; }
  libelleStatut(s: string): string {
    return ({ en_attente: 'en attente', en_cours: 'en préparation', fait: 'prêt', erreur: 'échec' } as Record<string, string>)[s] ?? s;
  }
  taille(o: number | undefined): string {
    if (!o) return '';
    return o >= 1e6 ? `${(o / 1e6).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} Mo` : `${Math.max(1, Math.round(o / 1e3))} ko`;
  }
  heure(iso: string | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
}
