from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0008_maystro_real_money'),
    ]

    operations = [
        migrations.AlterField(
            model_name='maystroproduct',
            name='display_id',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
