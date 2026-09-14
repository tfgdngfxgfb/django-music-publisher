from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from managed_music.models import ManagedRecording
from rights.models import RightsClaim, RightsDecision


_automatic_sync_suppressed = ContextVar(
    "automatic_flac_sync_suppressed", default=False
)


@contextmanager
def suppress_automatic_flac_sync():
    """Prevent prototype writes from queuing or touching radio files."""
    token = _automatic_sync_suppressed.set(True)
    try:
        yield
    finally:
        _automatic_sync_suppressed.reset(token)


def _mark(recording_id):
    if not recording_id or _automatic_sync_suppressed.get():
        return
    from .services import mark_recording_for_sync

    mark_recording_for_sync(recording_id)


@receiver(post_save, sender=Recording)
def recording_changed(sender, instance, created, **kwargs):
    if not created:
        _mark(instance.pk)


@receiver((post_save, post_delete), sender=RecordingContribution)
def contribution_changed(sender, instance, **kwargs):
    _mark(instance.recording_id)


@receiver((post_save, post_delete), sender=ExternalIdentifier)
def identifier_changed(sender, instance, **kwargs):
    _mark(instance.recording_id)


@receiver(post_save, sender=Release)
def release_changed(sender, instance, created, **kwargs):
    if not created:
        for recording_id in instance.tracks.values_list("recording_id", flat=True):
            _mark(recording_id)


@receiver((post_save, post_delete), sender=ReleaseTrack)
def release_track_changed(sender, instance, **kwargs):
    _mark(instance.recording_id)


@receiver(post_save, sender=ManagedRecording)
def managed_recording_changed(sender, instance, **kwargs):
    _mark(instance.library_entry.recording_id)


@receiver(post_save, sender=RightsDecision)
def rights_decision_changed(sender, instance, **kwargs):
    _mark(instance.claim.recording_id)


@receiver(post_save, sender=RightsClaim)
def rights_claim_changed(sender, instance, **kwargs):
    _mark(instance.recording_id)
