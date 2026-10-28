
from django.urls import path

from .views import (
    gmail_attachment_file,
    gmail_message_detail,
    gmail_message_list,
    gmail_sync,
    gmail_reply,
)

urlpatterns = [
    path(
        "gmail/messages/",
        gmail_message_list,
        name="gmail-message-list",
    ),
    path(
        "gmail/messages/<str:message_id>/",
        gmail_message_detail,
        name="gmail-message-detail",
    ),
    path(
        "gmail/sync/",
        gmail_sync,
        name="gmail-sync",
    ),
    path(
        "gmail/reply/",
        gmail_reply,
        name="gmail-reply",
    ),
    path(
        "gmail/attachments/<uuid:attachment_id>/",
        gmail_attachment_file,
        name="gmail-attachment-file",
    ),
]
