import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from media_assets.storage import StorageFileUnavailable, open_for_read
from gui_v2.home_state import remember_object

from .forms import DeliveryForm
from .models import (
    Delivery,
    DeliveryArtifact,
    DeliveryItem,
    DeliveryProfile,
    DownloadEvent,
)
from .services import (
    DeliveryPreviewStale,
    artifact_path,
    build_preview,
    confirm_preview,
)

logger = logging.getLogger(__name__)


@login_required
@permission_required("delivery.create_delivery", raise_exception=True)
@require_http_methods(["GET", "POST"])
def create(request):
    selected_ids = request.GET.getlist("recording") or request.POST.getlist("recording")
    preview = None
    form = DeliveryForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if "confirm" in request.POST:
            try:
                delivery = confirm_preview(
                    request.POST.get("preview_token", ""),
                    user=request.user,
                    allow_partial=request.POST.get("allow_partial") == "on",
                )
            except (ValidationError, DeliveryPreviewStale) as error:
                messages.error(request, "; ".join(error.messages))
            else:
                return redirect("delivery:detail", delivery_id=delivery.pk)
        elif form.is_valid():
            if form.cleaned_data[
                "profile"
            ] == DeliveryProfile.EXTERNAL_RADIO and not request.user.has_perm(
                "delivery.create_external_delivery"
            ):
                form.add_error(
                    "profile", "Du har ikke tilgang til ekstern leveranseprofil."
                )
            else:
                try:
                    preview = build_preview(
                        selected_ids, user=request.user, cleaned_data=form.cleaned_data
                    )
                except ValidationError as error:
                    form.add_error(None, "; ".join(error.messages))
    return render(
        request,
        "delivery/create.html",
        {
            "section": "delivery",
            "writes_enabled": True,
            "form": form,
            "selected_ids": selected_ids,
            "preview": preview,
        },
    )


@require_GET
@login_required
@permission_required("delivery.view_delivery", raise_exception=True)
def history(request):
    deliveries = (
        Delivery.objects.select_related("created_by").prefetch_related("items").all()
    )
    attention_only = request.GET.get("status") == "attention"
    if attention_only:
        deliveries = deliveries.filter(
            status__in=(Delivery.Status.PARTIAL, Delivery.Status.FAILED)
        )
    return render(
        request,
        "delivery/history.html",
        {
            "section": "delivery",
            "writes_enabled": True,
            "deliveries": deliveries,
            "attention_only": attention_only,
        },
    )


@require_GET
@login_required
@permission_required("delivery.view_delivery", raise_exception=True)
def detail(request, delivery_id):
    delivery = get_object_or_404(
        Delivery.objects.select_related("created_by").prefetch_related(
            "items__recording",
            "items__source_file_asset",
            "artifacts",
            "download_events__actor",
        ),
        pk=delivery_id,
    )
    remember_object(request, "delivery", delivery.pk)
    return render(
        request,
        "delivery/detail.html",
        {"section": "delivery", "writes_enabled": True, "delivery": delivery},
    )


@require_GET
@login_required
@permission_required("delivery.download_delivery", raise_exception=True)
def download(request, delivery_id):
    delivery = get_object_or_404(
        Delivery.objects.prefetch_related("items", "artifacts"), pk=delivery_id
    )
    DownloadEvent.objects.create(
        delivery=delivery,
        actor=request.user,
        event_type=DownloadEvent.EventType.REQUESTED,
    )
    artifact = next(iter(delivery.artifacts.filter(removed_at__isnull=True)), None)
    item = None
    try:
        if artifact:
            if artifact.expires_at <= timezone.now():
                raise Http404("Leveranseartefakten er utløpt.")
            path = artifact_path(artifact.relative_path)
            if not path.is_file():
                raise Http404("Leveranseartefakten finnes ikke.")
            stream = path.open("rb")
            filename = artifact.filename
            content_type = (
                "application/zip"
                if artifact.kind == DeliveryArtifact.Kind.ZIP
                else "audio/flac"
            )
        else:
            ready = list(
                delivery.items.filter(status=DeliveryItem.Status.READY).select_related(
                    "source_file_location", "source_file_asset"
                )
            )
            if delivery.profile != DeliveryProfile.INTERNAL_COMPLETE or len(ready) != 1:
                raise Http404("Leveransen har ikke et nedlastbart artefakt.")
            item = ready[0]
            stream = open_for_read(item.source_file_location)
            filename = item.output_filename
            content_type = "audio/flac"
    except (OSError, StorageFileUnavailable, ValidationError) as error:
        logger.warning(
            "Delivery download unavailable",
            extra={
                "delivery_id": str(delivery.pk),
                "delivery_item_id": str(item.pk) if item else None,
                "error_type": type(error).__name__,
            },
        )
        raise Http404("Leveransefilen er ikke tilgjengelig.") from error
    response = FileResponse(
        stream, as_attachment=True, filename=filename, content_type=content_type
    )
    response["X-Content-Type-Options"] = "nosniff"
    DownloadEvent.objects.create(
        delivery=delivery,
        delivery_item=item,
        actor=request.user,
        event_type=DownloadEvent.EventType.SERVED,
    )
    return response
