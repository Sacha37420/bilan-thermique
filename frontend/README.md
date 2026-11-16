# bilan-thermique — frontend

Interface Angular 21 (composants autonomes, signaux) de l'app `bilan-thermique`, servie par nginx sous
`/bilan-thermique/` ; l'API est sous `/bilan-thermique-api/`. Présentation fonctionnelle : `../README.md`.

## Structure

```
src/app/
  app.routes.ts          six onglets + outils experts + anciennes adresses redirigées (chaque page en loadComponent)
  core/                  Keycloak, intercepteur (jeton joint aux seules bases de token-scope.ts), contexte
                         parc/bâtiment courant (contexte.service.ts, ?p=&b=), thème, clients d'API communs
  pages/
    onglet-parc/         Bâtiments, Potentiels par bouquet, Ajouter, Import et partage, Sources des données
    onglet-batiment/     Synthèse, Données, Géométrie, Résultats en 3D, Enveloppe et systèmes, Météo,
                         Environnement et façades, Factures et mesures
    onglet-calculs/      Les simulations (catalogue GET /api/calculs/moteurs/), Lancer, Améliorations, Historique
    onglet-parametrage/  réglages par modèle de calcul et par parc, éditeur des bouquets du parc
    onglet-ia/           clés d'IA, imports (parc, bâtiment)
    onglet-rendu/        Paramétrage du rendu (constructeur de graphes, éditeur de blocs) et Rendu (aperçu,
                         exports) ; client d'API propre (rendu-api.service.ts)
    batiment/ environnement/ calcul-3d/ calcul-1d/ parois/ theorie/   outils experts, hors menu
  components/            composants partagés, notamment :
    contexte-bar/        bandeau du parc et du bâtiment courants
    graphe/              une figure Plotly construite par le serveur (Plotly chargé à la demande, palette des jetons)
    vue3d-resultats/     résultats N2 par triangle sur le maillage calculé (échelle, légende, triangle touché)
    mesh-viewer/         afficheur three.js (couleur par triangle, repaint sans reconstruire la géométrie)
    protections/         catalogue v2 des protections : choisir un modèle, régler ses paramètres, l'attacher
    scenarios/           améliorations, potentiels du bâtiment, éditeur de bouquets
    simulation/ calculs/ resultat-carte/ questionnaire/ geometrie/ systemes/ meteo-carte/ mesures/ documents/ …
```

Règles : toute liste servie à l'écran vient du serveur (catalogues des protections, des actions, des dimensions et
mesures du rendu, des moteurs) — aucune recopie de catalogue côté Angular ; les messages d'erreur affichés sont ceux,
fixes, du serveur.

## Construire

```bash
npm ci                                                        # installe exactement le verrou
flock /tmp/bt-ng.lock npx ng build --configuration development   # vérification rapide (verrou : 2 vCPU)
flock /tmp/bt-ng.lock npx ng build                               # production (ce que fait l'image Docker)
```

Le `flock` évite deux compilations simultanées sur la machine du lab. L'image (`Dockerfile`) fait `npm ci` puis
`npm run build` et copie `dist/bilan-thermique-frontend/browser` dans nginx ; `nginx-entrypoint.sh` écrit `env.js`
(variables non secrètes seulement). Pas de `ng serve` en usage normal : on vérifie sur l'app déployée.

**Budgets** (`angular.json`, production) : bundle initial 500 ko (avertissement) / 1 Mo (erreur) ; style d'un
composant 4 ko / 8 ko. Les onglets et outils sont chargés à la demande, et les grosses bibliothèques ne pèsent que sur
leur route : three.js (vues 3D), Leaflet (cartes) et surtout **plotly.js** (`plotly.js-dist-min` 2.35.2, ≈ 3,5 Mo),
importé dynamiquement par `components/graphe/graphe.component.ts` au premier graphe affiché, une seule fois. Ne jamais
l'importer statiquement : il entrerait dans le bundle de toute page qui l'importe.

## Graphes et vues 3D

- Le navigateur n'assemble aucune donnée : il reçoit une figure Plotly du serveur (`POST /api/rendu/figure/`, ou un
  bloc du document résolu) et l'affiche par `Plotly.react`. Le serveur n'écrit aucune couleur ; `palette.ts` pose la
  palette selon `layout.meta.palette` (catégorielle, séquentielle, divergente) et la relit à chaque bascule de thème.
- Accessibilité : titre en légende de figure, résumé textuel construit par le serveur (`layout.meta.resume`), zone de
  dessin nommée.
- `vue3d-resultats` colore chaque triangle selon une grandeur I4 (pertes d'hiver, gains d'été, solaire absorbé,
  températures de surface), bornes aux percentiles 2–98, légende ; un triangle touché montre toutes ses valeurs.

## Identité « Foyer »

Tokens CSS en tête de `src/styles.scss` (thème clair, sombre par `prefers-color-scheme` et bascule `[data-theme]`) ;
aucune couleur en dur dans un composant. Données : `--serie-1` à `--serie-8` (palette catégorielle), `--seq-bas`,
`--seq-haut` (séquentielle), `--div-froid`, `--div-neutre`, `--div-chaud` (divergente). Navigation verticale, rupture
fixe à 900 px, cibles tactiles de 44 × 44 px au moins, polices Inter et JetBrains Mono auto-hébergées. Exception
documentée : `../README.md`, « Couleurs en dur ».

## Tests

- **Aucun test unitaire** (pas de `ng test` en usage) : la vérification d'un changement d'interface est
  `ng build` sans erreur ni dépassement de budget, puis un **navigateur réel** sur l'app (compte de test, thèmes
  clair et sombre, console sans erreur).
- `e2e/cloisonnement.spec.ts` : test de cloisonnement du gabarit du lab, joué par le moteur du lab après chaque
  déploiement ; ne pas en ajouter d'autre dans ce dossier (règle du lab).
