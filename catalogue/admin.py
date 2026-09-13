from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from rights_core.admin import CanonicalAdmin

from .forms import TrackCreationForm
from .models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Label,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from .services import create_release_track


class ArchiveMembershipFilter(admin.SimpleListFilter):
    title = "Musikkarkiv"
    parameter_name = "in_library"

    def lookups(self, request, model_admin):
        return (("yes", "Ja"), ("no", "Nei"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(music_library_entry__isnull=False)
        if self.value() == "no":
            return queryset.filter(music_library_entry__isnull=True)
        return queryset


class ManagedMembershipFilter(admin.SimpleListFilter):
    title = "Forvaltet musikk"
    parameter_name = "managed"

    def lookups(self, request, model_admin):
        return (("yes", "Ja"), ("no", "Nei"))

    def queryset(self, request, queryset):
        lookup = "music_library_entry__managed_recording__isnull"
        if self.value() == "yes":
            return queryset.filter(**{lookup: False})
        if self.value() == "no":
            return queryset.filter(**{lookup: True})
        return queryset


class MissingMetadataFilter(admin.SimpleListFilter):
    title = "manglende viktige data"
    parameter_name = "missing"

    def lookups(self, request, model_admin):
        return (
            ("isrc", "ISRC"),
            ("artist", "Artist/medvirkende"),
            ("release", "Utgivelse"),
        )

    def queryset(self, request, queryset):
        if self.value() == "isrc":
            return queryset.exclude(identifiers__scheme=ExternalIdentifier.Scheme.ISRC)
        if self.value() == "artist":
            return queryset.filter(contributions__isnull=True)
        if self.value() == "release":
            return queryset.filter(release_tracks__isnull=True)
        return queryset


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
    fk_name = "recording"
    extra = 0
    fields = ("scheme", "namespace", "value", "normalized_value")
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
    list_filter = (
        ArchiveMembershipFilter,
        ManagedMembershipFilter,
        MissingMetadataFilter,
        "metadata_status",
        "recording_kind",
    )
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


class ReleaseTrackInline(admin.TabularInline):
    model = ReleaseTrack
    extra = 0
    autocomplete_fields = ("recording",)
    fields = (
        "recording",
        "disc_number",
        "side",
        "track_number",
        "title_override",
        "duration_ms",
        "sequence_number",
    )
    show_change_link = True


class ReleaseIdentifierInline(admin.TabularInline):
    model = ExternalIdentifier
    fk_name = "release"
    extra = 0
    fields = ("scheme", "namespace", "value", "normalized_value")
    readonly_fields = ("normalized_value",)


@admin.register(Label)
class LabelAdmin(CanonicalAdmin):
    list_display = ("name", "party", "updated_at", "id")
    search_fields = ("name", "party__name", "id")
    autocomplete_fields = ("party",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("party")


@admin.register(Release)
class ReleaseAdmin(CanonicalAdmin):
    change_form_template = "admin/catalogue/release/change_form.html"
    list_display = (
        "title",
        "release_type",
        "release_year",
        "label",
        "catalogue_number",
        "verification_status",
        "track_count",
        "id",
    )
    list_filter = ("release_type", "verification_status", "release_year", "label")
    search_fields = (
        "title",
        "catalogue_number",
        "label__name",
        "identifiers__normalized_value",
        "tracks__recording__title",
        "id",
    )
    autocomplete_fields = ("label",)
    inlines = (ReleaseTrackInline, ReleaseIdentifierInline)

    @admin.display(description="Spor", ordering="track_total")
    def track_count(self, obj):
        return obj.track_total

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("label")
            .annotate(track_total=Count("tracks"))
        )

    def get_urls(self):
        custom = [
            path(
                "<uuid:object_id>/registrer-spor/",
                self.admin_site.admin_view(self.add_track_view),
                name="catalogue_release_add_track",
            )
        ]
        return custom + super().get_urls()

    def add_track_view(self, request, object_id):
        release = get_object_or_404(Release, pk=object_id)
        if not self.has_change_permission(request, release):
            raise PermissionDenied
        next_sequence = (
            release.tracks.aggregate(value=Max("sequence_number"))["value"] or 0
        ) + 1
        form = TrackCreationForm(
            request.POST or None, initial={"sequence_number": next_sequence}
        )
        if request.method == "POST" and form.is_valid():
            try:
                track = create_release_track(release=release, **form.cleaned_data)
            except ValueError as error:
                form.add_error(None, str(error))
            else:
                self.message_user(
                    request,
                    f"Spor {track.sequence_number} «{track.display_title}» ble registrert.",
                    messages.SUCCESS,
                )
                if "save_and_continue" in request.POST:
                    return redirect(
                        reverse("admin:catalogue_release_add_track", args=(release.pk,))
                    )
                return redirect(
                    reverse("admin:catalogue_release_change", args=(release.pk,))
                )
        context = {
            **self.admin_site.each_context(request),
            "title": f"Registrer spor på «{release.title}»",
            "opts": self.model._meta,
            "release": release,
            "form": form,
        }
        return TemplateResponse(
            request, "admin/catalogue/release/add_track.html", context
        )


@admin.register(ReleaseTrack)
class ReleaseTrackAdmin(CanonicalAdmin):
    list_display = (
        "release",
        "sequence_number",
        "disc_number",
        "side",
        "track_number",
        "recording",
        "title_override",
    )
    list_filter = ("release__release_type", "disc_number", "side")
    search_fields = (
        "release__title",
        "release__catalogue_number",
        "recording__title",
        "recording__identifiers__normalized_value",
        "title_override",
        "id",
    )
    autocomplete_fields = ("release", "recording")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("release", "recording")


@admin.register(ExternalIdentifier)
class IdentifierAdmin(CanonicalAdmin):
    list_display = ("scheme", "normalized_value", "target", "namespace", "id")
    search_fields = ("value", "normalized_value", "recording__title", "release__title")
    list_filter = ("scheme",)
    autocomplete_fields = ("recording", "release")
    readonly_fields = (*CanonicalAdmin.readonly_fields, "normalized_value")

    @admin.display(description="Tilknyttet objekt")
    def target(self, obj):
        return obj.recording or obj.release

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("recording", "release")


@admin.action(description="Avvis valgte som dubletter")
def dismiss_duplicates(modeladmin, request, queryset):
    for candidate in queryset:
        candidate.status = DuplicateCandidate.Status.DISMISSED
        candidate.save(update_fields=("status",))


@admin.register(DuplicateCandidate)
class DuplicateCandidateAdmin(CanonicalAdmin):
    list_display = ("recording_a", "recording_b", "score", "signal_summary", "status")
    list_filter = ("status", "score")
    search_fields = (
        "recording_a__title",
        "recording_b__title",
        "recording_a__identifiers__normalized_value",
        "recording_b__identifiers__normalized_value",
        "id",
    )
    autocomplete_fields = ("recording_a", "recording_b")
    actions = (dismiss_duplicates,)

    @admin.display(description="Matchsignaler")
    def signal_summary(self, obj):
        return ", ".join(obj.signals)

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("recording_a", "recording_b")
        )
