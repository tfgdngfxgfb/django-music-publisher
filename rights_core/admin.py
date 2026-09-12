from django.contrib import admin


class CanonicalAdmin(admin.ModelAdmin):
    readonly_fields = ("id", "created_at", "updated_at", "revision")
