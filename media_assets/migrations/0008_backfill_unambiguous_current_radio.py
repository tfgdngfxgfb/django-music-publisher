from django.db import migrations


def backfill_unambiguous_current_radio(apps, schema_editor):
    FileAsset = apps.get_model("media_assets", "FileAsset")
    FileLocation = apps.get_model("media_assets", "FileLocation")
    RecordingMediaSelection = apps.get_model(
        "media_assets", "RecordingMediaSelection"
    )

    recording_ids = (
        FileAsset.objects.filter(
            role="radio_flac",
            recording__isnull=False,
        )
        .values_list("recording_id", flat=True)
        .distinct()
    )
    for recording_id in recording_ids.iterator():
        selection = RecordingMediaSelection.objects.filter(
            recording_id=recording_id
        ).first()
        if selection and selection.current_radio_id:
            continue

        assets = FileAsset.objects.filter(
            recording_id=recording_id,
            role="radio_flac",
        ).exclude(lifecycle_status__in=("candidate", "historical"))
        candidates = []
        location_is_ambiguous = False
        for asset in assets:
            if asset.sync_status in ("missing", "failed"):
                continue
            locations = list(
                FileLocation.objects.filter(
                    asset_id=asset.pk,
                    is_current=True,
                    status="active",
                    storage_type__in=("nas", "local"),
                ).values_list("pk", flat=True)[:2]
            )
            if len(locations) == 1:
                candidates.append(asset)
            elif len(locations) > 1:
                location_is_ambiguous = True

        if location_is_ambiguous or len(candidates) != 1:
            continue

        asset = candidates[0]
        other_current_exists = (
            FileAsset.objects.filter(
                recording_id=recording_id,
                role="radio_flac",
                lifecycle_status="current",
            )
            .exclude(pk=asset.pk)
            .exists()
        )
        if other_current_exists:
            continue
        FileAsset.objects.filter(pk=asset.pk).update(
            lifecycle_status="current"
        )
        if selection:
            RecordingMediaSelection.objects.filter(pk=selection.pk).update(
                current_radio_id=asset.pk
            )
        else:
            RecordingMediaSelection.objects.create(
                recording_id=recording_id,
                current_radio_id=asset.pk,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("media_assets", "0007_first_radio_metadata_confirmation"),
    ]

    operations = [
        migrations.RunPython(
            backfill_unambiguous_current_radio,
            migrations.RunPython.noop,
        ),
    ]
