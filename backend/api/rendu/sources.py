"""Ce que le rendu consomme des autres secteurs, **tel que le contrat l'écrit** (chantier bt-potentiels).

- I3 (P) : `GET /api/indicateurs/` (liste canonique) et `CalcRun.indicators` / `results['mensuel']` ;
- I4 (P) : `GET /api/calculs/<id>/triangles/` ;
- I5 (A) : `GET /api/batiments/<id>/potentiels/`, `GET /api/parcs/<id>/potentiels/` (formes I5.b) ;
- I7 (A) : `GET /api/ameliorations/actions/` (familles et actions).

Chaque route est appelée **par sa classe de vue** (celle que `urls.py` déclare), avec l'utilisateur déjà authentifié
de la requête en cours : mêmes droits, même forme qu'en HTTP, aucun aller-retour réseau. Une route qui ne répond pas
200 (pas encore livrée : 501 ; refusée : 403/404) rend `None` : le bloc qui en dépend est alors « indisponible ».
"""

from django.http import HttpRequest, QueryDict


def appel_get(view_cls, request, query=None, **kwargs):
    """(statut, données) de `view_cls` appelée en GET au nom de l'utilisateur de `request` (DRF)."""
    raw = HttpRequest()
    raw.method = 'GET'
    src = getattr(request, '_request', request)
    raw.META = {k: v for k, v in getattr(src, 'META', {}).items()
                if k in ('SERVER_NAME', 'SERVER_PORT', 'HTTP_HOST', 'REMOTE_ADDR')}
    raw.META.setdefault('SERVER_NAME', 'localhost')
    raw.META.setdefault('SERVER_PORT', '80')
    q = QueryDict(mutable=True)
    for k, v in (query or {}).items():
        q[k] = str(v)
    raw.GET = q
    # Authentification forcée par DRF (`ForcedAuthentication`) : l'utilisateur et le jeton déjà vérifiés.
    raw._force_auth_user = getattr(request, 'user', None)
    raw._force_auth_token = getattr(request, 'auth', None)
    try:
        resp = view_cls.as_view()(raw, **kwargs)
    except Exception:  # noqa: BLE001 — un producteur en erreur rend le bloc indisponible, jamais un 500 du rendu
        return 500, None
    return resp.status_code, getattr(resp, 'data', None)


def _ok(status, data):
    return data if status == 200 else None


def indicateurs(request):
    """Liste I3 `[{cle, libelle, unite, niveaux, additif, sens, famille}]`, ou None."""
    from .. import views_indicateurs
    data = _ok(*appel_get(views_indicateurs.IndicateursView, request))
    if isinstance(data, dict):            # tolérance : {"results": [...]} ou {"indicateurs": [...]}
        data = data.get('results') or data.get('indicateurs')
    return data if isinstance(data, list) else None


def triangles(request, run_id):
    from .. import views_triangles
    data = _ok(*appel_get(views_triangles.TrianglesView, request, pk=run_id))
    return data if isinstance(data, dict) and data.get('triangles') is not None else None


def potentiels_batiment(request, building_id):
    from .. import views_potentiels
    data = _ok(*appel_get(views_potentiels.PotentielsBatimentView, request, pk=building_id))
    return data if isinstance(data, dict) else None


def potentiels_parc(request, portfolio_id):
    from .. import views_potentiels
    data = _ok(*appel_get(views_potentiels.PotentielsParcView, request, pk=portfolio_id))
    return data if isinstance(data, dict) else None


def actions(request, portfolio_id=None):
    from ..ameliorations import views as am_views
    q = {'parc': portfolio_id} if portfolio_id else None
    data = _ok(*appel_get(am_views.ActionsView, request, query=q))
    return data if isinstance(data, dict) else None
