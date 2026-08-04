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
