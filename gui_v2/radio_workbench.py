"""Recording-centred, read-only radio work projection."""

from collections import Counter, defaultdict

from django.db.models import Q

from catalogue.models import Recording, ReleaseTrack
from media_assets.models import (
    FileAsset,
    FileDerivation,
    RadioFlacGeneration,
    RecordingMediaSelection,
)
from media_assets.pipeline_status import (
    RadioWorkState,
    get_recording_media_pipeline_status,
    radio_work_state,
)

WORK_LABELS = {
    RadioWorkState.NO_SELECTED_MASTER: "Ingen valgt master",
    RadioWorkState.MISSING_RADIO_FLAC: "Mangler Radio-FLAC",
    RadioWorkState.CURRENT_MATCHES_SELECTED_MASTER: "Oppdatert",
    RadioWorkState.CURRENT_FROM_PREVIOUS_MASTER: "Fra tidligere master",
    RadioWorkState.CURRENT_LINEAGE_UNKNOWN: "Opphav ukjent",
    RadioWorkState.CANDIDATE_FROM_SELECTED_MASTER: "Kandidat klar",
    RadioWorkState.CANDIDATE_FROM_OTHER_MASTER: "Kandidat fra annen master",
    RadioWorkState.GENERATING: "Generering pågår",
    RadioWorkState.GENERATION_FAILED: "Generering feilet",
}


def radio_work_rows(*, query="", chunk_size=150):
    """Load related facts per chunk; never query once per Recording."""
    ids = set(
        FileAsset.objects.filter(
            role=FileAsset.Role.EDITED_WAV_MASTER,
            recording_id__isnull=False,
        ).values_list("recording_id", flat=True)
    )
    ids.update(
        RecordingMediaSelection.objects.values_list("recording_id", flat=True)
    )
    ids.update(
        RadioFlacGeneration.objects.values_list("recording_id", flat=True)
    )
    recordings = Recording.objects.filter(pk__in=ids)
    if query:
        recordings = recordings.filter(
            Q(title__icontains=query)
            | Q(identifiers__value__icontains=query)
            | Q(release_tracks__release__title__icontains=query)
            | Q(release_tracks__release__catalogue_number__icontains=query)
        ).distinct()
    rows = []
    batch = []
    for recording in recordings.order_by("title", "pk").iterator(
        chunk_size=chunk_size
    ):
        batch.append(recording)
        if len(batch) == chunk_size:
            rows.extend(_rows_for_batch(batch))
            batch = []
    if batch:
        rows.extend(_rows_for_batch(batch))
    return rows


def _rows_for_batch(recordings):
    ids = [recording.pk for recording in recordings]
    selections = {
        item.recording_id: item
        for item in RecordingMediaSelection.objects.filter(
            recording_id__in=ids
        ).select_related("selected_master", "current_radio")
    }
    generations = defaultdict(list)
    radio_ids = {
        item.current_radio_id
        for item in selections.values()
        if item.current_radio_id
    }
    for generation in (
        RadioFlacGeneration.objects.filter(recording_id__in=ids)
        .select_related("master_asset", "candidate_asset")
        .order_by("-created_at", "id")
    ):
        generations[generation.recording_id].append(generation)
        if generation.candidate_asset_id:
            radio_ids.add(generation.candidate_asset_id)
    derivations = list(
        FileDerivation.objects.filter(derived_asset_id__in=radio_ids)
        .select_related("source_asset", "derived_asset")
        .order_by("-created_at", "id")
    )
    releases = defaultdict(list)
    for track in (
        ReleaseTrack.objects.filter(recording_id__in=ids)
        .select_related("release")
        .order_by("release__title", "release_id")
    ):
        if all(
            item.pk != track.release_id
            for item in releases[track.recording_id]
        ):
            releases[track.recording_id].append(track.release)
    rows = []
    for recording in recordings:
        facts = get_recording_media_pipeline_status(
            recording,
            selection=selections.get(recording.pk),
            generations=generations[recording.pk],
            derivations=derivations,
        )
        state = radio_work_state(facts)
        candidate_generation = next(
            (
                generation
                for generation in generations[recording.pk]
                if facts.candidate_asset
                and generation.candidate_asset_id == facts.candidate_asset.pk
            ),
            None,
        )
        rows.append(
            {
                "recording": recording,
                "facts": facts,
                "state": state,
                "label": WORK_LABELS[state],
                "releases": releases[recording.pk],
                "candidate_generation": candidate_generation,
            }
        )
    return rows


def radio_work_counts(rows):
    return Counter(row["state"] for row in rows)
