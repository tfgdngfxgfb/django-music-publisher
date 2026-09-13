import uuid

from django.db import migrations

TERRITORIES = (
    ("NO", "Norge", "71000000-0000-4000-8000-000000000001"),
    ("SE", "Sverige", "71000000-0000-4000-8000-000000000002"),
    ("DK", "Danmark", "71000000-0000-4000-8000-000000000003"),
    ("FI", "Finland", "71000000-0000-4000-8000-000000000004"),
    ("IS", "Island", "71000000-0000-4000-8000-000000000005"),
    ("GB", "Storbritannia", "71000000-0000-4000-8000-000000000006"),
    ("US", "USA", "71000000-0000-4000-8000-000000000007"),
    ("DE", "Tyskland", "71000000-0000-4000-8000-000000000008"),
)


def seed_territories(apps, schema_editor):
    territory_model = apps.get_model("rights", "Territory")
    for code, name, identifier in TERRITORIES:
        territory_model.objects.get_or_create(
            code=code,
            defaults={"id": uuid.UUID(identifier), "name_nb": name},
        )


class Migration(migrations.Migration):
    dependencies = [("rights", "0001_initial")]
    operations = [migrations.RunPython(seed_territories, migrations.RunPython.noop)]
