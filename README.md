# bilan-thermique

Simulateur thermique de bâtiments du lab. Deux régimes sur un même modèle de bâtiment
(refonte en cours, `refonte.md`) :

1. **Parc** — un gestionnaire importe une liste de bâtiments ; chacun est initialisé depuis l'open
   data (RNB, BDNB, IGN) et calculé sans saisie (niveaux N0/N1), avec confiance et complétude.
2. **Affinage** — le gestionnaire ou l'occupant d'un bâtiment corrige et complète la fiche ; chaque
   valeur garde sa provenance, chaque correction fait monter la confiance.

Pages expertes existantes : calcul 1D d'une paroi, bâtiment 3D (FEM par triangle, N2), ombrage par
lancer de rayons, environnement LiDAR HD × BD TOPO, façades Panoramax.

- Feuille de route (lue par le lab) : `FEUILLE_DE_ROUTE.md` ; cahier des charges de la refonte et
  décisions : `refonte.md` ; changements demandés au lab : `DEMANDES_LAB.md` ; journal des lots
  A→AM (archive) : `to_do_bilan_thermique.md`.
- Tests backend : `python manage.py test` (dans `backend/`), découpés par domaine dans
  `backend/api/tests/`. Aucun appel réseau : tout service externe est simulé.

## Ressources et limites

- Machine du lab : 2 vCPU. Le worker Celery tourne en concurrence 1 ; un seul calcul lourd à la fois
  (verrou atomique, `api/joblock.py`). N2 n'est jamais lancé en lot.
- Volume `facade-cache` : cache recalculable (photos, modèles de détection, textures) — vidé sans
  perte par un redéploiement complet.
- Fichiers des utilisateurs (documents, imports) : par l'API `storage` du lab (`DEMANDES_LAB.md`, DL-2).

## Dépendances

Toutes installées **dans les images Docker** (aucun script d'installation sur l'hôte :
`lab-module.json`, `"install": null`).

### Backend (`backend/requirements.txt`)

| Paquet | Rôle | Licence | Taille indicative |
| --- | --- | --- | --- |
| Django, djangorestframework, drf-spectacular, django-cors-headers, python-decouple, gunicorn | API | BSD / MIT | ~15 Mo |
| psycopg2-binary | PostgreSQL | LGPL | ~8 Mo |
| PyJWT, cryptography | jetons Keycloak, coffre chiffré | MIT / Apache-2.0 | ~10 Mo |
| numpy, scipy | solveurs (FEM, N0, N1) | BSD | ~90 Mo |
| celery, redis | file de tâches | BSD / MIT | ~5 Mo |
| trimesh, rtree, shapely, mapbox_earcut, embreex | géométrie, lancer de rayons | MIT / Apache-2.0 | ~40 Mo |
| requests | appels open data | Apache-2.0 | <1 Mo |
| laspy[lazrs], pyproj, triangle, pillow | LiDAR HD, projections, triangulation, orthophotos | BSD / MIT / usage non commercial (triangle) / HPND | ~60 Mo |
| onnxruntime, tokenizers, pycolmap | détection des baies, photogrammétrie (désactivée par défaut) | MIT / Apache-2.0 / BSD | ~150 Mo |

### Frontend (`frontend/package-lock.json`, installé par `npm ci`)

| Paquet | Rôle | Licence |
| --- | --- | --- |
| Angular 21 | interface | MIT |
| keycloak-js 22 | connexion | Apache-2.0 |
| three | vues 3D | MIT |

Les dépendances ajoutées par la refonte sont listées ici au lot qui les introduit.

## Couleurs en dur — exception documentée

Identité « Foyer » : aucune couleur en dur. Exception : les couleurs passées à **three.js**
(WebGL ne lit pas les variables CSS) — échelle de température de Calcul 3D
(`calcul-3d.component.ts`, elle encode une donnée et ne change pas avec le thème) et repli gris du
visualiseur (`mesh-viewer.component.ts`, qui résout sinon les tokens CSS à l'exécution).
