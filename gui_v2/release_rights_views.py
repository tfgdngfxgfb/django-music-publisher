"""Native matrix and modal, using actor-authorized 6E preview/apply only."""

from urllib.parse import urlencode
from uuid import UUID
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from catalogue.models import Recording, Release
from managed_music.models import ManagedRelease, ManagedRecording
from music_library.models import MusicLibraryEntry
from rights import workflows
from rights.workflow_forms import process_release_form
from gui_v2.release_rights import release_tracks, build_matrix
from gui_v2.release_rights_forms import BulkRightsForm
from gui_v2.recording_overview import select_release_cover


def can_onboard(user):
    try:
        workflows.require_onboarding(user)
    except PermissionDenied:
        return False
    return True


@require_GET
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def overview(request, release_id):
    from gui_v2.views import _safe_return, _artist_text

    release = get_object_or_404(
        Release.objects.select_related("label"), pk=release_id
    )
    tracks = release_tracks(release)
    base = reverse("gui_v2:release_detail", args=[release.pk])
    return_url = _safe_return(request, reverse("gui_v2:release_list"))
    context = {
        "section": "releases",
        "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        "release": release,
        "active_tab": "rights",
        "return_url": return_url,
        "release_base_url": base,
        "release_artist_text": ", ".join(
            dict.fromkeys(
                filter(None, (_artist_text(t.recording) for t in tracks))
            )
        ),
        "can_view_release_management": request.user.has_perm(
            "managed_music.view_managedrelease"
        ),
        "managed_release": (
            ManagedRelease.objects.select_related("source_system")
            .filter(release=release)
            .first()
            if request.user.has_perm("managed_music.view_managedrelease")
            else None
        ),
        "can_register": settings.GUI_V2_WRITES_ENABLED
        and request.user.has_perms(
            ("rights.view_rightsclaim", "rights.add_rightsclaim")
        ),
        "bulk_url": reverse("gui_v2:release_rights_bulk", args=[release.pk]),
    }
    if request.user.is_staff and request.user.has_perms(
        (
            "media_assets.view_fileasset",
            "media_assets.view_filelocation",
            "music_library.view_musiclibraryentry",
        )
    ):
        context["release_cover"] = select_release_cover(release)
    if request.user.has_perm("rights.view_rightsclaim"):
        context.update(build_matrix(release, tracks))
        target = reverse("gui_v2:release_detail", args=[release.pk])
        for row in context["rows"]:
            back = (
                target
                + "?"
                + urlencode(
                    {
                        "tab": "rights",
                        "track": row["track"].pk,
                        "return": return_url,
                    }
                )
            )
            row["rights_url"] = (
                reverse("gui_v2:recording_rights", args=[row["recording"].pk])
                + "?"
                + urlencode({"return": back})
            )
    return render(request, "gui_v2/release_rights.html", context)


@require_http_methods(["GET", "POST"])
@login_required
@permission_required(
    (
        "catalogue.view_release",
        "rights.view_rightsclaim",
        "rights.add_rightsclaim",
    ),
    raise_exception=True,
)
def bulk(request, release_id):
    release = get_object_or_404(Release, pk=release_id)
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied("GUI v2 er skrivebeskyttet.")
    params = request.POST if request.method == "POST" else request.GET
    try:
        ids = {UUID(value) for value in params.getlist("recordings")}
        selected = list(
            Recording.objects.filter(pk__in=ids).order_by("title", "pk")
        )
    except (ValidationError, ValueError):
        selected = []
    # GET also validates membership in this Release; never display arbitrary UUIDs.
    valid_ids = set(
        release.tracks.filter(
            recording_id__in=[r.pk for r in selected]
        ).values_list("recording_id", flat=True)
    )
    if (
        not selected
        or len(selected) > 500
        or len(selected) != len(ids)
        or valid_ids != {r.pk for r in selected}
    ):
        return render(
            request,
            "gui_v2/includes/release_rights_bulk.html",
            {
                "selection_error": "Velg mellom 1 og 500 innspillinger som fortsatt tilhører utgivelsen."
            },
            status=400,
        )
    form = BulkRightsForm(
        request.POST if request.method == "POST" else None,
        release=release,
        user=request.user,
        selected=selected,
    )
    plan = None
    if (
        request.method == "POST"
        and form.is_valid()
        and params.get("stage") != "edit"
    ):
        try:
            plan, claims = process_release_form(
                form, user=request.user, apply=params.get("stage") == "apply"
            )
        except ValidationError as error:
            form.add_error(None, ValidationError(error.messages))
        else:
            if claims is not None:
                return JsonResponse({"applied": len(claims)})
    managed_ids = set(
        ManagedRecording.objects.filter(
            library_entry__recording__in=selected
        ).values_list("library_entry__recording_id", flat=True)
    )
    library_ids = set(
        MusicLibraryEntry.objects.filter(recording__in=selected).values_list(
            "recording_id", flat=True
        )
    )
    preview_values = []
    if plan:
        for name in (
            "right_type",
            "share",
            "legal_scope",
            "territory_mode",
            "territories",
            "valid_from",
            "valid_until",
            "grantor",
            "source_record",
            "agreement",
            "evidence_strength",
            "notes",
        ):
            value = form.cleaned_data.get(name)
            if value is None or value == "":
                continue
            if name == "territories":
                value = ", ".join(str(t) for t in value) or "—"
            elif name in (
                "right_type",
                "legal_scope",
                "territory_mode",
                "evidence_strength",
            ):
                value = dict(form.fields[name].choices).get(value, value)
            preview_values.append((form.fields[name].label, value))
    return render(
        request,
        "gui_v2/includes/release_rights_bulk.html",
        {
            "preview_values": preview_values,
            "release": release,
            "form": form,
            "plan": plan,
            "selected": selected,
            "unmanaged_count": len(selected) - len(managed_ids),
            "missing_library": [
                r for r in selected if r.pk not in library_ids
            ],
            "can_onboard": can_onboard(request.user),
            "bulk_url": reverse(
                "gui_v2:release_rights_bulk", args=[release.pk]
            ),
            "primary_fields": [
                form[name]
                for name in (
                    "right_type",
                    "share",
                    "legal_scope",
                    "territory_mode",
                    "territories",
                    "valid_from",
                    "valid_until",
                )
            ],
            "detail_fields": [
                form[name]
                for name in (
                    "grantor",
                    "source_record",
                    "agreement",
                    "evidence_strength",
                    "notes",
                )
            ],
        },
    )
