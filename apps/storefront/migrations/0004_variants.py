from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storefront', '0003_design'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeproduct',
            name='options',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
