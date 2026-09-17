"""GUI form adaptation; validation and mutations remain in Rights."""

from django import forms
from rights.forms import RightsClaimForm, ClaimAgreementForm


class RecordingClaimForm(RightsClaimForm):
    def __init__(self, *args, user, locked_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if locked_type:
            self.fields["right_type"].disabled = True
            self.initial["right_type"] = locked_type
        for field, permission in (
            ("agreement", "rights.view_agreement"),
            ("source_record", "provenance.view_sourcerecord"),
        ):
            if not user.has_perm(permission):
                current = self.initial.get(field)
                self.fields[field].queryset = self.fields[
                    field
                ].queryset.filter(pk=getattr(current, "pk", current))
                self.fields[field].disabled = True
                self.fields[field].widget = forms.HiddenInput()


class DocumentationForm(ClaimAgreementForm):
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["agreement"].disabled = not user.has_perms(
            ("rights.manage_agreement", "rights.view_agreement")
        )
        if not user.has_perm("rights.view_agreement"):
            self.fields["agreement"].queryset = self.fields[
                "agreement"
            ].queryset.none()
            self.initial.pop("agreement", None)


ReplacementFormSet = forms.formset_factory(
    RecordingClaimForm,
    extra=0,
    min_num=1,
    validate_min=True,
    max_num=20,
    validate_max=True,
    absolute_max=20,
    can_delete=True,
)
