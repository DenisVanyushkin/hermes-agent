"""Tests for fam/mail.py (Task 10, Phase 2b): .ics generation, Gmail MIME
message assembly, and Gmail API delivery via a domain-wide-delegated
service account.

send_event_email's real google-auth/googleapiclient path is never
exercised here -- every test either stays on the pure build_ics/
build_message functions, or injects a fake `_service` object so the
lazy google.* import inside send_event_email is skipped entirely (see
mail.py's module docstring and test_no_google_import.py, which pins that
fam.mail imports cleanly with those packages absent).
"""
import base64
import re
from email import message_from_bytes

import pytest

from fam import mail

CFG = {
    "target": "whatsapp:+77782110625",
    "email_enabled": True,
    "email_from": "germes@vanyushk.in",
    "email_to": "hermes@vanyushk.in",
}


def _event(**overrides):
    base = {
        "id": 42,
        "title": "Врач",
        "start_utc": "2026-07-15T05:00:00+00:00",
        "end_utc": None,
        "start_local": "2026-07-15T10:00:00+05:00",  # Almaty = UTC+5
        "end_local": None,
        "place": {"id": 1, "name": "Клиника"},
        "participants": [{"id": 1, "name": "Денис", "slug": "denis"}],
        "status": "active",
        "notes": "",
    }
    base.update(overrides)
    return base


def _ics_field(ics_text, name):
    """Extract a single unfolded property value by name from `ics_text`
    (e.g. "SUMMARY", "UID"), un-escaping RFC5545 TEXT escapes back to
    literal characters -- the inverse of mail._escape_ics_text.
    """
    # build_ics joins lines with CRLF; strip the trailing \r left inside
    # each MULTILINE match's $-anchored capture before comparing values.
    normalized = ics_text.replace("\r\n", "\n")
    m = re.search(rf"^{name}:(.*)$", normalized, re.MULTILINE)
    assert m, f"{name} not found in:\n{ics_text}"
    raw = m.group(1)
    return (
        raw.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


# ---- build_ics ----

def test_build_ics_is_valid_vcalendar_shell():
    ics = mail.build_ics(_event())
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT\r\n" in ics
    assert "END:VEVENT\r\n" in ics
    assert "METHOD:PUBLISH" in ics


def test_build_ics_uid_is_stable_per_event_id():
    ics = mail.build_ics(_event(id=99))
    assert _ics_field(ics, "UID") == "fam-99@hermes-home"


def test_build_ics_summary_round_trips():
    ics = mail.build_ics(_event(title="Врач"))
    assert _ics_field(ics, "SUMMARY") == "Врач"


def test_build_ics_dtstart_from_start_utc():
    ics = mail.build_ics(_event(start_utc="2026-07-15T05:00:00+00:00"))
    assert _ics_field(ics, "DTSTART") == "20260715T050000Z"


def test_build_ics_dtend_defaults_to_start_plus_one_hour_when_missing():
    ics = mail.build_ics(_event(start_utc="2026-07-15T05:00:00+00:00", end_utc=None))
    assert _ics_field(ics, "DTEND") == "20260715T060000Z"


def test_build_ics_dtend_uses_event_end_when_present():
    ics = mail.build_ics(_event(
        start_utc="2026-07-15T05:00:00+00:00",
        end_utc="2026-07-15T07:30:00+00:00",
    ))
    assert _ics_field(ics, "DTEND") == "20260715T073000Z"


def test_build_ics_dtstamp_is_present_and_utc():
    ics = mail.build_ics(_event())
    stamp = _ics_field(ics, "DTSTAMP")
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp)


def test_build_ics_location_from_place_name():
    ics = mail.build_ics(_event(place={"id": 1, "name": "Клиника"}))
    assert _ics_field(ics, "LOCATION") == "Клиника"


def test_build_ics_no_location_line_when_no_place():
    ics = mail.build_ics(_event(place=None))
    assert "LOCATION:" not in ics


def test_build_ics_description_lists_participants():
    ics = mail.build_ics(_event(participants=[
        {"id": 1, "name": "Денис", "slug": "denis"},
        {"id": 2, "name": "Тая", "slug": "taya"},
    ]))
    desc = _ics_field(ics, "DESCRIPTION")
    assert "Денис" in desc and "Тая" in desc


def test_build_ics_no_description_line_when_no_participants():
    ics = mail.build_ics(_event(participants=[]))
    assert "DESCRIPTION:" not in ics


def test_build_ics_escapes_commas_semicolons_and_newlines_in_summary():
    title = "Встреча, важная; тема\nвторая строка"
    ics = mail.build_ics(_event(title=title))
    raw_line = re.search(
        r"^SUMMARY:(.*)$", ics.replace("\r\n", "\n"), re.MULTILINE
    ).group(1)
    # Escaped on the wire...
    assert "\\," in raw_line and "\\;" in raw_line and "\\n" in raw_line
    assert "\n" not in raw_line  # no literal newline snuck into the property line
    # ...and round-trips back to the original text.
    assert _ics_field(ics, "SUMMARY") == title


def test_build_ics_escapes_backslash_itself():
    ics = mail.build_ics(_event(title="C:\\Users\\denis"))
    assert _ics_field(ics, "SUMMARY") == "C:\\Users\\denis"


# ---- build_message ----

def _decode_message(raw_b64url):
    # Gmail API "raw" is base64url without padding requirements enforced --
    # accept either; email.message_from_bytes needs the raw MIME bytes.
    padded = raw_b64url + "=" * (-len(raw_b64url) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(padded))


def test_build_message_returns_raw_key_only():
    result = mail.build_message(_event(), CFG)
    assert set(result.keys()) == {"raw"}
    assert isinstance(result["raw"], str)


def test_build_message_raw_is_urlsafe_base64_alphabet():
    result = mail.build_message(_event(), CFG)
    assert re.fullmatch(r"[A-Za-z0-9_=\-]+", result["raw"])
    assert "+" not in result["raw"] and "/" not in result["raw"]


def test_build_message_headers_from_cfg():
    result = mail.build_message(_event(), CFG)
    msg = _decode_message(result["raw"])
    assert msg["From"] == CFG["email_from"]
    assert msg["To"] == CFG["email_to"]
    assert msg.is_multipart()


def test_build_message_has_ics_attachment_named_event_ics():
    result = mail.build_message(_event(), CFG)
    msg = _decode_message(result["raw"])
    attachments = [p for p in msg.walk() if p.get_filename() == "event.ics"]
    assert len(attachments) == 1
    ics_bytes = attachments[0].get_payload(decode=True)
    assert ics_bytes.decode("utf-8").startswith("BEGIN:VCALENDAR")


def test_build_message_ics_attachment_matches_build_ics():
    event = _event()
    result = mail.build_message(event, CFG)
    msg = _decode_message(result["raw"])
    attachment = next(p for p in msg.walk() if p.get_filename() == "event.ics")
    assert attachment.get_payload(decode=True).decode("utf-8") == mail.build_ics(event)


def test_build_message_text_body_is_russian_with_almaty_local_time():
    result = mail.build_message(_event(start_local="2026-07-15T10:00:00+05:00"), CFG)
    msg = _decode_message(result["raw"])
    text_part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    body = text_part.get_payload(decode=True).decode("utf-8")
    assert "Когда" in body and "Где" in body and "Участники" in body
    assert "10:00" in body  # Almaty local hour, not the 05:00 UTC hour
    assert "Клиника" in body
    assert "Денис" in body


def test_build_message_derives_local_time_from_utc_when_local_fields_absent():
    event = _event()
    del event["start_local"]
    result = mail.build_message(event, CFG)
    msg = _decode_message(result["raw"])
    text_part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    body = text_part.get_payload(decode=True).decode("utf-8")
    assert "10:00" in body  # 05:00 UTC == 10:00 Almaty (+05:00)


# ---- send_event_email ----

class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def send(self, userId, body):
        self.calls.append({"userId": userId, "body": body})
        return self

    def execute(self):
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self, response=None, exc=None):
        self.messages_obj = FakeMessages(response=response, exc=exc)

    def users(self):
        return FakeUsers(self.messages_obj)


def test_send_event_email_ok_path_returns_id():
    service = FakeService(response={"id": "msg-123"})
    result = mail.send_event_email(_event(), CFG, _service=service)
    assert result == {"ok": True, "id": "msg-123"}


def test_send_event_email_ok_path_calls_users_messages_send_with_body():
    # NOTE: MIMEMultipart() mints a fresh random boundary per call, so two
    # independent build_message() calls for the same event never produce
    # byte-identical "raw" -- this asserts structural equivalence (the
    # actual contract: send_event_email's body IS a build_message(event,
    # cfg) result) rather than raw-string equality.
    service = FakeService(response={"id": "msg-123"})
    event = _event()
    mail.send_event_email(event, CFG, _service=service)
    call = service.messages_obj.calls[0]
    assert call["userId"] == "me"
    sent_msg = _decode_message(call["body"]["raw"])
    assert sent_msg["From"] == CFG["email_from"]
    assert sent_msg["To"] == CFG["email_to"]
    attachment = next(p for p in sent_msg.walk() if p.get_filename() == "event.ics")
    assert attachment.get_payload(decode=True).decode("utf-8") == mail.build_ics(event)


def test_send_event_email_error_path_never_raises():
    service = FakeService(exc=RuntimeError("access_denied: unauthorized_client"))
    result = mail.send_event_email(_event(), CFG, _service=service)
    assert result["ok"] is False
    assert "unauthorized_client" in result["error"]


def test_send_event_email_error_path_does_not_raise_out_of_function():
    service = FakeService(exc=RuntimeError("boom"))
    # Must not raise -- this call itself is the assertion.
    result = mail.send_event_email(_event(), CFG, _service=service)
    assert isinstance(result, dict)
