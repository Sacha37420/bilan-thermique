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

## Lot 2 — Garde-fous de calcul   ·   statut : à faire
**Objectif :** un calcul ne peut pas saturer le worker partagé (2 vCPU), et deux calculs ne partent
pas en même temps.
**Constat :**
- [C, d'après le journal] Point L5 : `MAX_REALTIME_RAYCAST_OPS` ne compte pas la passe végétation.
- [C] Pas de verrou de calcul : `exists()` puis création (`views.py:395,436,455`).
**Tâches :**
- [ ] Compter la passe végétation dans le coût estimé
- [ ] Verrou atomique (contrainte unique partielle, ou `pg_advisory_xact_lock`)
**Tests à passer avant de clore le lot :**
- [ ] Un calcul avec végétation au-delà du seuil est refusé
- [ ] Deux lancements simultanés rendent `[202, 409]`
**Vérifié le :**

## Lot 3 — Historique des calculs et ménage   ·   statut : à faire
**Objectif :** revoir un résultat sans recalculer ; ne plus accumuler d'objets orphelins (points U3, L6, L7).
**Tâches :**
- [ ] Liste des calculs passés par bâtiment, avec un résultat rechargeable (U3)
- [ ] Environnements : purger ou marquer « lié » à chaque génération (L6)
- [ ] Bâtiment incomplet laissé par un mode simplifié abandonné : nettoyage (L7)
- [ ] Dossier `sfm/<id>` du volume `facade-cache` jamais nettoyé (Lot AM)
**Tests à passer avant de clore le lot :**
- [ ] Un résultat se revoit après avoir quitté la page, sans recalcul
- [ ] Régénérer 3 fois l'environnement en laisse 1
**Vérifié le :**

## Lot 4 — Mode d'emploi   ·   statut : à faire
**Objectif :** un nouvel utilisateur sait par où commencer. Il n'existe aucune doc d'usage : la page
Théorie est une documentation physique.
**Tâches :**
- [ ] Page « Démarrer » dans la nav Foyer : parcours 1D → Bâtiment → Calcul 3D, rôle et influence de chaque paramètre (U5), quand passer en temps réel (U6)
- [ ] `README.md` : but, parcours, ressources et limites (2 vCPU, volume `facade-cache`), tests
**Tests à passer avant de clore le lot :**
- [ ] Page vérifiée en navigateur réel, clair et sombre ; lien présent dans la nav
**Vérifié le :**

## Lot 5 — Gabarit front et chemin à portes   ·   statut : en cours
**Objectif :** même comportement que le gabarit corrigé (T3), déploiement par le moteur (T1).
**Tâches :**
- [x] Polices en chemin relatif (`styles.scss`) ; `tokenBases()` (`core/env.ts` : la seule API de l'app ; `core/token-scope.ts`, copie de lab-admin) ; `pkceMethod: 'S256'` (front — `--pkce` sur le client reste à poser par l'architecte après déploiement) ; `redirectUri` par `core/redirect-uri.ts` (copie de lab-admin : la page courante seulement sous l'origine et le préfixe de l'app, sinon `appUrl`)
- [x] Ports publiés liés à `${LAB_BIND_ADDR:-127.0.0.1}` (chantier « failles », I1)
- [ ] Renommer le service `worker` en `bilan-thermique-worker`
- [ ] Documenter l'exception « couleurs du rendu three.js » (`calcul-3d.component.ts:91-93`, `mesh-viewer.component.ts:25`)
- [ ] Découper `backend/api/tests.py` (4 514 lignes) par domaine
**Tests à passer avant de clore le lot :**
- [ ] `validate-app.sh bilan-thermique --quality-only` vert ; cloisonnement `ok` par le moteur ; même nombre de tests après le découpage
**Vérifié le :** 2026-11-01 — non déployé. Chantier « failles » (secteur E) : image candidate
`bilan-thermique-frontend:candidat` construite depuis une copie propre du frontend (`npm install` +
`ng build` OK ; le bundle porte `pkceMethod:"S256"`, les WOFF2 sont référencés en `./media/…`) ;
`docker compose config` rend `host_ip: 127.0.0.1` pour les deux ports publiés (backend, frontend ;
`worker` et `redis` n'en publient pas). Backend non modifié. Restent le renommage du `worker`, la
documentation des couleurs three.js et le découpage des tests (hors chantier).

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
