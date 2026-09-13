from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

admin.site.site_header = "P7 Arkiv og rettigheter"
admin.site.site_title = "P7 Arkiv og rettigheter"
admin.site.site_url = "/"

urlpatterns = [
    path(
        "",
        TemplateView.as_view(template_name="rights_project/home.html"),
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
