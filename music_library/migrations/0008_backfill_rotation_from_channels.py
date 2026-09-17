"""Align stored rotation with existing channel assignments without reading audio files."""

from django.db import migrations
from django.db.models import Exists, OuterRef


def backfill_rotation_from_channels(apps, schema_editor):
    entry_model = apps.get_model("music_library", "MusicLibraryEntry")
    link_model = apps.get_model("music_library", "MusicLibraryChannel")
    database = schema_editor.connection.alias
    assigned_channels = link_model.objects.using(database).filter(
        library_entry_id=OuterRef("pk")
    )
    entries = (
        entry_model.objects.using(database)
        .filter(rotation_suitability="")
        .filter(Exists(assigned_channels))
    )
    for entry in entries.iterator(chunk_size=500):
        entry.rotation_suitability = "suitable"
        entry.revision += 1
        entry.save(
            using=database,
            update_fields=("rotation_suitability", "revision", "updated_at"),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("music_library", "0007_alter_musiclibraryentry_rotation_suitability")
    ]

    operations = [
        migrations.RunPython(backfill_rotation_from_channels, migrations.RunPython.noop)
    ]
