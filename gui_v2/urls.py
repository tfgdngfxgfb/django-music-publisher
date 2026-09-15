from django.urls import path

from . import views

app_name = "gui_v2"

urlpatterns = [
    path("", views.home, name="home"),
    path("musikkarkiv/", views.music_library, name="music_library"),
    path(
        "innspillinger/<uuid:recording_id>/",
        views.recording_detail,
        name="recording_detail",
    ),
    path(
        "avspilling/innspillinger/<uuid:recording_id>/radio.flac",
        views.recording_audio,
        name="recording_audio",
    ),
    path("kanaler/<uuid:channel_id>/logo/", views.channel_logo, name="channel_logo"),
    path(
        "musikkarkiv/<uuid:entry_id>/filer/<uuid:asset_id>/les-pa-nytt/",
        views.rescan_library_file,
        name="rescan_library_file",
    ),
    path(
        "musikkarkiv/<uuid:entry_id>/filer/<uuid:asset_id>/skill-ut/",
        views.split_library_file,
        name="split_library_file",
    ),
    path("utgivelser/", views.release_list, name="release_list"),
    path("utgivelser/<uuid:release_id>/", views.release_detail, name="release_detail"),
    path("api/innspillinger/", views.recording_search, name="recording_search"),
    path("utgivelse-spor/", views.legacy_release_tracks, name="release_tracks"),
]
