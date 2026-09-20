"""GUI v2 radio workbench and explicit bulk generation preview."""

from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from catalogue.models import Recording
from media_assets.mastering import (
    build_generation_preview,
    create_generation_plan,
    generate_candidate,
)
from media_assets.pipeline_status import (
    get_recording_media_pipeline_status,
    radio_work_state,
)

from media_assets.pipeline_status import RadioWorkState

from .views import GENERATION_CHANGE_PERMISSIONS

from .radio_workbench import WORK_LABELS, radio_work_counts, radio_work_rows

VIEW_PERMS = (
    "catalogue.view_recording",
    "catalogue.view_release",
    "media_assets.view_fileasset",
    "media_assets.view_filelocation",
)
GENERATE_STATES = {
    RadioWorkState.MISSING_RADIO_FLAC,
    RadioWorkState.CURRENT_FROM_PREVIOUS_MASTER,
}
SIGNING_SALT = "gui-v2-radio-bulk-v1"


@require_GET
@login_required
@permission_required(VIEW_PERMS, raise_exception=True)
def index(request):
    query = request.GET.get("q", "").strip()[:120]
    selected_state = request.GET.get("status", "")
    if selected_state not in RadioWorkState._value2member_map_:
        selected_state = ""
    rows = radio_work_rows(query=query)
    counts = radio_work_counts(rows)
    filtered = (
        [row for row in rows if row["state"] == selected_state]
        if selected_state
        else rows
    )
    page = Paginator(filtered, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "gui_v2/radio_workbench.html",
        {
            "section": "radio_workbench",
            "query": query,
            "selected_state": selected_state,
            "filters": [
                {
                    "key": state.value,
                    "label": label,
                    "count": counts[state],
                }
                for state, label in WORK_LABELS.items()
            ],
            "total": len(rows),
            "page": page,
            "can_generate": settings.GUI_V2_WRITES_ENABLED
            and settings.P7_ALLOW_FILE_WRITES
            and request.user.has_perms(GENERATION_CHANGE_PERMISSIONS),
        },
    )


@require_POST
@login_required
@permission_required(GENERATION_CHANGE_PERMISSIONS, raise_exception=True)
def bulk(request):
    """Revalidate a signed preview before invoking the 4.5A generator."""
    if not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied("Medieendringer er deaktivert.")
    mode = request.POST.get("mode")
    error = ""
    rows = []
    results = []
    token = ""
    if mode == "preview":
        raw_ids = request.POST.getlist("recordings")
        try:
            ids = [UUID(value) for value in raw_ids]
            if not ids or len(ids) > 30 or len(set(ids)) != len(ids):
                raise ValidationError("Velg 1–30 ulike innspillinger.")
            recordings = {
                recording.pk: recording
                for recording in Recording.objects.filter(pk__in=ids)
            }
            if len(recordings) != len(ids):
                raise ValidationError("En valgt innspilling finnes ikke.")
            for pk in ids:
                recording = recordings[pk]
                facts = get_recording_media_pipeline_status(recording)
                if radio_work_state(facts) not in GENERATE_STATES:
                    raise ValidationError(
                        f"{recording.title}: Status tillater ikke bulkgenerering."
                    )
                preview = build_generation_preview(recording=recording)
                rows.append(
                    {
                        "recording": recording,
                        "master": preview["master"].filename,
                        "current": (
                            facts.current_radio.filename
                            if facts.current_radio
                            else "Ingen"
                        ),
                        "first_radio": preview["first_radio"],
                        "target": preview["target_relative_path"],
                        "snapshot": {
                            "recording": str(recording.pk),
                            "master": str(preview["master"].pk),
                            "current": (
                                str(facts.current_radio.pk)
                                if facts.current_radio
                                else ""
                            ),
                            "digest": preview["metadata_digest"],
                            "target": preview["target_relative_path"],
                        },
                    }
                )
            token = signing.dumps(
                {
                    "user": request.user.pk,
                    "rows": [row["snapshot"] for row in rows],
                },
                salt=SIGNING_SALT,
            )
        except (
            ValueError,
            ValidationError,
            OSError,
            ImproperlyConfigured,
        ) as exc:
            error = "; ".join(getattr(exc, "messages", [str(exc)]))
            rows = []
    elif mode == "apply":
        if not settings.P7_ALLOW_FILE_WRITES:
            raise PermissionDenied("Filskriving er deaktivert.")
        try:
            payload = signing.loads(
                request.POST.get("plan", ""),
                salt=SIGNING_SALT,
                max_age=15 * 60,
            )
            if payload.get("user") != request.user.pk:
                raise ValidationError(
                    "Forhåndsvisningen tilhører en annen bruker."
                )
            snapshots = payload.get("rows", [])
            if not snapshots or len(snapshots) > 30:
                raise ValidationError("Ugyldig forhåndsvisning.")
            validated = []
            for snapshot in snapshots:
                recording = Recording.objects.get(
                    pk=UUID(snapshot["recording"])
                )
                facts = get_recording_media_pipeline_status(recording)
                if radio_work_state(facts) not in GENERATE_STATES:
                    raise ValidationError(
                        f"{recording.title}: Arbeidsstatus er endret. Forhåndsvis på nytt."
                    )
                preview = build_generation_preview(recording=recording)
                if (
                    str(preview["master"].pk) != snapshot["master"]
                    or (
                        str(facts.current_radio.pk)
                        if facts.current_radio
                        else ""
                    )
                    != snapshot["current"]
                    or preview["metadata_digest"] != snapshot["digest"]
                    or preview["target_relative_path"] != snapshot["target"]
                ):
                    raise ValidationError(
                        f"{recording.title}: Master, radiofil eller metadata er endret. Forhåndsvis på nytt."
                    )
                if (
                    preview["first_radio"]
                    and request.POST.get(f"metadata_{recording.pk}") != "yes"
                ):
                    raise ValidationError(
                        f"{recording.title}: Bekreft kontroll av databaseopplysninger mot fysisk medium."
                    )
                validated.append((recording, preview))
            # Encoding writes physical files and cannot be one database transaction.
            # Every row is preflighted first; failures remain visible per row.
            for recording, preview in validated:
                try:
                    generation = create_generation_plan(
                        recording=recording,
                        user=request.user,
                        target_relative_path=preview["target_relative_path"],
                        database_metadata_confirmed=preview["first_radio"],
                        expected_metadata_digest=preview["metadata_digest"],
                    )
                    # The service handles verified plans idempotently and
                    # rejects failed/running plans; neither means success.
                    generate_candidate(
                        generation=generation, user=request.user
                    )
                    results.append(
                        {
                            "recording": recording,
                            "success": True,
                            "message": "Kandidat generert og verifisert",
                        }
                    )
                except (ValidationError, OSError, PermissionDenied) as exc:
                    results.append(
                        {
                            "recording": recording,
                            "success": False,
                            "message": "; ".join(
                                getattr(exc, "messages", [str(exc)])
                            ),
                        }
                    )
        except (
            signing.BadSignature,
            ValueError,
            KeyError,
            TypeError,
            ValidationError,
            Recording.DoesNotExist,
            OSError,
            ImproperlyConfigured,
        ) as exc:
            error = "; ".join(getattr(exc, "messages", [str(exc)]))
    else:
        error = "Velg forhåndsvisning eller bekreft en gyldig plan."
    return render(
        request,
        "gui_v2/radio_bulk.html",
        {
            "section": "radio_workbench",
            "rows": rows,
            "plan": token,
            "results": results,
            "error": error,
            "file_writes_enabled": settings.P7_ALLOW_FILE_WRITES,
        },
    )
