"""Ops UI urlconf — everything mounted under the admin-panel prefix.

Settings lives in this app; dashboard + brain browser views live in
apps.brain (they are views over the Entity index) but route through here
so the whole ops surface shares one prefix and one nav.
"""
from django.urls import path

from apps.brain import ops_views

from . import views

app_name = "brainconfig"

urlpatterns = [
    path("", ops_views.dashboard, name="dashboard"),
    path("brain/", ops_views.browser, name="browser"),
    path("brain/<str:entity_id>/", ops_views.entity_detail, name="entity"),
    path("settings/", views.settings_page, name="settings"),
]
