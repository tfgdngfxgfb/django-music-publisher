from contextlib import nullcontext

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from catalogue.models import ExternalIdentifier, Recording, RecordingContribution
from catalogue.services import create_release_track, resolve_release_track_recording
from flac_ingest.signals import suppress_automatic_flac_sync
from parties.models import ArtistIdentity


def _names(value):
    return [part.strip() for part in (value or "").replace("\n", ";").split(";") if part.strip()]


def _replace_credits(recording, role, names):
    RecordingContribution.objects.filter(recording=recording, role=role).delete()
    for index, name in enumerate(_names(names)):
        identity = ArtistIdentity.objects.filter(display_name__iexact=name).select_related("party").first()
        RecordingContribution.objects.create(
            recording=recording,
            party=identity.party if identity else None,
            artist_identity=identity,
            role=role,
            credited_as=name,
            display_order=index,
        )


def _set_isrc(recording, value):
    current = recording.identifiers.filter(scheme=ExternalIdentifier.Scheme.ISRC).first()
    if not value:
        if current:
            current.delete()
        return
    if current:
        current.value = value
        current.full_clean()
        current.save()
    else:
        identifier = ExternalIdentifier(recording=recording, scheme=ExternalIdentifier.Scheme.ISRC, value=value)
        identifier.full_clean()
        identifier.save()


@transaction.atomic
def save_release_track_rows(*, release, rows):
    """Save one submitted grid as a unit without scheduling FLAC writeback."""
    context = suppress_automatic_flac_sync() if suppress_automatic_flac_sync else nullcontext()
    with context:
        existing = {str(item.pk): item for item in release.tracks.select_for_update()}
        submitted_sequences = [row["sequence_number"] for row in rows if not row.get("remove")]
        if len(submitted_sequences) != len(set(submitted_sequences)):
            raise ValidationError("Sorteringsrekkefølge må være unik innen utgivelsen.")

        temporary = (release.tracks.aggregate(value=Max("sequence_number"))["value"] or 0) + 10000
        for offset, item in enumerate(existing.values()):
            item.sequence_number = temporary + offset
            item.save(update_fields=["sequence_number", "updated_at"])

        saved = []
        for row in rows:
            track = existing.get(str(row.get("track_id") or ""))
            if row.get("remove"):
                if track:
                    linked_files = list(track.file_assets.values_list("filename", flat=True)[:4])
                    if linked_files:
                        filenames = ", ".join(f"«{name}»" for name in linked_files)
                        if track.file_assets.count() > len(linked_files):
                            filenames += " og flere"
                        raise ValidationError(
                            "Sporet kan ikke fjernes fordi radiofilen "
                            f"{filenames} er knyttet til denne sporforekomsten. "
                            "Koble filen til riktig spor eller fjern filkoblingen først."
                        )
                    track.delete()
                continue

            recording_id = row.get("recording_id")
            recording = Recording.objects.filter(pk=recording_id).first() if recording_id else None
            is_new_recording = recording is None
            common = dict(
                release=release,
                sequence_number=row["sequence_number"],
                disc_number=row.get("disc_number"),
                side=(row.get("side") or "").strip(),
                track_number=row.get("track_number"),
                title_override=(row.get("title_override") or "").strip(),
                duration_ms=row.get("duration_ms"),
            )
            if track:
                recording = resolve_release_track_recording(
                    recording=recording,
                    new_recording_title=(row.get("recording_title") or "") if not recording else "",
                    new_isrc=row.get("isrc") or "",
                    force_create=row.get("force_create", False),
                    duration_ms=row.get("duration_ms"),
                )
                track.recording = recording
                for field, value in common.items():
                    if field != "release":
                        setattr(track, field, value)
                track.full_clean()
                track.save()
            else:
                track = create_release_track(
                    recording=recording,
                    new_recording_title=(row.get("recording_title") or "") if not recording else "",
                    new_isrc=row.get("isrc") or "",
                    force_create=row.get("force_create", False),
                    **common,
                )
                recording = track.recording

            if is_new_recording or row.get("update_shared_recording"):
                if row.get("update_shared_recording") and row.get("recording_title"):
                    recording.title = row["recording_title"].strip()
                    recording.full_clean()
                    recording.save()
                _set_isrc(recording, row.get("isrc") or "")
                _replace_credits(recording, RecordingContribution.Role.PRIMARY, row.get("artists"))
                _replace_credits(recording, RecordingContribution.Role.COMPOSER, row.get("composers"))
                _replace_credits(recording, RecordingContribution.Role.LYRICIST, row.get("lyricists"))
                _replace_credits(recording, RecordingContribution.Role.ARRANGER, row.get("arrangers"))
            saved.append(track)
        return saved
