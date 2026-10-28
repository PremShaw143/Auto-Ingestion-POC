
import base64
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_FILE = BASE_DIR / "token.json"


def get_header(headers, name):
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def main():
    if not TOKEN_FILE.exists():
        raise FileNotFoundError("Gmail token.json not found.")

    credentials = Credentials.from_authorized_user_file(
        str(TOKEN_FILE),
        [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )

    service = build("gmail", "v1", credentials=credentials)

    profile = service.users().getProfile(userId="me").execute()
    print("Connected Gmail:", profile.get("emailAddress"))

    results = service.users().messages().list(
        userId="me",
        maxResults=5,
    ).execute()

    messages = results.get("messages", [])
    print("Messages found:", len(messages))

    for item in messages:
        message = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="full",
        ).execute()

        headers = message.get("payload", {}).get("headers", [])
        sender = get_header(headers, "From")
        subject = get_header(headers, "Subject")
        snippet = message.get("snippet", "")

        print("-" * 50)
        print("From:", sender)
        print("Subject:", subject)
        print("Preview:", snippet)


if __name__ == "__main__":
    main()
