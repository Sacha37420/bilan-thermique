# bilan-thermique — to_do.md (Phase 2 : fiabilité, physique manquante, ergonomie · Phase 3 : correctifs + environnement fin)

Lots A→E livrés (`git log` du sous-module) : scaffold, page Théorie + Calcul 1D, bibliothèque de
parois, catalogue de départ (vitrages/murs ITE-ITI/toitures RT2005-RT2012-RE2020), maillage
bâtiment + ombrage par lancer de rayons (Lot B/C), solveur bâtiment multi-triangle sparse (Lot D),
dashboard résultats (Lot E), puis hors numérotation : raffinement de maillage, facteur de vue du
ciel occlus, mode ombrage temps réel, génération auto d'environnement (IGN BD TOPO / OSM) avec
géoréférencement du bâtiment. Détails et pièges déjà rencontrés : mémoire `project_bilan_thermique.md`.

Ce document couvre ce qui reste, identifié en relisant `solver.py`, `building_solver.py`,
`shadow.py`, `geometry.py`, `geodata.py`, `serializers.py` et le parcours Angular
(page Bâtiment → Calcul 3D) le 2026-08-06. Trois familles de chantiers : fiabiliser le solveur
existant (Lot F), combler des manques physiques qui faussent le résultat (Lots G→K, R), simplifier le
travail de l'utilisateur (Lots L→P, S) — Lot Q à cheval sur les deux dernières familles (généralise
G/H à un planning, ce qui est autant une correction physique qu'une simplification de saisie).

**Tous les lots sont livrés : F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U et V** (tests
physiques automatisés, renouvellement d'air, apports internes, cadre de fenêtre, occultations
mobiles, triangles au contact du sol, import météo automatique Open-Meteo + PVGIS TMY, résultats
normalisés kWh/m²/an, checklist de progression, générateur de boîte, aide au calcul de
`c_air_int`, plannings horaires, convection dynamique (vent + orientation), années type, mode
simplifié pour un bâtiment réel existant, rayonnement transmis intégralement à travers un vitrage,
calendrier d'occupation pour le mode thermostat) — voir leur section respective pour le détail.
**Lot V livré le 2026-08-09**, dernier lot restant, cadré avec l'utilisateur avant de coder
(six profils d'usage scolaire/tertiaire/habitation × climatisé ou non, calendrier jour de la
semaine + jours ouvrés + plages de vacances) — voir sa section, notamment la correction de cadrage
en cours de route (le hors-gel « vacances » est propre au scolaire, jamais au tertiaire) tranchée
avec l'utilisateur avant implémentation. **Lot J livré le 2026-08-09**, cadré avec l'utilisateur
avant de coder (patron de factorisation, puis valeurs des deux profils usuels) — voir sa section,
notamment le design en calque (`shading_profile_id`) qui a résolu le dilemme des deux options
d'origine sans les compromis d'aucune des deux. **Lot U (2026-08-08) est un correctif, pas une
nouvelle fonctionnalité** — signalé par l'utilisateur en testant manuellement des fenêtres en 1D,
il touchait `solver._propagate_solar`, partagée par les deux solveurs : jusqu'à 87 % du
rayonnement solaire incident sur tout le catalogue de vitrages disparaissait du bilan avant ce
correctif (voir sa section) — le plus significatif en gravité de tous les correctifs de cette
phase 2.
Chaque lot qui touche `_assemble_F`/`_assemble_F_hour` doit faire passer `python manage.py test api`
avant/après modification — c'est tout l'intérêt du Lot F : détecter une régression au lieu de la
découvrir en production (H, K, Q, I, R, U et J l'ont fait, 15/15 puis 28/28 puis 36/36 puis 43/43
puis 48/48 puis 70/70 puis 78/78 puis 91/91 OK à chaque étape ; Q, R et J touchaient la
factorisation LU du solveur, I a fait remonter un vrai trou de couverture par mutation testing, R un
piège d'identité tautologique (la conservation d'énergie ne peut pas détecter un bug dans K), U
l'inverse — un bug dans **F**, où cette même identité redevient légitime — et J une variante des
deux (un test avec soleil restait aveugle à un bug dans K, masqué par un canal F correct ; corrigé en
retirant le soleil pour isoler le canal K) — voir leurs sections respectives). **Lot I livré avec un
design différent de l'étape 2 d'origine** (tranché
avec l'utilisateur avant de coder — voir sa section) : le vitrage garde son maillage complet sur une
aire réduite plutôt que d'être remplacé par un résistor unique, pour ne pas perdre le gain solaire.
**Lot R livré le 2026-08-08** (cadré avec l'utilisateur avant de coder — formule Jürges, portée
étendue à h_i par orientation — voir sa section) : le « point dur » performance anticipé par le texte
d'origine (factorisation par valeur de vent) s'est avéré non bloquant une fois mesuré en réel
(14-21 classes de vent sur un an réel, ~30 s de calcul dans le pire cas mesuré) ; généralise le
patron de factorisation-par-valeur-distincte du Lot Q à `K_global` en entier plutôt qu'au seul nœud
d'air, comme anticipé. **Lot N livré le 2026-08-08** (checklist de progression, page Bâtiment) — voir
sa section, notamment l'écart assumé sur l'item « météo chargée » (aucun état persisté possible, Lot
L). **Lot O livré le 2026-08-08** (générateur de boîte) — voir sa section, notamment la dérivation à
la main des normales sortantes par groupe, vérifiée en réel via `api.geometry.compute_envelope_geometry`.
**Lot T (mode simplifié, spécifié par
l'utilisateur le 2026-08-07) livré le 2026-08-08** — recherche de bâtiment réel (IGN/OSM) + taux de
vitrage par paroi + météo automatique (réutilise le préremplissage existant de Calcul 3D), pensé
comme point d'entrée pédagogique vers la méthode complète ; les deux décisions à trancher au moment
de coder (regroupement des faces par mur, distinction paroi opaque/vitrage) ont été résolues sans
cadrage utilisateur supplémentaire — voir sa section pour le détail et les découvertes en réel
(limite `MAX_WALLS_SIMPLIFIED_MODE`, cascade de raffinement).

**La phase 2 est close.** La suite du document ouvre une **phase 3** (section « Phase 3 — analyse de
relecture + demandes utilisateur », plus bas), issue d'une relecture complète de l'application le
2026-08-09 et de quatre demandes d'évolution + un bug bloquant signalés par l'utilisateur : Lots W
(correctif bloquant du mode simplifié), X (écarter le bâtiment étudié de son propre environnement),
Y (créer un bâtiment depuis ses coordonnées hors mode simplifié), Z (végétation), AA (terrain/sol),
AB (correctifs physiques et de restitution), AC (mise à jour des limites de la page Théorie). La
section d'analyse qui les précède (A/B/C) est à lire avant d'attaquer n'importe lequel d'entre eux —
plusieurs lots corrigent des choses affirmées à tort dans les sections de la phase 2 ci-dessous.

À lire intégralement avant tout lot, comme pour tout cahier des charges du lab. Mêmes règles que le
reste de `dev/` (CLAUDE.md racine) : mémoire tenue à jour, demander avant tout commit/push et avant
toute décision d'architecture significative non tranchée ici.

---

## Lot F — Tests physiques automatisés ✅ livré le 2026-08-07

### Ce qui a été fait
`backend/api/tests.py` (nouveau, 11 tests, `django.test.SimpleTestCase` — pas de DB, cohérent avec
le reste du lab qui utilise déjà le framework de test Django plutôt que pytest, ex. `restauration`).
Chaque test compare le solveur à un oracle **indépendant de son propre code** (formule physique à la
main ou identité algébrique dérivée à la main) — jamais un appel aux fonctions privées
`_assemble_*`/`_build_mesh` du module testé, pour ne pas faire reproduire un bug côté « attendu ».

1. **Régime permanent connu** (`WallSteadyStateTest`) — mur mono-couche ET mur bi-couche finement
   maillé, comparés à U = 1/(1/h_e + Σe/λ + 1/h_i) après convergence.
2. **Conservation d'énergie** :
   - **1D** (`WallEnergyConservationTest`) — identité EXACTE (pas une tolérance de convergence) issue
     de l'algèbre d'Euler implicite sur un mur à un seul élément : la variation d'énergie stockée
     (capacités nodales `rho*c*e/2`, convention documentée de `_assemble_kc`) égale exactement le
     flux entrant moins sortant, intégré sur une météo **variable** (pas constante, pour ne pas
     laisser une erreur s'annuler par symétrie).
   - **3D** (`BuildingAirNodeEnergyBalanceTest`) — même identité sur le nœud d'air global, dérivée à
     la main : `C_air·ΔT_air = Σ envelope_flux_w·dt` en mode `'free'`. **Déviation assumée par
     rapport à l'énoncé d'origine** : testé en `'free'`/`'thermostat'` plutôt qu'`'imposed'` — en
     mode `imposed` le nœud d'air est figé par Dirichlet, il n'y a rien à conserver qui ne soit déjà
     couvert par le test de cohérence 1D↔3D (étape 3) et par le test 1D en mode `imposed` ci-dessus.
3. **Cohérence 1D ↔ 3D** (`Building1D3DConsistencyTest`) — bâtiment à un seul triangle d'aire
   volontairement différente de 1 m² (3,7 m²), même paroi/météo/conditions que `solver.run_simulation`
   en mode `imposed` : températures de surface identiques à 1e-6 près.
4. **Mode thermostat** (`test_thermostat_mode_hvac_matches_residual_energy_balance`) — même identité
   de conservation que l'étape 2 mais avec le terme HVAC en plus (`C_air·ΔT_air = Σflux·dt +
   (heating_kwh-cooling_kwh)·3,6e6`), sur un scénario météo forçant **chauffage ET climatisation**
   dans la même série (sinon le test ne dit rien sur le signe du résidu).
5. **`sun_direction` dédupliquée** — `building_solver._sun_direction` supprimée, `building_solver.py`
   appelle désormais `shadow.sun_direction` (seul point de vérité pour la convention azimuth).
6. **Ombrage** (`ShadowSkyViewFactorTest`, `ShadowOcclusionTest`) — sans obstacle, le facteur de vue
   du ciel retombe exactement sur `(1+cos(tilt))/2` (toiture plate ET mur vertical) ; avec un
   panneau plat placé juste au-dessus d'un triangle tourné vers le ciel, l'occlusion au zénith
   (élévation 90°) passe bien de visible à bloqué.

**Vérification que les tests détectent vraiment des bugs** (pas seulement qu'ils passent) : mutation
délibérée du signe du résidu HVAC dans une copie temporaire de `building_solver.py`, confirmé que
`test_thermostat_mode_hvac_matches_residual_energy_balance` échoue alors — pas un test vide qui
passerait de toute façon.

Vérifié dans le conteneur backend rebuildé (`setup2.sh bilan-thermique --yes`, pas juste patché à
chaud) : `python manage.py test api` → 11/11 OK, `python manage.py check` → aucun problème.
Inclut aussi une couverture du Lot G (renouvellement d'air) manquante à sa livraison initiale :
`test_free_mode_air_node_conserves_energy_with_ventilation` étend l'identité de conservation avec
le terme `g_vent`, plus une non-régression (plus de ventilation ⇒ un bâtiment chaud se refroidit
davantage vers `t_ext`).

### Reste ouvert
Pas de CI qui lance `manage.py test` automatiquement (aucune app du lab n'en a — cohérent avec le
reste de `dev/`, hors scope de ce lot). Pas de test sur le mode `'realtime'` de l'ombrage
(`_assemble_F_hour` avec `occluder_intersector`) ni sur `geodata.py` (appels réseau, plus difficile à
tester unitairement — nécessiterait de mocker `requests`).

---

## Lot G — Renouvellement d'air / infiltrations ✅ livré le 2026-08-06

### Ce qui a été fait
Terme de ventilation ajouté des deux côtés, mais **pas avec la même donnée d'entrée** — le 1D et le
3D n'ont pas la même convention d'unités pour le nœud d'air (voir `c_air_int` : J/(m²·K) en 1D,
J/K absolu en 3D), la ventilation suit la même logique :

- **1D** (`solver.py`) : `interior.g_vent` (W/m²·K), une conductance déjà ramenée au m² de la paroi
  étudiée — cohérent avec `c_air_int`, pas un débit réel (le 1D n'a pas de volume de bâtiment).
  Appliquée uniquement en mode `'free'` (le seul avec un nœud d'air). `_assemble_F` prend
  `air_idx`/`g_vent` et ajoute `F[air_idx] += g_vent * t_ext` ; `K[air, air] += g_vent` posé une fois
  à l'assemblage.
- **3D** (`building_solver.py`) : `interior.debit_vent_m3h` (m³/h, tout le bâtiment) +
  `interior.eta_recup_vent` (0..1, rendement VMC double flux) — grandeurs physiques réelles, converties
  en `g_vent = 0.34 * debit_vent_m3h * (1 - eta_recup_vent)` (W/K absolu) et ajoutées **une seule fois**
  à `K_global[air_idx, air_idx]`, jamais par triangle. Appliqué en modes `'free'`/`'thermostat'`
  (les deux ont un nœud d'air réel) ; sans effet en `'imposed'` (Dirichlet écrase la ligne du nœud
  d'air — vérifié par test, `flux_positive_kwh`/`flux_negative_kwh` strictement identiques avec ou
  sans `debit_vent_m3h`).
- **Pas de conversion 1D→3D** : ce sont deux paramètres distincts, chacun cohérent avec son solveur —
  la ventilation n'est pas un phénomène de surface (contrairement aux parois), donc pas de facteur
  d'aire entre les deux.
- **Catalogue de profils par époque** (avant 1948, 1948-1974, 1974-2000 RT/VMC autoréglable, 2000-2012
  hygroréglable, RE2020 double flux) : `frontend/src/app/core/ventilation-profiles.ts`, **côté 3D
  uniquement** — un profil (taux vol/h + rendement) ne se combine à un débit réel qu'avec un volume de
  bâtiment réel, qui n'existe pas côté 1D. UI Calcul 3D : champ volume (m³) + sélecteur de profil qui
  pré-remplit débit/rendement, modifiables ensuite à la main.
- Vérifié en réel (conteneur backend, patché à chaud puis testé) : la ventilation accélère bien le
  refroidissement en 1D et en 3D, la récupération de chaleur réduit bien la perte nette sans
  l'annuler, le mode `'imposed'` est bien insensible à `debit_vent_m3h`.

### Reste ouvert
Test automatisé pérenne ajouté depuis (Lot F, `test_free_mode_air_node_conserves_energy_with_ventilation`).
Reste hors scope : pas d'affichage dans le dashboard de la part du besoin de chauffage imputable à
la ventilation (différence avec/sans `debit_vent_m3h`) — resterait utile pour que l'utilisateur juge
l'ordre de grandeur. `debit_vent_m3h`/`eta_recup_vent` restent des **constantes pour tout le run** —
voir Lot Q pour les faire varier heure par heure (planning d'occupation), au même titre que les
apports internes du Lot H.

---

## Lot H — Apports internes (occupants, éclairage, électroménager) ✅ livré le 2026-08-07

### Ce qui a été fait
`apports_internes_w` (W, défaut 0) ajouté à `BuildingInteriorSerializer` — 3D uniquement (comme
`debit_vent_m3h`/Lot G, un bâtiment a un volume réel, pas le 1D), injecté directement dans
`F[air_idx]` (`_assemble_F_hour`/`run_building_simulation`, `building_solver.py`) : puissance
directe (W), pas de multiplication par `t_ext` contrairement à `g_vent`. Actif en modes
`'free'`/`'thermostat'` uniquement — sans effet en `'imposed'` (Dirichlet écrase la ligne du nœud
d'air, même raisonnement que le Lot G, vérifié par test). UI Calcul 3D : champ « Apports internes
(W) » dans le bloc intérieur libre/thermostat. Documenté dans la page Théorie, section « Portée et
hypothèses » (paragraphe sur les deux termes que le solveur 3D ajoute au nœud d'air, absents du
bilan 1D d'une paroi isolée). Tests (`backend/api/tests.py`, classe
`BuildingAirNodeEnergyBalanceTest`) : identité de conservation d'énergie
`C_air·ΔT_air = Σ(flux + apports_internes_w)·dt` (mode `'free'`), non-régression (des apports
internes laissent un bâtiment plus chaud), et non-effet en mode `'imposed'`.

### Reste ouvert
Valeur constante pour tout le run — le besoin d'un profil horaire (planning d'occupation) est
confirmé et traité au **Lot Q**, qui généralise `apports_internes_w` ET `debit_vent_m3h`/
`eta_recup_vent` (Lot G) au même mécanisme de planning (même moteur physique : la présence
humaine). Lot Q peut démarrer maintenant que ce lot est livré.

---

## Lot I — Cadre de fenêtre (Uw, fraction de cadre) ✅ livré le 2026-08-07

### Ce qui a été fait
**Design différent de l'étape 2 d'origine, tranché avec l'utilisateur avant de coder** (voir
mémoire projet) : la formule `U_global` combinée en un résistor unique remplaçant tout le système
du triangle a été écartée — elle aurait perdu le calcul détaillé du gain solaire du vitrage (le
poste le plus important pour une fenêtre) sans que le texte d'origine ne dise ce qui le remplace.
Design retenu à la place : le maillage multicouche existant du vitrage garde sa physique complète
(gains solaires couche par couche, capacité) **sur l'aire réduite à `(1 - frame_fraction) * aire`**
— aucun changement à `_build_triangle_systems` ni à la forme de `paroi_layers_by_id` (le cadre est
passé séparément via `paroi_frame_by_id = {paroi_model_id: (frame_u, frame_fraction)}`, construit
dans `tasks.py`). Le cadre lui-même devient une **résistance directe extérieur → nœud d'air**,
exactement le même schéma que `g_vent` (Lot G) mais **par triangle** plutôt qu'un terme global
unique (des fenêtres différentes peuvent avoir des cadres différents) : conductance
`frame_fraction * frame_u * aire_triangle`, ajoutée à `K_global[air_idx,air_idx]`
(`_assemble_global_kc`) et à `F[air_idx]` heure par heure (`_assemble_F_hour`, réutilise le même
`t_boundary` que la paroi — donc `t_ground` si le triangle est aussi marqué `'ground'`, combo non
réaliste pour une fenêtre mais géré sans crash). Pas de nœud de surface propre pour le cadre : pas
de comportement optique, capacité négligeable — hypothèses du texte d'origine conservées, juste
réparties différemment. `frame_u` est interprété comme déjà inclusif des résistances superficielles
(comme Uf/Uw dans le vocabulaire fenêtre standard, cohérent avec Ug du catalogue — voir
`seed_paroi_catalogue.py`, calculé avec Rsi/Rse conventionnels).

`ParoiModel.frame_u`/`frame_fraction` (migration `0009`), les deux à renseigner ensemble ou ni l'un
ni l'autre (`ParoiModelSerializer.validate`). **`frame_fraction` plafonné à 0,95, pas 1,0**, piège
découvert en concevant les tests : à 1,0 l'aire "vitrage" tomberait exactement à zéro, annulant tout
le bloc du triangle dans la matrice globale (lignes nulles) → système singulier, `spla.splu`
échouerait. Garde-fou en double : plafond côté `ParoiModelSerializer` ET vérification directe dans
`run_building_simulation` (`BuildingSimulationError` explicite) pour tout appelant direct du solveur
qui contournerait le serializer. Catalogue de départ étendu (cadre PVC usuel, `frame_u` 2,0/1,8
W/m²·K pour simple/double vitrage, `frame_fraction` 0,25). **UI non prévue par le texte d'origine
mais ajoutée** (page « Bibliothèque de parois ») : sans elle, `frame_u`/`frame_fraction` n'auraient
été réglables que par appel API direct — case à cocher « a un cadre » + deux champs, affichage
récapitulatif dans la liste des modèles.

**Tests** (`backend/api/tests.py`, `WindowFrameTest`, 5 nouveaux — 48/48 au total) :
non-régression `frame_fraction=0`, conservation d'énergie généralisée (frame_fraction 0,3 et 0,9),
attribution par triangle (comparaison différentielle, pas une égalité stricte — voir piège
ci-dessous), validation `frame_fraction` invalide. Mutation testing : 2 mutations injectées, la
formule de conductance du cadre détectée immédiatement (test de conservation), **la réduction d'aire
du vitrage NON détectée du premier coup** — un vrai trou de couverture corrigé en cours de route
(voir mémoire projet pour le piège de conception qui l'a causé).

### Reste ouvert
Aucun.

---

## Lot J — Occultations mobiles (volets, stores) ✅ livré le 2026-08-09

### Ce qui a été fait
Cadré avec l'utilisateur avant de coder, sur deux échanges successifs : d'abord confirmation que le
patron de factorisation-par-combinaison-distincte (Lot Q/R) s'applique aussi ici pour éviter de
refactoriser heure par heure, puis fourniture de deux profils usuels concrets (valeurs indicatives,
pas une table réglementaire officielle — même esprit que le catalogue de parois) :

| | Volet roulant (PVC/alu) | Store extérieur (toile/brise-soleil) |
|---|---|---|
| ΔR ajoutée | 0,20 m²·K/W | 0,08 m²·K/W |
| Fs,dir (fermé) | 0,0 (opaque) | 0,15 |
| Fs,dif (fermé) | 0,0 | 0,40 |

Toujours **entièrement fermés** quand actifs — pas de position intermédiaire modélisée, comme
explicitement demandé.

**Design retenu, résolvant le dilemme des deux options d'origine** : ni un champ résistance globale
(option 1, imprécis — un seul volet pour tout le bâtiment) ni un second `ParoiModel` complet par
fenêtre fermée (option 2 littérale, qui aurait nécessité de recalculer `_build_triangle_systems`
heure par heure) — un dispositif est un **calque** (`shading_profile_id`, nouveau champ sur chaque
triangle, indépendant de `paroi_model_id`) appliqué PAR-DESSUS le vitrage existant : résistance
ajoutée en série avec `h_e` (`1/(1/h_e + deltaR)`, PAR TRIANGLE) et rayonnement réduit
(`e_dir *= fs_dir`, `e_dif *= fs_dif`) AVANT propagation dans `_propagate_solar`. `paroi_model_id`
et `_build_triangle_systems` restent totalement inchangés — aucune reconstruction de maillage,
seulement K/F modifiés au moment de l'assemblage, exactement comme le cadre de fenêtre (Lot I) ou
`g_vent` (Lot G).

**Restructuration de `building_solver.py`** : puisque `h_e` peut désormais varier PAR TRIANGLE (pas
seulement par heure comme au Lot R), le motif `K_e_pattern` du Lot R (scalaire `h_e * pattern`
précalculé une fois) ne suffit plus — remplacé par `_h_e_diagonal(h_e_vec, ...)`, qui construit la
contribution directement depuis un vecteur PAR TRIANGLE (uniforme = `h_e` de l'heure si aucun volet
fermé, réduit pour les seuls triangles concernés sinon). Coût O(nb triangles), négligeable devant la
factorisation elle-même — reconstruite seulement pour chaque combinaison DISTINCTE `(g_vent, h_e,
volets_fermes)` rencontrée, toujours paresseusement (Lot R). `volets_fermes` réutilise le `planning`
24h du Lot Q (`PlanningEntrySerializer.volets_fermes`) — **un seul planning pour toutes les fenêtres
à dispositif**, comme demandé, pas un planning par fenêtre. Contrairement à
`debit_vent_m3h`/`apports_internes_w`, s'applique dans **tous** les modes intérieurs (y compris
`'imposed'`) : `h_e` touche l'équation de chaque triangle, pas seulement le nœud d'air libre.

**Piège découvert empiriquement en écrivant les tests (avant tout bug réel), instructif** : un
premier test comparant un run « ouvert puis fermé » à un run « toujours ouvert », AVEC soleil, ne
détectait PAS une mutation qui retire `volets_fermes` de la clé de cache — parce que la réduction
optique (F, appliquée fraîchement chaque heure indépendamment du cache) suffisait à elle seule à
créer un écart, masquant l'absence de mise à jour du côté K (résistance). Corrigé en retirant tout
soleil du test : sans lui, le SEUL effet possible d'un volet fermé est la résistance ajoutée — un
bundle réutilisé à tort pour la mauvaise heure fait alors disparaître TOUT écart avec « toujours
ouvert » (vérifié par mutation : les deux scénarios deviennent bit-à-bit identiques). Même famille de
leçon que le piège du Lot R (une identité peut être aveugle à un bug situé dans une partie du système
qu'elle ne teste pas assez directement) mais sous une forme différente : ici il fallait ÉLIMINER un
canal (l'optique) pour isoler l'autre (la résistance), pas changer d'oracle.

**Backend** — champs ajoutés : `TriangleInputSerializer.shading_profile_id` (choix parmi
`building_solver.SHADING_PROFILES`, défaut `None`) ; `PlanningEntrySerializer.volets_fermes` (bool,
défaut `False`). **Piège du Lot K appliqué par anticipation** : `BuildingSerializer._build_envelope`
reconstruit les triangles existants lors d'un PATCH partiel (`triangles` absent) — `shading_profile_id`
ajouté explicitement à cette liste de reconstruction dès l'écriture du champ, pas découvert après
coup. `geometry.py` (pass-through générique par exclusion, pas par inclusion) n'a nécessité AUCUNE
modification — vérifié en le relisant plutôt que supposé.

**Frontend** : nouveau module d'affichage `shading-profiles.ts` (id/label/description SEULEMENT —
les valeurs physiques elles-mêmes vivent uniquement côté backend, un seul point de vérité). Page
Bâtiment : sélecteur d'occultation par groupe OBJ, même patron que l'assignation de paroi/condition
limite déjà en place. Page Calcul 3D : 4e colonne optionnelle du planning existant
(`volets_fermes`), et la condition qui limitait l'envoi du `planning` aux modes libre/thermostat a
été retirée (les 3 champs ventilation restent ignorés en mode imposé côté backend, mais
`volets_fermes` doit désormais passer dans tous les cas). **Écart UI assumé** : le panneau
« Planning horaire » reste visible seulement en modes libre/thermostat (même gating que le
renouvellement d'air) — un utilisateur en mode imposé ne peut donc pas configurer de volets depuis
l'UI, bien que l'API le permette (vérifié directement) ; cohérent avec le fait qu'aucun réglage
n'est exposé pour le mode imposé de toute façon (T_int fixée artificiellement, cas déjà secondaire
dans cette app).

**Tests** (`backend/api/tests.py`, `MovableShadingTest` + `TriangleShadingSerializerTest`, 17
nouveaux) : catalogue sain, formule série indépendante, volet roulant fermé ≡ h_e réduit à la main +
rayonnement nul (équivalence exacte), store extérieur ≡ identité de conservation étendue à un terme
calculé à la main (transmission partielle, pas nulle), deux triangles à dispositifs différents
divergent, effet en mode imposé confirmé, non-régression (triangle sans dispositif jamais affecté),
préservation lors d'un PATCH partiel (`_build_envelope` appelé directement sur une instance
`Building()` non sauvegardée — aucun accès DB, cohérent avec le reste de `tests.py`), validation des
deux nouveaux champs de serializer. Mutation testing : clé de cache (voir piège ci-dessus), formule
de résistance série, réduction optique — les 3 confirmées détectées après correction du test de
transition. 91/91 tests au total, `manage.py check` propre.

**Vérifié en réel de bout en bout sur l'image rebuildée**, via le chemin API public complet (pas
seulement `run_building_simulation` directement) : création d'un bâtiment avec un triangle vitrage
+ volet-roulant (`BuildingSerializer`), sauvegarde en base, rechargement, **PATCH partiel
(vertices seul) confirmant `shading_profile_id` survit** (piège du Lot K vérifié sur le vrai flux
DB, pas seulement l'instance non sauvegardée des tests), validation `BuildingCalculRequestSerializer`
avec un planning `volets_fermes`, puis simulation complète réussie. `ng build` propre.

### Reste ouvert
Orientation de la façade par rapport au vent toujours pas modélisée pour `h_e` de base (simplification
du Lot R, inchangée). Pas de planning volets par groupe/fenêtre (un seul planning global, comme
demandé — une évolution possible si le besoin apparaît). Pas d'indicateur visuel dans le viewer 3D
pour les triangles ayant un dispositif assigné (seul le compteur du groupe le confirme). Panneau
« Planning horaire » invisible en mode imposé (voir ci-dessus). Non vérifié en navigateur réel (même
réserve que les autres lots récents).

---

## Lot K — Triangles au contact du sol (dallage) ✅ livré le 2026-08-07

### Ce qui a été fait
Champ `boundary` sur `TriangleInputSerializer` : `'exterior_air'` (défaut, comportement historique
inchangé pour tout maillage existant) ou `'ground'`. Température de sol **constante fournie dans le
payload de calcul** (`t_ground`, `BuildingCalculRequestSerializer`, défaut 12°C — moyenne annuelle
usuelle en France) plutôt qu'un champ sur `Building` : reste modifiable d'un run à l'autre sans
éditer le bâtiment, pas de migration sur `Building`. `_assemble_F_hour`
(`building_solver.py`) : pour un triangle `'ground'`, `f_local[0] += h_e * t_ground` au lieu de
`h_e * t_ext` — même conductance `h_e` que les autres triangles (pas de `h_e` distinct pour le sol,
cf. étape 3 d'origine — extension possible, non faite). UI : sélecteur « Sol / Air extérieur » par
groupe OBJ sur la page Bâtiment (même patron que l'assignation de modèle de paroi par groupe),
champ « Température de sol » sur la page Calcul 3D. Piège corrigé au passage :
`BuildingSerializer._build_envelope` reconstruisait les triangles existants (chemin PATCH
« triangles seul ») en ne recopiant que `v`/`group`/`paroi_model_id` — `boundary` aurait été
silencieusement perdu à la moindre sauvegarde partielle sans le correctif.

Tests (`backend/api/tests.py`, `GroundBoundaryTest`) : identité EXACTE (pas une convergence) — un
triangle `'ground'` soumis à une météo `t_ext` très différente et variable doit reproduire au
chiffre près (1e-6) un mur 1D alimenté par une météo **constante** égale à `t_ground`, prouvant que
`t_ext` n'a strictement aucune influence sur ce triangle ; non-régression sur un triangle
`'exterior_air'` (jamais influencé par `t_ground`, quelle que soit sa valeur).

### Reste ouvert
`h_e` distinct pour un triangle au sol (pas de convection due au vent contre la terre) — extension
possible, non faite (voir étape 3 d'origine). Dérivation automatique de `t_ground` depuis la
moyenne annuelle de la météo — n'a de sens qu'une fois le Lot L (météo réelle) en place.

---

## Lot L — Import météo automatique (Open-Meteo Archive) ✅ livré le 2026-08-07

### Ce qui a été fait
Nouveau module pur `backend/api/weather_source.py` (même patron que `geodata.py`) :
**Open-Meteo Archive** retenu comme unique source (PVGIS TMY laissé de côté — voir Lot S, qui
reste la bonne occasion d'ajouter une seconde source « année type »). `temperature_2m`,
`direct_normal_irradiance`, `diffuse_radiation` → `t_ext`/`e_dir`/`e_dif` directement (mêmes
grandeurs physiques, DNI = irradiance normale au rayon = exactement ce que `e_dir*cos(theta_i)`
attend). Open-Meteo ne fournissant ni azimuth ni élévation, calcul de la **position solaire réelle**
heure par heure : déclinaison + équation du temps (séries de Fourier de Spencer 1971, la référence
usuelle — citée par NOAA et Duffie & Beckman), puis (élévation, azimuth réel) par un changement de
repère équatorial → horizon **dérivé à la main** (`_elevation_azimuth`) plutôt que la formule
`cos(azimuth)` usuelle des calculatrices solaires — dont la disambiguïsation de quadrant (signe de
l'angle horaire) est une source classique d'erreur de signe, piège concrètement rencontré pendant ce
lot (première version basée sur la formule usuelle donnait un azimuth de 0° au lieu de 180° à midi
solaire). Formule finale recoupée numériquement avec la bibliothèque tierce `astral` (installée
temporairement dans le conteneur pour vérification, jamais ajoutée aux dépendances de l'app) sur 7
cas latitudes/saisons/hémisphères variés : écart < 0,4° partout sauf singularité connue au zénith.

**Point d'attention critique traité** : `to_local_azimuth` applique très exactement la même
rotation que `geodata._rotate_xy` (même signe, même convention `Building.georef_north_offset_deg`),
appliquée à un vecteur direction plutôt qu'à un point. Vérifié à la main comme demandé (soleil plein
sud à midi solaire quand latitude > déclinaison — cas général de « soleil plein sud », pas
seulement à l'équinoxe) et par test automatisé avec rotation à 90° (voir tests). Azimuth déjà dans
la convention interne (`geometry.py`) en sortie de `build_weather_series` — aucune conversion
supplémentaire côté appelant.

Tâche Celery `fetch_weather` (même pattern que `generate_environment`) + **endpoint autonome**
`POST /api/meteo/recuperer/` (pas `/batiments/<id>/meteo/` — la météo n'est jamais persistée côté
serveur, seulement renvoyée via `job.result` ; un endpoint autonome fonctionne aussi pour un
bâtiment non géoréférencé, conforme à l'étape 4 d'origine). `WeatherFetchRequestSerializer` : dates
converties en chaînes ISO avant `Job.params`/`.delay()` — un `datetime.date` brut n'est pas
sérialisable JSON (piège rencontré et corrigé pendant ce lot). UI page Calcul 3D : panneau
« Récupérer depuis Open-Meteo » (lat/lon pré-remplies depuis le géoréférencement du bâtiment si
disponible, rotation nord, période) au-dessus du bloc météo constante ; CSV manuel et générateurs
synthétiques conservés mais annotés explicitement « donnée de démonstration » (étape 5).

Tests (`backend/api/tests.py`, 13 nouveaux — `SolarEphemerisTest`, `SolarElevationAzimuthTest`,
`LocalAzimuthRotationTest`, `WeatherSeriesAssemblyTest`) : déclinaison bornée par l'inclinaison de
l'axe terrestre (fait astronomique indépendant de la formule), midi solaire → azimuth exact
0°/180° selon signe(déclinaison-latitude) + élévation exacte `90-|lat-décl|` (identité dérivée à la
main), lever/coucher exactement Est/Ouest à l'équinoxe à 6 latitudes différentes, symétrie
temporelle autour de midi solaire, rotation locale (identité à offset nul + deux cas à 90°),
assemblage de série synthétique (clampe vers les bornes de `BuildingWeatherPointSerializer`, saute
les heures à donnée manquante sans en inventer). **`_assemble_weather_series` séparée de
`fetch_open_meteo_archive`** spécifiquement pour rendre l'assemblage testable sans réseau (suit la
même réserve que le Lot F pour `geodata.py`, tout en couvrant la partie qui ne nécessite pas de
mocker `requests`). Vérifié par mutation testing sur les deux formules les plus critiques
(`_elevation_azimuth` et `to_local_azimuth`) : échec confirmé sur code muté, 28/28 après
restauration. Vérifié en réel sur l'image rebuildée (`recompose_docker.sh --force`) : appel direct
à l'API Open-Meteo (curl + script Python dans le conteneur), pipeline complet
DRF → `Job` → Celery **via le vrai worker/Redis** (`.delay()`, pas seulement `.apply()` synchrone) →
résultat exploitable, et chemin d'erreur (date hors plage Open-Meteo) → `Job` en `ERROR` avec message
clair plutôt qu'un crash. `manage.py test` → 28/28, `manage.py check` → propre, `ng build
--configuration production` → propre.

### Reste ouvert
**PVGIS TMY** (année type) non ajouté — traité au Lot S, qui devient significativement plus simple
maintenant que `weather_source.py` existe (juste une seconde fonction de fetch produisant le même
format de sortie). `vent_m_s` (Lot R, livré depuis) est désormais demandé à Open-Meteo — voir la
section du Lot R pour le détail (`wind_speed_unit=ms` explicite, le défaut étant km/h).
Pas de vérification manuelle dans un navigateur (UI derrière Keycloak SSO, non testée en
interactif) — vérifié uniquement en API directe + build de production ; à tester en réel dans le
navigateur avant de recommander l'outil pour un vrai bâtiment.

---

## Lot M — Surface de référence et résultats normalisés (kWh/m²/an) ✅ livré le 2026-08-07

### Ce qui a été fait
`surface_ref_m2` (optionnel, saisie directe — pas de déduction automatique depuis les triangles de
plancher, qui aurait supposé le Lot K déjà exploité pour ça) ajouté à `Building`
(migration `0008_building_surface_ref_m2`), champ sur la page Bâtiment. Dashboard Calcul 3D :
tuiles « Chauffage normalisé »/« Climatisation normalisée » (`heating_kwh`/`cooling_kwh` divisés
par `surface_ref_m2`) à côté des valeurs brutes existantes, avec un message explicite invitant à
renseigner la surface si absente (plutôt que de masquer silencieusement la fonctionnalité). Repère
visuel : note textuelle sous les tuiles (RT2012 ≈ 50 kWh/m²/an, RE2020 plus strict), avec la
réserve explicite que ces seuils couvrent aussi ECS/éclairage/auxiliaires non modélisés ici — donc
une comparaison approximative, pas une certification.

### Reste ouvert
Repère visuel volontairement textuel (pas de jauge/couleur) — un raffinement graphique reste
possible mais non fait faute de temps dans ce lot.

---

## Lot N — Checklist de progression guidée (page Bâtiment) ✅ livré le 2026-08-08

### Ce qui a été fait
Checklist compacte affichée en haut de la page Bâtiment (`batiment.component.ts`, getter `checklist`)
dès qu'un bâtiment est chargé/créé — 4 items dérivés uniquement de l'état déjà présent côté client
(aucun nouvel endpoint, comme requis) : **parois assignées** (`assignedCount === totalCount`),
**géoréférencement** (`georefLat`/`georefLon` renseignés), **environnement lié**
(`selectedEnvironmentId`), **ombrage à jour** (`!sunVisibilityStale()`). Chaque item est un lien
(`<a href="#section-...">`) vers l'ancre de la section qui le résout sur la même page — trois nouveaux
`id` posés sur les blocs existants (`#section-georef`, `#section-ombrage`, `#section-assignation`),
pas de nouvelle UI dupliquée. Badge généralisé plutôt que dupliqué, comme demandé : l'ancien
`.stale-badge` (couleur `--warning`/`--warning-tint`, un seul état) devient `.status-badge` avec deux
variantes (`.done` → `--success`/`--success-tint`, `.todo` → l'ancien style inchangé) — l'usage
existant du badge ombrage (`sunVisibilityStale()`) a été migré vers cette classe généralisée plutôt
que d'en garder deux qui se ressemblent.

**Écart assumé par rapport au texte d'origine** : « météo chargée » listée comme 5ᵉ item n'a **aucun**
état persisté à observer — Lot L a délibérément choisi de ne jamais stocker la météo côté serveur
(`weather_source`, réponse renvoyée directement au client, jamais écrite sur `Building`), et la page
Bâtiment n'a de toute façon aucun accès à l'état local de la page Calcul 3D. Ajouter un état factice
aurait été trompeur (rien ne peut vraiment se « décocher » si l'utilisateur revient sur Calcul 3D sans
rien construire). Remplacé par un item non trackable en fin de liste — toujours affiché, jamais coché
— pointant vers Calcul 3D avec la mention explicite que cette étape n'est pas suivie ici. Item
« parois assignées » également sans lien d'ancre séparé menant ailleurs : sa résolution est déjà sur
cette même page, juste plus bas.

Vérifié sur l'image rebuildée (`recompose_docker.sh --app bilan-thermique --force`) : le chunk lazy
de la page Bâtiment contient bien le nouveau code (`grep checklist` sur le bundle nginx), build
Angular sans erreur (`ng build` fait partie du `docker build`, un échec de template aurait fait
échouer le build), `manage.py test` → 56/56 inchangé (lot purement frontend), `manage.py check`
propre. **Non vérifié en navigateur réel** — même réserve que Lots L/S/Q/T (app derrière Keycloak SSO,
non testée en interactif dans cette session).

### Reste ouvert
Pas de test frontend dédié (aucune infrastructure de test frontend dans cette app, cohérent avec les
lots précédents à UI pure — P, T). Pas d'ancre pour l'item « parois assignées » si aucun triangle n'est
encore importé (`#section-assignation` n'existe pas tant que `totalCount === 0`) — sans conséquence
(le lien est simplement sans effet, le hint textuel dit déjà « importez un maillage »).

---

## Lot O — Générateur de bâtiment simple (« boîte ») ✅ livré le 2026-08-08

### Ce qui a été fait
Nouveau module pur `frontend/src/app/core/box-generator.ts` (`generateBoxEnvelope`, même patron que
`mesh-import.ts` — aucune dépendance Angular/Django) : génère `vertices`/`triangles`/`groups`
directement côté client à partir de largeur/longueur/hauteur, sans endpoint dédié, comme prévu.
Groupes nommés automatiquement (`sol`, `mur_est`, `mur_ouest`, `mur_sud`, `mur_nord`, `toiture` ou
`toiture_est`/`toiture_ouest` en 2 pans) — l'assignation par groupe déjà en place sur la page Bâtiment
fonctionne immédiatement, aucune modification nécessaire là. `sol` est marqué `boundary: 'ground'`
(convention Lot K, déjà réutilisée par le Lot T) plutôt que `exterior_air`, cohérent avec toute autre
source de maillage de l'app. Nouveau bloc « Générer une boîte » sur la page Bâtiment
(`batiment.component.html`), juste après l'import de fichier, avec un rappel que l'import reste la
voie avancée pour une géométrie réelle (et un lien vers le mode simplifié du Lot T pour un bâtiment
réel existant) — le générateur n'est qu'un point d'entrée pédagogique/de test, comme spécifié.

**Point délicat traité avec soin, malgré une géométrie qualifiée de « triviale » par le texte
d'origine** : chaque triangle doit avoir une normale sortante correcte (`api.geometry` la déduit du
seul ordre des sommets — produit vectoriel) pour que l'ombrage/l'exposition solaire aient un sens
physique. L'ordre des sommets de chacun des 6 groupes (et des 2 groupes de pignon en toiture 2 pans)
a été dérivé à la main (produit vectoriel), puis **vérifié en réel** dans le conteneur backend en
appelant directement `api.geometry.compute_envelope_geometry` sur le maillage généré : tilt/azimuth
obtenus exactement conformes à la convention documentée dans `geometry.py` pour les deux variantes de
toiture (murs à tilt=90° et l'azimuth attendu par orientation ; toiture plate à tilt=0° ; toiture 2
pans à tilt≈20,6° pour une boîte 8×6×3 avec faîtage +1,5 m, azimuth 90°/270° pour les pans est/ouest ;
sol à tilt=180°, normale vers le bas) — et aires cohérentes avec la géométrie attendue (aire de pan
en 2 pans = longueur de rampant × largeur, rampant = √(4²+1,5²) ≈ 4,27 m). Round-trip complet vérifié
via `BuildingSerializer` (création réelle, `manage.py shell`) : 16 triangles, 7 groupes, `sol` bien
seul groupe marqué `ground`.

Toiture 2 pans : faîtage toujours orienté nord-sud (les murs est/ouest restent rectangulaires jusqu'à
l'égout, les murs nord/sud deviennent des pentagones portant le pignon) — un seul degré de liberté
d'orientation plutôt que deux, cohérent avec l'esprit « point d'entrée simple » du lot (une vraie
orientation de faîtage librement choisie aurait nécessité un paramètre de rotation supplémentaire,
hors scope).

Vérifié sur l'image rebuildée (`recompose_docker.sh --app bilan-thermique --force`) : chunk lazy de
la page Bâtiment contient bien le nouveau code, build Angular sans erreur, `manage.py test` → 56/56
inchangé (lot purement frontend), `manage.py check` propre.

### Reste ouvert
Pas de test automatisé dédié dans `tests.py` — `box-generator.ts` est un module frontend pur sans
infrastructure de test frontend dans cette app (même situation que Lots P/T), vérifié à la place par
appel réel à `api.geometry.compute_envelope_geometry` sur le maillage produit (voir ci-dessus).
Orientation du faîtage fixe (nord-sud) — toiture à 2 pans orientée est-ouest non couverte, hors scope
initial. Non vérifié en navigateur réel (même réserve que L/S/Q/N/T).

---

## Lot P — Aide au calcul de `c_air_int` ✅ livré le 2026-08-07

### Ce qui a été fait
Bouton « Calculer depuis le volume » à côté du champ `c_air_int` (page Calcul 3D) :
`c_air_int = round(volumeM3 * 1200)` (J/m³·K, capacité thermique volumique usuelle de l'air),
champ resté éditable ensuite. Réutilise **le champ `volumeM3` déjà existant** (bloc « Renouvellement
d'air », Lot G) plutôt qu'un champ dupliqué — c'est physiquement le même volume d'air du bâtiment
qui sert aux deux calculs. Pas de déduction depuis `surface_ref_m2` × hauteur sous plafond (option
mentionnée en note) : `volumeM3` saisi directement est suffisant et évite une hauteur sous plafond
supplémentaire à demander.

### Reste ouvert
Aucun test automatisé ajouté (calcul UI pur, une multiplication ; l'app n'a pas d'infrastructure de
test frontend existante — aucune autre page n'en a non plus, cohérent avec le reste du lab).
Vérifié par un build Angular de production propre (`ng build --configuration production`, aucune
erreur, seulement les warnings de budget SCSS déjà présents avant ce lot).

---

## Lot Q — Plannings horaires (ventilation + apports internes) ✅ livré le 2026-08-07

### Ce qui a été fait
**Décision de l'étape 1 confirmée telle que recommandée** : un seul profil « jour type » (24
valeurs, cyclique), pas de distinguo semaine/week-end — reste hors scope (nécessiterait une donnée
de calendrier, absente de `BuildingWeatherPointSerializer` même avec le Lot L livré entretemps :
une série météo reste une séquence d'heures sans date attachée).

`PlanningEntrySerializer` (`{debit_vent_m3h?, eta_recup_vent?, apports_internes_w?}`, mêmes bornes
que les champs constants équivalents) + `planning` (liste, `required=False`, validée à exactement 24
entrées via `validate_planning`) + `heure_debut` (0-23, défaut 0) sur
`BuildingCalculRequestSerializer`. Absent → comportement inchangé (constantes de `interior`).

**Point dur traité** (`building_solver.py`) : extraction de `_factorize_for_g_vent(K_global_no_vent,
C_global, air_idx, mode, g_vent)` — construit `K_global[air_idx,air_idx] += g_vent` puis factorise
(`spla.splu`, l'étape coûteuse) le(s) système(s) nécessaires au mode donné, retournés dans un dict
(`'imposed'` : solver+col_saved+dirichlet_row ; `'free'` : solver ; `'thermostat'` : solver+
pinned_solver+A_free+col_saved_pinned, pour le résidu HVAC). `run_building_simulation` calcule
`g_vent_by_slot`/`apports_by_slot` (24 valeurs) si `planning` fourni, sinon une seule constante comme
avant ; `bundles_by_g_vent = {g: _factorize_for_g_vent(...) for g in set(g_vent_by_slot)}` — une
factorisation par **valeur distincte**, jamais par heure simulée (24 au plus, souvent bien moins :
le test de conservation d'énergie du Lot Q exerce jusqu'à 12 valeurs distinctes sur 24 heures et
reste quasi-instantané). La boucle horaire sélectionne `bundle = bundles_by_g_vent[g_vent]` selon
`(heure_debut + hour_idx) % 24`. `apports_internes_w` par heure n'affecte que `F` (recalculé à
chaque heure de toute façon) — aucun problème de factorisation de ce côté, comme anticipé. Sans
effet en mode `'imposed'` (le planning n'y est simplement pas exploité), cohérent avec les
constantes qu'il remplace.

**UI** (page Calcul 3D) : case à cocher « Utiliser un planning horaire », qui révèle un champ
`heure_debut` + un textarea 24 lignes (`débit_m3h, eta_recup, apports_w`) + un bouton d'exemple
(profil résidentiel plausible, creux nocturne/pointes matin-soir, dérivé des constantes déjà
saisies). Les champs constants restent affichés et actifs comme point de départ — un planning est
une variation AUTOUR, pas un remplacement (conforme à l'étape 5 d'origine).

Tests (`backend/api/tests.py`, 7 nouveaux — `HourlyPlanningTest`, `PlanningSerializerTest`) :
identité EXACTE entre planning constant (24 entrées identiques) et constantes équivalentes, dans les
trois modes `'imposed'`/`'free'`/`'thermostat'` (non-régression sur le nouveau mécanisme de bundles) ;
`heure_debut` sélectionne bien le bon index dès `hour_idx=0` ; identité de conservation d'énergie
généralisée à un `g_vent`/`apports_internes_w` variable heure par heure (même famille que Lot F/G,
avec un planning exerçant réellement plusieurs bundles distincts) ; `'imposed'` ignore le planning ;
validation stricte à 24 entrées. Mutation testing sur la sélection de slot (`heure_debut`) et sur la
sélection de bundle (forcer l'usage d'un seul bundle peu importe `g_vent`) : échec confirmé dans les
deux cas, 43/43 après restauration. Vérifié en réel sur l'image rebuildée via le pipeline DRF →
`validated_data` → `run_building_simulation` complet (payload avec planning variable + `heure_debut`
non nul).

---

## Lot R — Coefficient de convection extérieure dynamique (h_e) ✅ livré le 2026-08-08

### Ce qui a été fait
Cadré avec l'utilisateur avant de coder (`AskUserQuestion`, deux questions) : formule **Jürges**
(`h_e = 5,8 + 3,94·v`, retenue plutôt que McAdams comme pressenti — plus couramment citée en physique
du bâtiment française) et **portée étendue** aux deux volets prévus (h_e dynamique ET h_i par
orientation dans le même lot, plutôt que h_e seul).

**Le « point dur » performance a été mesuré empiriquement AVANT de coder, pas supposé** : appels curl
réels à Open-Meteo Archive sur une année complète (2023) à Paris et à Brest (côte venteuse, cas
défavorable) — le vent arrondi au m/s le plus proche ne donne que **14 à 21 valeurs distinctes** sur
8760 heures, largement dans l'ordre de grandeur déjà géré par le Lot Q (24 valeurs). Combiné à un
planning de ventilation (Lot Q) à 24 valeurs toutes distinctes (pire cas), le nombre de paires
(heure_du_jour, classe_vent) réellement rencontrées sur une année reste **272-338** — mesuré, pas
supposé. Benchmark réel d'une factorisation LU sur un bâtiment de taille réaliste (768 triangles,
8449 DOF) : ~0,09 s chacune, donc ~30 s pour le pire cas mesuré — largement absorbable sur le worker
`--concurrency=1` du lab. Le texte d'origine anticipait ce point comme bloquant ; la mesure a montré
qu'il ne l'était pas, avec la même discrétisation (arrondi au m/s) déjà envisagée.

**Restructuration de `building_solver.py`** : `h_e` (vent, varie par HEURE) et `h_i` (orientation,
varie par TRIANGLE mais jamais dans le temps) ne peuvent plus être bakés une fois pour toutes dans
`_build_triangle_systems` comme avant (Lots F→Q) — désormais construite « nue » (juste K/C par unité
de surface du mur, cache purement `(paroi_model_id, dx_max)`). `_assemble_global_kc` pose maintenant
`h_i_list` (une valeur par triangle) et retourne en plus `K_e_pattern` : la sensibilité de K à `h_e`
est **linéaire** (`+area_i` sur la diagonale du nœud extérieur de chaque triangle), donc
`K_global(h_e) = K_global_base + h_e * K_e_pattern` — pas besoin de reconstruire toute la matrice par
valeur de `h_e`, juste une addition creuse bon marché. `_factorize_for_g_vent` généralisé en
`_factorize_for(..., g_vent, h_e)`, appelé **paresseusement** (au premier usage, mis en cache) pour
chaque combinaison `(g_vent, h_e)` RÉELLEMENT rencontrée pendant le run plutôt que le produit
cartésien complet à l'avance — optimisation naturelle une fois la structure en place.
`MAX_DISTINCT_BOUNDARY_COMBOS = 2000` en garde-fou (marge ~6x au-delà du pire cas mesuré).

**h_e dynamique** (`payload.h_e_dynamic`, optionnel, défaut False) : dérive `h_e` heure par heure de
`weather[h].wind_m_s` (nouveau champ optionnel, `BuildingWeatherPointSerializer`) via
`h_e_from_wind` — vent arrondi au m/s AVANT la formule (c'est cet arrondi, pas la formule
elle-même, qui borne le nombre de valeurs distinctes). Si activé, chaque point météo DOIT porter
`wind_m_s` (validé côté serializer ET défensivement dans `run_building_simulation`, appelable
directement). `h_e` (constante) reste requis dans le payload mais simplement ignoré si actif — pas
d'exigence conditionnelle pour un gain marginal.

**h_i par orientation** (`payload.interior.h_i_auto`, optionnel, défaut False) : dérive `h_i` de
`tilt_deg` (déjà calculé par `api.geometry`) via `h_i_from_tilt` — classification ISO 6946 par TYPE
d'élément (murs tilt∈]60°,120°[ → 7,7 ; toiture/plafond tilt≤60° → 10,0, flux montant ; sol/plancher
tilt≥120° → 5,9, flux descendant), pas par le signe instantané du flux à un instant donné (comme la
norme elle-même) — cohérent avec le fait que `h_i` n'a jamais varié dans le temps dans ce solveur,
seulement, désormais, d'un triangle à l'autre. `h_i` (constante) devient optionnel si `h_i_auto` est
actif (sinon requis comme avant).

**Météo** : `weather_source.py` demande désormais `wind_speed_10m` (Open-Meteo, avec
`wind_speed_unit=ms` explicite — le défaut est km/h) et lit `WS10m` (PVGIS TMY, déjà nativement en
m/s) — vérifié en réel que les deux sources sont bien normalisées en m/s sans conversion
supplémentaire nécessaire. Une heure sans vent (bord de couverture) n'annule PAS l'heure entière
(contrairement à t_ext/e_dir/e_dif) : `h_e_dynamic` devient simplement inutilisable pour cette heure
précise si le payload l'exige, sans dégrader le reste du bilan.

**Frontend** (`Calcul3DComponent`) : case à cocher « h_e dynamique » (désactive le champ constant) et
« h_i par orientation » (idem), symétriques. CSV météo étendu à une 6e colonne optionnelle
(`vent_m_s`) — déjà remplie automatiquement par le fetch Lot L/S (le backend renvoie maintenant
`wind_m_s` par point), modifiable à la main sinon. Garde-fou côté UI (`weatherMissingWind`) qui
bloque le bouton de lancement tant qu'une heure manque de vent alors que le mode dynamique est actif,
plutôt que de laisser échouer la requête.

**Tests** (`backend/api/tests.py`, `DynamicConvectionTest` + `BuildingCalculRequestSerializerWindTest`,
14 nouveaux) : formules pures (`h_e_from_wind`, `h_i_from_tilt`, bornes de bucket incluses/exclues),
équivalence dynamique-avec-vent-constant ≡ constant-avec-h_e-manuel (oracle direct), arrondi du vent
avant Jürges, validation (vent manquant → erreur), non-régression (comportement par défaut inchangé
sans les deux nouveaux flags). **Piège identifié en écrivant les tests, avant tout bug réel — le plus
instructif du lot** : une identité de conservation d'énergie (même famille que Lots F/Q) est
**tautologique vis-à-vis de K** — elle re-dérive la ligne du nœud d'air du système linéaire
RÉELLEMENT résolu, quel que soit le contenu de K, donc elle est satisfaite par construction même si
un bundle est réutilisé à tort pour le mauvais `h_e` (confirmé par mutation : retirer `h_e` de la clé
de cache du bundle laisse ce test intact). Remplacé par un test comparant à un oracle **indépendant du
système résolu** (formule U(h_e) en régime permanent, méthode de `WallSteadyStateTest`) : vent
constant sur une longue phase, puis vent constant DIFFÉRENT sur une seconde phase assez longue pour
reconverger — le flux final doit correspondre au NOUVEAU h_e. Ce test-ci détecte bien la mutation
(vérifié). Mutation testing complet : formule de Jürges (signe), buckets `h_i_from_tilt` (permutés),
arrondi du vent (retiré), clé de cache du bundle (h_e retiré) — les 4 confirmées détectées après
correction, aucune non détectée en survivant. 70/70 tests, `manage.py check` propre.

**Vérifié en réel de bout en bout sur l'image rebuildée** : fetch réseau réel Open-Meteo (Paris, vent
présent sur 100% des points) → validation `BuildingCalculRequestSerializer` avec `h_e_dynamic` ET
`h_i_auto` actifs simultanément → simulation réelle (boîte 8×6×3, maillage réaliste, mode thermostat)
aboutissant à un résultat physique cohérent. `ng build` propre.

### Reste ouvert
Orientation de la façade par rapport à la direction du vent (face au vent / sous le vent) non
modélisée — une seule valeur de `h_e` pour tout le bâtiment à une heure donnée, simplification
assumée dès le cadrage, pas un oubli. Non vérifié en navigateur réel (même réserve que L/S/Q/N/T).

---

## Lot S — Années type (TMY) ✅ livré le 2026-08-07

### Ce qui a été fait
**PVGIS TMY** (`re.jrc.ec.europa.eu/api/v5_2/tmy`, JRC — Commission européenne, gratuite, sans clé)
ajouté à `backend/api/weather_source.py` (créé au Lot L) : `fetch_pvgis_tmy` + `_assemble_tmy_series`
(partie pure, testable sans réseau) + `build_tmy_series` (sans repli) + `build_tmy_or_fallback_series`
(point d'entrée principal, avec repli). `Gb(n)`/`Gd(h)` de la réponse PVGIS sont EXACTEMENT les mêmes
grandeurs physiques que `direct_normal_irradiance`/`diffuse_radiation` d'Open-Meteo (irradiance directe
normale au rayon / diffuse horizontale) → `e_dir`/`e_dif` directement. La position solaire (module déjà
écrit au Lot L) s'applique telle quelle : refactor `_enrich_hour`, extrait de
`_assemble_weather_series`, partagé par les deux sources — seul le parsing du format brut change d'une
source à l'autre (Open-Meteo : tableaux parallèles + timestamp ISO ; PVGIS : un tableau de dicts par
heure + timestamp `'YYYYMMDD:HHMM'`, `_parse_pvgis_timestamp`).

**Couverture PVGIS vérifiée en réel** (pas seulement documentée) : Paris/New York/Australie centrale
couverts (SARAH2/NSRDB/ERA5 selon la zone, confirmant la note d'origine) ; Pacifique, Svalbard (arctique)
et pôle Sud renvoient tous le **même message générique** `"Location over the sea"` malgré le fait que
Svalbard/pôle Sud soient des terres — PVGIS ne distingue pas "vraiment en mer" de "zone polaire non
couverte" dans son message d'erreur, `fetch_pvgis_tmy` ne cherche donc pas à interpréter ce message,
il déclenche simplement le repli sur toute erreur PVGIS (coverage ou transitoire). Pas de bbox de
couverture précalculée façon `geodata.is_in_france()` : PVGIS renvoie lui-même clairement si la zone
est couverte, un test try/except suffit, plus simple que le Lot L ne l'anticipait.

**Choix explicite côté UI** : `WeatherFetchRequestSerializer.source` (`'archive'` par défaut = Lot L
inchangé, `'tmy'` = Lot S) ; toggle sur la page Calcul 3D entre « Année type (PVGIS TMY) » et « Année
réelle datée (Open-Meteo) », avec les dates existantes réutilisées comme **repli uniquement** en mode
TMY (pas de champ dupliqué, pas de validation conditionnelle côté serializer — toujours requises, sans
effet si PVGIS répond). **Source annotée à deux endroits distincts** (to_do.md étape 3) : dans le
panneau météo dès la récupération (`weatherSourceLabel`, y compris le message de repli s'il a eu lieu),
et **séparément** dans le dashboard de résultats (`submittedWeatherSourceLabel`, capturée au moment du
clic « Lancer le calcul » plutôt que relue en direct — si l'utilisateur recharge une autre météo pendant
qu'un calcul tourne, le dashboard doit continuer à refléter la source qui a vraiment produit ce
résultat, pas celle actuellement dans le formulaire).

Tests (`backend/api/tests.py`, 8 nouveaux — `PvgisTimestampTest`, `TmySeriesAssemblyTest`,
`TmyFallbackTest`) : parsing du format de timestamp PVGIS, assemblage synthétique (mêmes vérifications
de clamping/heures manquantes que Lot L, format brut différent), et **branchement du repli mocké**
(`unittest.mock.patch.object` sur `build_tmy_series`/`build_weather_series` — première utilisation de
mock dans ce fichier de tests, pour isoler la logique de branchement du réseau réel, déjà couvert par
la vérification en direct). Mutation testing sur le retour `'pvgis-tmy'` du chemin succès : échec
confirmé, 36/36 après restauration. Vérifié en réel sur l'image rebuildée : Paris en mode TMY → 8760
heures `source: pvgis-tmy` ; océan Pacifique en mode TMY → repli automatique vers Open-Meteo Archive
avec avertissement clair, via le vrai worker Celery/Redis.

### Reste ouvert
`vent_m_s` (Lot R, livré depuis) est désormais lu depuis `WS10m` — vérifié en réel : PVGIS l'exprime
nativement en m/s, comme Open-Meteo avec `wind_speed_unit=ms`, aucune conversion nécessaire (voir la
section du Lot R). Pas de vérification dans un navigateur réel (même réserve qu'au Lot L).

---

## Lot T — Mode simplifié pour un bâtiment existant (recherche + taux de vitrage, pédagogique) ✅ livré le 2026-08-08

### Ce qui a été fait
Spécifié par l'utilisateur en 4 étapes (2026-08-07), implémenté le lendemain. Les deux décisions
« à trancher avant de coder » ont été résolues sans nécessiter de cadrage utilisateur :

- **Regroupement par mur** : `trimesh.creation.extrude_polygon` a en réalité un ordre de faces
  parfaitement déterministe (vérifié empiriquement sur un rectangle N=4 et un polygone en L non
  convexe N=6), même si non documenté par trimesh — pour une empreinte à N sommets, les N-2
  premières faces triangulent le sol, les N-2 suivantes le toit, puis exactement 2 triangles par
  arête dans l'ordre des arêtes. `geodata.extrude_footprint_grouped` exploite ce fait directement
  (avec une vérification de structure qui échoue bruyamment si jamais trimesh changeait de
  comportement) — pas besoin d'extruder côté par côté à la main comme l'étape 2 d'origine
  l'envisageait.
- **Distinction vitrage/opaque** : `ParoiModel.is_glazing` (booléen explicite, migration `0010`)
  plutôt qu'une heuristique sur les couches, comme pressenti.

**Découverte en réel non anticipée par le cadrage d'origine** : une empreinte IGN en zone urbaine
dense peut avoir des centaines de sommets (515 murs observés en plein Paris — un pâté de maisons
digitalisé comme un seul bâtiment complexe), inutilisable pour une configuration paroi par paroi.
`MAX_WALLS_SIMPLIFIED_MODE = 30` : `geodata.search_nearby_buildings` écarte silencieusement les
candidats trop complexes (comptés dans `n_skipped_too_complex`, affiché à l'utilisateur) plutôt que
de produire une UI à des centaines de menus déroulants — une zone pavillonnaire donne typiquement
4-26 murs, largement dans la limite.

**Deuxième découverte en réel, plus structurante** : `geometry.refine_envelope` (Lot raffinement de
maillage, existant) propage la subdivision à tout le maillage connecté par arêtes partagées — un
volume extrudé étanche (murs soudés au sol et au toit) ne permet donc PAS de raffiner finement les
murs sans que ça cascade vers le sol/la toiture. Un premier essai à 0,6 m de finesse a produit
16 384 triangles pour un petit pavillon à 5 murs (bien au-delà de ce que le solveur peut absorber
une fois chaque triangle maillé en profondeur — `MAX_TOTAL_DOF` aurait été dépassé). Testé
empiriquement plusieurs valeurs : 2,0 m donne ~128 triangles/mur (1024 au total pour ce même
bâtiment), largement assez fin pour un taux de vitrage à quelques % près, avec une marge
confortable — retenu comme défaut. Pas de raffinement séparé par groupe (aurait nécessité de
désolidariser les groupes en dupliquant les sommets aux jonctions, hors scope de cette V1).

**Étape météo simplifiée par réutilisation** : plutôt que d'intégrer un panneau météo dans
l'assistant, la dernière étape redirige vers la page Calcul 3D — celle-ci pré-remplit déjà
automatiquement `weatherFetchLat/Lon/NorthOffset` depuis `Building.georef_lat/lon/north_offset_deg`
(mécanisme du Lot L), donc un bâtiment créé par ce mode simplifié a sa météo déjà prête à récupérer
sans code supplémentaire — il suffit à l'utilisateur de choisir l'année.

**Entrée simplifiée ajoutée après coup (même jour, sur demande utilisateur) : renouvellement d'air.**
Même schéma de préremplissage que la météo, généralisé : `Building.suggested_debit_vent_m3h`/
`suggested_eta_recup_vent` (migration `0011`, nouveaux champs nullable, jamais lus par le solveur —
une SUGGESTION uniquement) sont calculés à l'étape 2 de l'assistant à partir d'un profil du catalogue
existant `ventilation-profiles.ts` (déjà utilisé par Calcul 3D, Lot G) appliqué au volume RÉEL du
bâtiment — empreinte (aire des triangles du groupe `sol`) × hauteur, calculé côté client depuis la
géométrie déjà récupérée, plus précis que la saisie manuelle du volume sur Calcul 3D. Repris par
`Calcul3DComponent.onBuildingChange` au chargement du bâtiment pour préremplir `debitVentM3h`/
`etaRecupVent`, exactement comme `georef_lat/lon` préremplit déjà le panneau météo — les deux champs
restent modifiables ensuite sur Calcul 3D comme pour tout bâtiment. Optionnel : aucun profil choisi
⇒ aucune suggestion envoyée, comportement inchangé pour un bâtiment créé sans passer par ce mode.
Vérifié en réel (round-trip serializer + bornes de validation, dans le conteneur rebuildé) plutôt
qu'ajouté aux tests automatisés du module `tests.py` — ce fichier est explicitement sans accès DB
(`SimpleTestCase` partout, voir son docstring) et ces deux champs sont de purs passe-plats
(`FloatField` sans logique propre) comme `georef_lat`/`surface_ref_m2`, qui n'ont eux non plus jamais
eu de test dédié.

**Backend** : `geodata.extrude_footprint_grouped` (triangles `{'v','group','boundary'}`, sol marqué
`boundary='ground'` — Lot K — puisqu'un plancher bas issu d'une empreinte réelle touche le terrain
par construction) + `geodata.search_nearby_buildings` (recherche, tri par distance existant réutilisé,
extrusion de chaque candidat au moment de la recherche pour éviter un second aller-retour réseau une
fois choisi) ; endpoint autonome et **synchrone** `POST /api/batiments/rechercher/`
(`SearchNearbyBuildingsView`/`SearchNearbyBuildingsRequestSerializer`, rayon plafonné à 150 m — pas
de Celery, contrairement à la génération d'environnement, le rayon volontairement petit rend l'appel
assez rapide). **Aucun nouvel endpoint de création** : le mesh extrudé d'un candidat est directement
consommable par `POST /api/batiments/` existant (`BuildingSerializer` accepte déjà `group`/`boundary`
par triangle) — vérifié en réel.

**Frontend** : nouvelle page `/mode-simplifie` (assistant à 4 étapes, lazy-loaded comme
Bâtiment/Environnement/Calcul 3D), lien de nav Foyer ajouté. Étape 3 (assignation proportionnelle)
entièrement côté client : pour chaque paroi configurée, tous les triangles reçoivent le modèle
opaque, puis un triangle sur N (N = arrondi de 1/taux) est réassigné au modèle vitrage — motif
régulier plutôt qu'aléatoire, comme décidé. Aperçu 3D coloré (vitrage/opaque/non assigné) via le
`mesh-viewer` déjà utilisé ailleurs.

**Tests** (`backend/api/tests.py`, 8 nouveaux — `ExtrudeFootprintGroupedTest`,
`SearchNearbyBuildingsSerializerTest`) : structure de faces figée (rectangle + polygone en L),
oracle indépendant du comptage de groupes (sol/toiture aux bonnes élévations z), intégration avec
`compute_envelope_geometry`, validation du serializer de recherche. `search_nearby_buildings`
lui-même (réseau) vérifié uniquement en réel, comme les autres fonctions réseau de `geodata.py`
(même réserve que Lot F pour ce module). Mutation testing sur l'arithmétique d'indices de faces :
échec confirmé. **Pipeline complet vérifié en réel de bout en bout sur l'image rebuildée** :
recherche (bâtiment réel, zone pavillonnaire) → création → subdivision (1024 triangles) →
assignation proportionnelle (130 triangles vitrage / 1024, ≈12,7 % pondéré sol+toiture+murs
inclus) → sauvegarde → **calcul thermique réel réussi** sur le bâtiment ainsi produit.
`manage.py test` → 56/56, `manage.py check` → propre, `ng build --configuration production` →
propre.

### Reste ouvert
Génération automatique d'un environnement voisin (Lot C existant, `generate_environment_for_building`)
non incluse dans l'assistant — l'utilisateur peut l'ajouter ensuite depuis la page Bâtiment, le
mécanisme existe déjà entièrement. Toiture non plane (pans, faîtage) hors scope — toiture plate,
cohérent avec `extrude_footprint`/`extrude_footprint_grouped`. Pas de raffinement différencié par
groupe (voir découverte ci-dessus) — un bâtiment avec une très grande façade et une très petite
pourrait avoir une précision de taux de vitrage inégale entre les deux à finesse de subdivision
égale. Pas de vérification dans un navigateur réel (même réserve que Lots L/S).

---

## Lot U — Correctif : rayonnement transmis intégralement à travers un vitrage ✅ livré le 2026-08-08

### Constat
Signalé par l'utilisateur : ses tests 1D sur des fenêtres montraient les nœuds du vitrage chauffant
toujours plus vite que l'air intérieur, même en réduisant `c_air_int` — alors que la lumière qui
traverse une fenêtre devrait au contraire chauffer l'air directement, donc plus vite que le vitrage
à `c_air_int` faible.

### Ce qui a été fait
Bug réel trouvé dans `solver._propagate_solar` (module partagé, réutilisé tel quel par
`building_solver.py` — **identique en 1D et 3D**) : à chaque couche translucide (τ &gt; 0), une
fraction `alpha·e_inc` est absorbée (chauffe la couche) et le reliquat `tau·e_inc` continue vers la
couche suivante. Le cas déjà géré et documenté (page Théorie, section 05) est le **mur Trombe** — une
couche translucide toujours suivie d'une couche opaque qui arrête tout et absorbe le reliquat. Mais
**rien ne gérait le cas où TOUTES les couches sont translucides** (pas de couche opaque en fond) :
à la fin de la boucle, le reliquat qui a traversé la DERNIÈRE couche était silencieusement perdu — ni
sur un nœud de la paroi, ni sur le nœud d'air. Or c'est exactement la structure de **tout le
catalogue de vitrages actuel** (`VITRAGE_SIMPLE` : 1 couche τ=0,87 ; `VITRAGE_DOUBLE` : 3 couches
τ=0,88/0,97/0,88) — pour ces valeurs, 87 % (simple) à 75 % (double) du rayonnement solaire incident
disparaissait purement et simplement du bilan, sur tout calcul avec vitrage fait jusqu'ici. Les nœuds
du vitrage chauffaient bien un peu (la petite fraction absorbée, ~6 % par vitre), mais l'air ne
recevait AUCUN gain solaire direct — seulement une conduction lente via `h_i`, insensible à
`c_air_int` puisqu'aucun grand terme ne l'alimente : exactement le symptôme rapporté.

**Correctif** : `_propagate_solar` détecte désormais (via `for...else` sur la boucle des couches — le
`else` ne s'exécute que si aucun `break` n'a eu lieu, donc jamais de couche opaque rencontrée) le cas
où le rayonnement a traversé toute la paroi sans être arrêté, et retourne une source supplémentaire
`('interior', None, énergie_W_par_m2)`. `solver._assemble_F` (1D) et `building_solver._assemble_F_hour`
(3D) l'ajoutent directement à `F[air_idx]` — même convention que le renouvellement d'air/apports
internes déjà en place (Lots G/H) : actif en cas 2 (nœud d'air libre), sans effet en cas 1 (T_int
imposée, 1D n'a même pas de nœud d'air dans ce mode) ni en mode `'imposed'` 3D (Dirichlet absorbe le
terme, comme pour `g_vent`/`apports_internes_w`). Non-régression stricte du mur Trombe (couche
translucide + couche opaque) et du mur opaque simple, vérifiée explicitement par test.

**Tests** (`backend/api/tests.py`, `TransmittedSolarGainTest`, 8 nouveaux) : formules pures (oracle
= produit des τ des couches successives, calculé à la main, indépendant de `_propagate_solar`),
non-régression mur opaque/mur Trombe, intégration 1D et 3D (identité de conservation d'énergie
étendue au nouveau terme — légitime ici, contrairement au piège du Lot R : le bug visé est dans **F**,
pas dans **K**, donc l'identité n'est pas tautologique, elle re-dérive `e_interior` à la main plutôt
que d'appeler `_propagate_solar`), et un test reproduisant directement le symptôme rapporté
(`c_air_int` faible → l'air chauffe désormais plus vite que le vitrage). Mutation testing sur les
trois points de correction (le `for...else` lui-même, le consommateur 1D, le consommateur 3D) : les
trois confirmées détectées, exactement les 5 tests concernés échouant à chaque fois (les 3 tests de
non-régression restant verts, comme attendu). 78/78 tests au total, `manage.py check` propre.

**Documentation** : page Théorie (section 05) complétée avec un troisième paragraphe couvrant ce cas
« aucune couche opaque », symétrique au terme de renouvellement d'air déjà documenté.

Vérifié en réel dans le conteneur (avant et après le correctif) avec les valeurs réelles du
catalogue : `c_air_int` faible → l'air atteint désormais 137°C après quelques heures pendant que le
vitrage reste à ~44°C, conforme à l'intuition physique de l'utilisateur.

### Reste ouvert
Pas de vérification en navigateur réel (même réserve que les autres lots récents).

---

## Lot V — Calendrier d'occupation (mode thermostat) ✅ livré le 2026-08-09

### Constat
Le mode thermostat (Lot précédent à celui-ci dans la conversation, pas un lot numéroté) n'avait
que deux constantes `t_min`/`t_max` pour tout le run — aucun moyen d'exprimer qu'un bâtiment
scolaire tombe en hors-gel pendant les vacances et le week-end, qu'un bureau se réduit la nuit, ou
qu'un logement n'a pas cette notion de fermeture. L'utilisateur a demandé de définir ces plages par
jour et par heure, avec quatre profils d'usage détaillés (scolaire, scolaire climatisé, tertiaire,
tertiaire climatisé) et deux profils supplémentaires proposés pour l'habitation.

**Cadrage** : la question clé, soulevée par l'utilisateur avant tout code, était de savoir si les
occultations mobiles (Lot J) avaient déjà établi un précédent d'équation-par-configuration pour
éviter de réinverser la factorisation heure par heure — non applicable ici puisque `t_min`/`t_max`
n'entrent **jamais dans K** (seulement dans la décision de pincement du second membre F, comme
`g_vent`/`apports_internes_w`), donc aucun souci de factorisation à anticiper, contrairement au
calque de Lot J. Un aller-retour de cadrage a corrigé ma première lecture du besoin : le hors-gel
« vacances » est un concept **propre au scolaire** (jamais au tertiaire, climatisé ou non) et
s'applique **également aux deux variantes scolaires** — confirmé explicitement par l'utilisateur
avant implémentation. Sur un jour classé hors-gel ou week-end entier, la consigne résultante
remplace les 24 heures de ce jour (pas de sous-distinction jour/nuit ce jour-là) ; le cycle
jour/nuit (19°C/16°C, frontière 7h-19h — même convention que l'exemple de planning du Lot Q) ne
s'applique que sur les jours normaux — confirmé également.

### Ce qui a été fait
**Backend** (`building_solver.py`) : chaque point météo peut désormais porter `t_min`/`t_max`
optionnels (`BuildingWeatherPointSerializer`, `FloatField(required=False, allow_null=True,
default=None)` — même modèle que `wind_m_s` du Lot R). En mode thermostat, l'heure courante utilise
ces valeurs si fournies, sinon replie sur les constantes du run (`interior.t_min`/`t_max`). Piège
DRF retrouvé et corrigé **avant** tout bug utilisateur (vérifié empiriquement en shell Django avant
d'écrire le code définitif) : `default=None` rend la clé **toujours présente** dans
`validated_data` avec la valeur `None` quand rien n'est fourni — un `point.get('t_min',
repli)` ne retombe alors **jamais** sur le repli (`.get()` ne consulte le défaut que si la clé est
absente, pas si sa valeur est `None`), ce qui aurait fait planter tout calcul thermostat en
production dès qu'un point réel passait par le serializer (`t_air < None` → `TypeError`). Corrigé
avec un test explicite (`is not None`), même convention déjà en place pour `wind_m_s` (Lot R).
Aucun changement de factorisation requis (contrairement à g_vent/h_e/volets_fermes) — confirmé par
le cadrage puis par l'implémentation elle-même : `t_min`/`t_max` ne touchent que la décision de
pincement du second membre, jamais `K_global`.

**Frontend** — nouveau catalogue `core/usage-profiles.ts` (résolu entièrement côté client, même
patron que `ventilation-profiles.ts`/`shading-profiles.ts` : le backend ne reçoit que deux nombres
optionnels par heure, jamais d'identifiant de profil ni de date calendaire). Six profils
(`UsageProfile[]`) : `scolaire`/`scolaire-clim` (hors-gel 7°C vacances+week-end, réduit 16°C la
nuit, normal 19°C/100°C ou 19°C/26°C le jour), `tertiaire`/`tertiaire-clim` (hors-gel week-end
seulement, jamais lié aux vacances), `habitation`/`habitation-clim` (aucune notion de fermeture,
seul le cycle jour/nuit 19°C/16°C compte). `OccupationCalendar` (jour de la semaine du premier
point météo, 7 booléens jours ouvrés, liste de plages de vacances en **indice de jour relatif au
début du run** — jamais de vraie date, cohérent avec le fait qu'aucun concept de date calendaire
n'existe ailleurs dans le solveur, seulement des séquences horaires pures, valable aussi bien pour
des données Open-Meteo Archive datées que pour un TMY composite non daté). `computeThermostatSetpoints()`
dérive un tableau `{t_min, t_max}[]` de même longueur que la météo. Vérifié par un script Node.js
autonome (arithmétique jour/semaine/vacances) avant intégration, puis par un script Python miroir
exécuté dans le conteneur backend contre le vrai solveur (`building_solver.run_building_simulation`)
sur un scénario 4 jours incluant un jour de vacances : le jour hors-gel reste sous 19°C toute la
journée (jamais pincé à la consigne normale), l'heure de jour normal se pince exactement à 19°C,
l'heure de nuit réduite se pince exactement à 16°C — comportement conforme au cadrage validé.

**UI** (`pages/calcul-3d/`) : section « Calendrier d'occupation (optionnel) », visible uniquement
en mode thermostat — sélecteur de profil, jour de la semaine du premier point météo, cases à cocher
jours ouvrés, liste de plages de vacances (ajout/retrait), bouton « Générer le calendrier ».
`submit()` fusionne les consignes générées dans chaque point météo avant envoi, uniquement si un
calendrier a été généré pour la longueur exacte de la météo actuelle ; toute modification de la
météo invalide le calendrier généré (`thermostatSetpoints.set(null)` dans `parseWeather()`) pour
éviter un calendrier périmé silencieusement réappliqué à une autre série. Aucune nouvelle classe
CSS introduite — réutilise uniquement `field-grid`/`mode-option`/`weather-actions`/`weather-count`/
`hint`/`btn-primary`/`btn-secondary`, déjà présentes dans `calcul-3d.component.scss`.

**Tests** (`backend/api/tests.py`, `ThermostatCalendarTest`, 4 nouveaux) : défaut inchangé sans
calendrier, `None` explicite dans le point météo retombe bien sur la constante du run, une
suspension ponctuelle (une heure hors-gel au milieu d'une série chaude) fait chuter le
`cooling_kwh` par rapport au run constant témoin et laisse l'air dépasser `t_max` pendant l'heure
suspendue puis se re-pincer normalement l'heure suivante, bornes invalides (`t_min >= t_max` pour
une heure donnée) lèvent bien `BuildingSimulationError`. Mutation testing sur le point de correction
(retour au `.get(key, repli)` fautif) : détecté immédiatement (`TypeError`), exactement le test de
retombée sur repli explicite qui échoue. 95/95 tests au total, `manage.py check` propre.

Vérifié en réel dans le conteneur (script Python miroir de la logique `usage-profiles.ts`, profil
`scolaire`, 4 jours dont un jour de vacances) : jour de vacances → air entre 7°C et 16°C toute la
journée (jamais pincé à 19°C), heure de jour normal → pincé exactement à 19°C, heure de nuit
normale → pincé exactement à 16°C.

### Reste ouvert
Pas de vérification en navigateur réel (même réserve que les autres lots récents). Les six profils
sont des valeurs indicatives usuelles (GTB/GTC, ADEME), pas une table réglementaire officielle —
même statut que le catalogue de parois.

---

# Phase 3 — analyse de relecture + demandes utilisateur (2026-08-09)

Relecture complète de l'application demandée par l'utilisateur une fois la phase 2 terminée
(`solver.py`, `building_solver.py`, `shadow.py`, `geometry.py`, `geodata.py`, `weather_source.py`,
`serializers.py`, `views.py`, `tasks.py`, `seed_paroi_catalogue.py`, et tout le parcours Angular
Mode simplifié → Bâtiment → Environnement → Calcul 3D), en trois volets : **ce qui mérite d'être
souligné**, **ce qui manque à la page Théorie côté limites**, **les erreurs physiques ou
méthodologiques réelles** (au-delà des simplifications déjà assumées). S'y ajoutent quatre demandes
d'évolution et un bug bloquant signalés par l'utilisateur.

Les sources de données externes citées plus bas (couches IGN, tags OSM, API d'altimétrie) ont
toutes été **vérifiées par appel réel** pendant cette analyse, jamais reprises de mémoire — voir
les résultats chiffrés dans chaque lot concerné.

---

## A — Ce qu'il faut souligner de l'analyse

**A1. L'architecture tient.** Le pari de départ (« un triangle = une paroi 1D, toutes couplées à un
nœud d'air unique ») a absorbé sans refonte : le renouvellement d'air, les apports internes, le
cadre de fenêtre, le contact au sol, les occultations mobiles, la convection dynamique, les
plannings horaires et le calendrier d'occupation. Chacun s'est ajouté comme un terme sur `K` ou sur
`F`, jamais comme une réécriture. Le seul point dur récurrent (des paramètres qui entrent dans `K`
et non seulement dans `F`) a été traité une bonne fois par la factorisation-par-combinaison-distincte
mise en cache paresseusement (`_factorize_for`/`bundles`), qui a ensuite servi telle quelle aux Lots
R et J. C'est le point le plus solide du code.

**A2. La séparation « modules purs » est réellement respectée.** `solver.py`, `building_solver.py`,
`geometry.py`, `shadow.py`, `geodata.py`, `weather_source.py` ne dépendent d'aucun modèle Django et
sont testables sans base ni réseau (les parties réseau sont systématiquement isolées de leur partie
assemblage — `fetch_*` vs `_assemble_*`). C'est ce qui rend les 91 tests possibles et rapides.
À préserver dans tout lot de la phase 3.

**A3. Le partage de `_propagate_solar` entre 1D et 3D est un multiplicateur, dans les deux sens.**
Le Lot U l'a montré : un seul bug dans cette fonction faussait *simultanément* les deux solveurs, et
un seul correctif a réparé les deux. C'est un bon design, mais il implique que **toute modification
de ce module doit être considérée comme touchant les deux solveurs**, jamais « juste le 1D ».

**A4. Les garde-fous de charge sont cohérents entre eux** (`MAX_NODES`, `MAX_TOTAL_DOF`,
`MAX_TRIANGLES`, `MAX_WEATHER_POINTS`, `MAX_REALTIME_RAYCAST_OPS`, `MAX_DISTINCT_BOUNDARY_COMBOS`,
mutex lab-wide sur `Job`) et chacun échoue avec un message qui dit quoi faire. Sur un hôte 2 vCPU /
16 Go partagé par tout le lab, c'est ce qui empêche un run pathologique de bloquer les autres apps.

**A5. En revanche, la couverture « chemin réel » est asymétrique.** Le backend est testé finement
(91 tests, plusieurs vérifiés par mutation testing) ; le frontend ne l'est pas du tout, et **aucun
lot de la phase 2 n'a été vérifié dans un navigateur réel** — chaque section « Reste ouvert » le
répète. Le bug 2.2.1 signalé par l'utilisateur (voir Lot W) en est la conséquence directe et
mesurable : le mode simplifié n'a jamais pu dépasser sa première étape depuis sa livraison, alors
que ses 56 tests backend passaient. **Toute la phase 3 doit se terminer par une vérification en
navigateur, pas par un `ng build` propre.**

**A6. Le géoréférencement est devenu le pivot silencieux de l'application.** `georef_lat/lon`
conditionne la météo réelle (position solaire), la génération d'environnement et l'alignement des
obstacles ; `georef_north_offset_deg` conditionne l'orientation de toutes les façades ;
`georef_ground_z` conditionne l'altitude des obstacles IGN. Les trois sont des champs de formulaire
optionnels sans aide à la saisie, et deux d'entre eux (offset nord, altitude sol) n'ont **aucun**
retour visuel permettant de détecter une erreur. Une erreur de 90° sur l'offset nord fausse tout le
bilan solaire sans qu'aucun garde-fou ne se déclenche. À traiter (Lot AA pour `georef_ground_z`, à
part pour l'offset nord).

---

## B — Limites : ce qui manque à la page Théorie

La page Théorie (section 01 « Portée et hypothèses », encadré « Hors périmètre », et section 12
« Extensions possibles ») décrit correctement le modèle **tel qu'il était à la fin du Lot E**. Elle
n'a été retouchée depuis que pour le Lot U (section 05). Deux problèmes distincts :

### B1 — Deux affirmations sont devenues fausses

- Section 01 : « Hypothèse assumée : une valeur **constante sur toute la durée du calcul** [pour les
  apports internes] […] un profil horaire est une extension possible (section 12) ». **Le Lot Q a
  livré ce profil horaire** (`planning`, 24 entrées). Le texte décrit donc une limite qui n'existe
  plus.
- Section 12 : « **Couplage multizone** — en cas 2, le nœud d'air intérieur ne voit ici qu'une seule
  paroi ; un bâtiment réel somme les contributions `hᵢ·(Tₙ − T_air,int)` de chaque paroi du local sur
  la même ligne du système ». C'est exactement ce que fait `_assemble_global_kc` depuis le Lot D. Ce
  qui reste une vraie limite, c'est le **mono-zone** (un seul nœud d'air pour tout le bâtiment), pas
  la sommation multi-parois — le paragraphe désigne la mauvaise limite.

### B2 — Onze limites réelles ne sont écrites nulle part

Aucune n'est un bug : ce sont des simplifications légitimes, mais **non déclarées**, donc invisibles
pour quiconque lit la page Théorie pour savoir ce que vaut un résultat. À ajouter (voir Lot AC pour
la rédaction) :

1. **`h_e` est un coefficient global convection + rayonnement.** La corrélation de Jürges
   (5,8 + 3,94·v) inclut l'échange radiatif grande longueur d'onde avec l'environnement supposé à
   `t_ext`. Conséquence concrète, jamais dite : **une surface ne peut jamais descendre sous la
   température de l'air**. Le refroidissement radiatif nocturne par ciel clair (une toiture réelle
   descend 3 à 6 K sous `t_ext`) est structurellement impossible dans ce modèle — ce qui sous-estime
   les déperditions nocturnes et surestime la surchauffe d'été.
2. **`h_e` est identique pour toutes les façades**, quelle que soit leur orientation par rapport à la
   direction du vent (déjà écrit dans le code, jamais sur la page). Face au vent / sous le vent, un
   écart d'un facteur 2 sur `h_e` est courant.
3. **Les propriétés optiques τ/r/α sont à incidence normale et constantes**. Sur un vitrage réel, τ
   s'effondre au-delà de ~60° d'incidence (≈ 0,87 à 0° → ≈ 0,4 à 75°). Le modèle applique 0,87 à
   toutes les incidences : **surestimation des apports solaires par les vitrages**, maximale en
   façade est/ouest le matin/soir et en mi-saison, exactement là où le confort d'été se joue.
4. **Le solaire transmis (Lot U) chauffe l'air instantanément et intégralement.** Dans un local réel,
   il frappe d'abord le sol et les parois intérieures, y est absorbé, stocké, puis restitué avec un
   déphasage de plusieurs heures. Ici il n'y a ni absorption intérieure, ni stockage, ni
   re-rayonnement, ni fraction ressortant par la fenêtre. C'est la simplification la plus lourde de
   conséquence sur la dynamique jour/nuit d'un bâtiment vitré.
5. **L'inertie intérieure est une capacité pure sans résistance.** `c_air_int` est censé représenter
   « air + mobilier » (et, en pratique, cloisons et planchers intermédiaires — c'est le seul endroit
   possible). Or elle est branchée directement sur le nœud d'air, sans surface ni résistance
   superficielle : cette masse réagit **instantanément**. Une inertie réelle est séparée de l'air par
   1/hᵢ puis diffuse lentement. Le modèle surestime donc l'amortissement rapide et sous-estime le
   déphasage.
6. **Aucune inertie ni résistance de sol sous un plancher `boundary='ground'`.** Voir C4 — c'est à la
   fois une limite à déclarer et une erreur à corriger.
7. **Le diffus est isotrope.** Pas de renforcement circumsolaire ni de bande d'horizon (modèles de
   Hay/Perez). Écart usuel de 5 à 15 % sur l'irradiation reçue par une façade verticale.
8. **Les obstacles sont parfaitement noirs.** Le facteur de vue du ciel occulté (`compute_sky_view_factors`)
   *retire* la fraction de ciel masquée sans que l'obstacle ne réémette ni ne réfléchisse quoi que ce
   soit. Combiné à l'albédo du sol absent (déjà déclaré), le modèle **sous-estime systématiquement les
   apports diffus en milieu dense** — précisément le cas d'usage de la génération automatique
   d'environnement.
9. **L'ombrage précalculé est discrétisé à 15°** en azimut et en élévation (`lookup_visibility` prend
   la case la plus proche). Un obstacle fin ou un bord de bâtiment fait basculer une heure entière de
   « ombré » à « ensoleillé ». En particulier, tout soleil entre 0° et 7,5° d'élévation est évalué à
   0° — rayon rasant, presque toujours bloqué. Le mode `realtime` existe précisément pour ça, mais
   rien sur la page ne dit quand il devient nécessaire.
10. **Les résultats sont des besoins, pas des consommations.** `heating_kwh`/`cooling_kwh` sont
    l'énergie qu'il faut fournir *à l'air du local*, avec un thermostat idéal (puissance illimitée,
    réaction instantanée, pas de bande morte au-delà de `t_min`/`t_max`). Aucun rendement de
    génération, d'émission, de distribution, ni COP. Le repère « RT2012 ≈ 50 kWh/m²/an » affiché sur
    le dashboard porte déjà cette réserve ; la page Théorie non.
11bis. **Aucune paroi mitoyenne.** Tout triangle est soit face à l'air extérieur, soit au contact du
    sol (`boundary`) : il n'existe aucun troisième cas « paroi partagée avec un bâtiment voisin
    chauffé », qui devrait être adiabatique (ou couplée à une température de local voisin) plutôt
    qu'exposée à `t_ext`. Constaté au Lot X : le filtrage y conserve délibérément les mitoyens comme
    obstacles d'ombrage — mais le mur qu'ils partagent avec le bâtiment étudié continue de déperdre
    comme s'il donnait sur l'extérieur. **Surestime les déperditions d'une maison de ville ou d'un
    appartement**, d'autant plus que la part de surface mitoyenne est grande (jusqu'à deux façades
    sur quatre pour une maison de rangée). Contournement possible aujourd'hui sans changer le
    modèle : affecter à ces triangles un modèle de paroi très isolant. Une vraie prise en charge
    demanderait un troisième `boundary` (voir Lot AC, étape 4).
11. **Aucune physique de l'humidité.** Pas de chaleur latente (ni sur la ventilation, ni sur les
    occupants), pas de condensation, pas de transferts de vapeur dans les parois. Une VMC réelle
    évacue une part notable de son énergie en latent.

À noter aussi, plus mineur : la page Théorie ne mentionne **aucun** des Lots G à V par leur effet
(ventilation, apports internes, cadre, sol, volets, h_e/h_i dynamiques, plannings, calendrier
d'occupation). Un lecteur qui s'en tient à la page ignore que ces termes existent.

---

## C — Erreurs physiques ou méthodologiques réelles

Distinctes de B : ici, le modèle ne fait pas ce qu'il prétend faire, ou le restitue mal.

### C1 — Le bâtiment étudié est chargé comme obstacle de son propre environnement ⚠️

`geodata.generate_environment_mesh` extrude **tous** les bâtiments trouvés dans le rayon, sans
exception. Le bâtiment étudié étant lui-même un bâtiment réel présent dans BD TOPO / OSM, il est
importé une seconde fois dans le maillage d'obstacles — et `shadow.build_occluder_mesh` fusionne
ensuite enveloppe + environnement, donc le solveur voit **deux exemplaires superposés** du même
bâtiment. Pour un bâtiment issu du mode simplifié (créé depuis la même empreinte IGN), c'est un
doublon exact. C'est la réponse à la question 2.1.3 : **non, rien n'est écarté aujourd'hui.** Détail,
conséquences et correctif : **Lot X**.

### C2 — Le dashboard appelle « gains » un flux qui n'inclut pas les gains principaux ⚠️

`envelope_flux_w` (et donc `flux_positive_kwh`/`flux_negative_kwh`, affichés « Flux entrant total
(gains) » / « Flux sortant total (pertes) ») vaut exactement `Σᵢ hᵢ·Aᵢ·(T_surface,i − T_air)` : le
seul canal *surface d'enveloppe → air*. Il **exclut**, alors que tous alimentent le même nœud d'air :

- le **rayonnement solaire transmis par les vitrages** (`F[air_idx] += value·area`, Lot U) — qui peut
  être le premier poste de gain d'un bâtiment vitré, et qui a justement été ajouté pour ne plus
  disparaître du bilan… mais reste invisible du dashboard ;
- les **apports internes** (Lot H/Q) ;
- le **renouvellement d'air** (`g_vent·(T_ext − T_air)`, Lot G/Q) ;
- la **conductance des cadres de fenêtre** (Lot I).

Les tuiles « Besoin chauffage / climatisation » restent justes (le résidu HVAC intègre bien tout).
Ce sont donc les deux tuiles de flux qui sont mal nommées **et** incomplètes. Correctif proposé :
**Lot AB**, avec un vrai bilan par poste plutôt qu'un simple renommage.

### C3 — Dérive du planning et du calendrier d'occupation dès qu'une heure météo manque ⚠️

`weather_source._assemble_weather_series` **saute** toute heure dont une donnée manque
(`n_missing += 1; continue`) — décision volontaire et correcte en soi (ne rien inventer). Mais en
aval, **deux mécanismes indexent le temps par la position dans le tableau** :

- serveur : `slot = (heure_debut + hour_idx) % 24` (`building_solver`) pour la ventilation, les
  apports internes et les volets (Lots Q et J) ;
- client : `computeThermostatSetpoints(profile, calendar, heureDebut, nHours)` (Lot V) pour t_min/t_max,
  qui dérive `hourOfDay = (heureDebut + h) % 24` et `dayIdx = ⌊(heureDebut + h)/24⌋`.

Une seule heure manquante décale donc **définitivement** tout le reste du run d'une heure : la
ventilation nocturne se déclenche à 23 h, les volets ferment une heure trop tôt, le week-end glisse.
Deux heures manquantes réparties dans l'année suffisent à décaler la fin d'année de 2 h. Et c'est
**silencieux** : `n_missing` n'apparaît que dans le message du `Job`. Correctif : **Lot AB**.

### C4 — Le plancher au contact du sol échange comme s'il était en plein vent ⚠️

Un triangle `boundary='ground'` (Lot K) échange avec `t_ground` **à travers `h_e`** — la même
conductance que l'air extérieur, et depuis le Lot R **la même conductance dérivée du vent**. Deux
problèmes distincts :

- **Physiquement absurde depuis le Lot R** : le couplage d'un dallage à la terre se met à osciller
  avec la vitesse du vent à 10 m. Le Lot K avait noté « un h_e distinct pour le sol reste une
  extension possible » ; le Lot R a transformé cette approximation acceptable en incohérence.
- **Quantitativement faux même à `h_e` constant** : `h_e = 25` → R = 0,04 m²·K/W, c'est-à-dire un
  contact quasi parfait avec une source à 12 °C. Un plancher sur terre-plein réel voit s'ajouter la
  résistance du sol lui-même et l'effet de bord (ISO 13370) : l'ordre de grandeur est 0,5 à 3 m²·K/W
  selon la géométrie. Le modèle **surestime donc fortement les déperditions par le sol**, d'autant
  plus que le plancher est bien isolé (la résistance manquante est en série avec celle de la paroi :
  plus la paroi est isolée, plus l'écart relatif est important).

S'y ajoute un manque côté catalogue : **il n'existe aucun modèle de plancher bas / dallage** dans
`seed_paroi_catalogue.py`. Le mode simplifié impose pourtant de choisir un « modèle opaque » pour le
groupe `sol` — l'utilisateur y met nécessairement un mur ou une toiture. Correctif : **Lot AB**.

### C5 — Décalage UTC / heure locale sur toute la temporalité d'usage

`weather_source` demande explicitement `timezone=UTC` à Open-Meteo, et PVGIS fournit `time(UTC)` —
c'est **correct et nécessaire** pour la position solaire. Mais `heure_debut` et `jourDebut` (Lot V)
sont des champs saisis à la main qui valent 0 et « lundi » par défaut, et **rien ne les préremplit
depuis la période effectivement chargée**, que l'application connaît pourtant (`start_date`). Deux
conséquences :

- un profil d'occupation « 7 h – 19 h » s'applique en réalité de 8 h à 20 h en hiver et de 9 h à
  21 h en été, en France ;
- `jourDebut` demande à l'utilisateur de savoir quel jour de la semaine tombait le 1ᵉʳ janvier de
  l'année choisie — sans quoi week-ends et vacances tombent au mauvais endroit.

Ce n'est pas une erreur de code mais une erreur de conception d'interface, avec un effet physique
réel sur le résultat. Correctif : **Lot AB**.

### C6 — Le mode simplifié est bloqué à sa première étape 🔴

Le bug signalé par l'utilisateur (2.2.1). Cause exacte identifiée, une ligne. Voir **Lot W**.

### C7 — Fragilités mineures relevées au passage (à traiter en marge, sans lot dédié)

- `geodata.extrude_footprint`/`extrude_footprint_grouped` : `polygon.buffer(0)` sur une empreinte
  invalide peut retourner un **MultiPolygon**, sur lequel `.exterior` lève `AttributeError` — non
  couvert par le `except GeodataError` de `search_nearby_buildings`, donc 500 au lieu d'un candidat
  ignoré. Prendre la plus grande composante après `buffer(0)`.
- `_first_exterior_ring` ne garde que l'anneau extérieur du premier polygone : les **cours
  intérieures** (trous) sont comblées, et une empreinte multi-parties est tronquée. Acceptable pour de
  l'occultation, faux pour un bâtiment étudié issu du mode simplifié (surface et volume surestimés,
  donc débit de ventilation suggéré surestimé).
- `search_nearby_buildings` trie par **distance au centroïde** de l'empreinte, pas par « empreinte
  contenant le point cliqué » : sur une parcelle allongée ou en angle, le voisin peut sortir premier.
- La lame d'air du double vitrage utilise un λ équivalent (0,094) intégrant convection et
  rayonnement — bon choix en régime permanent, mais son `alpha = 0,02` fait absorber 2 % du
  rayonnement **volumétriquement dans l'air**, alors que cette énergie est en réalité absorbée aux
  faces de verre. Effet négligeable, à noter seulement.

---

## Lot W — Correctif bloquant : le mode simplifié ne dépasse jamais l'étape 1 ✅ livré le 2026-08-09

### Constat (signalé par l'utilisateur, 2.2.1)
« J'ai testé le mode simplifié mais après avoir repéré le bon bâtiment j'ai beau le choisir il ne se
passe rien. »

### Cause exacte
`frontend/src/app/pages/mode-simplifie/mode-simplifie.component.ts` — `selectCandidate(i)` ne fait
que `this.selectedCandidateIndex.set(i)`. Or le signal `step` est initialisé à `'recherche'` et
**aucun code ne le fait jamais passer à `'creation'`** : les deux seuls `step.set()` du composant
sont `'configuration'` (dans `loadRefinedBuilding`, ligne 236) et `'termine'` (dans `save`, ligne
299). Côté template, la section 2 est gardée par
`@if (selectedCandidate && step() !== 'recherche')` (ligne 73) et son formulaire par
`@if (step() === 'creation')` (ligne 81).

Cliquer « Choisir » applique donc uniquement la classe `.active` sur la ligne du candidat — rien
d'autre ne peut se produire. **L'état `'creation'` est inatteignable, donc les étapes 2, 3 et 4 du
mode simplifié n'ont jamais été utilisables dans un navigateur depuis la livraison du Lot T** (le
2026-08-08). Les 56 tests backend du Lot T passaient parce qu'ils testent l'API, pas le parcours ;
la section « Reste ouvert » du Lot T annonçait d'ailleurs « pas de vérification dans un navigateur
réel ». C'est la démonstration la plus nette du point A5.

### Étapes
1. `selectCandidate(i)` doit faire avancer l'étape : `this.selectedCandidateIndex.set(i)` **puis**
   `this.step.set('creation')`. C'est le correctif minimal et suffisant.
2. Proposer un nom de bâtiment par défaut au passage (`buildingName` est vide et le bouton
   « Créer et subdiviser » est `[disabled]` tant qu'il l'est — sinon l'utilisateur voit apparaître
   une étape 2 dont le seul bouton est grisé, ce qui reproduit le même sentiment de « rien ne se
   passe »). Quelque chose comme `Bâtiment ${distance} m — ${source}`, modifiable.
3. Permettre de **revenir** au choix du bâtiment : une fois `step === 'creation'`, la liste des
   candidats disparaît (elle est sous `@if (step() === 'recherche')`) et il n'existe aucun moyen de
   changer d'avis sans recharger la page. Ajouter un bouton « Changer de bâtiment » qui repasse à
   `'recherche'` en conservant `candidates()` (ne pas relancer la recherche réseau).
4. Vérifier au passage que le reste de l'enchaînement fonctionne réellement en navigateur —
   c'est la première fois qu'il sera exécuté : création → `refineBuildingMesh` → rechargement →
   configuration par paroi → `generateAssignment` → aperçu 3D → sauvegarde → étape 4. Les étapes 3
   et 4 n'ont, elles non plus, jamais tourné dans un navigateur.
5. Contrôler en particulier deux points repérés à la lecture, qui ne se manifesteront qu'une fois le
   parcours débloqué :
   - `generateAssignment` répartit le vitrage par `pos % step === 0` avec
     `step = round(1/ratio)` : à 20 % demandé, `step = 5` → exactement 20 % ; mais à 30 %,
     `step = 3` → 33 % ; à 40 %, `step = round(2,5) = 2` → 50 %. L'écart peut atteindre 10 points.
     Afficher le taux **réellement obtenu** à côté du taux demandé plutôt que de laisser croire à
     une valeur exacte (le calcul est déjà disponible : `assignedCount` par groupe).
   - le groupe `sol` se voit imposer un « modèle opaque » choisi dans une liste qui ne contient que
     des murs et des toitures (cf. C4) — à relier au Lot AB, qui ajoute un modèle de plancher bas.

### Portée
Uniquement `mode-simplifie.component.ts/.html`. Aucun changement backend, aucun impact sur les 91
tests existants. À faire **en premier** : c'est un blocage total d'une fonctionnalité livrée, et
c'est aussi la page que l'utilisateur voudra rouvrir pour tester les lots suivants.

### Ce qui a été fait
Les cinq étapes ci-dessus, plus un correctif supplémentaire découvert **grâce à** l'étape 5 une fois
le parcours débloqué (voir plus bas).

1. **`selectCandidate` passe désormais à `'creation'`** — le correctif de fond, une ligne.
2. **Nom proposé par défaut** : `Bâtiment <lat>, <lon>` (coordonnées et non distance/source :
   `Building.name` est UNIQUE côté serveur, deux bâtiments cherchés depuis deux points différents
   doivent pouvoir coexister). Ne réécrit jamais une saisie manuelle — il n'est reposé que si le
   champ est vide ou contient encore le nom proposé pour le candidat précédent.
3. **Bouton « Changer de bâtiment »**, limité à l'étape `'creation'` (au-delà, le `Building` existe
   côté serveur et changer de candidat le laisserait orphelin). Ne relance pas la recherche réseau.
4. `stride` au lieu de `step` comme variable locale dans `generateAssignment` — `step` est le signal
   d'étape de l'assistant, qu'une locale homonyme masquait dans toute la portée. Sans effet
   fonctionnel, mais c'est précisément l'ambiguïté au cœur du bug.
5. **Taux de vitrage réellement obtenu affiché** sous chaque paroi, mesuré **en aire** et non en
   nombre de triangles (deux triangles d'un même groupe n'ont pas la même aire — la cascade de
   raffinement ne descend pas partout au même niveau, cf. Lot T), calculé une fois par génération
   et non à chaque cycle de détection de changement.

### Correctif supplémentaire — la répartition du vitrage elle-même était fausse
L'étape 5 devait « afficher le taux obtenu plutôt que de laisser croire à une valeur exacte ». Une
fois affiché en navigateur, il a montré que l'écart n'était pas l'inconvénient mineur annoncé dans le
texte d'origine de ce lot (« jusqu'à 10 points ») :

- `stride = round(1/ratio)` étant un **entier**, seuls les taux 1/N étaient atteignables. Pire, les
  arrondis rendaient deux consignes différentes indiscernables : **30 % et 40 % donnaient tous deux
  ≈ 33 %** (`1/0,4` vaut exactement 2,5 et `Math.round` arrondit au supérieur — l'estimation « 40 %
  → 50 % » du texte d'origine était fausse, elle supposait un arrondi vers le bas).
- Surtout, **au-delà de 50 % demandés, `stride` tombait à 1 et TOUS les triangles de la paroi
  devenaient du vitrage** : une façade réglée à 75 % ou 95 % devenait 100 % vitrée, sans aucune
  partie opaque. Écart maximal mesuré sur la grille des taux/maillages usuels : **25 points**.

Remplacé par une répartition par **accumulateur** (Bresenham) :
`round((pos+1)·ratio) > round(pos·ratio)`. Elle pose exactement `round(n·ratio)` triangles, également
espacés — donc le comptage le **plus proche** du taux demandé pour un maillage donné (`round` et non
`floor` : sur 16 triangles, 10 % donne 2 triangles = 12,5 % et non 1 = 6,25 %). Écart maximal
ramené à **5 points** (cas d'une paroi de 8 triangles seulement) et **< 0,5 point** dès 256 triangles
par paroi, c'est-à-dire dans tous les cas réels du mode simplifié. Aucun risque de régression : le
mode étant bloqué depuis sa livraison, aucun bâtiment n'a jamais pu être produit par ce chemin.

Le texte d'aide de l'étape 3 a été réécrit en conséquence (il annonçait « 20 % donne bien 20 % » —
faux : cela dépend aussi du nombre de triangles de la paroi) et renvoie désormais vers l'étape 2 pour
subdiviser plus finement si l'écart gêne.

### Vérification
**Première vérification en navigateur réel de toute cette application** (c'est le point A5 de
l'analyse). Chromium via `lab-runner`, login Keycloak réel (`e2e_member`), bundle Angular réel servi
par nginx derrière Caddy sur le domaine public, script jetable — délibérément PAS un second spec dans
`frontend/e2e/` (CLAUDE.md impose un fichier unique par app, et le pipeline runner/lab-admin suppose
ce format). **20/20 vérifications**, dont : étape 2 invisible avant le clic puis visible après (le
bug lui-même), nom par défaut posé, nom manuel non écrasé, retour arrière sans second appel réseau,
parcours complet jusqu'à l'étape 4, aperçu 3D rendu, et les trois taux obtenus (37,5 % pour 40 %
demandés sur 16 triangles, 18,8 % pour 20 %, et surtout **75 % pour 75 %** au lieu de 100 %).

Les réponses de l'API sont **stubbées** dans ce script : `e2e_member` n'a pas d'attribut `mail` dans
`sso-lab/ldap/init.ldif`, donc pas de claim `email`, donc **403 sur tout appel API authentifié**
(`api/authentication.py`) — le compte passe le flow de page mais pas l'API, limitation connue et
valable pour toutes les apps du lab. Ce qui est vérifié ici est donc exactement ce que le bug
cassait : la machine à états côté client (signal `step` + gardes `@if` du template), sur le vrai
bundle dans un vrai navigateur. Les endpoints eux-mêmes restent couverts par les tests backend.
`manage.py test api` → **95/95**, `manage.py check` propre, `ng build --configuration production`
propre, déployé via `setup2.sh bilan-thermique --yes`.

Détail relevé au passage, sans conséquence pour l'app : un attribut `name="opaque_{{g}}"`
**interpolé** est lié à l'`@Input name` de `NgModel` et n'atteint jamais le DOM, contrairement à un
`name` statique. Sans effet ici (aucun `<form>`, aucune soumission native), mais c'est ce qui rend
ces champs non sélectionnables par `[name=…]` dans un test.

### Reste ouvert
Le groupe `sol` se voit toujours imposer un « modèle opaque » choisi dans une liste qui ne contient
que des murs et des toitures — traité au **Lot AB3**, qui ajoute un plancher bas au catalogue. Le
parcours n'a pas été exercé contre l'API réelle de bout en bout (voir la limitation `e2e_member`
ci-dessus) ; le rendre possible supposerait d'ajouter un attribut `mail` au compte E2E dans
`sso-lab/ldap/init.ldif`, ce qui concerne **toutes** les apps du lab et doit être décidé à part.

---

## Lot X — Écarter le bâtiment étudié du maillage d'environnement ✅ livré le 2026-08-09

### Constat (question 2.1.3 de l'utilisateur)
« Comment traites-tu les éléments d'environnement (type bâtiments) qui ont une intersection non
nulle avec notre bâtiment, les supprimes-tu bien pour passer à l'étape de modélisation 3D ? »

**Réponse : non, aucun filtrage n'existe.** `geodata.generate_environment_mesh` extrude tous les
bâtiments retournés par IGN/OSM dans la bbox, y compris celui qui est étudié — qui est, par
construction, un bâtiment réel présent dans ces bases. `tasks.generate_environment_for_building`
enregistre le tout comme `Environment` et le lie au `Building`. Puis
`shadow.build_occluder_mesh(building_envelope, environment_envelope)` fusionne les deux : le solveur
travaille avec **deux exemplaires superposés** du bâtiment étudié.

### Pourquoi ce n'est pas anodin
- Bâtiment issu du **mode simplifié** : le doublon est *exact* (même empreinte IGN, même hauteur).
- Bâtiment issu d'un **import OBJ ou du générateur de boîte** : le doublon est *approximatif* et
  décalé — deux volumes qui s'interpénètrent partiellement, ce qui est le pire cas (des façades
  réelles se retrouvent à l'intérieur d'un obstacle).
- Effet sur l'**ombrage direct** : les rayons partent à 1 mm au-dessus du centroïde, vers l'extérieur,
  donc un doublon exact d'une forme **convexe** est majoritairement raté par les rayons — l'erreur est
  donc *faible mais silencieuse*, ce qui est plus dangereux qu'un plantage. Sur une empreinte
  **concave** (L, U, cour), les ailes du doublon bloquent bel et bien les ailes réelles, en plus de
  l'auto-ombrage déjà correctement pris en compte : l'ombrage est compté deux fois.
- Effet sur le **facteur de vue du ciel** : `compute_sky_view_factors` tire 128 directions par
  triangle dans tout l'hémisphère visible ; les directions rasantes touchent le doublon. `F_ciel` est
  sous-estimé, donc les apports diffus aussi, sur *tous* les triangles.
- Effet sur les **limites de maillage** : le doublon consomme des sommets/triangles sur
  `MAX_VERTICES`/`MAX_TRIANGLES` et peut, via le `break` de la boucle d'extrusion, **évincer un vrai
  voisin** plus éloigné.

### Étapes
1. Construire l'**empreinte 2D du bâtiment étudié** dans le repère où l'environnement est généré :
   union (shapely, déjà une dépendance) des projections XY de ses triangles — le plus simple et le
   plus robuste étant l'union des triangles `boundary='ground'` s'il y en a, avec repli sur
   l'enveloppe convexe de tous les sommets sinon (un OBJ importé peut n'avoir aucun triangle marqué
   `ground`). Attention : cette empreinte est dans le repère **local** du bâtiment, alors que les
   empreintes candidates sont converties par `local_xy` puis `_rotate_xy(north_offset_deg)` — donc
   comparer dans le repère local, après rotation, jamais avant.
2. Écarter tout candidat dont l'intersection avec cette empreinte est significative. **Ne pas utiliser
   `intersects`** : en tissu urbain, des mitoyens réels *touchent* légitimement l'empreinte étudiée et
   doivent rester des obstacles. Critère proposé : aire d'intersection / aire du candidat > un seuil
   (0,3 par exemple), ou `overlaps` avec un `buffer(-0.5)` pour tolérer les écarts de digitalisation
   entre la modélisation de l'utilisateur et la donnée IGN.
3. Faire remonter le résultat explicitement : `stats['buildings_self']` et un warning
   « 1 bâtiment écarté — il correspond au bâtiment étudié », affiché comme les warnings existants.
   Silencieux = invérifiable ; c'est justement ce qui a permis au problème de passer inaperçu.
4. Ne câbler ce filtrage **que** sur `generate_environment_for_building` (qui connaît un `Building`).
   La génération autonome (`generate_environment`, page Environnement) n'a aucun bâtiment de
   référence : y ajouter un filtrage n'aurait pas de sens. Passer l'empreinte en paramètre optionnel
   de `generate_environment_mesh`, défaut `None` = comportement actuel.
5. Cas limites à trancher pendant l'implémentation :
   - **Aucun candidat écarté** alors qu'on en attendait un (bâtiment absent d'OSM, ou empreinte
     manuelle très éloignée de la réalité) : le signaler comme warning plutôt que de laisser croire au
     filtrage.
   - **Plusieurs candidats écartés** : signe probable d'un `georef_north_offset_deg` ou d'un
     `georef_lat/lon` faux — c'est un excellent détecteur d'erreur de géoréférencement (cf. A6), à
     exploiter comme tel dans le message.
   - Un **mitoyen conservé** reste un obstacle, mais la paroi mitoyenne du bâtiment étudié devrait
     physiquement être adiabatique plutôt qu'exposée à `t_ext`. Hors scope de ce lot (ce serait un
     troisième `boundary`, après `exterior_air` et `ground`) — à mentionner dans le message et dans
     les limites de la page Théorie.
6. Tests : empreintes synthétiques, sans réseau (comme tout le reste de `geodata` — la partie réseau
   reste isolée). Doublon exact → écarté. Mitoyen partageant une arête → conservé. Voisin disjoint →
   conservé. Recouvrement partiel à 50 % → écarté. Rotation `north_offset_deg = 90°` → même résultat
   (vérifie l'étape 1, le piège de repère).

### Dépendance
Aucune. À faire juste après le Lot W : c'est le correctif qui a le plus d'effet sur la justesse d'un
calcul avec environnement automatique, et tous les lots suivants (Z végétation, AA terrain) ajoutent
des obstacles dans le même maillage — autant que le filtrage existe avant.

### Ce qui a été fait
Les six étapes ci-dessus, plus un correctif d'interface sans lequel tout le lot serait resté
invisible (voir « Avertissements » plus bas).

**Deux fonctions pures dans `geodata.py`**, où vit toute la décision — `generate_environment_mesh`
ne fait que les câbler :

- `envelope_footprint_polygon(envelope)` — empreinte 2D du bâtiment étudié. **Projette TOUS les
  triangles** plutôt que les seuls `boundary='ground'` : les murs verticaux se projettent en
  segments d'aire nulle (éliminés par le seuil), le sol et la toiture donnent la silhouette réelle,
  concavités et cours intérieures comprises. Décision prise en écrivant le code, contre l'étape 1
  d'origine qui prévoyait « les triangles `ground`, avec repli sur l'enveloppe convexe » : le repli
  convexe aurait comblé les creux (un voisin logé dans le renfoncement d'un bâtiment en L aurait été
  écarté à tort), et surtout un OBJ importé n'a **aucun** triangle marqué `ground` par défaut — le
  filtrage n'aurait donc protégé que les bâtiments issus du mode simplifié, exactement ceux qui en
  ont le moins besoin puisque leur doublon est exact et donc facile à repérer autrement.
- `_is_studied_building(footprint_xy, self_polygon)` — critère de recouvrement.

**Critère** : `aire(intersection) / min(aire_candidat, aire_étudié) > 0,3`. Rapporté au **minimum**
des deux aires et non à celle du candidat, pour détecter le même bâtiment que la modélisation de
l'utilisateur soit plus petite ou plus grande que la donnée IGN/OSM. Le `buffer(-0.5)` envisagé à
l'étape 2 s'est avéré inutile : deux polygones qui se touchent par une arête (mitoyen réel) ont une
intersection d'aire **nulle**, et un débordement de digitalisation de 10 cm sur 8 m donne 1,25 % —
très en dessous du seuil. Un simple critère d'aire suffit, et il reste vrai que `intersects` aurait
supprimé tous les mitoyens d'un tissu urbain dense.

**Filtrage placé avant l'extrusion et avant le contrôle de limite de maillage**, sinon le doublon
consommerait des sommets/triangles sur `MAX_VERTICES`/`MAX_TRIANGLES` et pourrait, via le `break`,
évincer un vrai voisin plus éloigné. `n_skipped` retranche désormais `n_self` : un bâtiment écarté
parce qu'il est le bâtiment étudié n'a rien à voir avec la limite de maillage, et le compter là
donnait le message « réduire le rayon », faux.

**Câblé uniquement sur `generate_environment_for_building`** (la seule tâche qui connaisse un
`Building`). `generate_environment` — génération autonome de la page Environnement — n'a aucun
bâtiment de référence : `self_envelope=None` par défaut, comportement d'origine strictement
inchangé, vérifié en réel.

### Avertissements : le frontend les jetait
`job.result['warnings']` était produit par le backend depuis la génération d'environnement… et
**jamais affiché** : `startEnvGenPoll` ne reprenait que `job.message`. Un filtrage silencieux étant
un filtrage invérifiable (et c'est précisément ce qui a permis au doublon de passer inaperçu jusqu'à
la question de l'utilisateur), la page Bâtiment affiche désormais la liste complète des
avertissements — bâtiment étudié écarté, hauteurs estimées, limite de maillage atteinte.

Piège d'ordonnancement traité au passage : `loadBuilding()` remet l'état à plat, et la génération
l'appelle **avant** de poser ses avertissements. Le reset de `envGenWarnings` est donc synchrone, en
tête de `loadBuilding` (comme `message`), et non dans le callback de la requête — sinon la réponse
HTTP, qui arrive après, les effacerait. C'est exactement ce qui arrive déjà à `envGenJob` (le bloc
d'état du job disparaît après un succès) ; comportement préexistant, non touché ici.

Trois messages distincts plutôt qu'un seul : `n_self == 1` (cas normal), `n_self > 1` (« inhabituel,
signale plutôt un géoréférencement erroné qu'un vrai doublon » — le détecteur d'erreur de
géoréférencement prévu à l'étape 5), et `self_polygon` fourni mais `n_self == 0` (« pas retrouvé
dans les données à cet endroit »), pour ne jamais laisser croire au filtrage quand il n'a rien fait.

### Vérification
**13 tests** (`SelfBuildingFilterTest`, `SelfBuildingFilterFrameTest`), sans réseau : aire de
l'empreinte insensible aux murs verticaux, empreinte obtenue sans aucun triangle `ground`,
concavité préservée (L de 12 m² et non son enveloppe convexe de 16), enveloppe vide → `None`,
doublon exact écarté, mitoyen à arête partagée conservé, voisin disjoint conservé, recouvrement
partiel à 75 % écarté, débordement de digitalisation conservé, candidat plus petit entièrement
inclus écarté, `self_envelope=None` ne filtre rien.

`SelfBuildingFilterFrameTest` rejoue la chaîne réelle (`local_xy` → `_rotate_xy`) à cinq caps
(0°, 37°, 90°, 180°, 270°) pour le **piège de repère** : les empreintes candidates sont converties
dans le repère local, `Building.envelope` y est déjà, une seconde rotation ferait rater le doublon.
Sensibilité confirmée par mutation : empreinte du bâtiment étudié construite dans le mauvais repère
→ détection encore vraie à 0° mais **fausse à 90° et 180°**. Un test qui ne couvrirait que le cap
nul serait donc aveugle à cette erreur — c'est bien pour ça que le test balaie plusieurs caps.

**Vérifié en réel contre l'API IGN**, sur deux zones pavillonnaires distinctes (Orléans banlieue,
Antony) : 42 → 41 et 41 → 40 bâtiments retenus, `buildings_self = 1` dans les deux cas — le
bâtiment étudié écarté, et lui seul, aucun voisin perdu. Chemin « pas retrouvé » vérifié également
en réel (enveloppe volontairement placée à 200 m : 0 écarté, 39 retenus, avertissement explicite).
Interface vérifiée en navigateur réel (même dispositif qu'au Lot W) : **6/6**, dont le rendu des
deux avertissements et leur survie au rechargement du bâtiment.

`manage.py test api` → **108/108** (95 + 13), depuis l'image reconstruite et non depuis des
fichiers copiés à chaud. `manage.py check` propre, `ng build --configuration production` propre,
déployé via `setup2.sh bilan-thermique --yes`.

### Reste ouvert
Un **mitoyen** est conservé comme obstacle (c'est voulu), mais la paroi que le bâtiment étudié
partage avec lui devrait physiquement être adiabatique plutôt qu'exposée à `t_ext` : cela suppose un
troisième `boundary` (après `exterior_air` et `ground`), non fait — à ajouter aux limites de la page
Théorie (Lot AC). Le seuil `SELF_OVERLAP_RATIO = 0,3` n'est pas réglable depuis l'interface ; les
deux vérifications en réel n'ont montré aucun cas limite, mais un bâtiment modélisé très
grossièrement (boîte débordant largement sur un petit voisin) pourrait faire écarter ce voisin —
l'avertissement « plus d'un bâtiment écarté » est le garde-fou prévu pour ce cas.

---

## Lot Y — Créer un bâtiment depuis ses coordonnées, hors mode simplifié ✅ livré le 2026-08-09

### Constat (question 2.1.2)
« Pour le chargement du bâtiment hors mode simplifié on ne peut pas commencer par sélectionner le
bâtiment à partir de ses coordonnées ? »

**Aujourd'hui : non.** La page Bâtiment n'offre que deux points de départ — import d'un fichier
OBJ/STL (`parseMeshFile`) et générateur de boîte (Lot O). La recherche par coordonnées existe
pourtant déjà **entièrement** côté backend (`POST /api/batiments/rechercher/` →
`geodata.search_nearby_buildings`, qui renvoie chaque candidat **déjà extrudé** en enveloppe groupée
`sol`/`toiture`/`mur_1..N` avec les `boundary` corrects), mais elle n'est appelée que par le mode
simplifié. C'est un chaînon manquant d'interface, pas une fonctionnalité à écrire.

### Ce que ça change par rapport au mode simplifié
Le mode simplifié impose son propre parcours (subdivision fine forcée, puis taux de vitrage par
paroi). Sur la page Bâtiment, la recherche ne serait qu'une **troisième source de maillage** : elle
remplit `vertices`/`triangles`/`groups` exactement comme un import OBJ, puis tout l'outillage
existant s'applique tel quel (assignation par groupe, sélection manuelle au clic,
`boundary`/`shading_profile_id` par groupe, raffinement à la demande, ombrage, environnement).

### Étapes
1. **Factoriser la recherche** en un composant partagé (formulaire lat/lon/rayon + liste de
   candidats + bouton « Choisir »), utilisé par la page Bâtiment **et** par le mode simplifié. Ne pas
   dupliquer : les deux pages divergeraient à la première évolution (et le mode simplifié vient
   justement de montrer qu'un parcours non testé pourrit en silence). Émettre le `Candidate` choisi
   en `output()`.
2. Sur la page Bâtiment, brancher la sortie comme `onFileSelected`/`generateBox` le font déjà :
   `currentBuildingId = null`, remplir `vertices`/`triangles`/`groups`, réinitialiser les
   assignations, et — c'est le vrai gain par rapport à un import OBJ — **préremplir
   `georefLat`/`georefLon` avec les coordonnées du candidat** et `georefNorthOffset = 0` (l'empreinte
   est produite dans le repère est/nord réel, donc l'offset nord est nul **par construction** — ce
   qui supprime d'un coup la principale source d'erreur silencieuse identifiée en A6).
3. Préremplir aussi `georefGroundZ` si le Lot AA est déjà fait (altitude du terrain au point choisi) ;
   sinon laisser vide et le brancher au Lot AA.
4. **Relever `MAX_WALLS_SIMPLIFIED_MODE`** (30) ou le rendre paramétrable : cette limite existe parce
   que le mode simplifié génère *un menu déroulant par paroi* (« inutilisable au-delà », 515 murs
   observés en plein Paris). La page Bâtiment, elle, assigne par groupe avec sélection multiple et
   dispose du sélecteur manuel au clic — la contrainte n'a pas lieu d'être. Passer le plafond en
   paramètre de `search_nearby_buildings` (défaut inchangé pour le mode simplifié) plutôt que de le
   supprimer.
5. Ne **pas** forcer de raffinement : sur la page Bâtiment, `refineMesh` est déjà un contrôle
   explicite. Un bâtiment issu de la recherche arrive avec 2 triangles par mur, ce qui est le bon
   point de départ pour une assignation par groupe.
6. Signaler dans l'interface ce que la source implique — mêmes réserves que le mode simplifié :
   toiture toujours plate, hauteur parfois estimée depuis le nombre d'étages, cours intérieures
   comblées (cf. C7).

### Dépendance
Aucune bloquante. Meilleur après le Lot W (le composant partagé sortira du code du mode simplifié
une fois celui-ci vérifié en navigateur) et idéalement après AA (pour `georefGroundZ`).

### Ce qui a été fait
Les six étapes. Nouveau composant partagé `components/building-search` (formulaire + appel + liste de
candidats), utilisé par la page Bâtiment **et** par le mode simplifié — dont le code de recherche a
été retiré au profit du composant. Il ne fait QUE chercher et laisser choisir : il n'écrit rien, ne
crée aucun bâtiment, et émet le candidat ; chaque page décide de ce qu'elle en fait. Le type
`BuildingCandidate` remonte dans `core/building.types.ts`, où il était de toute façon à sa place.

Sur la page Bâtiment, la recherche est une **troisième source de maillage**, à côté de l'import
OBJ/STL et du générateur de boîte — aucun parcours imposé ensuite, tout l'outillage habituel
(assignation par groupe, sélection au clic, `boundary`/occultations, raffinement) s'applique tel quel.
Le vrai gain par rapport à un import : **le géoréférencement est connu par construction**. Latitude,
longitude, et surtout `georef_north_offset_deg = 0` — l'empreinte étant produite dans le repère
est/nord réel, la rotation nord est nulle par définition, ce qui supprime d'un coup la principale
source d'erreur silencieuse identifiée au point A6 de l'analyse. `georef_ground_z` est récupéré dans
la foulée (Lot AA), donc les quatre champs du géoréférencement sont remplis sans une saisie.

`MAX_WALLS_SIMPLIFIED_MODE` est devenu **paramétrable** plutôt que supprimé : ce plafond de 30 existe
parce que le mode simplifié génère un menu déroulant PAR PAROI (« 515 murs observés en plein Paris »,
Lot T) — la page Bâtiment, qui assigne par groupe avec sélection multiple, le relève à 400. Le mode
simplifié garde le sien, il en a toujours besoin.

Pas de raffinement forcé : sur la page Bâtiment, `refineMesh` est déjà un contrôle explicite, et un
bâtiment issu de la recherche arrive avec 2 triangles par mur — le bon point de départ pour une
assignation par groupe. Les réserves de la source (toiture plate, hauteur parfois estimée, cours
intérieures comblées) sont écrites au-dessus du bloc.

### Vérification
**12/12 en navigateur réel**, dont trois vérifications de non-régression qui étaient tout l'enjeu de
la factorisation : le mode simplifié utilise bien le **même** composant, son plafond de parois n'a pas
bougé (`max_walls` absent → défaut serveur) alors que la page Bâtiment envoie bien 400, et **son
étape 2 est toujours atteinte** — le Lot W n'a pas été défait en refaisant sa page. Côté Bâtiment :
maillage chargé avec ses groupes, latitude/longitude/rotation nord/altitude préremplies, nom proposé,
et bouton d'altitude fonctionnel isolément. 150/150 backend.

### Reste ouvert
Le composant ne propose pas de carte : les coordonnées se saisissent au clavier, comme avant. La
recherche trie par distance au **centroïde** de l'empreinte et non par « empreinte contenant le point
demandé » (défaut C7 relevé à l'analyse, non traité) : sur une parcelle allongée ou en angle, le
voisin peut sortir premier — la liste affiche la distance, ce qui permet de choisir, mais l'ordre
n'est pas toujours l'intuition.

---

## Lot Z — Végétation dans l'environnement (arbres, haies, bois) ✅ Z1+Z2 livrés le 2026-08-09 (Z3 non fait)

### Constat (question 2.1.1)
« Pour le chargement de l'environnement j'aimerais qu'on puisse charger des éléments plus fins comme
les arbres — y a-t-il une source utilisable ? »

**Oui, deux, complémentaires — vérifiées par appel réel le 2026-08-09**, et qui reproduisent
exactement la bascule IGN/OSM déjà en place dans `geodata.py` :

| Source | Objet | Géométrie | Hauteur | Vérifié |
|---|---|---|---|---|
| `BDTOPO_V3:zone_de_vegetation` (IGN, WFS déjà utilisé) | zones boisées, haies | MultiPolygon | **absente** | 2000 features échantillonnées : `nature` ∈ Haie (929), Bois (929), Forêt fermée de feuillus (97), Lande ligneuse (18), Forêt fermée de conifères (14), Forêt fermée mixte (12), Forêt ouverte (1) |
| `BDTOPO_V3:haie` (IGN, couche distincte) | haies seules | — | — | couche présente au GetCapabilities |
| OSM `natural=tree` (nœuds) | **arbres individuels** | point | partielle | 337 arbres dans une bbox de ~800 × 700 m à Paris 10ᵉ ; `leaf_type` sur 294, `height` sur 134, `circumference` sur 127, `leaf_cycle` sur 76 |
| OSM `natural=tree_row`, `natural=wood`, `landuse=forest` | alignements, bois | ligne / polygone | partielle | — |

Points structurants qui en découlent :
- **Seul OSM connaît l'arbre individuel.** IGN ne fournit que des zones. Pour « les arbres devant ma
  maison », c'est donc OSM, y compris en France — l'inverse de la règle actuelle (IGN prioritaire).
  Les deux sources sont **complémentaires, pas alternatives** : IGN pour les masses boisées, OSM pour
  les sujets isolés.
- **Aucune des deux ne donne une hauteur fiable.** IGN : pas d'attribut hauteur du tout (vérifié).
  OSM : `height` sur 40 % des arbres au mieux, et en zone rurale bien moins. Une table de hauteurs par
  type est donc inévitable — même statut assumé que le catalogue de parois : valeurs indicatives, pas
  une référence. Le repli `circumference` → hauteur (relation allométrique) est possible mais ajoute
  une approximation sur une approximation ; à ne faire que si l'utilisateur le demande.
- Piste écartée après vérification : dériver la hauteur de végétation d'un modèle numérique de surface
  (MNS − MNT). L'API d'altimétrie de la Géoplateforme **refuse** la ressource `ign_rge_mns_wld`
  (`BAD_PARAMETER: 'ign_rge_mns_wld' is not an accepted resource`, testé) ; seul `ign_rge_alti_wld`
  (le terrain) est exposé. Le LiDAR HD classifié (classe 5 = haute végétation) donnerait la vraie
  hauteur, mais impose de télécharger des dalles LAZ de plusieurs centaines de Mo par km² — hors de
  proportion avec cet hôte, à exclure explicitement.

### Le vrai point dur : un arbre n'est pas une boîte opaque
`shadow.py` ne connaît aujourd'hui que **bloqué / pas bloqué** (`intersects_any` → `int8` dans
`per_triangle`). Un arbre à feuilles caduques transmet environ 10-20 % du rayonnement en été et
40-70 % en hiver, feuilles tombées — précisément la saison où l'apport solaire compte le plus. Le
modéliser en obstacle opaque **surestime lourdement les besoins de chauffage** ; l'ignorer surestime
la surchauffe d'été. Trois niveaux de réalisation, à trancher avec l'utilisateur **avant de coder** :

- **Z1 — géométrie seule, opaque.** Zones IGN extrudées et arbres OSM en volumes simples (prisme
  octogonal ou sphère grossière sur tronc), injectés dans le même maillage d'obstacles. Aucun
  changement à `shadow.py`, aucun changement au solveur. Faux en hiver, mais honnête si c'est écrit.
- **Z2 — occulteur à transmittance.** `compute_visibility_grid` passe de `intersects_any` à un
  test qui identifie **quel** triangle est touché (`intersects_first`/`intersects_id`), afin
  d'appliquer un facteur (1 − k) au lieu d'un 0/1. `per_triangle` devient un tableau de flottants —
  attention au poids du `JSONField` (`Building.sun_visibility`) : pour 1024 triangles × 24 azimuts ×
  7 élévations, on passe de ~172 k entiers 0/1 à autant de flottants, soit un JSON plusieurs fois
  plus gros. À mesurer avant de s'engager. Il faut aussi que le maillage occulteur porte une
  transmittance **par triangle**, donc que `build_occluder_mesh` cesse d'être un simple concaténateur.
- **Z3 — saisonnalité.** Deux grilles d'ombrage (feuillu en feuilles / hors feuilles) et une bascule
  par heure selon la date. Suppose une notion de **date calendaire côté serveur**, que l'application a
  jusqu'ici délibérément refusée (Lot V : « aucune notion de date calendaire réelle côté serveur »).
  C'est donc une décision d'architecture, pas un réglage — à ne pas prendre en marge d'un autre lot.

**Recommandation** : livrer Z1 et Z2 ensemble (Z2 sans Z1 n'a pas d'objet, Z1 sans Z2 donne un
résultat qu'on sait faux), en traitant tout arbre comme persistant si `leaf_cycle`/`leaf_type` ne dit
rien ; garder Z3 comme lot séparé, à décider ensuite.

### Étapes (Z1 + Z2)
1. `geodata.fetch_ign_vegetation(bbox)` et `geodata.fetch_osm_vegetation(bbox)` sur le modèle exact
   des fonctions bâtiments existantes (mêmes `_request_with_retry`, mêmes `GeodataError`, même
   séparation fetch / assemblage pour rester testable sans réseau).
2. Table `VEGETATION_PROFILES` : hauteur par défaut et transmittances (feuilles / sans feuilles) par
   `nature` IGN et par `leaf_type`/`leaf_cycle` OSM. Documenter la provenance des valeurs comme pour
   `SHADING_PROFILES`.
3. Volume : réutiliser `extrude_footprint` pour les zones IGN ; pour un arbre OSM ponctuel, un
   prisme octogonal (rayon depuis `diameter_crown`, sinon `circumference`, sinon défaut) est
   largement suffisant et évite d'introduire une primitive sphérique.
4. **Poser une limite de comptage dédiée** : 337 arbres dans une seule bbox urbaine ×
   (8 à 16 triangles chacun) mange `MAX_TRIANGLES` (20 000) à lui seul. Prévoir un garde-fou et un
   tri par distance analogue à celui des bâtiments, et un warning explicite quand ça coupe.
5. Option d'interface : cases à cocher « bâtiments / végétation » sur la génération d'environnement,
   plutôt qu'un chargement systématique — le coût de calcul d'ombrage croît avec le nombre de
   triangles occulteurs.
6. Écrire la limite correspondante sur la page Théorie (Lot AC) : la végétation est un écran à
   transmittance constante, sans saisonnalité tant que Z3 n'est pas fait.

### Dépendance
Après le Lot X (sinon la végétation s'ajoute à un environnement déjà faux) et de préférence après AA
(un arbre doit être posé sur le terrain réel, pas à z = 0).

### Ce qui a été fait
**Z1 et Z2 livrés ensemble**, comme recommandé ci-dessus — Z1 seul aurait donné un résultat qu'on
sait faux. **Z3 (saisonnalité) non fait** : il supposerait une notion de **date calendaire côté
serveur**, que l'application refuse délibérément depuis le Lot V (« aucune notion de date calendaire
réelle côté serveur »). C'est une décision d'architecture, pas un réglage : laissée ouverte.

**Sources cumulées et non alternatives**, contrairement à la règle « IGN d'abord, OSM en repli » du
reste du module : seule OSM connaît l'arbre **individuel**, seule l'IGN couvre proprement les masses
boisées françaises. Les zones boisées OSM ne sont reprises que hors de France, pour ne pas doubler la
BD TOPO. Table `VEGETATION_PROFILES` (hauteur + transmittance par type) : inévitable, puisque la
couche IGN n'a **aucun** attribut de hauteur et qu'OSM n'en porte que sur ~40 % des arbres.

**Le vrai travail est Z2 : un occulteur qui atténue au lieu de bloquer.** Le piège central, et ce que
le lot résout : *un rayon qui traverse un volume fermé touche au moins DEUX faces* (entrée et
sortie). Multiplier la transmittance à chaque face donnerait k² au lieu de k. D'où
`shadow.VegetationScene`, qui groupe les faces touchées **par objet** (`obj`) via
`intersects_id(multiple_hits=True)` et n'applique `k` qu'une fois par objet réellement traversé.

Architecture retenue pour ne rien coûter à ceux qui n'utilisent pas la végétation : **deux
intersecteurs et deux passes**. La passe opaque reste exactement celle d'avant
(`intersects_any`, avec sa sortie anticipée) ; la végétation — plus coûteuse — n'est interrogée que
pour les rayons ayant **déjà** passé le test opaque, et seulement s'il y a de la végétation. Sans
végétation, `build_vegetation_scene` retourne `None` et tout le chemin est bit à bit celui du Lot C.

`per_triangle` devient une **fraction** au lieu d'un booléen — le binaire n'étant que le cas
particulier k ∈ {0, 1}. Stocké en `int8` quand il n'y a pas de végétation (pour ne pas gonfler
`Building.sun_visibility`), en `float32` arrondi à 3 décimales sinon. `lookup_visibility` retourne
un `float`, et le solveur fait `cos_ti *= transmittance` au lieu de `cos_ti = 0`. Même généralisation
sur le facteur de vue du ciel (poids multiplié par la transmittance plutôt que mis à zéro) et sur le
mode temps réel.

Les zones de végétation qui recouvrent le bâtiment étudié sont écartées par le **même critère qu'au
Lot X** (recouvrement relatif, pas simple contact) — un bois cartographié englobant la maison en
ferait un volume translucide autour d'elle ; un arbre planté contre la façade, lui, reste légitime.

### Vérification
**7 tests.** Le décisif : un couvert traversé de part en part transmet **k et non k²** — l'assertion
teste explicitement les deux. Son pendant : deux couverts **distincts** empilés multiplient bien
(0,5 × 0,4 = 0,2), donc grouper par objet ne fusionne pas des objets différents. Plus : grille
strictement binaire sans triangle translucide (non-régression du Lot C), `k = 0` équivalent à
opaque, facteur de vue du ciel **strictement entre** le cas dégagé et le cas opaque, `lookup_visibility`
rendant un flottant, et un test **bout en bout** : sous un couvert à k = 0,25, l'échauffement de
surface vaut exactement 25 % de celui à ciel ouvert — l'atténuation traverse donc tout le chemin
jusqu'au flux calculé.

**Vérifié en réel** contre les deux API : 13 arbres OSM à Paris 10ᵉ (13 objets distincts, k = 0,20
feuillus) ; à Orléans, 3 zones IGN **et** 6 arbres OSM cumulés, 9 objets et deux transmittances
(0,15 bois, 0,20 feuillu) — les deux sources se complètent comme prévu. Sur un bâtiment réel avec sa
végétation réelle (10 objets, 1000 triangles), le facteur de vue du ciel moyen passe de 0,5000
(dégagé) à **0,4885** avec atténuation, contre 0,4867 si la même végétation était opaque : l'effet est
bien intermédiaire. 157/157.

### Reste ouvert
**Z3 — saisonnalité, non fait** : la valeur « sans feuilles » est déjà dans `VEGETATION_PROFILES`,
inutilisée. Aujourd'hui un arbre à feuilles caduques est modélisé **en feuilles toute l'année**, ce
qui **surestime l'ombrage hivernal** — précisément la saison où l'apport solaire compte le plus.
Écrit dans l'interface et remonté en avertissement à chaque génération, plutôt que masqué. La
végétation est posée à z = 0 et non sur le relief (le maillage de terrain du Lot AA, lui, l'est) ;
la couronne d'un arbre est un prisme octogonal, pas une sphère ; et le rayon de couronne se déduit de
la circonférence du tronc quand `diameter_crown` manque — une approximation sur une approximation,
assumée.

---

## Lot AA — Terrain / sol dans l'environnement ✅ livré le 2026-08-09

### Constat (question 2.1.4)
« Et pour finir dans l'environnement saurais-tu charger le sol ? »

**Oui.** `geodata.py` note aujourd'hui « Terrain supposé plat : pas d'appel séparé à l'API Altimétrie
IGN (limitée à 5 req/s, sans intérêt vu la faible variation de relief sur un rayon de ce type) ». Ce
raisonnement tient pour une parcelle plate, pas pour un bâtiment en pente, en fond de vallée ou en
contrebas d'un talus — et il ne couvre de toute façon pas les trois autres usages du terrain
ci-dessous.

**Sources vérifiées par appel réel le 2026-08-09 :**

| API | Couverture | Résultat testé |
|---|---|---|
| `data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json` (`resource=ign_rge_alti_wld`) | France | **60 points en un seul appel** avec `delimiter=\|` et `zonly=true` → `{"elevations":[106.9, …]}`. RGE ALTI, pas de clé |
| `api.open-meteo.com/v1/elevation` | monde | `{"elevation":[108.0, 109.0]}` — 1 m d'écart avec l'IGN sur les mêmes points, gratuit, sans clé |

Les deux se recoupent à 1 m près là où elles se superposent, et la bascule IGN → Open-Meteo reproduit
exactement le couple IGN/OSM déjà en place. Open-Meteo (Copernicus DEM ~90 m) est plus grossier :
suffisant pour une altitude de référence, insuffisant pour un micro-relief.

### Quatre usages distincts, à ne pas confondre
1. **Préremplir `georef_ground_z`** (un seul point, un seul appel). Aujourd'hui ce champ est saisi à
   la main, sans aide, et il pilote le calage vertical de **tous** les obstacles IGN
   (`base_z = altitude_minimale_sol − ground_z_ref`). Laissé vide, il vaut 0 : tous les voisins issus
   d'IGN sont placés à leur altitude NGF absolue (100 m et plus) au-dessus d'un bâtiment posé à z = 0.
   L'ombrage est alors totalement faux, **sans aucun signe visible**. C'est le gain le plus important
   du lot pour le plus petit coût — à faire en premier, indépendamment du reste.
2. **Poser les obstacles OSM sur le terrain réel.** `fetch_osm_buildings` met `base_z = None` → z = 0
   pour tous : hors de France, tous les voisins sont au même niveau quel que soit le relief. Une
   requête d'altitude par empreinte résout le problème.
3. **Maillage de terrain comme occulteur.** Grille régulière sur le rayon d'environnement (10 m de
   pas sur 150 m = 31 × 31 = 961 points → 16 appels IGN de 60 points, ou 10 appels Open-Meteo de 100),
   triangulée et ajoutée au maillage d'obstacles. Effet réel : masquage du soleil rasant par un relief
   amont, correctement pris en compte par `compute_visibility_grid` et par `compute_sky_view_factors`
   sans aucune modification de `shadow.py`.
4. **Sol comme surface réfléchissante (albédo).** Ajouter un maillage de terrain ne le fait **pas** :
   `shadow.py` ne calcule que de l'occultation. L'albédo est explicitement hors scope aujourd'hui
   (page Théorie) et le rester ; ne pas laisser croire que « charger le sol » l'apporte.

### Étapes
1. Nouveau module pur `elevation.py` (même patron que `weather_source.py`/`geodata.py` : `fetch_*`
   réseau séparé de l'assemblage, `ElevationError`, `_request_with_retry`), avec bascule
   IGN → Open-Meteo sur le même test `is_in_france` que `geodata`.
2. Batcher les points (60 par appel côté IGN, vérifié) et respecter la limite de débit annoncée
   (5 req/s) — un `time.sleep` entre lots, pas de parallélisme.
3. Endpoint `POST /api/altitude/` (synchrone, un point) pour l'usage 1, appelé depuis la page Bâtiment
   au moment où lat/lon sont renseignés : un bouton « récupérer l'altitude » plutôt qu'un appel
   automatique, pour rester cohérent avec la façon dont la météo est chargée.
4. Usages 2 et 3 dans la tâche Celery existante `generate_environment_for_building` (déjà
   asynchrone, déjà porteuse d'une barre de progression et de warnings).
5. Pièges à surveiller :
   - **Ne pas faire traverser le bâtiment par le terrain.** Le bâtiment est modélisé avec sa base à
     z = 0 ; le terrain doit être décalé de `georef_ground_z` et, autour de l'emprise du bâtiment,
     forcé au niveau de sa base (sans quoi un terrain en pente coupe les murs et bloque des rayons de
     l'intérieur).
   - **Coût du lancer de rayons** : le terrain est le plus gros contributeur en triangles. Pas de
     grille fine par défaut ; rendre le pas paramétrable et le compter dans les warnings.
   - Le terrain ne doit **jamais** devenir un triangle du `Building` : c'est de la géométrie
     d'occultation, comme les obstacles (`Environment.envelope`, aucun `paroi_model_id`).
6. Tests sans réseau sur la partie assemblage (grille → sommets/triangles, décalage vertical,
   aplanissement sous l'emprise), comme pour `_assemble_weather_series`.

### Dépendance
L'usage 1 est indépendant et peut être livré seul — à faire tôt, il corrige une erreur silencieuse
existante. Les usages 2 et 3 gagnent à venir après le Lot X.

### Ce qui a été fait
Nouveau module pur `elevation.py` (même patron que `geodata.py`/`weather_source.py` : réseau isolé
dans les `fetch_*`, assemblage testable sans réseau), avec la **même bascule IGN → mondial** que pour
les bâtiments. `ElevationError`, batching (60 points par appel IGN — la valeur mesurée, 100 côté
Open-Meteo) et délai entre lots pour respecter les 5 req/s annoncés.

**Usage 1 — `georef_ground_z` prérempli**, endpoint synchrone `POST /api/altitude/` et bouton
« Récupérer l'altitude » sur la page Bâtiment. C'était le gain le plus important pour le plus petit
coût : ce champ pilote le calage vertical de **tous** les obstacles IGN
(`base_z = altitude_minimale_sol − ground_z_ref`) et, laissé vide, il vaut 0 — les voisins se
retrouvent alors à leur altitude NGF absolue, souvent 100 m au-dessus d'un bâtiment posé à z = 0, et
l'ombrage est entièrement faux **sans le moindre signe visible**. L'aide du champ le dit maintenant
explicitement.

**Usage 3 — maillage de terrain**, opt-in (case à cocher + pas de grille) dans la génération
d'environnement. Ajouté au **même** maillage d'obstacles que les bâtiments : `api.shadow` ne fait
aucune différence entre les deux, seule compte la géométrie qui bloque les rayons — donc aucune
modification de `shadow.py`, et le relief est pris en compte aussi bien par la grille de visibilité
solaire que par le facteur de vue du ciel. Opt-in et non défaut parce que c'est de loin le plus gros
contributeur en triangles (deux par maille).

Deux pièges traités, tous deux signalés à l'étape 5 :
- **Le terrain est aplani sous l'emprise du bâtiment** (réutilise `envelope_footprint_polygon` du
  Lot X). Sans ça, une pente traverse les murs et bloque des rayons **depuis l'intérieur** de
  l'enveloppe : on fausserait l'ombrage au lieu de l'améliorer.
- **Garde-fous de charge** : `MAX_TERRAIN_POINTS` côté grille, et vérification que l'ajout ne
  dépasse pas `MAX_VERTICES`/`MAX_TRIANGLES` — auquel cas le terrain est abandonné avec un
  avertissement plutôt que d'évincer les bâtiments voisins, qui comptent bien davantage pour
  l'ombrage. Tout le chargement du terrain est best-effort : une panne d'altimétrie ne fait pas
  perdre les bâtiments déjà extrudés.

`geodata.latlon_from_local_xy` (inverse exact de `local_xy` + `_rotate_xy`) a dû être ajouté : la
grille est définie en coordonnées **locales** et l'altimétrie se demande en lat/lon. Une erreur de
signe y aurait demandé l'altitude au mauvais endroit, en silence — le terrain aurait eu l'air
plausible tout en étant celui d'à côté. D'où un test d'aller-retour à cinq caps.

### Vérification
**9 tests** sans réseau : géométrie de la grille (carrée, centrée, balayée ligne par ligne — l'ordre
est un contrat entre `terrain_grid_local` et `build_terrain_mesh`), refus des grilles trop denses et
des pas nuls, deux triangles par maille, indices tous valides, altitude relative à `ground_z_ref`,
**aplanissement sous l'emprise avec relief conservé au-delà**, et rejet d'un nombre d'altitudes
incohérent. Plus l'aller-retour `latlon_from_local_xy` à cinq caps et trois points.

**Vérifié en réel contre les deux API** : altitude ponctuelle à Paris (33,8 m, IGN), Annecy
(448,4 m, IGN) et Montréal (83,0 m, **Open-Meteo** — le repli hors de France fonctionne), en ~0,15 s.
Maillage complet sur un site en pente réel (Annecy, rayon 150 m au pas de 15 m) : 441 points,
800 triangles, **4,7 s**, amplitude de relief 4,5 m — contre 1,4 m sur un site plat (Orléans), ce qui
confirme que le terrain reproduit bien du relief et pas du bruit. 150/150 depuis l'image
reconstruite.

### Reste ouvert
**Usage 2 non fait** : les obstacles OpenStreetMap restent posés à z = 0 (`base_z = None`), donc hors
de France le relief entre bâtiments n'est toujours pas reproduit — le maillage de terrain, lui,
l'est. Le corriger demanderait une requête d'altitude par empreinte, facile à greffer sur
`elevation.fetch_elevations` mais qui multiplie les appels. **Usage 4 (albédo) délibérément hors
scope** : ajouter un maillage de terrain n'apporte PAS la réflexion solaire du sol, `shadow.py` ne
calculant que de l'occultation — à ne pas laisser croire. Pas de transition douce entre la zone
aplanie sous le bâtiment et le relief alentour (marche franche à la limite de l'emprise) : sans
conséquence pour de l'occultation.

---

## Lot AB — Correctifs physiques et méthodologiques (issus de la section C)

Quatre correctifs indépendants, regroupés parce qu'ils sont tous courts et tous dans la même famille
(le modèle ne fait pas ce que l'interface annonce). À faire dans cet ordre, du plus faussant au moins
faussant. **Chacun doit faire passer `python manage.py test api` avant/après** — c'est l'objet du
Lot F, et AB1/AB3 touchent la boucle horaire et `_assemble_F_hour`.

### AB1 — Dérive du planning et du calendrier sur heure météo manquante (C3) ✅ livré le 2026-08-09
Le point le plus vicieux : silencieux, cumulatif, et il fausse à la fois la ventilation, les apports
internes, les volets **et** les consignes de thermostat.

Deux corrections possibles, à trancher :
- **(a) Combler les trous** dans `_assemble_weather_series` par interpolation, en signalant les heures
  interpolées. Rétablit l'alignement, mais contredit la décision explicite du Lot L (« on saute
  l'heure plutôt que d'inventer une valeur qui fausserait silencieusement le bilan »).
- **(b) Transporter l'heure réelle** : ajouter un champ `hour_of_day` (0-23) — et, pour le Lot V, un
  `day_index` — à chaque point météo, rempli par `weather_source` depuis le `datetime` qu'il possède
  déjà, puis l'utiliser à la place de `(heure_debut + hour_idx) % 24`. `heure_debut` devient un repli
  pour les séries collées à la main. **Recommandé** : ça respecte la décision du Lot L, ça supprime
  aussi le problème C5 côté serveur, et ça retire un champ manuel à l'utilisateur.

Test : série avec un trou volontaire au milieu → le créneau de planning appliqué à la dernière heure
doit être le bon (identité exacte, pas une convergence).

#### Ce qui a été fait
**Option (b) retenue**, comme recommandé — la décision du Lot L (« ne rien inventer, sauter l'heure »)
reste intacte. Un seul champ ajouté au lieu des deux envisagés : **`hour_index`**, nombre d'heures
écoulées depuis minuit du **premier jour** de la série. `% 24` donne l'heure du jour (serveur),
`// 24` le numéro du jour (client, pour les week-ends et vacances) — c'est très exactement la
grandeur que `heure_debut + hour_idx` tentait de reconstruire, mais mesurée au lieu d'être déduite.

**Dérivé de la DATE de chaque ligne, jamais d'un compteur** : c'est ce qui le rend juste même quand
une **journée entière** manque (un compteur incrémenté ligne à ligne aurait décalé les week-ends
d'un jour). Deux dérivations, parce que les deux sources n'ont pas la même notion de date :
- Open-Meteo Archive : `(date − première_date).days * 24 + heure`, exact ;
- PVGIS TMY : comptage par **changement de (mois, jour)** — une TMY assemble des mois issus
  d'**années source différentes** (déjà documenté dans `_parse_pvgis_timestamp`), donc une
  soustraction de dates sauterait des années entières d'un mois à l'autre. Test dédié : 31 décembre
  2011 suivi du 1ᵉʳ janvier 2007 doivent rester deux jours consécutifs de l'année type.

Côté serveur, `slot` est calculé une fois en tête de boucle et sert au planning **et** aux volets
(qui dupliquaient l'expression). Piège du Lot V réappliqué : `point.get('hour_index')` puis test
`is not None` — le serializer (`default=None`) rend la clé toujours présente, donc `.get(clé, repli)`
ne retomberait jamais sur le repli. `heure_debut` reste le repli pour une série collée à la main.

**Le champ voyage dans le CSV (7e colonne), pas dans un tableau parallèle.** La série récupérée est
aplatie en CSV puis relue par `parseWeather()` — c'est ce qui la rend éditable. Un tableau parallèle
d'index se désaligne dès que l'utilisateur supprime une ligne, c'est-à-dire sous exactement
l'opération qui cause le bug qu'on corrige. Attachée à sa ligne, l'information reste juste quoi
qu'il arrive aux autres. Même précédent que `wind_m_s` (6e colonne, Lot R), même garde contre
`Number('') === 0`.

`computeThermostatSetpoints` prend désormais un **tableau d'heures absolues** au lieu du couple
(`heureDebut`, `nHours`) : la signature elle-même interdit de reconstruire l'heure depuis la
position. Et `heureDebut` est **désactivé dans l'interface** dès que la série porte ses propres
heures, avec la raison affichée — plutôt que de laisser un réglage visible qui ne fait plus rien.

#### Vérification
**9 tests backend** : `hour_index` égal à la position quand rien ne manque, préservé avec 3 heures
sautées (position 8 → heure 11, dernière → 23 et non 20), correct sur plusieurs jours, correct avec
une journée entière absente (24ᵉ point → 48 et non 24), TMY insensible aux années source ; et côté
solveur, planning non décalé sur série trouée, `hour_index` prioritaire sur un `heure_debut`
incohérent, `heure_debut` toujours actif sans `hour_index` (non-régression du Lot Q), volets suivant
le même créneau (test séparé : ils entrent dans `K`, pas seulement dans `F`).

**Mutation testing** sur le point de correction (retour à l'indexation par position) : **3 des 4
tests du solveur échouent**, le quatrième étant justement le test de non-régression qui doit rester
vert. 117/117 au total depuis l'image reconstruite.

**Vérifié en navigateur réel** (13/13) sur le cas du bug — série amputée de 3 heures : 7 colonnes
dans le CSV, 3ᵉ ligne portant l'heure 5 et non sa position 2, dernière portant 23 et non 20,
« Heure de début » désactivée avec explication, et surtout **inspection du payload réellement envoyé
à `/calcul-3d/`** : la frontière jour/nuit du profil scolaire tombe à 7 h réelles (position 4) et
non à la position 7. Tout le chemin client est donc exercé pour de vrai, pas seulement simulé.

#### Reste ouvert
`hour_index` est en **UTC**, comme la série météo — le décalage heure locale reste entier, c'est
l'objet de l'AB4. Une série collée à la main ne porte pas d'heures et continue de dépendre de
`heure_debut` : correct, mais l'utilisateur doit encore le régler à la main dans ce cas.

### AB2 — Bilan énergétique complet sur le dashboard (C2) ✅ livré le 2026-08-09
Accumuler, dans la boucle horaire de `run_building_simulation`, les quatre canaux aujourd'hui absents
(solaire transmis, apports internes, ventilation, cadres), en plus de l'existant, et les renvoyer
dans `job.result`. Puis remplacer les deux tuiles actuelles par un bilan par poste — et vérifier que
la somme des postes + variation de stockage ≈ HVAC, ce qui donne en prime un **contrôle de
conservation d'énergie visible par l'utilisateur** (le Lot F a montré que c'est le meilleur détecteur
de régression disponible). Renommer la tuile actuelle « Flux entrant par les surfaces d'enveloppe »,
qui est ce qu'elle mesure réellement.

Attention au piège documenté au Lot R : une identité de conservation peut être tautologique
vis-à-vis du terme qu'elle est censée vérifier. Ici, les canaux ajoutés sont des termes de `F`, donc
l'identité reste légitime (même raisonnement que le Lot U).

#### Ce qui a été fait
Six postes accumulés dans la boucle horaire, chacun une **puissance nette vers l'air évaluée en fin
de pas** (cohérent avec le schéma implicite et avec `envelope_flux_w` existant) : surfaces
d'enveloppe, solaire transmis (Lot U), apports internes (Lots H/Q), renouvellement d'air (Lots G/Q),
cadres de fenêtre (Lot I), chauffage/climatisation. Plus la variation de stockage
`c_air_int·(T_air,fin − T_air,début)` — qui télescope, donc seuls les deux bouts comptent.

L'identité vérifiée est **la ligne du nœud d'air du système résolu** :

```
C_air/Δt·(T_air,n+1 − T_air,n) = Σ hᵢAᵢ(T_surf,i − T_air) + Σ frame_g(t_bound − T_air)
                                 + g_vent(t_ext − T_air) + Q_int + Σ solaire_transmis + hvac
```

Elle n'est **pas tautologique** (piège du Lot R) : chaque poste est reconstruit à partir des
températures résolues et des paramètres physiques, jamais relu depuis `F`. Le `hvac` du mode
thermostat est exactement le résidu de cette ligne — c'est ce qui fait boucler l'ensemble par
construction, et donc ce qui rend une valeur non nulle de `closure_error_kwh` significative.

Deux termes ne sont connus que dans `_assemble_F_hour` (le solaire transmis, et la somme
`Σ frame_g[i]·t_boundary[i]` — chaque cadre suivant le `t_boundary` de SON triangle) : remontés par
un paramètre `diagnostics` optionnel plutôt qu'en changeant la valeur de retour, pour ne rien coûter
quand il n'est pas demandé. Le reste se reconstitue dans la boucle, qui connaît déjà `g_vent`,
`apports_internes_w` et `T_air`.

**`balance` vaut `None` en mode « Imposée »** : la ligne du nœud d'air y est écrasée par Dirichlet,
donc aucun de ces postes n'agit sur la solution (même raisonnement que `g_vent`/`apports_internes_w`,
déjà ignorés dans ce mode). Afficher leurs valeurs nominales laisserait croire à un effet inexistant ;
l'interface affiche à la place la raison et invite à passer en « Libre » ou « Thermostat ».

Côté dashboard : tableau par poste (avec colonne kWh/m² quand la surface de référence est
renseignée), pied de tableau portant la variation de stockage, et une ligne de contrôle qui annonce
explicitement le bouclage — **un contrôle de conservation d'énergie visible par l'utilisateur**, pas
seulement par la suite de tests. Les deux tuiles d'origine sont renommées « Enveloppe — flux
entrant / sortant » : elles restent utiles, mais elles ne s'appellent plus « gains » et « pertes »
alors qu'elles ne portent qu'un poste sur six.

#### Vérification
**8 tests** : bouclage exact (delta 1e-9 kWh) enveloppe seule, tous canaux actifs, thermostat en
chauffage **et** en climatisation ; oracle indépendant sur les apports internes (300 W × 12 h =
3,6 kWh exactement) ; non-régression stricte du poste « enveloppe » contre l'intégrale de
`envelope_flux_w` et contre `flux_positive + flux_negative` ; `balance is None` en mode imposé ; et
le solaire transmis dépassant le canal enveloppe sur une paroi vitrée ensoleillée — c'est-à-dire
la démonstration que le poste caché jusqu'ici pouvait être le premier du bilan.

Découverte au passage, en écrivant le test thermostat : avec 800 W/m² de direct derrière un vitrage
τ = 0,87, le solaire transmis l'emporte largement sur les déperditions **même à −5 °C dehors**, et le
thermostat doit **refroidir**. L'attente initiale (« il faut chauffer ») était fausse ; c'est
exactement le genre de lecture que les deux tuiles d'origine rendaient impossible. Les deux sens sont
désormais testés séparément. 125/125 depuis l'image reconstruite, vérifié en navigateur réel (12/12).

#### Reste ouvert
Le mode « Imposée » pourrait exposer un bilan lui aussi, en calculant le résidu de la ligne Dirichlet
(exactement la puissance qu'il faut injecter pour tenir `t_int`) — c'est la même mécanique que le
`hvac` du thermostat, non faite ici. Le bilan est global sur la période : pas de décomposition
mensuelle ni de courbe temporelle par poste.

### AB3 — Plancher sur terre-plein : résistance de sol et découplage du vent (C4) ✅ livré le 2026-08-09
1. Découpler `h_e` du sol : un triangle `boundary='ground'` ne doit **jamais** utiliser le `h_e`
   dérivé du vent. Correctif minimal dans `_h_e_diagonal`/`_assemble_F_hour` : forcer une valeur
   propre au sol pour ces triangles.
2. Introduire une **résistance de sol** `r_ground` (m²·K/W) en série, paramètre du payload avec une
   valeur par défaut documentée (ordre de grandeur ISO 13370). Conductance résultante
   `1/(1/h_sol + r_ground)`, exactement le même patron que le `delta_r` d'un volet fermé (Lot J) —
   le mécanisme existe déjà, il n'y a pas de nouvelle structure à inventer.
3. Ajouter au catalogue (`seed_paroi_catalogue.py`) au moins un **plancher bas sur terre-plein**
   (dalle béton + isolant sous chape + revêtement) par génération réglementaire, `is_glazing=False`.
   Sans ça, le mode simplifié et le Lot Y continuent d'imposer un mur au groupe `sol`.
4. Test : identité exacte entre un triangle `ground` avec `r_ground` et un mur 1D dont on a ajouté
   manuellement la même résistance — même méthode que le test `GroundBoundaryTest` du Lot K.

#### Ce qui a été fait
Les quatre étapes. La conductance côté extérieur d'un triangle `ground` vaut désormais **`1/r_ground`
seule** — pas `h_e`, pas un `h_e` corrigé : un dallage est en contact direct avec la terre, il n'y a
aucun film convectif à modéliser. Elle est donc **constante dans le temps** : ni vent (Lot R), ni
planning de volets (Lot J, dont l'application à un plancher n'avait aucun sens et est désormais
impossible par construction). `h_e_vec` est construit en une seule expression qui traite les trois
cas (sol / volet fermé / exposé), au lieu de deux branches auxquelles il aurait fallu en greffer une
troisième.

`DEFAULT_R_GROUND = 0.5` m²·K/W — le **bas** de la fourchette usuelle ISO 13370 (~0,5 à 3 selon la
dimension caractéristique B' = A/(0,5·P) et la conductivité du sol), choix volontairement
conservateur. Réglable page Calcul 3D, à côté de la température de sol. Minimum 0,05 côté serializer
plutôt que 0 : la conductance vaut `1/r_ground`, et un contact « parfait » avec la terre n'a pas de
sens physique.

Trois **planchers bas sur terre-plein** ajoutés au catalogue (RT2005/RT2012/RE2020 : dalle béton
15 cm + polystyrène extrudé 40/80/120 mm + chape 5 cm, U indicatifs 0,51 / 0,32 / 0,23 W/m²·K).
C'était le manque signalé au Lot W : le mode simplifié impose de choisir un modèle opaque pour le
groupe `sol`, et la liste n'offrait que des murs et des toitures. Couches ordonnées de l'extérieur
(côté terre) vers l'intérieur, comme partout ailleurs.

**Ampleur du correctif, mesurée en réel** (dallage 100 m², RT2012, 30 jours à −5 °C dehors / 20 °C
dedans / sol à 12 °C) : 210,1 kWh de déperditions avec l'ancien couplage (`h_e = 25`, soit
R = 0,04 m²·K/W) contre **170,4 kWh** au défaut actuel — **19 % de moins**, et 60 % de moins si l'on
retient le haut de la fourchette ISO. La surestimation était donc réelle et loin d'être marginale.

#### Vérification
**9 tests.** Le cœur : un triangle `ground` donne des flux **identiques** entre un vent nul et un
vent de 25 m/s en `h_e_dynamic` — accompagné du test miroir (une paroi `exterior_air` doit, elle,
toujours réagir au vent), sans lequel le premier passerait pour la mauvaise raison. Idem pour `h_e`
constant (5 contre 90 : aucun effet sur un triangle au sol). Identité **exacte** (1e-9) entre un
triangle `ground` à `r_ground` et un triangle `exterior_air` à `h_e = 1/r_ground` sous météo
constante à `t_ground` — la définition d'une résistance en série, vérifiée hors du code qui la pose.
Plus : sens physique (plus de résistance ⇒ moins de pertes), quantification de l'écart avec l'ancien
comportement, et volet sans effet sur un plancher. Deux tests de catalogue (ordre des couches,
`tau + r + alpha = 1`, passage du serializer).

**Un test existant a dû être corrigé, et c'est le bon signe** : `GroundBoundaryTest`
(Lot K) construisait sa référence 1D avec `h_e = 25`, ce qu'un triangle au sol ne voit plus. Sa
référence utilise désormais `h_e = 1/r_ground`, en gardant `h_e = 25` dans le payload 3D — le test
vérifie donc **en plus**, maintenant, que `h_e` est bien ignoré pour ce triangle. 134/134 depuis
l'image reconstruite, catalogue rejoué (14 modèles).

#### Reste ouvert
`r_ground` est un paramètre saisi, pas un calcul : ISO 13370 le fait dépendre de la géométrie réelle
du plancher (B' = A/(0,5·P)), que l'application pourrait dériver de ses triangles `ground` (aire
connue, périmètre calculable depuis les arêtes de bord). Ce serait la suite naturelle, non faite ici.
Pas de variation saisonnière de `t_ground` (déjà noté au Lot K), ni de déphasage : la terre est une
source à température constante et sans inertie. Le vitrage d'un cadre de fenêtre posé sur un triangle
`ground` (combinaison absurde mais possible) suit toujours `t_ground` via `frame_g`.

### AB4 — Temporalité d'usage : préremplir heure et jour depuis la période chargée (C5) ✅ livré le 2026-08-09
Une fois AB1(b) fait, `hour_of_day` est déjà transporté ; reste le **jour de la semaine** du premier
point (`jourDebut`, Lot V) et la question **UTC vs heure locale**. L'application connaît la
`start_date` demandée et les coordonnées : elle peut déduire les deux. À trancher : convertir la série
en heure locale à l'affichage/à l'usage (en gardant l'UTC pour la position solaire, qui doit le
rester), ou afficher explicitement « heures en UTC » et laisser l'utilisateur décaler. La première
option est la bonne pour un utilisateur, la seconde est plus honnête vis-à-vis de la donnée —
demander avant de coder.

#### Ce qui a été fait
**Arbitrage tranché avec l'utilisateur** : décalage **fixe**, prérempli automatiquement (option A des
trois proposées), après vérification empirique de ce que la source offre réellement.

Mesuré avant de choisir : Open-Meteo `timezone=auto` renvoie **un seul `utc_offset_seconds`**,
identique pour une requête de janvier et une de juillet (+7200 pour Paris), et une année complète y
compte 8784 heures continues — **pas de journée de 23 h ni de 25 h**. La vraie heure civile aurait
donc imposé un mapping lat/lon → fuseau (dépendance supplémentaire) *et* cassé l'hypothèse d'un
planning à 24 créneaux deux fois par an, pour une heure d'écart. Écarté sur cette base, pas de
principe.

`hour_index` est désormais en **heure locale** ; `dt_utc` reste l'instant physique utilisé pour la
position solaire, qui n'est pas concernée — c'est la propriété à ne pas casser, et elle est testée
explicitement. Le décalage est détecté par une **sonde minimale** (`api.open-meteo.com/v1/forecast`,
un jour, dont seul l'en-tête est lu) plutôt qu'en changeant la requête d'archive : elle sert **aussi**
au chemin PVGIS TMY, qui n'expose aucune notion de fuseau. Best-effort — si elle échoue, repli sur
UTC avec un avertissement, plutôt que d'annuler toute la récupération météo.

`utc_offset_h` est **optionnel** côté API : `null` = détection automatique, une valeur = imposée. Le
job renvoie celui qu'il a effectivement utilisé, que le client reprend dans son champ — donc
prérempli **et** modifiable ensuite, sans endpoint supplémentaire ni valeur dupliquée.

`jourDebut` du calendrier d'occupation est aligné automatiquement sur la date de début chargée
(`getUTCDay()` remis en base 0 = lundi) : l'utilisateur n'a plus à savoir quel jour de la semaine
tombait le 1ᵉʳ janvier de l'année choisie.

**Limite assumée et écrite dans l'interface** : la valeur détectée est le décalage en vigueur
*aujourd'hui* à cet endroit, pas à la date de la série — demandée en août pour Paris, elle vaut +2, si
bien qu'une série de janvier est étiquetée une heure en avance sur l'heure civile de l'époque. L'écart
reste **borné à une heure sur la moitié de l'année**, contre **une à deux heures en permanence** avant
ce lot ; et le champ est là pour trancher autrement.

#### Vérification
**7 tests** : décalage appliqué à `hour_index` (00:00 UTC → 01:00 locale, et la dernière heure qui
bascule au jour suivant), offset nul = comportement du Lot AB1 inchangé, décalage négatif ne
produisant jamais d'index négatif (le serializer exige ≥ 0), **position solaire strictement identique
avec et sans décalage** (12 décimales — la propriété centrale), année complète restant dans les
bornes du serializer (relevées de 8783 à 8807, un décalage positif reportant les dernières heures sur
un jour de plus), TMY décalée de la même façon, et validation du champ.

Un test existant a dû être mis à jour, et c'est le bon signe : `TmyFallbackTest` assertait la
signature complète de l'appel de repli — il a immédiatement signalé que le nouveau paramètre devait
traverser ce chemin aussi. L'assertion reste stricte, enrichie du nouveau paramètre.

**Vérifié en réel** contre l'API : offsets corrects sur trois continents (Paris +2, Montréal −4,
Sydney +10) et série réelle du 10 janvier à Paris étiquetée en local, position solaire inchangée.
141/141 depuis l'image reconstruite ; navigateur réel 6/6 (champ vide par défaut, `utc_offset_h: null`
réellement envoyé, valeur utilisée reprise dans le champ, heures décalées dans le CSV, jour de la
semaine prérempli sur « Mercredi » pour le 13 mars 2024).

#### Reste ouvert
Pas de changement d'heure (décision assumée ci-dessus). Une série collée à la main ne porte pas
d'heures et dépend toujours de `heure_debut`, réglé manuellement. Le décalage n'est appliqué qu'aux
séries récupérées automatiquement : il n'existe aucun moyen de re-décaler après coup une série déjà
chargée, sinon en la récupérant à nouveau.

---

## Lot AC — Page Théorie : mettre les limites à jour ✅ livré le 2026-08-09

Dernier lot de la phase, une fois que les correctifs ci-dessus ont figé ce qui est vrai.

### Étapes
1. **Corriger les deux affirmations devenues fausses** (B1) : le profil horaire d'apports internes
   existe (Lot Q) ; la sommation multi-parois sur le nœud d'air existe (Lot D) — la limite réelle est
   le **mono-zone**, à reformuler comme telle.
2. **Ajouter les onze limites de B2**, dans l'encadré « Hors périmètre » pour celles qui sont des
   exclusions nettes, et dans le corps des sections concernées pour celles qui qualifient un terme
   existant (h_e global convection+rayonnement en section 06 ; τ/α à incidence normale en section 05 ;
   solaire transmis instantané en section 05 ; `c_air_int` sans résistance en section 08).
3. **Documenter les termes livrés en phase 2** qui n'apparaissent nulle part sur la page :
   renouvellement d'air, apports internes, cadre de fenêtre, contact au sol, occultations mobiles,
   h_e/h_i dynamiques, plannings horaires, calendrier d'occupation. Une section « Du modèle 1D au
   bâtiment complet » listant chaque terme, où il s'insère (`K` ou `F`, nœud de surface ou nœud
   d'air) et sous quelle hypothèse, serait plus utile que de disséminer les mentions.
4bis. **Paroi mitoyenne** (limite 11bis, constatée au Lot X) : écrire explicitement qu'aucun
   troisième `boundary` n'existe et que le mur partagé avec un mitoyen déperd comme une façade
   extérieure. Si le sujet est un jour repris comme chantier et non comme limite documentée, il est
   de la même famille que le couplage multi-locaux de la section « Hors scope » (un `boundary`
   supplémentaire sans convection extérieure, soit adiabatique, soit vers une température de local
   voisin donnée) — nettement plus léger que le multi-zone complet, puisqu'il n'introduit aucun
   second nœud d'air.
4. **Documenter les termes ajoutés en phase 3** : filtrage du bâtiment étudié (Lot X), végétation à
   transmittance constante et non saisonnière (Lot Z), terrain occultant sans albédo (Lot AA),
   résistance de sol (Lot AB3).
5. Distinguer clairement, dans l'encadré, **trois catégories** plutôt qu'une liste unique — c'est ce
   qui manque le plus aujourd'hui : (i) hors périmètre définitif (ponts thermiques, multi-zone,
   humidité), (ii) approximation quantifiée et assumée (diffus isotrope, τ à incidence normale,
   grille d'ombrage à 15°), (iii) extension identifiée et chiffrable (albédo, IR ciel, saisonnalité
   de la végétation).

### Portée
Uniquement `theorie.component.html`. Aucun code, aucun test — mais c'est le lot qui détermine si un
tiers peut juger de ce que vaut un résultat produit par cet outil.

### Ce qui a été fait
Les cinq étapes.

**Les deux affirmations fausses corrigées** : le paragraphe « apports internes constants sur toute la
durée du calcul » a disparu (le Lot Q l'a livré) et renvoie désormais à la nouvelle section 12 ; le
point « couplage multizone » de la section Extensions désignait la mauvaise limite — la sommation
multi-parois est faite depuis le Lot D, ce qui reste une limite c'est le **mono-zone**, reformulé
comme tel avec ce qu'il faudrait pour en sortir (découpage en zones, et un type de paroi qui n'existe
pas : un refend, sans face extérieure).

**L'encadré de portée réécrit en trois catégories**, qui est le vrai apport de ce lot : *hors
périmètre* (demanderait une autre structure de modèle), *approximation quantifiée* (le terme existe,
sa valeur est simplifiée), *extension identifiée* (chiffrable, pas faite) — plus un quatrième bloc,
« et ce que les résultats ne sont pas ». Une liste unique et indifférenciée, comme avant, ne
permettait pas de distinguer ce qui est un choix définitif de ce qui est une valeur approchée. Les
douze limites de la section B y figurent, chacune avec **le sens de l'erreur** plutôt qu'une simple
mention : refroidissement radiatif nocturne structurellement impossible, apports solaires par vitrage
surestimés au-delà de 60° d'incidence, apports diffus sous-estimés en milieu dense, besoins d'une
maison mitoyenne surestimés, ombrage hivernal surestimé sous couvert végétal…

**Nouvelle section 12 « Du modèle 1D au bâtiment complet »** : un tableau de **14 termes** — pour
chacun, où il entre (`[K]`, `{F}`, nœud d'air, nœud de surface) et sous quelle hypothèse. C'est ce
qui manquait le plus : un lecteur qui s'en tenait à la page ignorait que la ventilation, les apports
internes, le cadre, le sol, les volets, les plannings, le calendrier, la végétation ou le relief
existaient. Le sommaire est renuméroté (12 → 13 → 14).

### Vérification
**26/26 en navigateur réel** : disparition des deux affirmations fausses, présence des trois
catégories, et présence individuelle des douze limites et du bloc « besoins ≠ consommations » ;
tableau de synthèse à 14 lignes avec ses termes clés ; sommaire à 14 entrées. Rendu contrôlé
visuellement (encadré et section 12). `ng build --configuration production` propre.

### Reste ouvert
La page reste un document sur le **modèle**, pas un mode d'emploi : elle ne dit pas quelle valeur
saisir ni comment interpréter un écart. Les valeurs indicatives (catalogue de parois, profils
d'occultation, de ventilation, d'usage, de végétation) n'y sont pas reproduites — elles vivent dans
les pages qui les proposent.

---

## Ordre recommandé

1. ~~**Lot W**~~ — ✅ livré le 2026-08-09. A remis en service le mode simplifié **et** corrigé au
   passage une répartition du vitrage qui vitrait intégralement toute paroi réglée au-delà de 50 %.
2. ~~**Lot X**~~ — ✅ livré le 2026-08-09. Vérifié en réel contre l'IGN sur deux zones : le bâtiment
   étudié écarté, et lui seul. A aussi fait afficher les avertissements de génération
   d'environnement, que le frontend jetait depuis toujours.
3. ~~**Lot AB1**, **AB2**, **AB3**, **AB4**~~ — ✅ tous livrés le 2026-08-09. AB2 a fait apparaître
   le solaire transmis, qui pouvait être le premier gain du bilan sans jamais s'afficher ; AB3 a
   chiffré la surestimation des déperditions par le sol (−19 % sur un dallage RT2012 mesuré en réel)
   et ajouté les planchers bas au catalogue ; AB4 a été tranché avec l'utilisateur après mesure de
   ce que la source météo permet réellement.
4. ~~**Lot AA**~~ — ✅ livré le 2026-08-09 (usages 1 et 3 ; usage 2 laissé ouvert, voir sa section).
5. ~~**Lot Y**~~ — ✅ livré le 2026-08-09. Composant de recherche factorisé et partagé avec le mode
   simplifié ; géoréférencement complet prérempli, rotation nord nulle par construction.
6. ~~**Lot Z**~~ — ✅ Z1+Z2 livrés le 2026-08-09 (occulteurs à transmittance). **Z3 (saisonnalité)
   laissé ouvert** : il introduirait une notion de date calendaire côté serveur, refusée
   délibérément depuis le Lot V — décision d'architecture à prendre à part.
7. ~~**Lot AC**~~ — ✅ livré le 2026-08-09. Encadré de portée réécrit en trois catégories avec le
   sens de l'erreur pour chaque limite, et nouvelle section 12 recensant les 14 termes ajoutés
   depuis le modèle 1D.

**Phase 3 terminée.** Seuls restent ouverts, explicitement et par décision : le **Lot Z3**
(saisonnalité de la végétation — introduirait une date calendaire côté serveur), l'**usage 2 du
Lot AA** (obstacles OSM posés sur le relief réel), et les points « Reste ouvert » de chaque lot.

Et, transversalement à toute la phase : **vérifier en navigateur réel**, systématiquement. C'est le
seul point commun entre le bug 2.2.1 et le fait que personne ne l'a vu pendant un jour et demi.

---

## Trous de logique et frictions d'usage restants — relevé du 2026-08-09 (après phase 3)

Relevé demandé par l'utilisateur une fois la phase 3 close. **Rien n'est corrigé ici** : c'est un
inventaire, à trancher lot par lot. Les « Reste ouvert » propres à chaque lot ne sont pas répétés.

> **Mis à jour le 2026-08-09** après les Lots AD, AE et AF : les entrées barrées sont traitées.
> Restent ouverts L5, L6, L7 et U3 à U6, plus le Lot Z3 (saisonnalité de la végétation) et la
> paroi mitoyenne (limite 11bis, documentée en Théorie mais non implémentée).

### Trous de logique

**L1.** ~~Modifier un environnement n'invalide pas l'ombrage des bâtiments qui l'utilisent~~ ✅ corrigé au Lot AD
`EnvironmentSerializer.update()` remplace `instance.envelope` et sauvegarde — sans jamais toucher au
`sun_visibility_stale` des `Building` qui pointent dessus. Ajouter de la végétation ou du relief à un
environnement existant puis relancer un calcul donne donc un résultat calculé contre l'**ancienne**
géométrie, sans aucun signe. Le mécanisme existe et fonctionne dans les deux autres cas (enveloppe
modifiée, lien d'environnement changé) : il ne manque que la propagation environnement → bâtiments.
Correctif : `Building.objects.filter(environment=instance).update(sun_visibility_stale=True)` dès que
l'enveloppe change. Le plus grave de la liste — silencieux, et sur le chemin que les Lots Z et AA
viennent justement de rendre attractif.

**L2.** ~~Le mode simplifié promet un calcul immédiat que le serveur refusera~~ ✅ corrigé aux Lots AD et AE
L'étape 4 affiche « rien à faire de plus […] lancez le calcul ». Or un `Building` neuf a
`sun_visibility_stale = True`, Calcul 3D part en `'precomputed'` par défaut, et `BuildingCalculView`
renvoie **400 « L'ombrage précalculé est périmé »**. L'utilisateur suit l'assistant à la lettre et se
heurte à un refus. Trois correctifs possibles, à trancher : lancer le précalcul depuis l'assistant ;
basculer par défaut en temps réel pour un bâtiment sans environnement (auto-ombrage seul, peu
coûteux) ; ou corriger le texte et ajouter l'étape manquante.

**L3.** ~~Le planning horaire ignore le jour de la semaine, le calendrier d'occupation non~~ ✅ corrigé au Lot AG
Depuis le Lot V, les consignes de thermostat distinguent jours ouvrés, week-ends et vacances. Le
planning du Lot Q (ventilation, apports internes, volets) reste un « jour type » indexé
`hour_index % 24`. Un bureau dont le thermostat passe en hors-gel le dimanche continue donc d'être
**ventilé au débit d'occupation** et de recevoir ses apports internes ce jour-là. L'incohérence
n'existait pas avant le Lot V — c'est lui qui a introduit un calendrier d'un seul côté. Correctif
naturel : deux profils de planning (ouvré / non-ouvré), réutilisant le calendrier déjà saisi.

**L4.** ~~La végétation est posée à z = 0, les bâtiments suivent le terrain~~ ✅ corrigé le 2026-08-09 (résout aussi l'usage 2 du Lot AA)
`generate_vegetation_mesh` place tous les volumes végétaux à l'altitude locale 0, alors que les
bâtiments IGN sont calés sur `altitude_minimale_sol − ground_z_ref` et que le maillage de terrain
(Lot AA) reproduit le relief. Sur un site en pente, les arbres flottent ou s'enterrent — l'ombrage en
pâtit là où il compte le plus (soleil rasant). Le code porte une ligne morte à supprimer
(`base_z = -(ground_z_ref or 0.0) * 0.0`). Se traite avec l'usage 2 du Lot AA, resté ouvert.

**L5. Le garde-fou du mode temps réel ne compte pas la végétation**
`MAX_REALTIME_RAYCAST_OPS` borne `triangles × heures`, ce qui reflétait le coût quand il n'y avait
qu'une passe de rayons. Depuis le Lot Z il y en a une seconde (végétation, `multiple_hits`, plus
chère par rayon) : le garde-fou sous-estime le coût réel, alors qu'il existe précisément pour
protéger le worker partagé du lab.

**L6. Chaque génération d'environnement en crée un nouveau, sans jamais nettoyer**
`generate_environment_for_building` crée un `Environment` « Auto — <bâtiment> — <date> » et remplace
le lien. Régénérer trois fois laisse deux environnements orphelins, indistinguables au nom près, et
rien n'indique lequel est réellement lié.

**L7. Le mode simplifié laisse un bâtiment incomplet si on l'abandonne**
`createAndRefine` crée le `Building` dès l'étape 2. Quitter à l'étape 3 laisse un bâtiment sans
aucune paroi assignée dans la liste. Sans conséquence physique, mais il encombre.

### Frictions d'usage

**U1.** ~~L'ombrage doit être relancé à la main~~ ✅ corrigé au Lot AD (bouton sur Calcul 3D) et au Lot AE (automatique en simplifié). Calcul 3D se
contente de refuser ; il faut savoir revenir sur la page Bâtiment. Un bouton « relancer le précalcul »
dans le message d'erreur supprimerait l'aller-retour — et couvrirait aussi L1 et L2.

**U2.** ~~`surface_ref_m2` saisie à la main~~ ✅ corrigé au Lot AE (renseignée depuis l'emprise réelle). Pour un bâtiment
issu de la recherche ou du générateur de boîte, l'aire au sol est calculable exactement (le mode
simplifié la calcule déjà pour le volume). Sans elle, tout le dashboard kWh/m² — le seul repère
chiffré de l'app — reste indisponible.

**U3. Aucun historique des calculs.** Le `Job` est persisté, mais l'interface ne le retrouve que tant
qu'on reste sur la page : quitter Calcul 3D perd le résultat, et il faut relancer (jusqu'à ~30 s)
pour le revoir. Comparer deux variantes oblige à noter les chiffres à la main.

**U4. Coordonnées à saisir au clavier, sans carte.** Toutes les entrées géographiques demandent une
latitude/longitude décimale, à obtenir ailleurs ; une faute de frappe ne se voit qu'au résultat.

**U5. Plusieurs paramètres physiques sans repère.** `c_air_int` a son aide (Lot P) ; `dx_max`,
`r_ground`, `t_ground`, `h_e`/`h_i` constants n'ont qu'un défaut et une phrase. Rien ne dit lesquels
sont sans conséquence et lesquels changent le résultat d'un facteur deux.

**U6. Le mode « temps réel » n'est pas proposé au moment où il devient nécessaire.** C'est la réponse
à la discrétisation à 15° (documentée au Lot AC), mais l'interface le présente comme une simple
alternative plus lente.

### Hors bilan-thermique — relevé en vérifiant les comptes E2E

**7 des 14 apps du lab n'ont aucun `frontend/e2e/cloisonnement.spec.ts`** : `analyse-lora`,
`app-builder`, `arbre-genealogique`, `atelier-3d`, `conciergerie`, `lab-admin`, `restauration`. Le
runner répond `[]` pour elles — **sans erreur ni avertissement**, exactement comme une app dont tous
les tests passeraient. Or `CLAUDE.md` décrit ce test comme le contrôle automatisé du cloisonnement, et
le lab est exposé sur Internet. À traiter dans `dev/`, pas ici.

---

## Lot AD — Continuité de l'UI : chaque page son rôle, aucune redondance ✅ livré le 2026-08-09

Audit demandé le 2026-08-09, après le constat que la végétation était introuvable depuis la page
Environnement. **Rien n'est modifié ici** : constat + plan, à valider avant d'exécuter.

### Cible énoncée par l'utilisateur
- **Mode simplifié** : tout le parcours sur UNE page. ✅ déjà le cas.
- **Parcours avancé** : une page pour le **bâtiment**, une page pour l'**environnement**, puis une
  page qui **les regroupe** — en évitant les redondances entre les deux (le bâtiment étudié ne doit
  pas se retrouver aussi dans l'environnement, ni intersecter ses obstacles) — et qui porte les
  variables de calcul, la météo et le calendrier.

### Constat : le découpage actuel ne correspond pas

| Page | Ce qu'elle contient aujourd'hui | Où ça devrait être |
|---|---|---|
| **Bâtiment** | liste, édition, géoréférencement, recherche, boîte, raffinage, assignation | ✅ à sa place |
| **Bâtiment** | **« Ombrage » : choix de l'environnement lié + lancement du précalcul** | ➜ Calcul 3D (c'est l'étape qui REGROUPE bâtiment et environnement) |
| **Bâtiment** | **« Générer l'environnement pour ce bâtiment »** (aligné, filtre le bâtiment étudié, terrain, végétation) | ➜ Environnement |
| **Environnement** | liste, import de fichier, **« Ou générer depuis des coordonnées »** (non aligné, sans filtrage, sans terrain) | ➜ à fusionner avec le générateur ci-dessus |
| **Calcul 3D** | sélection du bâtiment, paramètres, intérieur, calendrier, ventilation, apports, planning, **mode d'ombrage**, météo, résultats | ✅ à sa place |

### Les trois redondances réelles

**R1. Deux générateurs d'environnement, dans deux pages, dont un strictement inférieur.**
Celui de la page Bâtiment aligne sur le repère du bâtiment, écarte le bâtiment étudié (Lot X),
sait charger terrain et végétation, crée l'`Environment` et le lie. Celui de la page Environnement
ne fait rien de tout ça — son propre texte annonce « ne garantit **pas** l'alignement ». Deux
chemins pour la même intention, avec des résultats de qualité différente et aucune indication de
lequel choisir. **C'est la redondance principale.**

**R2. Le filtrage du bâtiment étudié n'existe que sur un des deux chemins.**
Conséquence directe de R1 : un environnement produit depuis la page Environnement contient le
bâtiment étudié en double (il est lui-même dans IGN/OSM), puisque `self_envelope` n'y est pas fourni
— exactement ce que le Lot X corrige sur l'autre chemin. C'est le point que l'utilisateur soulève
(« éviter que le bâtiment soit dans bâtiment ET dans environnement »).

**R3. L'étape qui regroupe les deux est éclatée sur deux pages.**
Le lien bâtiment↔environnement et le précalcul d'ombrage — qui n'ont de sens que pour un COUPLE
(bâtiment, environnement) — vivent sur la page Bâtiment, tandis que le mode d'ombrage
(précalculé/temps réel) vit sur Calcul 3D. D'où le parcours actuel : Bâtiment (lier + précalculer)
→ Calcul 3D (choisir le mode) → refus si périmé → retour Bâtiment. C'est la cause commune des
points L1, L2 et U1 du relevé ci-dessus.

### Plan proposé
1. **Déplacer la génération d'environnement vers la page Environnement**, en gardant la version
   complète (alignée, filtrante, terrain + végétation) et en supprimant l'autre. Elle a besoin d'un
   bâtiment de référence : un sélecteur « aligner sur le bâtiment … » sur cette page, avec le cas
   « aucun » qui redonne le comportement autonome actuel — et dit alors explicitement ce qu'on perd.
2. **Déplacer le lien bâtiment↔environnement et le précalcul d'ombrage vers Calcul 3D**, dans le
   bloc « Ombrage » qui existe déjà et ne porte que le mode. Le précalcul s'y lance donc là où son
   absence bloque — ce qui règle L2 et U1 sans texte d'excuse.
3. **Propager l'invalidation** (L1) : modifier un environnement doit périmer l'ombrage de tous les
   bâtiments qui l'utilisent.
4. **Généraliser le filtrage d'intersection** (R2) : aujourd'hui seul le bâtiment ÉTUDIÉ est écarté,
   au-delà de 30 % de recouvrement. Un voisin qui chevauche partiellement l'enveloppe modélisée (OBJ
   approximatif) reste interpénétré. À trancher : signaler, écarter, ou laisser.
5. La page Bâtiment ne garde alors que le bâtiment ; la checklist du Lot N est à réaligner sur le
   nouveau découpage (« environnement lié » et « ombrage à jour » ne s'y jugent plus).

### Coût et risque
Essentiellement du déplacement de blocs existants entre trois composants, sans changement de
solveur ni d'API — sauf le point 3 (une ligne côté serializer) et le point 4 (à trancher). Le risque
est surtout de casser un parcours en le déplaçant : à vérifier en navigateur réel page par page,
comme les lots W à AC.

### Ce qui a été fait
Les cinq points, avec les deux arbitrages tranchés par l'utilisateur.

**Un seul générateur d'obstacles, sur la page Environnement.** `POST /api/environnements/generer/`
accepte désormais `building_id` (optionnel), `include_vegetation` et `terrain_spacing_m` : fourni, le
bâtiment de référence apporte l'alignement, la mise à l'écart du bâtiment étudié et le rognage ;
absent, on retombe sur l'exploration libre, **et l'interface dit alors explicitement que le bâtiment
se retrouvera en double**. `generate_environment_for_building`, son endpoint, son serializer et son
bloc d'interface ont été supprimés — c'était la redondance R1, et son existence expliquait aussi
pourquoi la végétation restait introuvable depuis la page Environnement.

**Rognage plutôt que suppression** (choix de l'utilisateur). `resolve_against_self()` remplace le
booléen du Lot X par trois issues : `kept`, `self` (écarté), `clipped` (différence géométrique). Un
voisin qui interpénètre l'enveloppe modélisée — cas courant quand elle est approximative — garde
donc la partie qui existe vraiment au lieu d'être perdue en entier ou de s'enfoncer dans le
bâtiment. Le rognage peut couper un obstacle en deux (d'où une **liste** de polygones en retour) ou
créer un **trou** (bâtiment entièrement à l'intérieur d'un obstacle), les deux gérés nativement par
trimesh. Un reste inférieur à 1 m² est écarté : un copeau extrudé sur toute la hauteur coûte des
triangles sans rien masquer.

**Le critère « c'est le bâtiment étudié » a dû changer de forme.** Il rapportait l'intersection au
**minimum** des deux aires — ce qui vaut 1 dès que l'une contient l'autre, confondant deux
situations opposées : un pâté de maisons digitalisé d'un seul tenant CONTIENT le logement étudié
sans être lui, et se retrouvait donc écarté en entier alors que tout le reste du pâté masque
réellement le soleil. Remplacé par un rapport à l'**union** (indice de Jaccard, donc une mesure de
ressemblance) : doublon exact ≈ 1, bloc englobant ≈ 0,1 — le premier est écarté, le second rogné.
Les tests du Lot X ont survécu au changement sans retouche, ce qui confirme qu'ils testaient bien un
comportement et non une implémentation.

**L'étape qui regroupe est passée sur Calcul 3D** : sélection de l'environnement (qui `PATCH` le
bâtiment) et bouton de précalcul d'ombrage, avec le badge d'état. C'est là que leur absence bloque —
la page se contentait auparavant de refuser en renvoyant l'utilisateur en arrière. Règle du même coup
L2 et U1.

**Propagation de l'invalidation (L1)** : `EnvironmentSerializer.update` périme l'ombrage de tous les
bâtiments liés dès que la géométrie change — et **seulement** alors : un simple renommage ne doit pas
imposer un recalcul de plusieurs minutes.

La page Bâtiment ne porte plus que le bâtiment ; sa checklist perd les deux items qui ne s'y jugent
plus.

### Vérification
**8 tests** de rognage (doublon écarté, disjoint et mitoyen intacts, chevauchement partiel rogné au
m² près et sans intersection résiduelle, bloc englobant rogné **avec trou**, obstacle coupé en deux,
copeau écarté, parts réellement extrudables par trimesh) et **2 tests avec base de données** pour
l'invalidation (tous les bâtiments liés périmés, les autres intacts ; renommage sans effet).
173/173.

**Vérifié en réel** : sur un bâtiment volontairement décalé de 3 m à Orléans, 1 obstacle rogné et 42
conservés — et l'avertissement « vérifiez son géoréférencement » se déclenche, ce qui est le bon
signal pour un décalage de cette taille. **Navigateur réel 12/12** : la page Bâtiment ne porte plus
ni ombrage ni générateur mais garde ses outils, la page Environnement expose le sélecteur de
bâtiment avec le bon texte dans les deux cas et transmet bien les trois options, et Calcul 3D
associe l'environnement puis lance le précalcul et voit le badge passer à jour.

### Reste ouvert
Les environnements orphelins s'accumulent toujours (L6) — chaque génération enregistrée en crée un de
plus, et rien n'indique lequel est lié. Le générateur ne propose pas de carte (U4). Le mode
« temps réel » n'est toujours pas suggéré au moment où la discrétisation à 15° devient gênante (U6).

---

## Lot AE — Le mode simplifié va jusqu'au résultat ✅ livré le 2026-08-09

### Constat
Question de l'utilisateur : « le mode simplifié fait bien tout de A à Z en simplifié ? » **Non.**
Son étape 4 s'intitulait « Météo et calcul » et ne faisait **ni l'un ni l'autre** : elle affichait
« rien à faire de plus » et renvoyait sur Calcul 3D. Manquaient donc l'environnement, l'ombrage, la
météo, le calcul et le résultat — plus de la moitié du parcours. Pire, le calcul y aurait été
**refusé** : un bâtiment neuf a son ombrage marqué périmé et Calcul 3D part en mode précalculé
(point L2 du relevé). L'assistant conduisait donc l'utilisateur droit dans un mur.

### Ce qui a été fait
Deux étapes ajoutées, l'assistant va maintenant jusqu'au chiffre.

**Étape 4 — Voisinage et ombrage.** Une case à cocher (voisins, végétation, rayon) puis un
enchaînement complet sans autre intervention : génération des obstacles **alignés sur ce bâtiment**
(donc sans lui-même et sans obstacle qui l'empiète, cf. Lot AD) → enregistrement → association →
précalcul d'ombrage. Le précalcul est lancé **même sans voisins**, sinon le calcul serait refusé.
Les avertissements du filtrage (bâtiment écarté, obstacles rognés, hauteurs estimées) restent
affichés une fois l'étape franchie : ils qualifient le résultat final, pas seulement l'étape.

**Étape 5 — Météo et calcul.** Une seule question — l'usage du bâtiment — et tout le reste est
dérivé de ce que l'assistant connaît déjà : année type PVGIS aux coordonnées du bâtiment, capacité
thermique déduite du volume réel (× 1200, aide du Lot P), renouvellement d'air issu du profil choisi
à l'étape 2, `h_e` suivant le vent réel et `h_i` suivant l'orientation de chaque paroi (Lot R),
consignes de thermostat suivant le profil d'usage (Lot V). Tous ces choix sont **listés dans
l'interface**, pas cachés — et restent modifiables ensuite sur Calcul 3D.

**Résultat affiché** : besoins de chauffage et de climatisation en kWh **et en kWh/m²/an**, avec la
température intérieure moyenne. La `surface_ref_m2` est renseignée automatiquement depuis l'emprise
réelle au sol — ce qui répond aussi au point U2 du relevé, où elle était saisie à la main alors que
l'application connaît l'empreinte. Le libellé rappelle que ce sont des **besoins** et non des
consommations, et que les simplifications du mode se répercutent directement sur ces chiffres.

**Correctif de localisation trouvé en vérifiant** : Angular formatait tous les nombres en `en-US`,
donc « 4,210 kWh » là où il faut lire « 4 210 kWh » — dans un affichage de résultats chiffrés, cette
virgule se lit comme un séparateur décimal, soit un facteur 1000. `LOCALE_ID` passé à `fr-FR` pour
toute l'application (défaut Angular jamais changé depuis le scaffold, donc toutes les pages étaient
concernées).

### Vérification
**Navigateur réel, 12/12, parcours complet de bout en bout** : recherche → création → subdivision →
assignation → surface de référence posée automatiquement → génération d'environnement alignée sur le
bâtiment → association → précalcul d'ombrage → météo en année type → calcul en thermostat avec
consignes horaires, `h_e` dynamique et `h_i` par orientation, capacité déduite du volume → résultat
chiffré au format français. Les cinq étapes sont bien présentes au bout du parcours. 173/173 backend.

### Reste ouvert
Le calendrier d'occupation utilise le calendrier par défaut (semaine type commençant un lundi, sans
vacances) : le mode simplifié ne demande pas de dates, cohérent avec son objet, mais un bâtiment
scolaire y sera donc traité sans ses vacances. L'assistant ne propose pas de comparer deux variantes
(c'est le point U3, l'absence d'historique des calculs).

---

## Lot AF — Vacances scolaires réelles + libellé du profil d'usage ✅ livré le 2026-08-09

### Constat
Deux questions de l'utilisateur sur le Lot AE, dont une a fait tomber un défaut de fond.

**« Ça veut dire quoi ton "à quoi sert le bâtiment" ? »** — formulation trop vague de ma part pour
un champ qui pilote quelque chose de précis : le **profil d'usage** (Lot V) fixe les consignes de
thermostat heure par heure (19 °C en journée, 16 °C réduit la nuit, 7 °C hors gel à la fermeture,
plafond de climatisation ou non).

**« Pour le calendrier tu ne peux pas utiliser des vacances si on est scolaire ? »** — et là, un
vrai défaut : `defaultOccupationCalendar()` pose `vacances: []`, donc la branche
`if (profile.vacancesHorsGel && isVacation)` de `setpointsFor` **ne se déclenchait jamais**. Or le
hors-gel du week-end est commun au scolaire et au tertiaire : les vacances sont le SEUL trait qui
les distingue. **Choisir « Scolaire » donnait donc exactement le même résultat que « Tertiaire »** —
le profil existait, était documenté, testé au Lot V… et restait inerte faute de dates.

### Ce qui a été fait
**Table des vacances scolaires françaises** (`usage-profiles.ts`) : Toussaint, Noël, hiver,
printemps, été, avec les **trois zones** A/B/C pour les congés d'hiver et de printemps, qui seules
en dépendent. Dates **typiques** (calendrier 2024-2025 pris comme représentatif) et non celles d'une
année précise — elles glissent chaque année, et le mode simplifié travaille de toute façon sur une
année type météo, qui n'a pas de millésime. Même statut assumé que le catalogue de parois.

`schoolHolidayRanges(zone, startDayOfYear, nDays)` convertit ces dates en **indices de jour relatifs
au début du run**, l'unité qu'attend `OccupationCalendar` — la décision du Lot V (aucune date
calendaire côté serveur) est donc préservée. La conversion passe par l'ensemble des jours de
l'année plutôt que par un découpage à la main : c'est ce qui gère sans cas particulier le
chevauchement de Noël (21 décembre → 5 janvier, qui enjambe la fin d'année) et une période de départ
quelconque.

**Mode simplifié** : sélecteur de zone, affiché **uniquement** pour les profils scolaires — seuls
concernés — et vacances appliquées automatiquement. Le libellé du champ passe de « Usage du
bâtiment » à « **Régime d'occupation** », avec une phrase disant ce qu'il pilote réellement.

**Calcul 3D** : bouton « Charger les vacances scolaires » + sélecteur de zone dans le bloc
calendrier, à côté de la saisie manuelle de plages qui reste possible. Et un avertissement quand un
profil scolaire est choisi **sans aucune plage de vacances** : l'interface dit alors explicitement
qu'il se comportera comme un tertiaire — le piège dans lequel le mode simplifié était tombé.

### Vérification
Plages générées contrôlées zone par zone : **122 jours de vacances** dans chacune, chevauchement de
Noël correctement coupé en deux plages ([0-4] et [354-364]), congés d'hiver et de printemps bien
distincts d'une zone à l'autre.

**Effet chiffré du correctif** : sur une année complète, les profils Scolaire et Tertiaire diffèrent
désormais sur **2 136 heures (24,4 %)** — 4 632 heures de hors gel contre 2 496. Avant, la
différence était de **0 heure**. Parcours complet du mode simplifié revérifié en navigateur (12/12),
173/173 backend.

### Reste ouvert
Zone A/B/C à choisir à la main : la déduire des coordonnées demanderait une table
académie → département, disproportionnée ici. Les jours fériés isolés (1er mai, 14 juillet…) ne sont
pas traités — ils ne pèsent que quelques heures face aux 122 jours de vacances. Les vacances sont
posées en hors gel intégral, sans distinction jour/nuit ce jour-là : cadrage confirmé par
l'utilisateur au Lot V.

---

## Lot AG — Le planning horaire suit le calendrier d'occupation ✅ livré le 2026-08-09

### Constat (point L3 du relevé, signalé par l'utilisateur)
« Il faut bien sûr que le planning de ventilation soit basé aussi sur le planning d'occupation. »

Le Lot V n'a fait varier que les **consignes de thermostat** selon le calendrier. Ventilation,
apports internes et volets sont restés sur un unique « jour type » indexé `hour_index % 24`. Un
bureau dont le thermostat passait en hors gel le dimanche continuait donc d'être **ventilé au débit
d'occupation** et de recevoir ses apports internes ce jour-là. L'incohérence n'existait pas avant le
Lot V — c'est lui qui a introduit un calendrier d'un seul côté.

### Ce qui a été fait
**Un second planning « jours de fermeture »** (`planning_ferme`, 24 créneaux), appliqué aux heures
dont le point météo porte `occupied: false`. L'occupation est résolue **côté client**, depuis le même
calendrier que les consignes : le serveur reçoit un booléen par heure et ne connaît toujours aucune
date, décision du Lot V préservée.

**Le prédicat « fermé » est désormais partagé.** Il était enfoui dans `setpointsFor` ; il est extrait
en `isClosed(profile, isWeekend, isVacation)`, utilisé par les consignes **et** par l'occupation. Les
deux ne peuvent donc plus diverger — c'était précisément le défaut : deux notions de « fermé »
implicites, dont une seule appliquée.

**Non-régression stricte** : sans `planning_ferme`, `occupied` n'a aucun effet et un payload
existant se comporte exactement comme au Lot Q. Le serializer refuse `planning_ferme` sans
`planning` (il ne remplace que certaines heures, il ne peut pas porter tout le run).

**Interface.** Calcul 3D : second bloc de saisie, avec un bouton « Déduire du planning ci-dessus »
qui applique la convention usuelle — ventilation ramenée aux infiltrations (15 % du débit
d'occupation), apports internes nuls puisque personne n'est là, volets inchangés. Point de départ
modifiable, pas une règle imposée. Le bloc est désactivé tant qu'aucun régime d'occupation n'est
choisi, puisque rien ne définirait alors quand le bâtiment est fermé. Le parsing des deux plannings
est factorisé : même format, les dupliquer les ferait diverger.

**Mode simplifié** : la réduction est automatique, dérivée du profil de ventilation de l'étape 2 —
l'assistant connaît déjà le débit et le calendrier, il n'y a rien de plus à demander.

### Vérification
**10 tests** : jour fermé moins ventilé donc moins refroidi, absence de `planning_ferme` sans effet
(non-régression du Lot Q), clé `occupied` absente retombant sur le planning normal, apports internes
suivant l'occupation, semaine mixte basculant **heure par heure** et non une fois pour tout le run,
volets pouvant différer (vérifié en mode `imposed`, où seul le canal K agit), plus quatre tests de
validation. 183/183.

**Effet mesuré** sur une école en année type (zone C, 600 m³/h en occupation, 90 en fermeture) :
**14 945 → 13 368 kWh de chauffage, soit 10,6 % de moins**. L'ordre de grandeur est cohérent avec les
4 632 heures de fermeture sur 8 760.

**Navigateur réel, 16/16**, dont la preuve que le débit est réellement réduit (345,6 → 51,8 m³/h) et
que l'occupation et les consignes basculent bien sur les mêmes heures. Une vérification était
d'abord passée **pour la mauvaise raison** — le débit valait 0 des deux côtés, faute d'avoir choisi
un profil de ventilation dans le scénario de test : corrigée, la clause d'échappement qui masquait
le cas retirée (même leçon qu'au Lot I).

### Reste ouvert
La fraction de 15 % est une convention appliquée par le bouton, pas un réglage exposé : on modifie
le résultat en éditant le planning déduit. Le mode simplifié ne l'expose pas du tout. Les jours de
fermeture sont uniformes : pas de distinction entre un week-end et des vacances longues, où l'on
pourrait couper davantage.

---

## Lot AH — Environnement « observé » : LiDAR HD IGN × BD TOPO, bâtiment étudié choisi dans la scène ✅ livré le 2026-09-27

### Demande
Génération d'environnement entièrement automatique, appuyée sur les positions réellement observées
(toits, arbres) par le LiDAR, croisées avec la BD TOPO pour recaler ses emprises et construire les
bâtiments. Le bâtiment étudié se définit ensuite soit en le sélectionnant dans l'environnement, soit
en l'important à la main après avoir retiré de l'environnement ce qui doit l'être.

### Ce que les services exposent réellement (vérifié par appels, 2026-09-27)
- **Index des dalles** : WFS `IGNF_LIDAR-HD_METADONNEE:metadata` (une entité par dalle de 1 km²,
  `url_npl` = nuage COPC, dates d'acquisition, procédé de classement). L'ancienne couche
  `IGNF_NUAGES-DE-POINTS-LIDAR-HD:dalle` n'existe plus.
- **Nuage COPC** lu partiellement par requêtes Range : 300 × 300 m dans une dalle de 112 Mo =
  18 requêtes, 18,5 Mo, 8,5 s, 1,3 M points. Classes : 1, 2 sol, 3/4/5 végétation, 6 bâtiment, 64.
- **Limite de débit 1 req/s** (download ET WMS-R). Le lecteur HTTP de laspy lance 10 requêtes en
  parallèle et ne réessaie jamais un 429 → flux séquentiel maison `ThrottledRangeStream`.
- Rasters MNS/MNT/MNH 0,5 m en WMS-R sur emprise arbitraire : disponibles, **non utilisés** — sans
  classification, ils ne séparent pas un toit d'un arbre, ce qui est justement le besoin.

### Architecture
- `api/lidar_source.py` (réseau seul) : index, lecture COPC, BD TOPO **directement en Lambert 93**
  (jamais de double projection sur ce qu'on cherche à mesurer : le décalage entre sources).
- `api/observed_env.py` (pur, testable sans réseau) : repère, rasters, recalage, toitures, arbres,
  terrain, objets, composition, promotion en bâtiment, ajustement d'un import.
- `api/environment_service.py` (Django) : génération jusqu'à l'`Environment` enregistré, repli hors
  couverture, actions sur les objets.
- Modèle : `Environment` gagne son propre géoréférencement (`georef_*`), `scene_objects` (nommé ainsi
  car `objects` est le gestionnaire Django ; exposé `objects` par l'API) et `generation` (compte
  rendu). `envelope` devient une vue dérivée des objets actifs — seul format lu par `api.shadow`,
  donc **ni `shadow.py` ni `building_solver.py` ne changent**.
- Chaque objet : `{id, kind: building|vegetation|terrain, status: active|removed|studied, origin,
  label, reason, info, footprint, vertices, triangles, k, building_id}`.

### Chaîne de reconstruction
1. **Repère** : Lambert 93 → local avec correction de la **convergence des méridiens** (1,7° à Tours,
   3,4° à Rennes, > 5° en Bretagne ouest) et du facteur d'échelle, mesurés numériquement (pyproj +
   géodésique). Signe vérifié contre le chemin indépendant WGS84/équirectangulaire : rotation
   résiduelle 0,017° et 0,000° (un signe inversé aurait laissé 3,4° / 6,8°).
2. **Rasters** : MNT 1 m (points sol, trous interpolés), toits 0,5 m (max des points bâtiment),
   végétation haute 1 m, comptes sol/total (fraction de trouée). Marge de 30 m au-delà de la zone.
3. **Recalage BD TOPO** : décalage global (±6 m) puis individuel (±2 m), par corrélation FFT avec un
   poids = **profondeur dans le toit** — pas le recouvrement (voir pièges). Pas d'ajustement
   individuel pour un mitoyen.
4. **Statut** : emprise couverte par un toit LiDAR → confirmée ; sans toit mais avec sursol → gardée,
   prisme à la hauteur BD TOPO ; LiDAR voit le sol → **retirée d'office** (« démoli ? »),
   restaurable ; pas de points du tout (dalle manquante) → gardée, jamais conclue démolie.
5. **Bâtiments absents de la BD TOPO** : composantes du masque bâtiment hors emprises (+1,5 m),
   ouverture à 2,5 m, ≥ 12 m², rectangle orienté si le contour s'y prête.
6. **Toitures** : TIN par insertion gloutonne (Garland–Heckbert) contrainte à l'emprise
   (bibliothèque `triangle`), points de contour insérables (pointe des pignons dans les murs),
   budget proportionnel à la surface, tolérance croissant avec la distance. Murs verticaux jusqu'à
   l'égout de chaque sommet, plancher — **volume fermé, normales sortantes** (vérifié : 0 défaut sur
   222 bâtiments et 335 arbres réels).
7. **Arbres** : maxima locaux de la canopée + ligne de partage des eaux ; prisme du bas du houppier
   (10ᵉ centile) au sommet ; transparence **mesurée** (points sol / points totaux sous la couronne),
   appliquée si vol feuilles présentes, sinon valeur « en feuilles » par défaut (0,20) et mesure
   conservée dans `k_bare` (prête pour la saisonnalité, Lot Z3).
8. **Terrain** : TIN glouton sur le MNT (508 triangles pour 300 × 300 m pavillonnaire) ; percé sous
   l'emprise du bâtiment étudié à la composition.
9. **Budget** : 60 000 triangles (terrain d'abord, puis par distance).
10. **Repli** hors couverture / hors France : générateur historique découpé en objets, avec z = 0
    désormais toujours au sol de l'origine (l'ancien comportement laissait les bâtiments IGN à leur
    altitude NGF absolue quand aucune altitude n'était saisie).

### Bâtiment étudié
- **Dans la scène** : `POST /api/environnements/<id>/objets/<obj>/etudier/` → `Building` dans le
  repère de l'environnement (alignement exact par construction), lié à lui ; groupes `mur_k`
  (suffixe `_mitoyen` si un voisin actif longe l'arête), `toiture_<orientation>` / `toiture_plate`,
  `sol` (boundary `ground`).
- **Importé** : `.../remplacer/` → `observed_env.fit_import` place l'OBJ/STL sur l'emprise de
  l'objet (rotation, translation, **axe vertical Y/Z et unité m/cm/mm/pouce détectés**), triangles
  dégénérés écartés et comptés au lieu de faire rejeter le fichier. IoU affiché, avertissement
  sous 60 %.
- Retirer / restaurer : `PATCH .../objets/ {ids, status}` ; tout changement recompose l'enveloppe
  et périme l'ombrage des bâtiments liés. Rien n'est jamais supprimé.
- `generer/` avec `building_id` : repère du bâtiment repris, l'objet qui lui correspond est marqué
  `studied` (successeur du Lot X) ; son `georef_ground_z` vide est fixé au sol mesuré.
- Le job **enregistre** l'environnement (`result.environment_id`) — plus d'aller-retour de plusieurs
  Mo par le navigateur. Rayon ≤ 250 m.

### Mesures sur données réelles
| Zone | Durée totale (job réel) | Bâtiments | Arbres | Triangles | Erreur angulaire des toits vue du centre (méd. / p90 / p99) |
|---|---|---|---|---|---|
| Joué-lès-Tours, collectifs + pavillons, R = 150 m | ≈ 25 s (29 requêtes, 25 Mo) | 63 | 260 | 8 252 | 0,04° / 0,29° / 1,42° |
| Tours centre ancien, mitoyens, R = 120 m | ≈ 30 s | 159 | 74 | 13 756 | 0,34° / 1,68° / 4,45° |

Pour mémoire, la grille d'ombrage précalculée a un pas de 15°. Précalcul d'ombrage d'un bâtiment
choisi dans la scène (76 triangles) contre 8 172 triangles d'environnement : quelques secondes.

### Pièges rencontrés (tous constatés sur données réelles, corrigés)
- **Recouvrement ≠ recalage** : une emprise entièrement contenue dans son toit a déjà un
  recouvrement maximal, aucun décalage ne l'améliore — une bande de toit de 1–2 m restait d'un côté.
  D'où la pondération par profondeur (centre l'emprise, répartit le débord).
- **Bord de zone** : garder les emprises qui recoupent la zone sans agrandir les rasters laissait
  la partie hors raster sans toit (maison à deux pans reconstruite plate à 5,5 m au lieu de 11).
- **Points de façade** classés « bâtiment » : ils tiraient l'égout vers le bas (toit plat en
  pyramide). Mailles de bord plus basses de 1,5 m que leurs voisines écartées.
- **Ouverture morphologique** pour ôter les cheminées : elle écrêtait aussi chaque faîtage de
  pente × 1,25 m. Ne rabote plus que les taches compactes ≤ 4 m².
- **Insertion gloutonne arrêtée à mi-budget** quand ses 12 pires points étaient tous trop proches
  d'un sommet existant.
- **Égout surestimé** de 0,5–0,9 m sur un toit en pente (maille la plus proche à 0,5–1 m à
  l'intérieur) : plan local extrapolé jusqu'au contour, borné (jamais au-dessus des voisins + 0,2 m,
  sans quoi le bout d'un faîtage débordait).
- **Recalage individuel en îlot mitoyen** : toits continus, pas de signal → désactivé pour les
  emprises qui en touchent une autre.
- **Faux « bâtiments LiDAR seul »** : bandes de débord de toit et doublons en bord de zone.
- Champ de modèle nommé `objects` : interdit par Django (gestionnaire).
- `setup2.sh` a affiché « ✓ déployée » et rendu 0 alors que le build du frontend avait échoué
  (budget CSS) — conteneurs absents. Non corrigé (script partagé), à garder en tête.

### Vérifications
- 203 tests backend (dont 20 nouveaux : scène LiDAR synthétique à vérité connue — maison à deux
  pans, toit plat avec points de façade, annexe hors BD TOPO, bâtiment démoli, arbre à trouées,
  terrain en pente, emprises décalées de (1,5 ; −1) m — repère, recalage, promotion, ajustement
  d'import, service avec base, vue, génération bout en bout réseau simulé, repli).
- Job Celery réel sur l'app déployée, puis navigateur réel (lab-runner, Keycloak réel, API réelle —
  `e2e_member` a désormais un email) : ouverture, légende et compte rendu, glisser ≠ clic, sélection
  au clic, retrait, restauration, bâtiment étudié créé et ouvert sur la page Bâtiment
  (`/batiment?id=`), remplacement par un OBJ Y-up en mm (axe et unité reconnus, avertissement de
  faible recouvrement affiché). Données de test supprimées ensuite.

### Au passage
- Visualiseur : couleurs résolues une fois par passe (un `getComputedStyle` par triangle coûtait des
  secondes à 60 000 triangles) ; sélection au relâchement seulement si le pointeur n'a pas bougé ;
  cadrage resserré ; toile transparente (fond noir en thème clair auparavant).
- `DATA_UPLOAD_MAX_MEMORY_SIZE` = 30 Mo (un OBJ de 20 000 sommets dépassait les 2,5 Mo par défaut).

### Reste ouvert
- **Toitures en TIN 2,5D** : un ressaut vertical devient une pente de ≥ 0,7 m de large ; en centre
  ancien, p90 angulaire 1,7°. Une vraie segmentation en pans (LOD2) ferait mieux, sans enjeu à la
  résolution de 15° de la grille d'ombrage — à reconsidérer si le mode temps réel devient la norme.
- **Horizon lointain** (relief au-delà du rayon) ignoré : l'API horizon de PVGIS le fournirait.
- Mitoyens : le mur est signalé `_mitoyen`, mais reste exposé à `t_ext` faute de condition limite
  adiabatique (troisième `boundary`, déjà noté au Lot X).
- Couronnes en prisme ; massifs très irréguliers approchés par leur contour simplifié.
- `triangle` enveloppe la bibliothèque de J. R. Shewchuk : libre pour un usage non commercial.
- Préexistant, non corrigé : la page Bâtiment colore les modèles de paroi avec `--series-1..8`,
  variables définies seulement dans le style de Calcul 1D — sur Bâtiment elles sont vides et toutes
  les parois assignées s'affichent du même gris.

---

## Lot AI — Sol, matériaux, texture, et mode simplifié par sélection 3D ✅ livré le 2026-09-27

### Demandes (retours sur le Lot AH)
1. Végétation BD TOPO jugée nulle → la baser sur le LiDAR. *Constat* : c'était déjà le cas au Lot AH ;
   l'environnement critiqué venait de l'ancien générateur ou avait été lancé cases décochées.
   Libellés rendus explicites (« relevés par le LiDAR »).
2. Sol : exploiter les données → tranché avec l'utilisateur : **nature / albédo du sol** et
   réflexion solaire par le sol (absente jusqu'ici).
3. « Texturer » → tranché : **les deux** — texture visuelle (orthophoto) ET matériaux pour le calcul.
4. Mode simplifié trop difficile → charger l'environnement 3D, sélectionner le bâtiment (ou
   plusieurs emprises BD TOPO qui n'en forment qu'un), **recalculer l'enveloppe de l'ensemble**.

### Données vérifiées (2026-09-27)
- Orthophotos WMS-R `ORTHOIMAGERY.ORTHOPHOTOS` (RVB) et `ORTHOIMAGERY.ORTHOPHOTOS.IRC`
  (proche infrarouge en canal rouge), 20 cm natifs, emprise arbitraire. Même limite 1 req/s.
- BD TOPO : `materiaux_des_murs`, `materiaux_de_la_toiture` (codes fichiers fonciers à deux
  chiffres, un matériau par chiffre non nul : « 35 » = béton + aggloméré), `date_d_apparition`.
  Renseignés sur ~⅔ des bâtiments du quartier test.
- Le catalogue de parois ne contenait QUE du neuf (RT2005–RE2020) : aucune paroi pour un mur en
  pierre ou un béton des années 60 — pré-assigner exigeait de l'étendre.

### Ce qui a été fait
- **Albédo** (`observed_env.OrthoImage`) : moitié visible (RVB linéarisé, gamma 2,2), moitié
  proche infrarouge (IRC). Calibration vérifiée sur l'image réelle : feuillus 0,15, pelouse 0,20,
  enrobé 0,04, minéral clair 0,44 — dans les fourchettes usuelles. Eau reprise du LiDAR (classe 9).
  Grille d'albédo du sol à 2 m → `Environment.generation['ground_albedo']`.
- **Réflexion par le sol** (`shadow.compute_ground_reflection_factors`) : facteur par triangle
  `ρ_vu·(1 − cos β)/2`, ρ_vu = albédo du sol réellement vu (rayons vers le bas, voisins masquants,
  albédo lu au point d'impact). Solveur : `e_glo += facteur × GHI` (GHI = e_dir·sin h + e_dif),
  traité comme du diffus (y compris sous un store). Précalculé avec l'ombrage, recalculé en
  mode temps réel. **Aucun terme sans albédo relevé** : comportement antérieur inchangé.
- **Absorptance par triangle** (`alpha_ext`) : remplace l'alpha de la couche extérieure opaque
  sans dupliquer le modèle. Toiture : 1 − albédo relevé (75ᵉ centile, voir pièges) ; murs :
  valeur usuelle du matériau BD TOPO. Préservée par le PATCH « sommets seuls » (piège Lot K), la
  page Bâtiment et le mode simplifié.
- **Catalogue étendu** (`seed_paroi_catalogue`, 14 parois de bâti existant) : pierre 50 cm
  (U ≈ 1,9), brique pleine (1,6), pan de bois (2,0), béton banché non isolé (3,2), parpaing non
  isolé (2,3), ITI 40/60/80 mm (0,71/0,53/0,42), ossature bois, toitures non isolée → 150 mm,
  plancher non isolé. **Pré-assignation** par époque × matériau (`observed_env.suggest_materials`) ;
  sans année ni matériau, rien n'est inventé. Commande à relancer après déploiement (idempotente).
- **Fusion** (`observed_env.merge_objects`) : enveloppe REconstruite sur l'union des emprises
  (jours de quelques cm refermés), toiture relevée sur les maillages des parties par lancer de
  rayons vertical, murs seulement sur le contour extérieur ; un ressaut entre parties devient
  mur extérieur au-dessus du toit le plus bas. Sélection non contiguë refusée.
  Endpoint `POST /api/environnements/<id>/etudier/ {ids, name}`.
- **Texture** : `GET /api/environnements/<id>/orthophoto/` (cache disque), descripteur affine
  local → (u, v) dans le détail ; `MeshViewer` gère un second maillage texturé (toits et terrain),
  façades teintées par matériau (tokens `--mat-*`). Composant partagé `env-scene`.
- **Mode simplifié refondu** : 1. reconstruire (ou reprendre) le quartier ; 2. cliquer son ou ses
  bâtiments dans la vue 3D (réunis automatiquement) ; 3. parois pré-assignées, vitrage double 20 %
  pré-rempli sur les façades exposées, **réglage commun à toutes les façades**, détail par paroi
  replié ; maille élargie automatiquement si le bâtiment est trop grand ; 4. l'environnement est
  déjà lié, reste l'ombrage ; 5. calcul. Volume et emprise mesurés sur l'enveloppe (volume 2,5D
  exact, Σ aire projetée × hauteur).
- Page Environnement : sélection multiple (bâtiments seuls, déjà étudiés exclus), orthophoto
  activable, rapport de pré-assignation.

### Mesures réelles (quartier test, 47,4100 ; 0,6987, R = 150 m)
- Albédo du sol : médiane 0,145 (p5 0,05 – p95 0,30).
- Albédo des toits par matériau déclaré (75ᵉ centile) : ardoises 0,12 < tuiles 0,16 < béton 0,21
  < zinc 0,48 — l'ordre attendu.
- Parois suggérées pour 64 bâtiments sur 110 (les autres : ni année ni matériau).
- Fusion de deux bâtiments mitoyens réels (6,5 m et 17,4 m) : volume fermé 9 972 m³ contre
  9 969 m³ pour les parties (0,03 %), murs mitoyens signalés.
- Facteur de réflexion médian des murs du bâtiment fusionné : 0,039 (albédo vu ≈ 0,08 — rue
  canyon, voisins masquants).

### Pièges (constatés, corrigés)
- **Plancher sur terre-plein** : il « voit » tout le sol sous lui et recevait ρ × GHI sur sa face
  enterrée (facteur 0,74 mesuré). Exclu (`boundary == 'ground'`).
- **Albédo de toit à la médiane** : sur un toit à deux pans, le pan à l'ombre au moment de la prise
  de vue paraît sombre sans l'être — médiane biaisée bas (tuiles 0,12). 75ᵉ centile.
- **App sans zone.js** : une affectation dans un `subscribe` ne rafraîchit pas la vue → signal.
- **Sélection multiple** : un clic tombé sur le terrain effaçait la sélection ; un bâtiment déjà
  étudié pouvait y entrer. Bâtiments actifs seulement.
- **Maille de 2 m** refusée pour un collectif de 74 m (> 20 000 triangles) : repli automatique ×1,5.
- Mon propre test : triangle « face au sud » orienté au nord (ordre des sommets) — les valeurs
  obtenues étaient exactement celles du côté réellement vu.
- **Précalcul d'ombrage > 10 min** pour un collectif subdivisé (12 288 triangles) : lancer de rayons
  numpy de trimesh. Passage à **Embree** (`embreex`, repli numpy automatique,
  `shadow._ray_intersector`) : 200 000 rayons en 4,9 s contre 2 313 s, zéro désaccord ; sur une
  scène réelle complète, 11 cases de grille solaire sur 54 432 diffèrent (rayons rasants), facteur
  de ciel à 3·10⁻⁵ près, réflexion du sol identique. Le facteur de vue du ciel regroupe en outre
  ses rayons par lots (résultat identique à 3·10⁻¹⁶). Ombrage du parcours simplifié : 6 s.
- **Verrou fantôme** (préexistant) : une tâche coupée par un redéploiement restait « en cours » en
  base et bloquait tout précalcul et tout calcul du lab (409). Le worker marque désormais ces
  tâches en erreur à son démarrage (`tasks._release_orphan_jobs`, signal `worker_ready`).
- **Calcul refusé avec tout profil sans climatisation** (préexistant depuis le Lot V) : les profils
  codent « pas de clim » par t_max = 100, la consigne horaire était bornée à 50 → 400 heure par
  heure. Borne relevée à 100.

### Parcours vérifié en navigateur réel (API réelle)
Mode simplifié : quartier repris → clic sur un bâtiment → parois pré-assignées (2000, béton +
aggloméré, ardoises : ITI 80 mm, toiture 1989–2000, α toit 0,91 mesuré) → vitrage pré-rempli →
ombrage 6 s → année type → **6 708 kWh, 62 kWh/m²/an** sur 108 m². Page Environnement :
orthophoto servie et drapée, sélection multiple de deux bâtiments, bouton « Réunir » présent.

### Reste ouvert
- Façades voisines toujours noires (seul le sol réfléchit) ; ombres portées au sol ignorées.
- Orthophoto non « vraie ortho » : toit d'un bâtiment haut déporté ; son albédo peut mêler façade.
- Pré-assignation par ordre de grandeur d'époque ; pas de menuiseries réelles (proportion de
  triangles vitrés, comme avant).
- Texture : orthophoto en vue de dessus sur les toits en pente (étirement léger sur les pans raides).

---

## Lot AJ — Houppiers relevés sur le LiDAR, essence par CoSIA ✅ livré le 2026-09-27

### Demande
« Grâce au LiDAR × CoSIA, les arbres devraient avoir des formes assez fines plutôt que de vulgaires
polygones extrudés. »

### Données vérifiées
- **CoSIA** (couverture du sol par IA, IGN) en WMS-R par millésime : `IGNF_COSIA_2017-2020`,
  `_2021-2023`, `_2024-2026` (le millésime est choisi d'après l'année du relevé LiDAR). Rendu en
  couleurs de légende (`CoSIA-legend.png`) : la classe est décodée par la couleur la plus proche
  (tolérance 20). Palette relevée à la main — la détection automatique des pastilles se décalait
  d'une ligne.

### Ce qui a été fait
- `observed_env.crown_solid` : chaque houppier est découpé en tranches de ~1,5 m ; dans chaque
  tranche, rayon au 90ᵉ centile des points dans 8 directions autour de l'axe ; anneaux reliés, fond
  au bas du houppier, pointe au point le plus haut. Conifère conique, feuillu arrondi, dissymétrie
  conservée. ~72 triangles par arbre. Repli sur le prisme (contour du segment) pour un massif non
  étoilé ou < 30 points.
- **Essence** (CoSIA majoritaire sous la couronne) : un conifère garde ses aiguilles, donc la
  transparence mesurée par un vol hivernal vaut pour lui toute l'année (appliquée) ; feuillu ou
  inconnu : valeur « en feuilles » par défaut, mesure conservée (`k_bare`).
- Vue : feuillus et conifères de teintes distinctes (`--veg-feuillu`, `--veg-conifere`), fiche avec
  essence et forme.

### Mesures réelles (quartier test, R = 150 m)
280 arbres, 280 houppiers relevés (0 volume ouvert), 21 conifères, 26 192 triangles au total. Test
synthétique : cône de points → maillage fermé effilé, volume ~ cône (et non prisme).

### Reste ouvert
Haies et alignements : la reconstruction autour d'un axe unique les aplatit (forme en étoile) ;
une segmentation dédiée (squelette le long de la haie) ferait mieux.

---

## Lot AK — Façades : texture Panoramax, baies détectées, vrais vitrages ✅ livré le 2026-09-27

### Demande
Texture visuelle des façades, catégorisation vitrage / mur, et dans le mode simplifié de vrais
vitrages au lieu d'une proportion ; pouvoir couper la texture pour vérifier sur le maillage.

### Sources et outils vérifiés
- **Panoramax** (IGN × OSM France) : photos 360° équirectangulaires CC-BY-SA 4.0, API ouverte
  sans clé (`api.panoramax.xyz/api/search?bbox=`), position (précision annoncée ~4 m), cap du
  centre (`view:azimuth`), inclinaison (`pers:pitch`), HD 7680×3840. > 100 photos sur la zone test.
  Pas d'obliques IGN en service.
- **Détection des baies** — trois essais sur façades réelles : (1) règle de couleur : rate les
  volets clairs sur mur crème, prend les trouées d'une haie pour des baies ; (2) SegFormer/ADE20K :
  segmente bien végétation/ciel/véhicules mais **aucune** fenêtre ; (3) **OWLv2** (Google, Apache
  2.0, ONNX quantifié 155 Mo, requêtes « a window » / « a window shutter » / « a door ») : fenêtres,
  volets et portes correctement encadrés — retenu (~30 s par façade en tâche de fond).
- SegFormer conservé pour mesurer la part de façade visible (licence NVIDIA non commerciale).
  Modèles téléchargés au premier usage dans le cache du conteneur (re-téléchargés après
  redéploiement).

### Chaîne (`api/facades.py`)
1. Plans de façade (groupes `mur_k` exposés), repère (s, t) : s vers la droite vu de l'extérieur.
2. Photos candidates proches et de face (4–35 m, < 60°), classées ; pré-filtre de visibilité par
   lancer de rayons (bâtiments + terrain, bâtiment lui-même compris) avant tout téléchargement.
3. Hauteur d'appareil = terrain LiDAR SOUS la prise de vue + 2,2 m.
4. Recalage fin (±2 m, ±3°) sur les arêtes des bâtiments (contours orientés, végétation
   masquée, rappel vers le GPS, façade interdite dans le ciel).
5. Redressement (3 cm/px) ; photo retenue = la première suffisamment dégagée (≤ 55 % masquée
   selon SegFormer), sinon la moins masquée ; > 70 % masquée → « masquée ».
6. OWLv2 → baies ; fusion des cadres emboîtés / vantaux voisins ; filtres de taille et de contour.
7. Intégration (`insert_openings`) : chaque baie = trou rectangulaire dans le mur (triangulation
   contrainte), rempli par `vitrage_k` (modèle de vitrage) ou `porte_k` ; contour du mur conservé
   sommet pour sommet → volume fermé. Façade non vue : fenêtres régulières par étage à la
   proportion choisie. Toujours depuis l'enveloppe de base (réappliquable).

### API / interface
- `POST /api/batiments/<id>/facades/analyser/` (job), `GET …/facades/<groupe>/texture/`,
  `POST …/facades/appliquer/ {glazing_model_id, wall_model_id?, fallback_ratio, use_detection}`.
  `Building.facades` (migration 0013) ; l'enveloppe de base n'est pas renvoyée au navigateur.
- Composant `facade-panel` (mode simplifié étape 3 et page Bâtiment) : analyse, vue 3D texturée
  ou en couleurs (vitrage bleu, porte orange, mur, mitoyen), tableau par façade avec « utiliser la
  détection », intégration. Visualiseur : plusieurs textures (une par façade), UV en 3D.
- Mode simplifié : plus de proportion de petits triangles ; subdivision APRÈS les vitrages
  (finesse de l'ombrage), repli automatique de maille.

### Itérations sur données réelles (maison de la zone test) — pièges
- Caméra à 2,2 m au-dessus du pied du bâtiment : rue en pente → texture remontée dans le toit.
- Recalage avec grands déplacements : scores élevés sur de fausses solutions (lames d'un volet) ;
  la photo la plus proche, à peine corrigée, était la bonne → recalage borné, pas de recalage
  vertical, choix dans l'ordre géométrique.
- Choix par SegFormer seul : le muret de clôture est classé « mur » → exclu des classes façade.
- Vue d'un pignon caché par une maison voisine : détection des fenêtres de la voisine → test de
  visibilité par rayons.
- Cadres emboîtés / vantaux séparés → fusion.
- Pignon vu et aveugle recevait des fenêtres de repli → une façade vue suit la détection même vide.
- **Affinage de maillage refusé après insertion des vitrages** (trouvé par le parcours complet en
  navigateur) : l'ancien raffinement « rouge » marquait les trois côtés de tout triangle touché,
  ce qui se propageait à TOUT le maillage à chaque itération ; avec les petits triangles des baies
  et des côtés de 37 m, dépassement de la limite de 20 000 triangles même à 7 m. Remplacé par un
  raffinement local rouge-vert-bleu par le plus long côté (`geometry.refine_envelope`, conforme,
  orientation conservée) : 5 118 triangles à 2 m sur la même maison. Tests `RefineEnvelopeTest`
  (l'ancien algorithme échoue au test de non-propagation).
Résultat final sur la maison test : façade sur rue 3 fenêtres + porte exactes ; pignon vu,
aveugle ; pignon masqué (voiture, haie) détecté comme tel ; façade jardin sans photo. Enveloppe
fermée après insertion (vérifié).

### Reste ouvert
- Façades côté jardin : rarement photographiées depuis la rue → proportion.
- Recalage fragile quand peu d'arêtes nettes sont visibles ; détection manquée d'une fenêtre
  surexposée.
- Pas de dimension réelle des menuiseries (cadre) ; fenêtres de toit ignorées.
- Texture des bâtiments voisins non faite (bâtiment étudié seulement).

---

## Lot AL — Façades traitées dans l'ensemble de l'environnement ✅ livré le 2026-09-27

### Demande
« Le résultat de ta projection sur les façades verticales est nul » : chercher des sources et
méthodes plus adaptées (peut-être traiter le bâtiment dans l'ensemble de son environnement), puis
implémenter le mieux adapté.

### Diagnostic (bâtiment utilisateur « 4 bâtiments réunis », collectifs sur un talus planté)
Superposition des arêtes du modèle sur les photos recalées : le recalage N'était PAS le problème
principal (ligne de toit juste). Causes réelles :
- la haie du talus, les murets, les massifs ne sont pas dans la scène (seuls les arbres hauts le
  sont) → le bas des façades était texturé avec la haie ;
- SegFormer appliqué à la texture redressée d'un morceau de mur de 14 px étiré en 512² → une haie
  pleine mesurée « visible à 99 % » ;
- une photo par morceau de mur, sans cohérence entre voisins.

### Recherche (deux agents, appels réels)
- **Sources** : BDNB (CSTB) — DPE par bâtiment avec surfaces vitrées N/E/S/O, % de baies, Uw, g,
  menuiserie ; API PostgREST sans clé, Etalab 2.0 ; jointure par le RNB (`closest`). Vérifié :
  collectif de Tours 23 %, maison 1989 N 9,6 / S 10,9 / O 9,1 m². ADEME DPE (pas de surfaces par
  orientation dans le jeu plat). Mapillary : poses SfM corrigées mais jeton requis (couverture non
  vérifiée). IGN : ni obliques ouvertes, ni LOD2 pour Tours ; LiDAR HD sans couleur et peu de points
  en façade. OSM : aucune baie décrite.
- **Méthodes** : recalage de séquence (SfM COLMAP/OpenSfM + alignement LiDAR — non fait, lourd sur
  2 vCPU), texturation multi-vues avec visibilité et rejet des vues incohérentes (mvs-texturing),
  détection spécialisée de fenêtres (win_det_heatmaps MIT — poids sur Google Drive/Baidu en
  PyTorch, écarté), grille d'étages.

### Implémenté (`api/facade_texture.py`, `api/bdnb.py`, `api/facades.py`)
1. **Occultation par le nuage LiDAR brut** (`LidarOccupancy`) : voxels 0,3 m de tous les points
   hors sol (classes 1, 4, 5, 6, 64), objets bas (< 4 m) remplis jusqu'au sol (une haie vue d'avion
   n'a de points que sur son dessus), bâtiment étudié exclu (déjà maillage exact). Plus l'occulteur
   de scène (bâtiments + terrain). Test par rayon à la maille de 25 cm.
2. **Recalage par séquence** : décalage (dx, dy, cap) commun par tronçon de séquence (≤ 40 m), puis
   affinage individuel ±0,5 m, ±0,5°.
3. **Recalage de chaque vue sur CHAQUE façade** (`refine_view_on_facade`) : hauteur d'appareil et
   cap ajustés (±1 m, ±1°) sur le contour de la façade (égout, rampants, angles — pied exclu).
   Décisif : deux photos consécutives d'une même séquence plaçaient la façade d'une maison à ~1 m
   d'écart vertical (hauteur réelle d'appareil inconnue : voiture, piéton…).
4. **Segmentation par panorama entier** (SegFormer, 1024 px), consultée au pixel de chaque texel,
   vote par maille : végétation, véhicules, ciel… écartés pour la vue concernée.
5. **Composition par vue principale** : la vue qui apporte le plus (texels vus × résolution, vues
   < 8 m pénalisées × plausibilité² = part de texels « façade » selon la segmentation) couvre tout ce
   qu'elle voit ; les suivantes comblent ses trous par régions ≥ 0,5 m². Zone non vue = gris.
   Petits trous isolés (< 0,6 m²) bouchés par la couleur voisine. Texture à 2 cm/px.
6. **Baies** : OWL-ViT v1 patch 32 (Apache 2.0, ~6 s/passe) sur tuiles de ~6 m, image égalisée sur
   les seuls texels vus ; complément OWLv2 sur la façade entière quand < 10 % de vitrage trouvé
   sur ≥ 8 m² vus ; filtres : zone non vue, feuillage (> 25 % de vert), cadre collé au sommet ;
   « porte » ≥ 1,5 m de large → baie vitrée ; « porte » en étage → fenêtre SAUF si son bas est caché.
7. **Grille d'étages** (`regularize_openings`) : alignement par étages × travées, extrapolation
   dans les parties NON VUES seulement (rez caché par une haie) — jamais dans une partie vue.
8. **Référence DPE (BDNB)** : repli par orientation (maison) ou global (immeuble, le DPE ne décrit
   qu'un logement) pour les façades non vues et les parties cachées sans grille.
9. Statut « partielle » (vue à 10–60 %), part vue par façade, `POST …/facades/recomposer/` (textures
   perdues au redéploiement, recomposées en tâche de fond depuis les vues enregistrées).

### Pièges d'exploitation trouvés au test en navigateur
- **Textures invisibles** : le worker compose dans SON /tmp, le backend sert depuis le sien —
  conteneurs distincts. Au Lot AK le backend refaisait la texture lui-même depuis une photo, ce qui
  masquait le problème (et réaffichait l'ancienne texture « haie »). Volume local partagé
  `facade-cache` (docker-compose), vidé à chaque `setup2.sh` → bouton « Recomposer les textures »
  (~30 s pour 11 façades), les baies restant en base.
- Cases « utiliser la détection » conservées d'une analyse à l'autre : une façade devenue vue
  recevait le repli → réinitialisées à chaque nouvelle analyse.

### Essais abandonnés (avec la raison — à ne pas retenter sans nouvel argument)
- Mélange des vues texel par texel : mosaïque délavée à images fantômes (façade réelle non plane :
  balcons, parallaxe différente par photo).
- Estimation de l'inclinaison par les arêtes : score presque plat, estimation opposée à la
  métadonnée que la superposition confirmait juste.
- Regrouper plusieurs façades sur une image pour OWL : même façade 5 baies seule, 0 en compagnie.
- Contrôle de cohérence entre vues (corrélation de gradients) : ≈ 0 entre toutes les vues, même la
  bonne — le recalage n'est pas assez précis pour ça (il faudrait la SfM).
- 40 photos par bâtiment : > 10 min pour une maison → 16 photos, 5 vues par façade.

### Mesures
Maison (env. 16) ~3 min ; collectif 4 bâtiments réunis ~5–6 min. Façade sur rue de la maison :
égout en haut, fenêtres, porte, baie coulissante ; collectif : bas caché (haie) gris et rez complété
en grille.

### Reste ouvert
- Détection encore instable (zéro-shot) : varie avec de petites différences de texture ; baies
  derrière balcons profonds rarement vues.
- SfM de séquence (COLMAP/OpenSfM) + alignement LiDAR : seule voie vers un recalage assez précis
  pour la cohérence multi-vues ; non fait (coût CPU).
- Géométrie : façades courbes/balcons du collectif vs plans BD TOPO ; « bâtiments » LiDAR parasites
  (massifs) devant certaines maisons.
- Licences : SegFormer NVIDIA non commercial ; OWL-ViT/OWLv2 Apache 2.0 ; BDNB Etalab 2.0 ;
  Panoramax CC-BY-SA.

---

## Lot AM — Poses des photos par photogrammétrie (pycolmap) ⏸ désactivé par défaut le 2026-10-12 (trop coûteux pour cette machine)

### Pourquoi
Le recalage photo par photo des Lots AK–AL ne tenait pas (« Reste ouvert » du Lot AL) : position
GPS à ~4 m, cap annoncé faux de plusieurs degrés, inclinaison variable. Seule la reconstruction
d'ensemble des photos donne des poses relatives exactes.

### Implémenté (`api/facade_sfm.py`, branché dans `api/facades.analyse`)
- Jusqu'à 36 photos de la zone reconstruites ensemble par COLMAP (`pycolmap`, caméra
  équirectangulaire, sans GPU, 2 fils), masque de −25° à +50° d'inclinaison (bandeau incrusté,
  véhicule, ciel), amorçage à 2° de triangulation.
- Calage dans le repère du bâtiment : verticale = moyenne des axes « haut » ; similitude
  horizontale par RANSAC sur les positions GPS de toutes les photos du modèle ; hauteur = terrain
  LiDAR + hauteur d'appareil.
- Les photos non reconstruites retombent sur l'ancien recalage (source `gps`) ; la texture
  n'emploie que les 16 photos les plus utiles ; une photo à pose photogrammétrique ne subit plus
  de recalage local. Le résultat porte `method: 'photogrammetrie'` et un rapport `sfm`.
- Tests : `SfmAlignmentTest` (calage retrouvé malgré le bruit GPS et une position aberrante,
  convention de projection COLMAP). Suite complète : 244 tests verts.

### Mesure réelle du 2026-10-12 (image en service, 2 vCPU, Tours quartier Velpeau, sans bâtiment)
36 photos de 8 séquences → **27 placées dans un seul modèle** (24 retenues par le calage GPS),
erreur de reprojection 0,70 px, résidu GPS médian 1,66 m ; un second petit modèle rejeté
(« photos trop groupées »). Écart entre le cap reconstruit et le cap annoncé par Panoramax :
médiane −4,3°, de −40° à +17° — ce qui confirme que le cap annoncé n'est pas fiable.
**Durée : 554 s**, dont 226 s de points caractéristiques et 276 s d'appariement. Rejoué à
l'identique après le redéploiement du 2026-10-12 (pycolmap 4.2.1 au lieu de 4.2.0) : mêmes 27
photos, mêmes résidus, 505 s. Avec seulement les 14 photos les plus proches du même point
(6 séquences de 1 à 4 photos) : **aucune photo placée** en 140 s — la méthode a besoin de
séquences qui se recouvrent ; l'analyse retombe alors sur l'ancien recalage.

### Décision du 2026-10-12
Mémoire mesurée : **3,4 Go au pic** dans le worker pour 36 photos (repos : 0,2 Go), en plus des
~9 minutes. Sur 16 Go partagés par tout le lab, c'est trop pour tourner à chaque analyse :
`FACADE_SFM_ENABLED` (réglage, défaut `False`) coupe la photogrammétrie ; l'analyse retombe sur le
recalage par séquence du Lot AL (`method: 'multivue'`). Le code reste en place.
Le manque de mémoire du 2026-09-27 venait d'un essai d'ALIKED + LightGlue (points et appariement
appris, par onnxruntime) — variante **à ne pas retenter** sur cette machine, jamais commitée.

### Reste ouvert — si le lot est repris un jour
- **Le coût** : environ 9 minutes de plus par analyse de façades (le Lot AL avait ramené l'analyse
  à 3–6 min en limitant à 16 photos). Pistes : option à la demande, appariement séquentiel au
  lieu d'exhaustif, moins de photos, largeur réduite.
- **Jamais vérifié sur un bâtiment** : depuis la mise en service, aucune analyse n'a porté sur un
  lieu couvert par des photos (le seul bâtiment en base n'en a aucune). La qualité des textures
  et des baies avec ces poses reste à juger à l'œil sur une maison et sur le collectif d'essai.
- L'interface n'affiche ni la source de la pose (`sfm` / `gps`) ni le rapport `sfm`.
- Le dossier de travail (`sfm/<id>` dans le cache des façades) n'est pas nettoyé après analyse.

---

## Hors scope — décisions déjà prises, à ne pas entreprendre sans en rediscuter

La page Théorie (section « Portée et hypothèses ») exclut déjà explicitement, comme choix assumé et
non comme oubli : **ponts thermiques linéiques / effets 2D-3D**, **couplage multi-locaux** (un seul
nœud d'air pour tout le bâtiment), **échanges IR grande longueur d'onde avec le ciel**, **albédo du
sol**, **réflexions multiples entre vitrages successifs**. Ce sont des refontes structurelles bien plus
lourdes que les lots ci-dessus — à ne considérer que si l'utilisateur le demande explicitement, pas à
improviser en marge d'un autre lot. Pour le pont thermique linéique : un ψ par arête de jonction entre
triangles d'orientations différentes, sommé sur le linéaire réel — pas amorcé plus loin ici.

**Précision sur le couplage multi-locaux** (le plus structurant des trois refontes ci-dessus si jamais
entrepris), pour qu'une décision future ait un vrai point de départ :

- **Multiplier les nœuds d'air** suppose d'abord un **découpage en zones** : chaque triangle devrait
  porter un `zone_id` (aujourd'hui seul `group`, une chaîne libre côté import OBJ, existe —
  réutilisable ou à distinguer). `_assemble_global_kc` créerait alors N nœuds d'air (un par zone) au
  lieu d'un seul, chaque triangle couplé à SON nœud de zone plutôt qu'au nœud global partagé.
- **Échange entre zones adjacentes** : soit un mélange d'air simplifié (une conductance entre deux
  nœuds de zone, un paramètre utilisateur — même famille que `g_vent`, mais entre deux nœuds internes
  plutôt qu'un nœud interne et l'extérieur), soit — plus juste physiquement — une vraie **paroi de
  refend** entre les deux zones : un type de triangle qui n'existe pas aujourd'hui, puisque tout
  triangle est actuellement un morceau d'ENVELOPPE (convection vers `h_e`/`t_ext` ou `t_ground` au
  Lot K) — un refend n'a ni l'un ni l'autre, juste de la convection intérieure (`h_i`) sur ses DEUX
  faces, chacune vers un nœud de zone différent.
- **« Analyse des échanges intérieurs »** est un chantier séparé et plus fin encore : même à
  l'intérieur d'une SEULE zone, le modèle actuel fait déjà une approximation forte en fusionnant tout
  échange de surface (convectif ET radiatif) dans un unique coefficient `h_i` vers un nœud d'air commun
  — une fenêtre froide ne « voit » pas rayonner un mur chaud, tout passe par l'air. Les outils std
  séparent `h_i` en une composante convective (vers l'air) et une composante radiative (vers les autres
  surfaces, via un réseau de facteurs de forme entre chaque paire de surfaces intérieures — coûteux en
  O(n²) — ou son approximation usuelle, une température radiante moyenne unique par zone). Un premier
  pas réaliste, si ce chantier est un jour entrepris seul (sans multiplier les zones), serait cette
  seule séparation convectif/radiatif à zone unique, avant d'aller jusqu'au réseau complet multi-zone.
