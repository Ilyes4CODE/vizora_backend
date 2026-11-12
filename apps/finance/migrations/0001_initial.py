import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('catalog', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Campaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, unique=True)),
                ('platform', models.CharField(choices=[('facebook', 'Facebook'), ('instagram', 'Instagram'), ('tiktok', 'TikTok'), ('other', 'أخرى')], max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('starts_on', models.DateField(blank=True, null=True)),
                ('ends_on', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='campaigns', to='catalog.product')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='Expense',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('title', models.CharField(max_length=200)),
                ('category', models.CharField(choices=[('ads', 'الإعلانات'), ('salaries', 'الرواتب'), ('packaging', 'التغليف'), ('other', 'مصاريف أخرى'), ('goods', 'شراء بضاعة')], max_length=20)),
                ('amount', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('campaign', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='expenses', to='finance.campaign')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Settlement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('courier', models.CharField(default='maystro', max_length=30)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('status', models.CharField(choices=[('open', 'غير مسددة'), ('paid', 'مسددة')], default='open', max_length=10)),
                ('paid_on', models.DateField(blank=True, null=True)),
                ('statement_uploaded_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-period_start', '-id'],
            },
        ),
        migrations.CreateModel(
            name='StatementLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tracking_number', models.CharField(max_length=60)),
                ('amount', models.IntegerField()),
                ('settlement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statement_lines', to='finance.settlement')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('settlement', 'tracking_number'), name='unique_statement_tracking')],
            },
        ),
    ]
