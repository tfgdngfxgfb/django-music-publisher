"""Native Rights screens. All mutations go through authorized 6E workflows."""

from urllib.parse import urlencode
from django import forms

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from managed_music.models import ManagedRecording
from rights import workflows
from rights.forms import RightsDecisionForm
from rights.models import AgreementDocument, RightsClaim
from rights_core.models import VerificationStatus as Status
from gui_v2.recording_rights import (
    build_recording_rights,
    claims_queryset,
    claim_presentation,
)
from gui_v2.rights_forms import (
    RecordingClaimForm,
    DocumentationForm,
    ReplacementFormSet,
)
from gui_v2.views import (
    _recording_header_recording,
    _recording_header_data,
    _recording_workspace_context,
)

READ_PERMISSIONS = ("catalogue.view_recording", "rights.view_rightsclaim")
ACTION_PERMISSIONS = {
    "register": ("rights.add_rightsclaim",),
    "document": ("rights.change_rightsclaim",),
    "decide": ("rights.decide_rightsclaim",),
    "replace": ("rights.add_rightsclaim", "rights.decide_rightsclaim"),
}


def _context(request, recording_id):
    recording = _recording_header_recording(recording_id)
    context = _recording_workspace_context(
        request,
        recording,
        _recording_header_data(request, recording),
        "rights",
    )
    context["writes_enabled"] = settings.GUI_V2_WRITES_ENABLED
    return context


def _url(name, context, claim=None):
    args = [context["recording"].pk]
    if claim:
        args.append(claim.pk)
    return (
        reverse("gui_v2:" + name, args=args)
        + "?"
        + urlencode({"return": context["return_url"]})
    )


def _claim_context(request, context, claim):
    context["position"] = claim_presentation(claim)
    context["claim"] = claim
    context["claim_url"] = _url("recording_rights_claim", context, claim)
    context["action_urls"] = {
        action: _url("recording_rights_" + action, context, claim)
        for action in ("document", "decide", "replace")
        if settings.GUI_V2_WRITES_ENABLED
        and request.user.has_perms(ACTION_PERMISSIONS[action])
        and (action == "document" or claim.status != Status.SUPERSEDED)
    }


@require_GET
@login_required
@permission_required(READ_PERMISSIONS, raise_exception=True)
def overview(request, recording_id):
    context = _context(request, recording_id)
    context.update(build_recording_rights(context["recording"]))
    for group in context["claim_groups"]:
        for row in group["rows"]:
            row["url"] = _url("recording_rights_claim", context, row["claim"])
    if (
        context["managed"]
        and settings.GUI_V2_WRITES_ENABLED
        and request.user.has_perms(ACTION_PERMISSIONS["register"])
    ):
        context["register_url"] = _url("recording_rights_register", context)
    return render(request, "gui_v2/recording_rights.html", context)


@require_GET
@login_required
@permission_required(READ_PERMISSIONS, raise_exception=True)
def detail(request, recording_id, claim_id):
    context = _context(request, recording_id)
    claim = get_object_or_404(
        claims_queryset(context["recording"]), pk=claim_id
    )
    _claim_context(request, context, claim)
    context["replacements"] = [
        {"claim": item, "url": _url("recording_rights_claim", context, item)}
        for item in claim.superseded_by.order_by("created_at", "id")
    ]
    if claim.supersedes_id:
        context["previous_url"] = _url(
            "recording_rights_claim", context, claim.supersedes
        )
    if claim.agreement_id and request.user.has_perms(
        (
            "rights.view_agreement",
            "rights.view_agreementdocument",
            "media_assets.view_fileasset",
        )
    ):
        context["documents"] = AgreementDocument.objects.filter(
            agreement_id=claim.agreement_id
        ).select_related("file_asset")
    return render(request, "gui_v2/recording_rights_claim.html", context)


@login_required
@permission_required(READ_PERMISSIONS, raise_exception=True)
@require_http_methods(["GET", "POST"])
def action(request, recording_id, action, claim_id=None):
    if not request.user.has_perms(ACTION_PERMISSIONS[action]):
        raise PermissionDenied
    if request.method == "POST" and not settings.GUI_V2_WRITES_ENABLED:
        raise PermissionDenied
    context = _context(request, recording_id)
    recording = context["recording"]
    claim = (
        get_object_or_404(claims_queryset(recording), pk=claim_id)
        if claim_id
        else None
    )
    if claim:
        _claim_context(request, context, claim)
    if request.method == "GET":
        if (
            action == "register"
            and not ManagedRecording.objects.filter(
                library_entry__recording=recording
            ).exists()
        ):
            return redirect(_url("recording_rights", context))
        if (
            claim
            and claim.status == Status.SUPERSEDED
            and action != "document"
        ):
            return redirect(_url("recording_rights_claim", context, claim))
    data = request.POST if request.method == "POST" else None
    kwargs = {"recording": recording, "user": request.user}
    formset = None
    if action == "register":
        form = RecordingClaimForm(data, **kwargs)
    elif action == "document":
        form = DocumentationForm(
            data,
            user=request.user,
            initial={
                "agreement": claim.agreement_id,
                "evidence_strength": claim.evidence_strength,
            },
        )
    elif action == "decide":
        form = RightsDecisionForm(
            data, initial={"action": request.GET.get("decision", "")}
        )
        allowed = {
            "confirm": Status.CONFIRMED,
            "dispute": Status.DISPUTED,
            "reject": Status.REJECTED,
        }
        available = {a["value"] for a in context["position"]["decisions"]}
        form.fields["action"].widget = forms.RadioSelect()
        form.fields["action"].choices = [
            (key, label)
            for key, label in (
                ("confirm", "Bekreft"),
                ("dispute", "Bestrid"),
                ("reject", "Avvis"),
            )
            if allowed[key] in available
        ]
    else:
        initial = {
            name: getattr(claim, name)
            for name in RecordingClaimForm.Meta.fields
            if name != "territories"
        }
        initial["territories"] = list(claim.territories.all())
        formset = ReplacementFormSet(
            data,
            initial=[initial],
            prefix="positions",
            form_kwargs={**kwargs, "locked_type": claim.right_type},
        )
        form = None
    error = ""
    if data is not None and (
        formset.is_valid() if formset is not None else form.is_valid()
    ):
        try:
            if action == "register":
                claim = workflows.register_rights_claim(
                    user=request.user, recording=recording, **form.cleaned_data
                )
            elif action == "document":
                values = dict(form.cleaned_data)
                if form.fields["agreement"].disabled:
                    values.pop("agreement", None)
                workflows.document_rights_claim(
                    claim, user=request.user, **values
                )
            elif action == "decide":
                workflows.decide_rights_claim(
                    claim,
                    allowed[form.cleaned_data["action"]],
                    user=request.user,
                    note=form.cleaned_data["note"],
                )
            else:
                if request.POST.get("confirm_replace") != "yes":
                    raise ValidationError(
                        "Bekreft at det opprinnelige kravet blir permanent erstattet."
                    )
                replacements = [
                    {
                        key: value
                        for key, value in row.items()
                        if key != "DELETE"
                    }
                    for row in formset.cleaned_data
                    if row and not row.get("DELETE")
                ]
                workflows.supersede_rights_claims(
                    claim,
                    user=request.user,
                    replacements=replacements,
                    note=request.POST.get("note", ""),
                )
            messages.success(
                request, "Rettighetsendringen er registrert med revisjonsspor."
            )
            return redirect(_url("recording_rights_claim", context, claim))
        except ValidationError as exc:
            error = " ".join(exc.messages)
    context.update(
        {
            "action": action,
            "action_title": {
                "register": "Registrer rettighetsposisjon",
                "document": "Dokumenter rettighetskrav",
                "decide": "Vurder rettighetskrav",
                "replace": "Erstatt / splitt rettighetskrav",
            }[action],
            "form": form,
            "formset": formset,
            "action_error": error,
            "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
        }
    )
    return render(request, "gui_v2/recording_rights_action.html", context)
