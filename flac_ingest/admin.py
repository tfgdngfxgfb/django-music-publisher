from django.contrib import admin

from rights_core.admin import CanonicalAdmin

from .models import FlacIngestBatch, FlacIngestItem, FlacMaintenanceJob, FlacSyncLog


@admin.register(FlacIngestBatch)
class FlacIngestBatchAdmin(CanonicalAdmin):
    list_display = (
        "relative_root",
        "status",
        "recursive",
        "allow_uuid_recovery",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "recursive", "allow_uuid_recovery")


@admin.register(FlacIngestItem)
class FlacIngestItemAdmin(CanonicalAdmin):
    list_display = (
        "relative_path",
        "action",
        "match_method",
        "recording",
        "reviewed_by",
        "reviewed_at",
        "applied_at",
    )
    list_filter = ("action", "match_method")
    search_fields = ("relative_path", "recording__title")
    readonly_fields = (
        "batch",
        "relative_path",
        "raw_tags",
        "parsed_metadata",
        "technical_metadata",
        "sha256",
        "source_record",
        "reviewed_by",
        "reviewed_at",
        "review_note",
    )


@admin.register(FlacSyncLog)
class FlacSyncLogAdmin(CanonicalAdmin):
    list_display = ("asset", "result", "created_at")
    list_filter = ("result",)
    readonly_fields = ("asset", "result", "written_tags", "protected_tags", "error")


@admin.register(FlacMaintenanceJob)
class FlacMaintenanceJobAdmin(CanonicalAdmin):
    list_display = (
        "kind",
        "status",
        "relative_root",
        "created_by",
        "executed_by",
        "created_at",
    )
    list_filter = ("kind", "status")
    readonly_fields = (
        "kind",
        "status",
        "relative_root",
        "plan",
        "result",
        "error",
        "created_by",
        "executed_by",
        "started_at",
        "completed_at",
    )
