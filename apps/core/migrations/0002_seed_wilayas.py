from django.db import migrations

from apps.core.wilayas import WILAYAS


def create_wilayas(apps, schema_editor):
    wilaya = apps.get_model("core", "Wilaya")
    wilaya.objects.bulk_create([wilaya(code=code, name_ar=ar, name_fr=fr) for code, ar, fr in WILAYAS])


def remove_wilayas(apps, schema_editor):
    apps.get_model("core", "Wilaya").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_wilayas, remove_wilayas)]
