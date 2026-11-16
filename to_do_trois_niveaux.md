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

## Lot 42 — Calculs automatiques (secteur C)   ·   statut : livré
- [x] `recalcul.planifier` / `planifier_parc` : regroupement (délai du parc), tâche Celery unique par bâtiment, replanification si une demande arrive pendant l'exécution
- [x] Signaux : faits, protections, géométrie, enveloppe, environnement, météo, position, fin de chaîne, améliorations, bouquets, réglages du parc
- [x] Exécution : réel N0/N1 (file `celery`), puis réel N2 et potentiels N0/N1/N2 (file `parc`), N2 sous le verrou des calculs lourds, rien de recalculé s'il est à jour, réglages du parc respectés
- [x] Chaîne d'un bâtiment ou d'un parc : tout calculé sans geste
- [x] État des calculs (I2) : bâtiment, parc, « Recalculer » d'un gestionnaire
**Tests :** regroupement ; chaque déclencheur ; ordre ; « à jour » non recalculé ; verrou occupé ⇒ replanifié ; réglages coupés respectés ; droits ; aucun recalcul en tests ordinaires
**Vérifié le :** 2026-11-16 — suite complète 742 tests OK (`essai.sh`, réseau fermé)

## Lot 43 — Données à trois niveaux (secteur D)   ·   statut : livré
- [x] Potentiels N0 (en plus de N1 et N2) ; `calculer`, `a_jour`, `bouquets_visibles` (I3) ; potentiels de parc par niveau, N2 compris
- [x] Tableau du parc : `niveaux` N0/N1/N2 et état des calculs par bâtiment ; export CSV par niveau
- [x] Rendu : gabarits avec les trois niveaux, réel et bouquets ; questions par niveau ; niveau par défaut = le plus fin à jour
**Tests :** potentiels N0 ; parc par niveau (agrégats) ; tableau ; gabarits
**Vérifié le :** 2026-11-16 — suite complète 742 tests OK (`essai.sh`, réseau fermé)

## Lot 44 — Écran et intégration   ·   statut : livré
- [x] Bâtiment : les trois niveaux côte à côte (réel), état des calculs, rafraîchissement automatique ; « Recalculer » pour un gestionnaire
- [x] Calculs : potentiels par bouquet sur trois niveaux ; simulation et historique cohérents avec le recalcul automatique
- [x] Parc : tableau et potentiels par niveau, progression des calculs du parc
- [x] Rendu : choix du niveau dans le constructeur, aperçu qui suit les recalculs
- [x] Intégration : déploiement, cloisonnement, parcours réel (affiner un bâtiment → recalcul visible partout), documentation (README, refonte.md D49+, Théorie, moteurs)
**Vérifié le :** 2026-11-16 — 743 tests (dont la suppression par QuerySet, défaut trouvé au parcours réel) ; cloisonnement `lab-runner` 3/3 ; premier calcul de tous les bâtiments en service (`planifier_tous`, 4 bâtiments : N2 et potentiels N0/N1/N2, verrou lourd occupé ⇒ replanifié puis fait) ; **parcours réel** (compte de test, 0 erreur console) : parc créé, bâtiment par son adresse (chaîne 76 s), puis **N0, N1, N2 et les potentiels des deux bouquets calculés sans geste en 50 s**, bandeau suivi sans recharger (« 7 restants » → « Tous les calculs sont à jour »), plus aucun appel de suivi une fois à jour ; `U des murs` corrigé à 0,25 ⇒ « Recalcul prévu — suite à : donnée mur.u », puis recalcul des 9 calculs (N1 réel 62 → 59 kWh/m²·an, N0 78 → 73), visibles dans Bâtiment, Calculs, Parc (onglets N0/N1/N2, « Les trois côte à côte ») et Rendu. Non joués en navigateur : lecteur du parc, `calcul.n2` coupé (tenus par les tests backend)
