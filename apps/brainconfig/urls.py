from django.urls import path

from . import views

app_name = "brainconfig"

urlpatterns = [
    path("settings/", views.settings_page, name="settings"),
]
