from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_alter_order_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='variant',
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
