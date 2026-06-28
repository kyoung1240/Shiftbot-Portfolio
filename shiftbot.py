import base64
import json
import logging
import pickle
import sys

sys.stdout.reconfigure(encoding="utf-8")
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Search for the latest shift-related emails within the last hour
GMAIL_SEARCH_QUERY = 'newer_than:1h (schedule OR shift OR shifts)'

# Brandon's follow-up email has ": UPDATED" in the subject (e.g. "JUNE SCHEDULE: UPDATED").
# Matching on ": updated" is precise enough to avoid accidentally skipping real shift emails.
SKIP_SUBJECT_PATTERN = re.compile(r":\bupdated\b", re.IGNORECASE)

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "check_interval_seconds": 2,
    "minimize_to_tray": True,
    "auto_reply": True
}

def load_settings():
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_SETTINGS.copy()

def save_settings():
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4)

settings = load_settings()
TOKEN_PATH = BASE_DIR / "token.pickle"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
MAX_RESULTS = 10
SEEN_IDS_PATH = BASE_DIR / "seen_ids.txt"


def load_seen_ids() -> set[str]:
    if not SEEN_IDS_PATH.exists():
        return set()
    with open(SEEN_IDS_PATH, "r", encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def save_seen_ids(seen_ids: set[str]) -> None:
    with open(SEEN_IDS_PATH, "w", encoding="utf-8") as fh:
        for message_id in sorted(seen_ids):
            fh.write(f"{message_id}\n")

# Map day name → list of (start, end) blocked time ranges (24-hr "HH:MM" strings).
BLOCKED_TIMES: dict[str, list[tuple[str, str]]] = {
    "Tuesday": [("17:00", "20:30")],
}

# Shift date/time pattern: "MM/DD/YYYY HHMM-HHMM"
SHIFT_PATTERN = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{4})-(\d{4})")


# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Shift:
    date: str          # "MM/DD/YYYY"
    start: str         # "HHMM"
    end: str           # "HHMM"
    start_dt: datetime
    end_dt: datetime
    day: str           # e.g. "Tuesday"

    def __str__(self) -> str:
        return f"{self.date} {self.start}–{self.end} ({self.day})"


# ── Auth ───────────────────────────────────────────────────────────────────────
def get_gmail_service():
    """Return an authenticated Gmail API service, refreshing credentials if needed."""
    creds = None

    if TOKEN_PATH.exists():
        try:
            with open(TOKEN_PATH, "rb") as fh:
                creds = pickle.load(fh)
        except (pickle.UnpicklingError, EOFError) as e:
            log.warning(f"Corrupted token detected: {e}")

            try:
                TOKEN_PATH.unlink()
                log.info("Deleted corrupted token. Re-authenticating...")
            except OSError:
                pass

            creds = None
    if creds and creds.expired and creds.refresh_token:
        log.info("Refreshing expired credentials…")
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Missing {CREDENTIALS_PATH!r}. Download it from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "wb") as fh:
        pickle.dump(creds, fh)

    return build("gmail", "v1", credentials=creds)


# ── Email helpers ──────────────────────────────────────────────────────────────
def get_email_body(payload: dict) -> str:
    """Extract plain text from a Gmail message payload.

    Recursively walks nested multipart structures so shifts are never missed
    when HTML is buried inside multipart/alternative or multipart/mixed.
    Plain text is preferred; HTML is stripped via BeautifulSoup as a fallback.
    """

    def decode(data: str) -> str:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Handle single-part messages at the top level
    body_data = payload.get("body", {}).get("data")
    if body_data:
        return decode(body_data)

    html_fallback: Optional[str] = None

    def walk(part: dict):
        nonlocal html_fallback
        part_data = part.get("body", {}).get("data")
        mime = part.get("mimeType", "")

        if part_data:
            decoded = decode(part_data)
            if mime == "text/plain":
                # Only treat as plain text if it isn't secretly HTML
                if "<html" not in decoded.lower() and "<table" not in decoded.lower():
                    return decoded
            if mime == "text/html":
                html_fallback = decoded

        for child in part.get("parts", []):
            result = walk(child)
            if result:
                return result

        return None

    text = walk(payload)
    if text:
        return text

    if html_fallback:
        return BeautifulSoup(html_fallback, "html.parser").get_text("\n")

    return ""


def get_header(headers: list[dict], name: str) -> str:
    """Case-insensitive header lookup."""
    name_lower = name.lower()
    return next((h["value"] for h in headers if h["name"].lower() == name_lower), "")


def is_update_email(headers: list[dict]) -> bool:
    """Return True if the subject matches ': UPDATED'."""
    subject = get_header(headers, "subject")
    return bool(SKIP_SUBJECT_PATTERN.search(subject))

# ── Shift parsing ──────────────────────────────────────────────────────────────
def parse_shifts(text: str) -> list[Shift]:
    """Parse all unique shifts from email body text."""
    shifts: list[Shift] = []
    seen: set[tuple[str, str, str]] = set()

    for date_str, start_str, end_str in SHIFT_PATTERN.findall(text):
        key = (date_str, start_str, end_str)
        if key in seen:
            continue
        seen.add(key)

        try:
            start_dt = datetime.strptime(f"{date_str} {start_str}", "%m/%d/%Y %H%M")
            end_dt = datetime.strptime(f"{date_str} {end_str}", "%m/%d/%Y %H%M")
        except ValueError as exc:
            log.warning("Skipping unparseable shift %s: %s", key, exc)
            continue

        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        shifts.append(
            Shift(
                date=date_str,
                start=start_str,
                end=end_str,
                start_dt=start_dt,
                end_dt=end_dt,
                day=start_dt.strftime("%A"),
            )
        )

    return shifts


def is_blocked(shift: Shift) -> bool:
    """Return True if a shift overlaps any configured blocked window."""
    for block_start, block_end in BLOCKED_TIMES.get(shift.day, []):
        date = shift.start_dt.date()
        fmt = "%Y-%m-%d %H:%M"
        block_start_dt = datetime.strptime(f"{date} {block_start}", fmt)
        block_end_dt = datetime.strptime(f"{date} {block_end}", fmt)
        if shift.start_dt < block_end_dt and shift.end_dt > block_start_dt:
            return True
    return False


def usable_shifts(shifts: list[Shift]) -> list[Shift]:
    return sorted(
        (s for s in shifts if not is_blocked(s)),
        key=lambda s: s.start_dt,
    )


# ── Reply ──────────────────────────────────────────────────────────────────────
def make_reply(shifts: list[Shift]) -> str:
    shift_lines = "\n".join(
        f"* {s.date} ({s.day}) {s.start}-{s.end}"
        for s in shifts
    )

    return (
        "Hello,\n\n"
        "I would like to take all of the following shifts:\n\n"
        f"{shift_lines}\n\n"
        "Thank you."
    )


def send_reply(service, original_message: dict, reply_body: str) -> None:
    """Reply ONLY to the sender — never reply-all — so the response counts."""
    headers = original_message["payload"]["headers"]
    subject = get_header(headers, "subject") or "(no subject)"
    message_id = get_header(headers, "message-id")

    # Use Reply-To if present, otherwise fall back to From.
    # Never use CC/BCC — reply to the individual sender only.
    sender = get_header(headers, "reply-to") or get_header(headers, "from")

    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    msg = MIMEText(reply_body)
    msg["To"] = sender          # single recipient only
    msg["Subject"] = subject
    # Deliberately omitting CC/BCC so this is never a reply-all
    if message_id:
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": original_message["threadId"]},
    ).execute()


# ── Email search ───────────────────────────────────────────────────────────────
def search_emails(service, query: str, seen_ids: set[str]) -> list[dict]:
    try:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=MAX_RESULTS
        ).execute()
        log.debug("Raw Gmail results: %s", results)
    except HttpError as exc:
        log.error("Gmail API error during search: %s", exc)
        return []

    found = []
    # Process newest emails first (they come sorted newest first from API)
    for msg_stub in results.get("messages", []):
        msg_id = msg_stub["id"]
        if msg_id in seen_ids:
            continue

        try:
            full = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
        except HttpError as exc:
            log.warning("Could not fetch message %s: %s", msg_id, exc)
            continue

        headers = full["payload"]["headers"]
        subject = get_header(headers, "subject")
        message_id = get_header(headers, "message-id")

        if is_update_email(headers):
            log.info("Skipping update email: %r", subject)
            continue

        body = get_email_body(full["payload"])
        if parse_shifts(body):
            full["message_id_header"] = message_id  # Store for later check
            found.append(full)

    return found


def find_schedule_emails(service, seen_ids: set[str], last_seen_message_id: str = None) -> tuple[list[dict], str]:
    """
    Search for the latest shift emails from anyone.
    Skips if the first email's message-id matches last_seen_message_id (indicates no new emails).
    Returns (emails, latest_message_id).
    """
    results = search_emails(service, GMAIL_SEARCH_QUERY, seen_ids)
    log.debug("Search results: %s", results)
    if not results:
        return [], last_seen_message_id
    
    # Check if the newest email is the same as last time (no new emails)
    newest_msg = results[0]
    newest_msg_id_header = newest_msg.get("message_id_header")
    
    if newest_msg_id_header and newest_msg_id_header == last_seen_message_id:
        log.info("Old email, skipping")
        return [], last_seen_message_id
    
    # Add new emails to seen_ids and return
    emails = []
    for msg in results:
        msg_id = msg["id"]
        if msg_id not in seen_ids:
            seen_ids.add(msg_id)
            emails.append(msg)
            log.info("Found shift email: id=%s", msg_id)
    
    # Return latest message ID for next iteration
    latest_msg_id = newest_msg_id_header if newest_msg_id_header else last_seen_message_id
    return emails, latest_msg_id


# ── Entry point ────────────────────────────────────────────────────────────────
def now_stamp() -> str:
    return datetime.now().strftime("[%I:%M:%S %p]")

def main() -> None:
    log.info("ShiftBot starting…")

    try:
        service = get_gmail_service()
    except FileNotFoundError as exc:
        log.error(exc)
        return

    profile = service.users().getProfile(userId="me").execute()
    log.info("Connected to Gmail: %s", profile["emailAddress"])
    print(f"{now_stamp()} ShiftBot ready.\n")
    
    seen_ids = load_seen_ids()
    check_interval = settings.get("check_interval_seconds", 2)
    last_seen_message_id = None

    while True:
        print(f"{now_stamp()} Checking Gmail...")

        try:
            new_emails, last_seen_message_id = find_schedule_emails(service, seen_ids, last_seen_message_id)
        except Exception as exc:
            log.error("Gmail check failed: %s", exc)
            print(f"{now_stamp()} Gmail check failed. Retrying in {check_interval} seconds.\n")
            time.sleep(check_interval)
            continue

        if not new_emails:
            print(f"{now_stamp()} No new schedule emails.\n")
            time.sleep(check_interval)
            continue

        log.info("Found %d shift email(s) to process.", len(new_emails))
        save_seen_ids(seen_ids)

        for idx, email in enumerate(new_emails, 1):
            headers = email["payload"]["headers"]
            subject = get_header(headers, "subject")
            sender = get_header(headers, "from")
            
            log.info("── Email %d/%d ──────────────────────", idx, len(new_emails))
            log.info("  From:    %s", sender)
            log.info("  Subject: %s", subject)

            body = get_email_body(email["payload"])
            all_shifts = parse_shifts(body)
            log.info("  %d total shift(s) found.", len(all_shifts))

            good_shifts = usable_shifts(all_shifts)
            blocked_count = len(all_shifts) - len(good_shifts)
            log.info(
                "  %d usable; %d blocked (Tuesday 5:00–8:30 PM).",
                len(good_shifts), blocked_count,
            )

            if not good_shifts:
                log.info("  No usable shifts — skipping.")
                continue

            reply = make_reply(good_shifts)
            
            # Check if auto-reply is enabled
            if not settings.get("auto_reply", True):
                log.info("Auto-reply disabled. Reply NOT sent.")
                print(f"{now_stamp()} Auto-reply disabled. Skipping send.")
                print("-" * 56)
                print(reply)
                print("-" * 56 + "\n")
                continue
            
            try:
                send_reply(service, email, reply)
                seen_ids.add(email["id"])  # Mark as seen after successful reply
                save_seen_ids(seen_ids)
                log.info("Reply sent for email %d.", idx)
                print(f"{now_stamp()} Reply sent to: {sender}")
                print("-" * 56)
                print(reply)
                print("-" * 56 + "\n")
            except HttpError as exc:
                log.error("Failed to send reply: %s", exc)

        print(f"{now_stamp()} Sleeping for {check_interval} seconds.\n")
        time.sleep(check_interval)

if __name__ == "__main__":
    main()

