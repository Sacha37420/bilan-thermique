"""Qui voit quoi (refonte). Point unique : toute nouvelle vue passe par ces fonctions.

Règle D4 (`refonte.md`) : un bâtiment **sans parc** reste visible de tout membre de l'app (comme
avant la refonte) ; un bâtiment de parc n'est visible que des membres du parc et des personnes à
qui il est confié (`BuildingAccess`). Complété au Lot 11 (parcs).
"""

from .models import Building


def sub_of(request) -> str:
    return getattr(request.user, 'sub', '') or ''


def visible_buildings(request):
    return Building.objects.all()


def can_declare(request, building) -> bool:
    """Poser un fait déclaré/mesuré (surcharge). Bâtiment sans parc : tout membre (D4)."""
    return True


def can_retire(request, fact) -> bool:
    """Retirer une surcharge : son auteur (complété au Lot 11 : gestionnaires du parc)."""
    return bool(sub_of(request)) and fact.author_sub == sub_of(request)
