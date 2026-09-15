from django.contrib import admin

from rights_core.admin import CanonicalAdmin

from .models import FileAsset, FileChecksum, FileLocation

from .models import FileDerivation, MediaAssetEvent, RadioFlacGeneration, RecordingMediaSelection

class FileLocationInline(admin.TabularInline):
    model = FileLocation
    extra = 0
    fields = (
        "storage_type",
        "relative_path",
        "status",
        "is_current",
        "verification_status",
        "google_drive_id",
        "google_drive_url",
    )


@admin.register(FileAsset)
class FileAssetAdmin(CanonicalAdmin):
    list_display = (
        "filename",
        "role",
        "lifecycle_status",
        "mime_type",
        "size_bytes",
        "recording",
        "release",
        "release_track",
        "sync_status",
        "updated_at",
    )
    list_filter = ("role", "lifecycle_status", "mime_type", "sync_status")
    search_fields = (
        "filename",
        "sha256",
        "recording__title",
        "release__title",
        "locations__relative_path",
        "id",
    )
    autocomplete_fields = ("recording", "release", "release_track")
    inlines = (FileLocationInline,)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("recording", "release", "release_track")
        )


@admin.register(FileLocation)
class FileLocationAdmin(CanonicalAdmin):
    list_display = (
        "asset",
        "storage_type",
        "storage_root_key",
        "relative_path",
        "status",
        "is_current",
        "verification_status",
        "observed_at",
    )
    list_filter = (
        "storage_type",
        "status",
        "is_current",
        "verification_status",
    )
    search_fields = (
        "asset__filename",
        "relative_path",
        "google_drive_id",
        "id",
    )
    autocomplete_fields = ("asset",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("asset")


@admin.register(FileChecksum)
class FileChecksumAdmin(CanonicalAdmin):
    list_display = ("asset", "sha256", "reason", "observed_at")
    list_filter = ("reason",)
    search_fields = ("asset__filename", "sha256")
    autocomplete_fields = ("asset",)
    readonly_fields = ("asset", "sha256", "reason", "observed_at")
@admin.register(RecordingMediaSelection)
class RecordingMediaSelectionAdmin(CanonicalAdmin):
    list_display = (
        "recording",
        "selected_master",
        "current_radio",
        "updated_at",
    )
    autocomplete_fields = (
        "recording",
        "selected_master",
        "current_radio",
        "selected_master_by",
        "current_radio_by",
    )

@admin.register(FileDerivation)
class FileDerivationAdmin(CanonicalAdmin):
    list_display = (
        "source_asset",
        "relation_type",
        "derived_asset",
        "created_at",
    )
    autocomplete_fields = ("source_asset", "derived_asset", "created_by")

@admin.register(RadioFlacGeneration)
class RadioFlacGenerationAdmin(CanonicalAdmin):
    list_display = (
        "recording",
        "master_asset",
        "candidate_asset",
        "status",
        "created_at",
    )
    list_filter = ("status",)
    autocomplete_fields = (
        "recording",
        "master_asset",
        "radio_metadata_source",
        "candidate_asset",
        "created_by",
        "activated_by",
    )

@admin.register(MediaAssetEvent)
class MediaAssetEventAdmin(CanonicalAdmin):
    list_display = ("event_type", "recording", "asset", "actor", "created_at")
    list_filter = ("event_type",)
    autocomplete_fields = ("recording", "asset", "related_asset", "actor")
