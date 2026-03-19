# Gmail tool — fetch emails using OAuth credentials
import os
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

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

def fetch_latest(n: int = 5) -> list[dict]:
    """Return snippets of the latest n emails."""
    service = get_service()
    results = service.users().messages().list(userId="me", maxResults=n).execute()
    messages = results.get("messages", [])
    emails = []
    for m in messages:
        msg = service.users().messages().get(userId="me", id=m["id"]).execute()
        emails.append({
            "id": m["id"],
            "snippet": msg.get("snippet", ""),
        })
    return emails

def fetch_snippet() -> str:
    """Return the snippet of the most recent email."""
    emails = fetch_latest(1)
    return emails[0]["snippet"] if emails else "No emails found."
