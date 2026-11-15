"""Qui voit quoi, qui modifie quoi (refonte). Point unique : toute vue passe par ces fonctions.

Règle D4 (`refonte.md`) :
- un bâtiment **sans parc** reste visible et modifiable par tout membre de l'app (comme avant la
  refonte) ;
- un bâtiment **de parc** n'est visible que :
  - des membres du parc de rôle `gestionnaire_parc` ou `lecteur` (tous les bâtiments du parc),
  - des personnes à qui il est confié (`BuildingAccess` : gestionnaire ou occupant) ;
  un `gestionnaire_bat` du parc ne voit que les bâtiments qui lui sont confiés ;
- modifier la fiche (déclarer, calculer N0/N1) : gestionnaire de parc, gestionnaire ou occupant du
  bâtiment ; un lecteur ne modifie rien ;
- gérer (supprimer, partager, rejouer la chaîne, lancer le N2) : gestionnaire de parc ou du bâtiment.
Toute personne est désignée par son `sub` Keycloak.
"""

from django.db.models import Q

from .models import Building, BuildingAccess, Portfolio, PortfolioMember

PARC, BAT, LECTEUR = PortfolioMember.GESTIONNAIRE_PARC, PortfolioMember.GESTIONNAIRE_BAT, PortfolioMember.LECTEUR


def sub_of(request) -> str:
    return getattr(request.user, 'sub', '') or ''


def name_of(request) -> str:
    return (getattr(request.user, 'username', '') or '')[:150]


def portfolio_role(portfolio_id, sub):
    if not sub or portfolio_id is None:
        return None
    m = PortfolioMember.objects.filter(portfolio_id=portfolio_id, sub=sub).first()
    return m.role if m else None


def visible_portfolios(request):
    sub = sub_of(request)
    if not sub:
        return Portfolio.objects.none()
    return Portfolio.objects.filter(
        Q(members__sub=sub) | Q(buildings__accesses__sub=sub)).distinct()


def visible_buildings(request):
    sub = sub_of(request)
    q = Q(portfolio__isnull=True)
    if sub:
        q |= Q(portfolio__members__sub=sub, portfolio__members__role__in=[PARC, LECTEUR])
        q |= Q(accesses__sub=sub)
    return Building.objects.filter(q).distinct()


def building_role(request, building):
    """'commun' | 'gestionnaire_parc' | 'gestionnaire' | 'occupant' | 'lecteur' | None."""
    if building.portfolio_id is None:
        return 'commun'
    sub = sub_of(request)
    if not sub:
        return None
    prole = portfolio_role(building.portfolio_id, sub)
    if prole == PARC:
        return PARC
    acc = BuildingAccess.objects.filter(building=building, sub=sub).first()
    if acc is not None:
        return acc.role
    if prole == LECTEUR:
        return LECTEUR
    return None


def can_declare(request, building) -> bool:
    """Poser un fait déclaré/mesuré, lancer N0/N1."""
    return building_role(request, building) in ('commun', PARC, 'gestionnaire', 'occupant')


def can_manage(request, building) -> bool:
    """Supprimer, confier, rejouer la chaîne, modifier la géométrie, lancer N2."""
    return building_role(request, building) in ('commun', PARC, 'gestionnaire')


def can_retire(request, fact) -> bool:
    """Retirer une surcharge : son auteur, ou un gestionnaire (parc ou bâtiment)."""
    sub = sub_of(request)
    if sub and fact.author_sub == sub:
        return True
    return fact.building.portfolio_id is not None and building_role(request, fact.building) in (PARC, 'gestionnaire')


def can_see_personal(request, building, owner_sub) -> bool:
    """Données personnelles (mesures, documents) : leur auteur, ou un gestionnaire du bâtiment de parc."""
    sub = sub_of(request)
    if sub and owner_sub == sub:
        return True
    return building.portfolio_id is not None and building_role(request, building) in (PARC, 'gestionnaire')


def is_portfolio_manager(request, portfolio) -> bool:
    return portfolio_role(portfolio.pk, sub_of(request)) == PARC
