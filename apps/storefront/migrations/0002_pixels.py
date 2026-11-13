import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_alter_order_source'),
        ('storefront', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderorigin',
            name='fbc',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='orderorigin',
            name='fbp',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='orderorigin',
            name='page_url',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='orderorigin',
            name='ttclid',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='orderorigin',
            name='ttp',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='encrypted_facebook_token',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='encrypted_tiktok_token',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='facebook_server_since',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='facebook_test_code',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='purchase_moment',
            field=models.CharField(choices=[('placed', 'عند تسجيل الطلبية'), ('confirmed', 'عند تأكيد الطلبية'), ('delivered', 'عند تسليم الطرد')], default='placed', max_length=10),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='tiktok_server_since',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='storesettings',
            name='tiktok_test_code',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name='PixelEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('platform', models.CharField(choices=[('facebook', 'Facebook'), ('tiktok', 'TikTok')], max_length=10)),
                ('event_name', models.CharField(max_length=40)),
                ('event_id', models.CharField(max_length=60)),
                ('value', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('sent', 'وصل'), ('failed', 'فشل')], max_length=10)),
                ('is_test', models.BooleanField(default=False)),
                ('detail', models.CharField(blank=True, max_length=300)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='pixel_events', to='orders.order')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
