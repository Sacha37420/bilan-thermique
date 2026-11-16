"""Lignes de données d'un graphe ou d'un tableau : ce que le serveur lit, **lui seul** (I6.b).

Contexte = une portée (`parc` ou `batiment`) et l'utilisateur de la requête : seuls les bâtiments qu'il voit
(`access.visible_buildings`), seuls les bouquets du lab et ceux du parc du bâtiment.

Séries : « réel » = dernier calcul terminé de l'état actuel (I3, `CalcRun.indicators`) ; bouquet = calcul du
potentiel (`Potentiel.run`, I0), compté s'il est « fait » (à jour ou non, comme les agrégats I5.b).

Niveau (chantier bt-trois-niveaux, I5) : sans niveau précisé (ni dimension « niveau », ni filtre), chaque couple
(bâtiment, série) est lu au niveau **le plus fin disponible et à jour** — N2, sinon N1, sinon N0 ; à défaut d'un
calcul à jour, le dernier calcul terminé (N1, sinon N2, sinon N0, comme avant) — et la figure le dit dans
`layout.meta.resume` (`Contexte.resume_niveaux`).
"""

from django.db.models import Q

from .. import access, calc_service, facts, parc
from ..models import Bouquet, Building, CalcRun, Job, Portfolio, Potentiel
from . import catalogue

AUTO = 'auto'
ORDRE_FIN = ('N2', 'N1', 'N0')          # le plus fin d'abord
ORDRE_REPLI = ('N1', 'N2', 'N0')        # sans calcul à jour : le comportement d'avant (N1 d'abord)
HEURES_MOIS = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]


class CibleError(ValueError):
    """Cible invisible ou mal formée : la vue répond 404 (message fixe)."""


def _bornes_mois():
    out, a = [], 0
    for h in HEURES_MOIS:
        out.append((a, a + h))
        a += h
    return out


class Contexte:
    def __init__(self, request, portee, cible):
        self.request = request
        self.portee = portee
        if not isinstance(cible, dict):
            raise CibleError()
        key = 'parc' if portee == 'parc' else 'batiment'
        pk = cible.get(key)
        if isinstance(pk, bool) or not isinstance(pk, int):
            raise CibleError()
        if portee == 'parc':
            self.portfolio = access.visible_portfolios(request).filter(pk=pk).first()
            if self.portfolio is None:
                raise CibleError()
            self.batiments = list(access.visible_buildings(request).filter(portfolio=self.portfolio).order_by('name', 'pk'))
            pid = self.portfolio.pk
        else:
            b = access.visible_buildings(request).filter(pk=pk).first()
            if b is None:
                raise CibleError()
            self.batiments = [b]
            self.portfolio = Portfolio.objects.filter(pk=b.portfolio_id).first() if b.portfolio_id else None
            pid = b.portfolio_id
        q = Q(portfolio__isnull=True) | (Q(portfolio_id=pid) if pid else Q(pk__in=[]))
        self.bouquets = list(Bouquet.objects.filter(q).order_by('portfolio_id', 'ordre', 'pk'))
        self._bq = {b.pk: b for b in self.bouquets}
        self._runs = {}
        self._pots = {}
        self._commun = {}
        self._sources = {}
        self._auto = {}
        self._a_jour = {}
        self.choix_auto = {}
        self.cible = {key: pk}

    # ── Sources des autres secteurs (appelées une fois) ──
    def source(self, nom):
        if nom not in self._sources:
            from . import sources
            if nom == 'i3':
                self._sources[nom] = sources.indicateurs(self.request)
            elif nom == 'i7':
                self._sources[nom] = sources.actions(self.request, self.portfolio.pk if self.portfolio else None)
            elif nom == 'i5':
                if self.portee == 'parc':
                    self._sources[nom] = sources.potentiels_parc(self.request, self.portfolio.pk)
                else:
                    self._sources[nom] = sources.potentiels_batiment(self.request, self.batiments[0].pk)
        return self._sources[nom]

    def mesures(self):
        if 'mesures' not in self._sources:
            self._sources['mesures'] = catalogue.mesures(self.source('i3'))
        return self._sources['mesures']

    def familles(self):
        cat = self.source('i7') or {}
        fam = {f.get('cle'): f.get('libelle') or f.get('cle') for f in cat.get('familles') or [] if isinstance(f, dict)}
        act = {a.get('cle'): a for a in cat.get('actions') or [] if isinstance(a, dict)}
        return fam, act

    # ── Séries ──
    def series(self, spec):
        bq = [b.pk for b in self.bouquets]
        if spec == 'reel':
            return ['reel']
        if spec == 'bouquets':
            return bq
        if spec == 'reel_et_bouquets':
            return ['reel'] + bq
        return [s for s in spec if s in self._bq]

    def libelle_serie(self, s):
        return 'Réel' if s == 'reel' else self._bq[s].nom

    def potentiel(self, b, s, niveau):
        k = (b.pk, s, niveau)
        if k not in self._pots:
            self._pots[k] = (Potentiel.objects.filter(building=b, bouquet_id=s, niveau=niveau, statut='fait')
                             .select_related('run', 'scenario').first())
        return self._pots[k]

    def run(self, b, s, niveau):
        k = (b.pk, s, niveau)
        if k not in self._runs:
            r = None
            if s == 'reel':
                r = calc_service.latest(b, niveau)
            else:
                p = self.potentiel(b, s, niveau)
                if p is not None and p.run_id and p.run.status == Job.DONE:
                    r = p.run
            self._runs[k] = r
        return self._runs[k]

    def a_jour(self, b, s, niveau):
        """Le calcul lu pour (bâtiment, série, niveau) correspond-il aux entrées d'aujourd'hui ? (sans calcul)"""
        k = (b.pk, s, niveau)
        if k not in self._a_jour:
            ok = False
            run = self.run(b, s, niveau)
            if run is not None:
                if s == 'reel':
                    ok = calc_service.is_up_to_date(run) is True
                else:
                    from ..ameliorations import potentiels
                    p = self.potentiel(b, s, niveau)
                    ok = p is not None and p.empreinte == potentiels.empreinte(b, self._bq[s], niveau)
            self._a_jour[k] = ok
        return self._a_jour[k]

    def niveau_auto(self, b, s):
        """(niveau, à_jour) le plus fin disponible et à jour pour (bâtiment, série), ou None sans aucun calcul."""
        k = (b.pk, s)
        if k not in self._auto:
            choix = next(((n, True) for n in ORDRE_FIN if self.run(b, s, n) is not None and self.a_jour(b, s, n)),
                         None)
            if choix is None:
                choix = next(((n, False) for n in ORDRE_REPLI if self.run(b, s, n) is not None), None)
            self._auto[k] = choix
        return self._auto[k]

    def niveaux_pour(self, b, s, niveaux, grain=None):
        if list(niveaux) != [AUTO]:
            return list(niveaux)
        choix = self.niveau_auto(b, s)
        if choix is None:
            return ['N1'] if grain == 'action' else []
        self.choix_auto.setdefault(choix, set()).add((b.pk, s))
        return [choix[0]]

    def resume_niveaux(self):
        """Phrase dite dans `layout.meta.resume` quand le niveau a été choisi automatiquement ('' sinon)."""
        if not self.choix_auto:
            return ''
        parts = []
        for n in ORDRE_FIN:
            ok, old = len(self.choix_auto.get((n, True), ())), len(self.choix_auto.get((n, False), ()))
            if ok:
                parts.append(f"{n} pour {ok} série{'s' if ok > 1 else ''}")
            if old:
                parts.append(f"{n} pas à jour pour {old} série{'s' if old > 1 else ''}")
        return 'Niveau de calcul : le plus fin disponible et à jour (N2, sinon N1, sinon N0) — ' + ', '.join(parts) + '.'

    def commun(self, b):
        if b.pk not in self._commun:
            eff = facts.effective(b)
            vals = {k: f.value for k, f in eff.items()}
            usage = vals.get('identite.usage')
            annee = vals.get('identite.annee_construction')
            appro = parc.approfondissement(b, eff)
            self._commun[b.pk] = {
                'eff': eff, 'vals': vals,
                'dims': {'batiment': b.name, 'usage': facts.USAGES.get(usage, 'Non renseigné') if usage else 'Non renseigné',
                         'periode_construction': catalogue.periode_de(annee) or 'Non renseignée',
                         'classe_dpe': vals.get('dpe.etiquette') or 'Non renseignée',
                         'approfondissement': appro['libelle']},
                'raw': {'_b': b.pk, '_usage': usage, '_annee': annee, '_classe': vals.get('dpe.etiquette'),
                        '_appro': appro['niveau'], 'lat': b.georef_lat, 'lon': b.georef_lon},
            }
        return self._commun[b.pk]

    def faits_serie(self, b, s, niveau):
        if s == 'reel':
            return self.commun(b)['vals']
        p = self.potentiel(b, s, niveau)
        if p is None or p.scenario_id is None:
            return self.commun(b)['vals']
        return {k: f.value for k, f in facts.effective(b, p.scenario).items()}

    # ── Lignes ──
    def lignes(self, grain, series_spec, niveaux, besoins_faits=False):
        """`niveaux` : liste de niveaux, ou `[AUTO]` (le plus fin disponible et à jour, par bâtiment et série)."""
        out = []
        self.choix_auto = {}
        series = self.series(series_spec)
        mes = self.mesures()
        fam_lib, actions = self.familles() if grain == 'action' else ({}, {})
        for b in self.batiments:
            c = self.commun(b)
            for s in series:
                if grain in catalogue.SERIES_HORS_REEL_GRAINS and s == 'reel':
                    continue
                for niv in self.niveaux_pour(b, s, niveaux, grain):
                    run = self.run(b, s, niv)
                    if run is None and grain != 'action':
                        continue
                    base = {**c['raw'], **c['dims'], 'bouquet': self.libelle_serie(s), '_serie': s,
                            'niveau': catalogue.NIVEAUX[niv], '_niveau': niv, '_run': run.pk if run else None}
                    ind = dict(run.indicators or {}) if run else {}
                    if 'dh_26' not in ind and 'degree_hours_over_26' in ind:
                        ind['dh_26'] = ind['degree_hours_over_26']
                    base['etiquette_modele'] = ind.get('dpe_label_model') or 'Non calculée'
                    res = (run.results or {}) if run else {}
                    if grain == 'batiment':
                        row = dict(base)
                        vals = self.faits_serie(b, s, niv) if besoins_faits else {}
                        for k, d in mes.items():
                            if d['grain'] != 'batiment':
                                continue
                            if d['source'] == 'i3':
                                row[k] = _num(ind.get(k))
                            elif d['source'] == 'fait':
                                row[k] = _num(vals.get(k))
                        row['nb_batiments'] = 1
                        out.append(row)
                    elif grain == 'mois':
                        men = res.get('mensuel') or {}
                        if not men and res.get('monthly_heating_kwh'):
                            men = {'heating_kwh': res['monthly_heating_kwh']}
                        for i in range(12):
                            row = {**base, 'mois': catalogue.MOIS[i], '_mois': i + 1}
                            for key in ('heating_kwh', 'cooling_kwh', 'dh_re2020', 't_op_max_c', 't_ext_moy_c'):
                                serie = men.get(key)
                                row['mensuel.' + key] = _num(serie[i]) if isinstance(serie, list) and len(serie) == 12 else None
                            if any(row['mensuel.' + k] is not None for k in ('heating_kwh', 'cooling_kwh', 'dh_re2020',
                                                                              't_op_max_c', 't_ext_moy_c')):
                                out.append(row)
                    elif grain == 'heure':
                        t = res.get('t_op')
                        if not isinstance(t, list) or len(t) < 8760:
                            continue
                        t = t[-8760:]
                        for mi, (a, z) in enumerate(_bornes_mois()):
                            for h in range(24):
                                vs = [t[k] for k in range(a + h, z, 24) if _num(t[k]) is not None]
                                out.append({**base, 'mois': catalogue.MOIS[mi], '_mois': mi + 1, 'heure': f'{h} h',
                                            'horaire.t_op_c': round(sum(vs) / len(vs), 2) if vs else None})
                    elif grain == 'poste':
                        bal = res.get('balance') or {}
                        surf = _num(ind.get('surface_m2'))
                        for cle, lib, _sens in catalogue.POSTES:
                            v = _num(bal.get(cle))
                            if v is None:
                                continue
                            out.append({**base, 'poste_bilan': lib, '_poste': cle, 'bilan.kwh': v,
                                        'bilan.kwh_m2': round(v / surf, 2) if surf else None})
                    elif grain == 'poste_energie':
                        by = (res.get('energy') or {}).get('by_usage_kwh') or {}
                        for cle, lib in catalogue.POSTES_ENERGIE.items():
                            v = _num(by.get(cle))
                            if v is not None:
                                out.append({**base, 'poste_energie': lib, 'energie.poste_kwh': v})
                    elif grain == 'orientation':
                        by = res.get('solar_windows_by_orientation_kwh') or {}
                        for o in catalogue.ORIENTATIONS:
                            v = _num(by.get(o))
                            if v is not None:
                                out.append({**base, 'orientation': o, 'solaire.baies_kwh': v})
                    elif grain == 'action':
                        p = self.potentiel(b, s, niv)
                        if p is None:
                            continue
                        for a in p.actions or []:
                            if not isinstance(a, dict):
                                continue
                            cle = str(a.get('cle') or '')
                            fam = (actions.get(cle) or {}).get('famille')
                            appl = bool(a.get('appliquee'))
                            out.append({**base, 'action': str(a.get('libelle') or cle)[:150],
                                        'famille_action': fam_lib.get(fam, fam or 'Non classée'), '_famille': fam,
                                        'application': 'Appliquée' if appl else 'Écartée',
                                        '_appl': 'appliquee' if appl else 'ecartee', 'actions.nombre': 1})
        return out


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float('inf'), float('-inf')):
        return None
    return float(v)


def filtrer(rows, filtre):
    if not filtre:
        return rows
    out = []
    usages_lib = {v: k for k, v in facts.USAGES.items()}
    for r in rows:
        ok = True
        for k, v in filtre.items():
            if k == 'usage':
                allowed = {usages_lib.get(x, x) for x in v}
                ok = r.get('_usage') in allowed
            elif k == 'periode':
                a = r.get('_annee')
                lo, hi = v
                ok = a is not None and (lo is None or a >= lo) and (hi is None or a <= hi)
            elif k == 'classe_dpe':
                ok = r.get('_classe') in set(v)
            elif k == 'approfondissement':
                ok = r.get('_appro') in set(v) or r.get('approfondissement') in set(v)
            elif k == 'niveau':
                ok = r.get('_niveau') in set(v)
            elif k == 'batiment':
                ok = r.get('_b') in set(v)
            elif k == 'mois':
                ok = '_mois' not in r or str(r['_mois']) in set(v) or r.get('mois') in set(v)
            elif k == 'famille_action':
                ok = '_famille' not in r or r['_famille'] in set(v)
            elif k == 'application':
                ok = '_appl' not in r or r['_appl'] in set(v)
            if not ok:
                break
        if ok:
            out.append(r)
    return out


def niveaux_de(graphe):
    """Niveaux lus pour un graphe : les trois si « niveau » est une dimension, celui du filtre s'il y en a un, sinon
    `[AUTO]` (le plus fin disponible et à jour)."""
    dims = {graphe.get('x'), graphe.get('couleur'), graphe.get('facettes')}
    f = (graphe.get('filtre') or {}).get('niveau')
    if 'niveau' in dims:
        lv = ['N0', 'N1', 'N2']
    elif not f:
        return [AUTO]
    else:
        lv = ['N1']
    if f:
        lv = [n for n in ['N0', 'N1', 'N2'] if n in set(f)] if 'niveau' in dims else [n for n in ['N1', 'N0', 'N2']
                                                                                       if n in set(f)][:1]
    return lv or ['N1']


def bouquets_visibles(ctx):
    return [{'id': b.pk, 'nom': b.nom, 'objectif': b.objectif} for b in ctx.bouquets]


__all__ = ['AUTO', 'Contexte', 'CibleError', 'filtrer', 'niveaux_de', 'Building', 'CalcRun']
