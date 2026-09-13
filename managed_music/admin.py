from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from rights_core.admin import CanonicalAdmin

from .forms import ManagedRecordingCreationForm
from .models import ManagedRecording
from .services import create_managed_recording


@admin.register(ManagedRecording)
class ManagedRecordingAdmin(CanonicalAdmin):
    list_display = ("recording_title", "status", "source_system", "updated_at", "id")
    list_filter = ("status", "source_system")
    search_fields = (
        "library_entry__recording__title",
        "library_entry__recording__identifiers__normalized_value",
        "library_entry__recording__contributions__party__name",
        "id",
    )
    autocomplete_fields = ("library_entry", "source_system")

    @admin.display(
        description="Innspilling", ordering="library_entry__recording__title"
    )
    def recording_title(self, obj):
        return obj.recording.title

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("library_entry__recording", "source_system")
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return (*fields, "library_entry") if obj else tuple(fields)

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ManagedRecordingCreationForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            managed = create_managed_recording(**form.cleaned_data)
            self.message_user(
                request,
                f"«{managed.recording.title}» ble uttrykkelig lagt til i Forvaltet musikk.",
                messages.SUCCESS,
            )
            return redirect(
                reverse(
                    "admin:managed_music_managedrecording_change", args=(managed.pk,)
                )
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Legg til i Forvaltet musikk",
            "opts": self.model._meta,
            "form": form,
            "is_popup": False,
            "save_as": False,
            "has_view_permission": self.has_view_permission(request),
        }
        return TemplateResponse(
            request, "admin/managed_music/managedrecording/add_form.html", context
        )
