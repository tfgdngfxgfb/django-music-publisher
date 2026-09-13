from django.contrib import admin

from rights_core.admin import CanonicalAdmin
from rights_core.models import VerificationStatus

from .models import (
    AppliedMetadataChange,
    AssertionDecision,
    ImportBatch,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from .services import decide_assertion, supersede_assertion


@admin.register(SourceSystem)
class SourceSystemAdmin(CanonicalAdmin):
    list_display = ("name", "kind", "updated_at")
    list_filter = ("kind",)
    search_fields = ("name", "description", "id")


@admin.register(ImportBatch)
class ImportBatchAdmin(CanonicalAdmin):
    list_display = ("source_system", "external_batch_id", "imported_at")
    list_filter = ("source_system",)
    search_fields = ("external_batch_id", "notes", "id")
    autocomplete_fields = ("source_system",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("source_system")


@admin.register(SourceRecord)
class SourceRecordAdmin(CanonicalAdmin):
    list_display = (
        "source_system",
        "external_record_id",
        "source_locator",
        "updated_at",
    )
    list_filter = ("source_system",)
    search_fields = ("external_record_id", "source_locator", "id")
    autocomplete_fields = ("source_system", "import_batch")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        immutable = (
            "source_system",
            "import_batch",
            "external_record_id",
            "source_locator",
            "raw_payload",
        )
        return (*fields, *immutable) if obj else tuple(fields)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("source_system", "import_batch")
        )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Bekreft valgte påstander")
def confirm_assertions(modeladmin, request, queryset):
    for assertion in queryset:
        decide_assertion(assertion, VerificationStatus.CONFIRMED, user=request.user)


@admin.action(description="Marker valgte påstander som bestridt")
def dispute_assertions(modeladmin, request, queryset):
    for assertion in queryset:
        decide_assertion(assertion, VerificationStatus.DISPUTED, user=request.user)


@admin.action(description="Avvis valgte påstander")
def reject_assertions(modeladmin, request, queryset):
    for assertion in queryset:
        decide_assertion(assertion, VerificationStatus.REJECTED, user=request.user)


@admin.register(MetadataAssertion)
class MetadataAssertionAdmin(CanonicalAdmin):
    list_display = (
        "entity_type",
        "entity_uuid",
        "field_name",
        "raw_value",
        "status",
        "source_record",
    )
    list_filter = (
        "status",
        "entity_type",
        "field_name",
        "source_record__source_system",
    )
    search_fields = (
        "entity_uuid",
        "field_name",
        "raw_value",
        "source_record__external_record_id",
    )
    autocomplete_fields = ("source_record", "supersedes")
    actions = (confirm_assertions, dispute_assertions, reject_assertions)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        immutable = (
            "source_record",
            "entity_type",
            "entity_uuid",
            "field_name",
            "raw_value",
            "supersedes",
        )
        return (*fields, *immutable) if obj else tuple(fields)

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("source_record__source_system")
        )

    def save_model(self, request, obj, form, change):
        if not change and obj.supersedes_id:
            supersede_assertion(obj.supersedes, obj, user=request.user)
        else:
            super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssertionDecision)
class AssertionDecisionAdmin(CanonicalAdmin):
    list_display = ("assertion", "decision", "decided_by", "created_at")
    list_filter = ("decision",)
    search_fields = ("assertion__entity_uuid", "assertion__field_name", "note")
    autocomplete_fields = ("assertion", "decided_by")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        editable = ("assertion", "decision", "decided_by", "note")
        return (*fields, *editable) if obj else tuple(fields)

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("assertion", "decided_by")


@admin.register(AppliedMetadataChange)
class AppliedMetadataChangeAdmin(CanonicalAdmin):
    list_display = (
        "entity_type",
        "entity_uuid",
        "field_name",
        "before_value",
        "after_value",
        "changed_by",
        "created_at",
    )
    search_fields = ("entity_uuid", "field_name", "assertion__raw_value")
    list_filter = ("entity_type", "field_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("assertion", "changed_by")
