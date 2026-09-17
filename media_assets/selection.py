from django.db import transaction
from django.db.models import Prefetch

from catalogue.models import Recording

from .models import FileAsset, RecordingMediaSelection
from .playback import RadioPlaybackStatus, resolve_current_radio_asset


@transaction.atomic
def establish_current_radio_if_unambiguous(recording_id):
    """Promote one unambiguous legacy radio file without replacing a choice."""
    recording = (
        Recording.objects.select_for_update()
        .prefetch_related(
            Prefetch(
                "file_assets",
                queryset=FileAsset.objects.select_for_update().prefetch_related(
                    "locations"
                ),
            )
        )
        .get(pk=recording_id)
    )
    (
        selection,
        _,
    ) = RecordingMediaSelection.objects.select_for_update().get_or_create(
        recording=recording
    )
    if selection.current_radio_id:
        return selection.current_radio, False

    resolution = resolve_current_radio_asset(recording)
    if resolution.status != RadioPlaybackStatus.AVAILABLE:
        return None, False

    asset = resolution.asset
    current_ids = {
        item.pk
        for item in recording.file_assets.all()
        if item.lifecycle_status == FileAsset.LifecycleStatus.CURRENT
    }
    if current_ids and current_ids != {asset.pk}:
        return None, False

    if asset.lifecycle_status != FileAsset.LifecycleStatus.CURRENT:
        asset.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
        asset.save(update_fields=("lifecycle_status",))
    selection.current_radio = asset
    selection.save(update_fields=("current_radio",))
    return asset, True
