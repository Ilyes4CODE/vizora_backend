import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0001_initial'),
        ('core', '0001_initial'),
        ('finance', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportBatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_name', models.CharField(max_length=200)),
                ('columns', models.JSONField(default=list)),
                ('rows', models.JSONField(default=list)),
                ('mapping', models.JSONField(default=dict)),
                ('result', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('uploaded', 'مرفوع'), ('committed', 'مستورد')], default='uploaded', max_length=10)),
                ('created_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(blank=True, max_length=30, null=True, unique=True)),
                ('customer_name', models.CharField(max_length=150)),
                ('phone', models.CharField(db_index=True, max_length=15)),
                ('commune', models.CharField(blank=True, max_length=80)),
                ('address', models.CharField(blank=True, max_length=255)),
                ('delivery_type', models.CharField(choices=[('home', 'إلى المنزل'), ('desk', 'مكتب التوصيل')], default='home', max_length=10)),
                ('subtotal', models.PositiveIntegerField(default=0)),
                ('delivery_fee', models.PositiveIntegerField(default=0)),
                ('total', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('pending', 'بانتظار التأكيد'), ('confirmed', 'مؤكدة'), ('shipping', 'قيد الشحن'), ('delivered', 'مسلّمة'), ('returned', 'مرتجعة'), ('cancelled', 'ملغاة')], db_index=True, default='pending', max_length=12)),
                ('source', models.CharField(choices=[('manual', 'يدوية'), ('excel', 'استيراد Excel')], default='manual', max_length=10)),
                ('note', models.TextField(blank=True)),
                ('call_attempts', models.PositiveSmallIntegerField(default=0)),
                ('next_call_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('cancel_reason', models.CharField(blank=True, max_length=30)),
                ('suivi_attempts', models.PositiveSmallIntegerField(default=0)),
                ('suivi_next_at', models.DateTimeField(blank=True, null=True)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('shipped_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('returned_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_orders', to=settings.AUTH_USER_MODEL)),
                ('campaign', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='finance.campaign')),
                ('confirmed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('wilaya', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orders', to='core.wilaya')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='OrderEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stage', models.CharField(choices=[('system', 'النظام'), ('confirm', 'التأكيد'), ('suivi', 'السويفي')], default='system', max_length=10)),
                ('outcome', models.CharField(blank=True, max_length=20)),
                ('message', models.CharField(max_length=255)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='orders.order')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField()),
                ('unit_price', models.PositiveIntegerField()),
                ('unit_cost', models.PositiveIntegerField()),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='orders.order')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='order_items', to='catalog.product')),
            ],
        ),
    ]
