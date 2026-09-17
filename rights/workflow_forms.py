"""Shared preview/apply adapter for existing Workbench and GUI v2 forms."""

from .workflows import (
    apply_release_rights_registration,
    preview_release_rights_registration,
)


def process_release_form(form, *, user, apply=False):
    values = form.registration_values()
    if apply:
        claims = apply_release_rights_registration(
            preview_token=form.cleaned_data["preview_token"],
            user=user,
            release=form.release,
            **values,
        )
        return None, claims
    plan = preview_release_rights_registration(
        user=user, release=form.release, **values
    )
    data = form.data.copy()
    data[form.add_prefix("preview_token")] = plan.token
    form.data = data
    return plan, None
