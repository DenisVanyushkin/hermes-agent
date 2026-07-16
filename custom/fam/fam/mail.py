"""Gmail delivery of .ics event invites to Denis (Task 10, Phase 2b).

build_ics()/build_message() are pure stdlib (RFC5545 text + email MIME +
base64) -- fully unit-testable with no network, no DB, no Google
libraries. send_event_email() is the only function that touches Gmail: it
lazy-imports google-auth/google-api-python-client INSIDE the function
body, on the branch that builds real delegated credentials, mirroring
grid.py's lazy Pillow import -- so the rest of fam (core CRUD, non-mail
CLI commands) keeps working in environments where those packages aren't
installed (test_no_google_import.py pins this for fam.cli/fam.mail).

Transport: a Gmail API service-account key (~/.hermes/germes-sa.json,
NOT in git) impersonates germes@vanyushk.in via domain-wide delegation
(Credentials.with_subject) -- NOT smtplib/SMTP (that was the design in
the original Phase 2b plan text; superseded before this task started).

send_event_email() never raises: any failure (missing/invalid key file,
domain-wide delegation not yet authorized in Workspace admin --
403 unauthorized_client/access_denied -- network error, Gmail API error)
is caught and returned as {"ok": False, "error": ...}, so a mail hiccup
can never break the calendar operation that triggered it (see cli.py's
cal add/update hook, which is best-effort and audits mail.error on this
path rather than raising).
"""
import base64
import os
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
DEFAULT_KEY_PATH = "~/.hermes/germes-sa.json"


def _now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_utc(value):
    """Parse an ISO-8601 string into an aware UTC datetime. A naive string
    (no tzinfo) is treated as already UTC -- mirrors gate.py/tick.py's own
    local copy of this helper.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_ics_utc(iso_str):
    """Format an ISO-8601 string as an RFC5545 UTC DATE-TIME
    (YYYYMMDDTHHMMSSZ)."""
    return _parse_utc(iso_str).strftime("%Y%m%dT%H%M%SZ")


def _escape_ics_text(value):
    """Escape a TEXT property value per RFC5545 3.3.11: backslash first
    (so it doesn't double-escape the escapes introduced below), then
    comma/semicolon, then newlines (both CRLF and bare LF) to the literal
    two-character sequence \\n.
    """
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


_ICS_LINE_LIMIT = 75


def _fold_ics_line(line, limit=_ICS_LINE_LIMIT):
    """Fold one RFC5545 content line (no line break) into physical lines
    of at most `limit` OCTETS each, per RFC5545 3.1: a continuation
    physical line is introduced by CRLF followed by a single SPACE, and
    that leading space counts toward its 75-octet budget (so a
    continuation line carries at most limit-1 octets of real content).
    Byte-safe: counts UTF-8 octets, not characters, and never splits a
    multi-byte UTF-8 sequence across a fold boundary -- a naive
    fixed-width slice on the encoded bytes can land mid-character for
    Cyrillic (2 octets/char) text, corrupting it on decode. Returns the
    CRLF-joined folded text (no trailing line break); a line already
    within the limit is returned unchanged.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return line

    chunks = []
    start = 0
    n = len(encoded)
    first = True
    while start < n:
        budget = limit if first else limit - 1  # continuation lines reserve 1 octet for the leading space
        end = min(start + budget, n)
        # Back off `end` while it points into a UTF-8 continuation
        # byte (10xxxxxx) so we never split a multi-byte character.
        while end > start and end < n and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
        first = False
    return "\r\n ".join(chunks)


def build_ics(event):
    """Build an RFC5545 VCALENDAR/VEVENT text for `event` (the dict shape
    cal.get() returns: id, title, start_utc, end_utc, place, participants).
    Pure -- stdlib only, no I/O.

    METHOD:PUBLISH (not REQUEST) -- this is an informational calendar
    entry, not an RSVP invite. UID is stable per event id
    (fam-<id>@hermes-home) so a later re-send for the same event updates
    rather than duplicates the entry in the recipient's calendar. DTEND
    defaults to DTSTART + 1h when the event has no end_utc.
    """
    start_utc = event["start_utc"]
    end_utc = event.get("end_utc")
    if not end_utc:
        end_utc = (_parse_utc(start_utc) + timedelta(hours=1)).isoformat(
            timespec="seconds"
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//hermes-agent//fam//RU",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:fam-{event['id']}@hermes-home",
        f"DTSTAMP:{_to_ics_utc(_now_utc_iso())}",
        f"DTSTART:{_to_ics_utc(start_utc)}",
        f"DTEND:{_to_ics_utc(end_utc)}",
        f"SUMMARY:{_escape_ics_text(event['title'])}",
    ]

    place = event.get("place")
    if place:
        lines.append(f"LOCATION:{_escape_ics_text(place['name'])}")

    participants = event.get("participants") or []
    if participants:
        names = ", ".join(p["name"] for p in participants)
        lines.append(f"DESCRIPTION:{_escape_ics_text('Участники: ' + names)}")

    lines += ["END:VEVENT", "END:VCALENDAR"]
    # Fold each logical line to RFC5545's 75-octet limit before joining --
    # see _fold_ics_line's docstring. Applied here (once, on the fully
    # assembled content lines) rather than at each f-string call site
    # above, so every property -- including any added later -- is covered.
    folded_lines = [_fold_ics_line(l) for l in lines]
    return "\r\n".join(folded_lines) + "\r\n"


def _almaty_dt(iso_str):
    """Parse an ISO-8601 string (either a UTC offset like start_utc, or an
    already-Almaty offset like cal.get()'s start_local) and return it as
    an aware datetime in Asia/Almaty.
    """
    return _parse_utc(iso_str).astimezone(ALMATY)


def _format_when(start_dt, end_dt):
    if end_dt is None:
        return f"{start_dt:%d.%m.%Y %H:%M}"
    if end_dt.date() == start_dt.date():
        return f"{start_dt:%d.%m.%Y %H:%M}–{end_dt:%H:%M}"
    return f"{start_dt:%d.%m.%Y %H:%M} – {end_dt:%d.%m.%Y %H:%M}"


def build_message(event, cfg):
    """Build a Gmail API-ready message dict {"raw": base64url(mime)} for
    `event`: a text/plain human body (Russian, Almaty local times) plus an
    event.ics attachment (build_ics(event)). Pure-ish -- stdlib email +
    base64 only, no network/DB.

    Prefers event["start_local"]/["end_local"] (as produced by cal.get())
    for the displayed time when present, falling back to converting
    start_utc/end_utc to Asia/Almaty itself -- so a bare hand-built event
    dict (e.g. `fam mail test`'s crafted-script live check) works too.
    """
    msg = MIMEMultipart()
    msg["Subject"] = f"Событие: {event['title']}"
    msg["From"] = cfg["email_from"]
    msg["To"] = cfg["email_to"]

    start_dt = _almaty_dt(event.get("start_local") or event["start_utc"])
    end_source = event.get("end_local") or event.get("end_utc")
    end_dt = _almaty_dt(end_source) if end_source else None
    when = _format_when(start_dt, end_dt)

    place = event.get("place")
    where = place["name"] if place else "—"

    participants = event.get("participants") or []
    who = ", ".join(p["name"] for p in participants) if participants else "—"

    body = (
        "Гермес отправляет напоминание о событии.\n\n"
        f"{event['title']}\n"
        f"Когда: {when} (Алматы)\n"
        f"Где: {where}\n"
        f"Участники: {who}\n\n"
        "Во вложении — файл event.ics для календаря."
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # text/calendar (not application/ics): this is the RFC5545/RFC2445-
    # blessed content-type for a .ics MIME part, and method=PUBLISH here
    # (mirroring the METHOD:PUBLISH inside the .ics body itself, see
    # build_ics) is what makes Gmail and most calendar clients offer an
    # "Add to calendar" affordance instead of rendering a bare download.
    ics_part = MIMEText(build_ics(event), _subtype="calendar", _charset="utf-8")
    ics_part.set_param("method", "PUBLISH")
    ics_part.add_header("Content-Disposition", "attachment", filename="event.ics")
    msg.attach(ics_part)

    # Gmail's "raw" field is base64url (RFC4648 sec5) WITHOUT "=" padding
    # -- Gmail's API accepts padded input too, but strip it to match spec
    # or convention exactly rather than relying on server-side leniency.
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    return {"raw": raw}


def send_event_email(event, cfg, key_path=DEFAULT_KEY_PATH, _service=None):
    """Send `event`'s .ics invite via the Gmail API, as germes@vanyushk.in
    (cfg["email_from"], via domain-wide-delegated service account
    impersonation) to cfg["email_to"]. Returns {"ok": True, "id": <gmail
    message id>} on success, or {"ok": False, "error": <str>} on ANY
    failure -- this function never raises.

    _service is a test injection point: when given (a fake object
    implementing .users().messages().send(userId=, body=).execute()), the
    google-auth/googleapiclient lazy import and real credential-building
    below are skipped entirely, so unit tests never need a real key file
    or network access. Without it, key_path (default
    ~/.hermes/germes-sa.json) is read to build delegated credentials
    scoped to gmail.send, impersonating cfg["email_from"] --
    Workspace-side domain-wide-delegation authorization for this service
    account must be active for that to succeed; until it is, calls here
    fail with a 403 unauthorized_client/access_denied surfaced as this
    function's {"ok": False, "error": ...} return, not an exception.
    """
    try:
        message = build_message(event, cfg)

        if _service is not None:
            service = _service
        else:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build as build_gmail_service

            creds = service_account.Credentials.from_service_account_file(
                os.path.expanduser(key_path),
                scopes=[GMAIL_SEND_SCOPE],
            ).with_subject(cfg["email_from"])
            service = build_gmail_service(
                "gmail", "v1", credentials=creds, cache_discovery=False
            )

        result = service.users().messages().send(
            userId="me", body=message
        ).execute()
        return {"ok": True, "id": result.get("id")}
    except Exception as e:  # noqa: BLE001 -- deliberate catch-all: never raise
        return {"ok": False, "error": str(e)}
