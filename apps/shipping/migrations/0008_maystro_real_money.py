from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping', '0007_maystro_live'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='maystroconfig',
            name='mode',
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='balance_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='balance_current',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='balance_payable',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='maystroconfig',
            name='payments_synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='maystropayment',
            name='source',
            field=models.CharField(choices=[('manual', 'مسجّل يدوياً'), ('webhook', 'إشعار من Maystro'), ('maystro', 'من Maystro')], default='webhook', max_length=10),
        ),
    ]
