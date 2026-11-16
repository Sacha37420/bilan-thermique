import { Injectable, WritableSignal, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { apiUrl } from '../../core/env';
import { EtatCalculs, EtatParc, SuiviEtat, etatActif, parcActif } from './etat-calculs.types';

/** Intervalle de relecture (contrat bt-trois-niveaux § 7 : ≥ 5 s, arrêtée quand tout est à jour). */
export const RELECTURE_MS = 5000;
/** Un recalcul prévu loin dans le temps : on ne relit pas plus tôt que nécessaire, ni moins souvent que ceci. */
const ATTENTE_MAX_MS = 30000;

interface Suivi<T> {
  refs: number;
  minuteur: ReturnType<typeof setTimeout> | null;
  signature: string | null;
  enVol: boolean;
  url: string;
  actif: (e: T | null) => boolean;
  signer: (e: T) => string;
  delai: (e: T | null) => number;
}

function vide<T>(): SuiviEtat<T> { return { disponible: null, etat: null, erreur: '', avance: 0, actif: false }; }

function signerBatiment(e: EtatCalculs): string {
  // Un calcul qui passe « à jour » (ou dont le résultat change) fait avancer la vue.
  return e.calculs.filter(c => c.statut === 'a_jour').map(c => `${c.etat}:${c.niveau}:${c.run_id ?? ''}`).sort().join('|');
}
function signerParc(e: EtatParc): string {
  return `${e.resume.a_jour}/${e.resume.calculs_restants}/${e.resume.erreurs}`;
}
function delaiBatiment(e: EtatCalculs | null): number {
  if (e?.statut === 'planifie' && e.prevu_pour) {
    const dt = new Date(e.prevu_pour).getTime() - Date.now() + 1000;
    if (Number.isFinite(dt)) return Math.min(ATTENTE_MAX_MS, Math.max(RELECTURE_MS, dt));
  }
  return RELECTURE_MS;
}

/**
 * Suivi partagé de l'état des calculs automatiques (I2) : une seule relecture par bâtiment (ou par parc), quel que
 * soit le nombre de vues qui l'affichent ; elle s'arrête quand tout est à jour et quand plus aucune vue ne la suit.
 * Les vues lisent `batiments()[id]` / `parcs()[id]` et se relisent elles-mêmes quand `avance` change.
 */
@Injectable({ providedIn: 'root' })
export class EtatCalculsService {
  private http = inject(HttpClient);
  private get b(): string { return `${apiUrl()}/api`; }

  readonly batiments = signal<Record<number, SuiviEtat<EtatCalculs>>>({});
  readonly parcs = signal<Record<number, SuiviEtat<EtatParc>>>({});
  private sb = new Map<number, Suivi<EtatCalculs>>();
  private sp = new Map<number, Suivi<EtatParc>>();

  /** Suit l'état d'un bâtiment ; rend la fonction qui arrête ce suivi (à appeler en quittant la vue). */
  suivreBatiment(id: number): () => void {
    return this.abonner(this.sb, this.batiments, id, () => ({
      url: `/batiments/${id}/calculs/etat/`, actif: etatActif, signer: signerBatiment, delai: delaiBatiment,
    }));
  }
  suivreParc(id: number): () => void {
    return this.abonner(this.sp, this.parcs, id, () => ({
      url: `/parcs/${id}/calculs/etat/`, actif: parcActif, signer: signerParc, delai: () => RELECTURE_MS,
    }));
  }

  /** Relit tout de suite (après une modification ou un calcul lancé à la main) — sans effet si personne ne suit. */
  relireBatiment(id: number): void { const s = this.sb.get(id); if (s) this.lire(this.sb, this.batiments, id); }
  relireParc(id: number): void { const s = this.sp.get(id); if (s) this.lire(this.sp, this.parcs, id); }

  /** « Recalculer » (gestionnaire) : 202 + ETAT ; la relecture reprend. Rend un message d'erreur fixe, ou ''. */
  recalculer(id: number): Promise<string> {
    return new Promise(resolve => {
      this.http.post<EtatCalculs>(`${this.b}/batiments/${id}/calculs/recalculer/`, {}).subscribe({
        next: e => { this.recevoir(this.sb, this.batiments, id, e); resolve(''); },
        error: (err: HttpErrorResponse) => {
          if (err.status === 501) resolve('Le recalcul automatique n’est pas encore disponible sur ce serveur.');
          else if (err.status === 403) resolve(typeof err.error?.detail === 'string' ? err.error.detail : 'Action réservée aux gestionnaires de ce bâtiment.');
          else if (err.status === 0) resolve('Serveur injoignable.');
          else resolve('Recalcul refusé.');
        },
      });
    });
  }

  // ── mécanique commune ──
  private abonner<T>(m: Map<number, Suivi<T>>, sig: WritableSignal<Record<number, SuiviEtat<T>>>, id: number,
                     conf: () => Pick<Suivi<T>, 'url' | 'actif' | 'signer' | 'delai'>): () => void {
    let s = m.get(id);
    if (!s) {
      s = { refs: 0, minuteur: null, signature: null, enVol: false, ...conf() };
      m.set(id, s);
      if (!sig()[id]) sig.update(r => ({ ...r, [id]: vide<T>() }));
    }
    s.refs++;
    if (s.refs === 1) this.lire(m, sig, id);
    let libere = false;
    return () => {
      if (libere) return;
      libere = true;
      const x = m.get(id);
      if (!x) return;
      x.refs--;
      if (x.refs <= 0) {
        if (x.minuteur) clearTimeout(x.minuteur);
        m.delete(id);
        sig.update(r => { const c = { ...r }; if (c[id]) c[id] = { ...c[id], actif: false }; return c; });
      }
    };
  }

  private lire<T>(m: Map<number, Suivi<T>>, sig: WritableSignal<Record<number, SuiviEtat<T>>>, id: number): void {
    const s = m.get(id);
    if (!s || s.enVol) return;
    if (s.minuteur) { clearTimeout(s.minuteur); s.minuteur = null; }
    s.enVol = true;
    this.http.get<T>(`${this.b}${s.url}`).subscribe({
      next: e => { s.enVol = false; this.recevoir(m, sig, id, e); },
      error: (err: HttpErrorResponse) => {
        s.enVol = false;
        if (m.get(id) !== s) return;
        const indispo = err.status === 501;
        const erreur = indispo ? '' : err.status === 404 ? 'Bâtiment ou parc introuvable.' : err.status === 0 ? 'Serveur injoignable.' : 'État des calculs illisible.';
        sig.update(r => ({ ...r, [id]: { ...(r[id] ?? vide<T>()), disponible: indispo ? false : r[id]?.disponible ?? null, erreur, actif: false } }));
        // Erreur passagère (réseau, 5xx) : une nouvelle tentative plus tard ; 501 / 404 : on s'arrête.
        if (!indispo && err.status !== 404 && err.status !== 403) s.minuteur = setTimeout(() => this.lire(m, sig, id), ATTENTE_MAX_MS);
      },
    });
  }

  private recevoir<T>(m: Map<number, Suivi<T>>, sig: WritableSignal<Record<number, SuiviEtat<T>>>, id: number, e: T): void {
    const s = m.get(id);
    const prec = sig()[id] ?? vide<T>();
    let avance = prec.avance;
    const actif = s ? s.actif(e) : false;
    if (s) {
      const signature = s.signer(e);
      if (s.signature !== null && s.signature !== signature) avance++;
      // Fin d'une vague de calculs : une dernière relecture des vues, même si la signature ne bougeait plus.
      else if (prec.actif && !actif) avance++;
      s.signature = signature;
    }
    sig.update(r => ({ ...r, [id]: { disponible: true, etat: e, erreur: '', avance, actif } }));
    if (s && actif) {
      if (s.minuteur) clearTimeout(s.minuteur);
      s.minuteur = setTimeout(() => this.lire(m, sig, id), s.delai(e));
    }
  }
}
