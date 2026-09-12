from django.contrib import admin

from rights_core.admin import CanonicalAdmin
from .models import ArtistIdentity, Party


@admin.register(Party)
class PartyAdmin(CanonicalAdmin):
    list_display = ("name", "kind", "id", "updated_at")
    search_fields = ("name", "id")
    list_filter = ("kind",)


@admin.register(ArtistIdentity)
class ArtistIdentityAdmin(CanonicalAdmin):
    list_display = ("display_name", "party", "id")
    search_fields = ("display_name", "party__name", "id")
    autocomplete_fields = ("party",)
