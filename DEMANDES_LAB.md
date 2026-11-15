# Demandes au lab — bilan-thermique (refonte)

Changements **hors de l'app** dont dépend une partie de la refonte. Rien ici n'est appliqué par
l'app elle-même : chaque demande attend l'architecte du lab (le propriétaire). Format : quoi,
pourquoi, diff exact, fichiers, apps impactées, cohérence avec le lab, test de non-régression.
L'app fonctionne **sans** ces changements : la partie concernée est désactivée et le dit.

---

## DL-1 — Variables `.env` à poser par `env_set` (moteur)

| Clé | Valeur | Pourquoi | Effet si absente |
| --- | --- | --- | --- |
| `STORAGE_DOCUMENTS_ENABLED` | `True` — **seulement après DL-2** | Conserver l'original des documents dans l'espace `storage` de l'utilisateur (Lot 21) | Seul le texte lu est gardé ; l'interface le dit |
| `VAULT_ENCRYPTION_KEY` | une clé Fernet neuve (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) | Coffre des clés d'IA des utilisateurs (Lot 21, § B8) — jamais dérivée de `SECRET_KEY` | Page « Intelligence artificielle » : enregistrement refusé (« coffre non configuré ») ; rien d'autre ne change |

- Cohérence : clé propre à l'app, non gérée par le lab (`DB_*`, `KEYCLOAK_*`, `SECRET_KEY`, `DOMAIN`,
  `LAB_*` exclus) ; même nom que dans App Builder et les quatre apps à coffre ; `install.sh` du lab
  la génère déjà pour une app neuve (`fresh-env.sh`) si elle est dans `.env.example`.
- **Ne jamais la régénérer** une fois posée : les clés enregistrées deviendraient illisibles.
- Test : `GET /bilan-thermique-api/api/ai-keys/` rend `"vault": true`.

## DL-2 — `storage` : faire confiance au client `bilan-thermique`

- **Quoi** : ajouter `bilan-thermique` à `KEYCLOAK_TRUSTED_CLIENTS` dans `storage/.env`.
  ```diff
  -KEYCLOAK_TRUSTED_CLIENTS=storage,carto-lab,atelier-3d,conciergerie,traitement-de-fichiers-compils,arbre-genealogique,code-route,craft-lab
  +KEYCLOAK_TRUSTED_CLIENTS=storage,carto-lab,atelier-3d,conciergerie,traitement-de-fichiers-compils,arbre-genealogique,code-route,craft-lab,bilan-thermique
  ```
  puis `bash scripts/app-restart.sh storage` (pas de reconstruction).
- **Pourquoi** : les documents d'un bâtiment (DPE, audit, factures, imports météo et mesures) sont des
  fichiers d'utilisateur ; la règle du gabarit les envoie à `storage`. Variante `carto-lab` : le jeton
  de l'utilisateur est transmis, le fichier va dans **son** espace personnel ; aucune route de
  `storage` ne change.
- **Fichiers** : `storage/.env` seulement. **Apps impactées** : aucune (liste blanche additive).
- **Cohérence** : `admin-cli` n'entre pas dans la liste ; le contrôle de groupe de `storage` continue
  de s'appliquer (le jeton porte les mêmes `groups`) ; `bilan-thermique` n'obtient aucun droit sur
  l'espace d'un autre compte.
- **Test** : `bash storage/frontend/e2e/jouer-gestes.sh --deploye` toujours vert (25 gestes) ; un
  dépôt de document depuis bilan-thermique crée `bilan-thermique/<bâtiment>/…` dans l'espace de
  l'utilisateur, visible dans l'interface de `storage`.
- **En attendant** : le document est lu en mémoire (texte extrait gardé, original **non conservé**),
  et l'interface le dit (« original non conservé : storage non relié »).

## DL-3 — Pointeur du sous-module dans le dépôt du lab

- Après fusion de la branche `refonte-parc` dans `main` du dépôt de l'app : mettre à jour le pointeur
  `deploiement/bilan-thermique` du dépôt du lab (commit « chore: pointeur bilan-thermique »).
- Aucun autre fichier du lab.

## Sans demande au lab — serveur MCP (Lot 22)

`POST /bilan-thermique-api/api/mcp/` passe par le chemin Caddy existant `/bilan-thermique-api` : aucun
changement de Caddy, de Keycloak ni de `.env`. L'adresse affichée aux utilisateurs vient de `DOMAIN` et
`SCRIPT_NAME`, déjà posés par le lab.
