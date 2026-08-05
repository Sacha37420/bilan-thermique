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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'environments'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


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

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    envelope = models.JSONField(default=dict, blank=True)
    environment = models.ForeignKey(
        Environment, null=True, blank=True, on_delete=models.SET_NULL, related_name='buildings',
    )
    sun_visibility = models.JSONField(default=dict, blank=True)
    sun_visibility_stale = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'buildings'
        ordering = ['name']

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
