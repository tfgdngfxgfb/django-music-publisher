"""Read-only presentation, filters and navigation for derived follow-up items."""

from collections import Counter
from urllib.parse import urlencode

from django import forms
from django.urls import reverse

from rights.followup import CATEGORIES, RULES, item_sort_key
from rights.models import RightsClaim

TYPE_LABELS = {
    "claim": "Rettighetskrav",
    "recording": "Innspilling",
    "management": "Forvaltning",
    "release": "Utgivelse",
}


class FollowUpFilterForm(forms.Form):
    category = forms.ChoiceField(
        label="Kategori",
        choices=(("all", "Alle"), *CATEGORIES),
        required=False,
    )
    q = forms.CharField(label="Søk i listen", required=False, max_length=200)
    target = forms.ChoiceField(label="Objekttype", required=False)
    status = forms.ChoiceField(label="Status / egenskap", required=False)
    horizon = forms.ChoiceField(
        label="Kommende periode",
        choices=(
            ("30", "Neste 30 dager"),
            ("90", "Neste 90 dager"),
            ("180", "Neste 180 dager"),
        ),
    )
    right_type = forms.ChoiceField(label="Rettighetstype", required=False)
    signal = forms.ChoiceField(label="Signal", required=False)
    source = forms.ChoiceField(label="Kildesystem", required=False)
    sort = forms.ChoiceField(
        label="Sorter etter",
        required=False,
        choices=(
            ("", "Problem og dato"),
            ("date", "Nærmeste dato"),
            ("title", "Objektnavn"),
        ),
    )

    def __init__(self, *args, items=(), **kwargs):
        super().__init__(*args, **kwargs)
        codes = {code for item in items for code in item.codes}
        types = {item.target_type for item in items}
        rights = {c.right_type for item in items for c in item.claims}
        sources = sorted(
            {
                value
                for item in items
                for c in item.claims
                for label, value in c.details
                if label == "Kildesystem"
            }
        )
        self.fields["source"].choices = [
            ("", "Alle tilgjengelige kilder"),
            *((s, s) for s in sources),
        ]
        self.fields["target"].choices = [
            ("", "Alle objekttyper"),
            *((t, label) for t, label in TYPE_LABELS.items() if t in types),
        ]
        self.fields["right_type"].choices = [
            ("", "Alle rettighetstyper"),
            *(
                (t, label)
                for t, label in RightsClaim.RightType.choices
                if t in rights
            ),
        ]
        self.fields["signal"].choices = [
            ("", "Alle signaler"),
            *((c, RULES[c][2]) for c in RULES if c in codes),
        ]
        self.fields["status"].choices = [
            ("", "Alle egenskaper"),
            *(
                (c, RULES[c][2])
                for c in RULES
                if c in codes
                and c.startswith(
                    (
                        "management.",
                        "claim.unverified",
                        "claim.disputed",
                        "claim.expiring",
                        "claim.starts_soon",
                    )
                )
            ),
        ]


def filtered_items(items, values):
    query = values.get("q", "").casefold()
    selected = []
    for item in items:
        if values.get("category") not in (None, "", "all", item.category):
            continue
        if values.get("target") and values["target"] != item.target_type:
            continue
        if any(
            values.get(key) and values[key] not in item.codes
            for key in ("status", "signal")
        ):
            continue
        if values.get("right_type") and not any(
            c.right_type == values["right_type"] for c in item.claims
        ):
            continue
        if values.get("source") and not any(
            ("Kildesystem", values["source"]) in c.details for c in item.claims
        ):
            continue
        searchable = " ".join(
            [
                item.title,
                *(s.reason for s in item.signals),
                *(str(v) for _, v in item.details),
                *(str(v) for c in item.claims for _, v in c.details),
            ]
        ).casefold()
        if query and query not in searchable:
            continue
        selected.append(item)
    sort = values.get("sort")
    if sort == "title":
        key = lambda i: (i.title.casefold(), i.key)
    elif sort == "date":
        from datetime import date

        key = lambda i: (
            i.relevant_date or date.max,
            i.title.casefold(),
            i.key,
        )
    else:
        key = item_sort_key
    return sorted(selected, key=key)


def category_counts(items):
    counts = Counter(i.category for i in items)
    return [
        ("all", "Alle", len(items)),
        *((key, label, counts[key]) for key, label in CATEGORIES),
    ]


def present_item(item, request):
    query = request.GET.copy()
    query["selected"] = item.key
    select_url = "?" + query.urlencode()
    return_path = request.path + select_url
    suffix = "?" + urlencode({"return": return_path})
    links = []
    if item.release_id:
        links.append(
            (
                "Åpne utgivelse → Rettigheter",
                reverse("gui_v2:release_detail", args=[item.release_id])
                + "?"
                + urlencode({"tab": "rights", "return": return_path}),
            )
        )
    elif item.recording_id:
        if item.target_type == "claim":
            links.append(
                (
                    "Åpne rettighetskrav",
                    reverse(
                        "gui_v2:recording_rights_claim",
                        args=[item.recording_id, item.target_id],
                    )
                    + suffix,
                )
            )
        if (
            item.target_type == "management"
            and "management.local_basis_without_membership" not in item.codes
        ):
            links.append(
                (
                    "Åpne forvaltning",
                    reverse("gui_v2:managed_music")
                    + "?"
                    + urlencode(
                        {
                            "selected": item.target_id,
                            "q": str(item.recording_id),
                            "status": "all",
                            "return": return_path,
                        }
                    ),
                )
            )
        links.append(
            (
                "Åpne innspilling → Rettigheter",
                reverse("gui_v2:recording_rights", args=[item.recording_id])
                + suffix,
            )
        )
    claims = []
    for c in item.claims:
        claims.append(
            {
                "id": c.pk,
                "details": c.details,
                "url": reverse(
                    "gui_v2:recording_rights_claim",
                    args=[c.recording_id, c.pk],
                )
                + suffix,
            }
        )
    next_step = {
        "claim": "Vurder posisjonen i Rettigheter. Dokumentasjon og juridisk korreksjon har egne workflows.",
        "recording": "Sammenlign posisjonene i Rettigheter. Avklar om grunnlaget må dokumenteres, avvises eller erstattes.",
        "management": "Kontroller grunnlag og historikk i Forvaltet musikk. Medlemskap endres bare gjennom eksplisitt workflow.",
        "release": "Gjennomgå innspillingene i utgivelsens Rettigheter-fane. Katalogvern er ikke en bruksrett.",
    }[item.target_type]
    if item.codes == ("management.needs_reconciliation",):
        next_step = "Venter på eksplisitt systemoppdatering av lagret status. Denne siden viser beregnet status og utfører ingen reconciliation."
    return {
        "item": item,
        "type_label": TYPE_LABELS[item.target_type],
        "select_url": select_url,
        "links": links,
        "claims": claims,
        "next_step": next_step,
    }
