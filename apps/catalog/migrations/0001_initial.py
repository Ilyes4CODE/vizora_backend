import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, unique=True)),
                ('sku', models.CharField(blank=True, max_length=60, null=True, unique=True)),
                ('price', models.PositiveIntegerField()),
                ('cost', models.PositiveIntegerField(default=0)),
                ('on_hand', models.PositiveIntegerField(default=0, editable=False)),
                ('low_stock_threshold', models.PositiveIntegerField(default=50)),
                ('image', models.ImageField(blank=True, null=True, upload_to='products/')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('in', 'إدخال مخزون'), ('ship', 'شحن عبر Maystro'), ('return', 'مرتجع سليم'), ('adjust', 'تعديل جرد')], max_length=10)),
                ('quantity', models.IntegerField()),
                ('balance_after', models.PositiveIntegerField()),
                ('note', models.CharField(blank=True, max_length=255)),
                ('order_reference', models.CharField(blank=True, max_length=30)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movements', to='catalog.product')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
