"""Read-only status facts for a Recording's master and radio pipeline."""

from dataclasses import dataclass
from enum import StrEnum

from django.core.exceptions import ObjectDoesNotExist

from .models import (
    FileAsset,
    FileDerivation,
    RadioFlacGeneration,
)


class MasterState(StrEnum):
    NO_MASTER = "no_master"
    SELECTED = "selected"


class CurrentRadioState(StrEnum):
    NO_RADIO = "no_radio"
    CURRENT_WITHOUT_MASTER = "current_without_master"
    MATCHES_SELECTED_MASTER = "matches_selected_master"
    FROM_PREVIOUS_MASTER = "from_previous_master"
    LINEAGE_UNKNOWN = "lineage_unknown"


class GenerationState(StrEnum):
    NONE = "none"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    CANDIDATE_FROM_SELECTED_MASTER = "candidate_from_selected_master"
    CANDIDATE_FROM_OTHER_MASTER = "candidate_from_other_master"


class RadioWorkState(StrEnum):
    NO_SELECTED_MASTER = "no_selected_master"
    MISSING_RADIO_FLAC = "missing_radio_flac"
    CURRENT_MATCHES_SELECTED_MASTER = "current_matches_selected_master"
    CURRENT_FROM_PREVIOUS_MASTER = "current_from_previous_master"
    CURRENT_LINEAGE_UNKNOWN = "current_lineage_unknown"
    CANDIDATE_FROM_SELECTED_MASTER = "candidate_from_selected_master"
    CANDIDATE_FROM_OTHER_MASTER = "candidate_from_other_master"
    GENERATING = "generating"
    GENERATION_FAILED = "generation_failed"


def radio_work_state(status):
    """One read-only priority order shared by all radio work surfaces."""
    if status.master_state == MasterState.NO_MASTER:
        return RadioWorkState.NO_SELECTED_MASTER
    if (
        status.generation_state
        == GenerationState.CANDIDATE_FROM_SELECTED_MASTER
    ):
        return RadioWorkState.CANDIDATE_FROM_SELECTED_MASTER
    if status.generation_state == GenerationState.IN_PROGRESS:
        return RadioWorkState.GENERATING
    if status.generation_state == GenerationState.FAILED:
        return RadioWorkState.GENERATION_FAILED
    if status.generation_state == GenerationState.CANDIDATE_FROM_OTHER_MASTER:
        return RadioWorkState.CANDIDATE_FROM_OTHER_MASTER
    if status.current_state == CurrentRadioState.NO_RADIO:
        return RadioWorkState.MISSING_RADIO_FLAC
    if status.current_state == CurrentRadioState.FROM_PREVIOUS_MASTER:
        return RadioWorkState.CURRENT_FROM_PREVIOUS_MASTER
    if status.current_state == CurrentRadioState.LINEAGE_UNKNOWN:
        return RadioWorkState.CURRENT_LINEAGE_UNKNOWN
    return RadioWorkState.CURRENT_MATCHES_SELECTED_MASTER


@dataclass(frozen=True)
class RecordingMediaPipelineStatus:
    selected_master: FileAsset | None
    current_radio: FileAsset | None
    current_source_master: FileAsset | None
    candidate_asset: FileAsset | None
    candidate_source_master: FileAsset | None
    latest_generation: RadioFlacGeneration | None
    master_state: MasterState
    current_state: CurrentRadioState
    generation_state: GenerationState


_UNSET = object()


def _source_master(asset, derivations, generations):
    if not asset:
        return None
    sources = {
        relation.source_asset_id: relation.source_asset
        for relation in derivations
        if relation.derived_asset_id == asset.pk
        and relation.relation_type
        == FileDerivation.RelationType.GENERATED_FROM
        and relation.source_asset.role == FileAsset.Role.EDITED_WAV_MASTER
    }
    if len(sources) == 1:
        return next(iter(sources.values()))
    matching = [
        generation.master_asset
        for generation in generations
        if generation.candidate_asset_id == asset.pk
    ]
    return matching[0] if len(matching) == 1 else None


def get_recording_media_pipeline_status(
    recording,
    *,
    selection=_UNSET,
    generations=None,
    derivations=None,
):
    """Return structured facts without checking or changing physical files.

    Optional preloaded collections let list views reuse this exact decision
    logic without introducing one query per Recording.
    """
    if selection is _UNSET:
        try:
            selection = recording.media_selection
        except ObjectDoesNotExist:
            selection = None
    if generations is None:
        generations = list(
            recording.radio_flac_generations.select_related(
                "master_asset", "candidate_asset"
            ).order_by("-created_at", "id")
        )
    else:
        generations = list(generations)

    selected = selection.selected_master if selection else None
    current = selection.current_radio if selection else None
    candidate_ids = {
        generation.candidate_asset_id
        for generation in generations
        if generation.candidate_asset_id
    }
    if current:
        candidate_ids.add(current.pk)
    if derivations is None:
        derivations = list(
            FileDerivation.objects.filter(derived_asset_id__in=candidate_ids)
            .select_related("source_asset", "derived_asset")
            .order_by("-created_at", "id")
        )
    else:
        derivations = list(derivations)

    current_source = _source_master(current, derivations, generations)
    if not current:
        current_state = CurrentRadioState.NO_RADIO
    elif not selected:
        current_state = CurrentRadioState.CURRENT_WITHOUT_MASTER
    elif not current_source:
        current_state = CurrentRadioState.LINEAGE_UNKNOWN
    elif current_source.pk == selected.pk:
        current_state = CurrentRadioState.MATCHES_SELECTED_MASTER
    else:
        current_state = CurrentRadioState.FROM_PREVIOUS_MASTER

    latest_for_selected = next(
        (
            generation
            for generation in generations
            if selected and generation.master_asset_id == selected.pk
        ),
        None,
    )
    latest_generation = latest_for_selected or next(iter(generations), None)
    candidate_generation = next(
        (
            generation
            for generation in generations
            if generation.candidate_asset_id
            and (not current or generation.candidate_asset_id != current.pk)
            and generation.status == RadioFlacGeneration.Status.VERIFIED
            and selected
            and generation.master_asset_id == selected.pk
        ),
        None,
    )
    if candidate_generation:
        generation_state = GenerationState.CANDIDATE_FROM_SELECTED_MASTER
    elif (
        latest_for_selected
        and latest_for_selected.status == RadioFlacGeneration.Status.GENERATING
    ):
        generation_state = GenerationState.IN_PROGRESS
    elif (
        latest_for_selected
        and latest_for_selected.status == RadioFlacGeneration.Status.FAILED
    ):
        generation_state = GenerationState.FAILED
    elif (
        latest_for_selected
        and latest_for_selected.status == RadioFlacGeneration.Status.PLANNED
    ):
        generation_state = GenerationState.PLANNED
    else:
        candidate_generation = next(
            (
                generation
                for generation in generations
                if generation.candidate_asset_id
                and (
                    not current or generation.candidate_asset_id != current.pk
                )
                and generation.status == RadioFlacGeneration.Status.VERIFIED
            ),
            None,
        )
        generation_state = (
            GenerationState.CANDIDATE_FROM_OTHER_MASTER
            if candidate_generation
            else GenerationState.NONE
        )

    candidate = (
        candidate_generation.candidate_asset if candidate_generation else None
    )
    return RecordingMediaPipelineStatus(
        selected_master=selected,
        current_radio=current,
        current_source_master=current_source,
        candidate_asset=candidate,
        candidate_source_master=_source_master(
            candidate, derivations, generations
        ),
        latest_generation=latest_generation,
        master_state=(
            MasterState.SELECTED if selected else MasterState.NO_MASTER
        ),
        current_state=current_state,
        generation_state=generation_state,
    )
