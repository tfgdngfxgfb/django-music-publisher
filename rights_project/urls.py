from django.contrib import admin
from django.urls import include, path
from gui_v2.views import home
from workbench.views import help_page

admin.site.site_header = "P7 Arkiv og rettigheter"
admin.site.site_title = "P7 Arkiv og rettigheter"
admin.site.site_url = "/"

urlpatterns = [
    path("v2/leveranser/", include("delivery.urls")),
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
    path("v2/", include("gui_v2.urls")),
    path("admin/", admin.site.urls),
    path("publishing/", include("music_publisher.urls")),
]
