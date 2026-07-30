"""Ops UI urlconf — everything mounted under the admin-panel prefix.

Settings lives in this app; dashboard + brain browser views live in
apps.brain (they are views over the Entity index) but route through here
so the whole ops surface shares one prefix and one nav.
"""
from django.urls import path

from apps.brain import ops_views
from apps.events import ops_views as events_ops
from apps.feeds import ops_views as feeds_ops
from apps.reader import ops_views as reader_ops

from . import views

app_name = "brainconfig"

urlpatterns = [
    path("", ops_views.dashboard, name="dashboard"),
    # Shared data source for the brain visuals (rings now; explorer,
    # activity overlay and timeline in M3.5.3-.5).
    path("graph.json", ops_views.graph_json, name="graph-json"),
    path("graph/", ops_views.graph_explorer, name="graph"),
    # Cursor over the read log — drives the live activity overlay.
    path("activity.json", events_ops.activity_json, name="activity-json"),
    path("brain/", ops_views.browser, name="browser"),
    path("brain/<str:entity_id>/", ops_views.entity_detail, name="entity"),
    path("feeds/", feeds_ops.queue, name="feeds"),
    path("feeds/<int:pk>/", feeds_ops.feed_detail, name="feed-detail"),
    path("tasks/", events_ops.tasks, name="tasks"),
    path("logs/", events_ops.logs, name="logs"),
    path("chat/", reader_ops.chat_home, name="chat"),
    path("chat/<int:pk>/", reader_ops.chat_session, name="chat-session"),
    path("chat/<int:pk>/send", reader_ops.chat_send, name="chat-send"),
    path("chat/message/<int:pk>/", reader_ops.chat_message, name="chat-message"),
    path("settings/", views.settings_page, name="settings"),
]
