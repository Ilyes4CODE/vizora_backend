import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storefront', '0002_pixels'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DesignImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='design/')),
                ('width', models.PositiveIntegerField(default=0)),
                ('height', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddField(
            model_name='storesettings',
            name='design_token',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.CreateModel(
            name='Layout',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('site', 'الشكل العام'), ('home', 'الصفحة الرئيسية'), ('landing', 'صفحة هبوط')], max_length=10)),
                ('slug', models.SlugField(max_length=60, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('lang', models.CharField(default='ar', max_length=2)),
                ('draft', models.JSONField(default=dict)),
                ('published', models.JSONField(blank=True, null=True)),
                ('is_live', models.BooleanField(default=False)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('store_product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='landings', to='storefront.storeproduct')),
            ],
            options={
                'ordering': ['kind', '-created_at', 'id'],
            },
        ),
        migrations.AddField(
            model_name='orderorigin',
            name='landing',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='origins', to='storefront.layout'),
        ),
        migrations.CreateModel(
            name='LayoutVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data', models.JSONField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('layout', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='storefront.layout')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='LandingVisit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField()),
                ('count', models.PositiveIntegerField(default=0)),
                ('layout', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='visits', to='storefront.layout')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('layout', 'day'), name='unique_landing_visit_day')],
            },
        ),
    ]
