from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
)
from catalogue.services import (
    find_recording_candidates,
    record_duplicate_candidates,
)
from music_library.models import MusicLibraryEntry
from provenance.models import SourceRecord, SourceSystem
from rights.models import RightsClaim, RightsConfiguration
from rights.services import create_rights_claim

from .models import ManagedRecording, ManagedRelease
from .lifecycle import management_state


@transaction.atomic
def save_managed_release(
    *, release, status, relationship, source_system=None, notes=""
):
    """Save catalogue management without creating recording rights."""
    managed = (
        ManagedRelease.objects.select_for_update()
        .filter(release=release)
        .first()
    )
    if managed is None:
        return ManagedRelease.objects.create(
            release=release,
            status=status,
            relationship=relationship,
            source_system=source_system,
            notes=notes,
        )
    managed.status = status
    managed.relationship = relationship
    managed.source_system = source_system
    managed.notes = notes
    managed.save()
    return managed


@transaction.atomic
def create_managed_recording(
    *,
    recording=None,
    new_recording_title="",
    new_isrc="",
    artist_identity=None,
    force_create=False,
    relationship_type=None,
    ownership_share=None,
    source_system=None,
    notes="",
):
    if relationship_type not in RightsClaim.RightType.values:
        raise ValueError(
            "Angi om lokal organisasjon eier, administrerer eller distribuerer."
        )
    if relationship_type == RightsClaim.RightType.OWNERSHIP:
        if ownership_share is None:
            raise ValueError("Angi lokal organisasjons eierandel.")
    elif ownership_share is not None:
        raise ValueError("Andel brukes bare for mastereierskap.")
    configuration = RightsConfiguration.objects.select_related(
        "local_organization"
    ).first()
    if not configuration:
        raise ValueError(
            "Lokal organisasjon må konfigureres før forvaltning registreres."
        )
    if recording and new_recording_title.strip():
        raise ValueError(
            "Velg eksisterende innspilling eller opprett en ny, ikke begge."
        )
    if not recording and not new_recording_title.strip():
        raise ValueError(
            "Velg en innspilling eller skriv inn tittel for en ny."
        )
    matches = []
    if not recording:
        matches = find_recording_candidates(
            title=new_recording_title,
            isrc=new_isrc,
            artist_identity=artist_identity,
        )
        if matches and not force_create:
            names = ", ".join(
                f"{match.recording.title} ({match.recording.pk})"
                for match in matches[:5]
            )
            raise ValueError(f"Mulig eksisterende innspilling: {names}")
        if new_isrc and any(
            "samme ISRC" in match.signals for match in matches
        ):
            raise ValueError(
                "ISRC finnes allerede. Opprett eventuelt den nye innspillingen uten ISRC, "
                "og registrer kodekonflikten som en metadatapåstand."
            )
        recording = Recording.objects.create(title=new_recording_title.strip())
        if new_isrc:
            ExternalIdentifier.objects.create(
                recording=recording,
                scheme=ExternalIdentifier.Scheme.ISRC,
                value=new_isrc,
            )
        if artist_identity:
            RecordingContribution.objects.create(
                recording=recording,
                party=artist_identity.party,
                artist_identity=artist_identity,
                role=RecordingContribution.Role.PRIMARY,
                credited_as=artist_identity.display_name,
            )
        record_duplicate_candidates(recording, matches)
    Recording.objects.select_for_update().get(pk=recording.pk)
    library_entry, _ = MusicLibraryEntry.objects.get_or_create(
        recording=recording
    )
    managed = ManagedRecording.objects.create(
        library_entry=library_entry,
        status=ManagedRecording.Status.PENDING,
        source_system=source_system,
        notes=notes,
    )
    create_rights_claim(
        recording=recording,
        right_type=relationship_type,
        rights_holder=configuration.local_organization,
        share=(
            ownership_share
            if relationship_type == RightsClaim.RightType.OWNERSHIP
            else None
        ),
        territory_mode=RightsClaim.TerritoryMode.WORLD,
        notes=(
            "Opprettet som uttrykkelig grunnlag ved registrering i Forvaltet musikk. "
            "Kravet må vurderes og bekreftes separat."
        ),
    )
    return managed


@transaction.atomic
def return_to_music_library(managed, *, user, reason):
    """Explicitly close rejected/abandoned onboarding; retain all source history.

    No current, plausible or historical local basis may remain. This service is
    deliberately not exposed as a GUI action in phase 6A.
    """
    if not user.has_perm("managed_music.delete_managedrecording"):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Begrunn tilbakeføringen til Musikkarkivet.")
    recording_id = ManagedRecording.objects.values_list(
        "library_entry__recording_id", flat=True
    ).get(pk=managed.pk)
    Recording.objects.select_for_update().get(pk=recording_id)
    managed = ManagedRecording.objects.select_for_update().get(pk=managed.pk)
    state = management_state(managed)
    if not state.can_return_to_music_library:
        raise ValidationError(
            "Bare onboarding uten gjenstående P7-grunnlag og uten tidligere "
            "aktiv forvaltning kan tilbakeføres."
        )
    source_system, _ = SourceSystem.objects.get_or_create(
        name="P7 forvaltningshistorikk",
        defaults={"kind": SourceSystem.Kind.MANUAL},
    )
    audit = SourceRecord.objects.create(
        source_system=source_system,
        source_locator=f"Recording {recording_id}",
        raw_payload={
            "action": "return_to_music_library",
            "recording_id": str(recording_id),
            "library_entry_id": str(managed.library_entry_id),
            "performed_by_id": str(user.pk),
            "reason": reason.strip(),
            "managed_recording": {
                "id": str(managed.pk),
                "status": managed.status,
                "source_system_id": (
                    str(managed.source_system_id)
                    if managed.source_system_id
                    else None
                ),
                "notes": managed.notes,
                "revision": managed.revision,
                "created_at": managed.created_at.isoformat(),
                "updated_at": managed.updated_at.isoformat(),
            },
        },
    )
    managed.delete()
    return audit
