from django.contrib import admin

from rights_core.admin import CanonicalAdmin

from .models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
    MusicLibraryTargetAudience,
    TargetAudience,
)


class ChannelInline(admin.TabularInline):
    model = MusicLibraryChannel
    extra = 0


class TargetAudienceInline(admin.TabularInline):
    model = MusicLibraryTargetAudience
    extra = 0


class ManagedStatusFilter(admin.SimpleListFilter):
    title = "forvaltet musikk"
    parameter_name = "managed"

    def lookups(self, request, model_admin):
        return (("yes", "Ja"), ("no", "Nei"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(managed_recording__isnull=False)
        if self.value() == "no":
            return queryset.filter(managed_recording__isnull=True)
        return queryset


@admin.register(MusicLibraryEntry)
class MusicLibraryEntryAdmin(CanonicalAdmin):
    list_display = (
        "recording",
        "genre",
        "language",
        "energy",
        "rotation_suitability",
        "verification_status",
        "is_managed",
    )
    list_filter = (
        ManagedStatusFilter,
        "verification_status",
        "genre",
        "language",
        "gender",
        "energy",
        "rotation_suitability",
    )
    search_fields = (
        "recording__title",
        "recording__identifiers__normalized_value",
        "recording__contributions__party__name",
        "recording__contributions__artist_identity__display_name",
        "id",
    )
    autocomplete_fields = ("recording",)
    inlines = (ChannelInline, TargetAudienceInline)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return (*fields, "recording") if obj else tuple(fields)

    @admin.display(boolean=True, description="Forvaltet")
    def is_managed(self, obj):
        return hasattr(obj, "managed_recording")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("recording", "managed_recording")
            .prefetch_related("channels", "target_audiences")
        )


@admin.register(Channel)
class ChannelAdmin(CanonicalAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "id")


@admin.register(TargetAudience)
class TargetAudienceAdmin(CanonicalAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "id")
