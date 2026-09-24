"""Permission-aware entry points to existing management workflows."""

from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from music_library.models import MusicLibraryEntry
from rights.workflows import require_onboarding


def management_links(user, recording_ids, *, return_url):
    """Batch-load membership without deriving legal status from file possession."""
    if not user.has_perms(
        ("catalogue.view_recording", "managed_music.view_managedrecording")
    ):
        return {}
    can_register = False
    if settings.GUI_V2_WRITES_ENABLED:
        try:
            require_onboarding(user)
        except PermissionDenied:
            pass
        else:
            can_register = True
    result = {}
    for recording_id, managed_id in MusicLibraryEntry.objects.filter(
        recording_id__in=set(recording_ids)
    ).values_list("recording_id", "managed_recording__pk"):
        if managed_id:
            url = (
                reverse("gui_v2:managed_music")
                + "?"
                + urlencode(
                    {
                        "q": recording_id,
                        "selected": managed_id,
                        "status": "all",
                    }
                )
            )
        elif can_register:
            url = (
                reverse("gui_v2:managed_onboard")
                + "?"
                + urlencode({"recording": recording_id, "return": return_url})
            )
        else:
            url = ""
        result[recording_id] = {
            "registered": bool(managed_id),
            "url": url,
            "label": (
                "Se forvaltning" if managed_id else "Registrer forvaltning"
            ),
        }
    return result
