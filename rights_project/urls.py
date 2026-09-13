from django.contrib import admin
from django.urls import include, path
from workbench.views import help_page, home

admin.site.site_header = "P7 Arkiv og rettigheter"
admin.site.site_title = "P7 Arkiv og rettigheter"
admin.site.site_url = "/"

urlpatterns = [
    path(
        "",
        home,
        name="home",
    ),
    path(
        "hjelp/",
        help_page,
        name="help",
    ),
    path("arbeid/", include("workbench.urls")),
    path("admin/", admin.site.urls),
    path("publishing/", include("music_publisher.urls")),
]
