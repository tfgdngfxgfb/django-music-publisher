"""Native application settings. Host security settings remain deployment-owned."""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from media_assets.digitization_configuration import (
    picker_defaults,
    source_root_choices,
    validate_source_folder,
)
from media_assets.models import DigitizationConfiguration, FileAsset
from media_assets.storage import get_storage_root
from parties.models import Party
from rights.models import RightsConfiguration


class DigitizationSettingsForm(forms.ModelForm):
    class Meta:
        model = DigitizationConfiguration
        fields = (
            "raw_root_key",
            "raw_base_path",
            "raw_folder_template",
            "master_root_key",
            "master_base_path",
            "master_folder_template",
        )
        labels = {
            "raw_root_key": "Lagringsområde for rådigitalisering",
            "raw_base_path": "Startmappe for rådigitalisering",
            "raw_folder_template": "Undermappe for utgivelsen · RAW",
            "master_root_key": "Lagringsområde for redigerte mastere",
            "master_base_path": "Startmappe for redigerte mastere",
            "master_folder_template": "Undermappe for utgivelsen · master",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for prefix in ("raw", "master"):
            name = f"{prefix}_root_key"
            self.fields[name] = forms.ChoiceField(
                label=self.fields[name].label, choices=source_root_choices()
            )
            self.fields[f"{prefix}_base_path"].help_text = (
                "Mappe under lagringsroten, for eksempel Rå digitalisering/Klango. Bruk punktum (.) for hele området."
            )
            self.fields[f"{prefix}_folder_template"].help_text = (
                "Valgfri mappemal: {label}, {series}, {catalogue_number}, {title}. La stå tomt for å starte direkte i startmappen."
            )

    def clean(self):
        data = super().clean()
        for prefix in ("raw", "master"):
            key, path = data.get(f"{prefix}_root_key"), data.get(f"{prefix}_base_path")
            if key and path:
                try:
                    data[f"{prefix}_base_path"] = validate_source_folder(key, path)
                except (ValidationError, ImproperlyConfigured, OSError) as exc:
                    self.add_error(
                        f"{prefix}_base_path",
                        "; ".join(getattr(exc, "messages", [str(exc)])),
                    )
        return data


class OrganizationForm(forms.ModelForm):
    confirm_change = forms.BooleanField(
        required=False,
        label="Jeg forstår at endringen påvirker hvilken organisasjon rettigheter vurderes som lokale for.",
    )

    class Meta:
        model = RightsConfiguration
        fields = ("local_organization",)
        labels = {"local_organization": "Lokal P7-organisasjon"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local_organization"].queryset = Party.objects.filter(
            kind=Party.Kind.ORGANIZATION
        )

    def clean(self):
        data = super().clean()
        party = data.get("local_organization")
        if (
            party
            and self.instance.pk
            and not self.instance._state.adding
            and party.pk != self.instance.local_organization_id
            and not data.get("confirm_change")
        ):
            self.add_error(
                "confirm_change",
                "Bekreft konsekvensen før du bytter lokal organisasjon.",
            )
        return data


def _audit(user, obj, description):
    LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
        object_repr=str(obj)[:200],
        action_flag=CHANGE,
        change_message=description,
    )


def _storage_overview():
    rows = []
    for key, label in (
        ("music_library", "Musikkarkiv"),
        ("raw_sources", "Rå digitalisering"),
        ("edited_masters", "Redigerte mastere"),
        (
            getattr(settings, "P7_GENERATED_MEDIA_ROOT_KEY", "generated_media"),
            "Generert Radio-FLAC",
        ),
    ):
        try:
            root = get_storage_root(key)
        except (ImproperlyConfigured, ValidationError, OSError):
            rows.append({"label": label, "configured": False})
        else:
            rows.append(
                {
                    "label": label,
                    "configured": True,
                    "path": str(root.server_root),
                    "client_path": root.client_root,
                    "read_only": root.read_only,
                }
            )
    return rows


@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    section = request.GET.get("tab", "personal")
    if section not in {"personal", "digitization", "organization", "operation"}:
        section = "personal"
    is_admin = request.user.is_superuser
    if not is_admin:
        section = "personal"
    data = {
        "section": "settings",
        "settings_tab": section,
        "is_settings_admin": is_admin,
        "writes_enabled": settings.GUI_V2_WRITES_ENABLED,
    }
    if request.method == "POST" and (
        not is_admin or not settings.GUI_V2_WRITES_ENABLED
    ):
        raise PermissionDenied("Du har ikke tilgang til å endre fellesinnstillingene.")
    if is_admin:
        configuration = DigitizationConfiguration.objects.filter(pk=1).first()
        initial = {}
        for prefix, role in (
            ("raw", FileAsset.Role.RAW_DIGITIZATION),
            ("master", FileAsset.Role.EDITED_WAV_MASTER),
        ):
            defaults = picker_defaults(role, configuration)
            initial.update(
                {
                    f"{prefix}_root_key": defaults.root_key,
                    f"{prefix}_base_path": defaults.base_path,
                    f"{prefix}_folder_template": defaults.folder_template,
                }
            )
        action = request.POST.get("action") if request.method == "POST" else None
        if request.method == "POST" and action not in {"digitization", "organization"}:
            raise PermissionDenied("Ukjent innstillingshandling.")
        digitization_form = DigitizationSettingsForm(
            request.POST if action == "digitization" else None,
            instance=configuration,
            initial=initial,
        )
        organization = RightsConfiguration.objects.select_related(
            "local_organization"
        ).first()
        organization_name = str(organization.local_organization) if organization else ""
        organization_form = OrganizationForm(
            request.POST if action == "organization" else None, instance=organization
        )
        if action:
            form = digitization_form if action == "digitization" else organization_form
            data["settings_tab"] = action
            if form.is_valid():
                with transaction.atomic():
                    obj = form.save(commit=False)
                    if action == "digitization":
                        obj.pk = 1
                        obj.updated_by = request.user
                    obj.save()
                    _audit(
                        request.user,
                        obj,
                        "Endret fra Innstillinger i GUI v2: "
                        + ", ".join(form.changed_data),
                    )
                messages.success(
                    request, "Innstillingene er lagret og gjelder med en gang."
                )
                return redirect(reverse("gui_v2:settings") + "?tab=" + action)
        engine = settings.DATABASES["default"]["ENGINE"]
        data.update(
            digitization_form=digitization_form,
            organization_form=organization_form,
            organization=organization,
            organization_name=organization_name,
            storage_roots=_storage_overview(),
            database_type="PostgreSQL" if "postgresql" in engine else "SQLite",
            file_writes_enabled=settings.P7_ALLOW_FILE_WRITES,
            debug_enabled=settings.DEBUG,
            generated_relative_root=settings.P7_GENERATED_MEDIA_RELATIVE_ROOT,
            delivery_retention=settings.P7_DELIVERY_ARTIFACT_TTL_HOURS,
        )
    return render(request, "gui_v2/settings.html", data)
