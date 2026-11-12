import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0006_sync_progress'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='maystroconfig',
            name='live_error',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='live_parcels_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='live_stock_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='address',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='alerted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='customer_name',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='customer_phone',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='customer_phone2',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='live_seen_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='postponed_to',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='suivi_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='suivi_next_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='suivi_status',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroproduct',
            name='low_stock_level',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='ParcelCall',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('outcome', models.CharField(max_length=20)),
                ('message', models.CharField(max_length=255)),
                ('note', models.TextField(blank=True)),
                ('next_call_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('parcel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='calls', to='shipping.maystroorder')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
