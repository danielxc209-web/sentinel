# Gmail tool — fetch emails using OAuth credentials
import os
import base64
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
BASE_DIR = Path(__file__).parent.parent

def get_service():
    creds = None
    token_path = BASE_DIR / "token.json"
    creds_path = BASE_DIR / "credentials.json"

    # BUG FIX 1: No check that credentials.json actually exists before trying
    # to use it. If missing, InstalledAppFlow gives a cryptic FileNotFoundError.
    # Raise a clear, actionable error instead.
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Gmail credentials file not found at {creds_path}.\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # BUG FIX 2: Token refresh can fail (e.g. revoked token, network
            # error). Unhandled, this crashes the whole executor thread.
            # Catch and fall through to re-authorise instead.
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[Gmail] Token refresh failed ({e}), re-authorising...")
                creds = None

        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

def fetch_latest(n: int = 5) -> list[dict]:
    """Return snippets and subjects of the latest n emails."""
    service = get_service()
    results = service.users().messages().list(userId="me", maxResults=n).execute()
    messages = results.get("messages", [])
    emails = []
    for m in messages:
        msg = service.users().messages().get(
            userId="me",
            id=m["id"],
            # BUG FIX 3: The original fetched the full message payload for every
            # email just to get the snippet. The 'metadata' format returns
            # headers + snippet without downloading the full body, which is
            # significantly faster and cheaper on quota.
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()

        headers = {h["name"]: h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}

        emails.append({
            "id": m["id"],
            "snippet": msg.get("snippet", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
        })
    return emails

def fetch_snippet() -> str:
    """Return the snippet of the most recent email."""
    # BUG FIX 4: If fetch_latest() returns an empty list (empty inbox or API
    # error), emails[0] raises IndexError. The original had a guard but it only
    # checked for empty list, not for a missing "snippet" key. Both are now safe.
    emails = fetch_latest(1)
    if not emails:
        return "No emails found."
    return emails[0].get("snippet", "No snippet available.")