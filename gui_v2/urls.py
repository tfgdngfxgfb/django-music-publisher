from django.urls import path

from . import views

app_name = "gui_v2"

urlpatterns = [
    path("", views.home, name="home"),
    path("musikkarkiv/", views.music_library, name="music_library"),
    path("utgivelse-spor/", views.release_tracks, name="release_tracks"),
]
