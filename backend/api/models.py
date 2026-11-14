from django.db import models


class Department(models.Model):
    """Département ou équipe de l'organisation."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'departments'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class UserRecord(models.Model):
    """Enregistrement d'un utilisateur Keycloak, créé automatiquement à la première connexion."""

    email = models.EmailField(primary_key=True, max_length=255)
    display_name = models.CharField(max_length=200, blank=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='members',
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_records'
        ordering = ['email']

    def __str__(self) -> str:
        return self.display_name or self.email


class ParoiModel(models.Model):
    """Modèle de paroi réutilisable — un empilement de couches thermiques/optiques.

    layers : liste de dicts {e, lam, rho, c, tau, r, alpha}, même structure que
    api.serializers.LayerSerializer, validée avant sauvegarde par ParoiModelSerializer.
    Référencé par id depuis un maillage de bâtiment (chaque triangle pointe vers
    un modèle plutôt que de dupliquer sa définition).
    """

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    layers = models.JSONField()

    # Cadre de fenêtre (Lot I, optionnel — pertinent seulement pour un modèle de
    # type fenêtre). Les deux doivent être renseignés ensemble pour être exploités
    # par le solveur (building_solver.run_building_simulation) : frame_u (W/m²·K,
    # déjà inclusif des résistances superficielles, comme Ug/Uw dans le vocabulaire
    # standard des fenêtres) et frame_fraction (0..1, part de l'aire du triangle
    # occupée par le cadre plutôt que le vitrage). Le cadre est traité comme une
    # résistance directe extérieur->air (même schéma que g_vent, Lot G), le
    # vitrage garde son maillage multicouche complet (gains solaires, capacité)
    # sur l'aire réduite (1 - frame_fraction) — voir to_do.md, Lot I.
    frame_u = models.FloatField(null=True, blank=True)
    frame_fraction = models.FloatField(null=True, blank=True)

    # Modèle de vitrage plutôt que de paroi opaque (Lot T, mode simplifié) —
    # champ explicite plutôt qu'une heuristique déduite des couches (ex. au
    # moins une couche à tau > 0) pour ne pas dépendre d'un indice indirect qui
    # casserait si un mur avait par erreur une couche légèrement translucide.
    # Pilote uniquement les listes déroulantes "vitrage" vs "opaque" du mode
    # simplifié — n'affecte jamais le solveur (qui se base uniquement sur les
    # couches réelles, tau compris).
    is_glazing = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'paroi_models'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Environment(models.Model):
    """Maillage d'environnement — obstacles alentour (bâtiments voisins, relief)
    utilisés pour le test de visibilité solaire d'un Building (Lot C).

    Pas d'assignation de paroi ici, juste de la géométrie d'occlusion :
    envelope = {"vertices": [[x,y,z],...], "triangles": [{"v":[i,j,k]}, ...]}.
    L'enveloppe du bâtiment lui-même s'ajoute à ce maillage au moment du calcul
    d'ombrage (auto-ombrage) — Environment ne contient que les obstacles tiers.
    """

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    envelope = models.JSONField(default=dict, blank=True)

    # Lot AH — environnement « observé » (LiDAR HD × BD TOPO, api.observed_env).
    # Un environnement généré a désormais SON PROPRE repère géoréférencé : c'est
    # lui qui s'impose au bâtiment étudié qu'on en tire (même origine, même cap,
    # même z = 0), et non plus l'inverse. Vide pour un maillage importé à la main.
    georef_lat = models.FloatField(null=True, blank=True)
    georef_lon = models.FloatField(null=True, blank=True)
    georef_north_offset_deg = models.FloatField(default=0.0)
    georef_ground_z = models.FloatField(null=True, blank=True)
    # Objets individuels (bâtiments, arbres, terrain), chacun avec son maillage
    # et un statut active/removed/studied — voir observed_env.make_object.
    # `envelope` n'est plus qu'une vue dérivée (observed_env.compose_envelope),
    # recalculée à chaque changement de statut : seul format lu par api.shadow.
    # Nommé scene_objects et non `objects`, réservé au gestionnaire Django ;
    # l'API l'expose sous le nom `objects`.
    scene_objects = models.JSONField(default=list, blank=True)
    # Compte rendu de génération : source (lidar / repli BD TOPO-OSM), dalles et
    # dates d'acquisition LiDAR, statistiques, avertissements.
    generation = models.JSONField(default=dict, blank=True)
    # Lot 3 (refonte, point L6) : régénérer l'environnement d'un bâtiment ne laisse plus
    # s'accumuler les anciens. L'ancien n'est pas supprimé (il peut porter des retraits ou
    # remplacements d'objets faits à la main) : il est marqué remplacé et sort de la liste.
    replaced_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='replaces')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'environments'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Portfolio(models.Model):
    """Parc de bâtiments (refonte, Lot 11). Rôles par personne (`sub`) dans `PortfolioMember`."""

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    owner_sub = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portfolios'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class PortfolioMember(models.Model):
    GESTIONNAIRE_PARC, GESTIONNAIRE_BAT, LECTEUR = 'gestionnaire_parc', 'gestionnaire_bat', 'lecteur'
    ROLES = [(GESTIONNAIRE_PARC, 'Gestionnaire de parc'), (GESTIONNAIRE_BAT, 'Gestionnaire de bâtiment'),
             (LECTEUR, 'Lecteur')]
    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name='members')
    sub = models.CharField(max_length=64)
    display_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=ROLES)
    added_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portfolio_members'
        constraints = [models.UniqueConstraint(fields=['portfolio', 'sub'], name='portfolio_member_unique')]


class Building(models.Model):
    """Enveloppe d'un bâtiment — maillage triangulaire + assignation d'un
    ParoiModel par triangle.

    envelope : {
      "vertices": [[x, y, z], ...],
      "triangles": [
        {"v": [i0, i1, i2], "group": "nom_groupe_obj" | null,
         "paroi_model_id": int | null,
         "area": m², "normal": [nx, ny, nz], "tilt_deg": .., "azimuth_deg": ..},
        ...
      ]
    }
    area/normal/tilt_deg/azimuth_deg sont toujours recalculés côté serveur
    (api.geometry) à partir des sommets — jamais pris tels quels depuis le
    client. Convention : axe vertical = Z (Z-up), à confirmer/ajuster si les
    fichiers importés utilisent une autre convention.

    sun_visibility : rempli par api.tasks.precompute_shadows (Lot C) :
    {
      "azimuths_deg": [0, 15, ...], "elevations_deg": [0, 15, ..., 90],
      "per_triangle": [ [[0|1, ...] x n_elevations] x n_azimuths, ... ]
    }
    per_triangle[i] est la grille du triangle d'indice i dans envelope.triangles.
    Vide tant qu'aucun calcul n'a été lancé, ou périmé si l'enveloppe/l'environnement
    a changé depuis (voir Building.sun_visibility_stale).
    """

    # Refonte (Lot 11) : unique par parc, et parmi les bâtiments sans parc (contraintes plus bas).
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    envelope = models.JSONField(default=dict, blank=True)
    environment = models.ForeignKey(
        Environment, null=True, blank=True, on_delete=models.SET_NULL, related_name='buildings',
    )
    # Refonte (Lot 11) : parc de rattachement ; vide = bâtiment commun de l'app (comme avant).
    portfolio = models.ForeignKey('Portfolio', null=True, blank=True, on_delete=models.CASCADE,
                                  related_name='buildings')

    # Géoréférencement (optionnel) — ancre le repère local du bâtiment (0,0 = ce point,
    # +Y = ce cap réel) dans le monde réel, pour que la génération automatique
    # d'environnement (api.geodata) place les obstacles dans le même repère que le
    # bâtiment plutôt que de laisser l'alignement à la charge de l'utilisateur.
    georef_lat = models.FloatField(null=True, blank=True)
    georef_lon = models.FloatField(null=True, blank=True)
    georef_north_offset_deg = models.FloatField(default=0.0)
    georef_ground_z = models.FloatField(null=True, blank=True)

    # Surface de référence (m², Lot M) — saisie directe par l'utilisateur, sert
    # uniquement à normaliser l'affichage du dashboard (heating_kwh/surface_ref_m2,
    # comparable aux seuils RT2012/RE2020 du catalogue de parois) : ne participe à
    # aucun calcul du solveur lui-même.
    surface_ref_m2 = models.FloatField(null=True, blank=True)

    # Suggestion de ventilation (Lot T, mode simplifié) — calculée côté client à
    # partir d'un profil du catalogue (frontend/src/app/core/ventilation-profiles.ts)
    # et du volume réel du bâtiment (empreinte × hauteur, connus du bâtiment issu
    # d'une recherche IGN/OSM). Jamais lue par le solveur : une SUGGESTION que
    # Calcul 3D relit au chargement du bâtiment pour préremplir son propre
    # formulaire de renouvellement d'air (mêmes deux grandeurs que
    # BuildingInteriorSerializer.debit_vent_m3h/eta_recup_vent), qui reste
    # modifiable ensuite comme pour tout bâtiment — même schéma que georef_lat/lon
    # préremplissant le panneau météo de Calcul 3D.
    suggested_debit_vent_m3h = models.FloatField(null=True, blank=True)
    suggested_eta_recup_vent = models.FloatField(null=True, blank=True)

    # Lot AK — façades analysées sur les photos de rue Panoramax : par façade,
    # prise de vue retenue (recalée), texture, baies détectées ; plus
    # l'enveloppe de BASE (avant insertion des vitrages), pour réappliquer.
    facades = models.JSONField(default=dict, blank=True)

    sun_visibility = models.JSONField(default=dict, blank=True)
    sun_visibility_stale = models.BooleanField(default=True)

    # Refonte (Lot 9) : chaîne automatique (api.pipeline).
    rnb_id = models.CharField(max_length=20, blank=True, default='', db_index=True)
    address = models.CharField(max_length=300, blank=True, default='')
    pipeline_status = models.CharField(max_length=12, blank=True, default='')  # '' | en_attente | en_cours | complet | incomplet
    # Compte rendu : étapes (ok / erreur, message fixe), géométrie simplifiée, horizon par façade.
    pipeline = models.JSONField(default=dict, blank=True)

    # Refonte (Lot 8) : scores de la fiche effective (api.facts.scores), tenus à jour à chaque fait.
    confidence = models.FloatField(default=0.0)
    completeness = models.FloatField(default=0.0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buildings'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['portfolio', 'name'], name='building_name_per_portfolio'),
            models.UniqueConstraint(fields=['name'], condition=models.Q(portfolio__isnull=True),
                                    name='building_name_common'),
        ]

    def __str__(self) -> str:
        return self.name


class Job(models.Model):
    """Tâche asynchrone (Celery) — précalcul d'ombrage, résolution FEM bâtiment.

    Même forme que dans les autres apps du lab (atelier-3d, carto-lab,
    lab-admin) : la tâche Celery met à jour l'état via set_state() au fil de
    son exécution, le frontend interroge GET /api/jobs/<id>/ en polling.
    """

    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    DONE = 'DONE'
    ERROR = 'ERROR'
    STATUS_CHOICES = [(PENDING, 'Pending'), (RUNNING, 'Running'), (DONE, 'Done'), (ERROR, 'Error')]

    kind = models.CharField(max_length=50)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    progress = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    celery_task_id = models.CharField(max_length=100, blank=True)
    params = models.JSONField(default=dict, blank=True)
    result = models.JSONField(null=True, blank=True)
    # Lot 2 (refonte) : qui a lancé le job (`sub` Keycloak, vide pour un job d'avant la refonte
    # ou lancé par le système). Sert à ne rendre un job qu'à celui qui l'a lancé, et à garder les
    # traitements faits avec la clé d'IA d'un utilisateur pour cet utilisateur seul.
    owner_sub = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'jobs'
        ordering = ['-created_at']

    def set_state(self, status=None, progress=None, message=None):
        if status is not None:
            self.status = status
        if progress is not None:
            self.progress = progress
        if message is not None:
            self.message = message
        self.save(update_fields=['status', 'progress', 'message', 'updated_at'])

    def __str__(self) -> str:
        return f"{self.kind} #{self.pk} [{self.status}]"


class CalcRun(models.Model):
    """Un calcul passé, rechargeable sans recalcul (Lot 3, refonte — point U3).

    Un par calcul terminé ou en cours, quel que soit le niveau :
    - N0 : bilan statique mensuel (Lot 10) ; N1 : horaire réduit 5R1C (Lot 10) ;
    - N2 : FEM par triangle (`building_solver`, existant, lancé par Calcul 3D).

    `inputs_hash` : empreinte des entrées (fiche effective + météo + réglages) — dit si un résultat
    est à jour et évite de refaire un calcul identique. `indicators` : les chiffres du tableau de
    parc (petits, indexables) ; `results` : le résultat complet (séries horaires comprises), lu
    seulement sur la fiche d'un calcul.
    """

    N0, N1, N2 = 'N0', 'N1', 'N2'
    LEVEL_CHOICES = [(N0, 'N0 — statique mensuel'), (N1, 'N1 — horaire réduit'), (N2, 'N2 — FEM 3D')]

    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='calc_runs')
    # Variante (Lot 18) ; vide = état actuel (base).
    scenario = models.ForeignKey('Scenario', null=True, blank=True, on_delete=models.CASCADE, related_name='calc_runs')
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES)
    status = models.CharField(max_length=10, choices=Job.STATUS_CHOICES, default=Job.PENDING)
    job = models.ForeignKey(Job, null=True, blank=True, on_delete=models.SET_NULL, related_name='calc_runs')
    # Météo : description (source, lieu, période, empreinte de la série).
    weather = models.JSONField(default=dict, blank=True)
    inputs_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # Réglages du calcul (sans la série météo, qui peut peser plusieurs Mo).
    settings = models.JSONField(default=dict, blank=True)
    indicators = models.JSONField(default=dict, blank=True)
    results = models.JSONField(null=True, blank=True)
    message = models.CharField(max_length=500, blank=True)
    duration_s = models.FloatField(null=True, blank=True)
    owner_sub = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'calc_runs'
        ordering = ['-created_at']
        constraints = [
            # Verrou du § 8 : au plus un calcul actif par (bâtiment, variante, niveau).
            models.UniqueConstraint(
                fields=['building', 'scenario', 'level'],
                condition=models.Q(status__in=['PENDING', 'RUNNING']),
                nulls_distinct=False,
                name='calc_run_one_active',
            ),
        ]
        indexes = [models.Index(fields=['building', 'level', '-created_at'])]

    def __str__(self) -> str:
        return f"{self.level} #{self.pk} bâtiment {self.building_id} [{self.status}]"


class Scenario(models.Model):
    """Variante d'un bâtiment (refonte, principe 4) : ne stocke que ses différences, sous forme de
    faits rattachés à la variante (`BuildingFact.scenario`). La base (état actuel) n'a pas de ligne."""

    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='scenarios')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    # Météo propre à la variante (climat futur…), sinon celle de la base.
    weather = models.JSONField(default=dict, blank=True)
    author_sub = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'scenarios'
        ordering = ['building', 'created_at']

    def __str__(self) -> str:
        return f"{self.name} (bâtiment {self.building_id})"


class BuildingFact(models.Model):
    """Une valeur d'une clé de la fiche, avec sa provenance (Lot 8, refonte). Voir api.facts."""

    METHODS = [('mesure', 'Mesuré'), ('document', 'Document'), ('declare', 'Déclaré'),
               ('infere', 'Inféré'), ('defaut', 'Défaut')]

    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='facts')
    scenario = models.ForeignKey(Scenario, null=True, blank=True, on_delete=models.CASCADE, related_name='facts')
    key = models.CharField(max_length=80)
    value = models.JSONField(null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    method = models.CharField(max_length=10, choices=METHODS)
    source = models.CharField(max_length=300, blank=True)
    confidence = models.FloatField(default=0.5)
    # Personne : `sub` Keycloak (identité) ; `author_name` n'est qu'un affichage.
    author_sub = models.CharField(max_length=64, blank=True, default='')
    author_name = models.CharField(max_length=150, blank=True, default='')
    # Extrait cité (document lu, Lot 21) : {document_id, page, texte}.
    extract = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)
    retired_by = models.CharField(max_length=64, blank=True, default='')
    superseded_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='supersedes')

    class Meta:
        db_table = 'building_facts'
        ordering = ['building', 'key', '-created_at']
        indexes = [models.Index(fields=['building', 'key'])]

    def __str__(self) -> str:
        return f"{self.key}={self.value!r} [{self.method}] bâtiment {self.building_id}"


class SourceCache(models.Model):
    """Réponse d'un service open data, gardée par clé (Lot 9, refonte — api.sources)."""

    key = models.CharField(max_length=300, unique=True)
    service = models.CharField(max_length=30, blank=True)
    payload = models.JSONField(null=True, blank=True)
    fetched_at = models.DateTimeField()

    class Meta:
        db_table = 'source_cache'


class BuildingAccess(models.Model):
    """Bâtiment confié à une personne (refonte, Lot 11) : gestionnaire ou occupant (D2)."""

    GESTIONNAIRE, OCCUPANT = 'gestionnaire', 'occupant'
    ROLES = [(GESTIONNAIRE, 'Gestionnaire du bâtiment'), (OCCUPANT, 'Occupant')]
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='accesses')
    sub = models.CharField(max_length=64)
    display_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=ROLES)
    added_by = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'building_accesses'
        constraints = [models.UniqueConstraint(fields=['building', 'sub'], name='building_access_unique')]


class Invitation(models.Model):
    """Invitation par code à un parc ou à un bâtiment (refonte, Lot 11). Aucun annuaire : la personne
    invitée ouvre le lien avec son compte, et c'est son `sub` qui est enregistré. Seule l'empreinte
    du code est gardée."""

    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name='invitations')
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.CASCADE, related_name='invitations')
    role = models.CharField(max_length=20)
    token_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12)
    created_by = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invitations'
