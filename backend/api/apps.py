from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'
    verbose_name = 'Bilan Thermique API'

    def ready(self):
        # Chantier bt-trois-niveaux : signaux qui planifient le recalcul automatique (secteur C).
        from .recalcul import signaux  # noqa: F401

