import apps.core.models
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
            name='Backup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_name', models.CharField(max_length=120, unique=True)),
                ('size_bytes', models.PositiveBigIntegerField()),
                ('kind', models.CharField(choices=[('auto', 'تلقائية'), ('manual', 'يدوية'), ('pre_restore', 'قبل الاسترجاع')], max_length=20)),
                ('created_by_name', models.CharField(blank=True, max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='BusinessProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='Vizora Global', max_length=120)),
                ('phone', models.CharField(default='+213 667 27 76 76', max_length=30)),
                ('email', models.EmailField(default='Abd.allah.yt123456@gmail.com', max_length=254)),
                ('address', models.CharField(default='الحميز، الدار البيضاء، الجزائر', max_length=200)),
                ('facebook', models.CharField(default='Vizora Globab', max_length=120)),
                ('instagram', models.CharField(default='@vizora_global', max_length=120)),
                ('activity', models.TextField(default='تسويق وترويج · تفعيل بطاقات الفيزا · شحن الألعاب والتطبيقات والاشتراكات')),
                ('working_hours', models.JSONField(default=apps.core.models.default_working_hours)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='SecuritySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('two_factor_required', models.BooleanField(default=False)),
                ('auto_logout_enabled', models.BooleanField(default=True)),
                ('auto_logout_minutes', models.PositiveSmallIntegerField(default=30)),
                ('approved_devices_only', models.BooleanField(default=False)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Wilaya',
            fields=[
                ('code', models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ('name_ar', models.CharField(max_length=60)),
                ('name_fr', models.CharField(max_length=60)),
                ('home_fee', models.PositiveIntegerField(default=600)),
                ('desk_fee', models.PositiveIntegerField(default=400)),
                ('home_available', models.BooleanField(default=True)),
                ('desk_available', models.BooleanField(default=True)),
                ('courier_ref', models.CharField(blank=True, max_length=40)),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_name', models.CharField(blank=True, max_length=150)),
                ('action', models.CharField(db_index=True, max_length=60)),
                ('message', models.CharField(max_length=255)),
                ('target_type', models.CharField(blank=True, max_length=40)),
                ('target_id', models.CharField(blank=True, max_length=40)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Commune',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name_ar', models.CharField(max_length=80)),
                ('name_fr', models.CharField(blank=True, max_length=80)),
                ('courier_ref', models.CharField(blank=True, max_length=40)),
                ('wilaya', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='communes', to='core.wilaya')),
            ],
            options={
                'ordering': ['name_ar'],
                'constraints': [models.UniqueConstraint(fields=('wilaya', 'name_ar'), name='unique_commune_per_wilaya')],
            },
        ),
    ]
