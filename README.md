# bilan-thermique

Simulateur thermique de bâtiments du lab, pensé pour deux usages sur un même modèle de bâtiment :

1. **Parc** — un gestionnaire importe une liste de bâtiments ; chacun est initialisé depuis l'open data (BAN, RNB,
   BDNB/DPE, BD TOPO, LiDAR HD de l'IGN) et calculé sans saisie (niveaux N0 et N1), avec sa confiance et sa
   complétude.
2. **Affinage** — le gestionnaire ou l'occupant d'un bâtiment corrige et complète sa fiche ; chaque valeur garde sa
   provenance (défaut, inférence, document, déclaration), et une correction d'une personne prime toujours sur une
   valeur automatique (`refonte.md`, D20).

Sur ce modèle commun, l'app propose des **améliorations** (actions élémentaires regroupées en **bouquets**), en
calcule les **potentiels atteignables** bâtiment par bâtiment et à l'échelle du parc, et produit des **rendus**
(graphes Plotly, fiches, synthèses) exportables en HTML, Word et PDF.

C'est une **aide à la décision**, pas un DPE ni un calcul réglementaire (D6) : N0 est « de type 3CL », les
degrés-heures suivent la méthode de la RE2020 sans en être une attestation.

Documents de référence, à côté de ce fichier :
- `FEUILLE_DE_ROUTE.md` — lots, tests et vérifications (lu par le lab : `validate-app.sh`, `app-test.sh`) ;
- `refonte.md` — cahier des charges de la refonte, décisions D1 à D48, avancement ;
- `to_do_potentiels.md` — suivi du chantier « potentiels, protections, rendu » (lots 35 à 40) ;
- `DEMANDES_LAB.md` — changements demandés au lab ; `to_do_bilan_thermique.md` — journal des lots A→AM (archive) ;
- page **Théorie** de l'app (`/theorie`) — la physique, du modèle 1D aux moteurs réduits et aux améliorations ;
- `GET /api/calculs/moteurs/` (`backend/api/moteurs.py`) — fiche de chaque moteur (méthode, hypothèses, limites),
  affichée dans Calculs › « Les simulations ».

## Les six onglets

Un parc et un bâtiment **courants** sont partagés par tous les onglets (bandeau de contexte, `?p=&b=` dans l'adresse).

| Onglet | Ce qu'on y fait |
| --- | --- |
| **Parc** | Bâtiments (tableau : besoin, fourchette P10–P90, confiance, complétude, approfondissement, modèles utilisés ; carte) · **Potentiels par bouquet** (parc réel comparé à chaque bouquet, agrégats) · ajouter des bâtiments · import et partage (CSV, rôles, invitations) · sources des données (orchestrateur de la chaîne, réglages du parc) |
| **Bâtiment** | Synthèse · données et provenance · géométrie · **Résultats en 3D** (dernier N2, par triangle) · enveloppe et systèmes (dont les protections solaires) · météo · environnement et façades · factures et mesures |
| **Calculs** | Les simulations (catalogue documenté des moteurs) · lancer et voir le résultat (entrées exactes, fiabilité, durée) · **Améliorations** (potentiels du bâtiment par bouquet, améliorations à la main, comparaison) · historique (entrées figées, résultats, comparaison de deux calculs) |
| **Paramétrage** | Valeurs utilisées par chaque modèle (isolants, menuiseries, archétypes, ventilation, rendements, confort d'été…), surchargées par parc ; **bouquets du parc** (copie d'un bouquet du lab, édition guidée) |
| **IA et imports** | Clés d'IA de l'utilisateur (coffre chiffré), import de fichiers pour le parc ou pour un bâtiment, documents lus et propositions à valider |
| **Rendu** | **Paramétrage du rendu** (sections, blocs, constructeur de graphes guidé, modèles enregistrés par parc) et **Rendu** (aperçu du document construit par le serveur, exports HTML / Word / PDF), en portée parc ou bâtiment |

Outils experts, hors du menu et joignables depuis les onglets : éditeur de maillage (`/maillage`), environnement d'un
quartier (`/environnement`), Calcul 3D avancé (`/calcul-3d` : planning horaire, régime imposé ou libre, ombrage en
temps réel, vent), paroi 1D (`/calcul-1d`), modèles de paroi (`/parois`), théorie (`/theorie`).

### Parcours type

1. **Parc** : créer un parc, importer un CSV d'adresses (ou ajouter des bâtiments un par un). La chaîne A1→A6 tourne
   en file `parc` : adresse, bâtiment RNB, DPE, emprise BD TOPO, archétype, puis environnement relevé (LiDAR HD ou
   MNS) qui recale la géométrie ; **N0, N1, N2 et les potentiels des bouquets suivent sans geste** (D49).
2. **Bâtiment** : relire la synthèse et les trois niveaux côte à côte, corriger ce qui est faux (une correction
   déclarée prime), ajouter des protections solaires, choisir la météo. Chaque affinage relance les calculs devenus
   périmés après un court regroupement (30 s par défaut) ; l'état des calculs se met à jour à l'écran.
3. **Calculs** : lire les résultats et leurs entrées figées ; un gestionnaire peut encore lancer un calcul à la main
   (« Recalculer », ou un N2 sur une enveloppe experte) ; « Améliorations » montre les potentiels par bouquet en N0,
   N1 et N2.
4. **Parc › Potentiels par bouquet** : comparer le réel du parc à chaque bouquet, au niveau choisi (N0, N1 ou N2).
5. **Rendu** : partir d'un gabarit (« Fiche d'identité », « Synthèse de parc ») ou d'une question du constructeur
   guidé, ajuster, enregistrer pour le parc, exporter.

## Géométrie et environnement

- **Géométrie croisée BD TOPO × LiDAR HD** (`api/croisement.py`, lot 30) : la BD TOPO seule se trompe de plusieurs
  mètres sur la hauteur. Avec un environnement relevé relié, l'emprise est recalée, la hauteur à l'égout et le
  faîtage sont mesurés, la toiture reconnue (terrasse, deux ou quatre pans, pente) ; voisins et arbres relevés
  remplacent la BD TOPO. Une correction de l'utilisateur prime ; la géométrie BD TOPO est rétablie si l'environnement
  est détaché.
- **Repli MNS** (`api/mns_source.py`, lot 31) : sans nuage LiDAR, MNS LiDAR HD raster (50 cm) puis MNS Correl
  (photogrammétrie, couverture nationale), classés sans la BD TOPO (sursol, NDVI de l'orthophoto infrarouge) ; la
  BD TOPO / OSM n'est plus que le dernier recours. Un toit non vu laisse la note « hauteur de la BD TOPO gardée — à
  vérifier ».
- **Horizon par profil** (`api/horizon.py`, lot 30) : profil d'obstruction par tranche de 10° d'azimut, mesuré depuis
  des points de chaque façade exposée à trois hauteurs ; le direct d'une façade est réduit à la part de ses points qui
  voient le soleil. Le diffus du ciel n'est pas masqué.
- **Protubérances** (`api/protrusions.py`, lot 33) : un mur qui dépasse du volume chauffé devient une ailette dont les
  deux faces sont dehors (N2 et Calcul 3D).
- **Sol simulé** (`api/engines/sol.py`, lot 32) : colonne de sol 1D de 15 m au pas horaire sur la météo du calcul,
  couplée au plancher selon son type (terre-plein, vide sanitaire, sous-sol non chauffé, local chauffé dessous).
  `plancher.u` reste le **U équivalent vers l'extérieur** (« U final » du DPE) ; le U propre du plancher en est déduit
  pour un vide sanitaire ou un sous-sol (EN ISO 13370).

## Moteurs

| Niveau | Méthode | Durée | Qui |
| --- | --- | --- | --- |
| **N0** | bilan mensuel ISO 13790 (proche 3CL) | < 0,1 s | qui peut modifier la fiche |
| **N1** | 5R1C horaire (ISO 13790 annexe C / ISO 52016-1), fourchette P10–P90 par tirages vectorisés | ~1 s | idem |
| **N2** | éléments finis par triangle de l'enveloppe (une paroi 1D par triangle, un nœud d'air), enveloppe reconstruite en mémoire depuis la fiche, ou enveloppe experte | secondes à minutes, en tâche de fond | **automatique** après la chaîne et après chaque affinage (file `parc`, sous le verrou des calculs lourds, D49) ; à la main : gestionnaires (D2) |

Les trois niveaux lisent la même fiche. Chaque calcul enregistre ses **entrées figées** (`CalcRun.inputs` : faits
utilisés et leur provenance, sol, géométrie, améliorations appliquées et leurs hypothèses) et une **empreinte** : un
calcul dont les entrées ont changé devient « périmé ».

**Calculs automatiques** (`api/recalcul/`, D49–D51) : un signal (fait, protection, géométrie, enveloppe,
environnement, météo, position, fin de chaîne, amélioration, bouquet, réglages du parc) ne fait que **planifier** le
bâtiment ; après le délai de regroupement (`calcul.recalcul_delai_s`, 30 s), une tâche recalcule ce qui est périmé :
réel N0 et N1 (file `celery`), puis réel N2 et potentiels N0, N1, N2 de chaque bouquet visible (file `parc`, N2 sous
`joblock` ; s'il est occupé, la tâche se replanifie). Rien de ce qui est à jour n'est recalculé. Réglages par parc
(Paramétrage › orchestration) : `calcul.recalcul_auto`, `calcul.n2`, `calcul.potentiels`. État :
`GET /api/batiments/<id>/calculs/etat/`, `GET /api/parcs/<id>/calculs/etat/` ; « Recalculer » (gestionnaire) :
`POST /api/batiments/<id>/calculs/recalculer/`. Un `QuerySet.update()` n'émet aucun signal : toute écriture d'une
entrée de calcul doit passer par `save()`. Versions des moteurs au 2026-11-16 : `n0-5`, `n1-6`, `n2-5`
(`calc_service.ENGINE_VERSION`).

Indicateurs (I3) : besoins de chauffage et de refroidissement, puissance maximale, énergie finale, primaire, CO₂,
coût (rendements 3CL, COP horaire d'une PAC), et, en N1/N2 seulement, **DH RE2020** (`dh_re2020`,
`heures_inconfort` : T_conf = 0,33·θ_rm + 21,8 °C bornée à [26 ; 28] °C le jour, 26 °C la nuit en logement), DH
base 26 °C (`dh_26`), séries mensuelles. Le N2 cumule aussi, **par triangle**, les pertes d'hiver (octobre–avril),
les gains d'été (juin–août), le solaire absorbé et les températures de surface intérieure
(`GET /api/calculs/<id>/triangles/`).

## Protections solaires

Catalogue v2 (`api/engines/shading.py`, servi par `GET /api/protections/catalogue/`), 16 modèles en quatre familles :
- **fixes** — casquette, balcon, débord de toiture (profondeur, hauteur de la sous-face, **inclinaison**, débords
  latéraux à gauche et à droite), joues (profondeur, hauteur, côté), loggia, brise-soleil à lames fixes ;
- **mobiles** — volet roulant, volet battant, persienne, store extérieur, store intérieur, brise-soleil orientable
  (BSO) : transmissions fermé, **ΔR** fermé (EN ISO 10077-1 annexe G), position, pilotage fixe / planning /
  automatique (éclairement de la façade, température extérieure), fermeture nocturne, lames suivant le soleil (BSO) ;
- **vitrage** — film, vitrage de contrôle solaire (facteur g) ;
- **végétal** — arbre caduc, pergola (transmission en saison de feuilles et hors saison).

Débords et joues par l'angle de profil (géométrie de l'EN ISO 52016-1 annexe F), bande de mur ombrée comprise ;
lames (inclinaison, largeur, entraxe, épaisseur, transmission) selon l'ISO 15099 § 7. N0/N1 : facteurs heure par
heure ; N2 : débords et joues construits en obstacles géométriques, mobiles, lames, vitrages et végétal appliqués par
triangle de baie. Chaque paramètre se règle dans Bâtiment › Enveloppe et systèmes ; une protection s'attache à l'état
actuel ou à une amélioration.

## Améliorations, bouquets, potentiels

- **Actions** (`api/ameliorations/catalogue.py`, `GET /api/ameliorations/actions/`) : 24 actions en six familles —
  isolation (ITE, ITI, plancher bas, double et triple vitrage), ventilation (VMC hygroréglable, double flux,
  étanchéité, surventilation nocturne), protections solaires (store sud, casquette sud, protection mobile, protection
  fixe, film ou vitrage de contrôle), végétalisation de la parcelle (arbres, abords végétalisés, toiture et murs
  végétalisés, rafraîchissement de l'air — **hypothèse affichée**), toiture et albédos (combles, toiture claire, murs
  clairs, abords clairs), chauffage et systèmes (PAC). Chacune dit ses paramètres, ses **conditions** (bloquante :
  l'action est écartée avec sa raison ; non bloquante : appliquée avec un avertissement), ses indicateurs pertinents
  (énergie, confort), ses cibles et comment N0, N1 et N2 l'appliquent. Une amélioration n'écrit que des faits, des
  surcharges (arbres, verdure, rafraîchissement) et des protections — jamais l'enveloppe ni l'environnement.
- **Bouquets** (`api/ameliorations/bouquets.py`) : liste ordonnée d'actions ou de choix « une parmi » (la première
  applicable) ; deux éléments ne visent jamais la même cible. Deux bouquets du lab, lisibles par tous, non
  modifiables : **« Économiser l'énergie »** (ITE ou ITI, combles, plancher bas, triple ou double vitrage, double flux
  ou hygroréglable, PAC) et **« Minimiser les DH »** (store extérieur sud et ouest piloté, surventilation nocturne,
  toiture végétalisée ou claire, combles, murs clairs, deux arbres au sud et à l'ouest). Un gestionnaire de parc les
  copie et les modifie, ou crée les siens.
- **Potentiels** (`api/ameliorations/potentiels.py`) : appliquer un bouquet crée une amélioration générée (unique par
  bâtiment et bouquet, **commune aux trois niveaux**, vidée et réécrite à chaque calcul) et la calcule en N0, N1 et
  N2. Calculés automatiquement (voir Moteurs) ; à la main : N0/N1 d'un bâtiment synchrones (4 bouquets au plus par
  appel), N2 d'un bâtiment par un gestionnaire, tout un parc à un niveau donné en file `parc`. Chaque entrée de
  `GET …/potentiels/` porte son `niveau` : un consommateur filtre par bouquet **et** par niveau. « À jour » par empreinte (fiche, protections, géométrie, météo,
  bouquet, réglages du parc, versions des moteurs et des actions). Écarts au réel par indicateur ; agrégats de parc
  (somme, moyenne, médiane, effectif).

Deux réglages de confort d'été à connaître : l'**ouverture des fenêtres en été**, réglable par parc (Paramétrage),
**désactivée par défaut** (seuil 25 °C, extérieur plus frais d'1 °C, 3 vol/h, tertiaire aux heures d'occupation) ; et
le **by-pass d'été** de la double flux, toujours modélisé en N1/N2 (échangeur coupé quand l'air extérieur, au-dessus de
12 °C, est plus frais que dedans et que l'intérieur dépasse la consigne de 2 °C — seuils à confirmer).

## Rendu et exports

- Le **serveur est le seul producteur** des figures et des documents (`api/rendu/`) : l'aperçu Angular affiche ce que
  le serveur construit, les exports reprennent le même document résolu.
- Spécification d'un document (`GET /api/rendu/schema/`) : sections de blocs — texte (markdown restreint, assaini),
  indicateurs, tableau, graphe, carte (sans fond de carte en ligne), vue 3D, fiche, saut de page.
- **15 formes de graphes** Plotly (barres, empilées, groupées, courbes, aires, nuage, bulles, boîte, violon,
  histogramme, camembert, radar, carte de chaleur, cascade, coordonnées parallèles) ; séries réel / bouquets, couleur,
  taille, facettes, filtres, agrégations ; aucune couleur écrite par le serveur (palette posée au rendu).
- Gabarits du lab « Fiche d'identité » et « Synthèse de parc » ; **18 questions** prêtes à l'emploi pour le
  constructeur guidé ; modèles enregistrés par parc (écriture : gestionnaire du parc).
- **Exports** (réservés à leur auteur, transitoires 24 h, volume `rendu-exports`) :
  - **HTML** autonome : plotly.js et three.js embarqués, interactif hors ligne, sommaire cliquable, CSS d'impression,
    `Content-Security-Policy: default-src 'none'` ;
  - **PDF** : ce même HTML imprimé par le service de rendu (signets, sommaire, numéros de page) ;
  - **Word** (python-docx) : styles de titre (sommaire actualisable), vrais tableaux, figures et vues 3D en images
    fournies par le service de rendu, légendes numérotées, liens internes.

## Conteneurs et ressources

| Conteneur | Rôle | Réseaux |
| --- | --- | --- |
| `bilan-thermique-backend` | API Django (gunicorn) | `sso-net`, `dev-net`, réseau de l'app, réseau de rendu |
| `bilan-thermique-worker` | Celery, concurrence 1, files `celery` puis `parc` (D13) | `dev-net`, réseau de l'app, réseau de rendu |
| `bilan-thermique-redis` | courtier Celery | réseau de l'app |
| `bilan-thermique-rendu` | service de rendu (`rendu-service/` : Node + Playwright/Chromium) : PDF, images des figures et des vues 3D | réseau **interne** `bilan-thermique-rendu-net` seulement |
| `bilan-thermique-frontend` | Angular servi par nginx | `sso-net` |

- Machine du lab : **2 vCPU**. Un seul calcul lourd à la fois (verrou atomique, `api/joblock.py`) ; le service de rendu
  traite une requête à la fois, 60 s au plus.
- Service de rendu : aucun port publié, aucun label Caddy, réseau interne sans sortie, et toute requête du navigateur
  autre que `data:`/`about:` abandonnée ; en-tête `X-Rendu-Cle` = `RENDU_SERVICE_KEY` (`.env` de l'app) ; conteneur
  en lecture seule, `cap_drop: ALL`, 1 Go de mémoire au plus. Jamais de Chromium dans l'image Django.
- Volumes : `facade-cache` (cache recalculable des façades) et `rendu-exports` (exports de 24 h), non `external` :
  perdus sans dommage par un redéploiement complet.
- Fichiers des utilisateurs (documents, imports) : par l'API `storage` du lab (`DEMANDES_LAB.md`, DL-2).

## Tests

- Backend : `python manage.py test` (dans `backend/`), un module par domaine dans `backend/api/tests/` — **743 tests**
  au 2026-11-16. Aucun appel réseau : tout service externe (open data, IA, service de rendu) est simulé. Depuis le lab :
  `bash chantiers/tests/essai.sh bilan-thermique` (PostgreSQL jetable, réseau fermé).
- Service de rendu : `npm test` (unitaires) et `npm run test:integration` dans `rendu-service/` (dans son image).
- Frontend : pas de tests unitaires ; `ng build` et vérification en navigateur réel (voir `frontend/README.md`).
- Cloisonnement : `frontend/e2e/cloisonnement.spec.ts` (gabarit du lab, joué par le moteur du lab).

## Limites connues

- Un seul nœud d'air (pas de multizone, D9) ; ponts thermiques non comptés ; pas d'échange infrarouge avec le ciel.
- Le diffus du ciel n'est masqué ni par l'horizon ni par les arbres plantés (N0/N1).
- Le N0 (mensuel) ne représente ni la surventilation nocturne, ni l'ouverture des fenêtres, ni le by-pass, et ne
  donne aucun indicateur de confort.
- Sol : un seul sol par défaut (limoneux humide) ; l'intérieur des nœuds tampons est pris à la consigne (couplage non
  itéré).
- Végétalisation : modèle simple (évapotranspiration de Priestley-Taylor, eau disponible β = 0,5 supposée) ; le
  rafraîchissement de l'air n'est **pas calculé**, c'est une hypothèse recopiée telle quelle.
- By-pass de la double flux et ouverture des fenêtres : seuils choisis par l'app (réglages usuels), à confirmer.
- Une enveloppe experte (N2, D35) ignore les améliorations d'enveloppe : le calcul le dit.
- Calculs automatiques : sur 2 vCPU, le N2 et les potentiels N2 d'un grand parc prennent des heures (un calcul lourd
  à la fois) ; `calcul.n2` coupe le N2 automatique par parc. Les rendus prennent par défaut le niveau le plus fin à
  jour, et le disent dans le résumé de chaque figure.

## Dépendances

Toutes installées **dans les images Docker** (aucun script d'installation sur l'hôte : `lab-module.json`,
`"install": null`).

### Backend (`backend/requirements.txt`)

| Paquet | Rôle | Licence | Taille indicative |
| --- | --- | --- | --- |
| Django, djangorestframework, drf-spectacular, django-cors-headers, python-decouple, gunicorn | API | BSD / MIT | ~15 Mo |
| psycopg2-binary | PostgreSQL | LGPL | ~8 Mo |
| PyJWT, cryptography | jetons Keycloak, coffre chiffré | MIT / Apache-2.0 | ~10 Mo |
| numpy, scipy | solveurs (FEM, N0, N1, sol) | BSD | ~90 Mo |
| celery, redis | file de tâches | BSD / MIT | ~5 Mo |
| trimesh, rtree, shapely, mapbox_earcut, embreex | géométrie, lancer de rayons | MIT / Apache-2.0 | ~40 Mo |
| requests | appels open data | Apache-2.0 | <1 Mo |
| laspy[lazrs], pyproj, triangle, pillow | LiDAR HD, projections, triangulation, orthophotos | BSD / MIT / usage non commercial (triangle) / HPND | ~60 Mo |
| anthropic 1.13.0 (refonte, Lot 21) | SDK Claude pour l'extraction de documents, avec la clé de l'utilisateur | MIT | ~5 Mo |
| pypdf 5.4.0 (refonte, Lot 21) | lecture du texte des PDF (pas d'OCR, D16) | BSD-3-Clause | ~1 Mo |
| python-docx 1.1.2 (lot 35) | export Word | MIT | ~1 Mo |
| onnxruntime, tokenizers, pycolmap | détection des baies, photogrammétrie (désactivée par défaut) | MIT / Apache-2.0 / BSD | ~150 Mo |

`backend/vendor/` : `plotly-2.35.2.min.js` et `three-0.180.0.global.js`, embarqués dans l'export HTML (licences :
`backend/vendor/LICENCES.txt`).

### Service de rendu (`rendu-service/package-lock.json`)

| Paquet | Rôle | Licence |
| --- | --- | --- |
| playwright 1.48.0 (image `mcr.microsoft.com/playwright:v1.48.0-jammy`) | Chromium : PDF, images | Apache-2.0 |
| plotly.js-dist-min 2.35.2, three 0.180.0 | mêmes versions que l'export HTML et le frontend | MIT |

### Frontend (`frontend/package-lock.json`, installé par `npm ci`)

| Paquet | Rôle | Licence |
| --- | --- | --- |
| Angular 21 | interface | MIT |
| keycloak-js 22 | connexion | Apache-2.0 |
| three 0.180 | vues 3D | MIT |
| leaflet 1.9.4 (refonte, Lot 19) | cartes : parc, sélection d'emprises, position d'un bâtiment ; fonds IGN Géoplateforme sans clé | BSD-2-Clause, ~150 Ko |
| plotly.js-dist-min 2.35.2 (lot 35) | graphes, chargé à la demande (hors bundle initial) | MIT, ~3,5 Mo |
| @types/leaflet 1.9.22, @types/plotly.js-dist-min 2.3.4 (développement) | types TypeScript | MIT |

## Couleurs en dur — exception documentée

Identité « Foyer » : aucune couleur en dur dans un composant ; les couleurs des graphes et des vues de résultats
viennent des jetons de données de `src/styles.scss` (`--serie-1` à `--serie-8`, `--seq-*`, `--div-*`, en clair et en
sombre), relus à l'exécution par `components/graphe/palette.ts` — y compris pour three.js, qui ne lit pas les
variables CSS (échelle des températures de Calcul 3D et des résultats en 3D depuis le lot 39). Exceptions restantes :
le repli gris du visualiseur (`mesh-viewer.component.ts`, quand un jeton ne se résout pas) et le thème d'impression des
exports (`backend/api/rendu/theme.py`, palette fixe lisible sur papier blanc, d'après Okabe et Ito).
