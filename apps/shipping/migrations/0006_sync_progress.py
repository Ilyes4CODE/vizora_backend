from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0005_maystro_balance'),
    ]

    operations = [
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_progress_done',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_progress_total',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
