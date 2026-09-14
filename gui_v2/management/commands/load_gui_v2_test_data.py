from django.core.management.base import BaseCommand

from catalogue.models import ExternalIdentifier, Label, Recording, RecordingContribution, Release, ReleaseTrack
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from parties.models import ArtistIdentity, Party
from rights_core.models import VerificationStatus


DATA = (
    ("Nordlys over byen", "De Nordiske", "Pop", "nb", 4, "female"),
    ("Mellom fjell", "Ingrid Eksempel", "Viser", "nb", 2, "female"),
    ("Stille havn", "Signalverket", "Pop", "no", 3, "group"),
    ("Fjerne lys", "Atlas Nord", "Elektronisk", "en", 5, "instrumental"),
    ("Spor i regnet", "Mina Vale", "Pop", "nb", 3, "female"),
    ("Glass og stein", "Kystlinje", "Rock", "no", 4, "group"),
    ("Etter midnatt", "Lumen", "Elektronisk", "en", 5, "male"),
    ("Nærmere", "Mina Vale", "Viser", "nb", 2, "female"),
    ("Vintervei", "De Nordiske", "Pop", "nb", 3, "mixed"),
    ("Open Skies", "Atlas Nord", "Pop", "en", 4, "male"),
    ("Morgenklang", "Signalverket", "Instrumental", "", 2, "instrumental"),
    ("Bak horisonten", "Kystlinje", "Rock", "nb", 4, "group"),
)


class Command(BaseCommand):
    help = "Laster deterministiske, oppdiktede data i den isolerte GUI-v2-testdatabasen."

    def handle(self, *args, **options):
        riks, _ = Channel.objects.get_or_create(code="p7-riks", defaults={"name": "P7 Riks"})
        ung, _ = Channel.objects.get_or_create(code="p7-ung", defaults={"name": "P7 Ung"})
        voksen, _ = TargetAudience.objects.get_or_create(code="voksen", defaults={"name": "Voksen"})
        ung_voksen, _ = TargetAudience.objects.get_or_create(code="ung-voksen", defaults={"name": "Ung voksen"})
        label, _ = Label.objects.get_or_create(name="Nordlys Demo")
        release_a, _ = Release.objects.get_or_create(
            title="Nordlysarkivet", catalogue_number="DEMO-001",
            defaults={"release_type": Release.Type.LP, "release_year": 2026, "label": label, "verification_status": VerificationStatus.CONFIRMED},
        )
        release_b, _ = Release.objects.get_or_create(
            title="Signaler fra nord", catalogue_number="DEMO-002",
            defaults={"release_type": Release.Type.CD, "release_year": 2025, "label": label, "verification_status": VerificationStatus.CONFIRMED},
        )
        releases = (release_a, release_b)
        for index, (title, artist_name, genre, language, energy, gender) in enumerate(DATA, start=1):
            party, _ = Party.objects.get_or_create(name=artist_name, kind=Party.Kind.GROUP)
            identity, _ = ArtistIdentity.objects.get_or_create(party=party, display_name=artist_name)
            recording, _ = Recording.objects.get_or_create(title=title, defaults={"duration_ms": (155 + index * 7) * 1000, "metadata_status": Recording.Status.REVIEWED})
            RecordingContribution.objects.get_or_create(
                recording=recording, role=RecordingContribution.Role.PRIMARY,
                defaults={"party": party, "artist_identity": identity, "credited_as": artist_name},
            )
            ExternalIdentifier.objects.get_or_create(
                scheme=ExternalIdentifier.Scheme.ISRC, namespace="", normalized_value=f"NOZ9A26{index:05d}",
                defaults={"recording": recording, "value": f"NO-Z9A-26-{index:05d}"},
            )
            entry, _ = MusicLibraryEntry.objects.get_or_create(
                recording=recording,
                defaults={"genre": genre, "language": language, "energy": energy, "gender": gender, "verification_status": VerificationStatus.CONFIRMED},
            )
            for channel in (riks, *([ung] if index % 3 == 0 else [])):
                MusicLibraryChannel.objects.get_or_create(library_entry=entry, channel=channel)
            for audience in (voksen, *([ung_voksen] if index % 2 == 0 else [])):
                MusicLibraryTargetAudience.objects.get_or_create(
                    library_entry=entry, target_audience=audience
                )
            if index in {1, 2, 5}:
                ManagedRecording.objects.get_or_create(library_entry=entry, defaults={"status": ManagedRecording.Status.ACTIVE})
            release = releases[(index - 1) // 6]
            track, _ = ReleaseTrack.objects.get_or_create(
                release=release, sequence_number=((index - 1) % 6) + 1,
                defaults={"recording": recording, "disc_number": 1, "side": "A" if index % 6 < 3 else "B", "track_number": ((index - 1) % 3) + 1, "duration_ms": recording.duration_ms},
            )
            asset, _ = FileAsset.objects.get_or_create(
                recording=recording, filename=f"{index:02d}-{title.lower().replace(' ', '-')}.flac",
                defaults={"role": FileAsset.Role.RADIO_FLAC, "mime_type": "audio/flac", "size_bytes": 1000000 + index, "sync_status": FileAsset.SyncStatus.SYNCED},
            )
            FileLocation.objects.get_or_create(
                storage_type=FileLocation.StorageType.LOCAL,
                relative_path=f"gui-v2-demo/{release.catalogue_number}/{asset.filename}",
                defaults={"asset": asset, "status": FileLocation.Status.ACTIVE, "is_current": True},
            )
            if not asset.release_track_id:
                asset.release_track = track
                asset.save()
        self.stdout.write(self.style.SUCCESS("GUI v2-testdata er klare: 12 oppdiktede innspillinger og 2 utgivelser."))
