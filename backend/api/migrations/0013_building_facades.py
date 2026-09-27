from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0012_environment_observed'),
    ]

    operations = [
        migrations.AddField(
            model_name='building',
            name='facades',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
