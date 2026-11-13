import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0002_maystro_official_api'),
        ('orders', '0002_alter_order_source'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContactMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('phone', models.CharField(max_length=15)),
                ('message', models.TextField(max_length=2000)),
                ('lang', models.CharField(default='ar', max_length=2)),
                ('ip', models.GenericIPAddressField(blank=True, null=True)),
                ('handled', models.BooleanField(db_index=True, default=False)),
                ('handled_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['handled', '-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Review',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80)),
                ('wilaya', models.CharField(blank=True, max_length=60)),
                ('text', models.TextField(max_length=600)),
                ('rating', models.PositiveSmallIntegerField(default=5)),
                ('is_published', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['sort_order', '-id'],
            },
        ),
        migrations.CreateModel(
            name='StorePage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=40, unique=True)),
                ('title_ar', models.CharField(max_length=120)),
                ('title_en', models.CharField(max_length=120)),
                ('body_ar', models.TextField()),
                ('body_en', models.TextField()),
                ('show_in_footer', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='StoreSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_open', models.BooleanField(default=True)),
                ('whatsapp', models.CharField(blank=True, default='+213667277676', max_length=30)),
                ('facebook_url', models.URLField(blank=True)),
                ('instagram_url', models.URLField(blank=True, default='https://www.instagram.com/vizora_global/')),
                ('tiktok_url', models.URLField(blank=True)),
                ('announcement_ar', models.CharField(blank=True, max_length=160)),
                ('announcement_en', models.CharField(blank=True, max_length=160)),
                ('delivery_days_ar', models.CharField(blank=True, max_length=120)),
                ('delivery_days_en', models.CharField(blank=True, max_length=120)),
                ('facebook_pixel', models.CharField(blank=True, max_length=40)),
                ('tiktok_pixel', models.CharField(blank=True, max_length=40)),
                ('google_analytics', models.CharField(blank=True, max_length=40)),
                ('orders_per_hour', models.PositiveSmallIntegerField(default=5)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='OrderOrigin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ip', models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=255)),
                ('lang', models.CharField(default='ar', max_length=2)),
                ('utm_source', models.CharField(blank=True, max_length=100)),
                ('utm_medium', models.CharField(blank=True, max_length=100)),
                ('utm_campaign', models.CharField(blank=True, max_length=100)),
                ('referrer', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='web', to='orders.order')),
            ],
        ),
        migrations.CreateModel(
            name='StoreProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=80, unique=True)),
                ('is_published', models.BooleanField(default=False)),
                ('is_featured', models.BooleanField(default=False)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('title_ar', models.CharField(max_length=150)),
                ('title_en', models.CharField(blank=True, max_length=150)),
                ('summary_ar', models.CharField(blank=True, max_length=255)),
                ('summary_en', models.CharField(blank=True, max_length=255)),
                ('description_ar', models.TextField(blank=True)),
                ('description_en', models.TextField(blank=True)),
                ('badge_ar', models.CharField(blank=True, max_length=40)),
                ('badge_en', models.CharField(blank=True, max_length=40)),
                ('compare_price', models.PositiveIntegerField(blank=True, null=True)),
                ('max_quantity', models.PositiveSmallIntegerField(default=5)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='store', to='catalog.product')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='StoreOffer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title_ar', models.CharField(max_length=120)),
                ('title_en', models.CharField(blank=True, max_length=120)),
                ('details_ar', models.CharField(blank=True, max_length=255)),
                ('details_en', models.CharField(blank=True, max_length=255)),
                ('price', models.PositiveIntegerField()),
                ('compare_price', models.PositiveIntegerField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('store_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='offers', to='storefront.storeproduct')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='StoreImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='products/')),
                ('width', models.PositiveIntegerField(default=0)),
                ('height', models.PositiveIntegerField(default=0)),
                ('alt_ar', models.CharField(blank=True, max_length=150)),
                ('alt_en', models.CharField(blank=True, max_length=150)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('store_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='images', to='storefront.storeproduct')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.CreateModel(
            name='StockAlert',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(max_length=15)),
                ('lang', models.CharField(default='ar', max_length=2)),
                ('notified', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('store_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alerts', to='storefront.storeproduct')),
            ],
            options={
                'ordering': ['notified', '-created_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('store_product', 'phone'), name='unique_stock_alert_phone')],
            },
        ),
    ]
