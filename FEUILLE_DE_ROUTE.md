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
- [x] U4 : choisir les coordonnées sur une carte — fait au Lot 19 (Leaflet)
- [ ] Texture des bâtiments voisins
- [x] Lot Z3 (saisonnalité de la végétation) : repris en protection paramétrique (D8) ; paroi mitoyenne : faite au Lot 12
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

## Lot 11 — Parc : import, file `parc`, tableau, export, partage   ·   statut : livré
**Objectif :** un gestionnaire importe un parc et obtient, sans saisie, un tableau chiffré avec
fourchette, confiance et complétude (`refonte.md` § 4, D1-D4, D13).
**Tâches :**
- [x] `Portfolio`, `PortfolioMember` (gestionnaire de parc / de bâtiment / lecteur), `BuildingAccess`
  (gestionnaire / occupant), `Invitation` (lien à usage limité, empreinte seule, aucun annuaire) ;
  `Building.name` unique par parc et parmi les bâtiments communs
- [x] Cloisonnement (`api/access.py`) appliqué aux nouvelles routes **et aux anciennes** (liste, détail,
  précalcul, calcul 3D, façades, génération d'environnement) — sans effet sur les bâtiments sans parc (D4) ;
  un job n'est rendu qu'à celui qui l'a lancé
- [x] Import CSV (en-têtes français/anglais, `;` ou `,`, colonnes facultatives → faits **déclarés**),
  traitement par la file `parc` (un bâtiment par tâche, file interactive consultée d'abord, reprise
  des bâtiments bloqués), N0 + N1 avec **fourchette P10–P90** (48 tirages vectorisés, `api/uncertainty.py`)
- [x] Tableau (besoin, fourchette, DPE, heures > 28 °C, confiance, complétude, priorité), export CSV
  (formules neutralisées), rattacher un bâtiment commun ; pages « Parcs » et « Invitation »
- [x] Contrôle de cohérence de la surface d'un DPE « maison » (écart > ×2 avec emprise × niveaux)
- [x] Worker : `-Q celery,parc --prefetch-multiplier=1`, `queue_order_strategy: priority` ; commande `traiter_parc`
**Tests à passer avant de clore le lot :**
- [x] Parc de 20 adresses réelles traité sans intervention (recette réelle, ci-dessous ; fichier
  `backend/api/management/commands/recette_parc20.csv`)
- [x] Cloisonnement : tiers, lecteur, gestionnaire de bâtiment, occupant, invitations (`ParcAccessTest`, 12 tests)
**Vérifié le :** 2026-11-14 — 336 tests OK. Recette réelle (`traiter_parc`, conteneur jetable avec Internet,
base jetable) : **19/20 complets**, 85 s au total, **4,2 s par bâtiment** (réseau, chaîne, N0, N1 à 48 tirages) ;
le 20ᵉ (« avenue de Sévigné », sans numéro) n'a pas de bâtiment RNB au point géocodé, la raison est affichée.
Limite de données constatée : un point d'adresse à l'entrée d'un grand site peut désigner la loge
(musée de Tours, hôtel de ville de Blois : 68–113 m²) — confiance basse, à corriger à l'affinage.
Navigateur réel (banc, worker `celery,parc`) : création, import de 5 bâtiments, traitement complet par la
file `parc`, tableau avec 5 fourchettes, export téléchargé, lien d'invitation « occupant » accepté par un
second compte (une seule ligne visible, pas d'import), tiers sans aucun parc ; clair et sombre sans erreur.

## Lot 12 — Formulaires guidés et mitoyenneté (E9)   ·   statut : livré
**Objectif :** le gestionnaire ou l'occupant répond à des questions simples ; chaque réponse devient un
fait déclaré, fait bouger le résultat et la confiance (`refonte.md` § B1, E9).
**Tâches :**
- [x] `api/questionnaire.py` : 15 questions en langage courant, traduites en faits (U des murs déduit de
  l'épaisseur d'isolant, +10 % en ITI ; U de toiture ; fenêtres → Uw, g ; ventilation ; étanchéité → n50 ;
  mitoyenneté) ; une réponse sur l'année ou l'usage recalcule les défauts de l'archétype
- [x] Classement par **influence × incertitude** : sensibilité N1 vectorisée (±1 écart type de la méthode
  de chaque valeur), les 3 à 5 questions les plus utiles d'abord ; incohérences signalées (9 règles)
- [x] `GET /api/batiments/<id>/questions/`, `POST /api/batiments/<id>/questionnaire/` (N0/N1 relancés,
  avant/après, confiance avant/après) ; carte « Questions » de la fiche
- [x] Paroi mitoyenne dans le N2 : limite `adiabatic` (flux nul, sans soleil) — sérialiseur, solveur,
  page Bâtiment (« Mitoyen chauffé ») ; N0/N1 l'avaient déjà par `identite.mitoyennete`
**Tests à passer avant de clore le lot :**
- [x] Une réponse fait bouger le résultat et la confiance (`QuestionnaireApiTest.test_answer_moves_result_and_confidence`)
- [x] Façades mitoyennes : besoin N2 réduit (`PartyWallN2Test`)
**Vérifié le :** 2026-11-14 — 348 tests OK ; `ng build` OK ; navigateur réel (banc) : 5 questions prioritaires
avec influence en kWh, « murs isolés par l'extérieur, 14 cm » → besoin 173 → 85 kWh/m²·an, confiance
14 → 20 %, carte Résultat « à jour » ; clair et sombre sans erreur console.

## Lot 13 — Météo choisie et comparée (B2)   ·   statut : livré
**Objectif :** choisir la météo d'un calcul (année type, période réelle, station Météo-France, climat
futur TRACC, import) et comparer ses effets (`refonte.md` § B2).
**Tâches :**
- [x] `WeatherDataset` (série horaire stockée une fois par empreinte, statistiques, qualité) ;
  `Building.weather_dataset` (météo retenue pour N0/N1, défaut : année type du site)
- [x] `api/weather_service.py` : année type PVGIS ; réanalyse Open-Meteo sur une période ; **stations
  Météo-France** (fichiers horaires par département lus en flux, rayonnement de la station décomposé par
  Erbs, sinon Open-Meteo ; trous ≤ 3 h comblés et comptés) ; **TRACC** +2 / +2,7 / +4 °C avec ou sans vague
  de chaleur (station de référence la plus proche) ; imports EPW et CSV ; découpage par période
- [x] Jobs `build_weather_dataset`, `list_weather_stations` ; routes `/api/meteo/…`, `PUT …/meteo/`,
  `POST …/comparer-meteo/` (N1 sous chaque météo, DJU, ensoleillement) ; carte « Météo » de la fiche
**Tests à passer avant de clore le lot :**
- [x] Même bâtiment, 3 météos, 3 résultats comparés (`MeteoApiTest.test_same_building_three_weathers_three_results`)
- [x] Lecture des fichiers Météo-France, TRACC, EPW, CSV ; trous ; périodes ; cache (`test_meteo_choix.py`, 19 tests)
**Vérifié le :** 2026-11-14 — 367 tests OK. Sources réelles (conteneur jetable avec Internet) : 15 stations à
moins de 60 km de Tours en 9 s ; station TOURS (37179001) année 2023 : 8 760 h, aucune heure manquante, 8 s
(DJU 1 982, T moy. 13,6 °C) ; TRACC +2,7 °C station Tours : 8 760 h en 1 s (T max 43,2 °C, 190 h > 30 °C) ;
année type PVGIS (DJU 2 268). Navigateur réel (banc) : 3 météos comparées → 186 / 234 / 119 kWh/m², choix
d'une météo → résultat à refaire, import CSV ; clair et sombre sans erreur console.

## Lot 14 — Protections solaires (B4)   ·   statut : livré
**Objectif :** déclarer des protections fixes, mobiles, de vitrage ou végétales et voir leur effet sur
le confort d'été et le chauffage, avec deux méthodes cohérentes (`refonte.md` § B4, D8).
**Tâches :**
- [x] `ShadingDevice` (type, façade, mode paramétrique ou géométrique, paramètres, pilotage, variante)
- [x] `api/engines/shading.py` : débord (casquette, balcon, débord de toit, loggia) et joues par angle de
  profil, diffus masqué ; mobiles à position intermédiaire, pilotés par planning (heures, mois) ou
  automatiquement (éclairement sur la façade, T ext., absence) ; film / vitrage (g) ; arbre caduc et
  pergola (été / hiver) ; intégré au N1 et au N0 (gains par façade rendus)
- [x] Mode géométrique : panneaux générés au-dessus / à côté des baies de la façade, ajoutés aux obstacles
  du précalcul d'ombrage et du temps réel du N2 (`shading_service.geometric_occluders`) ; l'ombrage est marqué périmé
- [x] Routes `/api/protections/catalogue/`, `…/protections/`, `…/protections/effet/` (sans / toutes /
  chacune : heures > 26 et 28 °C, degrés-heures, T op. max, chauffage, apports par façade) ;
  carte « Protections solaires » ; **fiche en onglets** (Synthèse, Météo, Protections, Données)
**Tests à passer avant de clore le lot :**
- [x] Casquette sud : réduction des heures chaudes cohérente entre les 2 méthodes (`ParametricVersusGeometricTest`)
- [x] Fraction ensoleillée paramétrique = lancer de rayons à 0,1 près sur 6 positions du soleil
**Vérifié le :** 2026-11-14 — 378 tests OK. Mesures : fraction ensoleillée paramétrique / lancer de rayons, écart
maximal 0,067 ; degrés-heures > 26 °C sur 45 jours d'été réduits de **69,6 % (N1 paramétrique)** et
**87,6 % (N2 géométrique)**. Navigateur réel (banc) : casquette + store automatique au sud → résultat
« périmé », effet 59 → 0 degrés-heures ; rejeu de tous les scénarios des Lots 3 à 14 avec les onglets ;
clair et sombre sans erreur console.

## Lot 15 — Géométrie : emprise, hauteurs, toiture, baies (B3)   ·   statut : livré
**Objectif :** corriger la géométrie automatique sans la perdre ; l'enveloppe régénérée est validée
avant tout calcul (`refonte.md` § B3).
**Tâches :**
- [x] La chaîne garde l'emprise (repère local) et jusqu'à 40 voisins (`Building.pipeline`)
- [x] `GeometryEdit` (surcharge : emprise, hauteur à l'égout, niveaux, toiture, pente ; résumé recalculé)
  et `api/geometry_service.py` : validation (polygone simple, 3 à 60 sommets, ≥ 5 m², hauteurs, pente),
  mitoyenneté et horizon recalculés avec les voisins, faits déclarés, enveloppe N2 régénérée — toit
  plat, deux pans (pignons) ou quatre pans sur le rectangle orienté minimal — et **vérifiée** (fermée,
  normales sortantes, aucun triangle dégénéré) ; jamais d'écrasement d'une enveloppe aux parois
  assignées à la main sans le demander ; « revenir à l'automatique » ; écart affiché
- [x] Clés `toiture.type`, `toiture.pente_deg` ; surface de toiture inclinée dans N0/N1 ; N0/N1 lisent la
  géométrie effective (édition, sinon automatique)
- [x] `GET|POST|DELETE /api/batiments/<id>/geometrie/` ; onglet « Géométrie » : éditeur SVG (sommets
  déplaçables, ajout, suppression, déplacement au clavier), hauteurs, toiture, taux de vitrage par façade
**Tests à passer avant de clore le lot :**
- [x] Édition → enveloppe valide → calcul (`GeometryEditTest.test_edit_regenerates_valid_envelope_then_computes`)
- [x] Toits plat, deux pans, quatre pans fermés ; repli terrasse ; validations (`EnvelopeTest`)
**Vérifié le :** 2026-11-14 — 386 tests OK. Navigateur réel (banc) : glisser un sommet (80 → 123 m²), ajouter
puis supprimer un sommet, hauteur 9 m, 3 niveaux, deux pans → « enveloppe régénérée et vérifiée », écart
« hauteur 6 → 9 m » affiché, calcul « à jour » ; 390 px sans défilement horizontal ; clair et sombre sans
erreur console (une erreur `NaN` de la vue SVG avant chargement, trouvée par ce contrôle, est corrigée).

## Lot 16 — Systèmes et consommations finales et primaires (B5)   ·   statut : livré
**Objectif :** passer du besoin à la consommation (énergie finale par énergie, primaire, CO₂, coût) et
la comparer à l'étiquette DPE affichée (`refonte.md` § B5, D6).
**Tâches :**
- [x] `api/engines/systems.py` : rendements génération × émission × distribution × régulation (3CL), PAC à
  COP horaire selon T ext. et température des émetteurs, ECS, refroidissement, éclairage, auxiliaires
  (cinq usages), facteurs primaire et CO₂ de l'arrêté DPE 2021, prix indicatifs, étiquette du modèle
  (double seuil EP / GES), écart à la consommation DPE déclarée
- [x] Calculs N0/N1 : `results.energy` et indicateurs (finale, primaire, CO₂, coût, étiquette, écart) ;
  colonnes du tableau de parc et de l'export
- [x] Onglet « Systèmes » (générateur, émetteurs, régulation, ECS, rendement ; consommations par usage et
  par énergie) ; énergie primaire et écart au DPE sur la carte Résultat ; page Théorie
**Tests à passer avant de clore le lot :**
- [x] Écart à l'étiquette DPE affiché (`CalcWithSystemsTest.test_run_indicators_show_gap_to_dpe`)
- [x] COP horaire, chaudière à condensation, facteur 2,3, double seuil (`SystemsTest`)
**Vérifié le :** 2026-11-14 — 391 tests OK ; navigateur réel (banc) : énergie primaire et « écart DPE »
sur la carte Résultat ; passage à une PAC sur plancher → CO₂ 64 → 5 kg/m²·an, COP saisonnier affiché,
étiquette du modèle face au DPE affiché « E » ; clair et sombre sans erreur console. COP ramené à une
fraction de Carnot de 0,40 après relecture (5,0 sur plancher semblait optimiste).

## Lot 17 — Mesures, calage, sensibilité, fourchettes (B6, § 6.4)   ·   statut : livré
**Objectif :** caler le modèle sur des consommations réelles avec des critères reconnus, classer les
paramètres par influence, afficher des fourchettes (`refonte.md` § B6, § 6.4, D7, D23).
**Tâches :**
- [x] `Measurement` (factures, courbe Linky/Gazpar CSV agrégée par mois, sonde, test d'étanchéité →
  fait « mesuré » `ventilation.n50`) ; données personnelles visibles de leur auteur et des gestionnaires
  du bâtiment de parc seulement ; fichier d'origine non gardé (DL-2)
- [x] `api/calibration.py` : criblage de **Morris** (effets élémentaires, un passage N1 vectorisé) ;
  **calage** sur la météo réelle de la période facturée (météo datée retenue si elle couvre la période,
  sinon réanalyse), optimisation bornée L-BFGS-B (± 2 écarts types) des 4 paramètres les plus influents
  parmi les valeurs **non déclarées** et hors géométrie, gradient par différences finies calculé en un
  seul passage vectorisé ; critères **ASHRAE 14** mensuels (CV(RMSE) ≤ 15 %, |NMBE| ≤ 5 %) ; proposition
  appliquée seulement à la demande, en « inféré par calage »
- [x] Fourchettes P10–P90 : en place depuis le Lot 11 (D23)
- [x] Routes `…/mesures/`, `…/sensibilite/`, `…/calage/`, `…/calage/appliquer/` ; onglet « Mesures et calage »
**Tests à passer avant de clore le lot :**
- [x] Calage mensuel conforme aux critères (`MorrisAndCalibrationTest.test_twin_calibration_meets_ashrae_criteria`)
- [x] Déclarations jamais calées ; mesures privées ; application par l'auteur du calage seulement
**Vérifié le :** 2026-11-14 — 398 tests OK. Cas réel de météo : station Météo-France TOURS 2023 (lue en
flux), factures de gaz mensuelles produites par le modèle avec des valeurs cachées (U murs 0,6, n50 4,
consigne 20 °C) et un bruit de 3 % — **aucune facture réelle n'était disponible** : CV(RMSE) 19,3 → 5,5 %,
NMBE −14,0 → +1,9 %, conforme, 29 s, 130 simulations. Les valeurs retrouvées ne sont pas les valeurs
cachées (équifinalité) : c'est dit dans l'interface. Calage d'abord 10 fois trop lent (529 s pour la
suite du lot) : gradient vectorisé. Navigateur réel (banc) : Morris, calage conforme, application,
provenance « calage sur factures » ; un autre compte ne voit pas les factures ; clair et sombre sans
erreur. Défaut trouvé par le navigateur (booléen numpy non sérialisable dans `Job.result`) : corrigé, testé.

## Lot 18 — Scénarios et comparateur (B7)   ·   statut : livré
**Objectif :** comparer l'état actuel et des variantes de travaux, sous la météo actuelle et un climat
futur, avec les points de vigilance non thermiques (`refonte.md` § B7, principe 4).
**Tâches :**
- [x] Variantes (`Scenario`) : faits, protections solaires, géométrie et météo propres ; la base ne change
  jamais ; 10 bouquets de travaux (ITE, ITI, combles, menuiseries, triple vitrage, VMC hygro, double flux,
  PAC, stores et casquettes au sud) traduits par le questionnaire ; points de vigilance par bouquet
- [x] Comparateur : N1 avec fourchette et consommations pour chaque variante × météo (jusqu'à 5 × 3) ;
  la météo propre à une variante est reprise par les calculs
- [x] Routes `…/scenarios/`, `/api/scenarios/bouquets/`, `…/comparer/` ; onglet « Scénarios »
**Tests à passer avant de clore le lot :**
- [x] Base et 2 scénarios comparés (`ScenarioTest.test_base_and_two_scenarios_compared`)
- [x] Une variante ne touche pas la base ; climat futur et protections ; validations et droits
**Vérifié le :** 2026-11-14 — 402 tests OK. Navigateur réel (banc) : deux variantes créées par bouquets, comparaison
base + 2 variantes × (année type, TRACC +4 °C) = 6 lignes, besoin décroissant de l'état actuel à la
rénovation globale, points de vigilance ; clair et sombre sans erreur console.

## Lot 19 — Carte (U4) et vue parc cartographique   ·   statut : livré
**Objectif :** voir le parc sur une carte, y sélectionner des bâtiments, et corriger la position d'un
bâtiment à la carte (`refonte.md` § 4.1, § 4.3, U4, D15).
**Tâches :**
- [x] Dépendance **Leaflet 1.9.4** (épinglée ; types résolus au 2026-10-31), fonds IGN Plan v2 et
  orthophotos de la Géoplateforme (sans clé, aucun jeton joint) ; composant `app-carte` réutilisable,
  couleurs par jetons Foyer lus à l'exécution
- [x] Parc : vue « Carte » (couleur selon besoin, confiance, heures > 28 °C ou étiquette, légende, fiche
  en un clic) ; sélection d'emprises BD TOPO de la zone affichée (`POST /api/carte/batiments/`, zone de
  800 m de côté au plus, en cache) et ajout au parc (`POST /api/parcs/<id>/ajouter/`, analyse par la file `parc`)
- [x] U4 : carte « Position » de l'onglet Géométrie (clic → nouvelle position, enregistrement par la route
  existante, chaîne automatique rejouable)
**Tests à passer avant de clore le lot :**
- [x] Sélection de bâtiments à la carte (`MapTest.test_select_buildings_on_map_adds_them_to_parc`, navigateur réel)
**Vérifié le :** 2026-11-14 — 405 tests OK ; `ng build` OK ; navigateur réel (banc ; tuiles IGN et emprises
BD TOPO servies par `page.route`, le banc n'ayant pas Internet) : carte affichée, 2 emprises sélectionnées et
ajoutées au parc, marqueurs et légende selon l'indicateur, position corrigée au clic et enregistrée ;
clair et sombre sans erreur console.

## Lot 20 — Multizone / radiatif   ·   statut : livré
**Objectif :** périmètre tranché par la décision D9 de `refonte.md` : **pas de multizone** dans cette
refonte ; livrer la part qui sert le confort d'été — une température opérative comparable entre N1 et N2.
La paroi mitoyenne est au Lot 12.
**Tâches :**
- [x] N2 : température radiante moyenne (faces intérieures pondérées par l'aire) et température opérative
  heure par heure (`t_mrt`, `t_op`), rendues par le calcul 3D et enregistrées dans `CalcRun`
- [x] Les heures > 26 / 28 °C et les degrés-heures de tous les niveaux se comptent sur la température
  opérative (`calc_runs.indicators_from` la préfère à la température d'air) ; page Théorie
- [ ] Multizone (N nœuds d'air, refends) : hors périmètre (D9) — chemin décrit dans le journal, section « Hors scope »
**Tests à passer avant de clore le lot :**
- [x] Définition de θ_op, régime d'hiver (θ_rm entre l'air et l'extérieur), indicateurs sur θ_op (`OperativeTemperatureN2Test`)
**Vérifié le :** 2026-11-14 — 408 tests OK (dont tous les tests du N2 existants, inchangés).

## Lot 21 — IA documentaire sur le compte de l'utilisateur   ·   statut : livré
**Objectif :** l'IA lit les documents d'un bâtiment et **propose** des valeurs ; elle ne calcule jamais la
physique ; rien n'entre dans la fiche sans validation ; chaque utilisateur paie sa propre IA (refonte § B8).
**Tâches :**
- [x] Coffre `AiKeyVault` par `sub` (`EncryptedTextField`, `VAULT_ENCRYPTION_KEY`), registre fermé
  claude / mistral / albert, aucune URL saisie, `sk-ant-oat…` refusé, clé jamais renvoyée ni journalisée
  (copie d'App Builder `0ff4e4e`, en-têtes « copie d'app-builder »)
- [x] Documents : dépôt (PDF, XML DPE, texte ; 15 Mo), texte lu sur le serveur, original dans `storage`
  si relié (DL-2), sinon non conservé
- [x] XML DPE lu sans IA ; PDF : estimation des pages et des jetons, confirmation explicite, job avec la
  clé de l'**auteur** du document seulement ; boucle bornée (3 appels, 90 s), outil `proposer_faits`
- [x] Contrôle de chaque proposition (plage, cohérence, extrait retrouvé) ; acceptation = fait `document`
  avec extrait et page ; rejet conservé
- [x] Pages « Intelligence artificielle » (`/ia`) et onglet « Documents » de la fiche
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_ia.py` (15) : clé jamais rendue, `sk-ant-oat` refusé, coffre fermé, XML à entités refusé,
  XML DPE → propositions, extraction simulée, extrait introuvable rejeté, clé d'un autre jamais utilisée,
  confirmation exigée, document d'un autre invisible
- [x] Navigateur (banc, clair et sombre) : clé enregistrée « configurée » seulement, XML DPE → 5 propositions,
  acceptation → provenance « document », estimation avant envoi
**Vérifié le :** 2026-11-15 — 423 tests OK ; aucun appel réel à un fournisseur (tous simulés).

## Lot 22 — Serveur MCP et jetons personnels   ·   statut : livré
**Objectif :** la voie « abonnement » : le Claude Code ou le Mistral Vibe de l'utilisateur pilote la fiche par
le serveur MCP de l'app, avec un jeton personnel ; l'app n'appelle aucune IA (refonte § B8).
**Tâches :**
- [x] `McpToken` : préfixe `btt_`, seule l'empreinte sha256 gardée, 90 jours au plus, révocable, portée
  `lire` / `ecrire`, liaison facultative à un bâtiment ou à un parc, identité par `sub`
- [x] `POST /api/mcp/` (JSON-RPC 2.0 sans état, modèle App Builder `0ff4e4e`) : n'accepte que `btt_` ; un `btt_`
  n'ouvre rien d'autre ; journal sans arguments ni jeton
- [x] Outils : lister_batiments, lire_catalogue, lire_fiche, lire_resultats, lister_documents, lire_document,
  lister_propositions, proposer_faits, lancer_calcul, comparer_scenarios, appliquer_propositions
- [x] Page « Intelligence artificielle » : créer (jeton affiché une fois), commandes Claude Code / Vibe, révoquer
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_mcp.py` (13) : expiré, révoqué, inconnu (même message), jeton d'autrui, Keycloak refusé sur
  `/api/mcp/`, `btt_` refusé ailleurs, portée `lire` sans outil d'écriture, jeton lié à un bâtiment, droits du
  titulaire (parc d'autrui, document d'autrui, lecteur), membre retiré, proposer puis appliquer
- [x] Navigateur (banc, clair et sombre) + appel MCP réel par HTTP : initialize, tools/list, lire_fiche,
  dernier usage, révocation → 401
**Vérifié le :** 2026-11-15 — 436 tests OK. Non éprouvé : un vrai Claude Code relié (aucun abonnement utilisé).

## Lot 23 — N2 calculé depuis la fiche   ·   statut : livré
**Objectif :** une seule source de vérité. Le N2 (FEM par triangle) se lance depuis la fiche comme N0/N1,
sans retourner sur les pages expertes ; il dit « à jour » ou « périmé » ; scénarios et comparaison N0/N1/N2
portent sur le même bâtiment (décisions D34 à D37 de `refonte.md`).
**Tâches :**
- [x] `api/engines/n2_inputs.py` : fiche effective → enveloppe (emprise corrigée ou automatique, toiture,
  baies par façade d'après `baies.ratio_*`), parois reconstituées dont le U **égale** celui de la fiche (murs,
  toiture, plancher, vitrages), réglages (ventilation, consignes, apports), voisins de la chaîne en obstacles
- [x] Mode « géométrie experte » : une enveloppe entièrement assignée à la main est prise telle quelle
  (D35) ; ventilation, consignes, apports et météo viennent toujours de la fiche
- [x] `run_level(…, 'N2')` en tâche de fond (verrou du lab), empreinte des entrées, `CalcRun.inputs`
  (entrées figées : faits, géométrie, parois par groupe, réglages, météo) ; `up_to_date` calculé pour N2
- [x] `POST /api/batiments/<id>/calculer/` accepte `N2` (gestionnaire seulement, jamais en lot) ; carte
  « Résultat » : bouton N2 et état
**Tests à passer avant de clore le lot :**
- [x] U des parois reconstituées = U de la fiche (à 1 % près) ; enveloppe fermée ; taux de vitrage par façade
- [x] Écart N1 / N2-fiche sous 20 % sur les trois cas de recette ; changer un fait rend le N2 périmé
- [x] Mode expert : parois assignées conservées ; un occupant ne lance pas N2 ; entrées figées enregistrées
**Vérifié le :** 2026-11-15 — 452 tests OK (16 nouveaux, `api/tests/test_n2_fiche.py`). Écart N1 / N2-fiche sur les trois
cas de recette : boîte −5,3 %, maison −0,1 %, collectif −0,6 % (N2 : 2 à 10 s, 12 à 108 triangles). Navigateur réel
(banc : Keycloak simulé, API et worker réels), clair et sombre : N1 puis N2 lancés depuis la fiche, progression suivie,
N2 « à jour », +1 % par rapport à N1 ; une correction le rend « périmé », revenir à l'automatique le rend « à jour » ;
un occupant voit le résultat sans bouton N2 ; aucune erreur console. Le solveur N2 rend en plus sa série horaire de
puissance (`phi_hc_w`) — ajout pur. La page Calcul 3D reste en place (Lot 27) ; ses N2 sont marqués « lancé depuis
Calcul 3D » (pas d'état « à jour »).

## Lot 24 — Page « Calculs » : historique, détail, comparaison   ·   statut : livré
**Objectif :** pour chaque calcul, voir l'ensemble des entrées utilisées et l'ensemble des résultats.
**Tâches :**
- [x] API : liste filtrable (niveau, variante) avec état « à jour / périmé » et mode, détail avec entrées figées (`inputs`)
  et séries horaires (lues seulement à l'ouverture d'un calcul),
  `GET /api/calculs/<a>/comparer/<b>/` (entrées qui diffèrent, écart des indicateurs)
- [x] Onglet « Calculs » de la fiche : liste ; détail (entrées par famille avec provenance, météo, indicateurs,
  mois, bilan par poste, courbes horaires) ; comparaison de deux calculs
**Tests à passer avant de clore le lot :**
- [x] Détail invisible pour qui ne voit pas le bâtiment ; comparaison de deux calculs du même bâtiment seulement
- [x] Navigateur réel, clair et sombre : liste, détail N1 et N2, comparaison
**Vérifié le :** 2026-11-15 — 457 tests OK (5 nouveaux, `api/tests/test_calculs_page.py`). Navigateur réel (banc),
clair et sombre, 34 contrôles : liste de 5 calculs avec les périmés signalés ; détail N2 (résultats, entrées par famille
avec provenance, géométrie, taux de vitrage de la fiche et modélisé, parois et couches, limites) ; courbes journalières
de température opérative et de puissance avec info-bulle au survol (« 23 juin · moyenne 21,6 °C · min 19,3 · max 24,2 ») ;
détail N1 (paramètres du modèle) ; comparaison de deux N1 nommant « U des murs » et l'écart des résultats ; aucune erreur
console. Courbes : une série par graphique, couleur d'accent validée en clair et en sombre (`validate_palette.js`), tableau
mensuel en complément.

## Lot 25 — Page « Simulation » : préparer, juger la fiabilité, lancer   ·   statut : livré
**Objectif :** avant de lancer, savoir ce qui sera calculé, avec quelles entrées et quelle fiabilité.
**Tâches :**
- [x] `GET /api/batiments/<id>/simulation/?niveau=&scenario=` : entrées par famille (valeur, provenance,
  confiance), les entrées les plus influentes et incertaines, fourchette attendue, durée estimée, blocages
- [x] Onglet « Simulation » : choix du niveau, de la variante et de la météo, tableau des entrées, fiabilité,
  liens « corriger », bouton lancer ; la carte « Résultat » y renvoie
**Tests à passer avant de clore le lot :**
- [x] Les entrées listées sont exactement celles que le calcul enregistre ensuite
- [x] Navigateur réel, clair et sombre
**Vérifié le :** 2026-11-15 — 462 tests OK (5 nouveaux, `api/tests/test_simulation.py` : entrées annoncées = entrées
enregistrées pour N1 et N2, « déjà calculé », fourchette, verdict, mesure d'une entrée → dispersion en baisse,
blocages, droits, préparer ne lance rien). Navigateur réel (banc), clair et sombre, 23 contrôles : la carte Résultat
mène à la Simulation ; verdict « à confirmer » (dispersion ± 21 %) ; entrées décisives (surface, hauteur, U des murs,
renouvellement d'air) avec lien « Corriger » ; N2 annoncé (géométrie, parois, ≈ 9 s), lancé, suivi, puis onglet Calculs
ouvert sur ce N2 ; entrées inchangées → « Voir le calcul déjà fait » ; occupant : N1 lançable, N2 bloqué et expliqué.
Dispersion : effets des entrées incertaines (± 1 écart type de leur méthode, `uncertainty.REL_SPREAD`) supposés
indépendants, sommés en quadrature ; verdict < 10 % « fiable », < 25 % « à confirmer », sinon « indicatif ».

## Lot 26 — Gestes experts → faits   ·   statut : livré
**Objectif :** ce qui se fait sur les pages expertes améliore la fiche, donc tous les niveaux.
**Tâches :**
- [x] Parois assignées à la main → U des murs, toiture, plancher et vitrages, g, taux de vitrage par orientation,
  part mitoyenne et emprise **déduits** (fait `declare`, source « enveloppe experte », au nom de qui enregistre),
  remplacés si l'assignation change, retirés si l'enveloppe n'est plus entièrement assignée (D35, `api/expert_facts.py`)
- [x] Environnement relevé (LiDAR × BD TOPO) → horizon par façade pour N0/N1, à la place de l'horizon simplifié
- [x] Façades analysées (Panoramax) → document local « Analyse des façades » et propositions de taux de vitrage par
  orientation, validées dans l'onglet Documents comme celles d'un DPE (D39)
**Tests à passer avant de clore le lot :**
- [x] Chaque geste crée le fait attendu et le retire quand il est défait ; N0/N1 deviennent périmés
**Vérifié le :** 2026-11-15 — 469 tests OK (7 nouveaux, `api/tests/test_gestes_experts.py` : valeurs déduites = U des
couches, ré-enregistrer ne duplique rien, désassigner retire, assignation changée → valeurs mises à jour, part mitoyenne,
géométrie corrigée qui régénère l'enveloppe → retrait ; tour de 24 m au sud → horizon S > 40°, N1 périmé puis besoin en
hausse ; façades → proposition « ok », acceptée = fait « document », nouvelle analyse remplace les propositions en attente).
Navigateur réel (banc), clair et sombre : U des murs « déclaré · enveloppe experte » au-dessus de l'automatique ; document
« façades analysées » sans bouton IA ; proposition acceptée → provenance « document ». Deux défauts trouvés en écrivant
les tests et corrigés avant livraison : une toiture plate (inclinaison 0) comptée comme un mur (`or 90`), et
l'enveloppe écrite par `vertices`/`triangles` (pas `envelope`, en lecture seule) non détectée.

## Lot 27 — Fiche unique et anciennes pages   ·   statut : livré
**Objectif :** une seule entrée par bâtiment ; les pages d'avant deviennent des onglets ou des outils.
**Tâches :**
- [x] Onglets « Maillage 3D » (vue et assignation de l'ancienne page Bâtiment) et « Environnement » dans la fiche,
  chargés à la demande (`@defer`) ; les deux pages gardent leur mode autonome
- [x] `/batiment?id=…` → fiche, onglet Maillage 3D ; `/mode-simplifie` → Parcs (page supprimée : l'adresse → fiche
  automatique → calcul la couvre) ; `/batiment` (sans bâtiment) reste l'outil « Importer un maillage » et
  `/environnement` l'outil « Environnement d'un quartier » ; **Calcul 3D reste un outil expert** (« Calcul 3D avancé ») :
  il garde des réglages absents de la Simulation (planning horaire, régime imposé ou libre, ombrage temps réel, vent) et
  l'annonce en tête de page ; Calcul 1D, Parois et Calcul 3D liés depuis l'onglet Calculs ; navigation réordonnée
- [x] Commande `migrer_fiches` : tout bâtiment reçoit une fiche (idempotente, `--check`)
**Tests à passer avant de clore le lot :**
- [x] Navigateur réel, clair et sombre : chaque ancienne adresse arrive au bon onglet
- [x] `migrer_fiches --check` rend 0 sur la base de service après passage
**Vérifié le :** 2026-11-15 — 470 tests OK (1 nouveau, `api/tests/test_fiche_unique.py`). Navigateur réel (banc),
clair et sombre, 24 contrôles : `/batiment?id=1` arrive sur l'onglet Maillage 3D (vue 3D, sans liste, renvoi vers la
Simulation) ; onglet Environnement sans liste, génération autour du bâtiment ; `/mode-simplifie` → `/parc` ; Calcul 3D
annonce la fiche ; navigation sans « Mode simplifié » ; outils liés depuis Calculs ; aucune erreur console. Recette
complète des Lots 23 à 27 rejouée sur banc neuf : 105 contrôles, 0 échec. `migrer_fiches --check` sur la base de service :
à jouer après déploiement (le seul bâtiment en service a déjà une fiche).

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 244 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement `lab-runner` 3/3 avant et après le durcissement du client, puis de nouveau 3/3 sur Keycloak 26.8.1.
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).
