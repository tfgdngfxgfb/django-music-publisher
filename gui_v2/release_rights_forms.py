"""GUI adapter over the signed 6E registration form; no mutation here."""

from django import forms
from rights.forms import ReleaseRightsClaimForm
from rights.models import RightsClaim
from rights.services import get_local_organization
from gui_v2.catalogue_sources import catalogue_sources


class BulkRightsForm(ReleaseRightsClaimForm):
    def __init__(self, *args, user, selected=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.local = get_local_organization()
        self.fields["rights_holder"].disabled = True
        self.fields["rights_holder"].widget = forms.HiddenInput()
        self.initial["rights_holder"] = self.local
        self.fields["recordings"].widget = forms.MultipleHiddenInput()
        self.initial["recordings"] = selected
        self.initial["share"] = 100
        self.fields["share"].help_text = (
            "Standard er 100 %. Endre dersom P7 eier en lavere andel av alle valgte mastere. Bruk separate batcher ved ulike andeler."
        )
        self.fields["legal_scope"].choices = (
            ("", "Velg uttrykkelig …"),
            ("general", "Innspillingene generelt"),
            ("release", "Bare denne utgivelsen"),
        )
        self.fields["legal_scope"].label = "Rettigheten gjelder"
        self.initial["legal_scope"] = ""
        self.fields["source_record"].widget = forms.TextInput()
        self.fields["source_record"].help_text = (
            "UUID til en eksisterende kildepost. Ingen ny kildepost opprettes."
        )
        sources = catalogue_sources([r.pk for r in selected])
        source_ids = {
            sources[r.pk].pk if r.pk in sources else None for r in selected
        }
        self.default_source = (
            next(iter(sources.values()))
            if len(source_ids) == 1 and None not in source_ids
            else None
        )
        if self.default_source:
            self.initial["source_record"] = self.default_source
        if not user.has_perm("provenance.view_sourcerecord"):
            self.fields["source_record"].queryset = self.fields[
                "source_record"
            ].queryset.none()
            self.initial["source_record"] = None
            self.default_source = None
        if not user.has_perm("rights.view_agreement"):
            self.fields["agreement"].queryset = self.fields[
                "agreement"
            ].queryset.none()
        self.fields["allow_managed_registration"].label = (
            "Tillat onboarding av valgte innspillinger som ikke er i Forvaltet musikk"
        )

    def clean(self):
        data = super().clean()
        kind, scope = data.get("right_type"), data.get("legal_scope")
        if kind == RightsClaim.RightType.OWNERSHIP:
            if scope == "release":
                self.add_error(
                    "legal_scope",
                    "Mastereierskap kan ikke være utgivelsesavgrenset.",
                )
            if data.get("share") is None:
                self.add_error("share", "Angi lokal eierandel.")
            data["legal_scope"] = "general"
        elif not scope:
            self.add_error(
                "legal_scope",
                "Velg om retten gjelder generelt eller bare denne utgivelsen.",
            )
        return data
