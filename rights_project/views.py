from django.apps import apps
from django.contrib import admin
from django.shortcuts import render
from django.urls import reverse


def _admin_link(request, app_label, model_name, label, description):
    model = apps.get_model(app_label, model_name)
    model_admin = admin.site._registry.get(model)
    if not model_admin or not model_admin.has_module_permission(request):
        return None
    if not model_admin.has_view_permission(request):
        return None
    return {
        "label": label,
        "description": description,
        "url": reverse(f"admin:{app_label}_{model._meta.model_name}_changelist"),
    }


def home(request):
    section_specs = (
        (
            "Musikkarkiv",
            (
                (
                    "music_library",
                    "MusicLibraryEntry",
                    "Åpne Musikkarkiv",
                    "Innspillinger og radiometadata som brukes i musikkarkivet.",
                ),
            ),
        ),
        (
            "Forvaltet musikk",
            (
                (
                    "managed_music",
                    "ManagedRecording",
                    "Åpne Forvaltet musikk",
                    "Innspillinger som uttrykkelig er lagt til for forvaltning.",
                ),
            ),
        ),
        (
            "Utgivelser",
            (
                (
                    "catalogue",
                    "Release",
                    "Åpne utgivelser",
                    "Registrer utgivelser og spor, og opprett eller gjenbruk innspillinger.",
                ),
            ),
        ),
        (
            "Kontroll",
            (
                (
                    "catalogue",
                    "DuplicateCandidate",
                    "Mulige dubletter",
                    "Sammenlign mulige dubletter uten automatisk sammenslåing.",
                ),
                (
                    "provenance",
                    "MetadataAssertion",
                    "Kilder og verifikasjon",
                    "Kontroller importerte, bekreftede og bestridte metadata.",
                ),
            ),
        ),
    )
    sections = []
    for title, link_specs in section_specs:
        links = [
            link
            for link in (
                _admin_link(request, *link_spec) for link_spec in link_specs
            )
            if link is not None
        ]
        if links:
            sections.append({"title": title, "links": links})

    sections.append(
        {
            "title": "Hjelp",
            "links": [
                {
                    "label": "Åpne hjelp",
                    "description": "Begreper og praktisk veiledning for registrering.",
                    "url": reverse("help"),
                }
            ],
        }
    )
    context = {
        **admin.site.each_context(request),
        "title": "Startside",
        "sections": sections,
    }
    return render(request, "rights_project/home.html", context)
