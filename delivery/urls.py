from django.urls import path

from . import views

app_name = "delivery"

urlpatterns = [
    path("", views.history, name="history"),
    path("ny/", views.create, name="create"),
    path("<uuid:delivery_id>/", views.detail, name="detail"),
    path("<uuid:delivery_id>/last-ned/", views.download, name="download"),
]
