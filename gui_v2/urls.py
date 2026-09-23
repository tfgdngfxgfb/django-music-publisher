from django.urls import path

from . import (
    views,
    digitization,
    rights_views,
    managed_views,
    release_rights_views,
    followup_views,
    radio_work_views,
    settings_views,
)

app_name = "gui_v2"

urlpatterns = [
    path("innstillinger/", settings_views.index, name="settings"),
    path("radio-flac/", radio_work_views.index, name="radio_workbench"),
    path(
        "radio-flac/bulk/", radio_work_views.bulk, name="radio_workbench_bulk"
    ),
    path("oppfolging/", followup_views.index, name="followup"),
    path(
        "utgivelser/<uuid:release_id>/rettigheter/registrering/",
        release_rights_views.bulk,
        name="release_rights_bulk",
    ),
    path("forvaltet-musikk/", managed_views.index, name="managed_music"),
    path(
        "forvaltet-musikk/registrer/",
        managed_views.onboard,
        name="managed_onboard",
    ),
    path(
        "forvaltet-musikk/<uuid:managed_id>/avklar/",
        managed_views.action,
        {"action": "correct"},
        name="managed_correct",
    ),
    path(
        "forvaltet-musikk/<uuid:managed_id>/tilbakefor/",
        managed_views.action,
        {"action": "return"},
        name="managed_return",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/",
        rights_views.overview,
        name="recording_rights",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/registrer/",
        rights_views.action,
        {"action": "register"},
        name="recording_rights_register",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/<uuid:claim_id>/",
        rights_views.detail,
        name="recording_rights_claim",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/<uuid:claim_id>/dokumenter/",
        rights_views.action,
        {"action": "document"},
        name="recording_rights_document",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/<uuid:claim_id>/vurder/",
        rights_views.action,
        {"action": "decide"},
        name="recording_rights_decide",
    ),
    path(
        "innspillinger/<uuid:recording_id>/rettigheter/<uuid:claim_id>/erstatt/",
        rights_views.action,
        {"action": "replace"},
        name="recording_rights_replace",
    ),
    path("digitalisering/", digitization.index, name="digitization_index"),
    path(
        "digitalisering/start/", digitization.start, name="digitization_start"
    ),
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
    path(
        "digitalisering/<uuid:batch_id>/filer/",
        digitization.browse_files,
        name="digitization_browse_files",
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
        "innspillinger/<uuid:recording_id>/utgivelser/",
        views.recording_releases,
        name="recording_releases",
    ),
    path(
        "innspillinger/<uuid:recording_id>/radio/",
        views.recording_radio,
        name="recording_radio",
    ),
    path(
        "innspillinger/<uuid:recording_id>/leveranser/",
        views.recording_deliveries,
        name="recording_deliveries",
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
        "avspilling/innspillinger/<uuid:recording_id>/lyd",
        views.recording_audio,
        name="recording_audio",
    ),
    path(
        "avspilling/innspillinger/<uuid:recording_id>/radio.flac",
        views.recording_audio,
        {"radio_only": True},
        name="recording_radio_audio",
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
