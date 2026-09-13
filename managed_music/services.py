from django.db import transaction

from catalogue.models import ExternalIdentifier, Recording, RecordingContribution
from catalogue.services import find_recording_candidates, record_duplicate_candidates
from music_library.models import MusicLibraryEntry
from rights.models import RightsClaim, RightsConfiguration
from rights.services import create_rights_claim

from .models import ManagedRecording


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
        raise ValueError("Lokal organisasjon må konfigureres før forvaltning registreres.")
    if recording and new_recording_title.strip():
        raise ValueError(
            "Velg eksisterende innspilling eller opprett en ny, ikke begge."
        )
    if not recording and not new_recording_title.strip():
        raise ValueError("Velg en innspilling eller skriv inn tittel for en ny.")
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
        if new_isrc and any("samme ISRC" in match.signals for match in matches):
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
    library_entry, _ = MusicLibraryEntry.objects.get_or_create(recording=recording)
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
