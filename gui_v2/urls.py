from django.urls import path

from . import views

app_name = "gui_v2"

urlpatterns = [
    path("", views.home, name="home"),
    path("musikkarkiv/", views.music_library, name="music_library"),
    path("utgivelser/", views.release_list, name="release_list"),
    path("utgivelser/<uuid:release_id>/", views.release_detail, name="release_detail"),
    path("api/innspillinger/", views.recording_search, name="recording_search"),
    path("utgivelse-spor/", views.legacy_release_tracks, name="release_tracks"),
]
