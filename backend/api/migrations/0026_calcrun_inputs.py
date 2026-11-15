from django.db import migrations, models


class Migration(migrations.Migration):
    """Refonte (Lot 23) : entrées figées d'un calcul. Additive : vide pour les calculs passés."""

    dependencies = [('api', '0025_mcp_tokens')]

    operations = [
        migrations.AddField(model_name='calcrun', name='inputs', field=models.JSONField(blank=True, null=True)),
    ]
