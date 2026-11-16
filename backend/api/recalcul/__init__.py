"""Recalcul automatique des trois niveaux, réel et potentiels (chantier bt-trois-niveaux, secteur C).

Module d'attente posé par la fondation : interface I1 du contrat, sans effet. Le secteur C le réécrit."""


def planifier(building, cause, *, immediat=False):
    return None


def planifier_parc(portfolio, cause):
    return 0


def etat_batiment(building):
    return {'statut': 'inactif', 'cause': '', 'prevu_pour': None, 'message': '', 'peut_recalculer': False,
            'calculs': [], 'calculs_restants': 0}


def resume_parc(portfolio):
    return {'batiments': 0, 'a_jour': 0, 'planifies': 0, 'en_cours': 0, 'erreurs': 0, 'calculs_restants': 0}
