"""Object-scoped catalogue protection, independent of radio and file metadata."""

from dataclasses import dataclass

from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from managed_music.models import ManagedRecording, ManagedRelease
from rights.models import RightsClaim, RightsConfiguration
from rights_core.models import VerificationStatus

from .models import Recording, Release, ReleaseTrack


def annotate_recording_authority(queryset, *, recording_ref="pk"):
    """Works for Recording, library-entry and file querysets without per-row reads."""
    today = timezone.localdate()
    return queryset.annotate(
        authority_managed=Exists(
            ManagedRecording.objects.filter(
                library_entry__recording_id=OuterRef(recording_ref)
            )
        ),
        authority_via_release=Exists(
            ReleaseTrack.objects.filter(
                recording_id=OuterRef(recording_ref),
                release__managed_release__isnull=False,
            )
        ),
        # Preserve the pre-6B safety rule for confirmed local ownership outside
        # explicit membership; do not silently discard existing catalogue authority.
        authority_local_ownership=Exists(
            RightsClaim.objects.filter(
                recording_id=OuterRef(recording_ref),
                rights_holder_id=Subquery(
                    RightsConfiguration.objects.values(
                        "local_organization_id"
                    )[:1]
                ),
                right_type=RightsClaim.RightType.OWNERSHIP,
                status=VerificationStatus.CONFIRMED,
            ).filter(
                Q(valid_from__isnull=True) | Q(valid_from__lte=today),
                Q(valid_until__isnull=True) | Q(valid_until__gte=today),
            )
        ),
    ).annotate(
        authority_release_only=Q(
            authority_via_release=True, authority_managed=False
        )
    )


@dataclass(frozen=True)
class RecordingAuthority:
    managed: bool
    via_release: bool
    local_ownership: bool

    @property
    def managed_release_only(self):
        return self.via_release and not self.managed

    @property
    def writeback(self):
        return self.managed or self.local_ownership

    @property
    def protected(self):
        return self.writeback or self.via_release


def recording_authority(recording):
    values = (
        annotate_recording_authority(Recording.objects.filter(pk=recording.pk))
        .values_list(
            "authority_managed",
            "authority_via_release",
            "authority_local_ownership",
        )
        .get()
    )
    return RecordingAuthority(*values)


def release_is_protected(release):
    return bool(
        release
        and ManagedRelease.objects.filter(release_id=release.pk).exists()
    )


def protecting_releases(recording):
    return (
        Release.objects.filter(
            tracks__recording_id=recording.pk, managed_release__isnull=False
        )
        .select_related("managed_release")
        .distinct()
    )


def annotate_file_authority(queryset):
    return queryset.annotate(
        on_managed_release_track=Exists(
            ReleaseTrack.objects.filter(
                pk=OuterRef("release_track_id"),
                release__managed_release__isnull=False,
            )
        )
    )


def file_release_is_protected(asset):
    return bool(
        asset.release_track_id
        and release_is_protected(asset.release_track.release)
    )
