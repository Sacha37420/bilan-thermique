import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { ContexteService, HORS_PARC } from '../../core/contexte.service';
import { ErreurApi, RenduApiService, messageDe } from './rendu-api.service';
import { EtatCalculsService } from '../../components/resultat-carte/etat-calculs.service';
import { Bouquet, Cible, Dimension, Gabarits, Mesure, Modele, ModelesParc, Portee, Spec, specVide } from './contrat.types';

const CLE_BROUILLON = 'bt-rendu-brouillon';

interface Brouillon { modeleId: number | null; spec: Spec }

function lireBrouillon(cle: string): Brouillon | null {
  try {
    const s = localStorage.getItem(cle);
    if (!s) return null;
    const b = JSON.parse(s) as Brouillon;
    return b && b.spec && Array.isArray(b.spec.sections) ? b : null;
  } catch { return null; }
}
function ecrireBrouillon(cle: string, b: Brouillon | null): void {
  try { if (b) localStorage.setItem(cle, JSON.stringify(b)); else localStorage.removeItem(cle); } catch { /* stockage indisponible */ }
}

/**
 * État partagé des deux pages de l'onglet Rendu (paramétrage, rendu) : catalogues du serveur (gabarits,
 * dimensions, mesures, bouquets), modèles du parc, et la spécification en cours d'édition (I6.a).
 * Une modification non enregistrée reste un aperçu local (brouillon du navigateur, par parc et par portée).
 */
@Injectable()
export class RenduEtatService {
  private api = inject(RenduApiService);
  private ctx = inject(ContexteService);
  private calculs = inject(EtatCalculsService);

  readonly portee = signal<Portee>('parc');

  readonly gabarits = signal<Gabarits | null>(null);
  readonly gabaritsErreur = signal('');
  readonly dimensions = signal<Dimension[]>([]);
  readonly mesures = signal<Mesure[]>([]);
  readonly catalogueErreur = signal('');
  readonly modelesParc = signal<ModelesParc | null>(null);
  readonly modelesErreur = signal('');
  readonly bouquets = signal<Bouquet[]>([]);

  readonly modeleId = signal<number | null>(null);
  readonly spec = signal<Spec | null>(null);
  readonly modifie = signal(false);
  readonly info = signal('');
  readonly erreur = signal('');
  readonly enregistrement = signal(false);

  readonly parcReel = computed(() => { const p = this.ctx.parcId(); return p !== null && p !== HORS_PARC ? p : null; });
  readonly cible = computed<Cible | null>(() => {
    if (this.portee() === 'parc') { const p = this.parcReel(); return p === null ? null : { parc: p }; }
    const b = this.ctx.batimentId();
    return b === null ? null : { batiment: b };
  });
  readonly modeles = computed(() => {
    const p = this.portee();
    const lab = (this.gabarits()?.modeles ?? []).filter(m => m.portee === p);
    const parc = (this.modelesParc()?.modeles ?? []).filter(m => m.portee === p);
    return { lab, parc };
  });
  readonly modeleCourant = computed<Modele | null>(() => {
    const id = this.modeleId();
    if (id === null) return null;
    const { lab, parc } = this.modeles();
    return [...parc, ...lab].find(m => m.id === id) ?? null;
  });
  readonly peutEcrire = computed(() => !!this.modelesParc()?.peut_ecrire && this.parcReel() !== null);
  readonly questions = computed(() => (this.gabarits()?.questions ?? []).filter(q => q.portees.includes(this.portee())));
  readonly dimensionsPortee = computed(() => this.dimensions().filter(d => d.portees.includes(this.portee())));
  readonly mesuresPortee = computed(() => this.mesures().filter(m => m.portees.includes(this.portee())));
  /** Source à envoyer au serveur : le modèle tel qu'enregistré, ou la spécification modifiée. */
  readonly source = computed<{ spec: Spec } | { modele_id: number } | null>(() => {
    const id = this.modeleId(), s = this.spec();
    if (id !== null && !this.modifie()) return { modele_id: id };
    return s ? { spec: s } : null;
  });

  /** Incrémenté quand un calcul du parc (ou du bâtiment) courant se termine : aperçus et document se redemandent. */
  readonly actualisation = signal(0);
  /** Phrase d'avancement des calculs automatiques de la cible (vide quand tout est à jour). */
  readonly calculsEnCours = computed(() => {
    const c = this.cible();
    if (!c) return '';
    if ('parc' in c) {
      const r = this.calculs.parcs()[c.parc]?.etat?.resume;
      return r && r.calculs_restants ? `Calculs du parc en cours : ${r.calculs_restants} restant${r.calculs_restants > 1 ? 's' : ''}. L’aperçu se met à jour quand ils avancent.` : '';
    }
    const e = this.calculs.batiments()[c.batiment]?.etat;
    return e && e.calculs_restants ? `Calculs du bâtiment en cours : ${e.calculs_restants} restant${e.calculs_restants > 1 ? 's' : ''}. L’aperçu se met à jour quand ils avancent.` : '';
  });

  private parcCharge: number | null | undefined = undefined;
  private initialiseDe = '';

  constructor() {
    void this.chargerCatalogues();
    // Suivi de l'état des calculs de la cible (I2) le temps que l'onglet Rendu est ouvert.
    effect(onCleanup => {
      const c = this.cible();
      if (!c) return;
      onCleanup(untracked(() => ('parc' in c ? this.calculs.suivreParc(c.parc) : this.calculs.suivreBatiment(c.batiment))));
    });
    let avance = -1, de = '';
    effect(() => {
      const c = this.cible();
      const cle = c ? JSON.stringify(c) : '';
      const a = !c ? -1 : 'parc' in c ? this.calculs.parcs()[c.parc]?.avance ?? -1 : this.calculs.batiments()[c.batiment]?.avance ?? -1;
      untracked(() => {
        if (cle === de && avance >= 0 && a > avance) this.actualisation.update(v => v + 1);
        avance = a; de = cle;
      });
    });
    effect(() => {
      const p = this.parcReel();
      untracked(() => void this.chargerParc(p));
    });
    // Spécification initiale : brouillon local, sinon modèle par défaut (parc puis lab), sinon document vide.
    effect(() => {
      const cle = `${this.parcReel() ?? 'x'}-${this.portee()}`;
      const prets = this.gabarits() !== null || this.gabaritsErreur() !== '';
      const parcPret = this.parcReel() === null || this.modelesParc() !== null || this.modelesErreur() !== '';
      if (!prets || !parcPret || cle === this.initialiseDe) return;
      untracked(() => { this.initialiseDe = cle; this.initialiser(); });
    });
  }

  private cleBrouillon(): string { return `${CLE_BROUILLON}-${this.parcReel() ?? 'hors'}-${this.portee()}`; }

  private initialiser(): void {
    this.info.set(''); this.erreur.set('');
    const b = lireBrouillon(this.cleBrouillon());
    if (b && b.spec.portee === this.portee()) {
      this.modeleId.set(b.modeleId); this.spec.set(b.spec); this.modifie.set(true);
      return;
    }
    const { lab, parc } = this.modeles();
    const m = parc.find(x => x.par_defaut) ?? lab.find(x => x.par_defaut) ?? parc[0] ?? lab[0] ?? null;
    if (m) this.choisirModele(m);
    else { this.modeleId.set(null); this.spec.set(specVide(this.portee())); this.modifie.set(false); }
  }

  private async chargerCatalogues(): Promise<void> {
    const [g, d, m] = await Promise.allSettled([this.api.gabarits(), this.api.dimensions(), this.api.mesures()]);
    if (g.status === 'fulfilled') this.gabarits.set(g.value);
    else this.gabaritsErreur.set(messageDe(g.reason, 'Gabarits du lab'));
    if (d.status === 'fulfilled') this.dimensions.set(d.value);
    if (m.status === 'fulfilled') this.mesures.set(m.value);
    const e = [d, m].find(x => x.status === 'rejected') as PromiseRejectedResult | undefined;
    if (e) this.catalogueErreur.set(messageDe(e.reason, 'Dimensions et mesures'));
  }

  private async chargerParc(p: number | null): Promise<void> {
    if (p === this.parcCharge) return;
    this.parcCharge = p;
    this.modelesParc.set(null); this.modelesErreur.set(''); this.bouquets.set([]);
    if (p === null) return;
    const [m, b] = await Promise.allSettled([this.api.modelesParc(p), this.api.bouquetsParc(p)]);
    if (this.parcCharge !== p) return;
    if (m.status === 'fulfilled') this.modelesParc.set(m.value);
    else this.modelesErreur.set(messageDe(m.reason, 'Modèles du parc'));
    if (b.status === 'fulfilled') this.bouquets.set(b.value.bouquets);
  }

  choisirModele(m: Modele): void {
    this.modeleId.set(m.id);
    this.spec.set(structuredClone(m.spec));
    this.modifie.set(false);
    this.info.set(''); this.erreur.set('');
    ecrireBrouillon(this.cleBrouillon(), null);
  }

  documentVide(): void {
    this.modeleId.set(null);
    this.spec.set(specVide(this.portee()));
    this.modifie.set(true);
    this.memoriser();
  }

  /** Modifie la spécification en cours (sur une copie) et garde le brouillon local. */
  modifier(f: (s: Spec) => void): void {
    const s = this.spec();
    if (!s) return;
    const c = structuredClone(s);
    f(c);
    this.spec.set(c);
    this.modifie.set(true);
    this.info.set('');
    this.memoriser();
  }

  private memoriser(): void {
    const s = this.spec();
    if (s) ecrireBrouillon(this.cleBrouillon(), { modeleId: this.modeleId(), spec: s });
  }

  revenir(): void {
    const m = this.modeleCourant();
    if (m) this.choisirModele(m);
    else { ecrireBrouillon(this.cleBrouillon(), null); this.initialiseDe = ''; this.initialiser(); }
  }

  async enregistrer(nouveauNom: string | null): Promise<void> {
    const p = this.parcReel(), s = this.spec();
    if (p === null || !s || !this.peutEcrire()) return;
    this.enregistrement.set(true); this.erreur.set(''); this.info.set('');
    try {
      const courant = this.modeleCourant();
      let m: Modele;
      if (nouveauNom === null && courant?.modifiable) {
        m = await this.api.modifierModele(courant.id, { spec: s });
      } else {
        m = await this.api.creerModele(p, { nom: (nouveauNom || s.titre || 'Nouveau modèle').slice(0, 150), portee: this.portee(), spec: s });
      }
      await this.rechargerModeles(p);
      this.choisirModele(m);
      this.info.set(`Modèle « ${m.nom} » enregistré pour le parc.`);
    } catch (e) {
      this.erreur.set(messageDe(e, 'Enregistrement du modèle'));
    } finally {
      this.enregistrement.set(false);
    }
  }

  async supprimer(): Promise<void> {
    const m = this.modeleCourant(), p = this.parcReel();
    if (!m?.modifiable || p === null) return;
    this.enregistrement.set(true); this.erreur.set('');
    try {
      await this.api.supprimerModele(m.id);
      await this.rechargerModeles(p);
      this.modeleId.set(null);
      this.initialiseDe = ''; ecrireBrouillon(this.cleBrouillon(), null); this.initialiser();
      this.info.set(`Modèle « ${m.nom} » supprimé.`);
    } catch (e) {
      this.erreur.set(messageDe(e, 'Suppression du modèle'));
    } finally {
      this.enregistrement.set(false);
    }
  }

  private async rechargerModeles(p: number): Promise<void> {
    try { this.modelesParc.set(await this.api.modelesParc(p)); } catch (e) { if (!(e instanceof ErreurApi)) throw e; }
  }

  nomBouquet(id: number | string): string {
    return this.bouquets().find(b => String(b.id) === String(id))?.nom ?? `Bouquet ${id}`;
  }
}
