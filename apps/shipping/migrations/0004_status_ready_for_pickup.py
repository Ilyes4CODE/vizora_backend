from django.db import migrations


def add_status(apps, schema_editor):
    config_model = apps.get_model("shipping", "MaystroConfig")
    for config in config_model.objects.all():
        if "33" not in config.status_map:
            config.status_map = {**config.status_map, "33": "desk"}
            config.save(update_fields=["status_map"])


def remove_status(apps, schema_editor):
    config_model = apps.get_model("shipping", "MaystroConfig")
    for config in config_model.objects.all():
        config.status_map = {key: value for key, value in config.status_map.items() if key != "33"}
        config.save(update_fields=["status_map"])


class Migration(migrations.Migration):
    dependencies = [("shipping", "0003_maystro_account")]
    operations = [migrations.RunPython(add_status, remove_status)]
