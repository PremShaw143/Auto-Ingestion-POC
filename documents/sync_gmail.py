
import os
import sys
import django
import base64
from pathlib import Path
from datetime import datetime, timezone

# Resolve the backend directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Make the Django project importable
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from documents.models import Message


TOKEN_FILE = BASE_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_header(headers, name):
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def get_body(payload):
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(
            body_data + "=" * (-len(body_data) % 4)
        ).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = get_body(part)
        if result:
            return result

    return ""


def main():
    credentials = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )
    service = build("gmail", "v1", credentials=credentials)

    results = service.users().messages().list(
        userId="me",
        maxResults=5,
    ).execute()

    saved = 0

    for item in results.get("messages", []):
        email = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="full",
        ).execute()

        payload = email.get("payload", {})
        headers = payload.get("headers", [])

        timestamp = int(email.get("internalDate", "0")) / 1000
        received_at = datetime.fromtimestamp(
            timestamp, tz=timezone.utc
        )

        content = get_body(payload) or email.get("snippet", "")

        _, created = Message.objects.get_or_create(
            provider="gmail",
            external_id=email["id"],
            defaults={
                "sender": get_header(headers, "From"),
                "subject": get_header(headers, "Subject"),
                "content": content,
                "received_at": received_at,
            },
        )

        if created:
            saved += 1

    print(f"New Gmail messages saved: {saved}")


if __name__ == "__main__":
    main()
