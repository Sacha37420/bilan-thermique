# Potentiels, protections paramétrables et rendu — suivi (lots 35 à 40)

Cadrage du 2026-11-16. Contrat d'interfaces (fait autorité, versionné) : `chantiers/bt-potentiels/CONTRAT.md` dans le
dépôt du lab (non versionné : il décrit des points ouverts). Ce fichier est le suivi : une case se coche quand la
tâche est **livrée et vérifiée** (pas quand elle est écrite). Chaque lot clos est recopié, résumé, dans
`FEUILLE_DE_ROUTE.md` avec sa ligne « Vérifié le ».

## Ce qui est demandé (propriétaire, 2026-11-16)

1. Un lot clair sur le paramétrage des **protections solaires** : modèles disponibles ; variables modulables par
   protection (épaisseur, débord, inclinaison des lames…).
2. Un lot sur les **améliorations possibles** : isolations ; ventilation ; protections solaires ; végétalisation de
   la parcelle ; isolation et albédo de toiture, albédo des murs et des pourtours — avec, pour chacune, les résultats
   où elle est pertinente (**DH** ou **consommations**), les **conditions** dans lesquelles elle peut avoir lieu, et la
   **façon de l'appliquer dans chaque simulation** (N0, N1, N2) quand elle y est applicable.
3. Des **bouquets** de solutions définis par l'utilisateur (de base : « Économiser l'énergie » et « Minimiser les
   DH ») : pour chaque bâtiment, toutes les actions possibles du bouquet sont appliquées ; chaque bouquet est un
   **potentiel atteignable**.
4. **Fiche d'identité** : afficher les potentiels par bouquet. **Parc** : comparer les potentiels par bouquet et le
   parc réel.
5. **Rendu** : une page de paramétrage et une page de rendu ; un paramétrage qui construit toutes sortes de graphes
   comparatifs et représentatifs (**Plotly**) ; exports **HTML, Word, PDF** reprenant au mieux le HTML.
6. **Fiche bâtiment** : modèles **3D** avec les pertes d'hiver par triangle, les gains d'été par triangle…
7. **Documentation** : actualisation complète des README et documentations internes de l'app.

Choix du propriétaire au cadrage : conteneur de rendu dédié pour les exports ; DH à la RE2020 (seuil adaptatif
26-28 °C, DH 26 °C gardé) ; végétalisation = arbres, sol, toiture et murs, rafraîchissement de l'air (affiché comme
hypothèse).

## Organisation

| Lot | Secteur | Qui | Dépend de |
|---|---|---|---|
| 35 | Fondation (modèles, migrations, clés de faits, routes, dépendances) | architecte | — |
| 36 | Physique : protections paramétrables, végétal, albédos, ventilation nocturne, DH RE2020, résultats par triangle | secteur P | 35 |
| 37 | Actions, bouquets et potentiels | secteur A | 35 (consomme I2/I3 tels qu'écrits) |
| 38 | Rendu — document, figures Plotly, exports, conteneur de rendu | secteur R | 35 (consomme I3-I5) |
| 39 | Rendu — interface (paramétrage, aperçu, fiche, parc, 3D) | secteur F | 35 (consomme I1, I4-I8) |
| 40 | Intégration, documentation complète, parcours réel | architecte | 36-39 |

Les lots 36 à 39 tournent **en parallèle** après la fondation, chacun dans son périmètre de fichiers (contrat § 3).
Aucun secteur ne commite, ne déploie ni ne touche un fichier partagé (contrat § 5) ; une interface qui ne tient pas
remonte en amendement, jamais en contournement.

---

## Lot 35 — Fondation   ·   statut : livré
- [x] Contrat `bt-potentiels` v1 relu ; constat revérifié au commit de départ
- [x] Modèles `Bouquet`, `Potentiel`, `RapportModele`, `ExportRendu` ; champs `Scenario.bouquet`, `.surcharges`, `.genere` ; migrations (I0)
- [x] Anciens bouquets : leurs clés deviennent des actions du catalogue à l'identique (secteur A) — `weather['bundles']` reste lisible, aucune migration de données
- [x] Clés de faits : `toiture.absorptance`, `abords.albedo`, `ventilation.nocturne_vol_h`, `ventilation.nocturne_seuil_c`, `toiture.vegetalisee`, `murs.vegetalises` (poids 0)
- [x] Routes déclarées (`urls.py`) pour I1 à I7, rendant la **forme** du contrat (vide ou 501 fixe) le temps des secteurs
- [x] Dépendances : `python-docx==1.1.2` (serveur) ; `plotly.js-dist-min` 2.35.2 figé (frontend, chargé à la demande) — `backend/vendor/` revient au secteur R
- [ ] Service `rendu` dans `docker-compose.yml` (réseau `dev-net`, sans port ni label Caddy), clé `RENDU_SERVICE_KEY` dans le `.env`, volume `rendu-exports` ; `validate-app.sh` accepte la composition — **reporté au lot 40** (bloc rapporté par R)
- [x] Tests : migrations aller/retour ; modèles ; routes présentes avec leurs droits (lecteur / gestionnaire / étranger)
**Vérifié le :** 2026-11-16 — `tests/test_fondation_potentiels.py` (3 tests) ; suite backend **556 tests OK** ; `ng build` OK.

## Lot 36 — Physique (secteur P)   ·   statut : à faire
**Protections solaires paramétrables**
- [ ] Catalogue I1 v2 : modèles par famille (fixes, mobiles, vitrage, végétal), paramètres typés et bornés (épaisseur, profondeur de débord, débords latéraux par côté, inclinaison du débord, lames : inclinaison, largeur, entraxe, transmission ; position), pilotage (fixe, planning, auto), texte d'application par niveau
- [ ] Modèle de lames (BSO, persiennes) : transmission directe selon l'angle de profil et l'inclinaison, diffuse selon la géométrie des lames
- [ ] N0/N1 : chaque paramètre a son effet ; ΔR des fermetures mobiles en hiver ; opaque ombré par les débords
- [ ] N2 : débords et joues avec épaisseur et inclinaison réelles ; mobiles et lames appliqués par triangle de baie (position partielle) depuis la fiche, plus seulement depuis Calcul 3D
- [ ] Interface `components/protections` : choisir un modèle, régler **tous** ses paramètres (groupés, aide, bornes du catalogue), modifier une protection existante, l'attacher à l'état actuel ou à une amélioration
**Surcharges d'amélioration (I2)**
- [ ] Albédo des abords (N0/N1 : albédo du sol ; N2 : grille remplacée), absorptance de toiture distincte de celle des murs
- [ ] Arbres plantés : masques saisonniers (profil d'horizon N0/N1 ; obstacles translucides N2)
- [ ] Toiture et murs végétalisés : U, inertie, albédo, puits d'évapotranspiration en surface (modèle simple, source citée)
- [ ] Rafraîchissement de l'air : T_ext corrigée (mois, heures), hypothèse recopiée dans les entrées figées
- [ ] Surventilation nocturne : débit de nuit quand T_ext < seuil (N1, N2)
- [ ] Entrées figées de chaque calcul : `inputs['ameliorations']` décrit tout ce qui a été appliqué, par niveau
**Indicateurs et sorties**
- [ ] DH RE2020 (`dh_re2020`, `heures_inconfort`, `t_conf_methode`), `dh_26` ; séries mensuelles `results['mensuel']` ; `GET /api/indicateurs/`
- [ ] N2 : cumuls par triangle (pertes d'hiver, gains d'été, solaire absorbé, températures de surface) ; `GET /api/calculs/<id>/triangles/` (I4)
- [ ] Bascule intérieure/extérieure de Calcul 3D réellement recolorée (à vérifier, probable défaut)
**Tests à passer :**
- [ ] DH RE2020 sur une série construite à la main (valeur attendue calculée à côté)
- [ ] Chaque paramètre de protection change le résultat dans chaque niveau où le catalogue le dit applicable, et seulement là
- [ ] Chaque surcharge I2 : effet de signe et d'ordre de grandeur attendus en N0, N1, N2 (table du contrat § 7)
- [ ] Cumuls par triangle : leur somme égale le flux d'enveloppe du bilan (à la tolérance près)
- [ ] Suite backend complète verte ; `ng build`
**Vérifié le :**

## Lot 37 — Actions, bouquets et potentiels (secteur A)   ·   statut : à faire
- [ ] Catalogue I7 : actions des cinq familles (isolation ; ventilation ; protections solaires ; végétalisation de la parcelle ; toiture et albédos), chacune avec paramètres, **conditions** (bloquantes ou non), **indicateurs pertinents** (énergie, DH), **cibles**, **application par niveau** en mots, points de vigilance
- [ ] Anciens `BUNDLES` repris comme actions ; améliorations existantes intactes
- [ ] Application d'un bouquet à un bâtiment : toutes les actions applicables, « une parmi » (première applicable), refus de deux éléments sur la même cible, raison de chaque action écartée
- [ ] Bouquets du lab : « Économiser l'énergie » (objectif énergie) et « Minimiser les DH » (objectif confort) ; copie dans un parc ; bouquets propres à un parc (création, modification, suppression par un gestionnaire)
- [ ] Potentiels (I5) : calcul N1 d'un bâtiment, de tout un parc (file « parc ») ; N2 d'un bâtiment à la demande (gestionnaire, jamais en lot) ; « à jour » par empreinte ; écarts au réel
- [ ] Agrégats parc : réel et chaque bouquet (somme, moyenne, médiane, effectifs)
- [ ] Interface : Paramétrage → bouquets (édition guidée, catalogue d'actions avec conditions et application) ; Calculs → Améliorations : potentiels du bâtiment, actions appliquées / écartées
**Tests à passer :**
- [ ] Conditions : une action non applicable est écartée avec sa raison ; « une parmi » prend la première applicable
- [ ] Un bouquet appliqué deux fois donne la même amélioration (recréée, pas dupliquée) et le même résultat
- [ ] Droits : lecteur lit, ne crée ni ne calcule ; étranger 404 ; N2 refusé en lot
- [ ] Potentiels d'un parc de 3 bâtiments, deux bouquets : agrégats exacts
- [ ] Suite backend complète verte ; `ng build`
**Vérifié le :**

## Lot 38 — Rendu : document, figures, exports (secteur R)   ·   statut : à faire
- [ ] Schéma de spécification I6.a servi (`/api/rendu/schema/`), dimensions et mesures (`/dimensions/`, `/mesures/`)
- [ ] Figures Plotly construites par le serveur pour **toutes** les formes I6 (barres, empilées, groupées, courbes, aires, nuage, bulles, boîte, violon, histogramme, camembert, radar, carte de chaleur, cascade, coordonnées parallèles), séries réel / bouquets, facettes, filtres, agrégations
- [ ] Document résolu (I6.c) ; bloc « fiche » repris de la fiche d'identité (lot 34) avec la section **potentiels par bouquet**
- [ ] Gabarits du lab : « Fiche d'identité », « Synthèse de parc », graphes prêts à l'emploi (réel / potentiels par bouquet, besoins mensuels, DH, postes de bilan, nuage besoin / DH, cascade des actions)
- [ ] Export **HTML** autonome : plotly.js et three.js embarqués, interactif hors ligne, sommaire cliquable, impression soignée
- [ ] Conteneur `rendu-service` (I8) : PDF (vectoriel, signets, sommaire, numéros de page), images des figures, images des vues 3D ; aucune sortie réseau
- [ ] Export **PDF** (HTML imprimé par I8) et **DOCX** (python-docx : titres et styles, sommaire actualisable, vrais tableaux, figures et vues 3D en images, légendes, liens internes)
- [ ] Fichiers transitoires 24 h, réservés à leur auteur, purge planifiée
**Tests à passer :**
- [ ] Chaque forme de graphe : figure valide (schéma Plotly) sur un parc de test et sur un bâtiment
- [ ] Un même document : HTML, DOCX (ouvert par python-docx, titres et tableaux présents) et PDF (pages, signets)
- [ ] Le conteneur de rendu refuse une requête sans clé et toute ressource réseau ; un export d'autrui est refusé
- [ ] Suite backend complète verte
**Vérifié le :**

## Lot 39 — Rendu : interface (secteur F)   ·   statut : à faire
- [ ] Onglet Rendu en deux pages : **Paramétrage du rendu** (sections, blocs, constructeur de graphes guidé : une question → un graphe proposé, puis réglages ; gabarits ; enregistrement par un gestionnaire) et **Rendu** (aperçu fidèle du document serveur, exports HTML / Word / PDF)
- [ ] Composant `graphe` : Plotly chargé à la demande, thème clair/sombre par les tokens Foyer (aucune couleur en dur), accessible (titre, résumé textuel)
- [ ] Fiche d'identité : potentiels par bouquet (écarts, actions appliquées / écartées)
- [ ] Parc : comparaison du parc réel et des potentiels par bouquet (tableau, graphes, carte)
- [ ] Bâtiment : vue 3D des résultats N2 — pertes d'hiver, gains d'été, solaire absorbé, températures de surface — échelle et légende, triangle cliquable (valeurs), lien vers le calcul
- [ ] Le bloc `vue3d` du rendu utilise la même vue
**Tests à passer :**
- [ ] Navigateur réel (compte de test), clair et sombre : construire un graphe de chaque famille, l'enregistrer, l'exporter dans les trois formats et ouvrir les fichiers
- [ ] Vue 3D sur un N2 réel ; cas sans N2 (message, lien pour le lancer si gestionnaire)
- [ ] Aucune erreur console ; budgets `ng build` tenus (Plotly hors bundle initial)
**Vérifié le :**

## Lot 40 — Intégration et documentation   ·   statut : à faire
- [ ] Rapports des secteurs vérifiés (version du contrat), fichiers partagés appliqués en un seul edit
- [ ] Déploiement séquencé (image Django, image du conteneur de rendu, frontend), `app-test.sh`, cloisonnement 3/3
- [ ] Parcours réel de bout en bout : parc d'essai → bouquets → potentiels → fiche d'identité → comparaison de parc → rendu personnalisé → exports HTML, Word, PDF ouverts → vue 3D ; parc d'essai supprimé
**Documentation (actualisation complète)**
- [ ] `README.md` : but, six onglets, parcours, protections, améliorations / bouquets / potentiels, rendu et exports, conteneur de rendu, ressources (2 vCPU), tests
- [ ] `frontend/README.md` : remplacer le gabarit Angular (structure, build, budgets, Plotly à la demande)
- [ ] `refonte.md` : § 16 avancement lots 28 à 40, § 17, décisions nouvelles (D40+)
- [ ] `FEUILLE_DE_ROUTE.md` : lots 35 à 40 livrés et vérifiés
- [ ] `backend/api/moteurs.py` : sol simulé, protubérances, protections, améliorations, DH RE2020, sorties par triangle
- [ ] Page Théorie : sol 1D, MNS, horizon par profil, protubérances, protections et lames, végétalisation, albédos, DH RE2020, application de chaque action par niveau
- [ ] `CLAUDE.md` du lab (service de rendu, pièges) et mémoire
**Vérifié le :**
