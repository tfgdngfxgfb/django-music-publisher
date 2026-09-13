"""Create a deterministic operational catalogue used only by the smoke test."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalogue.models import ExternalIdentifier, Label, Recording, Release
from catalogue.services import create_release_track
from managed_music.services import create_managed_recording
from media_assets.models import FileAsset, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from parties.models import ArtistIdentity, Party
from provenance.models import MetadataAssertion, SourceRecord, SourceSystem
from provenance.services import decide_assertion
from rights_core.models import VerificationStatus


class Command(BaseCommand):
    help = "Opprett fase-2-data for isolert smoke-test."

    @transaction.atomic
    def handle(self, *args, **options):
        if os.getenv("P7_ALLOW_SMOKE_DATA") != "true":
            raise CommandError(
                "Smoke-data kan bare opprettes av den isolerte testkjøringen."
            )
        party = Party.objects.create(name="Kari Nordmann", kind=Party.Kind.PERSON)
        artist = ArtistIdentity.objects.create(party=party, display_name="KARI N")
        label = Label.objects.create(name="Lynor", party=None)
        first_release = Release.objects.create(
            title="Operativ LP",
            release_type=Release.Type.LP,
            label=label,
            catalogue_number="LYNOR123",
            release_year=1978,
        )
        first_track = create_release_track(
            release=first_release,
            sequence_number=1,
            side="A",
            track_number=1,
            new_recording_title="Operativ master",
            new_isrc="NO-P7A-26-00001",
            artist_identity=artist,
            duration_ms=183000,
        )
        second_release = Release.objects.create(
            title="Operativ CD", release_type=Release.Type.CD
        )
        create_release_track(
            release=second_release,
            sequence_number=1,
            disc_number=1,
            track_number=1,
            recording=first_track.recording,
        )
        ExternalIdentifier.objects.create(
            release=first_release,
            scheme=ExternalIdentifier.Scheme.EAN,
            value="4006381333931",
        )
        library_entry = MusicLibraryEntry.objects.create(
            recording=first_track.recording,
            genre="Pop",
            language="nb",
            gender=MusicLibraryEntry.Gender.FEMALE,
            energy=4,
            verification_status=VerificationStatus.CONFIRMED,
        )
        channel = Channel.objects.create(code="p7_riks", name="P7 Riks")
        audience = TargetAudience.objects.create(code="familie", name="Familie")
        MusicLibraryChannel.objects.create(
            library_entry=library_entry, channel=channel
        )
        MusicLibraryTargetAudience.objects.create(
            library_entry=library_entry, target_audience=audience
        )
        archive_only = Recording.objects.create(title="Kun i Musikkarkivet")
        MusicLibraryEntry.objects.create(
            recording=archive_only, genre="Salme", language="nb"
        )
        managed = create_managed_recording(
            recording=first_track.recording,
            status="active",
            notes="Uttrykkelig adminhandling",
        )
        if managed.library_entry_id != library_entry.pk:
            raise RuntimeError("Forvaltet musikk gjenbrukte ikke Musikkarkivet")
        if hasattr(archive_only.music_library_entry, "managed_recording"):
            raise RuntimeError("Arkivpost ble feilaktig forvaltet")

        cover = SourceSystem.objects.create(
            name="LP-cover", kind=SourceSystem.Kind.PHYSICAL
        )
        orchard = SourceSystem.objects.create(
            name="Orchard testkilde", kind=SourceSystem.Kind.IMPORT
        )
        cover_record = SourceRecord.objects.create(
            source_system=cover,
            source_locator="Operativ LP-cover",
            raw_payload={"year": "1978"},
        )
        orchard_record = SourceRecord.objects.create(
            source_system=orchard,
            external_record_id="ORCHARD-TEST-1",
            raw_payload={"year": "1979"},
        )
        confirmed = MetadataAssertion.objects.create(
            source_record=cover_record,
            entity_type=MetadataAssertion.EntityType.RELEASE,
            entity_uuid=first_release.pk,
            field_name="release_year",
            raw_value="1978",
            normalized_value=1978,
        )
        disputed = MetadataAssertion.objects.create(
            source_record=orchard_record,
            entity_type=MetadataAssertion.EntityType.RELEASE,
            entity_uuid=first_release.pk,
            field_name="release_year",
            raw_value="1979",
            normalized_value=1979,
        )
        decide_assertion(
            confirmed, VerificationStatus.CONFIRMED, note="Kontrollert mot cover"
        )
        decide_assertion(
            disputed, VerificationStatus.DISPUTED, note="Avviker fra cover"
        )

        asset = FileAsset.objects.create(
            recording=first_track.recording,
            filename="track01.flac",
            mime_type="audio/flac",
            size_bytes=12345678,
            role=FileAsset.Role.RADIO_FLAC,
            technical_metadata={"sample_rate": 48000, "bits_per_sample": 24},
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Mudi/Lynor/LYNOR123/Audio/track01.flac",
        )

        # Explicitly create a separate possible duplicate; no merge is performed.
        create_release_track(
            release=second_release,
            sequence_number=2,
            new_recording_title="Operativ master",
            duration_ms=183000,
            force_create=True,
        )
        self.stdout.write(self.style.SUCCESS(str(first_track.recording.pk)))
