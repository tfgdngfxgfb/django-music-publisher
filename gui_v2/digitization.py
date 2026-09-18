"""Music digitization workbench, consuming media services and existing GUI v2."""

import logging
from dataclasses import replace

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
    ObjectDoesNotExist,
)
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from urllib.parse import quote

from catalogue.models import Label, Release, ReleaseTrack
from catalogue.metadata_providers import provider_availability
from managed_music.models import ManagedRelease
from media_assets.digitization import (
    apply_plan,
    delete_empty_batch,
    complete_digitization,
    list_digitization_folder,
    preview_operation,
    preview_registration,
    require_operator,
)
from media_assets.models import (
    DigitizationBatch,
    DigitizationDerivation,
    DigitizationFile,
    DigitizationPlan,
    FileAsset,
    FileDerivation,
    FileLocation,
    MediaAssetEvent,
    RecordingMediaSelection,
    RadioFlacGeneration,
)
from media_assets.pipeline_status import (
    CurrentRadioState,
    GenerationState,
    MasterState,
    RadioWorkState,
    get_recording_media_pipeline_status,
    radio_work_state,
)
from media_assets.digitization_matching import suggest_master_links
from media_assets.digitization_storage import (
    suggested_folder,
    folder_breadcrumbs,
)
from .forms import MasterRegistrationForm, ReleaseMetadataForm
from .home_state import remember_object
from .recording_files import _location_data, _technical, _size

logger = logging.getLogger(__name__)
VIEW_PERMS = (
    "media_assets.view_digitizationbatch",
    "media_assets.view_fileasset",
    "media_assets.view_filelocation",
    "catalogue.view_release",
    "catalogue.view_recording",
)


class BatchForm(forms.ModelForm):
    class Meta:
        model = DigitizationBatch
        fields = (
            "release",
            "title",
            "captured_on",
            "source_description",
            "notes",
        )
        widgets = {
            "captured_on": forms.DateInput(attrs={"type": "date"}),
            "source_description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class DigitizationReleaseForm(ReleaseMetadataForm):
    class Meta(ReleaseMetadataForm.Meta):
        fields = (
            "title",
            "release_type",
            "release_year",
            "label",
            "catalogue_number",
            "notes",
        )


class FolderForm(MasterRegistrationForm):
    role = forms.ChoiceField(
        label="Filrolle",
        choices=(
            (FileAsset.Role.RAW_DIGITIZATION, "Rå digitalisering"),
            (FileAsset.Role.EDITED_WAV_MASTER, "Redigerte WAV-mastere"),
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = []
        if str(getattr(settings, "P7_MUSIC_ROOT", "") or "").strip():
            choices.append(
                ("music_library", "Musikkarkiv · felles kildeområde")
            )
        for key, label in (
            ("raw_sources", "RAW-arkiv"),
            ("edited_masters", "Redigerte mastere"),
        ):
            if key in settings.P7_STORAGE_ROOTS:
                choices.append((key, label))
        self.fields["root_key"].choices = choices
        self.fields["relative_path"].label = "Mappe under lagringsroten"
        self.fields["relative_path"].required = False
        self.fields["relative_path"].initial = "."
        self.fields["relative_path"].help_text = (
            "Leser WAV-filene direkte i mappen. Lydredigering skjer utenfor P7."
        )


def _write_access(request):
    require_operator(request.user)
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied(
            "Endringer er deaktivert i dette GUI-v2-oppsettet."
        )


def _present_pipeline_status(status):
    master = {
        MasterState.NO_MASTER: ("Mangler", "muted"),
        MasterState.SELECTED: ("Valgt", "ok"),
    }[status.master_state]
    current = {
        CurrentRadioState.NO_RADIO: ("Ingen radiofil", "muted"),
        CurrentRadioState.CURRENT_WITHOUT_MASTER: (
            "Gjeldende radiofil",
            "ok",
        ),
        CurrentRadioState.MATCHES_SELECTED_MASTER: (
            "Oppdatert fra valgt master",
            "ok",
        ),
        CurrentRadioState.FROM_PREVIOUS_MASTER: (
            "Fra tidligere master",
            "warning",
        ),
        CurrentRadioState.LINEAGE_UNKNOWN: (
            "Masteropprinnelse ukjent",
            "muted",
        ),
    }[status.current_state]
    generation = {
        GenerationState.NONE: ("Ingen kandidat", "muted"),
        GenerationState.PLANNED: ("Generering planlagt", "cyan"),
        GenerationState.IN_PROGRESS: ("Generering pågår", "cyan"),
        GenerationState.FAILED: ("Generering feilet", "error"),
        GenerationState.CANDIDATE_FROM_SELECTED_MASTER: (
            "Ny radiofil klar til aktivering",
            "cyan",
        ),
        GenerationState.CANDIDATE_FROM_OTHER_MASTER: (
            "Kandidat fra annen master",
            "warning",
        ),
    }[status.generation_state]
    return {
        "facts": status,
        "master_label": master[0],
        "master_kind": master[1],
        "current_label": current[0],
        "current_kind": current[1],
        "generation_label": generation[0],
        "generation_kind": generation[1],
        "show_generation": status.generation_state != GenerationState.NONE,
    }


def batch_workspace(batch):
    """Bounded query groups; rendering never checks physical file availability."""
    tracks = list(
        batch.release.tracks.select_related("recording").order_by(
            "sequence_number"
        )
    )
    files = [
        item.asset
        for item in batch.files.select_related(
            "asset", "asset__recording", "asset__release_track"
        ).prefetch_related("asset__locations")
    ]
    files.sort(key=lambda asset: (asset.filename.casefold(), str(asset.pk)))
    ids = {track.recording_id for track in tracks} | {
        asset.recording_id for asset in files if asset.recording_id
    }
    recordings = {track.recording_id: track.recording for track in tracks}
    recordings.update(
        {
            asset.recording_id: asset.recording
            for asset in files
            if asset.recording_id
        }
    )
    selections = {
        item.recording_id: item
        for item in RecordingMediaSelection.objects.filter(
            recording_id__in=ids
        ).select_related("selected_master", "current_radio")
    }
    digitization_derivations = list(
        DigitizationDerivation.objects.filter(
            derived_asset_id__in=[asset.pk for asset in files]
        )
        .select_related("source_asset", "derived_asset")
        .order_by("-created_at")
    )
    active_sources = {
        item.derived_asset_id: item
        for item in digitization_derivations
        if item.is_active
    }
    generations = list(
        RadioFlacGeneration.objects.filter(recording_id__in=ids)
        .select_related("master_asset", "candidate_asset")
        .order_by("-created_at", "id")
    )
    current_ids = {
        selection.current_radio_id
        for selection in selections.values()
        if selection.current_radio_id
    }
    candidate_ids = {
        generation.candidate_asset_id
        for generation in generations
        if generation.candidate_asset_id
    }
    machine_derivations = list(
        FileDerivation.objects.filter(
            derived_asset_id__in=current_ids | candidate_ids
        )
        .select_related("source_asset", "derived_asset")
        .order_by("-created_at", "id")
    )
    generations_by_recording = {
        recording_id: [
            generation
            for generation in generations
            if generation.recording_id == recording_id
        ]
        for recording_id in ids
    }
    statuses = {
        recording_id: _present_pipeline_status(
            get_recording_media_pipeline_status(
                recordings[recording_id],
                selection=selections.get(recording_id),
                generations=generations_by_recording[recording_id],
                derivations=machine_derivations,
            )
        )
        for recording_id in ids
    }
    radio_states = [
        radio_work_state(status["facts"]) for status in statuses.values()
    ]
    release_counts = {
        item["recording_id"]: item["count"]
        for item in ReleaseTrack.objects.filter(recording_id__in=ids)
        .values("recording_id")
        .annotate(count=Count("release_id", distinct=True))
    }
    rows, raws = [], []
    suggestions = suggest_master_links(
        [
            asset
            for asset in files
            if asset.role == FileAsset.Role.EDITED_WAV_MASTER
        ],
        tracks,
        [
            asset
            for asset in files
            if asset.role == FileAsset.Role.RAW_DIGITIZATION
        ],
        {
            key: relation.source_asset
            for key, relation in active_sources.items()
        },
    )
    for asset in files:
        selection = selections.get(asset.recording_id)
        row = {
            "asset": asset,
            "technical": _technical(asset),
            "size": _size(asset.size_bytes),
            "locations": [
                _location_data(loc) for loc in asset.locations.all()
            ],
            "selection": selection,
            "source": active_sources.get(asset.pk),
            "pipeline_status": statuses.get(asset.recording_id),
            "release_count": release_counts.get(asset.recording_id, 0),
        }
        if asset.role == FileAsset.Role.RAW_DIGITIZATION:
            row["outputs"] = [
                item
                for item in digitization_derivations
                if item.source_asset_id == asset.pk and item.is_active
            ]
            raws.append(row)
        else:
            row["link_suggestion"] = suggestions[asset.pk]
            row["selected"] = bool(
                selection and selection.selected_master_id == asset.pk
            )
            row["can_select"] = bool(
                asset.recording_id
                and (
                    not selection
                    or not selection.selected_master_id
                    or selection.selected_master_id == asset.pk
                )
            )
            row["generation"] = next(
                (
                    item
                    for item in generations
                    if item.master_asset_id == asset.pk
                ),
                None,
            )
            rows.append(row)
    pipeline = []
    for track in tracks:
        masters = [
            row
            for row in rows
            if row["asset"].recording_id == track.recording_id
        ]
        selection = selections.get(track.recording_id)
        pipeline.append(
            {
                "track": track,
                "masters": masters,
                "selection": selection,
                "raw": any(row["source"] for row in masters),
                "generation": next(
                    (
                        g
                        for g in generations
                        if selection
                        and g.master_asset_id == selection.selected_master_id
                    ),
                    None,
                ),
                "pipeline_status": statuses.get(track.recording_id),
                "release_count": release_counts.get(track.recording_id, 0),
            }
        )
    return {
        "tracks": tracks,
        "masters": rows,
        "raws": raws,
        "pipeline": pipeline,
        "events": MediaAssetEvent.objects.filter(
            Q(digitization_batch=batch)
            | Q(asset_id__in=[asset.pk for asset in files])
            | Q(related_asset_id__in=[asset.pk for asset in files])
        ).select_related("actor", "asset", "related_asset")[:50],
        "progress": {
            "total": len(pipeline),
            "raw": sum(bool(row["raw"]) for row in pipeline),
            "masters": sum(bool(row["masters"]) for row in pipeline),
            "selected": sum(
                bool(row["selection"] and row["selection"].selected_master_id)
                for row in pipeline
            ),
            "generated": sum(
                bool(
                    row["generation"] and row["generation"].candidate_asset_id
                )
                for row in pipeline
            ),
            "current": sum(
                bool(row["selection"] and row["selection"].current_radio_id)
                for row in pipeline
            ),
            "radio_missing": radio_states.count(
                RadioWorkState.MISSING_RADIO_FLAC
            ),
            "radio_previous": radio_states.count(
                RadioWorkState.CURRENT_FROM_PREVIOUS_MASTER
            ),
            "radio_unknown": radio_states.count(
                RadioWorkState.CURRENT_LINEAGE_UNKNOWN
            ),
            "radio_updated": radio_states.count(
                RadioWorkState.CURRENT_MATCHES_SELECTED_MASTER
            ),
            "radio_candidate": radio_states.count(
                RadioWorkState.CANDIDATE_FROM_SELECTED_MASTER
            ),
        },
        "derivations": digitization_derivations,
    }


def release_matrix_workspace(release):
    """Read-only release view over all batches and global Recording choices."""
    tracks = list(
        release.tracks.select_related("recording").order_by("sequence_number")
    )
    recording_ids = {track.recording_id for track in tracks}
    memberships = list(
        DigitizationFile.objects.filter(batch__release=release)
        .select_related(
            "batch", "asset", "asset__recording", "asset__release_track"
        )
        .order_by("batch__created_at", "asset__filename")
    )
    masters = [
        member.asset
        for member in memberships
        if member.asset.role == FileAsset.Role.EDITED_WAV_MASTER
    ]
    raw_assets = [
        member.asset
        for member in memberships
        if member.asset.role == FileAsset.Role.RAW_DIGITIZATION
    ]
    sources = list(
        DigitizationDerivation.objects.filter(
            derived_asset_id__in=[master.pk for master in masters],
            is_active=True,
        ).select_related("source_asset", "derived_asset")
    )
    sources_by_master = {source.derived_asset_id: source for source in sources}
    masters_by_recording = {recording_id: [] for recording_id in recording_ids}
    for master in masters:
        if master.recording_id in masters_by_recording:
            masters_by_recording[master.recording_id].append(master)
    selections = {
        selection.recording_id: selection
        for selection in RecordingMediaSelection.objects.filter(
            recording_id__in=recording_ids
        ).select_related("selected_master", "current_radio")
    }
    generations = list(
        RadioFlacGeneration.objects.filter(recording_id__in=recording_ids)
        .select_related("master_asset", "candidate_asset")
        .order_by("-created_at", "id")
    )
    generations_by_recording = {
        recording_id: [] for recording_id in recording_ids
    }
    for generation in generations:
        generations_by_recording[generation.recording_id].append(generation)
    radio_asset_ids = {
        asset_id
        for asset_id in (
            [selection.current_radio_id for selection in selections.values()]
            + [generation.candidate_asset_id for generation in generations]
        )
        if asset_id
    }
    derivations = list(
        FileDerivation.objects.filter(
            derived_asset_id__in=radio_asset_ids
        ).select_related("source_asset", "derived_asset")
    )
    statuses = {
        track.recording_id: _present_pipeline_status(
            get_recording_media_pipeline_status(
                track.recording,
                selection=selections.get(track.recording_id),
                generations=generations_by_recording[track.recording_id],
                derivations=derivations,
            )
        )
        for track in tracks
    }
    release_counts = {
        item["recording_id"]: item["count"]
        for item in ReleaseTrack.objects.filter(recording_id__in=recording_ids)
        .values("recording_id")
        .annotate(count=Count("release_id", distinct=True))
    }
    rows = []
    for track in tracks:
        status = statuses[track.recording_id]
        facts = status["facts"]
        track_masters = masters_by_recording[track.recording_id]
        # A version can be global to the Recording, not just this release.
        master_versions = list(track_masters)
        if facts.selected_master and all(
            asset.pk != facts.selected_master.pk for asset in master_versions
        ):
            master_versions.append(facts.selected_master)
        raw_sources = [
            sources_by_master[asset.pk].source_asset
            for asset in track_masters
            if asset.pk in sources_by_master
        ]
        if not track_masters:
            issue = "Ingen redigert master knyttet til denne digitaliseringen"
            issue_kind = "work"
            filter_key = "missing_master"
        elif not facts.selected_master:
            issue = "Ingen master valgt for innspillingen"
            issue_kind = "work"
            filter_key = "missing_selected"
        elif facts.current_state == CurrentRadioState.FROM_PREVIOUS_MASTER:
            issue = "Gjeldende radiofil kommer fra en tidligere master"
            issue_kind = "review"
            filter_key = "previous_master"
        elif facts.current_state == CurrentRadioState.LINEAGE_UNKNOWN:
            issue = "Gjeldende radiofils masteropprinnelse er ikke dokumentert"
            issue_kind = "unknown"
            filter_key = "unknown_lineage"
        elif facts.generation_state == GenerationState.FAILED:
            issue = "Siste generering feilet"
            issue_kind = "blocked"
            filter_key = "generation_failed"
        elif (
            facts.generation_state
            == GenerationState.CANDIDATE_FROM_SELECTED_MASTER
        ):
            issue = "Verifisert kandidat venter på vurdering"
            issue_kind = "work"
            filter_key = "candidate"
        elif facts.current_state == CurrentRadioState.NO_RADIO:
            issue = "Ingen gjeldende radiofil registrert"
            issue_kind = "work"
            filter_key = "no_radio"
        else:
            issue = "Ingen åpne ledd i denne oversikten"
            issue_kind = "ok"
            filter_key = "complete"
        rows.append(
            {
                "track": track,
                "masters": master_versions,
                "has_batch_master": bool(track_masters),
                "raw_sources": raw_sources,
                "status": status,
                "candidate_generation": next(
                    (
                        generation
                        for generation in generations_by_recording[
                            track.recording_id
                        ]
                        if facts.candidate_asset
                        and generation.candidate_asset_id
                        == facts.candidate_asset.pk
                    ),
                    None,
                ),
                "issue": issue,
                "issue_kind": issue_kind,
                "filter_key": filter_key,
                "release_count": release_counts.get(track.recording_id, 0),
            }
        )
    return {
        "rows": rows,
        "batches": list(
            release.digitization_batches.order_by("-created_at", "id")
        ),
        "raw_count": len(raw_assets),
        "master_count": len(masters),
        "issue_count": sum(row["issue_kind"] != "ok" for row in rows),
    }


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET"])
def release_matrix(request, release_id):
    release = get_object_or_404(
        Release.objects.select_related("label"), pk=release_id
    )
    return render(
        request,
        "gui_v2/digitization_release_matrix.html",
        {
            "section": "digitization",
            "release": release,
            "workspace": release_matrix_workspace(release),
        },
    )


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def start(request):
    """Choose a Release first; the batch is an internal work context."""
    query = request.GET.get("q", "").strip()[:100]
    releases = Release.objects.select_related("label").order_by("title")
    if query:
        releases = releases.filter(
            Q(title__icontains=query)
            | Q(catalogue_number__icontains=query)
            | Q(label__name__icontains=query)
            | Q(tracks__recording__contributions__credited_as__icontains=query)
            | Q(
                tracks__recording__contributions__artist_identity__display_name__icontains=query
            )
        ).distinct()
    release_form = DigitizationReleaseForm(
        request.POST if request.POST.get("mode") == "new" else None,
        prefix="release",
    )
    error = ""
    if request.method == "POST":
        _write_access(request)
        mode = request.POST.get("mode")
        release = None
        if mode == "existing":
            try:
                release = Release.objects.filter(
                    pk=request.POST.get("release_id")
                ).first()
            except (ValidationError, ValueError):
                release = None
            if release is None:
                error = "Velg en eksisterende utgivelse."
        elif mode == "new":
            if not request.user.has_perm("catalogue.add_release"):
                raise PermissionDenied
            if not release_form.is_valid():
                error = "Kontroller feltene for den nye utgivelsen."
        else:
            error = "Velg eksisterende eller ny utgivelse."
        if release or (mode == "new" and not error):
            with transaction.atomic():
                if mode == "new":
                    release = release_form.save()
                    release_form.save_barcode()
                batch = DigitizationBatch.objects.create(
                    release=release,
                    title=(
                        f"Digitalisering · {release.title} · "
                        f"{timezone.localdate():%d.%m.%Y}"
                    )[:255],
                    created_by=request.user,
                )
            return redirect(
                f"{reverse('gui_v2:digitization_detail', args=[batch.pk])}?step=metadata"
            )
    return render(
        request,
        "gui_v2/digitization_start.html",
        {
            "section": "digitization",
            "query": query,
            "releases": releases[:50],
            "release_form": release_form,
            "error": error,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def index(request):
    query = request.GET.get("q", "").strip()
    batches = DigitizationBatch.objects.select_related(
        "release",
        "release__label",
        "release__managed_release",
        "created_by",
    )
    if query:
        batches = batches.filter(
            Q(title__icontains=query)
            | Q(release__title__icontains=query)
            | Q(release__catalogue_number__icontains=query)
        )
    if request.GET.get("needs") == "recording":
        batches = batches.filter(
            files__asset__role=FileAsset.Role.EDITED_WAV_MASTER,
            files__asset__recording__isnull=True,
        ).distinct()
    for param, field in (
        ("status", "status"),
        ("label", "release__label_id"),
        ("year", "release__release_year"),
    ):
        value = request.GET.get(param)
        if value:
            try:
                batches = batches.filter(**{field: value})
            except (ValidationError, ValueError):
                batches = batches.none()
    can_view_release_management = request.user.has_perm(
        "catalogue.view_release"
    )
    management = request.GET.get("management", "")
    if can_view_release_management:
        if management == "owned":
            batches = batches.filter(
                release__managed_release__status=ManagedRelease.Status.ACTIVE,
                release__managed_release__relationship=(
                    ManagedRelease.Relationship.OWNED_CATALOGUE
                ),
            )
        elif management == "managed":
            batches = batches.filter(
                release__managed_release__status=ManagedRelease.Status.ACTIVE,
                release__managed_release__relationship=(
                    ManagedRelease.Relationship.MANAGED_CATALOGUE
                ),
            )
        elif management in {
            ManagedRelease.Status.PENDING,
            ManagedRelease.Status.INACTIVE,
        }:
            batches = batches.filter(
                release__managed_release__status=management
            )
        elif management == "none":
            batches = batches.filter(release__managed_release__isnull=True)
    new_release = request.POST.get("operation") == "create_release"
    form = BatchForm(
        request.POST if request.method == "POST" and not new_release else None,
        initial={"release": request.GET.get("release")},
    )
    release_form = DigitizationReleaseForm(
        request.POST if new_release else None, prefix="release"
    )
    if request.method == "POST":
        _write_access(request)
        if new_release:
            if not request.user.has_perm("catalogue.add_release"):
                raise PermissionDenied
            if release_form.is_valid():
                release = release_form.save()
                return redirect(
                    f"{reverse('gui_v2:digitization_index')}?release={release.pk}"
                )
        elif form.is_valid():
            batch = form.save(commit=False)
            batch.created_by = request.user
            batch.save()
            return redirect("gui_v2:digitization_detail", batch_id=batch.pk)
    page = Paginator(batches, 40).get_page(request.GET.get("page"))
    page.object_list = list(page.object_list)
    release_ids = {batch.release_id for batch in page.object_list}
    tracks_by_release = {release_id: [] for release_id in release_ids}
    for track in ReleaseTrack.objects.filter(
        release_id__in=release_ids
    ).values("release_id", "recording_id"):
        tracks_by_release[track["release_id"]].append(track["recording_id"])
    # Fetch membership once per page, not once per displayed batch.
    members = list(
        FileAsset.objects.filter(
            digitization_file__batch__in=page.object_list
        ).values("digitization_file__batch_id", "role", "recording_id", "id")
    )
    selections = {
        item.recording_id: item
        for item in RecordingMediaSelection.objects.filter(
            recording_id__in={
                recording_id
                for track_ids in tracks_by_release.values()
                for recording_id in track_ids
            }
        )
    }
    for batch in page.object_list:
        track_ids = tracks_by_release[batch.release_id]
        assets = [
            m for m in members if m["digitization_file__batch_id"] == batch.pk
        ]
        masters = [
            m for m in assets if m["role"] == FileAsset.Role.EDITED_WAV_MASTER
        ]
        linked_recording_ids = {m["recording_id"] for m in masters}
        batch.progress = {
            "total": len(track_ids),
            "raw": sum(
                m["role"] == FileAsset.Role.RAW_DIGITIZATION for m in assets
            ),
            "masters": len(masters),
            "linked": sum(
                recording_id in linked_recording_ids
                for recording_id in track_ids
            ),
            "selected": sum(
                bool(
                    selections.get(recording_id)
                    and selections[recording_id].selected_master_id
                )
                for recording_id in track_ids
            ),
            "radio": sum(
                bool(
                    selections.get(recording_id)
                    and selections[recording_id].current_radio_id
                )
                for recording_id in track_ids
            ),
        }
        managed = getattr(batch.release, "managed_release", None)
        if managed is None:
            batch.release_management_label = "Ikke forvaltet"
            batch.release_management_kind = "muted"
        elif managed.status != ManagedRelease.Status.ACTIVE:
            batch.release_management_label = managed.get_status_display()
            batch.release_management_kind = "muted"
        else:
            batch.release_management_label = managed.get_relationship_display()
            batch.release_management_kind = "ok"
    return render(
        request,
        "gui_v2/digitization_index.html",
        {
            "section": "digitization",
            "page": page,
            "form": form,
            "release_form": release_form,
            "query": query,
            "statuses": DigitizationBatch.Status.choices,
            "management_choices": (
                ("owned", "Eid/kontrollert katalog"),
                ("managed", "Forvaltet på vegne av andre"),
                (ManagedRelease.Status.PENDING, "Til vurdering"),
                (ManagedRelease.Status.INACTIVE, "Ikke aktiv"),
                ("none", "Ikke forvaltet"),
            ),
            "can_view_release_management": can_view_release_management,
            "labels": Label.objects.all(),
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


def _browse_storage(batch, params):
    params = params.copy()
    hint = None
    hint_error = ""
    if params.get("suggest") and params.get("root_key"):
        try:
            hint = suggested_folder(
                batch.release, params.get("role"), params["root_key"]
            )
            params["relative_path"] = hint["path"]
        except (ValidationError, OSError, ImproperlyConfigured) as exc:
            hint_error = "; ".join(getattr(exc, "messages", [str(exc)]))
    browse_form = FolderForm(
        params if params.get("browse") else None,
        initial={"role": FileAsset.Role.RAW_DIGITIZATION},
    )
    storage_entries = []
    browse_error = hint_error
    browse_form.folder_hint = hint
    browse_form.breadcrumbs = folder_breadcrumbs(
        params.get("relative_path", ".")
    )
    active_role = params.get("role", FileAsset.Role.RAW_DIGITIZATION)
    if active_role not in {
        FileAsset.Role.RAW_DIGITIZATION,
        FileAsset.Role.EDITED_WAV_MASTER,
    }:
        active_role = FileAsset.Role.RAW_DIGITIZATION
    if params.get("browse") and not hint_error:
        if browse_form.is_valid():
            try:
                storage_entries = list_digitization_folder(
                    root_key=browse_form.cleaned_data["root_key"],
                    relative_path=browse_form.cleaned_data["relative_path"]
                    or ".",
                    query=params.get("file_q", "")[:100],
                )
                locations = {
                    location.relative_path: location
                    for location in FileLocation.objects.filter(
                        storage_root_key=browse_form.cleaned_data["root_key"],
                        relative_path__in=[
                            item["path"]
                            for item in storage_entries
                            if not item["folder"]
                        ],
                        is_current=True,
                    ).select_related("asset", "asset__digitization_file")
                }
                for item in storage_entries:
                    location = locations.get(item["path"])
                    membership = (
                        getattr(location.asset, "digitization_file", None)
                        if location
                        else None
                    )
                    item["registered_here"] = bool(
                        membership and membership.batch_id == batch.pk
                    )
                    item["unavailable"] = bool(
                        membership and membership.batch_id != batch.pk
                    )
            except (ValidationError, OSError, ImproperlyConfigured) as exc:
                browse_error = "; ".join(getattr(exc, "messages", [str(exc)]))
    return browse_form, storage_entries, browse_error, active_role


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET"])
def browse_files(request, batch_id):
    """Render only one source picker; browser search does not reload the page."""
    batch = get_object_or_404(
        DigitizationBatch.objects.select_related("release__label"), pk=batch_id
    )
    role = request.GET.get("role")
    if role not in {
        FileAsset.Role.RAW_DIGITIZATION,
        FileAsset.Role.EDITED_WAV_MASTER,
    }:
        return HttpResponseBadRequest("Velg RAW eller redigert master.")
    browse_form, entries, error, active_role = _browse_storage(
        batch, request.GET
    )
    raw = role == FileAsset.Role.RAW_DIGITIZATION
    return render(
        request,
        "gui_v2/includes/digitization_file_picker.html",
        {
            "batch": batch,
            "picker_id": "raw-picker" if raw else "master-picker",
            "picker_role": role,
            "picker_heading": (
                "Finn RAW-filer" if raw else "Finn redigerte mastere"
            ),
            "picker_step": (
                "Rå digitalisering" if raw else "Redigerte mastere"
            ),
            "picker_description": (
                "Velg filer i RAW-området. Filene leses og registreres her; de flyttes eller endres ikke."
                if raw
                else "Velg ferdig redigerte WAV-mastere i masterområdet. Koble dem deretter til RAW-kilde og riktig spor i neste steg: Koble og velg mastere."
            ),
            "picker_default_root": (
                (
                    "raw_sources"
                    if "raw_sources" in settings.P7_STORAGE_ROOTS
                    else "music_library"
                )
                if raw
                else (
                    "edited_masters"
                    if "edited_masters" in settings.P7_STORAGE_ROOTS
                    else "music_library"
                )
            ),
            "picker_noun": "RAW-filer" if raw else "mastere",
            "source_roots": FolderForm().fields["root_key"].choices,
            "browse_form": browse_form,
            "active_role": active_role,
            "storage_browsed": bool(request.GET.get("browse")),
            "storage_entries": entries,
            "browse_error": error,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def detail(request, batch_id):
    batch = get_object_or_404(
        DigitizationBatch.objects.select_related("release", "release__label"),
        pk=batch_id,
    )
    if request.method == "GET":
        remember_object(request, "batch", batch.pk)
    folder_form = FolderForm()
    browse_form, storage_entries, browse_error, active_role = (
        _browse_storage(batch, request.GET)
        if request.method == "GET"
        else _browse_storage(batch, {})
    )
    plan = None
    error = ""
    if request.method == "POST":
        _write_access(request)
        operation = request.POST.get("operation")
        try:
            if operation == "finish":
                complete_digitization(batch=batch, user=request.user)
                messages.success(
                    request,
                    "Digitaliseringen er avsluttet. Radio-FLAC kan behandles senere.",
                )
                return redirect(
                    f"{reverse('gui_v2:digitization_detail', args=[batch.pk])}?step=radio"
                )
            if operation == "delete_batch":
                if request.POST.get("confirmed") != "yes":
                    raise ValidationError(
                        "Bekreft at digitaliseringsbatchen skal fjernes."
                    )
                release_title = batch.release.title
                delete_empty_batch(batch=batch, user=request.user)
                messages.success(
                    request,
                    f"Digitaliseringsbatchen ble fjernet. Utgivelsen {release_title} er beholdt.",
                )
                return redirect("gui_v2:digitization_index")
            if operation == "apply":
                plan = get_object_or_404(
                    DigitizationPlan,
                    pk=request.POST.get("plan"),
                    batch=batch,
                    created_by=request.user,
                )
                if request.POST.get("confirmed") != "yes":
                    raise ValidationError(
                        "Bekreft de viste endringene før de utføres."
                    )
                apply_plan(plan=plan, user=request.user)
                messages.success(
                    request,
                    "Alle viste endringer er lagret. Lydfilene er urørt.",
                )
                return redirect(
                    f"{reverse('gui_v2:digitization_detail', args=[batch.pk])}?applied=1&step="
                    + (
                        "radio"
                        if plan.operation == "select_master"
                        else (
                            "raw"
                            if plan.operation == "register"
                            and plan.payload["files"][0]["role"]
                            == FileAsset.Role.RAW_DIGITIZATION
                            else "links"
                        )
                    )
                )
            elif operation == "register":
                folder_form = FolderForm(request.POST)
                if folder_form.is_valid():
                    plan = preview_registration(
                        batch=batch,
                        user=request.user,
                        root_key=folder_form.cleaned_data["root_key"],
                        relative_path=folder_form.cleaned_data["relative_path"]
                        or ".",
                        role=folder_form.cleaned_data["role"],
                        selected_names=(
                            request.POST.getlist("filenames")
                            if request.POST.get("selection_mode") == "selected"
                            else None
                        ),
                    )
            else:
                assets = request.POST.getlist("assets")
                payload = {"assets": assets}
                if operation == "raw_link":
                    payload.update(
                        source=request.POST.get("source", ""),
                        note=request.POST.get("note", ""),
                    )
                elif operation in {"recording_link", "link_masters"}:
                    payload = {
                        "note": request.POST.get("note", ""),
                        "rows": [
                            {
                                "asset": pk,
                                "source": request.POST.get(f"source_{pk}", ""),
                                "track": request.POST.get(f"track_{pk}", ""),
                                "recording": request.POST.get(
                                    f"recording_{pk}", ""
                                ),
                                "new_title": request.POST.get(
                                    f"new_title_{pk}", ""
                                ),
                                "force_create": request.POST.get(f"force_{pk}")
                                == "on",
                            }
                            for pk in assets
                        ],
                    }
                plan = preview_operation(
                    batch=batch,
                    operation=operation,
                    payload=payload,
                    user=request.user,
                )
        except ObjectDoesNotExist:
            error = "En valgt fil, innspilling eller sporforekomst finnes ikke i arbeidsgrunnlaget. Velg på nytt."
            plan = None
        except (ValidationError, ValueError) as exc:
            error = (
                " · ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            plan = None
        except (OSError, ImproperlyConfigured, IntegrityError) as exc:
            logger.warning(
                "Digitization operation failed batch=%s type=%s",
                batch.pk,
                type(exc).__name__,
            )
            error = "Handlingen kunne ikke fullføres. Kontroller filtilgang og registrerte koblinger. Ingen deler av bulkhandlingen er lagret."
            plan = None
    workspace = batch_workspace(batch)
    # Keep unsaved choices visible when preview validation fails.
    if request.method == "POST" and error:
        for row in workspace["masters"]:
            suggestion = row["link_suggestion"]
            row["link_suggestion"] = replace(
                suggestion,
                track=next(
                    (
                        t
                        for t in workspace["tracks"]
                        if str(t.pk)
                        == request.POST.get(f"track_{row['asset'].pk}")
                    ),
                    suggestion.track,
                ),
                source=next(
                    (
                        r["asset"]
                        for r in workspace["raws"]
                        if str(r["asset"].pk)
                        == request.POST.get(f"source_{row['asset'].pk}")
                    ),
                    suggestion.source,
                ),
            )
    default_step = "raw" if workspace["tracks"] else "metadata"
    step = request.POST.get("step") or request.GET.get("step", default_step)
    if step not in {"metadata", "raw", "masters", "links", "radio", "history"}:
        step = default_step
    return render(
        request,
        "gui_v2/digitization_detail.html",
        {
            "section": "digitization",
            "batch": batch,
            "workspace": workspace,
            "step": step,
            "metadata_providers": provider_availability(),
            "folder_form": folder_form,
            "browse_form": browse_form,
            "source_roots": folder_form.fields["root_key"].choices,
            "raw_default_root": (
                "raw_sources"
                if "raw_sources" in settings.P7_STORAGE_ROOTS
                else "music_library"
            ),
            "master_default_root": (
                "edited_masters"
                if "edited_masters" in settings.P7_STORAGE_ROOTS
                else "music_library"
            ),
            "active_role": active_role,
            "storage_browsed": bool(request.GET.get("browse")),
            "storage_entries": storage_entries,
            "browse_error": browse_error,
            "plan": plan,
            "error": error,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )
