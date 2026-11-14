"""Moteurs de calcul réduits de la refonte (Lot 10) : N0 (mensuel) et N1 (horaire 5R1C).

Tous deux lisent la **même fiche** par le même traducteur (`inputs.from_fiche`), ce qui garantit
que N0, N1 et le tableau de parc parlent du même bâtiment. N2 (FEM, `building_solver`) reste le
calcul détaillé ; `inputs.from_envelope` traduit un bâtiment maillé vers les mêmes paramètres
pour comparer N1 à N2.
"""
