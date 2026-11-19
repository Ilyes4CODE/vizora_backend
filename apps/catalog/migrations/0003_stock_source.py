from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_maystro_official_api'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='stock_source',
            field=models.CharField(choices=[('maystro', 'مخازن Maystro'), ('local', 'عندي')], default='maystro', max_length=10),
        ),
    ]
