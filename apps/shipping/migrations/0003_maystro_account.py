import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0002_maystro_official_api'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaystroPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=64, unique=True)),
                ('paid_on', models.DateTimeField()),
                ('order_ids', models.JSONField(default=list)),
                ('orders_count', models.PositiveIntegerField(default=0)),
                ('amount', models.PositiveBigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-paid_on', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MaystroProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('maystro_id', models.CharField(max_length=64, unique=True)),
                ('display_id', models.PositiveIntegerField(blank=True, null=True)),
                ('name', models.CharField(max_length=255)),
                ('reference', models.CharField(blank=True, max_length=120)),
                ('delivery_rate', models.FloatField(blank=True, null=True)),
                ('cancelation_rate', models.FloatField(blank=True, null=True)),
                ('deleted', models.BooleanField(default=False)),
                ('last_used', models.DateTimeField(blank=True, null=True)),
                ('stock', models.JSONField(default=list)),
                ('synced_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='current_points',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_full_sync_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_running_since',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_sync_error',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_sync_ok',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='mirror_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='store_id',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name='MaystroOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('maystro_id', models.CharField(max_length=64, unique=True)),
                ('display_id', models.CharField(db_index=True, max_length=40)),
                ('status', models.PositiveSmallIntegerField(db_index=True)),
                ('product_price', models.BigIntegerField(default=0)),
                ('delivery_price', models.BigIntegerField(default=0)),
                ('total_price', models.BigIntegerField(default=0)),
                ('return_fees', models.BigIntegerField(blank=True, null=True)),
                ('paid_to_store', models.BooleanField(default=False)),
                ('is_refund', models.BooleanField(default=False)),
                ('is_exchange', models.BooleanField(default=False)),
                ('delivery_type', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('wilaya', models.CharField(blank=True, max_length=80)),
                ('wilaya_id', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('commune_name', models.CharField(blank=True, max_length=120)),
                ('product_names', models.CharField(blank=True, max_length=500)),
                ('products', models.JSONField(default=list)),
                ('abort_reason', models.CharField(blank=True, max_length=200)),
                ('alert_reason', models.CharField(blank=True, max_length=200)),
                ('ordered_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('remote_updated_at', models.DateTimeField(blank=True, null=True)),
                ('synced_at', models.DateTimeField(auto_now=True)),
                ('shipment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mirror_orders', to='shipping.shipment')),
                ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='shipping.maystropayment')),
            ],
            options={
                'ordering': ['-ordered_at', '-id'],
            },
        ),
    ]
