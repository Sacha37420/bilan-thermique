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
