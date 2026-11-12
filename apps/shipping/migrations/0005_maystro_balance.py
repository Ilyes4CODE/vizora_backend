import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0004_status_ready_for_pickup'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='maystroconfig',
            name='balance_start_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroorder',
            name='shipped_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystropayment',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='maystropayment',
            name='note',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='maystropayment',
            name='source',
            field=models.CharField(choices=[('manual', 'مسجّل يدوياً'), ('webhook', 'إشعار من Maystro')], default='webhook', max_length=10),
        ),
    ]
