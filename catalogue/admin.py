from django.contrib import admin

from rights_core.admin import CanonicalAdmin
from .models import ExternalIdentifier, Recording, RecordingContribution


class ContributionInline(admin.TabularInline):
    model = RecordingContribution
    extra = 0
    autocomplete_fields = ("party", "artist_identity")
    fields = (
        "party",
        "role",
        "artist_identity",
        "credited_as",
        "display_order",
    )


class IdentifierInline(admin.TabularInline):
    model = ExternalIdentifier
    extra = 0
    fields = ("scheme", "value", "normalized_value")
    readonly_fields = ("normalized_value",)


@admin.register(Recording)
class RecordingAdmin(CanonicalAdmin):
    list_display = (
        "title",
        "version_designation",
        "metadata_status",
        "id",
        "updated_at",
    )
    search_fields = (
        "title",
        "version_designation",
        "id",
        "identifiers__normalized_value",
    )
    list_filter = ("metadata_status", "recording_kind")
    inlines = (ContributionInline, IdentifierInline)
    fieldsets = (
        (
            None,
            {"fields": ("title", "version_designation", "metadata_status")},
        ),
        (
            "Valgfrie metadata",
            {"fields": ("recording_kind", "duration_ms", "language")},
        ),
        (
            "Identitet og historikk",
            {"fields": ("id", "created_at", "updated_at", "revision")},
        ),
    )

@admin.register(RecordingContribution)
class ContributionAdmin(CanonicalAdmin):
    list_display = (
        "recording",
        "party",
        "role",
        "credited_as",
        "display_order",
    )
    list_filter = ("role",)
    search_fields = ("recording__title", "party__name", "credited_as")
    autocomplete_fields = ("recording", "party", "artist_identity")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("recording", "party")


@admin.register(ExternalIdentifier)
class IdentifierAdmin(CanonicalAdmin):
    list_display = ("scheme", "normalized_value", "recording", "id")
    search_fields = ("value", "normalized_value", "recording__title")
    list_filter = ("scheme",)
    autocomplete_fields = ("recording",)
    readonly_fields = (*CanonicalAdmin.readonly_fields, "normalized_value")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("recording")
