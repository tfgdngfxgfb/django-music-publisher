"""Load deterministic, fictional catalogue data for local demonstrations."""

import csv
import hashlib
import json
import math
import shutil
import struct
import uuid
import wave
from pathlib import Path

from PIL import Image, ImageDraw
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from mutagen.flac import FLAC

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Label,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from managed_music.models import ManagedRecording
from media_assets.models import (
    FileAsset,
    FileLocation,
    RecordingMediaSelection,
)
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)
from parties.models import ArtistIdentity, Party
from provenance.models import (
    AssertionDecision,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from rights.models import (
    Agreement,
    AgreementDocument,
    AgreementParty,
    ClaimTerritory,
    RightsClaim,
    RightsConfiguration,
    Territory,
)
from rights.services import decide_rights_claim
from rights_core.models import VerificationStatus


def demo_uuid(number):
    """Return a visibly reserved UUID from the deterministic demo namespace."""
    return uuid.UUID(f"70000000-0000-4000-8000-{number:012d}")


class Command(BaseCommand):
    help = (
        "Opprett et fast, fiktivt demosett i den valgte utviklingsdatabasen."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--files-root",
            type=Path,
            default=Path(settings.BASE_DIR) / ".local" / "demo-nas",
            help="Rotmappe for de genererte demofilene.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo-data kan bare lastes når DEBUG=true.")

        root = Path(options["files_root"]).expanduser().resolve()
        paths = self._write_files(root)

        person, _ = Party.objects.get_or_create(
            pk=demo_uuid(1),
            defaults={
                "name": "Ingrid Solberg (demo)",
                "kind": Party.Kind.PERSON,
            },
        )
        group, _ = Party.objects.get_or_create(
            pk=demo_uuid(2),
            defaults={
                "name": "Nordlys Ensemble (demo)",
                "kind": Party.Kind.GROUP,
            },
        )
        organization, _ = Party.objects.get_or_create(
            pk=demo_uuid(3),
            defaults={
                "name": "Aurora Musikk AS (demo)",
                "kind": Party.Kind.ORGANIZATION,
            },
        )
        local_organization, _ = Party.objects.get_or_create(
            pk=demo_uuid(4),
            defaults={
                "name": "P7 Demoorganisasjon",
                "kind": Party.Kind.ORGANIZATION,
            },
        )
        RightsConfiguration.objects.get_or_create(
            singleton=True,
            defaults={
                "id": demo_uuid(5),
                "local_organization": local_organization,
            },
        )
        reviewer, created = get_user_model().objects.get_or_create(
            username="p7-demo-rights-reviewer",
            defaults={"is_active": False},
        )
        if created:
            reviewer.set_unusable_password()
            reviewer.save(update_fields=("password",))
        soloist, _ = ArtistIdentity.objects.get_or_create(
            pk=demo_uuid(10),
            defaults={"party": person, "display_name": "INGRID SOL (demo)"},
        )
        ensemble, _ = ArtistIdentity.objects.get_or_create(
            pk=demo_uuid(11),
            defaults={"party": group, "display_name": "NORDLYS (demo)"},
        )
        label, _ = Label.objects.get_or_create(
            pk=demo_uuid(20),
            defaults={
                "name": "Aurora Demo Records",
                "party": organization,
                "notes": "Fiktiv label opprettet av P7s demo-datasett.",
            },
        )

        recordings = []
        recording_specs = (
            (30, "Nordlys over byen (demo)", 213000, "nb"),
            (31, "Stille vann (demo)", 187000, "nb"),
            (32, "Morning Light (demo)", 201000, "en"),
            (
                33,
                "Nordlys over byen – alternativ metadata (demo)",
                214000,
                "nb",
            ),
            (34, "Ukjent opptak fra kassett (demo)", None, ""),
        )
        for number, title, duration, language in recording_specs:
            recording, _ = Recording.objects.get_or_create(
                pk=demo_uuid(number),
                defaults={
                    "title": title,
                    "duration_ms": duration,
                    "language": language,
                    "recording_kind": Recording.Kind.SOUND,
                },
            )
            recordings.append(recording)

        for number, recording, artist, party in (
            (40, recordings[0], ensemble, group),
            (41, recordings[1], soloist, person),
            (42, recordings[2], soloist, person),
            (43, recordings[3], ensemble, group),
        ):
            RecordingContribution.objects.get_or_create(
                pk=demo_uuid(number),
                defaults={
                    "recording": recording,
                    "party": party,
                    "artist_identity": artist,
                    "role": RecordingContribution.Role.PRIMARY,
                    "credited_as": artist.display_name,
                },
            )

        album, _ = Release.objects.get_or_create(
            pk=demo_uuid(50),
            defaults={
                "title": "Lys over fjorden (demo)",
                "release_type": Release.Type.CD,
                "release_year": 2024,
                "label": label,
                "catalogue_number": "P7-DEMO-001",
                "verification_status": VerificationStatus.CONFIRMED,
            },
        )
        single, _ = Release.objects.get_or_create(
            pk=demo_uuid(51),
            defaults={
                "title": "Nordlys over byen – single (demo)",
                "release_type": Release.Type.DIGITAL,
                "release_year": 2025,
                "label": label,
                "catalogue_number": "P7-DEMO-002",
            },
        )
        for number, release, recording, sequence, disc, side, track in (
            (60, album, recordings[0], 1, 1, "", 1),
            (61, album, recordings[1], 2, 1, "", 2),
            (62, album, recordings[2], 3, 1, "", 3),
            (63, single, recordings[0], 1, None, "A", 1),
        ):
            ReleaseTrack.objects.get_or_create(
                pk=demo_uuid(number),
                defaults={
                    "release": release,
                    "recording": recording,
                    "sequence_number": sequence,
                    "disc_number": disc,
                    "side": side,
                    "track_number": track,
                },
            )

        self._identifier(70, recordings[0], None, "ISRC", "NO-P7D-24-00001")
        self._identifier(71, recordings[1], None, "ISRC", "NO-P7D-24-00002")
        self._identifier(72, None, album, "EAN", "4006381333931")
        self._identifier(73, None, single, "UPC", "036000291452")

        library_entries = []
        library_specs = (
            (80, recordings[0], "Pop", "nb", ("P7 Riks", "P7 Ung"), 4),
            (81, recordings[1], "Visesang", "nb", ("P7 Riks",), 2),
            (82, recordings[2], "Pop", "en", ("P7 Ung",), 3),
            (83, recordings[4], "", "", (), None),
            (84, recordings[3], "Pop", "nb", ("P7 Riks",), 3),
        )
        channels = {
            name: Channel.objects.get_or_create(
                pk=demo_uuid(850 + offset),
                defaults={"code": code, "name": name},
            )[0]
            for offset, (code, name) in enumerate(
                (("p7_riks", "P7 Riks"), ("p7_ung", "P7 Ung"))
            )
        }
        audiences = {
            name: TargetAudience.objects.get_or_create(
                pk=demo_uuid(860 + offset),
                defaults={"code": code, "name": name},
            )[0]
            for offset, (code, name) in enumerate(
                (("ung_voksen", "Ung voksen"), ("voksen", "Voksen"))
            )
        }
        for (
            number,
            recording,
            genre,
            language,
            channel_names,
            energy,
        ) in library_specs:
            entry, _ = MusicLibraryEntry.objects.get_or_create(
                pk=demo_uuid(number),
                defaults={
                    "recording": recording,
                    "genre": genre,
                    "language": language,
                    "energy": energy,
                    "verification_status": (
                        VerificationStatus.CONFIRMED
                        if number != 83
                        else VerificationStatus.UNVERIFIED
                    ),
                    "notes": "Fiktive radiodata for prøvebruk.",
                },
            )
            MusicLibraryChannel.objects.filter(library_entry=entry).delete()
            for channel_name in channel_names:
                MusicLibraryChannel.objects.create(
                    library_entry=entry, channel=channels[channel_name]
                )
            MusicLibraryTargetAudience.objects.filter(
                library_entry=entry
            ).delete()
            if channel_names:
                for audience in audiences.values():
                    MusicLibraryTargetAudience.objects.create(
                        library_entry=entry, target_audience=audience
                    )
            library_entries.append(entry)
        entries_by_recording = {
            entry.recording_id: entry for entry in library_entries
        }
        for offset, recording in enumerate(recordings):
            ManagedRecording.objects.get_or_create(
                pk=demo_uuid(90 + offset),
                defaults={
                    "library_entry": entries_by_recording[recording.pk],
                    "status": ManagedRecording.Status.ACTIVE,
                    "notes": "Demopost. Angir forvaltning, ikke dokumentert eierskap.",
                },
            )

        physical, _ = SourceSystem.objects.get_or_create(
            pk=demo_uuid(100),
            defaults={
                "name": "Demo: CD-omslag",
                "kind": SourceSystem.Kind.PHYSICAL,
            },
        )
        imported, _ = SourceSystem.objects.get_or_create(
            pk=demo_uuid(101),
            defaults={
                "name": "Demo: ekstern katalog",
                "kind": SourceSystem.Kind.IMPORT,
            },
        )
        cover_source, _ = SourceRecord.objects.get_or_create(
            pk=demo_uuid(110),
            defaults={
                "source_system": physical,
                "source_locator": "Lys over fjorden, omslagets bakside",
                "raw_payload": {
                    "title": "Nordlys over byen",
                    "language": "Norsk",
                },
            },
        )
        import_source, _ = SourceRecord.objects.get_or_create(
            pk=demo_uuid(111),
            defaults={
                "source_system": imported,
                "external_record_id": "DEMO-IMPORT-001",
                "raw_payload": {"title": "Nordlys i byen", "language": "no"},
            },
        )
        confirmed = self._assertion(
            120,
            cover_source,
            recordings[0],
            "title",
            "Nordlys over byen",
            "Nordlys over byen",
        )
        disputed = self._assertion(
            121,
            import_source,
            recordings[0],
            "title",
            "Nordlys i byen",
            "Nordlys i byen",
        )
        self._decision(
            130,
            confirmed,
            VerificationStatus.CONFIRMED,
            "Kontrollert mot omslaget",
        )
        self._decision(
            131,
            disputed,
            VerificationStatus.DISPUTED,
            "Avviker fra fysisk omslag",
        )

        DuplicateCandidate.objects.get_or_create(
            pk=demo_uuid(140),
            defaults={
                "recording_a": recordings[0],
                "recording_b": recordings[3],
                "signals": ["lignende tittel", "samme artist", "nær varighet"],
                "score": 72,
                "notes": "Skal vurderes manuelt; ingen automatisk sammenslåing.",
            },
        )

        self._file_records(recordings[0], album, root, paths)
        self._rights_records(
            recordings,
            group,
            organization,
            local_organization,
            cover_source,
            import_source,
            reviewer,
        )
        self.stdout.write(
            self.style.SUCCESS("Det faste demo-datasettet er klart.")
        )
        self.stdout.write(f"Demofiler: {root}")
        self.stdout.write(
            "Kjør samme kommando igjen uten å opprette duplikater."
        )

    def _identifier(self, number, recording, release, scheme, value):
        ExternalIdentifier.objects.get_or_create(
            pk=demo_uuid(number),
            defaults={
                "recording": recording,
                "release": release,
                "scheme": scheme,
                "value": value,
            },
        )

    def _assertion(self, number, source, recording, field, raw, normalized):
        assertion, _ = MetadataAssertion.objects.get_or_create(
            pk=demo_uuid(number),
            defaults={
                "source_record": source,
                "entity_type": MetadataAssertion.EntityType.RECORDING,
                "entity_uuid": recording.pk,
                "field_name": field,
                "raw_value": raw,
                "normalized_value": normalized,
            },
        )
        return assertion

    def _decision(self, number, assertion, decision, note):
        _, created = AssertionDecision.objects.get_or_create(
            pk=demo_uuid(number),
            defaults={
                "assertion": assertion,
                "decision": decision,
                "note": note,
            },
        )
        if created and assertion.status != decision:
            assertion.status = decision
            assertion.save(update_fields=("status",))

    def _write_files(self, root):
        folders = {
            "cover": root / "P7-Demo" / "Aurora" / "P7-DEMO-001" / "Cover",
            "audio": root / "P7-Demo" / "Aurora" / "P7-DEMO-001" / "Audio",
            "metadata": root
            / "P7-Demo"
            / "Aurora"
            / "P7-DEMO-001"
            / "Metadata",
            "documents": root
            / "P7-Demo"
            / "Aurora"
            / "P7-DEMO-001"
            / "Documents",
        }
        for folder in folders.values():
            folder.mkdir(parents=True, exist_ok=True)

        cover = folders["cover"] / "p7-demo-cover.png"
        image = Image.new("RGB", (1200, 1200), "#082f3b")
        draw = ImageDraw.Draw(image)
        for radius, color in (
            (420, "#087f83"),
            (300, "#30b8ad"),
            (170, "#d9f4ef"),
        ):
            box = (600 - radius, 600 - radius, 600 + radius, 600 + radius)
            draw.ellipse(box, outline=color, width=24)
        draw.text((72, 72), "P7 DEMO", fill="white", stroke_width=1)
        draw.text((72, 1080), "LYS OVER FJORDEN", fill="white", stroke_width=1)
        image.save(cover, format="PNG", optimize=True)

        audio = folders["audio"] / "nordlys-demo-tone.wav"
        sample_rate = 44100
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            frames = bytearray()
            for index in range(sample_rate * 2):
                fade = min(index / 2205, (sample_rate * 2 - index) / 2205, 1)
                value = int(
                    6000
                    * fade
                    * math.sin(2 * math.pi * 440 * index / sample_rate)
                )
                frames.extend(struct.pack("<h", value))
            output.writeframes(frames)

        radio_flac = folders["audio"] / "nordlys-radio-demo.flac"
        shutil.copyfile(
            Path(settings.BASE_DIR)
            / "flac_ingest"
            / "test_fixtures"
            / "silence.flac",
            radio_flac,
        )
        flac = FLAC(radio_flac)
        flac["TITLE"] = "Tittel fra FLAC som avviker (demo)"
        flac["ARTIST"] = "NORDLYS (demo)"
        flac["ALBUM"] = "Lys over fjorden (demo)"
        flac["TRACKNUMBER"] = "1"
        flac["DISCNUMBER"] = "1"
        flac["ISRC"] = "NOP7D2400001"
        flac["COMPOSER"] = "Kari Nordmann (demo)"
        flac["LYRICIST"] = "Kari Nordmann (demo)"
        flac["GENRE"] = "Pop"
        flac["LANGUAGE"] = "nb"
        flac["RATING"] = "4"
        flac["KANAL"] = ["P7 Riks", "P7 Ung"]
        flac["TARGET"] = ["Ung voksen", "Voksen"]
        flac["P7UUID"] = str(demo_uuid(30))
        flac["P7_DEMO_UNKNOWN"] = "Bevares ved synkronisering"
        flac.save()

        json_path = folders["metadata"] / "catalogue-sample.json"
        json_path.write_text(
            json.dumps(
                {
                    "notice": "Fiktive P7-demodata",
                    "recording_uuid": str(demo_uuid(30)),
                    "title": "Nordlys over byen (demo)",
                    "artist": "NORDLYS (demo)",
                    "isrc": "NOP7D2400001",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        csv_path = folders["metadata"] / "catalogue-sample.csv"
        with csv_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("title", "artist", "isrc", "language", "genre"))
            writer.writerow(
                (
                    "Nordlys over byen (demo)",
                    "NORDLYS (demo)",
                    "NOP7D2400001",
                    "nb",
                    "Pop",
                )
            )
            writer.writerow(
                (
                    "Stille vann (demo)",
                    "INGRID SOL (demo)",
                    "NOP7D2400002",
                    "nb",
                    "Visesang",
                )
            )
        agreement = folders["documents"] / "demo-rights-agreement.txt"
        agreement.write_text(
            "P7 DEMO – IKKE EN VIRKELIG AVTALE\n\n"
            "Denne filen finnes bare for å teste kobling mellom avtale og dokumentasjon.\n",
            encoding="utf-8",
        )
        return {
            "cover": cover,
            "audio": audio,
            "radio_flac": radio_flac,
            "json": json_path,
            "csv": csv_path,
            "agreement": agreement,
        }

    def _file_records(self, recording, release, root, paths):
        assets = (
            (
                150,
                recording,
                None,
                paths["cover"],
                FileAsset.Role.COVER_IMAGE,
                "image/png",
            ),
            (
                151,
                recording,
                None,
                paths["audio"],
                FileAsset.Role.RAW_DIGITIZATION,
                "audio/wav",
            ),
            (
                152,
                None,
                release,
                paths["json"],
                FileAsset.Role.DOCUMENT,
                "application/json",
            ),
            (
                153,
                None,
                release,
                paths["csv"],
                FileAsset.Role.DOCUMENT,
                "text/csv",
            ),
            (
                154,
                None,
                None,
                paths["agreement"],
                FileAsset.Role.DOCUMENT,
                "text/plain",
            ),
            (
                155,
                recording,
                None,
                paths["radio_flac"],
                FileAsset.Role.RADIO_FLAC,
                "audio/flac",
            ),
        )
        for (
            offset,
            recording_target,
            release_target,
            path,
            role,
            mime,
        ) in assets:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            asset, _ = FileAsset.objects.get_or_create(
                pk=demo_uuid(offset),
                defaults={
                    "recording": recording_target,
                    "release": release_target,
                    "filename": path.name,
                    "mime_type": mime,
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                    "role": role,
                    "technical_metadata": (
                        {
                            "sample_rate": 44100,
                            "bits_per_sample": 16,
                            "demo_tone": True,
                        }
                        if mime == "audio/wav"
                        else (
                            {
                                "sample_rate": 44100,
                                "bits_per_sample": 16,
                                "channels": 1,
                                "demo_silence": True,
                            }
                            if mime == "audio/flac"
                            else {"demo_file": True}
                        )
                    ),
                    "sync_status": (
                        FileAsset.SyncStatus.CONFLICT
                        if role == FileAsset.Role.RADIO_FLAC
                        else FileAsset.SyncStatus.NOT_APPLICABLE
                    ),
                    "sync_error": (
                        "Demofilens TITLE avviker med hensikt fra katalogtittelen."
                        if role == FileAsset.Role.RADIO_FLAC
                        else ""
                    ),
                },
            )
            relative = path.relative_to(root).as_posix()
            FileLocation.objects.get_or_create(
                pk=demo_uuid(offset + 20),
                defaults={
                    "asset": asset,
                    "storage_type": FileLocation.StorageType.NAS,
                    "relative_path": relative,
                    "status": FileLocation.Status.ACTIVE,
                    "verification_status": FileLocation.VerificationStatus.VERIFIED,
                },
            )
            if role == FileAsset.Role.RADIO_FLAC:
                selection, _ = RecordingMediaSelection.objects.get_or_create(
                    recording=recording_target
                )
                if selection.current_radio_id is None:
                    current = list(
                        FileAsset.objects.filter(
                            recording=recording_target,
                            role=FileAsset.Role.RADIO_FLAC,
                            lifecycle_status=FileAsset.LifecycleStatus.CURRENT,
                        )[:2]
                    )
                    if not current:
                        asset.lifecycle_status = FileAsset.LifecycleStatus.CURRENT
                        asset.save(update_fields=("lifecycle_status",))
                        current = [asset]
                    if len(current) == 1:
                        selection.current_radio = current[0]
                        selection.save(update_fields=("current_radio",))

    def _rights_records(
        self,
        recordings,
        artist_group,
        catalogue_company,
        local_organization,
        physical_source,
        imported_source,
        reviewer,
    ):
        recording = recordings[0]
        agreement, _ = Agreement.objects.get_or_create(
            pk=demo_uuid(200),
            defaults={
                "title": "Demoavtale for Nordlys over byen",
                "internal_reference": "RIGHTS-DEMO-001",
                "agreement_type": Agreement.Type.LICENSE,
                "status": Agreement.Status.DRAFT,
                "notes": "Fiktiv avtale. Skal aldri brukes som rettighetsbevis.",
            },
        )
        AgreementParty.objects.get_or_create(
            pk=demo_uuid(201),
            defaults={
                "agreement": agreement,
                "party": artist_group,
                "role": AgreementParty.Role.LICENSOR,
            },
        )
        AgreementParty.objects.get_or_create(
            pk=demo_uuid(202),
            defaults={
                "agreement": agreement,
                "party": local_organization,
                "role": AgreementParty.Role.LICENSEE,
            },
        )
        AgreementDocument.objects.get_or_create(
            pk=demo_uuid(203),
            defaults={
                "agreement": agreement,
                "file_asset": FileAsset.objects.get(pk=demo_uuid(154)),
                "description": "Fiktiv testavtale",
            },
        )
        self._claim(
            210,
            recording,
            RightsClaim.RightType.OWNERSHIP,
            catalogue_company,
            share="60.00",
            source=physical_source,
            agreement=agreement,
        )
        self._claim(
            230,
            recordings[0],
            RightsClaim.RightType.OWNERSHIP,
            local_organization,
            share="100.00",
            source=physical_source,
            agreement=agreement,
            evidence_strength=RightsClaim.EvidenceStrength.DOCUMENTED,
            decision=VerificationStatus.CONFIRMED,
            reviewer=reviewer,
        )
        self._claim(
            231,
            recordings[1],
            RightsClaim.RightType.OWNERSHIP,
            local_organization,
            share="40.00",
            source=physical_source,
            evidence_strength=RightsClaim.EvidenceStrength.STRONG,
            decision=VerificationStatus.CONFIRMED,
            reviewer=reviewer,
        )
        self._claim(
            232,
            recordings[2],
            RightsClaim.RightType.OWNERSHIP,
            catalogue_company,
            share="100.00",
            source=physical_source,
            evidence_strength=RightsClaim.EvidenceStrength.DOCUMENTED,
            decision=VerificationStatus.CONFIRMED,
            reviewer=reviewer,
        )
        self._claim(
            233,
            recordings[2],
            RightsClaim.RightType.ADMINISTRATION,
            local_organization,
            grantor=catalogue_company,
            source=physical_source,
            evidence_strength=RightsClaim.EvidenceStrength.STRONG,
            decision=VerificationStatus.CONFIRMED,
            reviewer=reviewer,
        )
        self._claim(
            234,
            recordings[2],
            RightsClaim.RightType.DISTRIBUTION,
            local_organization,
            grantor=catalogue_company,
            source=imported_source,
            evidence_strength=RightsClaim.EvidenceStrength.PROBABLE,
            decision=VerificationStatus.CONFIRMED,
            reviewer=reviewer,
        )
        self._claim(
            235,
            recordings[4],
            RightsClaim.RightType.OWNERSHIP,
            artist_group,
            share="100.00",
            source=imported_source,
            evidence_strength=RightsClaim.EvidenceStrength.WEAK,
            decision=VerificationStatus.DISPUTED,
            reviewer=reviewer,
        )
        for number, managed_recording in (
            (236, recordings[3]),
            (237, recordings[4]),
        ):
            self._claim(
                number,
                managed_recording,
                RightsClaim.RightType.DISTRIBUTION,
                local_organization,
                source=physical_source,
                evidence_strength=RightsClaim.EvidenceStrength.STRONG,
                decision=VerificationStatus.CONFIRMED,
                reviewer=reviewer,
            )
        self._claim(
            211,
            recording,
            RightsClaim.RightType.OWNERSHIP,
            artist_group,
            share="40.00",
            source=imported_source,
        )
        self._claim(
            212,
            recording,
            RightsClaim.RightType.ADMINISTRATION,
            local_organization,
            grantor=artist_group,
            source=physical_source,
            agreement=agreement,
        )
        self._claim(
            213,
            recording,
            RightsClaim.RightType.DISTRIBUTION,
            local_organization,
            grantor=artist_group,
            territory_mode=RightsClaim.TerritoryMode.INCLUDE,
            territories=Territory.objects.filter(code__in=("NO", "SE", "DK")),
            source=physical_source,
            agreement=agreement,
        )

    def _claim(
        self,
        number,
        recording,
        right_type,
        holder,
        *,
        share=None,
        grantor=None,
        territory_mode=RightsClaim.TerritoryMode.WORLD,
        territories=(),
        source=None,
        agreement=None,
        evidence_strength=RightsClaim.EvidenceStrength.NOT_ASSESSED,
        decision=None,
        reviewer=None,
    ):
        claim, _ = RightsClaim.objects.get_or_create(
            pk=demo_uuid(number),
            defaults={
                "recording": recording,
                "right_type": right_type,
                "rights_holder": holder,
                "grantor": grantor,
                "share": share,
                "territory_mode": territory_mode,
                "source_record": source,
                "agreement": agreement,
                "evidence_strength": evidence_strength,
                "notes": "Uverifisert, fiktivt rettighetskrav for prøvebruk.",
            },
        )
        for index, territory in enumerate(territories):
            ClaimTerritory.objects.get_or_create(
                pk=demo_uuid(220 + number - 210 + index * 10),
                defaults={"claim": claim, "territory": territory},
            )
        if decision and claim.status == VerificationStatus.UNVERIFIED:
            decide_rights_claim(
                claim,
                decision,
                user=reviewer,
                note="Fiktiv demobeslutning for prøvebruk.",
            )
