from django.urls import path

from . import views

app_name = "workbench"

urlpatterns = [
    path("musikkarkiv/", views.library_list, name="library"),
    path("musikkarkiv/legg-til/", views.library_add, name="library_add"),
    path("forvaltet/", views.managed_list, name="managed"),
    path("forvaltet/legg-til/", views.managed_add, name="managed_add"),
    path("utgivelser/", views.release_list, name="releases"),
    path("utgivelser/ny/", views.release_add, name="release_add"),
    path("utgivelser/<uuid:pk>/", views.release_detail, name="release"),
    path(
        "utgivelser/<uuid:pk>/identifikator/",
        views.release_identifier_add,
        name="release_identifier_add",
    ),
    path("utgivelser/<uuid:pk>/spor/", views.release_tracks, name="release_tracks"),
    path("innspillinger/<uuid:pk>/", views.recording_detail, name="recording"),
    path(
        "innspillinger/<uuid:pk>/rediger/", views.recording_edit, name="recording_edit"
    ),
    path(
        "innspillinger/<uuid:pk>/identifikator/",
        views.recording_identifier_add,
        name="recording_identifier_add",
    ),
    path(
        "innspillinger/<uuid:pk>/medvirkende/",
        views.contribution_add,
        name="contribution_add",
    ),
    path("innspillinger/<uuid:pk>/radio/", views.radio_edit, name="radio_edit"),
    path("kilder/<uuid:pk>/handling/", views.assertion_action, name="assertion_action"),
    path("api/innspillinger/", views.recording_search, name="recording_search"),
    path("artister/", views.parties_list, name="parties"),
    path("artister/ny-part/", views.party_add, name="party_add"),
    path("artister/ny-identitet/", views.artist_add, name="artist_add"),
    path("kontroll/", views.control, name="control"),
    path("filer/", views.files_list, name="files"),
    path("filer/ny/", views.file_add, name="file_add"),
    path("filer/<uuid:pk>/", views.file_detail, name="file"),
    path(
        "filer/<uuid:pk>/plassering/", views.file_location_add, name="file_location_add"
    ),
]
