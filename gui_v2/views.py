from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render
from django.views.decorators.http import require_GET


PROTOTYPE_RECORDINGS = (
    {
        "title": "Nordlys over byen",
        "artist": "De Nordiske",
        "genre": "Pop",
        "energy": "4 av 5",
        "status": "Eksempel",
    },
    {
        "title": "Mellom fjell",
        "artist": "Ingrid Eksempel",
        "genre": "Viser",
        "energy": "2 av 5",
        "status": "Eksempel",
    },
)

PROTOTYPE_TRACKS = (
    {"position": "A1", "title": "Første spor", "artist": "Eksempelartist"},
    {"position": "A2", "title": "Andre spor", "artist": "Eksempelartist"},
    {"position": "B1", "title": "Tredje spor", "artist": "Gjest Eksempel"},
)


@require_GET
@login_required
def home(request):
    return render(request, "gui_v2/home.html", {"section": "home"})


@require_GET
@login_required
@permission_required("music_library.view_musiclibraryentry", raise_exception=True)
def music_library(request):
    return render(
        request,
        "gui_v2/music_library.html",
        {"section": "music_library", "recordings": PROTOTYPE_RECORDINGS},
    )


@require_GET
@login_required
@permission_required("catalogue.view_release", raise_exception=True)
def release_tracks(request):
    return render(
        request,
        "gui_v2/release_tracks.html",
        {"section": "release_tracks", "tracks": PROTOTYPE_TRACKS},
    )
