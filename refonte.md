# Refonte bilan-thermique — cahier des charges et suivi

> **Fichier de référence de la refonte** (branche `refonte-parc` du dépôt de l'app). Issu de la
> fusion, le 2026-11-14, de `refonte-bilan-thermique.md` (base fournie par le propriétaire, rédigée
> le même jour) et de l'ancien `refonte.md` — voir « Arbitrages de fusion » en fin de fichier.
> État du code relevé à l'origine : `9f8cc8d` (2026-11-11).
> Les règles d'installation d'un module viennent du chantier « modules »
> (`chantiers/modules/REGLES_MODULE.md`).
> Légende : [C] constaté dans le code ou la feuille de route, [D] déduit, [R] recherche externe
> (source en fin de fichier), Ⓟ question qui attendait le propriétaire — **toutes tranchées en
> session**, voir « Décisions prises en session ».

---

## 0. Objet en une phrase

Faire de bilan-thermique un outil à **deux régimes sur un même modèle de bâtiment** :

1. **Automatique (parc)** : un gestionnaire de parc importe une liste de bâtiments ; chaque
   bâtiment est initialisé depuis l'open data et calculé sans intervention, avec un niveau de
   confiance affiché.
2. **Affiné (bâtiment)** : le gestionnaire ou l'occupant d'un bâtiment du parc corrige et complète
   ce modèle (formulaires, météo choisie, géométrie, protections solaires, systèmes, mesures) ;
   chaque correction relance seulement ce qui en dépend et fait monter la confiance.

Le principe directeur : **ne jamais repartir d'une fiche vierge, ne jamais écraser une donnée sans
garder sa provenance.**

---

## 1. État des lieux du module actuel [C]

### 1.1 Ce qui existe et fonctionne

| Domaine | Existant | Fichiers |
| --- | --- | --- |
| Paroi 1D | Éléments finis multicouches, Euler implicite au pas horaire, rayonnement propagé couche par couche | `api/solver.py` |
| Bâtiment 3D | Chaque triangle = une paroi 1D couplée à **un seul nœud d'air** ; système creux, LU factorisée une fois par combinaison distincte `(g_vent, h_e, volets)` | `api/building_solver.py` |
| Ombrage | Lancer de rayons, mode précalculé (grille 15°) ou temps réel, auto-ombrage, végétation (passe `multiple_hits`) | `api/shadow.py` |
| Météo | Open-Meteo Archive (année réelle datée, Lot L), PVGIS TMY (Lot S), position solaire calculée (Spencer), décalage UTC/heure locale (AB4) | `api/weather_source.py` |
| Environnement | LiDAR HD × BD TOPO (Lot AH), terrain, houppiers LiDAR + essences CoSIA (Lot AJ), objets activables, bâtiment étudié choisi dans la scène | `api/observed_env.py`, `api/lidar_source.py`, `api/geodata.py`, `api/elevation.py` |
| Façades | Photos Panoramax, baies détectées, vrais vitrages insérés (Lots AK, AL) ; SfM pycolmap écrit mais désactivé (Lot AM) | `api/facades.py`, `api/facade_texture.py`, `api/facade_sfm.py` |
| DPE | Vitrages déclarés via RNB `closest` → BDNB `dpe_representatif_logement` (proportion de baies, Uw, g, menuiserie) | `api/bdnb.py` |
| Usage | Ventilation + récupération, apports internes, plannings horaires, calendrier d'occupation (ouvré / week-end / vacances scolaires réelles), thermostat | Lots G, H, Q, V, AF, AG |
| Fenêtres | Cadre (Uw, fraction de cadre), occultations mobiles (volet roulant, store extérieur) **toujours entièrement fermées**, **un seul planning pour toutes les fenêtres** | Lots I, J |
| Sol | Triangles au contact du sol, résistance de sol et découplage du vent | Lots K, AB3 |
| Albédo et absorptance | Albédo du sol relevé sur les orthophotos IGN (½ visible RVB + ½ proche infrarouge IRC, eau reprise de la classe 9 du LiDAR), grille de 2 m ; réflexion vers chaque triangle `ρ_vu·(1 − cos β)/2 · GHI`, ρ_vu lu par lancer de rayons vers le bas (voisins masquants) ; absorptance des toitures = 1 − albédo relevé, murs selon le matériau BD TOPO | Lot AI — `observed_env.py` (`OrthoImage`, `ground_albedo_grid`), `shadow.compute_ground_reflection_factors`, `building_solver.py` |
| Parcours | Mode simplifié (recherche de bâtiment + taux de vitrage, jusqu'au résultat), générateur de boîte, checklist guidée | Lots T, W, AE, O, N |
| Tests | 244 tests backend dans un seul `tests.py` de 4 514 lignes | `backend/api/tests.py` |

Pages Angular : `theorie`, `calcul-1d`, `parois`, `mode-simplifie`, `batiment`, `environnement`,
`calcul-3d`.

### 1.2 Limites qui bloquent la cible [C]

| # | Limite | Conséquence pour la refonte |
| --- | --- | --- |
| E1 | Aucune notion de **parc** ni de lot de bâtiments ; `Building.name` est unique globalement | Impossible de lancer N bâtiments, de comparer, de prioriser |
| E2 | Aucun **historique des calculs** (U3) : le résultat est perdu en quittant Calcul 3D | Pas de comparaison avant/après, pas de tableau de parc |
| E3 | Pas de **provenance** des valeurs : une donnée BDNB, une saisie et un défaut sont indiscernables | Impossible d'afficher une confiance ou de savoir quoi demander à l'utilisateur |
| E4 | Résultat = **besoin** (thermostat) ; aucun système (génération, émission, régulation, ECS), aucun rendement | Pas de consommation en énergie finale/primaire, pas de comparaison aux factures ni au DPE |
| E5 | **Un seul nœud d'air** (multizone hors périmètre, décision documentée) | Pas de distinction logement/parties communes, ni par étage ou orientation |
| E6 | Protections solaires **mobiles seulement**, binaires, planning unique ; aucune protection **fixe** paramétrée (casquette, joues, loggia, brise-soleil) hors géométrie maillée à la main | Le confort d'été et les gains solaires sont mal pilotables par un non-modélisateur |
| E7 | Garde-fous de calcul incomplets (Lot 2 : passe végétation non comptée, pas de verrou atomique) | Un traitement de parc saturerait le worker (2 vCPU, concurrence 1) |
| E8 | Pas de carte pour choisir les coordonnées (U4) | Saisie de parc et vérification de position laborieuses |
| E9 | Paroi mitoyenne non implémentée | Erreur majeure sur tout le bâti accolé (centres-villes, collectifs) |

Restent hors périmètre par décision, à rediscuter seulement si la refonte le demande : ponts
thermiques linéiques, IR grande longueur d'onde vers le ciel, réflexions multiples entre vitrages.

> Attention : la section « Hors scope » de `to_do_bilan_thermique.md` cite encore l'albédo du sol.
> Elle est antérieure au Lot AI, qui l'a implémenté : la ligne est à corriger dans le journal.

Limites restantes de l'albédo [C, page Théorie] : le sol est éclairé uniformément par le GHI (ses
ombres portées sont ignorées) ; les façades voisines sont des obstacles « noirs » qui ne
réfléchissent rien ; le terme n'existe que pour un environnement généré depuis le LiDAR et les
orthophotos (rien pour un environnement importé ou ancien, ni pour un bâtiment sans environnement).

---

## 2. Utilisateurs et niveaux d'usage

| Profil | Ce qu'il fait | Ce qu'il voit | Niveau de calcul |
| --- | --- | --- | --- |
| Gestionnaire de parc | Importe un parc, lance l'analyse, priorise | Tableau et carte du parc, classement, confiance par bâtiment | N0 / N1 automatiques |
| Gestionnaire de bâtiment | Affine **un** bâtiment qui lui est confié | Fiche bâtiment, formulaires, éditeurs, scénarios | N1, N2 à la demande |
| Occupant (facultatif, Ⓟ) | Répond à un questionnaire court, signale un inconfort | Questionnaire, résultat simplifié | Aucun calcul lancé directement |
| Expert / propriétaire du lab | Tout le reste : parois, théorie, calcul 1D, réglages physiques | Pages actuelles | N2 (FEM 3D) |

Droits : rattacher ces profils aux groupes Keycloak du lab (accès actuel :
`developers,famille,amis`). Un gestionnaire de bâtiment ne voit que les bâtiments partagés avec lui.
Les rôles de l'app (gestionnaire de parc, gestionnaire de bâtiment, lecteur) sont **distincts de la
porte** (groupes requis), comme dans la forme v2 d'App Builder ; ils vivent en base, par parc.
Toute personne est désignée par son `sub` Keycloak, jamais par son email (piège noté dans
`CLAUDE.md`). Ⓟ : réutiliser le modèle de partage de `storage` ou un modèle propre à l'app.

---

## 3. Principes de conception

1. **Un modèle de bâtiment, trois niveaux de détail.** La même fiche alimente N0 (statique,
   instantané), N1 (horaire réduit, quelques secondes) et N2 (FEM par triangle, existant).
   Chaque niveau lit ce dont il a besoin et ignore le reste. C'est l'approche des « enrichisseurs
   de données » : séparer jeux de données et moteur, et adapter un même moteur à une saisie
   minimale, intermédiaire ou détaillée [R1].
2. **Provenance par valeur.** Chaque attribut porte `source`, `méthode`
   (`mesuré > document > déclaré > inféré > défaut`), `confiance` (0–1), `auteur`, `date`.
   Une valeur de rang supérieur prime sans effacer l'autre (historique conservé).
3. **Surcharges plutôt que modifications.** La correction d'un utilisateur est une surcharge
   posée sur la valeur automatique. On peut la retirer pour revenir à l'automatique, et la
   réimportation d'un millésime BDNB ne l'écrase pas.
4. **Variantes.** Un bâtiment a une **base** (état actuel) et des **scénarios** (travaux,
   protections, météo future) qui ne stockent que leurs différences.
5. **Recalcul incrémental.** Un graphe de dépendances dit ce qui est périmé : changer la météo ne
   relance pas l'ombrage ; ajouter une casquette relance l'ombrage de la façade concernée, puis le
   calcul ; corriger une hauteur relance tout. Le mécanisme `sun_visibility_stale` existant est le
   germe de ce graphe.
6. **Questions guidées par la sensibilité.** L'outil demande d'abord ce qui change le plus le
   résultat et dont la confiance est faible (par exemple : isolation des murs avant couleur des
   volets).
7. **Contrainte machine assumée.** 2 vCPU et un worker en concurrence 1 : le parc tourne en N0/N1
   dans une file dédiée, de nuit si besoin ; N2 reste unitaire et à la demande.

---

## 4. Parcours A — analyse automatique d'un parc

### 4.1 Entrées acceptées

- Fichier CSV/XLSX : adresse, ou identifiant RNB, ou lat/lon, plus des colonnes facultatives
  (nom, usage, surface, année, énergie de chauffage, consommation annuelle).
- Sélection sur carte : clic ou lasso sur les emprises (résout aussi U4).
- Liste d'identifiants RNB collée.

### 4.2 Chaîne automatique par bâtiment

| Étape | Traitement | Source | Sortie | Coût |
| --- | --- | --- | --- | --- |
| A1 Identification | Géocodage BAN, puis RNB `closest` (déjà dans `bdnb.py`) | BAN, RNB | `rnb_id`, emprise | réseau |
| A2 Fiche BDNB | Année, usage, nombre de logements, matériaux murs/toiture, énergie de chauffage, DPE réel ou prédit, mitoyenneté | BDNB API (limite : 120 requêtes/min, relevée dans `bdnb.py`) | Attributs `inféré` ou `document` | réseau |
| A3 Géométrie | Emprise BD TOPO + hauteurs/toiture LiDAR HD ; à défaut, extrusion hauteur BD TOPO | IGN | Enveloppe LOD1 ou LOD2 | moyen |
| A4 Baies | Taux de vitrage par orientation depuis le DPE (existant) ; sinon archétype par période | BDNB/DPE, archétypes | Baies par façade | faible |
| A5 Compositions | Parois et menuiseries par archétype (période × usage × matériau), dans le catalogue `ParoiModel` | Archétypes TABULA / typologies françaises | Paroi par groupe de triangles | faible |
| A6 Masques et albédo | Horizon simplifié par façade (voisins BD TOPO + relief), sans maillage d'environnement complet ; albédo moyen du sol devant chaque façade et albédo de toiture lus sur la grille orthophoto du Lot AI (réutilisée telle quelle, sans lancer de rayons) | IGN (BD TOPO, MNT, orthophotos RVB + IRC) | Facteur d'ombrage et facteur de réflexion par façade ; absorptance de toiture | faible |
| A7 Météo | Année type de la zone, ou dernière année réelle si on compare à des factures | PVGIS TMY / Open-Meteo / Météo-France | Série horaire | réseau, en cache |
| A8 Calcul N1 | Modèle horaire réduit (§ 6) | — | Besoins, puissance, surchauffe | ~secondes |
| A9 Indicateurs | kWh/m².an, kW, heures d'inconfort, comparaison au DPE affiché, score de confiance | — | Ligne du tableau de parc | — |

Règles :
- Chaque appel externe est **mis en cache** par clé (`rnb_id`, millésime) pour ne pas refaire le
  réseau à chaque relance.
- Les erreurs d'une étape n'arrêtent pas le parc : le bâtiment passe en « incomplet » avec la
  raison, et il est rejouable seul.
- Le traitement tourne dans une **file Celery dédiée** (`parc`), un bâtiment à la fois, avec reprise
  après coupure. Aucun calcul N2 n'est lancé automatiquement.

### 4.3 Restitution parc

- Tableau triable : bâtiment, usage, surface, besoin kWh/m².an (fourchette), étiquette DPE
  affichée, écart modèle/DPE, heures > 28 °C, confiance, complétude de la fiche.
- Carte des bâtiments colorée par indicateur (bibliothèque cartographique à introduire, Lot 7 / U4).
- Priorisation : besoin × surface × confiance, avec filtres.
- Export CSV/XLSX et lien vers la fiche de chaque bâtiment.

---

## 5. Parcours B — affinage d'un bâtiment

Chaque module ci-dessous est une **carte** de la fiche bâtiment, avec son état (automatique /
corrigé / mesuré), son influence estimée sur le résultat et un bouton « revenir à l'automatique ».

### B1 — Formulaires guidés

Questionnaire par blocs, chaque champ prérempli avec sa valeur automatique et sa source :

| Bloc | Champs principaux |
| --- | --- |
| Identité | Usage, année de construction, surface habitable/utile, nombre de niveaux et de logements, mitoyenneté par façade |
| Enveloppe | Murs (matériau, épaisseur, isolation : type, épaisseur, côté intérieur/extérieur, année), toiture/combles, plancher bas (terre-plein, vide sanitaire, sous-sol) |
| Menuiseries | Par façade : vitrage (simple/double/triple, faible émissivité), menuiserie, Uw, g, présence d'occultations |
| Ventilation | Naturelle, VMC simple flux (autoréglable/hygro), double flux avec rendement, perméabilité connue ou non |
| Systèmes | Génération (chaudière gaz/fioul, condensation, PAC, effet Joule, réseau de chaleur), émetteurs, régulation, ECS |
| Usage | Profil (logement, bureau, école…), consignes, calendrier (déjà présent), occupants |

Fonctionnement :
- Chaque réponse crée une surcharge `déclaré` ; un document importé (XML DPE ADEME, audit) crée des
  valeurs `document`. Le modèle de données de la base DPE de l'ADEME et ses ressources sont publiés
  en open source [R2] ; un moteur 3CL-2021 libre (PHP) calcule déjà un DPE depuis un XML ADEME [R3].
- Les questions sont ordonnées par **sensibilité × incertitude** (§ 6.4) : on montre d'abord les
  trois à cinq questions qui réduiraient le plus la fourchette du résultat.
- Les incohérences sont signalées (par exemple : « triple vitrage » avec « 1950 sans rénovation »
  → demander l'année des menuiseries).

### B2 — Choix et comparaison de la météo

| Option | Usage | Source |
| --- | --- | --- |
| Année type (TMY) du site | Défaut : comparaison entre bâtiments, scénarios | PVGIS (existant, Lot S) |
| Année réelle datée, réanalyse | Comparaison à des factures d'une année donnée | Open-Meteo Archive (existant, Lot L) |
| **Station Météo-France historique** | Calage fin, sites où la réanalyse est mal adaptée (relief, littoral) | Données climatologiques horaires de toutes les stations de métropole et d'outre-mer, depuis leur ouverture, en CSV par département [R4] |
| Fichiers des zones climatiques RE2020 | Comparaison « réglementaire » | DHUP / Météo-France |
| **Climat futur TRACC +2 / +2,7 / +4 °C** | Adaptation, confort d'été futur | Fichiers climatiques prospectifs ADEME / Météo-France / CSTB / DHUP, séries horaires de 17 paramètres pour les huit zones climatiques de référence de métropole [R5] |
| Import EPW / CSV | Données propres (station sur site) | Utilisateur |

Fonctions à fournir :
- Choix de la station la plus proche (distance, altitude, période couverte), avec avertissement si
  l'écart d'altitude dépasse un seuil.
- Choix d'une **période** quelconque (une saison, une vague de chaleur, l'hiver d'une facture), pas
  seulement une année civile.
- Comparaison de deux jeux : degrés-jours, ensoleillement, température maximale, et l'effet sur le
  résultat.
- Contrôle qualité : trous comblés et signalés (le correctif AB1 sur les heures manquantes
  s'applique).
- Pour le calage, **toujours une météo réelle de la période facturée**, jamais une année type.
- Normalisation : `WeatherDataset` stocké une fois, partagé entre bâtiments du même site.

### B3 — Modification de la géométrie

Éditeur 3D dans la page Bâtiment (three.js déjà présent) :

| Action | Détail | Effet sur le calcul |
| --- | --- | --- |
| Emprise | Déplacer, ajouter, supprimer des sommets sur l'orthophoto (existante : `orthophoto/`) | Régénère l'enveloppe |
| Hauteurs | Hauteur à l'égout et au faîtage, nombre de niveaux, hauteur sous plafond | Volume, surfaces |
| Toiture | Type (terrasse, 2 pans, 4 pans, mansarde), pente, orientation du faîtage, débords | Surfaces, ombrage propre |
| Mitoyenneté | Marquer une façade ou une portion comme mitoyenne, chauffée ou non (E9) | Paroi adiabatique ou vers un local à température réduite |
| Baies | Ajouter, déplacer, redimensionner par façade ; taux de vitrage par façade en un geste | Gains solaires, déperditions |
| Locaux non chauffés | Garage, combles perdus, cage d'escalier (préparation du multizone) | Coefficient de réduction b, puis zone propre |
| Validation | Maillage fermé, normales sortantes, pas de triangles dégénérés, surfaces recalculées côté serveur (déjà le cas dans `api/geometry.py`) | Refus avant calcul |

Règle : la géométrie automatique reste en mémoire comme base ; l'édition est une surcharge et le
diff s'affiche (par exemple : « hauteur 9,2 m → 11,5 m, corrigée par X le … »).

### B4 — Protections solaires

Trois familles, avec un catalogue et un mode « dessin » ou « paramètres » :

| Famille | Exemples | Paramètres | Méthode de calcul |
| --- | --- | --- | --- |
| Fixes liées à la baie | Casquette, joues, tableau ou embrasure, loggia, balcon de l'étage supérieur, débord de toiture | Profondeur, distance au vitrage, débordement latéral, côtés | **Paramétrique** : facteur de réduction du direct par heure, selon l'annexe F de l'EN ISO 52016-1:2017, qui couvre obstacles extérieurs et casquettes pour un élément d'orientation et d'inclinaison quelconques [R6] ; ou **géométrique** : génération de triangles d'obstacle ajoutés au lancer de rayons existant |
| Mobiles | Volet roulant, battant, persienne, store extérieur, store intérieur, brise-soleil orientable | ΔR, Fs direct/diffus, position (0–100 %), pilotage | Lot J généralisé : **position intermédiaire**, **un planning par façade ou par baie**, **pilotage automatique** (seuil d'éclairement sur la façade, température intérieure, absence d'occupants) |
| Vitrage et film | Vitrage de contrôle solaire, film | g, τ visible | Modifie la couche optique de la `ParoiModel` |
| Végétation | Arbre caduc, pergola végétalisée | Feuillage saisonnier | Lot Z3 (écarté jusqu'ici, Ⓟ) : transmission différente été/hiver |

Pourquoi les deux méthodes pour les fixes : la paramétrique est instantanée et suffit au N1 et au
parc ; la géométrique réutilise l'ombrage N2 sans nouveau code physique. Les deux doivent donner
le même ordre de grandeur sur un cas test (critère de recette).

Restitution associée : gain sur les heures > 26/28 °C et perte d'apports solaires en hiver, par
façade, pour chaque protection ajoutée.

### B5 — Systèmes et consommations (comble E4)

- Rendements de génération, de distribution, d'émission et de régulation, avec les valeurs par
  défaut de la méthode 3CL comme point de départ.
- Pompe à chaleur : COP dépendant de la température extérieure horaire (la série météo existe).
- ECS : besoin selon l'occupation, système, stockage.
- Sorties : énergie finale par énergie, énergie primaire, CO₂, coût.
- Nécessaire pour comparer aux factures (B6) et à l'étiquette DPE (parcours A).

### B6 — Mesures et calage

| Donnée | Saisie | Usage |
| --- | --- | --- |
| Factures (mensuelles/annuelles) | Formulaire ou import CSV | Calage mensuel |
| Courbes de charge Linky / Gazpar | Import de fichier, puis éventuellement API avec consentement (Ⓟ, RGPD) | Calage horaire ou journalier |
| Sondes T°/HR/CO₂ | Import CSV | Calage du comportement libre, confort |
| Test d'étanchéité, thermographie | Valeur ou image annotée | Surcharge `mesuré` sur l'infiltration et les parois |

Critères de calage retenus [R7] :

| Pas de temps | CV(RMSE) | NMBE |
| --- | --- | --- |
| Mensuel | ≤ 15 % | ± 5 % |
| Horaire | ≤ 30 % | ± 10 % |

Méthode : sélection des paramètres influents (§ 6.4), puis optimisation bornée sur ces seuls
paramètres. Les paramètres ajustés sont affichés comme `inféré par calage`, jamais présentés comme
mesurés. Le calage se fait sur la météo réelle de la période facturée.

### B7 — Scénarios

- Bouquets de travaux (ITE, ITI, combles, menuiseries, VMC, PAC, protections solaires) appliqués
  comme surcharges d'une variante.
- Comparaison base / scénarios sous météo actuelle et TRACC.
- Points de vigilance non thermiques signalés sans calcul (ITI et risque hygrothermique, par
  exemple).

### B8 — Sources documentaires et IA, avec le compte de l'utilisateur

**But.** Tirer des documents du bâtiment (DPE PDF ou XML, audit, plans, CCTP, DOE, factures,
carnet d'information du logement, photos de plaques signalétiques) des **faits proposés**, que
l'utilisateur valide avant qu'ils entrent dans la fiche. L'IA ne calcule jamais la physique : elle
lit, extrait, vérifie la cohérence et explique des résultats déjà calculés.

**Principe non négociable : l'IA tourne sur le compte de l'utilisateur.** Chaque utilisateur pose
lui-même sa clé dans l'interface, ou relie lui-même son Claude Code / Vibe (voie « abonnement »,
ci-dessous). L'app ne fournit aucune clé commune ; une clé ne sert qu'aux demandes de son
propriétaire.

#### Réutiliser ce qu'App Builder a déjà mis en service [C, PR du 2026-11-11 au 2026-11-14]

App Builder v2 a résolu exactement ce problème, testé et en service. bilan-thermique **reprend le
même schéma** au lieu d'en inventer un second :

| Élément d'App Builder | Ce qu'il fait | Repris dans bilan-thermique |
| --- | --- | --- |
| `budget.FOURNISSEURS` | Registre fermé : `claude` (protocole `anthropic`), `mistral` et `albert` (protocole `openai`, adresse fixe) | Même registre, mêmes identifiants |
| `api/ia/fournisseurs.py` | Un adaptateur Anthropic + un adaptateur compatible OpenAI ; erreurs traduites en **message fixe** (jamais le texte de l'exception, qui peut citer la requête) | Copié, avec mention « copie d'app-builder `0ff4e4e` » (pratique du lab, comme `redirect-uri.ts` copié de lab-admin) |
| `api/ia/catalogue.py` | Modèles d'Albert lus par `GET /v1/models` avec la clé de l'utilisateur, cache 10 min par empreinte de clé, table prudente en repli | Copié |
| `fields.EncryptedTextField` + `AiKeyVault` | Coffre chiffré (Fernet, `VAULT_ENCRYPTION_KEY`, jamais dérivée de `SECRET_KEY`) ; valeur indéchiffrable = absente | Même champ, même variable ; table propre à l'app |
| `GET/PUT /api/ai-keys/`, `/api/ai-settings/` | Rend seulement « configurée : oui/non » ; chaîne vide = suppression ; préférences fournisseur/modèle sur le serveur, jamais dans `localStorage` | Mêmes routes, mêmes réponses |
| Page « Intelligence artificielle » (`/ia`) | Un seul endroit : clés, état, modèle par défaut, Claude Code et Vibe | Même page dans bilan-thermique |
| Serveur MCP + jetons personnels `abt_…` | Voie « abonnement » : Claude Code ou Vibe **de l'utilisateur** relié au serveur MCP de l'app ; jeton haché, 90 jours au plus, révocable, portée lire/écrire, éventuellement limité à un objet | Même mécanisme, préfixe propre (ex. `btt_…`), limitable à un bâtiment ou à un parc |

Règles déjà tranchées par le lab, à ne pas rediscuter :
- **Pas d'OAuth d'abonnement Claude** : le jeton `sk-ant-oat…` est refusé au coffre, car les
  conditions d'Anthropic réservent cet OAuth à Claude Code et à leurs propres applications
  (décision écrite dans `CLAUDE.md`, section App Builder v2). Quelqu'un qui veut utiliser son
  abonnement relie son Claude Code par le MCP.
- **Clé d'un utilisateur** : chiffrée côté serveur, jamais dans `localStorage` ; une réponse d'API
  dit seulement si elle est réglée (règle désormais écrite dans le gabarit,
  `_templates/regles/CLAUDE.md`).
- **Albert** : clé personnelle, réservée aux agents publics (accès par ProConnect) ; identifiants de
  modèles lus en direct, jamais écrits en dur.

Écarts volontaires avec App Builder :
- **Identité par le `sub` Keycloak, pas par l'email.** App Builder range son coffre par
  `owner_email`, ce que `CLAUDE.md` signale désormais comme un piège non résolu (changer l'email
  d'un compte rend ses données orphelines). Les nouvelles tables de bilan-thermique portent le
  `sub` (l'email reste un affichage).
- **Pas de point d'accès « compatible OpenAI » à adresse libre** en v1 : une base URL saisie par
  l'utilisateur ferait appeler par le serveur n'importe quelle adresse, y compris le réseau interne
  du lab (risque SSRF). Un nouveau fournisseur s'ajoute au registre, par le code.

#### Gestion des clés

- Saisie sur la page « Intelligence artificielle » ; bouton « Tester » (lecture des modèles).
- Jamais renvoyée, jamais journalisée (empreinte seulement), jamais utilisée pour un autre
  compte, ni par un traitement de parc lancé par quelqu'un d'autre, ni par une session de nuit.
- Suppression immédiate par l'utilisateur.
- Centralisation future dans `oauth-hub` ou dans une bibliothèque commune avec App Builder :
  changement du lab, à demander (`DEMANDES_LAB.md`), pas un prérequis.

#### Chaîne documentaire

1. **Dépôt** du document par l'API `storage` du lab (règle du gabarit : pas de volume média, pas de
   blob en base), rattaché au bâtiment.
2. **Extraction locale d'abord** : XML DPE ADEME analysé sans IA (structure connue [R2]) ; PDF
   texte extrait localement. L'OCR ou la vision par IA ne servent que si le texte manque.
3. **Avertissement avant envoi** : fournisseur, pages envoyées, volume estimé de jetons ; envoi
   seulement après confirmation, document par document.
4. **Extraction structurée par outils**, comme le chat d'App Builder : l'IA appelle un outil
   `proposer_faits` dont le schéma reprend les clés de `BuildingFact`, chaque valeur avec la page
   et l'extrait cité, `null` si le document ne le dit pas ; boucle bornée côté serveur (nombre
   d'appels et durée).
5. **Contrôle automatique** : unités, plages plausibles, cohérence avec les faits existants ; un
   extrait introuvable dans le texte source fait rejeter la valeur.
6. **Validation humaine** : les valeurs arrivent comme `FactProposal`, à côté de la valeur actuelle
   et de l'extrait. Accepter crée un `BuildingFact` de méthode `document`. Rien n'entre sans clic.
7. **Recalcul** selon le graphe de dépendances (principe 5).

#### Par le MCP (Claude Code ou Vibe de l'utilisateur)

Outils proposés : `lire_fiche`, `lire_resultats`, `lire_document`, `proposer_faits`,
`lancer_calcul` (N0/N1 seulement), `comparer_scenarios`. Même règle que dans l'interface : une
écriture n'est qu'une **proposition**, que l'utilisateur valide (dans l'interface, ou par l'outil
`appliquer_propositions` si son jeton a la portée « écrire »).

#### Autres usages de l'IA (même clé, mêmes règles)

- Contrôle de cohérence de la fiche (« double vitrage déclaré mais Uw 5,8 »).
- Explication d'un résultat, nourrie **uniquement** par le `CalcRun` et la fiche.
- Aide au questionnaire B1 : interpréter une réponse libre en valeur proposée.

#### Garde-fous

- Taille maximale par document et par demande ; nombre de pages plafonné.
- Une erreur du fournisseur n'abîme aucun fait ; message fixe à l'utilisateur.
- Données personnelles (factures, courbes) envoyées seulement par choix explicite.
- Tests sans réseau : fournisseurs simulés, comme `AlbertTests` et `CoffreClesTests` d'App Builder.

---

## 6. Moteurs de calcul

### 6.1 Trois niveaux

| Niveau | Méthode | Temps visé | Usage | Statut |
| --- | --- | --- | --- | --- |
| N0 | Bilan statique mensuel (déperditions × DJU – apports), proche 3CL / ISO 52016-1 mensuel | < 0,1 s | Tri d'un parc, vérification de cohérence | À écrire |
| N1 | Modèle horaire réduit RC (5R1C ISO 13790 / ISO 52016-1 horaire), par zone | ~1 s pour un an | Parc, formulaires, protections paramétriques, calage | À écrire ou intégrer |
| N2 | FEM 1D par triangle + nœud d'air, ombrage par lancer de rayons | 10–30 s | Bâtiment affiné, géométrie détaillée | Existant |

Briques libres pour N1, à évaluer avant d'écrire du code [R8] :
- **pyBuildingEnergy** (Eurac Research) : bibliothèque Python libre qui calcule besoins horaires ou
  mensuels, température intérieure et charges selon l'ISO 52016-1:2017 [R8a].
- **DIBS** (IWU) : simulateur horaire dérivé de l'ISO 13790 pour bâtiments isolés et parcs,
  publié en paquets pip [R8b].
- **BUEM** : modèle 5R1C de l'ISO 52016-1 en Python, sous licence MIT [R8c].
- **TEASER** (RWTH Aachen) : génération de bâtiments archétypes à partir de données minimales
  (usage, surface, année, volumétrie approximative) et enrichissement, export Modelica, MIT [R8d].
  Ses travaux de **calibration bayésienne d'archétypes sur mesures** sont directement utiles au
  B6 [R8e].

Décision proposée : écrire N1 dans `api/` en s'appuyant sur la formulation ISO 52016-1 (testable
contre pyBuildingEnergy), plutôt que d'importer une dépendance lourde. Ⓟ.

### 6.2 Cohérence entre niveaux

- Les trois niveaux lisent **la même fiche** ; un traducteur par niveau (fiche → entrées du moteur).
- Cas de recette communs : boîte simple (Lot O), maison réelle de la zone test, collectif de Tours
  (déjà utilisé au Lot AL). Écart N1/N2 suivi en test (seuil à fixer, par exemple 15 % sur le
  besoin annuel).
- Le tableau de parc affiche le niveau ayant produit chaque chiffre.

### 6.3 Extensions du N2 à remettre en discussion (Ⓟ)

1. **Paroi mitoyenne** (adiabatique ou vers local à température donnée) : faible coût, gain
   important (E9).
2. **Multizone** : `zone_id` par triangle, N nœuds d'air, refends comme nouveau type de triangle ;
   le chemin est déjà décrit dans la section « Hors scope » du journal.
3. **Séparation convectif / radiatif** à zone unique (température radiante moyenne) : prérequis
   d'un vrai confort d'été (température opérative).
4. Ponts thermiques forfaitaires par linéaire de jonction (ψ par défaut selon le type de jonction).
5. **Albédo, suite du Lot AI** :
   - ombres portées sur le sol : pondérer l'albédo vu par la part ensoleillée du sol à chaque
     heure (la grille de visibilité existe déjà pour les triangles) ;
   - réflexion par les façades voisines (un rebond, albédo de façade par matériau BD TOPO ou
     texture Panoramax), utile en rue étroite où les murs ne voient presque que des façades ;
   - albédo par défaut (0,20 dans `observed_env.DEFAULT_GROUND_ALBEDO`) appliqué à tout bâtiment
     sans environnement observé, au lieu d'aucun terme, pour que N1 et N2 restent comparables ;
   - B4 : traiter un sol clair ou une terrasse claire comme une source d'apports à surveiller en
     confort d'été (une protection fixe en casquette ne coupe pas ce rayonnement venu du bas).

### 6.4 Sensibilité et incertitude

- Analyse de Morris (criblage) sur N1 pour classer les paramètres par influence, par archétype ;
  résultat mis en cache et utilisé pour ordonner les questions (B1) et choisir les paramètres de
  calage (B6).
- Propagation Monte-Carlo sur N1 (quelques centaines de tirages, faisable sur N1, pas sur N2) pour
  afficher une fourchette (P10–P90) au lieu d'un chiffre unique.
- Bibliothèque candidate : SALib (Python, libre).

---

## 7. Sources de données (à jour au 2026-11-14)

| Source | Ce qu'on en tire | Accès | Statut dans l'app |
| --- | --- | --- | --- |
| RNB | Identifiant unique, emprise, lien adresses ; mise à jour par les millésimes BDNB et BD TOPO, les autorisations d'urbanisme et les signalements [R9] | API, export départemental | Utilisé (`closest`) |
| BDNB (CSTB) | Croisement d'une cinquantaine de bases publiques, mis à jour deux fois par an, plus de 32 millions de bâtiments [R10] | API PostgREST, GPKG, pgdump | Utilisé (vitrages DPE seulement) → **étendre à toute la fiche** |
| DPE ADEME | Fiche complète des logements et tertiaires diagnostiqués depuis 2021 | API data.ademe.fr | Indirect (via BDNB) |
| BD TOPO, BAN, cadastre | Emprises, hauteurs, adresses | IGN / Etalab | Utilisé |
| LiDAR HD | Nuages classifiés et MNT/MNS/MNH ; couverture totale visée fin 2026 [R11] | geoservices.ign.fr | Utilisé |
| Panoramax | Photos de façades | API | Utilisé |
| Open-Meteo Archive, PVGIS | Année réelle, TMY | API | Utilisé |
| Météo-France stations horaires | Historique par station | CSV par département sur meteo.data.gouv.fr [R4] | **À ajouter** |
| Fichiers TRACC bâtiment | Années types futures +2 / +2,7 / +4 °C [R5] | data.gouv.fr | **À ajouter** |
| Archétypes TABULA / EPISCOPE | Compositions par période | Tableaux ouverts | **À ajouter** |
| Factures, Linky, Gazpar | Consommations réelles | Utilisateur, avec consentement | **À ajouter** |

---

## 8. Modèle de données proposé (Django)

Nouveaux modèles, en gardant les existants (`Building`, `Environment`, `ParoiModel`, `Job`) :

```text
Portfolio            id, nom, propriétaire, description, created_at
PortfolioMember      portfolio → user, rôle (gestionnaire_parc | gestionnaire_bat | lecteur)
Building (+champs)   portfolio (null = bâtiment isolé), rnb_id, adresse, statut_pipeline,
                     completeness (0–1), confidence (0–1)
                     → `name` n'est plus unique globalement, mais par portfolio
BuildingFact         building, clé (ex. "mur.isolation.epaisseur_m"), valeur JSON, unité,
                     méthode (mesure|document|declare|infere|defaut), source, confiance,
                     auteur, created_at, supersedes → BuildingFact (historique)
Scenario             building, nom, base (bool), parent, surcharges JSON (diff de faits)
WeatherDataset       type (tmy|reel|station|tracc|import), lat/lon ou station, période,
                     hash, série horaire (table ; un fichier importé est déposé dans storage),
                     qualité
ShadingDevice        building, cible (groupe de triangles / baie), famille, paramètres JSON,
                     mode (parametrique|geometrique), pilotage JSON
Zone (plus tard)     building, nom, type (chauffée|non chauffée|mitoyen), consigne
Measurement          building, type (facture|courbe|sonde|test), période, valeurs, source
                     (fichier d'origine dans storage)
CalcRun              building, scenario, niveau (N0|N1|N2), weather_dataset, empreinte des
                     entrées (hash), statut, résultats JSON, indicateurs indexés, durée,
                     created_at        → résout U3 et sert le tableau de parc
SourceCache          clé (service + identifiant + millésime), réponse JSON, fetched_at, ttl
AiKeyVault           owner_sub (unique), claude_key, mistral_key, albert_key
                     (EncryptedTextField, VAULT_ENCRYPTION_KEY), preferred_provider,
                     preferred_models — même forme qu'App Builder, clé d'identité = sub
McpToken             owner_sub, nom, prefix, token_hash (sha256), portée (lire|ecrire),
                     building / portfolio facultatif, expires_at (≤ 90 j), last_used_at,
                     revoked_at
BuildingDocument     building, owner_sub, référence storage (relative_path), type, pages,
                     texte extrait, created_at
AIExtractionRun      document, credential, fournisseur, modèle, jetons estimés/réels, statut,
                     erreurs, created_at   (jamais le contenu de la clé)
FactProposal         run, building, clé, valeur, unité, page, extrait cité, contrôle
                     (ok|hors_plage|incoherent|extrait_introuvable), décision
                     (en_attente|acceptee|rejetee), décidé par, décidé le
```

Règles :
- La **fiche effective** d'un scénario = faits les plus prioritaires + surcharges du scénario.
- `CalcRun.inputs_hash` permet de dire « résultat à jour » ou « périmé » et d'éviter un recalcul
  identique.
- Le verrou atomique du Lot 2 s'applique à `CalcRun` (une contrainte unique partielle sur
  `building, scenario, niveau, statut in (PENDING, RUNNING)`).

---

## 9. Restitution

| Vue | Contenu |
| --- | --- |
| Parc | Tableau + carte, filtres, priorisation, export ; colonne confiance et complétude |
| Fiche bâtiment | Cartes B1–B7 avec état et influence, résultat courant avec fourchette, sources cliquables |
| Comparateur | Base / scénarios / météos côte à côte : besoin, consommation, heures d'inconfort, puissance |
| Bilan détaillé | Existant (dashboard AB2), complété par façade : déperditions, gains solaires, effet des protections |
| Confort d'été | Heures > 26/28 °C, degrés-heures, sous météo actuelle et TRACC |
| Traçabilité | Pour chaque chiffre : niveau de calcul, météo, liste des faits utilisés et leur provenance |

---

## 10. Contraintes d'exploitation du lab

- 2 vCPU, worker Celery en concurrence 1 : une **file `parc`** séparée de la file interactive,
  priorité à l'interactif ; N2 jamais lancé en lot.
- Estimation de coût avant lancement (à corriger d'abord : la passe végétation, Lot 2).
- Quotas externes respectés par limiteur (BDNB 120 requêtes/min) et cache `SourceCache`.
- Données personnelles (factures, courbes de charge, occupation) : séparées des données bâtiment,
  supprimables, jamais exposées au niveau parc sans droit explicite.
- Tests : découper `tests.py` par domaine (Lot 5) avant d'ajouter N0/N1, sinon le fichier devient
  ingérable.
- Fichiers des utilisateurs (documents, imports météo, mesures) : **API `storage` seulement**, pas
  de volume média, pas de blob en base (règle du gabarit). Le volume `facade-cache` existant est un
  cache de calcul, pas un fichier d'utilisateur.
- Règles du gabarit à tenir dans tout nouveau code (`_templates/regles/CLAUDE.md`, mises à jour le
  2026-11-14) : une exception `requests` ne se renvoie ni ne se journalise (`str(exc)` porte l'URL),
  message fixe — à vérifier aussi dans `bdnb.py`, `weather_source.py`, `geodata.py`,
  `lidar_source.py` ; un sérialiseur appelé sans `request` masque les champs réservés au
  propriétaire ; aucune route ne rend la liste des comptes ; ports publiés liés à `127.0.0.1` ;
  le frontend ne reçoit jamais `env_file: .env`.
- Appels d'administration à Keycloak (rôles, groupes) par l'adresse interne, jamais par l'URL
  publique.
- Notifications (parc terminé, calage fini) : `send_mail` de Django, compte réglé dans lab-admin.
- Déploiement : **par le moteur uniquement** (outil MCP `deploy` ou bouton « Déployer » de
  lab-admin), d'un commit poussé ; les tests de la feuille de route tournent avant la mise en
  service.
- Installation des dépendances : selon `chantiers/modules/REGLES_MODULE.md` (dans l'image Docker
  d'abord ; sinon `install.sh` et `install.bat` à la racine, rejouables, `--check`, sans privilège).
- Sessions de nuit : elles peuvent ouvrir des PR sur l'app (branches `auto/…`). Toute branche de
  travail se met à jour sur elles avant fusion.

---

## 11. Feuille de route proposée

Les lots existants 2 à 5 de `FEUILLE_DE_ROUTE.md` deviennent des prérequis. Numérotation proposée à
la suite (format machine de la feuille de route à respecter : titres, « Tests à passer », « Vérifié
le »).

| Lot | Contenu | Prérequis | Recette |
| --- | --- | --- | --- |
| 2 (existant) | Garde-fous de calcul + verrou atomique | — | Calcul refusé au-delà du seuil ; `[202, 409]` |
| 3 (existant) | Historique des calculs → **`CalcRun`** | 2 | Résultat revu sans recalcul |
| 5 (existant) | Découpage des tests | — | Même nombre de tests |
| 8 | Provenance : `BuildingFact`, fiche effective, surcharges | 3 | Une surcharge retirée rend la valeur automatique |
| 9 | Fiche BDNB complète + archétypes → fiche initiale automatique | 8 | 10 adresses réelles initialisées sans saisie |
| 10 | Moteur N1 (ISO 52016-1 horaire) + N0, comparaison à N2 | 8 | Écart N1/N2 sous le seuil sur les 3 cas de recette |
| 11 | Parc : `Portfolio`, import CSV, file `parc`, tableau, export | 9, 10 | Parc de 50 bâtiments traité de nuit sans intervention |
| 12 | Formulaires guidés B1 + mitoyenneté (E9) | 8 | Une réponse fait bouger le résultat et la confiance |
| 13 | Météo B2 : stations Météo-France, TRACC, périodes, comparaison | 3 | Même bâtiment, 3 météos, 3 résultats comparés |
| 14 | Protections solaires B4 : fixes paramétriques et géométriques, mobiles généralisées | 10 | Casquette sud : réduction des heures > 28 °C cohérente entre les 2 méthodes |
| 15 | Géométrie B3 : éditeur emprise/hauteur/toiture/baies | 8 | Édition → enveloppe valide → calcul |
| 16 | Systèmes B5 : consommations finales et primaires | 10 | Écart à l'étiquette DPE affiché |
| 17 | Mesures et calage B6, sensibilité (Morris), fourchettes | 10, 16 | Calage mensuel conforme aux critères § B6 sur un cas réel |
| 18 | Scénarios B7 + comparateur | 3, 8 | Base et 2 scénarios comparés |
| 19 | Carte (U4) et vue parc cartographique | 11 | Sélection de bâtiments à la carte |
| 20 | Multizone / radiatif (Ⓟ) | 10 | À définir |
| 21 | IA documentaire B8 : page « Intelligence artificielle », coffre et fournisseurs copiés d'App Builder (Claude, Mistral, Albert), XML DPE sans IA, extraction par outil `proposer_faits`, propositions validées | 8 | Un DPE PDF produit des propositions avec extraits cités ; aucune n'entre sans validation ; la clé n'apparaît dans aucune réponse ni aucun journal ; jeton `sk-ant-oat…` refusé |
| 22 | Serveur MCP de l'app + jetons personnels (voie abonnement : Claude Code / Vibe de l'utilisateur) | 21 | Jeton expiré, révoqué, d'autrui ou hors portée refusé ; jeton Keycloak refusé sur `/api/mcp/` ; un vrai Claude Code lit une fiche et propose un fait |

Ordre conseillé : 2 → 3 → 5 → 8 → 9 → 10 → 11 (premier résultat de parc), puis 12–14 (affinage le
plus demandé), puis le reste.

---

## 12. Questions ouvertes (Ⓟ)

- [ ] Qui sont les premiers gestionnaires de parc : comptes du lab uniquement, ou ouverture à des
      utilisateurs externes (et donc à d'autres groupes Keycloak) ?
- [ ] Faut-il un profil « occupant » sans droit de calcul ?
- [ ] N1 écrit maison (contrôlé, testable) ou intégration de pyBuildingEnergy / TEASER ?
- [ ] Ambition réglementaire : rester en aide à la décision, ou viser un calcul « type 3CL »
      comparable au DPE ?
- [ ] Données Linky / Gazpar : import de fichier seulement, ou API avec consentement (via
      `oauth-hub` si le fournisseur offre un OAuth : changement du lab) ?
- [ ] Reprendre le Lot Z3 (végétation saisonnière) dans le cadre des protections solaires ?
- [ ] Le multizone est-il un objectif de cette refonte ou une étape ultérieure ?
- [ ] Plus tard : coffre IA et adaptateurs dans une bibliothèque commune avec App Builder, ou dans
      `oauth-hub` (changement du lab, à demander) ?

---

## 13. Sources

- [R1] HQE-GBC, projet CIBLE, support de lancement (enrichisseurs de données, moteur et niveaux de
  saisie) — https://www.hqegbc.org/wp-content/uploads/2022/11/Support-presentation-CIBLE_lancement_04012022.pdf
- [R2] ADEME, note technique open data DPE (modèle de données open source) —
  https://data.ademe.fr/data-fair/api/v1/datasets/dpe-v2-tertiaire-2/metadata-attachments/ADEME%20-%20DPE%20-%20Note%20technique%20Opendata%20-%20V2.pdf
  ; dépôt : https://gitlab.com/observatoire-dpe/observatoire-dpe
- [R3] Moteur PHP de calcul DPE 3CL-2021 depuis un XML ADEME — https://packagist.org/packages/pandopia/calcul-dpe-php
- [R4] Météo-France, données climatologiques de base horaires — https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-horaires
- [R5] ADEME / Météo-France, données climatiques prospectives TRACC pour le bâtiment —
  https://www.data.gouv.fr/datasets/donnees-climatiques-prospectives-france-2-7degc-vague-de-chaleur
- [R6] EPB Center, étude de cas EN ISO 52016-1 annexe F (facteurs de réduction d'ombrage) —
  https://epb.center/document/case-iso-52016-1_solshad/
- [R7] Critères ASHRAE Guideline 14 (CV(RMSE), NMBE) — https://discovery-pp.ucl.ac.uk/10117731/3/Burman_2020_BPA_SimBuild_JainN.pdf
- [R8a] pyBuildingEnergy (Eurac Research) — https://build-up.ec.europa.eu/en/resources-and-tools/tools/pybuildingenergy
- [R8b] DIBS (IWU) — https://www.github.com/IWUGERMANY/DibsComputingCore
- [R8c] BUEM — https://pypi.org/project/buem
- [R8d] TEASER (RWTH-EBC) — https://github.com/RWTH-EBC/TEASER
- [R8e] TEASER, publications (calibration bayésienne d'archétypes) — https://pypi.org/project/teaser/
- [R9] Référentiel national des bâtiments — https://www.data.gouv.fr/datasets/referentiel-national-des-batiments/community-resources
- [R10] BDNB — https://www.data.gouv.fr/datasets/base-de-donnees-nationale-des-batiments/informations
- [R11] Programme LiDAR HD — https://www.actuia.com/actualite/les-premiers-modeles-numeriques-issus-du-programme-lidar-hd-sont-disponibles/
- [R12] Albert API, prise en main (clé, base URL, compatibilité OpenAI) — https://guides.ia.numerique.gouv.fr/albert-api/prise-en-main/quickstart.md
- [R13] Demande d'accès à Albert API (DINUM) — https://www.demarches-simplifiees.fr/commencer/contacter-etalab/dossier_vide
- Code relevé : https://github.com/Sacha37420/bilan-thermique (`9f8cc8d`),
  https://github.com/Sacha37420/app-builder (`0ff4e4e` : `api/ia/`, `fields.py`, `budget.py`,
  `FEUILLE_DE_ROUTE.md` lots 8 et 11 à 14), https://github.com/Sacha37420/sso-lab (`b28471c` :
  `CLAUDE.md`, `_templates/regles/CLAUDE.md`, `workspace/regles-lab.md`) (`FEUILLE_DE_ROUTE.md`,
  `to_do_bilan_thermique.md`, `backend/api/*.py`)

---

## 14. Arbitrages de fusion (2026-11-14)

| # | Point | Arbitrage | Raison |
| --- | --- | --- | --- |
| F1 | `refonte.md` « présent sur le serveur » | **Introuvable** : ni dans le dossier de l'app, ni dans l'historique git (toutes branches), ni ailleurs dans `~` (recherche par nom). La fusion part donc de `refonte-bilan-thermique.md` seul, recopié ici tel quel sauf les ajouts ci-dessous. | Rien à garder d'un fichier absent ; si l'ancien fichier réapparaît, comparer ses sections à celles-ci et ne reprendre que ce qui n'est pas contredit par le code. |
| F2 | § 1.1 « 244 tests » | Gardé : 244 constaté (`grep -c "def test"`, et suite jouée le 2026-11-14 par `chantiers/tests/essai.sh`). | Conforme au code. |
| F3 | Note « Hors scope cite encore l'albédo » (§ 1.2) | Appliquée : la ligne de `to_do_bilan_thermique.md` est corrigée (mention du Lot AI). | Demandé par le texte lui-même. |
| F4 | `bdnb.py`, `weather_source.py`, `geodata.py`, `lidar_source.py` et `str(exc)` (§ 10) | Vérifié dans le code : `bdnb._get` recopie `str(exc)` d'une exception `requests` dans son message (qui remonte à l'utilisateur) ; `weather_source.py` et `geodata.py` envoient un **email personnel** dans leur `User-Agent`. Les deux sont corrigés au Lot 2 (message fixe, `User-Agent` de l'app sans adresse). | Règle du gabarit ; une adresse personnelle n'a rien à faire dans une requête vers un service externe. |
| F5 | « Service nommé `<app>-worker` » (consigne de mission) | Le conteneur s'appelle déjà `bilan-thermique-worker`, le **service** compose `worker` : renommage au Lot 5, comme prévu par la feuille de route. | Règle du lab. |
| F6 | § 11, recette Lot 11 « parc de 50 bâtiments de nuit » et recette finale « 20 adresses » | Recette du Lot 11 ramenée à **20 adresses** (celle de la mission) ; 50 reste un objectif de charge mesuré si le temps le permet. | La mission fixe 20 ; ne pas promettre une recette non jouée. |
| F7 | Ordre des lots | Celui de la mission : prérequis 2, 3, 5, puis 8 → 22 dans l'ordre numérique (le § 11 conseillait 12–14 avant le reste : même chose, l'ordre numérique le respecte). | Consigne explicite. |

## 15. Décisions prises en session

Format : date — question — choix — raison — pour revenir en arrière.

| # | Date | Question (Ⓟ) | Choix | Raison | Retour arrière |
| --- | --- | --- | --- | --- | --- |
| D1 | 2026-11-14 | Premiers gestionnaires de parc : comptes du lab ou externes ? | **Comptes du lab seulement** (porte inchangée : `developers,famille,amis`). Les rôles de l'app vivent en base, par parc. | Aucun changement Keycloak (changement du lab) ; réversible. | Ajouter un groupe = demande au lab (`DEMANDES_LAB.md`), aucun code de l'app à changer. |
| D2 | 2026-11-14 | Profil « occupant » ? | **Oui, comme rôle par bâtiment** (`BuildingAccess.role = occupant`) : il déclare des faits, répond au questionnaire, lance N0/N1, voit le résultat ; il ne partage pas, ne supprime pas, ne lance pas N2 ni le parc. | L'objectif cite l'occupant comme acteur de l'affinage. Un rôle de plus en base, aucune porte nouvelle. | Ne plus attribuer ce rôle ; les faits qu'il a posés restent (provenance `auteur`). |
| D3 | 2026-11-14 | Partage : modèle de `storage` ou modèle propre ? | **Modèle propre à l'app** : `PortfolioMember` (sub, rôle) et `BuildingAccess` (sub, rôle). | Le modèle de `storage` décrit des fichiers, pas des droits métier ; l'appeler pour chaque lecture coûterait un aller-retour réseau. | Tables indépendantes, supprimables. |
| D4 | 2026-11-14 | Visibilité d'un bâtiment existant (sans parc) | **Inchangée** : un bâtiment sans parc reste visible et modifiable par tout membre de l'app (comportement d'avant la refonte). Un bâtiment de parc n'est visible que des membres du parc et des personnes à qui il est confié. Les **données personnelles** (mesures, documents, propositions IA) ne sont visibles que de leur auteur et, pour un bâtiment de parc, des gestionnaires. | « Un bâtiment existant devient un bâtiment sans parc » sans perte d'usage ; on ne referme pas après coup un accès dont dépendent les pages actuelles. | Rattacher un bâtiment à un parc le fait sortir de l'espace commun. |
| D5 | 2026-11-14 | N1 maison ou pyBuildingEnergy / TEASER ? | **Maison, dans `api/`**, en numpy : modèle 5R1C (ISO 13790 / ISO 52016-1 horaire simplifié), aucune nouvelle dépendance. N0 mensuel ISO 13790 également maison. | Testable, léger dans l'image, contrôlé ; pyBuildingEnergy reste une référence de comparaison hors image. | Remplacer `api/engines/n1.py` par un adaptateur derrière la même fonction `run_n1(fiche, météo)`. |
| D6 | 2026-11-14 | Ambition réglementaire | **Aide à la décision**. N0 est « de type 3CL » (mêmes grandeurs, mêmes défauts de rendement), jamais présenté comme un DPE. | Un calcul réglementaire demande une certification hors de portée. | Aucun code à défaire. |
| D7 | 2026-11-14 | Linky / Gazpar : fichier ou API ? | **Import de fichier seulement** (CSV). | L'API demanderait `oauth-hub` (changement du lab) et un consentement RGPD formalisé. | Ajouter une source au modèle `Measurement`, inchangé. |
| D8 | 2026-11-14 | Reprendre le Lot Z3 (végétation saisonnière) ? | **Oui, en protection solaire paramétrique seulement** (« arbre caduc », transmission été/hiver), lue par N0/N1 ; le N2 n'est pas modifié. | Sert le confort d'été sans toucher au lancer de rayons. | Retirer l'entrée du catalogue. |
| D9 | 2026-11-14 | Multizone dans cette refonte ? | **Non** : étape ultérieure. Le Lot 20 se limite à la **température opérative** (N1 la calcule nativement : nœud de surface du 5R1C) et à la paroi mitoyenne (traitée au Lot 12 pour N0/N1 et N2). | Multizone = refonte structurelle du N2 ; l'objectif (parc + affinage) n'en dépend pas. | Le chemin reste décrit dans la section « Hors scope » du journal. |
| D10 | 2026-11-14 | Coffre IA commun avec App Builder / `oauth-hub` ? | **Copie dans l'app** (pratique du lab), demande de bibliothèque commune écrite dans `DEMANDES_LAB.md`, non bloquante. | Hors périmètre d'écriture de la mission. | Remplacer la copie par l'import commun quand le lab l'aura fait. |
| D11 | 2026-11-14 | Préfixe des jetons MCP | `btt_` | Distinct d'`abt_` (App Builder) : un jeton ne peut pas être présenté à la mauvaise app sans être reconnu. | Changer le préfixe invalide les jetons émis (ils sont hachés, rien d'autre à migrer). |
| D12 | 2026-11-14 | Seuil d'écart N1/N2 | **20 % sur le besoin annuel de chauffage** pour les cas de recette synthétiques (boîte, maison type, petit collectif) ; écart mesuré écrit à côté. | 15 % (proposé) est plus serré que l'écart usuel entre un modèle 5R1C et un modèle détaillé ; on publie l'écart réel plutôt que de régler N1 sur N2. | Changer la constante `N1_N2_TOLERANCE` du test. |
| D13 | 2026-11-14 | File `parc` sur un worker en concurrence 1 | **Une seule instance de worker** qui écoute `celery,parc` dans cet ordre, `prefetch_multiplier=1`, `acks_late` : un job interactif en attente passe avant le bâtiment de parc suivant (la tâche de parc traite **un** bâtiment puis se replanifie). Pas de second conteneur. | 2 vCPU : un second worker doublerait la charge. | Ajouter un service `bilan-thermique-worker-parc` si la machine grandit. |
| D14 | 2026-11-14 | Archétypes de composition | **Table embarquée dans le code** (`api/archetypes.py`) : périodes de construction françaises (TABULA-FR simplifiée) × matériau de mur, U et inertie par défaut ; valeurs marquées `defaut`/`infere`, confiance faible. | Pas de téléchargement au build ni à l'exécution ; la table se relit et se teste. | Remplacer la table par un import de données plus riche. |
| D15 | 2026-11-14 | Bibliothèque cartographique (U4, Lot 19) | **Leaflet 1.9.4** (BSD-2, ~150 Ko) avec fond **IGN Plan v2** (Géoplateforme, sans clé). | Le plus léger, sans clé, sans compte. | Retirer la dépendance et la page carte. |
| D16 | 2026-11-14 | Lecture des PDF (Lot 21) | **`pypdf`** (BSD-3, pur Python) pour le texte ; aucune OCR locale (pas de Tesseract dans l'image). Une page sans texte n'est envoyée à l'IA (vision) qu'après confirmation. | Pur Python, image légère. | Retirer la dépendance : seul le XML DPE reste lisible sans IA. |
| D18 | 2026-11-14 | Lot 5 « en cours » alors que l'ordre impose 2 → 3 → 5 | Lot 5 repassé « à faire » (tâches cochées conservées) | Règle « un seul lot en cours ». | Aucun. |
| D19 | 2026-11-14 | L6 : purger ou marquer les environnements régénérés ? | **Marquer** (`replaced_by`), jamais supprimer automatiquement ; L7 : commande manuelle `nettoyer_brouillons` | Un environnement peut porter des retraits d'objets faits à la main ; une suppression automatique serait irréversible. | Effacer le champ `replaced_by`. |
| D20 | 2026-11-14 | Une valeur DPE importée (méthode « document ») battait une correction « déclarée » | **Toute surcharge d'une personne prime sur toute valeur automatique** ; le rang départage à l'intérieur de chaque famille | Principe 3 : la réimportation ne doit pas reprendre la main sur une correction. | Retirer `is_override` de `facts._better`. |
| D17 | 2026-11-14 | Fichiers des utilisateurs sans `storage` joignable dans les tests | Client `storage` simulé dans les tests ; en service, appels par compte de service `bilan-thermique-admin` → **demande au lab** (client compagnon + `KEYCLOAK_SERVICE_WRITE_SHARES`). En attendant, le dépôt de document passe par le **jeton de l'utilisateur** transmis à `storage` (variante `carto-lab`), ce qui demande seulement que `bilan-thermique` soit dans `KEYCLOAK_TRUSTED_CLIENTS` de `storage` — demande au lab également. | Aucune route de `storage` ne change. | — |

## 16. Avancement de la refonte

Tenu à jour à chaque lot livré (détail dans `FEUILLE_DE_ROUTE.md`).

| Lot | Statut | Commit | Résumé |
| --- | --- | --- | --- |
| 2 | livré 2026-11-14 | `Lot 2` | Passe végétation comptée et contrôlée dans la vue ; verrou `pg_advisory_xact_lock` + envoi `on_commit` ; jobs bloqués expirés à 6 h ; `Job.owner_sub` ; `api/net.py` (messages fixes, `User-Agent` sans email). 244 → 257 tests. |
| 3 | livré 2026-11-14 | `Lot 3` | `CalcRun` (historique rechargeable, indicateurs communs aux 3 niveaux), environnements remplacés sans suppression, `nettoyer_brouillons`, ménage SfM. 257 → 268 tests. |
| 5 | livré 2026-11-14 | `Lot 5` | Service `bilan-thermique-worker`, tests découpés (268 = 268), `lab-module.json`, `README.md`, exception three.js documentée, `npm ci`. |
| 8 | livré 2026-11-14 | `Lot 8` | `BuildingFact` + catalogue de clés, fiche effective, surcharges réversibles, scores de confiance/complétude, page Fiche. 268 → 282 tests. |
| 9 | livré 2026-11-14 | `Lot 9` | Chaîne A1→A6 (BAN, RNB, BDNB/DPE, BD TOPO, archétypes), cache + quota, mitoyenneté et horizon, carte d'initialisation. **10/10 adresses réelles** en ~2 s. 282 → 301 tests. |
