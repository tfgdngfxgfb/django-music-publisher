"""Music digitization workbench, consuming media services and existing GUI v2."""

import logging

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
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalogue.models import Label, Release
from media_assets.digitization import (
    apply_plan,
    preview_operation,
    preview_registration,
    require_operator,
    suggest_tracks,
)
from media_assets.models import (
    DigitizationBatch,
    DigitizationDerivation,
    DigitizationPlan,
    FileAsset,
    MediaAssetEvent,
    RecordingMediaSelection,
    RadioFlacGeneration,
)
from .forms import MasterRegistrationForm, ReleaseMetadataForm
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
        self.fields["relative_path"].label = "Mappe under lagringsroten"
        self.fields["relative_path"].help_text = (
            "Leser WAV-filene direkte i mappen. Lydredigering skjer utenfor P7."
        )


def _write_access(request):
    require_operator(request.user)
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied(
            "Endringer er deaktivert i dette GUI-v2-oppsettet."
        )


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
    selections = {
        item.recording_id: item
        for item in RecordingMediaSelection.objects.filter(
            recording_id__in=ids
        ).select_related("selected_master", "current_radio")
    }
    derivations = list(
        DigitizationDerivation.objects.filter(
            derived_asset_id__in=[asset.pk for asset in files]
        )
        .select_related("source_asset", "derived_asset")
        .order_by("-created_at")
    )
    active_sources = {
        item.derived_asset_id: item for item in derivations if item.is_active
    }
    generations = list(
        RadioFlacGeneration.objects.filter(recording_id__in=ids)
        .select_related("candidate_asset")
        .order_by("-created_at")
    )
    rows, raws = [], []
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
        }
        if asset.role == FileAsset.Role.RAW_DIGITIZATION:
            row["outputs"] = [
                item
                for item in derivations
                if item.source_asset_id == asset.pk and item.is_active
            ]
            raws.append(row)
        else:
            row["suggestion"] = suggest_tracks(asset, tracks)
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
        },
        "derivations": derivations,
    }


@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def index(request):
    query = request.GET.get("q", "").strip()
    batches = DigitizationBatch.objects.select_related(
        "release", "release__label", "created_by"
    )
    if query:
        batches = batches.filter(
            Q(title__icontains=query)
            | Q(release__title__icontains=query)
            | Q(release__catalogue_number__icontains=query)
        )
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
    # Fetch membership once per page, not once per displayed batch.
    members = list(
        FileAsset.objects.filter(
            digitization_file__batch__in=page.object_list
        ).values("digitization_file__batch_id", "role", "recording_id", "id")
    )
    selections = {
        item.recording_id: item
        for item in RecordingMediaSelection.objects.filter(
            recording_id__in=[
                m["recording_id"] for m in members if m["recording_id"]
            ]
        )
    }
    for batch in page.object_list:
        assets = [
            m for m in members if m["digitization_file__batch_id"] == batch.pk
        ]
        masters = [
            m for m in assets if m["role"] == FileAsset.Role.EDITED_WAV_MASTER
        ]
        batch.progress = {
            "raw": sum(
                m["role"] == FileAsset.Role.RAW_DIGITIZATION for m in assets
            ),
            "masters": len(masters),
            "linked": sum(bool(m["recording_id"]) for m in masters),
            "selected": sum(
                bool(
                    selections.get(m["recording_id"])
                    and selections[m["recording_id"]].selected_master_id
                    == m["id"]
                )
                for m in masters
            ),
            "radio": len(
                {
                    m["recording_id"]
                    for m in masters
                    if selections.get(m["recording_id"])
                    and selections[m["recording_id"]].current_radio_id
                }
            ),
        }
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
            "labels": Label.objects.all(),
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
    folder_form = FolderForm()
    plan = None
    error = ""
    if request.method == "POST":
        _write_access(request)
        operation = request.POST.get("operation")
        try:
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
                    "gui_v2:digitization_detail", batch_id=batch.pk
                )
            elif operation == "register":
                folder_form = FolderForm(request.POST)
                if folder_form.is_valid():
                    plan = preview_registration(
                        batch=batch,
                        user=request.user,
                        **folder_form.cleaned_data,
                    )
            else:
                assets = request.POST.getlist("assets")
                payload = {"assets": assets}
                if operation == "raw_link":
                    payload.update(
                        source=request.POST.get("source", ""),
                        note=request.POST.get("note", ""),
                    )
                elif operation == "recording_link":
                    payload = {
                        "rows": [
                            {
                                "asset": pk,
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
                        ]
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
    return render(
        request,
        "gui_v2/digitization_detail.html",
        {
            "section": "digitization",
            "batch": batch,
            "workspace": batch_workspace(batch),
            "folder_form": folder_form,
            "plan": plan,
            "error": error,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        },
    )
