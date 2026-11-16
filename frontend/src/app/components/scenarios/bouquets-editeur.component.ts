import { Component, Input, OnChanges, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NgTemplateOutlet } from '@angular/common';
import { AmeliorationsApiService } from './ameliorations-api.service';
import {
  Action, Bouquet, BouquetsParc, CatalogueActions, ElementAction, ElementBouquet, OBJECTIFS, Objectif, Parametre,
  ValeurParametre, estUneParmi,
} from './ameliorations.types';

interface Brouillon { id: number | null; nom: string; description: string; objectif: Objectif; elements: ElementBouquet[]; }

/** Bouquets d'améliorations d'un parc (I7) : liste, copie des bouquets du lab, éditeur guidé. */
@Component({
  selector: 'app-bouquets-editeur',
  standalone: true,
  imports: [FormsModule, NgTemplateOutlet],
  templateUrl: './bouquets-editeur.component.html',
  styleUrl: './bouquets-editeur.component.scss',
})
export class BouquetsEditeurComponent implements OnChanges {
  @Input({ required: true }) parcId!: number;
  private api = inject(AmeliorationsApiService);
  readonly OBJECTIFS = OBJECTIFS;
  readonly estUneParmi = estUneParmi;

  data = signal<BouquetsParc | null>(null);
  catalogue = signal<CatalogueActions | null>(null);
  edition = signal<Brouillon | null>(null);
  /** Élément « une parmi » qui reçoit les actions ajoutées (null = ajout en fin de liste). */
  cibleAjout = signal<number | null>(null);
  erreur = signal('');
  info = signal('');
  busy = signal(false);

  ngOnChanges(): void {
    this.edition.set(null); this.data.set(null); this.catalogue.set(null); this.erreur.set(''); this.info.set('');
    this.charger();
    const p = this.parcId;
    this.api.actions(p).subscribe({
      next: c => { if (p === this.parcId) this.catalogue.set(c); },
      error: e => this.echec(e, 'Catalogue des actions indisponible.'),
    });
  }

  charger(): void {
    const p = this.parcId;
    this.api.bouquetsParc(p).subscribe({
      next: r => { if (p === this.parcId) this.data.set(r); },
      error: e => this.echec(e, 'Bouquets indisponibles.'),
    });
  }
  private echec(e: { error?: { detail?: string } }, repli: string): void {
    this.busy.set(false);
    this.erreur.set(e?.error?.detail ?? repli);
  }

  ecrire = computed(() => !!this.data()?.peut_ecrire);
  duLab = computed(() => (this.data()?.bouquets ?? []).filter(b => b.portfolio === null));
  duParc = computed(() => (this.data()?.bouquets ?? []).filter(b => b.portfolio !== null));
  private parCle = computed(() => new Map((this.catalogue()?.actions ?? []).map(a => [a.cle, a])));
  familles = computed(() => {
    const c = this.catalogue();
    if (!c) return [];
    return c.familles.map(f => ({ ...f, actions: c.actions.filter(a => a.famille === f.cle) })).filter(f => f.actions.length);
  });

  action(cle: string): Action | undefined { return this.parCle().get(cle); }
  libelle(cle: string): string { return this.action(cle)?.libelle ?? cle; }
  objectif(o: string): string { return OBJECTIFS.find(x => x.cle === o)?.libelle ?? o; }
  sousActions(e: ElementBouquet): ElementAction[] { return estUneParmi(e) ? e.une_parmi : [e]; }
  parametres(cle: string): Parametre[] { return this.action(cle)?.parametres ?? []; }

  /** Valeur lisible d'un paramètre (lecture). */
  affiche(p: Parametre, v: ValeurParametre | undefined): string {
    if (v === null || v === undefined || v === '') return '—';
    if (p.type === 'bool') return v ? 'oui' : 'non';
    if (p.type === 'enum') return p.choix?.[String(v)] ?? String(v);
    return `${typeof v === 'number' ? v.toLocaleString('fr-FR') : v}${p.unite ? ' ' + p.unite : ''}`;
  }
  choix(p: Parametre): [string, string][] { return Object.entries(p.choix ?? {}); }

  /** Éléments qui visent une même cible (refusé par le serveur). */
  conflits = computed(() => {
    const b = this.edition();
    if (!b) return [];
    const vu = new Map<string, number>();
    const out: string[] = [];
    b.elements.forEach((e, i) => {
      const cs = new Set(this.sousActions(e).flatMap(a => this.action(a.action)?.cibles ?? []));
      for (const c of cs) {
        const j = vu.get(c);
        if (j !== undefined) out.push(`Éléments ${j + 1} et ${i + 1} : même cible « ${c} »`);
        else vu.set(c, i);
      }
    });
    return out;
  });

  // ── Édition ──
  nouveau(): void {
    this.edition.set({ id: null, nom: '', description: '', objectif: 'energie', elements: [] });
    this.cibleAjout.set(null); this.info.set(''); this.erreur.set('');
  }
  modifier(b: Bouquet): void {
    this.edition.set({ id: b.id, nom: b.nom, description: b.description, objectif: b.objectif,
      elements: JSON.parse(JSON.stringify(b.elements)) });
    this.cibleAjout.set(null); this.info.set(''); this.erreur.set('');
  }
  fermer(): void { this.edition.set(null); this.cibleAjout.set(null); }
  private maj(f: (b: Brouillon) => void): void {
    const b = this.edition();
    if (!b) return;
    const n = { ...b, elements: [...b.elements] };
    f(n);
    this.edition.set(n);
  }
  champ(k: 'nom' | 'description' | 'objectif', v: string): void { this.maj(b => { (b as unknown as Record<string, string>)[k] = v; }); }

  ajouter(a: Action): void {
    if (a.desactivee) return;
    const el: ElementAction = { action: a.cle, parametres: Object.fromEntries(a.parametres.map(p => [p.cle, p.defaut])) };
    const g = this.cibleAjout();
    this.maj(b => {
      const cible = g !== null ? b.elements[g] : undefined;
      if (cible && estUneParmi(cible)) b.elements[g!] = { une_parmi: [...cible.une_parmi, el] };
      else b.elements.push(el);
    });
  }
  ajouterGroupe(): void {
    this.maj(b => b.elements.push({ une_parmi: [] }));
    this.cibleAjout.set(this.edition()!.elements.length - 1);
  }
  deplacer(i: number, d: -1 | 1): void {
    this.maj(b => {
      const j = i + d;
      if (j < 0 || j >= b.elements.length) return;
      [b.elements[i], b.elements[j]] = [b.elements[j], b.elements[i]];
    });
    const g = this.cibleAjout();
    if (g === i) this.cibleAjout.set(i + d); else if (g === i + d) this.cibleAjout.set(i);
  }
  retirer(i: number): void {
    this.maj(b => b.elements.splice(i, 1));
    const g = this.cibleAjout();
    if (g === i) this.cibleAjout.set(null); else if (g !== null && g > i) this.cibleAjout.set(g - 1);
  }
  retirerDuGroupe(i: number, k: number): void {
    this.maj(b => {
      const e = b.elements[i];
      if (estUneParmi(e)) b.elements[i] = { une_parmi: e.une_parmi.filter((_, x) => x !== k) };
    });
  }
  /** Pose un paramètre d'une action (k = rang dans le groupe « une parmi », sinon 0). */
  poser(i: number, k: number, p: Parametre, brut: unknown): void {
    let v: ValeurParametre;
    if (p.type === 'bool') v = !!brut;
    else if (p.type === 'enum') v = String(brut);
    else {
      const n = Number(String(brut).trim().replace(',', '.'));
      if (String(brut).trim() === '' || !Number.isFinite(n)) return;
      v = p.type === 'int' ? Math.round(n) : n;
    }
    this.maj(b => {
      const e = b.elements[i];
      const set = (a: ElementAction): ElementAction => ({ ...a, parametres: { ...a.parametres, [p.cle]: v } });
      b.elements[i] = estUneParmi(e) ? { une_parmi: e.une_parmi.map((a, x) => (x === k ? set(a) : a)) } : set(e);
    });
  }
  valeur(a: ElementAction, p: Parametre): ValeurParametre { return p.cle in a.parametres ? a.parametres[p.cle] : p.defaut; }

  enregistrer(): void {
    const b = this.edition();
    if (!b || !b.nom.trim()) { this.erreur.set('Donnez un nom au bouquet.'); return; }
    if (b.elements.some(e => estUneParmi(e) && !e.une_parmi.length)) {
      this.erreur.set('Un élément « une parmi » est vide : ajoutez-y des actions ou retirez-le.'); return;
    }
    const corps = { nom: b.nom.trim(), description: b.description, objectif: b.objectif, elements: b.elements };
    this.busy.set(true); this.erreur.set('');
    const req = b.id === null ? this.api.creerBouquet(this.parcId, corps) : this.api.modifierBouquet(b.id, corps);
    req.subscribe({
      next: r => { this.busy.set(false); this.fermer(); this.info.set(`Bouquet « ${r.nom} » enregistré.`); this.charger(); },
      error: e => this.echec(e, 'Enregistrement refusé.'),
    });
  }
  copier(b: Bouquet): void {
    this.busy.set(true); this.erreur.set('');
    this.api.copierBouquet(this.parcId, b.id).subscribe({
      next: r => { this.busy.set(false); this.info.set(`Copié dans le parc : « ${r.nom} ».`); this.charger(); },
      error: e => this.echec(e, 'Copie refusée.'),
    });
  }
  supprimer(b: Bouquet): void {
    if (!confirm(`Supprimer le bouquet « ${b.nom} » ?`)) return;
    this.api.supprimerBouquet(b.id).subscribe({
      next: () => { if (this.edition()?.id === b.id) this.fermer(); this.charger(); },
      error: e => this.echec(e, 'Suppression refusée.'),
    });
  }
}
