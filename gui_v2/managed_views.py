"""Native management workspace over authorized 6E and explicit 6D services."""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from catalogue.models import Recording
from managed_music.services import return_to_music_library
from rights import workflows
from gui_v2.managed_forms import ManagedFilterForm, OnboardingForm, ReasonForm
from gui_v2.managed_music import filtered_rows, memberships, present_batch
from gui_v2.views import (
    _recording_header_data,
    _safe_return,
    _playback_context,
)

READ = ("managed_music.view_managedrecording", "catalogue.view_recording")


def can_onboard(user):
    return user.is_superuser and user.has_perms(
        ("managed_music.add_managedrecording", "rights.add_rightsclaim")
    )


def can_correct(user):
    return user.is_superuser and user.has_perms(
        ("managed_music.change_managedrecording", "rights.decide_rightsclaim")
    )


def context(request):
    return {
        "section": "managed",
        "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        "can_onboard": can_onboard(request.user),
        "can_view_rights": request.user.has_perm("rights.view_rightsclaim"),
        "return_url": _safe_return(request, reverse("gui_v2:managed_music")),
    }


def attach_urls(request, row, return_url):
    recording_id = row["recording"].pk
    suffix = "?" + urlencode({"return": return_url})
    row["recording_url"] = (
        reverse("gui_v2:recording_detail", args=[recording_id]) + suffix
    )
    row["rights_url"] = (
        reverse("gui_v2:recording_rights", args=[recording_id]) + suffix
    )
    state = row["state"]
    if settings.GUI_V2_WRITES_ENABLED and state:
        if state.history_uncertain and can_correct(request.user):
            row["correct_url"] = (
                reverse("gui_v2:managed_correct", args=[row["managed"].pk])
                + suffix
            )
        if state.can_return_to_music_library and request.user.has_perm(
            "managed_music.delete_managedrecording"
        ):
            row["return_action_url"] = (
                reverse("gui_v2:managed_return", args=[row["managed"].pk])
                + suffix
            )


@require_GET
@login_required
@permission_required(READ, raise_exception=True)
def index(request):
    data = context(request)
    form = ManagedFilterForm(
        request.GET, can_view_rights=data["can_view_rights"]
    )
    valid = form.is_valid()
    try:
        page_number = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page_number = 1
    # Scan derived predicates before slicing, retaining only two page buffers.
    page_size = 25
    count, page_rows, last_page = 0, [], []
    for row in (
        filtered_rows(
            form.cleaned_data, can_view_rights=data["can_view_rights"]
        )
        if valid
        else ()
    ):
        if count % page_size == 0:
            last_page = []
        last_page.append(row)
        if (page_number - 1) * page_size <= count < page_number * page_size:
            page_rows.append(row)
        count += 1
    page = Paginator(range(count), page_size).get_page(page_number)
    page.object_list = page_rows if page.number == page_number else last_page
    query = request.GET.copy()
    query.pop("selected", None)
    query.pop("page", None)
    list_return = request.get_full_path()
    for row in page.object_list:
        select_query = request.GET.copy()
        select_query["selected"] = str(row["managed"].pk)
        row["select_url"] = "?" + select_query.urlencode()
        attach_urls(request, row, list_return)
    selected = next(
        (
            r
            for r in page.object_list
            if str(r["managed"].pk) == request.GET.get("selected")
        ),
        None,
    )
    if selected is None and page.object_list:
        selected = page.object_list[0]
    if selected:
        selected["header"] = _recording_header_data(
            request, selected["recording"]
        )
        selected["playback"] = _playback_context(
            selected["recording"], request.user
        )
    data.update(
        form=form, page=page, selected=selected, page_query=query.urlencode()
    )
    return render(request, "gui_v2/managed_music.html", data)


@login_required
@permission_required(READ, raise_exception=True)
@require_http_methods(["GET", "POST"])
def onboard(request):
    workflows.require_onboarding(request.user)
    if request.method == "POST" and not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied
    data = context(request)
    form = OnboardingForm(
        request.POST if request.method == "POST" else None,
        initial={"recording": request.GET.get("recording")},
    )
    if request.method == "POST" and form.is_valid():
        try:
            managed = workflows.onboard_managed_recording(
                user=request.user, **form.cleaned_data
            )
        except (ValidationError, ValueError, IntegrityError) as error:
            form.add_error(
                None,
                (
                    " ".join(error.messages)
                    if isinstance(error, ValidationError)
                    else str(error)
                ),
            )
        else:
            messages.success(
                request,
                f"«{managed.recording.title}» er registrert i Forvaltet musikk under vurdering. Rettighetsgrunnlaget er uverifisert.",
            )
            if data["return_url"] != reverse("gui_v2:managed_music"):
                return redirect(data["return_url"])
            return redirect(
                reverse("gui_v2:managed_music")
                + "?"
                + urlencode(
                    {"selected": managed.pk, "q": managed.recording.title}
                )
            )
    chosen = form.fields["recording"].queryset.first()
    if chosen:
        data["chosen"] = chosen
        data["header"] = _recording_header_data(request, chosen)
        if request.user.has_perm("catalogue.view_release"):
            data["releases"] = form.fields["release_scope"].queryset
    term = request.GET.get("q", "").strip()
    if term:
        candidates = (
            Recording.objects.filter(music_library_entry__isnull=False)
            .filter(
                Q(title__icontains=term)
                | Q(identifiers__normalized_value__icontains=term)
                | Q(contributions__credited_as__icontains=term)
                | Q(contributions__party__name__icontains=term)
                | Q(
                    contributions__artist_identity__display_name__icontains=term
                )
            )
            .distinct()
            .order_by("title", "pk")[:20]
        )
        data["candidates"] = [
            {
                "recording": r,
                "url": "?"
                + urlencode({"recording": r.pk, "return": data["return_url"]}),
            }
            for r in candidates
        ]
    groups = (
        (
            "2. Forvaltningsgrunnlag",
            (
                "relationship_type",
                "ownership_share",
                "notes",
            ),
        ),
        (
            "Område, periode og utgivelsesavgrensning",
            (
                "grantor",
                "territory_mode",
                "territories",
                "valid_from",
                "valid_until",
                "release_scope",
            ),
        ),
        (
            "Kilde og dokumentasjon",
            (
                "source_system",
                "source_record",
                "agreement",
                "evidence_strength",
            ),
        ),
    )
    data.update(
        form=form,
        groups=[
            (label, [form[name] for name in names]) for label, names in groups
        ],
    )
    return render(request, "gui_v2/managed_onboard.html", data)


@login_required
@permission_required(READ, raise_exception=True)
@require_http_methods(["GET", "POST"])
def action(request, managed_id, action):
    if action == "correct":
        if not can_correct(request.user):
            raise PermissionDenied
    elif not request.user.has_perm("managed_music.delete_managedrecording"):
        raise PermissionDenied
    if request.method == "POST" and not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied
    data = context(request)
    managed = get_object_or_404(memberships({}), pk=managed_id)
    row = present_batch([managed], can_view_rights=data["can_view_rights"])[0]
    attach_urls(request, row, data["return_url"])
    state = row["state"]
    eligible = bool(
        state
        and (
            state.history_uncertain
            if action == "correct"
            else state.can_return_to_music_library
        )
    )
    form = ReasonForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            # Services re-read and validate under their established locks.
            if action == "correct":
                workflows.correct_legacy_management_history(
                    managed,
                    user=request.user,
                    reason=form.cleaned_data["reason"],
                )
            else:
                return_to_music_library(
                    managed,
                    user=request.user,
                    reason=form.cleaned_data["reason"],
                )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                (
                    "Legacy-status er korrigert. Medlemskapet er beholdt."
                    if action == "correct"
                    else "Tilknytningen til Forvaltet musikk er fjernet. Innspilling og historikk er beholdt."
                ),
            )
            return redirect(data["return_url"])
    row["header"] = _recording_header_data(request, row["recording"])
    data.update(
        selected=row,
        form=form,
        eligible=eligible,
        action=action,
        action_title=(
            "Avklar feil legacy-status"
            if action == "correct"
            else "Tilbakefør til Musikkarkiv"
        ),
    )
    return render(request, "gui_v2/managed_action.html", data)
