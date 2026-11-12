from django.db import migrations

DEFAULTS = {
    "confirmation": {"orders", "confirmation", "shipping"},
    "stock": {"orders", "products", "shipping"},
    "accountant": {"accounting", "dues", "campaigns", "reports"},
}
SECTIONS = ["orders", "confirmation", "products", "shipping", "accounting", "dues", "campaigns", "reports", "team", "security"]


def create_defaults(apps, schema_editor):
    permission = apps.get_model("accounts", "RolePermission")
    permission.objects.bulk_create(
        [
            permission(role=role, section=section, allowed=section in allowed)
            for role, allowed in DEFAULTS.items()
            for section in SECTIONS
        ]
    )


def remove_defaults(apps, schema_editor):
    apps.get_model("accounts", "RolePermission").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(create_defaults, remove_defaults)]
