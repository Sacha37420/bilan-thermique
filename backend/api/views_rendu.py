"""Secteur R (I6) : document de rendu, figures, exports, gabarits — chantier bt-potentiels, contrat v3, I6.a-g.

GET  /api/rendu/schema/              schéma JSON de la spécification
GET  /api/rendu/dimensions/          [{cle, libelle, unite, portees, type, formes}]
GET  /api/rendu/mesures/             [{cle, libelle, unite, portees, famille, formes}]
POST /api/rendu/figure/              {portee, cible, graphe} → {data, layout, vide}
POST /api/rendu/document/            {spec | modele_id, cible} → document résolu
GET  /api/rendu/exports/             exports de l'appelant non expirés ; POST {spec | modele_id, cible, format} → 202
GET  /api/rendu/exports/<id>/        statut (auteur seul) ; …/fichier : le fichier (auteur seul, 24 h)
GET  /api/rendu/gabarits/            {modeles, questions}
GET  /api/parcs/<id>/rapports/       {peut_ecrire, modeles} ; POST (gestionnaire du parc) → 201
GET|PATCH|DELETE /api/rapports/<id>/ modèle (gabarit du lab : lecture seule)
"""

from django.db import transaction
from django.http import FileResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access
from .models import RapportModele
from .rendu import catalogue, document, exports, figures, gabarits, sources, spec as spec_mod
from .rendu.donnees import CibleError, Contexte

CIBLE_INTROUVABLE = {'detail': 'Cible introuvable.'}
CIBLE_ATTENDUE = 'Cible attendue : {"parc": id} ou {"batiment": id}, selon la portée.'
MODELE_INTROUVABLE = {'detail': 'Modèle de rendu introuvable.'}
EXPORT_INTROUVABLE = {'detail': 'Export introuvable.'}
LAB_LECTURE = {'detail': 'Gabarit du lab : copiez-le dans un parc pour le modifier.'}
MAX_MODELES_PAR_PARC = 50


def _bad(msg):
    return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def modele_json(m, modifiable):
    return {'id': m.pk, 'nom': m.nom, 'portee': m.portee, 'spec': m.spec, 'par_defaut': m.par_defaut,
            'portfolio': m.portfolio_id, 'modifiable': bool(modifiable)}


def _modele_visible(request, pk):
    if isinstance(pk, bool) or not isinstance(pk, int):
        return None
    m = RapportModele.objects.filter(pk=pk).first()
    if m is None:
        return None
    if m.portfolio_id is not None and not access.visible_portfolios(request).filter(pk=m.portfolio_id).exists():
        return None
    return m


def _portee_de_cible(cible):
    if not isinstance(cible, dict) or len(cible) != 1:
        return None
    k = next(iter(cible))
    return {'parc': 'parc', 'batiment': 'batiment'}.get(k)


class _Doc(APIView):
    """Spécification (directe ou modèle), cible, contexte — commun à `document` et `exports`."""

    def preparer(self, request):
        d = _data(request)
        modele = None
        gabarits.assurer()
        if d.get('modele_id') is not None:
            modele = _modele_visible(request, d.get('modele_id'))
            if modele is None:
                return None, Response(MODELE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
            brut = modele.spec
        elif 'spec' in d:
            brut = d['spec']
        else:
            return None, _bad('Spécification ou modèle attendu (« spec » ou « modele_id »).')
        cible = d.get('cible')
        portee = _portee_de_cible(cible)
        if portee is None:
            return None, _bad(CIBLE_ATTENDUE)
        try:
            ctx = Contexte(request, portee, cible)
        except CibleError:
            return None, Response(CIBLE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        try:
            spec = spec_mod.valider(brut, ctx.mesures())
        except spec_mod.SpecError as exc:
            return None, _bad(str(exc))
        if spec['portee'] != portee:
            return None, _bad(CIBLE_ATTENDUE)
        return (ctx, spec, modele, cible), None


def sous_titre(ctx):
    if ctx.portee == 'parc':
        n = len(ctx.batiments)
        return f"Parc « {ctx.portfolio.name} » — {n} bâtiment{'s' if n > 1 else ''}"
    b = ctx.batiments[0]
    return ' — '.join(x for x in (b.address, f'parc « {ctx.portfolio.name} »' if ctx.portfolio else '') if x)


class SchemaView(APIView):
    def get(self, request):
        return Response(spec_mod.schema_json())


class DimensionsView(APIView):
    def get(self, request):
        return Response(catalogue.liste_dimensions(sources.indicateurs(request)))


class MesuresView(APIView):
    def get(self, request):
        return Response(catalogue.liste_mesures(sources.indicateurs(request)))


class FigureView(APIView):
    def post(self, request):
        d = _data(request)
        portee = d.get('portee')
        if portee not in catalogue.PORTEES or _portee_de_cible(d.get('cible')) != portee:
            return _bad(CIBLE_ATTENDUE)
        try:
            ctx = Contexte(request, portee, d.get('cible'))
        except CibleError:
            return Response(CIBLE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(figures.figure_de_spec(ctx, d.get('graphe')))
        except spec_mod.SpecError as exc:
            return _bad(str(exc))


class DocumentView(_Doc):
    def post(self, request):
        prep, err = self.preparer(request)
        if err is not None:
            return err
        ctx, spec, _m, _c = prep
        return Response(document.resoudre(ctx, spec))


class ExportsView(_Doc):
    def get(self, request):
        exports.purger()
        return Response([exports.public(e) for e in exports.vivants(access.sub_of(request))])

    def post(self, request):
        fmt = _data(request).get('format')
        if fmt not in exports.TYPES:
            return _bad('Format attendu : « html », « docx » ou « pdf ».')
        sub = access.sub_of(request)
        if not sub:
            return Response({'detail': 'Identité de l’appelant inconnue.'}, status=status.HTTP_403_FORBIDDEN)
        exports.purger()
        if exports.vivants(sub).count() >= exports.MAX_PAR_PERSONNE:
            return _bad('Trop d’exports en cours (vingt au plus sur 24 h) : attendez qu’ils expirent.')
        prep, err = self.preparer(request)
        if err is not None:
            return err
        ctx, spec, modele, cible = prep
        doc = document.resoudre(ctx, spec)
        e = exports.creer(sub, doc, spec, cible, fmt, modele, sous_titre(ctx))
        from .rendu import taches
        transaction.on_commit(lambda: taches.rendu_export.delay(e.pk))
        return Response({'id': e.pk, 'statut': e.statut, 'format': e.format, 'cree_le': e.cree_le},
                        status=status.HTTP_202_ACCEPTED)


def _export_de(request, pk):
    sub = access.sub_of(request)
    e = exports.ExportRendu.objects.filter(pk=pk).first()
    if e is None or not sub or e.owner_sub != sub or e.expire_le is None:
        return None
    from django.utils import timezone
    if e.expire_le < timezone.now():
        return None
    return e


class ExportDetailView(APIView):
    def get(self, request, pk):
        e = _export_de(request, pk)
        if e is None:
            return Response(EXPORT_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        return Response(exports.public(e))


class ExportFichierView(APIView):
    def get(self, request, pk):
        e = _export_de(request, pk)
        if e is None:
            return Response(EXPORT_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        p = exports.fichier(e)
        if p is None:
            return Response({'detail': 'Export pas encore prêt.'}, status=status.HTTP_409_CONFLICT)
        resp = FileResponse(open(p, 'rb'), as_attachment=True, filename=e.chemin, content_type=exports.TYPES[e.format])
        resp['Cache-Control'] = 'no-store'
        resp['X-Content-Type-Options'] = 'nosniff'
        # Un HTML exporté s'ouvre hors du site : jamais exécuté sous l'origine de l'app.
        resp['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return resp


class GabaritsView(APIView):
    def get(self, request):
        return Response({'modeles': [modele_json(m, False) for m in gabarits.assurer()],
                         'questions': gabarits.questions()})


def _valider_modele(d, partiel=False):
    out = {}
    if 'nom' in d or not partiel:
        nom = d.get('nom')
        if not isinstance(nom, str) or not nom.strip() or len(nom) > 150:
            raise spec_mod.SpecError('Nom attendu (150 caractères au plus).')
        out['nom'] = ''.join(c for c in nom if c.isprintable()).strip()
    if 'spec' in d or not partiel:
        out['spec'] = spec_mod.valider(d.get('spec'))
        out['portee'] = out['spec']['portee']
    if 'par_defaut' in d:
        if not isinstance(d['par_defaut'], bool):
            raise spec_mod.SpecError('« par_defaut » : oui ou non attendu.')
        out['par_defaut'] = d['par_defaut']
    return out


def _un_seul_defaut(m):
    if m.par_defaut and m.portfolio_id is not None:
        RapportModele.objects.filter(portfolio_id=m.portfolio_id, portee=m.portee, par_defaut=True).exclude(
            pk=m.pk).update(par_defaut=False)


class RapportsParcView(APIView):
    def _parc(self, request, pk):
        return access.visible_portfolios(request).filter(pk=pk).first()

    def get(self, request, pk):
        p = self._parc(request, pk)
        if p is None:
            return Response(CIBLE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        ecrire = access.is_portfolio_manager(request, p)
        return Response({'peut_ecrire': ecrire,
                         'modeles': [modele_json(m, ecrire) for m in RapportModele.objects.filter(portfolio=p)]})

    def post(self, request, pk):
        p = self._parc(request, pk)
        if p is None:
            return Response(CIBLE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        if not access.is_portfolio_manager(request, p):
            return Response({'detail': 'Réservé aux gestionnaires du parc.'}, status=status.HTTP_403_FORBIDDEN)
        if RapportModele.objects.filter(portfolio=p).count() >= MAX_MODELES_PAR_PARC:
            return _bad('Cinquante modèles au plus par parc.')
        try:
            v = _valider_modele(_data(request))
        except spec_mod.SpecError as exc:
            return _bad(str(exc))
        m = RapportModele.objects.create(portfolio=p, auteur_sub=access.sub_of(request), **v)
        _un_seul_defaut(m)
        return Response(modele_json(m, True), status=status.HTTP_201_CREATED)


class RapportDetailView(APIView):
    def _modele(self, request, pk):
        m = _modele_visible(request, pk)
        if m is None:
            return None, False
        return m, m.portfolio_id is not None and access.is_portfolio_manager(request, m.portfolio)

    def get(self, request, pk):
        m, ecrire = self._modele(request, pk)
        if m is None:
            return Response(MODELE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        return Response(modele_json(m, ecrire))

    def _ecriture(self, request, pk):
        m, ecrire = self._modele(request, pk)
        if m is None:
            return None, Response(MODELE_INTROUVABLE, status=status.HTTP_404_NOT_FOUND)
        if m.portfolio_id is None:
            return None, Response(LAB_LECTURE, status=status.HTTP_403_FORBIDDEN)
        if not ecrire:
            return None, Response({'detail': 'Réservé aux gestionnaires du parc.'}, status=status.HTTP_403_FORBIDDEN)
        return m, None

    def patch(self, request, pk):
        m, err = self._ecriture(request, pk)
        if err is not None:
            return err
        try:
            v = _valider_modele(_data(request), partiel=True)
        except spec_mod.SpecError as exc:
            return _bad(str(exc))
        for k, val in v.items():
            setattr(m, k, val)
        m.save()
        _un_seul_defaut(m)
        return Response(modele_json(m, True))

    def delete(self, request, pk):
        m, err = self._ecriture(request, pk)
        if err is not None:
            return err
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
