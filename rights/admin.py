from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from rights_core.admin import CanonicalAdmin
from rights_core.models import VerificationStatus

from .models import (
    Agreement,
    AgreementDocument,
    AgreementParty,
    ClaimTerritory,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
    Territory,
)
from .services import decide_rights_claim


class AgreementPartyInline(admin.TabularInline):
    model = AgreementParty
    extra = 0
    can_delete = False


class AgreementDocumentInline(admin.TabularInline):
    model = AgreementDocument
    extra = 0
    can_delete = False


class ClaimTerritoryInline(admin.TabularInline):
    model = ClaimTerritory
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Territory)
class TerritoryAdmin(CanonicalAdmin):
    list_display = ("name_nb", "code")
    search_fields = ("name_nb", "code")


@admin.register(Agreement)
class AgreementAdmin(CanonicalAdmin):
    list_display = (
        "title",
        "internal_reference",
        "agreement_type",
        "status",
        "effective_date",
        "expiry_date",
    )
    list_filter = ("agreement_type", "status")
    search_fields = ("title", "internal_reference", "notes", "id")
    inlines = (AgreementPartyInline, AgreementDocumentInline)

    def has_add_permission(self, request):
        return request.user.has_perm(
            "rights.manage_agreement"
        ) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(
            "rights.manage_agreement"
        ) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Bekreft valgte rettighetskrav")
def confirm_claims(modeladmin, request, queryset):
    for claim in queryset:
        try:
            decide_rights_claim(claim, VerificationStatus.CONFIRMED, user=request.user)
        except ValidationError as error:
            modeladmin.message_user(request, "; ".join(error.messages), messages.ERROR)


@admin.action(description="Marker valgte rettighetskrav som bestridt")
def dispute_claims(modeladmin, request, queryset):
    for claim in queryset:
        decide_rights_claim(claim, VerificationStatus.DISPUTED, user=request.user)


@admin.action(description="Avvis valgte rettighetskrav")
def reject_claims(modeladmin, request, queryset):
    for claim in queryset:
        decide_rights_claim(claim, VerificationStatus.REJECTED, user=request.user)


@admin.register(RightsClaim)
class RightsClaimAdmin(CanonicalAdmin):
    list_display = (
        "recording",
        "right_type",
        "rights_holder",
        "share",
        "territory_mode",
        "status",
        "evidence_strength",
        "valid_from",
        "valid_until",
    )
    list_filter = ("right_type", "status", "evidence_strength", "territory_mode")
    search_fields = (
        "recording__title",
        "rights_holder__name",
        "grantor__name",
        "agreement__title",
        "id",
    )
    autocomplete_fields = (
        "recording",
        "rights_holder",
        "grantor",
        "source_record",
        "agreement",
        "supersedes",
    )
    readonly_fields = CanonicalAdmin.readonly_fields + ("status",)
    inlines = (ClaimTerritoryInline,)
    actions = (confirm_claims, dispute_claims, reject_claims)

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("rights.decide_rightsclaim"):
            for name in ("confirm_claims", "dispute_claims", "reject_claims"):
                actions.pop(name, None)
        return actions

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RightsDecision)
class RightsDecisionAdmin(CanonicalAdmin):
    list_display = ("claim", "decision", "decided_by", "created_at")
    list_filter = ("decision",)
    search_fields = ("claim__recording__title", "claim__rights_holder__name", "note")
    readonly_fields = CanonicalAdmin.readonly_fields + (
        "claim",
        "decision",
        "decided_by",
        "note",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RightsConfiguration)
class RightsConfigurationAdmin(CanonicalAdmin):
    list_display = ("local_organization", "updated_at")
    autocomplete_fields = ("local_organization",)

    def has_add_permission(self, request):
        return not RightsConfiguration.objects.exists() and super().has_add_permission(
            request
        )

    def has_delete_permission(self, request, obj=None):
        return False


class AgreementRelationAdmin(CanonicalAdmin):
    def has_add_permission(self, request):
        return request.user.has_perm(
            "rights.manage_agreement"
        ) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(
            "rights.manage_agreement"
        ) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


class ClaimTerritoryAdmin(CanonicalAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AgreementParty, AgreementRelationAdmin)
admin.site.register(AgreementDocument, AgreementRelationAdmin)
admin.site.register(ClaimTerritory, ClaimTerritoryAdmin)
