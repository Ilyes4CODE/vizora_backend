import apps.shipping.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('finance', '0001_initial'),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MaystroConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('simulated', 'محاكاة'), ('live', 'مباشر')], default='simulated', max_length=10)),
                ('base_url', models.URLField(default='https://backend.maystro-delivery.com/api')),
                ('encrypted_token', models.TextField(blank=True)),
                ('auth_scheme', models.CharField(default='Token', max_length=20)),
                ('ping_path', models.CharField(blank=True, max_length=200)),
                ('create_path', models.CharField(blank=True, max_length=200)),
                ('status_path', models.CharField(blank=True, max_length=200)),
                ('field_map', models.JSONField(blank=True, default=dict)),
                ('status_map', models.JSONField(default=apps.shipping.models.default_status_map)),
                ('tracking_field', models.CharField(default='tracking_number', max_length=60)),
                ('external_id_field', models.CharField(default='id', max_length=60)),
                ('status_field', models.CharField(default='status', max_length=60)),
                ('webhook_token', models.CharField(blank=True, max_length=64)),
                ('return_fee', models.PositiveIntegerField(default=350)),
                ('last_sync_at', models.DateTimeField(blank=True, null=True)),
                ('last_sync_ok', models.BooleanField(default=True)),
                ('last_sync_error', models.CharField(blank=True, max_length=255)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Shipment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tracking_number', models.CharField(max_length=60, unique=True)),
                ('external_id', models.CharField(blank=True, max_length=60)),
                ('stage', models.CharField(choices=[('transit', 'في الطريق'), ('out', 'خرج مع الموزع'), ('desk', 'في مكتب التوصيل'), ('failed', 'تعذّر التسليم'), ('delivered', 'مسلّم'), ('returned', 'مرتجع')], db_index=True, default='transit', max_length=12)),
                ('raw_status', models.CharField(blank=True, max_length=60)),
                ('delivery_attempts', models.PositiveSmallIntegerField(default=0)),
                ('return_fee', models.PositiveIntegerField(default=0)),
                ('return_condition', models.CharField(blank=True, max_length=10)),
                ('return_received_at', models.DateTimeField(blank=True, null=True)),
                ('last_update_at', models.DateTimeField()),
                ('last_event_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField()),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='shipment', to='orders.order')),
                ('settlement', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shipments', to='finance.settlement')),
            ],
            options={
                'ordering': ['-last_update_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ShipmentEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('raw_status', models.CharField(max_length=60)),
                ('stage', models.CharField(blank=True, max_length=12)),
                ('message', models.CharField(blank=True, max_length=255)),
                ('occurred_at', models.DateTimeField()),
                ('shipment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='shipping.shipment')),
            ],
            options={
                'ordering': ['-occurred_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('shipment', 'raw_status', 'occurred_at'), name='unique_shipment_event')],
            },
        ),
    ]
