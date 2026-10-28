
import base64
import mimetypes
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from documents.models import Attachment, Message


class GmailService:
    """Fetch Gmail messages, save attachments, synchronize, and send replies."""

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    def __init__(self):
        self.token_file = Path(settings.BASE_DIR) / "token.json"

    # ---------------------------------------------------------
    # 1. Gmail authentication
    # ---------------------------------------------------------
    def _get_gmail_client(self):
        """Create an authenticated Gmail API client."""

        if not self.token_file.exists():
            raise FileNotFoundError(
                f"Gmail token.json not found at {self.token_file}. "
                "Run gmail_auth.py first."
            )

        credentials = Credentials.from_authorized_user_file(
            str(self.token_file),
            self.SCOPES,
        )

        if not credentials.valid and credentials.refresh_token:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
            self.token_file.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )

        if not credentials.valid:
            raise RuntimeError(
                "Gmail credentials are invalid. Re-authorize Gmail."
            )

        return build("gmail", "v1", credentials=credentials)

    # ---------------------------------------------------------
    # 2. Read Gmail headers and message bodies
    # ---------------------------------------------------------
    @staticmethod
    def _get_header(headers, name):
        """Return a header value, ignoring case."""

        for header in headers:
            if header.get("name", "").lower() == name.lower():
                return header.get("value", "")

        return ""

    @staticmethod
    def _decode_data(data):
        """Decode Gmail URL-safe Base64 data."""

        if not data:
            return b""

        return base64.urlsafe_b64decode(
            data + "=" * (-len(data) % 4)
        )

    @classmethod
    def _get_body(cls, payload):
        """Extract plain-text content from a Gmail payload."""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data")

        if mime_type == "text/plain" and body_data:
            return cls._decode_data(body_data).decode(
                "utf-8",
                errors="replace",
            )

        for part in payload.get("parts", []):
            result = cls._get_body(part)
            if result:
                return result

        return ""

    # ---------------------------------------------------------
    # 3. List emails and support Gmail search filters
    # ---------------------------------------------------------
    def list_recent_emails(self, limit=100, query=None):
        """
        List Gmail messages with pagination.

        Args:
            limit: Maximum number of messages to fetch (1-500).
            query: Optional Gmail search query, e.g.
                   from:sender@example.com after:2026/10/26

        Returns:
            List of Gmail message metadata dictionaries.
        """

        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")

        gmail = self._get_gmail_client()
        results = []
        page_token = None

        while len(results) < limit:
            page_size = min(100, limit - len(results))

            request_args = {
                "userId": "me",
                "maxResults": page_size,
            }

            if query:
                request_args["q"] = query

            if page_token:
                request_args["pageToken"] = page_token

            page = (
                gmail.users()
                .messages()
                .list(**request_args)
                .execute(num_retries=3)
            )

            messages = page.get("messages", [])

            for item in messages:
                if len(results) >= limit:
                    break

                email = (
                    gmail.users()
                    .messages()
                    .get(
                        userId="me",
                        id=item["id"],
                        format="metadata",
                        metadataHeaders=[
                            "From",
                            "To",
                            "Subject",
                            "Date",
                        ],
                    )
                    .execute(num_retries=3)
                )

                payload = email.get("payload", {})
                headers = payload.get("headers", [])

                internal_date = email.get("internalDate")
                received_at = None

                if internal_date:
                    received_at = datetime.fromtimestamp(
                        int(internal_date) / 1000,
                        tz=timezone.utc,
                    ).isoformat()

                sender = self._get_header(headers, "From")

                results.append({
                    "message_id": email["id"],
                    "gmail_message_id": email["id"],
                    "thread_id": email.get("threadId"),
                    "sender": sender,
                    "user_email": sender,
                    "to": self._get_header(headers, "To"),
                    "subject": self._get_header(headers, "Subject"),
                    "date_header": self._get_header(headers, "Date"),
                    "date": received_at[:10] if received_at else None,
                    "received_at": received_at,
                    "timestamp": received_at,
                    "snippet": email.get("snippet", ""),
                })

            page_token = page.get("nextPageToken")

            if not page_token or not messages:
                break

        return results

    # ---------------------------------------------------------
    # 4. Synchronize Gmail messages to the local database
    # ---------------------------------------------------------
    def sync_messages(self, limit=5):
        """Fetch recent Gmail messages and save basic details."""

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")

        gmail = self._get_gmail_client()

        response = (
            gmail.users()
            .messages()
            .list(
                userId="me",
                maxResults=limit,
                q="newer_than:7d",
            )
            .execute(num_retries=3)
        )

        saved = 0
        processed = 0

        for item in response.get("messages", []):
            email = (
                gmail.users()
                .messages()
                .get(
                    userId="me",
                    id=item["id"],
                    format="full",
                )
                .execute(num_retries=3)
            )

            payload = email.get("payload", {})
            headers = payload.get("headers", [])

            timestamp = int(email.get("internalDate", "0")) / 1000
            received_at = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

            content = (
                self._get_body(payload)
                or email.get("snippet", "")
            )

            _, created = Message.objects.get_or_create(
                provider="gmail",
                external_id=email["id"],
                defaults={
                    "sender": self._get_header(headers, "From"),
                    "subject": self._get_header(headers, "Subject"),
                    "content": content,
                    "received_at": received_at,
                },
            )

            processed += 1

            if created:
                saved += 1

        return {
            "provider": "gmail",
            "processed": processed,
            "saved": saved,
            "message": "Gmail sync completed.",
        }

    # ---------------------------------------------------------
    # 5. Extract selected email body and attachments
    # ---------------------------------------------------------
    def _extract_selected_email(self, gmail, message_id, payload):
        """Extract text/HTML bodies and attachment bytes."""

        plain_parts = []
        html_parts = []
        attachment_parts = []
        inline_counter = 0

        def walk(part):
            nonlocal inline_counter

            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            data = body.get("data")
            gmail_attachment_id = body.get("attachmentId")
            filename = Path(part.get("filename", "")).name

            is_file = bool(filename) or (
                mime_type.startswith("image/")
                and bool(gmail_attachment_id or data)
            )

            if is_file:
                if not filename:
                    inline_counter += 1
                    extension = (
                        mimetypes.guess_extension(mime_type) or ".bin"
                    )
                    filename = f"inline-image-{inline_counter}{extension}"

                file_bytes = b""

                if gmail_attachment_id:
                    attachment_response = (
                        gmail.users()
                        .messages()
                        .attachments()
                        .get(
                            userId="me",
                            messageId=message_id,
                            id=gmail_attachment_id,
                        )
                        .execute(num_retries=3)
                    )

                    file_bytes = self._decode_data(
                        attachment_response.get("data", "")
                    )

                elif data:
                    file_bytes = self._decode_data(data)

                if file_bytes:
                    attachment_parts.append({
                        "filename": filename,
                        "content_type": (
                            mime_type
                            or mimetypes.guess_type(filename)[0]
                            or "application/octet-stream"
                        ),
                        "data": file_bytes,
                    })

            elif data:
                decoded = self._decode_data(data).decode(
                    "utf-8",
                    errors="replace",
                )

                if mime_type == "text/plain":
                    plain_parts.append(decoded)

                elif mime_type == "text/html":
                    html_parts.append(decoded)

            for child in part.get("parts", []):
                walk(child)

        walk(payload)

        return (
            "\n".join(plain_parts),
            "\n".join(html_parts),
            attachment_parts,
        )

    # ---------------------------------------------------------
    # 6. Save or repair a local attachment
    # ---------------------------------------------------------
    def _save_attachment(self, message, item):
        """Create or repair an attachment's locally stored file."""

        filename = Path(item["filename"]).name or "attachment.bin"

        attachment = message.attachments.filter(
            filename=filename
        ).first()

        if attachment is None:
            attachment = Attachment(
                message=message,
                filename=filename,
                content_type=item["content_type"],
            )

        file_exists = bool(
            attachment.file
            and attachment.file.name
            and attachment.file.storage.exists(attachment.file.name)
        )

        if not file_exists:
            attachment.file.save(
                filename,
                ContentFile(item["data"]),
                save=False,
            )

        attachment.filename = filename
        attachment.content_type = item["content_type"]
        attachment.save()

        return attachment

    # ---------------------------------------------------------
    # 7. Fetch one Gmail message and save it locally
    # ---------------------------------------------------------
    def fetch_selected_message(self, external_id):
        """Fetch one Gmail message and save its attachments locally."""

        gmail = self._get_gmail_client()

        email = (
            gmail.users()
            .messages()
            .get(
                userId="me",
                id=external_id,
                format="full",
            )
            .execute(num_retries=3)
        )

        payload = email.get("payload", {})
        headers = payload.get("headers", [])

        plain_body, html_body, files = self._extract_selected_email(
            gmail,
            external_id,
            payload,
        )

        content = plain_body or email.get("snippet", "")

        timestamp = int(email.get("internalDate", "0")) / 1000
        received_at = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )

        message, _ = Message.objects.update_or_create(
            provider="gmail",
            external_id=external_id,
            defaults={
                "sender": self._get_header(headers, "From"),
                "subject": self._get_header(headers, "Subject"),
                "content": content,
                "received_at": received_at,
            },
        )

        saved_attachments = []

        for item in files:
            attachment = self._save_attachment(message, item)

            saved_attachments.append({
                "id": str(attachment.id),
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size_bytes": len(item["data"]),
                "url": (
                    attachment.file.url
                    if attachment.file
                    else None
                ),
                "saved_path": (
                    str(
                        Path(settings.MEDIA_ROOT)
                        / attachment.file.name
                    )
                    if attachment.file
                    else None
                ),
            })

        return {
            "id": str(message.id),
            "gmail_message_id": external_id,
            "thread_id": email.get("threadId"),
            "sender": message.sender,
            "subject": message.subject,
            "received_at": message.received_at.isoformat(),
            "body_text": plain_body or content,
            "body_html": html_body,
            "attachments": saved_attachments,
        }

    # ---------------------------------------------------------
    # 8. Reply to a Gmail email
    # ---------------------------------------------------------
    def send_reply(self, original_message_id, reply_text):
        """Send a reply in the original Gmail conversation."""

        if not isinstance(reply_text, str) or not reply_text.strip():
            raise ValueError("Reply text cannot be empty.")

        gmail = self._get_gmail_client()

        original = (
            gmail.users()
            .messages()
            .get(
                userId="me",
                id=original_message_id,
                format="full",
            )
            .execute(num_retries=3)
        )

        headers = original.get("payload", {}).get("headers", [])

        recipient = (
            self._get_header(headers, "Reply-To")
            or self._get_header(headers, "From")
        )

        if not recipient:
            raise ValueError("Original email has no reply address.")

        subject = self._get_header(headers, "Subject")

        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}" if subject else "Re:"

        original_rfc_message_id = self._get_header(
            headers,
            "Message-ID",
        )
        references = self._get_header(headers, "References")

        if original_rfc_message_id:
            references = (
                f"{references} {original_rfc_message_id}".strip()
            )

        message = MIMEText(reply_text.strip(), "plain", "utf-8")
        message["To"] = recipient
        message["Subject"] = subject

        if original_rfc_message_id:
            message["In-Reply-To"] = original_rfc_message_id

        if references:
            message["References"] = references

        raw_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode("utf-8").rstrip("=")

        result = (
            gmail.users()
            .messages()
            .send(
                userId="me",
                body={
                    "raw": raw_message,
                    "threadId": original.get("threadId"),
                },
            )
            .execute(num_retries=3)
        )

        return {
            "status": "sent",
            "thread_id": result.get("threadId"),
            "sent_message_id": result.get("id"),
        }
