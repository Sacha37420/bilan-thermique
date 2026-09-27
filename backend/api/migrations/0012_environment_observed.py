from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0011_building_suggested_debit_vent_m3h_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='environment',
            name='georef_lat',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='georef_lon',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='georef_north_offset_deg',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='environment',
            name='georef_ground_z',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='scene_objects',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='environment',
            name='generation',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
