from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from .views import home

admin.site.site_header = "P7 Arkiv og rettigheter"
admin.site.site_title = "P7 Arkiv og rettigheter"
admin.site.site_url = "/"

urlpatterns = [
    path(
        "",
        admin.site.admin_view(home),
        name="home",
    ),
    path(
        "hjelp/",
        TemplateView.as_view(template_name="rights_project/help.html"),
        name="help",
    ),
    path("admin/", admin.site.urls),
    path("publishing/", include("music_publisher.urls")),
]
