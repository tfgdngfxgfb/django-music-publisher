"""Small, session-scoped navigation history for the common GUI-v2 home."""

from datetime import datetime, timezone as datetime_timezone
from uuid import UUID

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Recording, Release
from delivery.models import Delivery
from media_assets.models import (
    DigitizationBatch,
    DigitizationFile,
    FileAsset,
)

RECENT_LIMIT = 8
RECENT_PERMISSIONS = {
    "batch": (
        "media_assets.view_digitizationbatch",
        "media_assets.view_fileasset",
        "media_assets.view_filelocation",
        "catalogue.view_release",
        "catalogue.view_recording",
    ),
    "recording": ("catalogue.view_recording",),
    "release": ("catalogue.view_release",),
    "delivery": ("delivery.view_delivery",),
}


def _session_key(request):
    return f"p7-v2-recent:{request.user.pk}"


def remember_object(request, kind, object_id):
    """Remember an opened object, never a search result or a global edit event."""
    if kind not in RECENT_PERMISSIONS or not request.user.has_perms(
        RECENT_PERMISSIONS[kind]
    ):
        return
    key = _session_key(request)
    item_id = str(object_id)
    history = [
        item
        for item in request.session.get(key, [])
        if isinstance(item, dict)
        and (item.get("kind"), item.get("id")) != (kind, item_id)
    ]
    request.session[key] = [
        {"kind": kind, "id": item_id, "seen": timezone.now().timestamp()},
        *history,
    ][:RECENT_LIMIT]


def recent_objects(request):
    """Resolve saved references anew so deleted objects or lost access disappear."""
    saved = request.session.get(_session_key(request), [])
    if not isinstance(saved, list):
        return []
    ids = {kind: [] for kind in RECENT_PERMISSIONS}
    valid = []
    for item in saved[:RECENT_LIMIT]:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind not in ids or not request.user.has_perms(
            RECENT_PERMISSIONS[kind]
        ):
            continue
        try:
            object_id = UUID(str(item.get("id")))
            seen = datetime.fromtimestamp(
                float(item.get("seen")), tz=datetime_timezone.utc
            )
        except (TypeError, ValueError, OverflowError):
            continue
        ids[kind].append(object_id)
        valid.append((kind, object_id, seen))
    batches = {
        item.pk: item
        for item in DigitizationBatch.objects.filter(pk__in=ids["batch"])
        .select_related("release")
        .annotate(file_count=Count("files"))
    }
    recordings = Recording.objects.in_bulk(ids["recording"])
    releases = {
        item.pk: item
        for item in Release.objects.filter(pk__in=ids["release"]).annotate(
            track_count=Count("tracks")
        )
    }
    deliveries = Delivery.objects.in_bulk(ids["delivery"])
    collections = {
        "batch": batches,
        "recording": recordings,
        "release": releases,
        "delivery": deliveries,
    }
    result = []
    for kind, object_id, seen in valid:
        item = collections[kind].get(object_id)
        if not item:
            continue
        if kind == "batch":
            title = item.title
            detail = f"{item.release.title} · {item.get_status_display()} · {item.file_count} filer"
            url = reverse("gui_v2:digitization_detail", args=[object_id])
            label = "Digitalisering"
            icon = "▤"
        elif kind == "recording":
            title = item.title
            detail = "Innspilling"
            url = reverse("gui_v2:recording_detail", args=[object_id])
            label = "Innspilling"
            icon = "♫"
        elif kind == "release":
            title = item.title
            detail = f"{item.catalogue_number or 'Uten katalognummer'} · {item.track_count} spor"
            url = reverse("gui_v2:release_detail", args=[object_id])
            label = "Utgivelse"
            icon = "▣"
        else:
            title = f"Leveranse {str(object_id)[:8]}"
            detail = (
                f"{item.get_status_display()} · {item.get_purpose_display()}"
            )
            url = reverse("delivery:detail", args=[object_id])
            label = "Leveranse"
            icon = "⇧"
        result.append(
            {
                "kind": kind,
                "title": title,
                "detail": detail,
                "url": url,
                "label": label,
                "icon": icon,
                "seen": seen,
            }
        )
    return result


def build_home_context(request):
    """Only cheap, permission-aware facts; never scan physical media on Hjem."""
    hour = timezone.localtime().hour
    greeting = (
        "God morgen" if hour < 10 else "God dag" if hour < 18 else "God kveld"
    )
    can_view_digitization = request.user.has_perms(RECENT_PERMISSIONS["batch"])
    workspaces = []
    for permission, title, description, icon, url_name in (
        (
            "music_library.view_musiclibraryentry",
            "Musikkarkiv",
            "Søk og arbeid med innspillinger.",
            "♫",
            "gui_v2:music_library",
        ),
        (
            "media_assets.view_digitizationbatch",
            "Digitalisering",
            "Råkilder, mastere og koblinger.",
            "▤",
            "gui_v2:digitization_index",
        ),
        (
            "catalogue.view_release",
            "Utgivelser",
            "Utgivelser og spor i katalogen.",
            "▣",
            "gui_v2:release_list",
        ),
        (
            "delivery.view_delivery",
            "Leveranser",
            "Følg opp og åpne leveranser.",
            "⇧",
            "delivery:history",
        ),
    ):
        allowed = (
            can_view_digitization
            if url_name == "gui_v2:digitization_index"
            else request.user.has_perm(permission)
        )
        if allowed:
            workspaces.append(
                {
                    "title": title,
                    "description": description,
                    "icon": icon,
                    "url": reverse(url_name),
                }
            )

    followups = []
    if can_view_digitization:
        open_count = DigitizationBatch.objects.filter(
            status=DigitizationBatch.Status.OPEN
        ).count()
        if open_count:
            followups.append(
                {
                    "count": open_count,
                    "title": "Digitaliseringer under arbeid",
                    "url": f"{reverse('gui_v2:digitization_index')}?status={DigitizationBatch.Status.OPEN}",
                    "tone": "info",
                }
            )
        unlinked_count = DigitizationFile.objects.filter(
            asset__role=FileAsset.Role.EDITED_WAV_MASTER,
            asset__recording__isnull=True,
        ).count()
        if unlinked_count:
            followups.append(
                {
                    "count": unlinked_count,
                    "title": "Redigerte mastere uten innspilling",
                    "url": f"{reverse('gui_v2:digitization_index')}?needs=recording",
                    "tone": "warning",
                }
            )
    if request.user.has_perm("delivery.view_delivery"):
        delivery_count = Delivery.objects.filter(
            status__in=(Delivery.Status.PARTIAL, Delivery.Status.FAILED)
        ).count()
        if delivery_count:
            followups.append(
                {
                    "count": delivery_count,
                    "title": "Leveranser med avvik",
                    "url": f"{reverse('delivery:history')}?status=attention",
                    "tone": "warning",
                }
            )

    recent = recent_objects(request)
    return {
        "section": "home",
        "greeting": greeting,
        "display_name": request.user.get_short_name()
        or request.user.get_username(),
        "workspaces": workspaces,
        "followups": followups,
        "continue_items": recent[:3],
        "recent_items": recent,
    }
