"""Read-only provenance defaults, shared by single and bulk onboarding."""

from catalogue.models import RecordingContribution
from music_library.models import MusicLibraryEntry
from provenance.models import MetadataAssertion, SourceRecord


def catalogue_sources(recording_ids):
    ids = set(recording_ids)
    if not ids:
        return {}
    entries = dict(
        MusicLibraryEntry.objects.filter(recording_id__in=ids).values_list(
            "pk", "recording_id"
        )
    )
    links = []
    for entity, targets in (
        ("recording", {pk: pk for pk in ids}),
        ("music_library_entry", entries),
    ):
        links.extend(
            (targets[target], source)
            for target, source in MetadataAssertion.objects.filter(
                entity_type=entity, entity_uuid__in=targets
            ).values_list("entity_uuid", "source_record_id")
        )
    links.extend(
        RecordingContribution.objects.filter(
            recording_id__in=ids, source_record__isnull=False
        ).values_list("recording_id", "source_record_id")
    )
    links.extend(
        SourceRecord.objects.filter(
            flac_ingest_item__recording_id__in=ids,
            flac_ingest_item__applied_at__isnull=False,
        ).values_list("flac_ingest_item__recording_id", "pk")
    )
    sources = {
        s.pk: s
        for s in SourceRecord.objects.filter(
            pk__in={s for _, s in links}
        ).select_related("source_system")
    }
    result = {}
    for recording_id, source_id in links:
        source = sources.get(source_id)
        if source and (
            recording_id not in result
            or (source.created_at, source.pk)
            < (result[recording_id].created_at, result[recording_id].pk)
        ):
            result[recording_id] = source
    return result
