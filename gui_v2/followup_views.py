from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET

from gui_v2.followup import (
    FollowUpFilterForm,
    category_counts,
    filtered_items,
    present_item,
)
from rights.followup_queries import collect_items


@require_GET
@login_required
@permission_required("rights.view_rightsclaim", raise_exception=True)
def index(request):
    params = request.GET.copy()
    params.setdefault("category", "action")
    params.setdefault("horizon", "90")
    horizon = (
        int(params["horizon"])
        if params["horizon"] in ("30", "90", "180")
        else 90
    )
    items, configured = collect_items(request.user, horizon=horizon)
    form = FollowUpFilterForm(params, items=items)
    selected_items = (
        filtered_items(items, form.cleaned_data) if form.is_valid() else []
    )
    page = Paginator(selected_items, 25).get_page(request.GET.get("page"))
    rows = [present_item(item, request) for item in page]
    selected = next(
        (r for r in rows if r["item"].key == request.GET.get("selected")),
        rows[0] if rows else None,
    )
    categories = []
    for key, label, count in category_counts(items):
        query = params.copy()
        query["category"] = key
        for transient in ("page", "selected"):
            query.pop(transient, None)
        categories.append(
            {
                "key": key,
                "label": label,
                "count": count,
                "url": "?" + query.urlencode(),
                "active": params["category"] == key,
            }
        )
    page_query = params.copy()
    page_query.pop("page", None)
    page_query.pop("selected", None)
    return render(
        request,
        "gui_v2/followup.html",
        {
            "section": "followup",
            "writes_enabled": False,
            "form": form,
            "rows": rows,
            "selected": selected,
            "page": page,
            "categories": categories,
            "configured": configured,
            "page_query": page_query.urlencode(),
        },
    )
