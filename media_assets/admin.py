from django.contrib import admin

from rights_core.admin import CanonicalAdmin

from .models import FileAsset, FileLocation


class FileLocationInline(admin.TabularInline):
    model = FileLocation
    extra = 0
    fields = (
        "storage_type",
        "relative_path",
        "status",
        "is_current",
        "google_drive_id",
        "google_drive_url",
    )


@admin.register(FileAsset)
class FileAssetAdmin(CanonicalAdmin):
    list_display = (
        "filename",
        "role",
        "mime_type",
        "size_bytes",
        "recording",
        "release",
        "updated_at",
    )
    list_filter = ("role", "mime_type")
    search_fields = (
        "filename",
        "sha256",
        "recording__title",
        "release__title",
        "locations__relative_path",
        "id",
    )
    autocomplete_fields = ("recording", "release")
    inlines = (FileLocationInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("recording", "release")


@admin.register(FileLocation)
class FileLocationAdmin(CanonicalAdmin):
    list_display = (
        "asset",
        "storage_type",
        "relative_path",
        "status",
        "is_current",
        "observed_at",
    )
    list_filter = ("storage_type", "status", "is_current")
    search_fields = ("asset__filename", "relative_path", "google_drive_id", "id")
    autocomplete_fields = ("asset",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("asset")
