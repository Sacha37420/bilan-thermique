from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_calcrun_inputs'),
    ]

    operations = [
        migrations.AddField(model_name='portfolio', name='orchestration',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='portfolio', name='parametrage',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='portfolio', name='rendu',
                            field=models.JSONField(blank=True, default=dict)),
    ]
