from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='source',
            field=models.CharField(choices=[('manual', 'يدوية'), ('excel', 'استيراد Excel'), ('website', 'الموقع')], default='manual', max_length=10),
        ),
    ]
