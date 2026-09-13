from django.urls import path

from . import views

app_name = "workbench"

urlpatterns = [
    path("omslag/<uuid:pk>/", views.cover_image, name="cover_image"),
    path("musikkarkiv/", views.library_list, name="library"),
    path("musikkarkiv/legg-til/", views.library_add, name="library_add"),
    path("musikkarkiv/les-inn/", views.flac_ingest_start, name="flac_ingest_start"),
    path(
        "musikkarkiv/les-inn/<uuid:pk>/",
        views.flac_ingest_preview,
        name="flac_ingest_preview",
    ),
    path(
        "musikkarkiv/les-inn/<uuid:pk>/bruk/",
        views.flac_ingest_apply,
        name="flac_ingest_apply",
    ),
    path(
        "musikkarkiv/les-inn/<uuid:pk>/skann-pa-nytt/",
        views.flac_ingest_rescan,
        name="flac_ingest_rescan",
    ),
    path(
        "musikkarkiv/les-inn/<uuid:pk>/kontroller/<uuid:item_pk>/",
        views.flac_ingest_review,
        name="flac_ingest_review",
    ),
    path("forvaltet/", views.managed_list, name="managed"),
    path("forvaltet/legg-til/", views.managed_add, name="managed_add"),
    path("utgivelser/", views.release_list, name="releases"),
    path("utgivelser/ny/", views.release_add, name="release_add"),
    path("utgivelser/<uuid:pk>/", views.release_detail, name="release"),
    path(
        "utgivelser/<uuid:pk>/rettigheter/ny/",
        views.release_rights_add,
        name="release_rights_add",
    ),
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
    path(
        "innspillinger/<uuid:pk>/rettigheter/ny/",
        views.rights_claim_add,
        name="rights_claim_add",
    ),
    path(
        "rettigheter/<uuid:pk>/beslutning/",
        views.rights_claim_decide,
        name="rights_claim_decide",
    ),
    path(
        "rettigheter/<uuid:pk>/erstatt/",
        views.rights_claim_supersede,
        name="rights_claim_supersede",
    ),
    path(
        "rettigheter/<uuid:pk>/avtale/",
        views.rights_claim_link_agreement,
        name="rights_claim_link_agreement",
    ),
    path("avtaler/", views.agreement_list, name="agreements"),
    path("avtaler/ny/", views.agreement_add, name="agreement_add"),
    path("avtaler/<uuid:pk>/", views.agreement_detail, name="agreement"),
    path("avtaler/<uuid:pk>/rediger/", views.agreement_edit, name="agreement_edit"),
    path(
        "avtaler/<uuid:pk>/part/",
        views.agreement_party_add,
        name="agreement_party_add",
    ),
    path(
        "avtaler/<uuid:pk>/dokument/",
        views.agreement_document_add,
        name="agreement_document_add",
    ),
    path("kilder/<uuid:pk>/handling/", views.assertion_action, name="assertion_action"),
    path("api/innspillinger/", views.recording_search, name="recording_search"),
    path("artister/", views.parties_list, name="parties"),
    path("artister/ny-part/", views.party_add, name="party_add"),
    path("artister/ny-identitet/", views.artist_add, name="artist_add"),
    path("kontroll/", views.control, name="control"),
    path(
        "kontroll/bekreft-flac/",
        views.confirm_flac_metadata,
        name="confirm_flac_metadata",
    ),
    path("filer/", views.files_list, name="files"),
    path("filer/ny/", views.file_add, name="file_add"),
    path("filer/<uuid:pk>/", views.file_detail, name="file"),
    path(
        "filer/<uuid:pk>/plassering/", views.file_location_add, name="file_location_add"
    ),
]
