# bilan-thermique

Simulateur thermique de bâtiments du lab. Deux régimes sur un même modèle de bâtiment
(refonte en cours, `refonte.md`) :

1. **Parc** — un gestionnaire importe une liste de bâtiments ; chacun est initialisé depuis l'open
   data (RNB, BDNB, IGN) et calculé sans saisie (niveaux N0/N1), avec confiance et complétude.
2. **Affinage** — le gestionnaire ou l'occupant d'un bâtiment corrige et complète la fiche ; chaque
   valeur garde sa provenance, chaque correction fait monter la confiance.

Un bâtiment s'étudie dans sa **fiche** : Synthèse, **Simulation** (préparer un calcul N0, N1 ou N2 : entrées,
fiabilité, durée), Météo, Géométrie, **Maillage 3D** (enveloppe et parois assignées, page Bâtiment intégrée),
**Environnement** (LiDAR HD × BD TOPO), Protections solaires, Systèmes, Mesures et calage, Scénarios, **Calculs**
(historique, entrées figées, résultats, comparaison), Documents, Données et provenance. Les trois niveaux (N0 mensuel,
N1 5R1C horaire, N2 éléments finis par paroi) partent de la même fiche ; ce qui se fait sur le maillage, l'environnement
ou les façades l'alimente (Lots 23 à 27).

Outils experts hors fiche : calcul 1D d'une paroi, modèles de paroi, import d'un maillage, environnement d'un quartier,
Calcul 3D avancé (planning horaire, régime imposé ou libre, ombrage temps réel, vent), théorie.

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
| anthropic 1.13.0 (refonte, Lot 21) | SDK Claude pour l'extraction de documents, avec la clé de l'utilisateur | MIT | ~5 Mo |
| pypdf 5.4.0 (refonte, Lot 21) | lecture du texte des PDF (pas d'OCR, D16) | BSD-3-Clause | ~1 Mo |
| onnxruntime, tokenizers, pycolmap | détection des baies, photogrammétrie (désactivée par défaut) | MIT / Apache-2.0 / BSD | ~150 Mo |

### Frontend (`frontend/package-lock.json`, installé par `npm ci`)

| Paquet | Rôle | Licence |
| --- | --- | --- |
| Angular 21 | interface | MIT |
| keycloak-js 22 | connexion | Apache-2.0 |
| three | vues 3D | MIT |
| leaflet 1.9.4 (refonte, Lot 19) | cartes : parc, sélection d'emprises, position d'un bâtiment ; fonds IGN Géoplateforme sans clé | BSD-2-Clause, ~150 Ko |
| @types/leaflet 1.9.22 (développement) | types TypeScript | MIT |

Les dépendances ajoutées par la refonte sont listées ici au lot qui les introduit. Côté backend, la refonte
n'a ajouté **aucune** dépendance (N0, N1, Morris, calage, protections solaires : numpy et scipy déjà présents).

## Couleurs en dur — exception documentée

Identité « Foyer » : aucune couleur en dur. Exception : les couleurs passées à **three.js**
(WebGL ne lit pas les variables CSS) — échelle de température de Calcul 3D
(`calcul-3d.component.ts`, elle encode une donnée et ne change pas avec le thème) et repli gris du
visualiseur (`mesh-viewer.component.ts`, qui résout sinon les tokens CSS à l'exécution).
