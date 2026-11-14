# Feuille de route — bilan-thermique

Ce fichier est lu par une machine : `validate-app.sh` refuse de déployer s'il manque ou s'il ne
contient aucun lot au format ci-dessous, et `app-test.sh` lit la commande de test. Garder les titres
tels quels.

App livrée avant l'obligation de feuille de route : le Lot 1 résume l'existant. Les lots suivants
viennent de l'analyse du 2026-10-31 ; la vue d'ensemble est dans `to_do_global.md`, à la racine du
dépôt du lab.

Le journal complet des lots livrés (A→AL, décisions, pièges) est dans `to_do_bilan_thermique.md`, à
côté de ce fichier. Il fait 3 249 lignes : c'est désormais une archive. Ce qui y restait ouvert est
repris ci-dessous. Légende : [C] constaté, [D] déduit.

## Commande de test
python manage.py test

## Lot 1 — Existant : 1D, bâtiment 3D, ombrage, météo, environnement   ·   statut : livré
**Objectif :** simulateur thermique de bâtiment :
- calcul 1D, puis 3D sur maillage ;
- ombrage par lancer de rayons, météo ;
- environnement IGN/LiDAR/BD TOPO, façades Panoramax.

Accès : `developers,famille,amis`.
**Tâches :**
- [x] Lots A à AL (`to_do_bilan_thermique.md`)
- [x] Lot AM (pose des façades par SfM) écrit, **désactivé par défaut** (`FACADE_SFM_ENABLED=False`, décision du 2026-10-12)
**Tests à passer avant de clore le lot :**
- [x] 244 tests backend (`backend/api/tests.py`)
**Vérifié le :** 2026-10-31 — relevé par lecture (version en service = `c308617`), tests non rejoués.

## Lot 2 — Garde-fous de calcul   ·   statut : livré
**Objectif :** un calcul ne peut pas saturer le worker partagé (2 vCPU), et deux calculs ne partent
pas en même temps. Prérequis de la refonte (`refonte.md`, § 11).
**Constat :**
- [C, d'après le journal] Point L5 : `MAX_REALTIME_RAYCAST_OPS` ne compte pas la passe végétation.
- [C] Pas de verrou de calcul : `exists()` puis création (`views.py:395,436,455`).
- [C, relevé à la fusion] `bdnb._get` et les modules réseau recopiaient `str(exc)` d'une exception
  `requests` (l'URL) dans les messages ; deux `User-Agent` portaient une adresse email personnelle.
**Tâches :**
- [x] Compter la passe végétation dans le coût estimé (`building_solver.check_realtime_cost`,
  poids `VEGETATION_PASS_WEIGHT = 2`), contrôlé **dans la vue** avant tout envoi au worker (400, `code: cost`)
- [x] Verrou atomique : `api/joblock.py` (`pg_advisory_xact_lock`, envoi Celery `on_commit`), utilisé
  par précalcul d'ombrage, calcul 3D, analyse et recomposition des façades ; un job actif depuis
  plus de 6 h est marqué en erreur au passage
- [x] `Job.owner_sub` (qui a lancé le job, `sub` Keycloak) — migration `0014`
- [x] `api/net.py` : `reason(exc)` (cause fixe, jamais le texte d'une exception `requests`),
  `public_message(exc)` pour les jobs, `User-Agent` de l'app sans donnée personnelle ; appliqué
  aux neuf modules réseau et à `tasks.py`
**Tests à passer avant de clore le lot :**
- [x] Un calcul avec végétation au-delà du seuil est refusé (`VegetationCostTest`, `VegetationCostViewTest`)
- [x] Deux lancements simultanés rendent `[202, 409]` (`ConcurrentLaunchTest.test_two_simultaneous_launches`)
- [x] Aucune URL dans un message d'erreur ; `User-Agent` sans `@` (`NoUrlInErrorsTest`)
**Vérifié le :** 2026-11-14 — `python manage.py test` dans l'image `bilan-thermique-backend`,
PostgreSQL jetable sur réseau interne (copie locale de `chantiers/tests/essai.sh`) : **257 tests OK**
(244 avant le lot, aucun supprimé). Aucun écran touché.

## Lot 3 — Historique des calculs et ménage   ·   statut : livré
**Objectif :** revoir un résultat sans recalculer ; ne plus accumuler d'objets orphelins (points U3, L6, L7).
**Tâches :**
- [x] Liste des calculs passés par bâtiment, avec un résultat rechargeable (U3) : modèle `CalcRun`
  (niveau N0/N1/N2, empreinte des entrées, indicateurs, résultat complet, `owner_sub`), créé dans la
  transaction du job, terminé par la tâche ; `GET /api/batiments/<id>/calculs/`, `GET /api/calculs/<id>/` ;
  panneau « Calculs précédents » + « Revoir » dans Calcul 3D ; au plus un calcul actif par (bâtiment, niveau)
- [x] Environnements (L6) : régénérer pour un bâtiment marque les anciens `replaced_by` (rien supprimé :
  ils peuvent porter des retraits faits à la main) ; ils sortent de la liste sauf s'ils sont encore liés
  à un bâtiment ; `?tous=1` les rend tous
- [x] Bâtiment incomplet laissé par un mode simplifié abandonné (L7) : commande
  `nettoyer_brouillons` (liste par défaut, `--appliquer` pour supprimer — jamais automatique)
- [x] Dossier `sfm/<id>` du volume `facade-cache` : supprimé après le calcul des poses et à la
  suppression du bâtiment (`facade_sfm.cleanup_workdir`, borné à `SFM_DIR`)
**Tests à passer avant de clore le lot :**
- [x] Un résultat se revoit après avoir quitté la page, sans recalcul (`CalcHistoryTest.test_result_reviewed_after_leaving_page_without_recalc`)
- [x] Régénérer 3 fois l'environnement en laisse 1 (`EnvironmentReplacedTest.test_regenerating_three_times_leaves_one`)
**Vérifié le :** 2026-11-14 — `python manage.py test` (image de l'app, base jetable) : **268 tests OK**.
`ng build` OK. Navigateur réel (Chromium de l'image `runner-runner`, banc d'essai jetable : API réelle
sur base jetable, Keycloak simulé, réseau interne) : Calcul 3D → bâtiment → « Calculs précédents » →
« Revoir » affiche le résultat de 48 h, en clair et en sombre, sans erreur console.

## Lot 4 — Mode d'emploi   ·   statut : à faire
**Objectif :** un nouvel utilisateur sait par où commencer. Il n'existe aucune doc d'usage : la page
Théorie est une documentation physique.
**Tâches :**
- [ ] Page « Démarrer » dans la nav Foyer : parcours 1D → Bâtiment → Calcul 3D, rôle et influence de chaque paramètre (U5), quand passer en temps réel (U6)
- [ ] `README.md` : but, parcours, ressources et limites (2 vCPU, volume `facade-cache`), tests
**Tests à passer avant de clore le lot :**
- [ ] Page vérifiée en navigateur réel, clair et sombre ; lien présent dans la nav
**Vérifié le :**

## Lot 5 — Gabarit front et chemin à portes   ·   statut : livré
**Objectif :** même comportement que le gabarit corrigé (T3), déploiement par le moteur (T1).
*Repassé de « en cours » à « à faire » le 2026-11-14 (refonte) : un seul lot en cours à la fois, et
l'ordre de la refonte est 2 → 3 → 5. Les tâches déjà cochées restent acquises.*
**Tâches :**
- [x] Polices en chemin relatif (`styles.scss`) ; `tokenBases()` (`core/env.ts` : la seule API de l'app ; `core/token-scope.ts`, copie de lab-admin) ; `pkceMethod: 'S256'` (front — `--pkce` sur le client reste à poser par l'architecte après déploiement) ; `redirectUri` par `core/redirect-uri.ts` (copie de lab-admin : la page courante seulement sous l'origine et le préfixe de l'app, sinon `appUrl`)
- [x] Ports publiés liés à `${LAB_BIND_ADDR:-127.0.0.1}` (chantier « failles », I1)
- [x] Renommer le service `worker` en `bilan-thermique-worker` (même `container_name` ; `--remove-orphans`
  des deux chemins de déploiement retire l'ancien service — vérifié sur un projet compose factice)
- [x] Documenter l'exception « couleurs du rendu three.js » (commentaires dans les deux fichiers + `README.md`)
- [x] Découper `backend/api/tests.py` (4 514 lignes) par domaine : `backend/api/tests/` (8 domaines +
  `helpers.py`, script AST, classes recopiées à l'identique ; tests des Lots 2 et 3 déplacés dedans)
- [x] (refonte) `lab-module.json` à la racine (`REGLES_MODULE.md` : dépendances, ressources, groupes,
  `install: null` — tout est dans l'image) ; `README.md` de l'app (dépendances, licences, limites)
- [x] (refonte) Image frontend : `npm ci` au lieu de `npm install` (piège « résolution au jour du build »)
**Tests à passer avant de clore le lot :**
- [x] `validate-app.sh bilan-thermique --quality-only` vert ; cloisonnement `ok` par le moteur ; même nombre de tests après le découpage
**Vérifié le :** 2026-11-01 — non déployé. Chantier « failles » (secteur E) : image candidate
`bilan-thermique-frontend:candidat` construite depuis une copie propre du frontend (`npm install` +
`ng build` OK ; le bundle porte `pkceMethod:"S256"`, les WOFF2 sont référencés en `./media/…`) ;
`docker compose config` rend `host_ip: 127.0.0.1` pour les deux ports publiés (backend, frontend ;
`worker` et `redis` n'en publient pas). Backend non modifié. Restent le renommage du `worker`, la
documentation des couleurs three.js et le découpage des tests (hors chantier).
**Vérifié le 2026-11-14 (refonte) :** `bash scripts/validate-app.sh bilan-thermique --quality-only` code 0 ;
contrôle complet : seul refus « arbre de travail modifié » (avant commit, attendu) ;
`bash scripts/lab-modules.sh show bilan-thermique --check` → « rien ne manque » ; tests **268 avant et
268 après le découpage**, tous OK ; `ng build` OK. Cloisonnement : dernier verdict du moteur `ok`
(2026-11-01) ; non rejoué — cette session n'a pas d'accès au moteur (voir `refonte.md`, rapport).
**Relecture du 2026-11-08 (nuit) :** statut « en cours » juste (seul lot en cours). Déjà acquis pour la
case de test : `validate-app.sh --quality-only` code 0 (copie de ce dossier, outil `valq.sh`) et
cloisonnement `ok` par le moteur le 2026-11-01 (`<LAB_STATE_DIR>/tests/bilan-thermique.json`) ; le
`--pkce` du client a été posé au chantier « failles ». Restent les trois tâches non cochées :
`docker-compose.yml:55` nomme toujours le service `worker`, `backend/api/tests.py` fait toujours 4 514 lignes.

## Lot 6 — Façades par SfM à la demande (Lot AM)   ·   statut : à faire
**Objectif :** Ⓟ facultatif. Rendre le Lot AM utilisable sans option globale.
**Tâches :**
- [ ] Bouton explicite par bâtiment plutôt que `FACADE_SFM_ENABLED`, et affichage de la source de la pose
- [ ] Première analyse réelle sur un bâtiment couvert par Panoramax
**Tests à passer avant de clore le lot :**
- [ ] Une analyse réelle aboutit et indique sa source
**Vérifié le :**

## Lot 7 — Pistes écartées ou différées   ·   statut : à faire
**Objectif :** mémoire des pistes. Rien à faire sans décision du propriétaire.
**Tâches :**
- [ ] U4 : choisir les coordonnées sur une carte (aucune bibliothèque cartographique dans le frontend)
- [ ] Texture des bâtiments voisins
- [ ] Écartés par décision : Lot Z3 (saisonnalité de la végétation), paroi mitoyenne
**Tests à passer avant de clore le lot :**
- [ ] À définir avec chaque tâche retenue
**Vérifié le :**

## Lot 8 — Provenance : faits, fiche effective, surcharges   ·   statut : livré
**Objectif :** chaque valeur de la fiche d'un bâtiment garde sa provenance (`refonte.md`, principes 2 et 3,
§ 8) ; une correction est une surcharge réversible ; la confiance et la complétude se calculent.
**Tâches :**
- [x] Catalogue de clés (`api/facts.py` : identité, enveloppe, menuiseries, ventilation, usage, systèmes,
  DPE ; type, unité, plage plausible, poids) et validation à message fixe
- [x] `BuildingFact` (valeur, méthode `mesure > document > declare > infere > defaut`, source, confiance,
  auteur par `sub`, extrait cité, `retired_at`, `superseded_by`) et `Scenario` (variante, utilisée au Lot 18)
- [x] Fiche effective (rang puis date ; surcharge de variante prioritaire), empreinte `fiche_hash`,
  scores `Building.confidence` / `completeness` tenus à jour
- [x] Réimportation automatique : remplace l'ancienne valeur automatique, jamais une surcharge
- [x] API : `GET /api/fiche/catalogue/`, `GET /api/batiments/<id>/fiche/`, `GET|POST /api/batiments/<id>/faits/`,
  `DELETE /api/faits/<id>/` (« revenir à l'automatique », auteur seulement ; méthodes système refusées)
- [x] Page « Fiche bâtiment » (`/fiche?b=<id>`) : provenance lisible, Corriger, Revenir à l'automatique,
  historique ; classes partagées `rf-` dans `styles.scss`
**Tests à passer avant de clore le lot :**
- [x] Une surcharge retirée rend la valeur automatique (`FactLogicTest.test_retired_override_restores_automatic_value`,
  `FicheApiTest.test_override_then_back_to_automatic`)
- [x] La réimportation n'écrase pas une surcharge ; la confiance monte avec la méthode (`test_reimport_never_overwrites_override`,
  `test_scores_rise_with_corrections`)
**Vérifié le :** 2026-11-14 — 282 tests OK (14 nouveaux) ; `ng build` OK ; navigateur réel (banc jetable) :
corriger « U des murs » → badge « déclaré » + valeur automatique visible → « Revenir à l'automatique » →
« inféré », en clair et en sombre, sans erreur console ; 390 px de large sans défilement horizontal.

## Lot 9 — Fiche initiale automatique : BDNB complète et archétypes   ·   statut : livré
**Objectif :** une adresse (ou un identifiant RNB, ou une position) suffit à remplir la fiche depuis
l'open data, sans saisie (`refonte.md` § 4.2, étapes A1 à A6).
**Tâches :**
- [x] `api/sources.py` : géocodage (Géoplateforme), RNB `closest` et détail, BDNB (groupe complet + DPE
  représentatif) ; cache `SourceCache` par clé et durée de vie ; quota BDNB (≥ 0,5 s entre deux requêtes) ;
  `User-Agent` de l'app ; erreurs à message fixe
- [x] `api/pipeline.py` : correspondances BDNB/DPE → clés de la fiche (U des parois, vitrage, inertie,
  isolation, ventilation, générateur, ECS, étiquette ; DPE d'une maison = `document`, d'un immeuble = `infere`),
  géométrie BD TOPO (emprise, murs exposés par orientation, **mitoyenneté**, horizon par façade),
  enveloppe LOD1 pour le N2, archétypes (`api/archetypes.py`, D14) sous tout le reste
- [x] Une étape en échec n'arrête pas la chaîne (« incomplet » + raison) ; rejouer ne duplique rien et ne
  touche aucune surcharge ; **une surcharge prime sur toute valeur automatique** (D20)
- [x] `POST /api/batiments/initialiser/`, `POST /api/batiments/<id>/initialiser/`, tâche `init_building` ;
  champs `rnb_id`, `address`, `pipeline_status`, `pipeline`, `confidence`, `completeness` (lecture) ;
  commande `initialiser_adresses` ; carte « Fiche initiale automatique » de la page Fiche
**Tests à passer avant de clore le lot :**
- [x] 10 adresses réelles initialisées sans saisie (recette réelle, ci-dessous)
- [x] Tests sans réseau : correspondances sur une réponse BDNB relevée, géométrie (mitoyenneté, horizon),
  chaîne simulée complète / en échec / rejouée, cache, quota (`api/tests/test_fiche_auto.py`)
**Vérifié le :** 2026-11-14 — 301 tests OK. Recette réelle (conteneur jetable de l'image, accès Internet,
base jetable) : `initialiser_adresses` sur 10 adresses de Tours, Orléans et Blois → **10/10 « complet »**,
1,0 à 2,3 s par bâtiment ; confiance 0,18 à 0,39, complétude 0,15 à 0,72 (selon la présence d'un DPE).
Navigateur réel (banc sans Internet) : initialisation → « incomplet », étapes et messages fixes affichés,
valeurs d'archétype posées, en clair et en sombre, sans erreur console.

## Lot 10 — Moteurs N0 (mensuel) et N1 (horaire 5R1C), comparaison à N2   ·   statut : livré
**Objectif :** un résultat en secondes depuis la fiche, comparable au N2 (`refonte.md` § 6, D5, D12).
**Tâches :**
- [x] `api/engines/inputs.py` : traducteur fiche → paramètres (murs par orientation, mitoyenneté, baies,
  ventilation effective, inertie, plancher couplé au sol, horizon) et traducteur maillage N2 → mêmes paramètres
- [x] `api/engines/n1.py` : 5R1C ISO 13790 annexe C, Crank-Nicolson, chauffage et froid, température
  opérative, bilan par poste, mois ; **vectorisé** sur S jeux de paramètres (prêt pour Monte-Carlo)
- [x] `api/engines/n0.py` : méthode mensuelle (facteur d'utilisation des apports)
- [x] `api/calc_service.py` : météo de l'année type du site (PVGIS, repli Open-Meteo, en cache), `CalcRun`
  avec indicateurs et **faits utilisés** (traçabilité), empreinte des entrées : pas de recalcul identique,
  « à jour / périmé », et le calcul correspondant à la fiche actuelle est repris s'il existe
- [x] `POST /api/batiments/<id>/calculer/`, `GET /api/batiments/<id>/resultat/` ; la chaîne automatique
  enchaîne N0 puis N1 (étape A8) ; carte « Résultat » de la fiche ; section 13 bis de la page Théorie
**Tests à passer avant de clore le lot :**
- [x] Écart N1/N2 sous le seuil (20 %, D12) sur les 3 cas de recette (`N1VersusN2Test`)
- [x] Régime permanent conforme à l'oracle du 5R1C ; effets dans le bon sens (U, mitoyenneté, consigne, horizon)
**Vérifié le :** 2026-11-14 — 313 tests OK. Écarts mesurés sur année synthétique : N1/N2 −3,4 % (boîte),
+6,0 % (maison vitrée au sud), −15,3 % (petit collectif) ; N0/N2 +5 %, +33 %, −1 %. Durées : N2 ≈ 2,2 s
(12 à 20 triangles), N1 0,5 à 0,9 s, N0 < 0,1 s. Navigateur réel (banc) : Calculer → « à jour », indicateurs,
mois, bilan ; corriger « U des murs » → « périmé » ; recalcul 173 → 91 kWh/m² ; revenir à l'automatique →
de nouveau « à jour » sans recalcul ; clair et sombre sans erreur console.

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 244 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement `lab-runner` 3/3 avant et après le durcissement du client, puis de nouveau 3/3 sur Keycloak 26.8.1.
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).
