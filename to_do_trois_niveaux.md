# Trois niveaux calculés et tenus à jour automatiquement — suivi (lots 41 à 44)

Demande du propriétaire (2026-11-16) : « que ce soit par la méthode N0, N1, N2, ou le Bâtiment maillé 3D ce que je veux
c'est que les résultats de ces trois simulations soient automatiquement calculés et puissent être affinés par les
utilisateurs ! Aussi bien dans l'onglet Parc que dans l'onglet Bâtiment ou dans les affichages possibles dans les
rendus ». Choix : les potentiels par bouquet aussi, en N0, N1 et N2 ; recalcul automatique quand un utilisateur affine
un bâtiment. Remplace les décisions D37 et D44. Contrat : `chantiers/bt-trois-niveaux/CONTRAT.md` (dépôt du lab).

## Lot 41 — Fondation   ·   statut : livré
- [x] Modèle `Recalcul` (migration 0029), réglages `calcul.n2`, `calcul.potentiels`, `calcul.recalcul_auto`, `calcul.recalcul_delai_s`
- [x] Paquet `recalcul/` (attente), signaux branchés par `ApiConfig.ready()`, tâches importées, routes I2 déclarées, mode test sans recalcul
**Vérifié le :** 2026-11-16 — `test_fondation_trois_niveaux.py`, `test_navigation.py` OK

## Lot 42 — Calculs automatiques (secteur C)   ·   statut : à faire
- [ ] `recalcul.planifier` / `planifier_parc` : regroupement (délai du parc), tâche Celery unique par bâtiment, replanification si une demande arrive pendant l'exécution
- [ ] Signaux : faits, protections, géométrie, enveloppe, environnement, météo, position, fin de chaîne, améliorations, bouquets, réglages du parc
- [ ] Exécution : réel N0/N1 (file `celery`), puis réel N2 et potentiels N0/N1/N2 (file `parc`), N2 sous le verrou des calculs lourds, rien de recalculé s'il est à jour, réglages du parc respectés
- [ ] Chaîne d'un bâtiment ou d'un parc : tout calculé sans geste
- [ ] État des calculs (I2) : bâtiment, parc, « Recalculer » d'un gestionnaire
**Tests :** regroupement ; chaque déclencheur ; ordre ; « à jour » non recalculé ; verrou occupé ⇒ replanifié ; réglages coupés respectés ; droits ; aucun recalcul en tests ordinaires
**Vérifié le :**

## Lot 43 — Données à trois niveaux (secteur D)   ·   statut : à faire
- [ ] Potentiels N0 (en plus de N1 et N2) ; `calculer`, `a_jour`, `bouquets_visibles` (I3) ; potentiels de parc par niveau, N2 compris
- [ ] Tableau du parc : `niveaux` N0/N1/N2 et état des calculs par bâtiment ; export CSV par niveau
- [ ] Rendu : gabarits avec les trois niveaux, réel et bouquets ; questions par niveau ; niveau par défaut = le plus fin à jour
**Tests :** potentiels N0 ; parc par niveau (agrégats) ; tableau ; gabarits
**Vérifié le :**

## Lot 44 — Écran et intégration   ·   statut : à faire
- [ ] Bâtiment : les trois niveaux côte à côte (réel), état des calculs, rafraîchissement automatique ; « Recalculer » pour un gestionnaire
- [ ] Calculs : potentiels par bouquet sur trois niveaux ; simulation et historique cohérents avec le recalcul automatique
- [ ] Parc : tableau et potentiels par niveau, progression des calculs du parc
- [ ] Rendu : choix du niveau dans le constructeur, aperçu qui suit les recalculs
- [ ] Intégration : déploiement, cloisonnement, parcours réel (affiner un bâtiment → recalcul visible partout), documentation (README, refonte.md D49+, Théorie, moteurs)
**Vérifié le :**
