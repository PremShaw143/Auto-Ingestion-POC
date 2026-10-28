
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
import uuid

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from googleapiclient.errors import HttpError

from .models import Message, Attachment
from .services.gmail_service import GmailService


logger = logging.getLogger(__name__)


# 1. List and filter Gmail messages
@api_view(["GET"])
def gmail_message_list(request):
    """
    Supported query parameters:
    user_email, date, timestamp,
    received_after, received_before, limit
    """

    user_email = (request.query_params.get("user_email") or "").strip()
    date_value = (request.query_params.get("date") or "").strip()
    timestamp_value = (request.query_params.get("timestamp") or "").strip()
    received_after = (
        request.query_params.get("received_after") or ""
    ).strip()
    received_before = (
        request.query_params.get("received_before") or ""
    ).strip()

    try:
        limit = int(request.query_params.get("limit", 10))

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        def parse_iso_datetime(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        query_terms = []

        # Sender filter
        if user_email:
            _, sender_email = parseaddr(user_email)
            sender_email = sender_email or user_email

            if (
                "@" not in sender_email
                or any(ch.isspace() for ch in sender_email)
            ):
                return Response(
                    {"error": "user_email must contain a valid email address."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            query_terms.append(f"from:{sender_email}")

        # Date filter
        if date_value:
            selected_date = datetime.strptime(
                date_value, "%Y-%m-%d"
            ).date()

            start_of_day = datetime.combine(
                selected_date,
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            next_day = start_of_day + timedelta(days=1)

            query_terms.extend([
                f"after:{int(start_of_day.timestamp()) - 1}",
                f"before:{int(next_day.timestamp())}",
            ])

        # Exact timestamp filter
        if timestamp_value:
            exact_time = parse_iso_datetime(timestamp_value)

            query_terms.extend([
                f"after:{int(exact_time.timestamp()) - 1}",
                f"before:{int((exact_time + timedelta(seconds=1)).timestamp())}",
            ])

        # Date range filters
        if received_after:
            after_dt = parse_iso_datetime(received_after)
            query_terms.append(f"after:{int(after_dt.timestamp()) - 1}")

        if received_before:
            before_dt = parse_iso_datetime(received_before)
            query_terms.append(f"before:{int(before_dt.timestamp()) + 1}")

        gmail_query = " ".join(query_terms) or None

        # GmailService.list_recent_emails must support query.
        emails = GmailService().list_recent_emails(
            limit=limit,
            query=gmail_query,
        )

        def email_datetime(email):
            value = email.get("received_at") or email.get("timestamp")

            if not value:
                return None

            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = parse_iso_datetime(value)

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        # Apply precise timestamp/date checks to returned results.
        if date_value:
            emails = [
                email for email in emails
                if email_datetime(email)
                and email_datetime(email).date() == selected_date
            ]

        if timestamp_value:
            target = parse_iso_datetime(timestamp_value)
            emails = [
                email for email in emails
                if email_datetime(email)
                and abs(
                    (email_datetime(email) - target).total_seconds()
                ) < 1
            ]

        if received_after:
            start = parse_iso_datetime(received_after)
            emails = [
                email for email in emails
                if email_datetime(email)
                and email_datetime(email) >= start
            ]

        if received_before:
            end = parse_iso_datetime(received_before)
            emails = [
                email for email in emails
                if email_datetime(email)
                and email_datetime(email) <= end
            ]

        return Response(
            {
                "count": len(emails),
                "limit": limit,
                "filters": {
                    "user_email": user_email or None,
                    "date": date_value or None,
                    "timestamp": timestamp_value or None,
                    "received_after": received_after or None,
                    "received_before": received_before or None,
                },
                "results": emails,
            },
            status=status.HTTP_200_OK,
        )

    except ValueError as exc:
        return Response(
            {
                "error": "Invalid query parameter.",
                "detail": str(exc),
                "expected_date": "YYYY-MM-DD",
                "expected_timestamp": "ISO-8601, e.g. 2026-10-27T13:01:08Z",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    except HttpError as exc:
        logger.exception("Gmail API request failed while listing messages")

        if exc.resp.status == 403:
            return Response(
                {
                    "error": "Gmail API quota or permission limit reached.",
                    "detail": "Check Gmail API permissions and quota.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        return Response(
            {
                "error": "Gmail API request failed.",
                "detail": f"Google API returned HTTP {exc.resp.status}.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    except FileNotFoundError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    except Exception:
        logger.exception("Gmail message listing failed")
        return Response(
            {"error": "Unable to list Gmail messages. Check backend logs."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# 2. Fetch selected Gmail message details
@api_view(["GET"])
def gmail_message_detail(request, message_id):
    """
    message_id should be the Gmail external message ID,
    not an attachment UUID.
    """
    try:
        # Support a local Message UUID as well as a Gmail message ID.
        try:
            uuid.UUID(message_id)
            is_local_uuid = True
        except (ValueError, TypeError, AttributeError):
            is_local_uuid = False

        if is_local_uuid:
            try:
                message = Message.objects.get(
                    pk=message_id,
                    provider="gmail",
                )
            except Message.DoesNotExist:
                return Response(
                    {"error": "Saved Gmail message not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            message_id = message.external_id

            if not message_id:
                return Response(
                    {"error": "Saved message has no Gmail external ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        result = GmailService().fetch_selected_message(message_id)

        return Response(result, status=status.HTTP_200_OK)

    except FileNotFoundError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    except Exception:
        logger.exception("Selected Gmail message fetch failed")
        return Response(
            {"error": "Unable to fetch Gmail message. Check backend logs."},
            status=status.HTTP_502_BAD_GATEWAY,
        )


# 3. Synchronize Gmail messages
@api_view(["POST"])
def gmail_sync(request):
    limit = request.data.get("limit", 5)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return Response(
            {"error": "limit must be an integer"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not 1 <= limit <= 100:
        return Response(
            {"error": "limit must be between 1 and 100"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = GmailService().sync_messages(limit=limit)
        return Response(result, status=status.HTTP_200_OK)

    except FileNotFoundError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    except Exception:
        logger.exception("Gmail sync failed")
        return Response(
            {"error": "Gmail sync failed. Check backend logs."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# 4. Reply to a Gmail email
@api_view(["POST"])
def gmail_reply(request):
    message_id = str(request.data.get("message_id", "")).strip()
    reply_text = request.data.get("text")

    if (
        not message_id
        or not isinstance(reply_text, str)
        or not reply_text.strip()
    ):
        return Response(
            {"error": "message_id and non-empty text are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Resolve a local Django UUID to Gmail's external message ID.
    try:
        uuid.UUID(message_id)
        is_local_uuid = True
    except (ValueError, TypeError, AttributeError):
        is_local_uuid = False

    if is_local_uuid:
        try:
            original = Message.objects.get(
                pk=message_id,
                provider="gmail",
            )
        except Message.DoesNotExist:
            return Response(
                {"error": "Saved Gmail message not found for this local UUID."},
                status=status.HTTP_404_NOT_FOUND,
            )

        gmail_message_id = original.external_id

        if not gmail_message_id:
            return Response(
                {"error": "The saved message has no Gmail external ID."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        gmail_message_id = message_id

    try:
        result = GmailService().send_reply(
            original_message_id=gmail_message_id,
            reply_text=reply_text.strip(),
        )
        return Response(result, status=status.HTTP_200_OK)

    except ValueError as exc:
        return Response(
            {"error": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    except Exception:
        logger.exception("Gmail reply failed for message ID %s", message_id)
        return Response(
            {
                "error": "Gmail reply failed.",
                "detail": (
                    "Check backend logs and confirm Gmail send permission "
                    "is authorized."
                ),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )


# 5. Preview or download a Gmail attachment
@api_view(["GET"])
def gmail_attachment_file(request, attachment_id):
    attachment = get_object_or_404(
        Attachment.objects.select_related("message"),
        pk=attachment_id,
        message__provider="gmail",
    )

    file_exists = (
        bool(attachment.file and attachment.file.name)
        and attachment.file.storage.exists(attachment.file.name)
    )

    # Try restoring the file from Gmail if it is missing locally.
    if not file_exists:
        try:
            GmailService().fetch_selected_message(
                attachment.message.external_id
            )
            attachment.refresh_from_db()

            file_exists = (
                bool(attachment.file and attachment.file.name)
                and attachment.file.storage.exists(attachment.file.name)
            )

        except Exception:
            logger.exception(
                "Failed to restore missing Gmail attachment %s",
                attachment_id,
            )

    if not file_exists:
        return Response(
            {
                "error": "Attachment file not found locally.",
                "detail": (
                    "Check Gmail access and the configured media storage."
                ),
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    content_type = attachment.content_type or "application/octet-stream"

    try:
        response = FileResponse(
            attachment.file.open("rb"),
            content_type=content_type,
        )

        disposition = (
            "inline"
            if content_type.startswith("image/")
            or content_type == "application/pdf"
            else "attachment"
        )

        filename = Path(attachment.filename).name
        response["Content-Disposition"] = (
            f'{disposition}; filename="{filename}"'
        )

        return response

    except FileNotFoundError:
        logger.exception(
            "Attachment disappeared while opening file %s",
            attachment_id,
        )
        return Response(
            {"error": "Attachment is missing from local storage."},
            status=status.HTTP_404_NOT_FOUND,
        )
