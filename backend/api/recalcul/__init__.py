"""Recalcul automatique des trois niveaux, réel et potentiels (chantier bt-trois-niveaux, I1/I2 — secteur C).

API (contrat I1) :
    planifier(building, cause, *, immediat=False) -> None        regroupé ; ne lève jamais
    planifier_parc(portfolio, cause) -> int                       nombre de bâtiments planifiés
    etat_batiment(building, *, peut_recalculer=False) -> ETAT     I2, sans réseau ni calcul
    resume_parc(portfolio, buildings=None) -> dict                sans requête par bâtiment
En plus : `planifier_batiments(queryset, cause)` (lot, file de fond), `planifier_tous(cause)` (mise en service) et
`lignes_parc(portfolio, buildings)` → (résumé, [{id, statut, calculs_restants}]) en trois requêtes pour tout un parc
(à préférer à `etat_batiment` appelé à chaque ligne d’un tableau).

Modules : `planification` (demandes, regroupement), `execution` (ordre des calculs, verrous), `taches` (Celery),
`signaux` (déclencheurs), `etat` (I2), `views` (routes I2).
"""

from .etat import etat_batiment, lignes_parc, resume_parc  # noqa: F401
from .planification import CHAINE, MANUEL, planifier, planifier_batiments, planifier_parc, planifier_tous  # noqa: F401
