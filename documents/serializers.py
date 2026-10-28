
from rest_framework import serializers
from .models import Message, Attachment


class AttachmentSerializer(serializers.ModelSerializer):
    message = serializers.UUIDField(write_only=True)
    file = serializers.FileField(write_only=False)

    class Meta:
        model = Attachment
        fields = [
            "id",
            "message",
            "filename",
            "file",
            "content_type",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "filename",
            "content_type",
            "created_at",
        ]


class MessageSerializer(serializers.ModelSerializer):
    attachments = AttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "provider",
            "external_id",
            "sender",
            "subject",
            "content",
            "received_at",
            "created_at",
            "attachments",
        ]
