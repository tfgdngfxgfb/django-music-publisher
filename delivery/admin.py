from django.contrib import admin

from .models import Delivery, DeliveryArtifact, DeliveryItem, DownloadEvent


class DeliveryItemInline(admin.TabularInline):
    model = DeliveryItem
    extra = 0
    readonly_fields = ("recording", "source_file_asset", "status", "output_filename")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "created_by", "purpose", "profile", "status")
    inlines = (DeliveryItemInline,)


admin.site.register(DeliveryArtifact)
admin.site.register(DownloadEvent)
