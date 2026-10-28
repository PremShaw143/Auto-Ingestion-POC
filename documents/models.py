from django.db import models

# Create your models here.

import uuid

from django.db import models


class Message(models.Model):
    class Provider(models.TextChoices):
        GMAIL = "gmail", "Gmail"
        WHATSAPP = "whatsapp", "WhatsApp"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    external_id = models.CharField(max_length=255)
    sender = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    content = models.TextField(blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="unique_provider_external_message",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider}: {self.external_id}"


class Attachment(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="attachments/")
    content_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.filename
