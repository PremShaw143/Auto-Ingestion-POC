
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).resolve().parent.parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def authorize_gmail():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"OAuth credentials not found: {CREDENTIALS_FILE}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        SCOPES,
    )

    credentials = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w", encoding="utf-8") as token:
        token.write(credentials.to_json())

    print("Gmail OAuth authorization successful.")
    print(f"Token saved to: {TOKEN_FILE}")


if __name__ == "__main__":
    authorize_gmail()
