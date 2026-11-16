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

## Lot 28 — Navigation en six onglets   ·   statut : livré
**Objectif :** une application compréhensible à l'échelle du parc et naturelle pour l'approfondissement
(demande du propriétaire du 2026-11-16 : « parfaitement inexploitable »). Le menu de 9 entrées et la fiche
à 13 onglets laissent place à six onglets — **Parc, Bâtiment, Calculs, Paramétrage, IA et imports, Rendu** —
avec un parc et un bâtiment courants partagés par tous les onglets (`ContexteService`, `?p=&b=`).
Contrat du chantier : `chantiers/bt-navigation/CONTRAT.md` (lab, non versionné).
**Tâches :**
- [x] Contexte global parc/bâtiment, bandeau de contexte, menu à six entrées, anciennes adresses redirigées
  (`/fiche?onglet=` → onglet correspondant, éditeur de maillage sous `/maillage`)
- [x] Parc : parcs, bâtiments avec confiance, approfondissement et modèles de calcul utilisés ; ajout de
  bâtiments dans le parc ; import et partage ; orchestrateur des sources par simulation (réglages de parc)
- [x] Bâtiment : synthèse, données et provenance, géométrie, physique, météo, environnement, mesures
- [x] Calculs : catalogue documenté des simulations, simulation par simulation, améliorations et comparaison, historique
- [x] Paramétrage : valeurs par modèle de calcul (isolants, améliorations, menuiseries, archétypes,
  ventilation, rendements), surchargées par parc et réellement prises en compte
- [x] IA et imports : clés, import de fichiers pour le parc, import pour un bâtiment
- [x] Rendu : rendu de parc paramétrable (carte, chiffres, classement), fiche d'identité imprimable
- [x] API : `parcs/<id>/reglages/<section>/`, `calculs/moteurs/`, `parcs/<id>/documents/`,
  `batiments/<id>/identite/`, tableau enrichi, `portfolio` à l'initialisation et dans `BuildingSerializer`
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_navigation.py` : formes, droits, validation, effet de chaque réglage pris en compte
- [x] Suite backend complète verte ; `ng build` sans erreur
- [x] Navigateur réel (banc), clair et sombre : les six onglets, contexte conservé d'un onglet à l'autre,
  anciennes adresses redirigées, aucune erreur console
**Vérifié le :** 2026-11-16 — backend : **498 tests OK** (28 nouveaux, `api/tests/test_navigation.py` ; base
jetable, image de l'app). `ng build` (production) OK. Navigateur réel (banc jetable : API réelle, Keycloak simulé,
réseau interne), clair et sombre : 46 contrôles de navigation — menu à six entrées, parc puis bâtiment conservés
d'un onglet à l'autre et dans l'URL, chaque sous-vue des onglets Parc (4), Bâtiment (7) et Calculs (4) rendue,
Paramétrage N0/N1/N2, fiche d'identité avec la mention « ne remplace pas un DPE », `/fiche?onglet=scenarios` →
Calculs › Améliorations, bâtiments hors parc listés — et 6 contrôles d'écriture (λ des murs enregistré pour le
parc, BDNB désactivée dans l'orchestrateur et conservée, création d'un parc), aucune erreur console.
Réglages pris en compte (chacun prouvé par un test) : toute l'orchestration sauf l'environnement « avant N2 »
(affiché « pas encore pris en compte ») ; tout le paramétrage. Un réglage modifié vaut pour les analyses suivantes
(chaîne relancée, réponse au questionnaire, nouvelle amélioration), sans rien réécrire de ce qui est déjà posé.
Corrigés au passage : SystemesComponent (NG0100 sans zone), visionneuse 3D d'un bâtiment sans maillage.

## Lot 29 — Environnement observé dans l'orchestrateur   ·   statut : livré
**Objectif :** que l'orchestrateur du parc récupère aussi, automatiquement, l'environnement observé (LiDAR HD ×
BD TOPO, albédo des orthophotos, essences CoSIA) — opération lourde, mais objectif du propriétaire (2026-11-16) —
au lieu de le laisser à la seule génération à la main.
**Tâches :**
- [x] Réglage `environnement.mode` branché : « à la demande », « avant chaque calcul détaillé, s'il manque »,
  « pendant la récupération automatique » (étape **A6** de la chaîne, avant N0/N1 qui en tirent l'horizon par
  façade ; couvre aussi le N2) ; réglages `environnement.rayon_m` (150 m), `.vegetation`, `.terrain`
- [x] `environment_service.ensure_for_building` : génère, relie, périme l'ombrage ; ne lève jamais (échec ⇒ voisins
  BD TOPO, cause fixe au compte rendu) ; un environnement déjà relié (éventuellement retouché) n'est jamais remplacé ;
  empreinte du bâtiment étudié prise sur la géométrie corrigée ou l'emprise de la chaîne
- [x] N2 : environnement généré au début du calcul (même tâche, donc sous le verrou des calculs lourds), empreinte
  des entrées refaite ensuite (le calcul reste « à jour ») ; un N2 terminé sans environnement n'est pas repris ;
  durée estimée et annonce sur la page Simulation
- [x] N2 depuis la fiche : réflexion par le sol (albédo relevé) passée au solveur, comme dans l'outil Calcul 3D —
  moteur `n2-2` (les N2 `n2-1` restent reconnus comme lancés depuis la fiche, et deviennent périmés)
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_environnement_orchestre.py` : modes, chaîne (file du parc et initialisation seule), ordre A6 →
  N0/N1, réglages transmis, échec sans arrêt et message fixe, N2 généré et à jour, ancien N2 non repris, albédo
  transmis, hors parc inchangé
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — backend : **510 tests OK** (12 nouveaux, `api/tests/test_environnement_orchestre.py` ; base
jetable, image de l'app). `ng build` (production) OK. Essai réel (base jetable, réseau ouvert, hôtel de ville de
Tours, parc en mode « pendant la récupération automatique », rayon 80 m) : chaîne complète en 24 s, dont **A6 en
18 s** (LiDAR HD : 20 bâtiments, 7 arbres, terrain, albédo du sol), N0 et N1 calculés ensuite avec l'horizon
« environnement relevé » ; N2 en 11 s sur l'environnement relié, « à jour », relance sans nouveau calcul.
Limite relevée, antérieure au lot : l'horizon par façade (chaîne comme environnement) prend l'obstacle le plus haut
du cône ±45° et l'applique à toute la façade — 71° au sud (BD TOPO) et 79° (environnement) sur ce cas, à revoir.

## Lot 30 — Géométrie croisée BD TOPO × LiDAR HD, horizon par profil   ·   statut : livré
**Objectif :** la BD TOPO ne peut pas servir seule de géométrie au bâtiment (propriétaire, 2026-11-16) : hauteur
fausse de plusieurs mètres (10 m pour 14,8 m mesurés à l'égout sur l'hôtel de ville de Tours, 1,4 m pour 6 m à
Langeais), emprises parfois décalées, bâtiments absents. La croiser avec le LiDAR, et refaire l'horizon des façades.
**Tâches :**
- [x] `api/croisement.py` : avec un environnement relevé relié (même repère), emprise recalée, hauteur à l'égout
  mesurée (médiane pondérée du haut des murs), faîtage, toiture (terrasse / deux / quatre pans, pente), voisins
  recalés à leur hauteur mesurée et arbres remplacent la BD TOPO ; faits `infere` « LiDAR HD × BD TOPO (IGN) »
  (une correction de l'utilisateur prime) ; niveaux et surface déduits par la chaîne recalculés ; niveaux d'une
  source automatique incohérents avec la hauteur (étage hors 2,3–5 m) recalculés ; géométrie BD TOPO gardée et
  rétablie si l'environnement est détaché ; enveloppe 3D remplacée seulement si c'est encore celle de la chaîne
- [x] Branché partout : chaîne A3 (environnement déjà relié), A6 (juste après la génération, avant N0/N1), N2
  (génération au début du calcul), rattachement / détachement depuis la page du bâtiment
- [x] Réglage `environnement.mode` : « pendant la récupération automatique » **par défaut**, hors parc aussi
- [x] `api/horizon.py` : profil d'horizon par tranche de 10°, mesuré depuis des points de chaque façade exposée à
  trois hauteurs ; le direct est réduit à la part de la façade qui voit le soleil (`engines.solar`) ; un mitoyen
  n'est plus un masque, le bâtiment se masque lui-même (ailes) ; `horizon_deg` = angle face à la façade (±15°).
  Moteurs N0 `n0-3`, N1 `n1-3` (calculs précédents périmés)
- [x] Défaut corrigé au passage : le sommet d'un arbre de l'environnement était compté depuis l'altitude absolue
  du terrain — aucun arbre ne masquait jamais rien
- [x] Carte Géométrie : origine de la géométrie (BD TOPO seule, ou croisée : recalage, égout, faîtage, toiture)
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_geometrie_lidar.py` : profil (site dégagé, mur en face, mitoyen, immeuble de côté, aile,
  moteur solaire), mesure des toitures, croisement (remplacement, voisins et arbres, priorité de l'utilisateur,
  détachement, chaîne rejouée, enveloppe automatique seule, autre repère, objet retrouvé par l'emprise), niveaux
  incohérents, rattachement par l'API, défaut du réglage
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — backend : **532 tests OK** (22 nouveaux). `ng build` OK. Essai réel (base jetable,
réseau ouvert, bâtiments hors parc, réglages par défaut, rayon 150 m) : chaîne complète de 34 s (Chambray-lès-Tours)
à 134 s (rue Colbert, Tours) ; égout mesuré / BD TOPO : 14,8 / 10 m (hôtel de ville de Tours), 12,7 / 9,3 m,
6,3 / 5,1 m, 6,0 / 1,4 m (Langeais) ; toitures reconnues (deux pans à 50°, quatre pans à 36°…).
**Limites :** le diffus du ciel n'est pas masqué ; l'enveloppe 3D d'un bâtiment créé avant ce lot n'est pas
remplacée (elle ne sert qu'à l'affichage et au mode expert) ; hors couverture LiDAR la BD TOPO reste seule (lot 31).

## Lot 31 — Repli MNS hors LiDAR   ·   statut : livré
**Objectif :** la BD TOPO ne sert jamais seule à placer le bâtiment (propriétaire, 2026-11-16) : sans nuage LiDAR
HD, recaler l'emprise et mesurer la hauteur sur un modèle numérique de surface (MNS).
**Tâches :**
- [x] `api/mns_source.py` : MNS LiDAR HD raster (50 cm) et son MNT, sinon MNS Correl (photogrammétrie, couverture
  nationale) et RGE ALTI, en valeurs brutes (WMS-R `image/x-bil;bits=32`) ; nuage synthétique classé **sans la BD
  TOPO** (sursol, NDVI de l'orthophoto infrarouge, à défaut rugosité locale à la médiane) ; même chaîne que le
  LiDAR (`observed_env.build_objects` : recalage, toits, arbres, terrain)
- [x] Génération : nuage LiDAR → MNS LiDAR → MNS Correl → repli historique BD TOPO / OSM (en dernier recours)
- [x] Croisement : sources `mns_lidar` / `mns_correl`, libellés et confiances propres (0,75 / 0,6) ; toit non vu
  ⇒ note « hauteur de la BD TOPO gardée — à vérifier » (carte Géométrie)
- [x] Défauts corrigés au passage : les arbres (`vegetation`) n'étaient jamais des obstacles de l'horizon (le code
  cherchait `tree`, Lot 26 compris) ; sans bâtiment étudié de référence, un obstacle compte pour sa hauteur propre
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_mns.py` : classification (toit, arbre, sol, haie au NDVI), ordre des sources, absence de
  toute source, génération complète sur un MNS synthétique et géométrie croisée recalée qui en sort
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — essai réel (base jetable, nuage LiDAR forcé absent), hôtel de ville de Tours :
égout 13,4 m (MNS LiDAR) et 14,2 m (Correl) pour 14,8 m au nuage ; 84 et 78 arbres pour 83 ; horizon nord / sud
49° / 68,5° et 47,5° / 74,7° pour 47,8° / 68,1°. Langeais : MNS LiDAR, égout 5,4 m pour 6,0 m ; **le MNS Correl
ne voit pas la maison** (sursol médian 1,2 m pour 6 m : donnée photogrammétrique antérieure ou trop lisse) — la
note le dit, la hauteur BD TOPO reste à vérifier.

## Lot 32 — Sol simulé en 1D, couplé au sous-sol et au plancher bas   ·   statut : livré
**Objectif :** couplage sous-sol / sol fondé sur une simulation de la température du sol en profondeur et dans le
temps, d'après la météo (propriétaire, 2026-11-16), au lieu d'un sol à 12 °C constants pour tout plancher bas.
**Tâches :**
- [x] `api/engines/sol.py` : colonne de sol 1D de 15 m (40 mailles), conduction transitoire implicite au pas
  horaire sur la météo du calcul, surface couplée à l'air (h = 15 W/m²·K), fond adiabatique, trois années de mise
  en régime ; sol limoneux humide (λ 1,5 W/m·K, ρc 2,0 MJ/m³·K)
- [x] Couplage selon `plancher.type` : terre-plein (sol à B′/2, borné 0,5–6 m), vide sanitaire ventilé (nœud :
  plancher, sol à 30 cm, air extérieur), sous-sol non chauffé (nœud : plancher, murs enterrés, radier, murs hors
  sol, ventilation ; `sous_sol.profondeur_m`, 2,5 m par défaut), local chauffé dessous (aucun échange)
- [x] `plancher.u` reste le **U équivalent vers l'extérieur** (« U final » du DPE 3CL) : U propre du plancher déduit
  pour un vide sanitaire ou un sous-sol (1/Ue = 1/Uf + A/ΣH, EN ISO 13370) — le modèle redonne Ue en moyenne
  annuelle, sans compter deux fois le local tampon ; U incompatible ⇒ U propre plafonné à 5 W/m²·K, avec une note
- [x] Branché dans N0, N1 (série horaire par jeu de paramètres, fourchette comprise) et N2 (température de sol
  portée par chaque heure de la météo ; couches du plancher au U propre) ; description figée avec chaque calcul
  (`sol`) ; moteurs `n0-4`, `n1-4`, `n2-3` (calculs précédents périmés)
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_sol.py` : onde annuelle analytique (amplitude e^(−z/d), retard z/d, d ≈ 2,74 m), sol profond
  stable, couplages par type, U équivalent retrouvé en moyenne, plafond, N0/N1/N2 branchés, fait de profondeur
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — essai réel (maison de Chambray-lès-Tours, année type PVGIS, emprise 103 m²) : sol à
0,5 m entre 2,4 et 21,9 °C, à 4 m entre 10,1 et 14,0 °C, à 8 m quasi constant (moyenne 12,1 °C = air). Besoin N1
par rapport au sol constant, U équivalent 0,8 : terre-plein +1,2 %, vide sanitaire +19,6 %, sous-sol +11,6 % (le
local tampon suit l'air extérieur en hiver : l'ancien sol à 12 °C était optimiste en saison de chauffe) ; U 2,0 :
incompatible avec un vide ventilé, U propre plafonné (−6 % et −8 %).
**Limites :** l'intérieur des nœuds tampons est pris à la consigne (couplage non itéré) ; ni soleil, ni neige, ni
évaporation en surface du sol ; un sol par défaut (pas de nature de sol par site).

## Lot 33 — Protubérances   ·   statut : livré
**Objectif :** un mur qui dépasse du bâtiment était vu comme une paroi dont la face « intérieure » échange avec le
nœud d'air chauffé (propriétaire, 2026-11-16) — aberrant : ses deux faces sont dehors.
**Tâches :**
- [x] `api/protrusions.py` : rayon lancé de chaque triangle vers l'intérieur sur le maillage du bâtiment — hors
  volume s'il ressort sans rien rencontrer, s'il rencontre à moins de 80 cm une face opposée (deux faces d'une même
  plaque ; un coin en biseau pan de toit / mur n'en est pas un), ou si la première face est abordée par l'extérieur
  (bout d'un mur qui bute sur la façade) ; garde-fou : au-delà de 50 % de l'enveloppe, maillage hors convention
  (murs épais en deux faces, maillage ouvert), rien n'est reclassé et l'avertissement le dit
- [x] `building_solver` : un triangle « hors volume » reste une ailette (conduction, inertie, soleil, ombre portée)
  dont les deux faces échangent avec l'air extérieur ; ni couplage au nœud d'air, ni flux d'enveloppe, ni
  température radiante, ni cadre ; indices inchangés (ombrage précalculé valable) ; appliqué à tout calcul N2 et
  à l'outil Calcul 3D (`detect_protrusions`, actif par défaut)
- [x] Compte rendu (`protrusions`) dans le résultat ; ligne dans les limites du N2 quand des triangles sont reclassés
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_protuberances.py` : bâtiment fermé et volumes accolés (rien), biseaux d'égout (rien), mur de
  20 cm dépassant de 3 m (ses 8 triangles), murs épais en deux faces (rien, avertissement), solveur (besoin égal à
  celui du bâtiment seul ; +11,5 % sans la détection)
- [x] Suite backend complète verte
**Vérifié le :** 2026-11-16 — enveloppes LiDAR réelles (hôtel de ville de Tours 342 triangles, rue Colbert 66,
Langeais 64) : aucun triangle reclassé, détection en 0,01 s ; une première version comptait 1 à 4 triangles d'égout
comme minces (biseau pan de toit / mur), d'où la condition de faces opposées.
**Limites :** une annexe non chauffée épaisse (garage accolé) n'est pas un élément mince : elle reste dans le volume
tant qu'elle n'est pas retirée de l'enveloppe ; la face arrière d'une ailette n'est pas ensoleillée.

## Lot 34 — Lisibilité du parcours et des bilans   ·   statut : livré
**Objectif :** relire le parcours et les bilans produits après les lots 30 à 33 (propriétaire, 2026-11-16), sur un
parcours réel : parc créé, bâtiment ajouté par son adresse, chaîne complète, six onglets, fiche d'identité imprimée.
**Constats (navigateur réel, compte de test, 2026-11-16) :** aucune erreur ; la carte Géométrie et les entrées de
Calculs disent la géométrie croisée ; mais la **fiche d'identité imprimable** affichait les choix sous leur code
(`sous_sol`, `2pans`, `iti`, `vmc_sf_auto`, `joule`…), l'année « 1 990 », mitoyenneté et vitrages en fraction, ne
disait ni l'origine de la géométrie, ni les masques, ni la source de l'environnement, ni le sol vu par le plancher,
et ses sources parlaient en codes d'étape (A1, A3…) avec une source A6 figée ; le texte de l'outil Environnement
annonçait un repli BD TOPO direct hors LiDAR.
**Tâches :**
- [x] `facts.display_value` : valeur d'un fait en mots (libellé du choix, %, année, unité), rendue par le serveur
- [x] Fiche d'identité : origine de la géométrie (croisée : égout, faîtage, écart à la BD TOPO, toiture, recalage ;
  ou BD TOPO seule), masques face aux façades, environnement (source, voisins, arbres, rayon), plancher bas (modèle,
  température vue, U propre / équivalent, note), limites des calculs, sources nommées dans l'ordre de la chaîne
  (source réelle de A3 et A6, étape désactivée distinguée d'un échec)
- [x] Calculs : sol vu par le plancher et origine de la géométrie dans les entrées figées (N0, N1, N2)
- [x] Carte Géométrie (ponctuation) ; texte de l'outil Environnement (MNS, génération automatique)
**Tests à passer avant de clore le lot :**
- [x] `IdentiteTest` (`test_navigation.py`) : texte, plancher, géométrie, limites ; `DisplayValueTest` (`test_sol.py`)
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — backend : **553 tests OK**. `ng build` OK. Parcours réel rejoué après déploiement.

## Lot 35 — Fondation des potentiels et du rendu   ·   statut : livré
**Objectif :** poser ce dont les quatre secteurs du chantier « bt-potentiels » (propriétaire, 2026-11-16 : protections
paramétrables, améliorations et bouquets, potentiels, rendu Plotly et exports) ont besoin avant de travailler en
parallèle, sur un contrat écrit (`chantiers/bt-potentiels/CONTRAT.md`, lab, non versionné ; suivi :
`to_do_potentiels.md`).
**Tâches :**
- [x] Modèles `Bouquet`, `Potentiel`, `RapportModele`, `ExportRendu` ; champs `Scenario.bouquet`, `.surcharges`,
  `.genere` ; migration `0028_potentiels_fondation`
- [x] Anciens bouquets (`scenarios.BUNDLES`) repris comme actions du catalogue à l'identique : `weather['bundles']`
  reste lisible, aucune migration de données
- [x] Clés de faits `toiture.absorptance`, `abords.albedo`, `ventilation.nocturne_vol_h`, `ventilation.nocturne_seuil_c`,
  `toiture.vegetalisee`, `murs.vegetalises` (poids 0 : la complétude ne change pas)
- [x] Routes I1 à I7 déclarées (`urls.py`), rendant la forme du contrat le temps des secteurs
- [x] Dépendances : `python-docx==1.1.2` (serveur), `plotly.js-dist-min` 2.35.2 figé (frontend, chargé à la demande)
- [x] Service `rendu`, réseau interne, clé `RENDU_SERVICE_KEY` et volume `rendu-exports` dans `docker-compose.yml`
  (posés au lot 40, d'après le bloc rapporté par le secteur R)
**Tests à passer avant de clore le lot :**
- [x] `api/tests/test_fondation_potentiels.py` : migrations, modèles, routes présentes avec leurs droits
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — intégré, 663 tests backend OK ; vérification en navigateur au lot 40.

## Lot 36 — Protections paramétrables et physique des améliorations   ·   statut : livré
**Objectif :** que chaque protection solaire se règle (épaisseur, débord, inclinaison des lames…) et produise son
effet dans chaque niveau où elle s'applique, et que toute amélioration (isolation, ventilation, végétalisation,
albédos) soit prise en compte par N0, N1 et N2 ; DH à la RE2020 ; résultats N2 par triangle.
**Tâches :**
- [x] Catalogue des protections v2 (`engines/shading.py`, `GET /api/protections/catalogue/`) : 16 modèles en quatre
  familles (fixe, mobile, vitrage, végétal) ; paramètres typés et bornés (débord : profondeur, hauteur de la sous-face,
  inclinaison, débords latéraux gauche et droite ; joues : profondeur, hauteur, côté ; lames : inclinaison, largeur,
  entraxe, épaisseur, transmission ; fermé : transmissions et ΔR) ; pilotage fixe / planning / automatique, fermeture
  nocturne, lames suivant le soleil (BSO) ; texte d'application par niveau ; protections d'avant le lot gardent leur
  comportement
- [x] Débords et joues par l'angle de profil (géométrie de l'EN ISO 52016-1 annexe F), bande de mur ombrée ; lames
  selon l'ISO 15099 § 7 (direct selon l'angle de profil, diffus intégré) ; ΔR des fermetures (EN ISO 10077-1 annexe G)
- [x] N2 : débords et joues construits en obstacles géométriques quel que soit le mode ; mobiles, lames, vitrages et
  végétal appliqués par triangle de baie, position partielle comprise, depuis la fiche
- [x] Interface `components/protections` : choisir un modèle, régler tous ses paramètres (groupes, aide, bornes du
  catalogue), modifier une protection, l'attacher à l'état actuel ou à une amélioration
- [x] Surcharges d'amélioration (`engines/surcharges.py`, `engines/vegetation.py`) : albédo des abords, absorptance de
  toiture distincte de celle des murs, arbres plantés (houppiers translucides saisonniers : horizon N0/N1, obstacles
  N2), toiture et murs végétalisés (U, absorptance du feuillage, évapotranspiration de Priestley-Taylor),
  rafraîchissement de l'air (hypothèse affichée), surventilation nocturne (N1, N2) ; surcharge mal formée non appliquée
  et dite ; `inputs['ameliorations']` décrit ce que chaque niveau a appliqué
- [x] DH RE2020 (`engines/comfort.py` : `dh_re2020`, `heures_inconfort`, `t_conf_methode`, `dh_26`), séries
  mensuelles `results['mensuel']`, liste canonique `GET /api/indicateurs/`
- [x] N2 : cumuls par triangle (pertes d'hiver, gains d'été, solaire absorbé, températures de surface),
  `GET /api/calculs/<id>/triangles/` ; moteurs `n0-5`, `n1-5`, `n2-4`
**Tests à passer avant de clore le lot :**
- [x] `test_physique_confort.py` : DH RE2020 sur des séries construites à la main (seuil, moyenne glissante, heures
  tertiaires), N0 sans indicateur de confort
- [x] `test_physique_protections.py`, `test_protections_catalogue.py`, `test_protections.py` : chaque paramètre change
  le résultat là où le catalogue le dit, ΔR, protections anciennes inchangées, géométrie N2, droits
- [x] `test_physique_ameliorations.py` : chaque surcharge (signe et ordre de grandeur en N0, N1, N2), surcharges
  mal formées refusées et dites
- [x] `test_physique_triangles.py` : somme des cumuls par triangle = flux d'enveloppe du bilan ; forme I4, droits
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — intégré, 663 tests backend OK ; vérification en navigateur au lot 40.

## Lot 37 — Actions, bouquets et potentiels   ·   statut : livré
**Objectif :** un catalogue d'améliorations possibles, chacune avec les résultats où elle est pertinente (énergie ou
DH), ses conditions et sa façon d'être appliquée par N0, N1 et N2 ; des bouquets définis par l'utilisateur, dont deux
du lab ; pour chaque bâtiment, chaque bouquet appliqué en entier est un **potentiel atteignable**.
**Tâches :**
- [x] Catalogue `ameliorations/catalogue.py` (`GET /api/ameliorations/actions/`) : 24 actions en six familles
  (isolation, ventilation, protections solaires, végétalisation de la parcelle, toiture et albédos, chauffage et
  systèmes) ; paramètres, conditions bloquantes ou non (« à vérifier »), indicateurs pertinents, cibles, application
  par niveau, points de vigilance ; défauts et désactivation par les réglages du parc
- [x] Les dix anciennes clés de bouquet sont des actions à l'identique ; améliorations existantes intactes
- [x] Application d'un bouquet : toutes les actions applicables, « une parmi » (la première applicable), deux éléments
  jamais sur la même cible, raison de chaque action écartée ; amélioration générée unique par bâtiment et bouquet
- [x] Bouquets du lab « Économiser l'énergie » et « Minimiser les DH » (`ameliorations/bouquets.py`) ; bouquets de parc
  (création, copie, modification, suppression par un gestionnaire)
- [x] Potentiels (`ameliorations/potentiels.py`) : N1 d'un bâtiment (synchrone, 4 bouquets au plus par appel), d'un
  parc (file `parc`, un à la fois) ; N2 d'un bâtiment à la demande, gestionnaire, jamais en lot ; « à jour » par
  empreinte ; écarts au réel ; agrégats de parc (somme, moyenne, médiane, effectif)
- [x] Interface : Paramétrage › bouquets du parc ; Calculs › Améliorations : potentiels du bâtiment, actions
  appliquées et écartées
**Tests à passer avant de clore le lot :**
- [x] `test_actions_catalogue.py` : familles et actions complètes, condition bloquante écartée avec sa raison, « une
  parmi », avertissement non bloquant, anciens bouquets identiques
- [x] `test_potentiels_calcul.py` : bouquet énergie idempotent et efficace, bouquet modifié ⇒ périmé, limites et N2,
  droits, parc de trois bâtiments et deux bouquets (agrégats exacts), erreur qui n'arrête pas la file
- [x] `test_bouquets_crud.py` : bouquets du lab idempotents, lisibles et copiables, droits, validation
- [x] Suite backend complète verte ; `ng build` sans erreur
**Vérifié le :** 2026-11-16 — intégré, 663 tests backend OK ; vérification en navigateur au lot 40.

## Lot 38 — Rendu : document, figures et exports   ·   statut : livré
**Objectif :** un document de rendu construit par le serveur — seul producteur des figures et des documents — et
exportable en HTML, Word et PDF en reprenant au mieux le HTML.
**Tâches :**
- [x] Spécification (`rendu/spec.py`, `GET /api/rendu/schema/`) ; dimensions et mesures du constructeur
  (`/api/rendu/dimensions/`, `/mesures/`, par grain de données)
- [x] Figures Plotly construites par le serveur (`rendu/figures.py`) pour les 15 formes, séries réel / bouquets,
  facettes, filtres, agrégations ; aucune couleur (palette posée au rendu) ; textes venus des données échappés
- [x] Document résolu (`rendu/document.py`) : blocs texte (markdown restreint assaini), indicateurs, tableau, graphe,
  carte sans fond en ligne, vue 3D, fiche (avec les potentiels par bouquet), saut de page ; bloc non résolu ⇒
  « indisponible », jamais une erreur du document
- [x] Gabarits du lab « Fiche d'identité » et « Synthèse de parc », 18 questions du constructeur guidé
  (`rendu/gabarits.py`) ; modèles enregistrés par parc
- [x] Export HTML autonome (plotly.js et three.js embarqués, interactif hors ligne, sommaire, CSS d'impression,
  `Content-Security-Policy: default-src 'none'`)
- [x] Service de rendu `rendu-service/` (Node + Playwright/Chromium) : PDF (signets, en-têtes et numéros de page),
  images des figures et des vues 3D ; toute requête autre que `data:`/`about:` abandonnée ; clé en en-tête
- [x] Export PDF (HTML imprimé par le service) et Word (python-docx : styles de titre, sommaire actualisable, vrais
  tableaux, figures et vues 3D en images, légendes numérotées, liens internes)
- [x] Exports transitoires 24 h, réservés à leur auteur, purgés (volume `rendu-exports`)
**Tests à passer avant de clore le lot :**
- [x] `test_rendu_figures.py` : toutes les formes sur un parc et un bâtiment, séries, cascade, facettes, refus fixes,
  noms hostiles échappés, carte sans fond
- [x] `test_rendu_document.py` : fiche d'identité avec potentiels, vue 3D avec et sans N2, synthèse de parc, texte
  assaini, routes consommées par leur classe de vue
- [x] `test_rendu_exports.py` : HTML autonome, Word, PDF par le service, service injoignable (message fixe), sans clé,
  export d'autrui refusé, expiration et purge ; `rendu-service` : tests unitaires et d'intégration
- [x] Suite backend complète verte
**Vérifié le :** 2026-11-16 — intégré, 663 tests backend OK ; vérification en navigateur au lot 40.

## Lot 39 — Rendu : interface   ·   statut : livré
**Objectif :** une page de paramétrage du rendu et une page de rendu ; les potentiels par bouquet dans la fiche
d'identité et dans le parc ; les résultats N2 par triangle en 3D dans la fiche du bâtiment.
**Tâches :**
- [x] Onglet Rendu en deux pages : **Paramétrage du rendu** (sections et blocs, constructeur de graphes guidé : une
  question → un graphe proposé, puis réglages ; gabarits ; enregistrement par un gestionnaire) et **Rendu** (aperçu du
  document serveur, exports HTML / Word / PDF), en portée parc ou bâtiment
- [x] Composant `components/graphe` : Plotly chargé à la demande, palette tirée des jetons `--serie-*`, `--seq-*`,
  `--div-*` (clair et sombre), titre et résumé textuel
- [x] Potentiels par bouquet dans la fiche d'identité (écarts, actions appliquées et écartées) ; onglet Parc ›
  « Potentiels par bouquet » (réel et bouquets comparés)
- [x] Onglet Bâtiment › « Résultats en 3D » (`components/vue3d-resultats`) : pertes d'hiver, gains d'été, solaire
  absorbé, températures de surface ; échelle et légende ; triangle touché ⇒ ses valeurs ; sans N2 : message et lien
  pour le lancer (gestionnaire) ; même vue dans le bloc `vue3d` du rendu
- [x] Calcul 3D : bascule intérieure / extérieure réellement recolorée (`mesh-viewer.repaint`)
**Tests à passer avant de clore le lot :**
- [x] `ng build` sans erreur, budgets tenus (Plotly hors du bundle initial)
- [ ] Navigateur réel (compte de test), clair et sombre : un graphe de chaque famille, enregistrement, exports
  ouverts ; vue 3D sur un N2 réel et cas sans N2 ; aucune erreur console — **au lot 40**
**Vérifié le :** 2026-11-16 — intégré, 663 tests backend OK ; vérification en navigateur au lot 40.

## Lot 40 — Intégration et documentation   ·   statut : livré
**Objectif :** intégrer les quatre secteurs, mettre en service, rejouer le parcours réel de bout en bout et actualiser
toute la documentation interne de l'app.
**Tâches :**
- [x] Rapports des secteurs vérifiés contre le contrat (v4), fichiers partagés appliqués en un seul edit (service de
  rendu, clé et volume dans `docker-compose.yml`, `.env.example`)
- [x] By-pass d'été de la double flux (N1, N2 : `n1-6`, `n2-5`) ; ouverture des fenêtres en été réglable par parc,
  désactivée par défaut (`confort.ouverture_*`, contrat I9) — `test_physique_bypass.py`, `test_physique_ouverture.py`
- [x] Documentation : `README.md`, `frontend/README.md`, `refonte.md` (§ 16, D40 à D48), cette feuille de route,
  `to_do_potentiels.md`, fiches des moteurs (`backend/api/moteurs.py`), page Théorie
- [x] Déploiement séquencé (image Django, image du service de rendu, frontend), `app-test.sh`, cloisonnement 3/3
- [x] Parcours réel : parc d'essai → bouquets → potentiels → fiche d'identité → comparaison de parc → rendu
  personnalisé → exports HTML, Word, PDF ouverts → vue 3D ; parc d'essai supprimé
- [x] `CLAUDE.md` du lab (service de rendu, pièges) et mémoire
**Tests à passer avant de clore le lot :**
- [x] Suite backend complète verte sur le code intégré
- [x] `ng build` sans erreur ; navigateur réel, clair et sombre, aucune erreur console
- [x] `validate-app.sh bilan-thermique --quality-only` accepte la composition (service de rendu compris)
**Vérifié le :** 2026-11-16 — suite backend **669 tests OK** (banc d'essai et `app-test.sh` sur l'image de service) ;
`ng build` production OK (bundle initial 377 ko, Plotly à part) ; `validate-app.sh bilan-thermique` OK (service de rendu
sur réseau interne) ; mise en service séquencée (backend, worker, frontend, `bilan-thermique-rendu` sain, aucune
sortie réseau constatée depuis le conteneur de rendu) ; cloisonnement `lab-runner` **3/3**. **Parcours réel** en
navigateur (compte de test, clair et sombre, **0 erreur console**) : parc d'essai, bâtiment par son adresse (chaîne 48 s),
potentiels N1 des deux bouquets du lab, potentiels du parc, comparaison de parc (chiffres clés, tableau, graphe, carte),
fiche d'identité avec potentiels par bouquet, exports **HTML** (4,6 Mo, Plotly embarqué, 6 figures, sommaire), **PDF**
(11 pages, signets, sommaire), **Word** (titres, 12 tableaux, 6 images) téléchargés et relus, calcul N2 réel puis **vue 3D**
(64 triangles, pertes d'hiver, triangle cliquable avec ses valeurs) ; parc d'essai supprimé. Défauts trouvés par ce
parcours et corrigés : export resté « en file d'attente » (date non sérialisable), bouton des potentiels grisé sans
explication, unités et sol absents de la fiche, clés techniques dans la liste du parc, bouquet « Économiser l'énergie »
à 14 908 °C·h (récupération de la double flux toute l'année → by-pass d'été : 7 794 °C·h, le reste tenant à l'absence
d'ouverture des fenêtres, réglage désactivé par défaut — décision du propriétaire).

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 244 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement `lab-runner` 3/3 avant et après le durcissement du client, puis de nouveau 3/3 sur Keycloak 26.8.1.
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).
