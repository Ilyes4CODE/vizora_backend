from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='maystroconfig',
            name='auth_scheme',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='base_url',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='create_path',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='external_id_field',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='field_map',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='ping_path',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='status_field',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='status_path',
        ),
        migrations.RemoveField(
            model_name='maystroconfig',
            name='tracking_field',
        ),
        migrations.AlterField(
            model_name='shipment',
            name='stage',
            field=models.CharField(choices=[('transit', 'في الطريق'), ('out', 'خرج مع الموزع'), ('desk', 'في مكتب التوصيل'), ('failed', 'تعذّر التسليم'), ('delivered', 'مسلّم'), ('returned', 'مرتجع'), ('aborted', 'ملغى عند Maystro')], db_index=True, default='transit', max_length=12),
        ),
    ]
