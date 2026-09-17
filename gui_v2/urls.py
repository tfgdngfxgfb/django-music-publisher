from django.urls import path

from . import views, digitization

app_name = "gui_v2"

urlpatterns = [
    path("digitalisering/", digitization.index, name="digitization_index"),
    path(
        "digitalisering/utgivelser/<uuid:release_id>/matrise/",
        digitization.release_matrix,
        name="digitization_release_matrix",
    ),
    path(
        "digitalisering/<uuid:batch_id>/",
        digitization.detail,
        name="digitization_detail",
    ),
    path("", views.home, name="home"),
    path("musikkarkiv/", views.music_library, name="music_library"),
    path(
        "innspillinger/<uuid:recording_id>/",
        views.recording_detail,
        name="recording_detail",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/",
        views.recording_files,
        name="recording_files",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/master/registrer/",
        views.recording_master_register,
        name="recording_master_register",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/master/<uuid:asset_id>/velg/",
        views.recording_master_select,
        name="recording_master_select",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/generering/forbered/",
        views.recording_generation_preview,
        name="recording_generation_preview",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/generering/<uuid:generation_id>/generer/",
        views.recording_generate_candidate,
        name="recording_generate_candidate",
    ),
    path(
        "innspillinger/<uuid:recording_id>/filer/generering/<uuid:generation_id>/aktiver/",
        views.recording_activate_candidate,
        name="recording_activate_candidate",
    ),
    path(
        "avspilling/innspillinger/<uuid:recording_id>/radio.flac",
        views.recording_audio,
        name="recording_audio",
    ),
    path(
        "kanaler/<uuid:channel_id>/logo/",
        views.channel_logo,
        name="channel_logo",
    ),
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
    path(
        "utgivelser/<uuid:release_id>/",
        views.release_detail,
        name="release_detail",
    ),
    path(
        "api/innspillinger/", views.recording_search, name="recording_search"
    ),
    path(
        "utgivelse-spor/", views.legacy_release_tracks, name="release_tracks"
    ),
]
