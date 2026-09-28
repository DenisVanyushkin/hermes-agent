import json

from tools import gmail_host_tool as gmail


ALLOWED = "cron_6bcf7ea3d72d_20260928_060042"


class FakeIMAP:
    flags = b"\\Seen"

    def __init__(self, host, port, timeout):
        self.calls = []
        self.labels_removed = False
        self.capabilities = ("IMAP4REV1", "X-GM-EXT-1")

    def login(self, address, password):
        self.calls.append(("login", address, password))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, action, *args):
        self.calls.append((action, *args))
        if action == "SEARCH":
            return "OK", [b"" if self.labels_removed else b"17"]
        if action == "FETCH":
            if "BODY.PEEK[]" in args[-1]:
                return "OK", [(b"1 (BODY[] {60}", b"From: Sender <sender@example.com>\r\nSubject: News\r\n\r\nReadable body"), b")"]
            meta = b'1 (UID 17 X-GM-MSGID 999 INTERNALDATE "28-Sep-2026 06:00:00 +0000" RFC822.SIZE 123 FLAGS (' + self.flags + b') BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {65}'
            headers = b"From: Sender <sender@example.com>\r\nSubject: News\r\nDate: Mon, 28 Sep 2026 06:00:00 +0000\r\n\r\n"
            return "OK", [(meta, headers), b")"]
        if action == "MOVE":
            self.labels_removed = True
            return "OK", [b"moved"]
        raise AssertionError((action, args))

    def list(self):
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

    def logout(self):
        return "BYE", [b"bye"]


def _setup(monkeypatch):
    instances = []

    def make(*args, **kwargs):
        instance = FakeIMAP(*args, **kwargs)
        instances.append(instance)
        return instance

    monkeypatch.setattr(gmail.imaplib, "IMAP4_SSL", make)
    monkeypatch.setenv("EMAIL_ADDRESS", "hermes@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "example-secret")
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.gmail.com")
    return instances


def test_other_sessions_cannot_open_mailbox(monkeypatch):
    instances = _setup(monkeypatch)
    result = gmail.handle_gmail_host({"action": "list"}, session_id="telegram_personal")
    assert "error" in result
    assert instances == []


def test_list_reads_inbox_without_marking_seen(monkeypatch):
    instances = _setup(monkeypatch)
    data = json.loads(gmail.handle_gmail_host({"action": "list"}, session_id=ALLOWED))
    assert data["messages"][0]["uid"] == "17"
    assert data["messages"][0]["gm_msgid"] == "999"
    assert data["messages"][0]["internal_date"].startswith("2026-09-28T06:00:00")
    calls = instances[0].calls
    assert ("select", "INBOX", True) in calls
    assert any(call[0] == "FETCH" and "BODY.PEEK[HEADER.FIELDS" in call[-1] for call in calls)
    assert not any(call[0] == "MOVE" for call in calls)


def test_archive_requires_matching_gmail_message_id(monkeypatch):
    instances = _setup(monkeypatch)
    result = gmail.handle_gmail_host(
        {"action": "archive", "uid": "17", "gm_msgid": "998"}, session_id=ALLOWED
    )
    assert "error" in result
    assert not any(call[0] == "MOVE" for call in instances[0].calls)


def test_get_reads_body_without_setting_seen(monkeypatch):
    instances = _setup(monkeypatch)
    data = json.loads(gmail.handle_gmail_host(
        {"action": "get", "uid": "17", "gm_msgid": "999"}, session_id=ALLOWED
    ))
    assert data["body"] == "Readable body"
    assert ("select", "INBOX", True) in instances[0].calls
    assert any(call[0] == "FETCH" and "BODY.PEEK[]" in call[-1] for call in instances[0].calls)
    assert not any(call[0] == "MOVE" for call in instances[0].calls)


def test_flagged_mail_cannot_be_archived(monkeypatch):
    instances = _setup(monkeypatch)
    monkeypatch.setattr(FakeIMAP, "flags", b"\\Seen \\Flagged")
    result = gmail.handle_gmail_host(
        {"action": "archive", "uid": "17", "gm_msgid": "999"}, session_id=ALLOWED
    )
    assert "error" in result
    assert not any(call[0] == "MOVE" for call in instances[0].calls)


def test_archive_removes_inbox_label_and_verifies(monkeypatch):
    instances = _setup(monkeypatch)
    data = json.loads(gmail.handle_gmail_host(
        {"action": "archive", "uid": "17", "gm_msgid": "999"}, session_id=ALLOWED
    ))
    assert data["archived"] is True
    calls = instances[0].calls
    assert ("select", "INBOX", False) in calls
    assert any(call[0] == "MOVE" and call[1] == "17" and "All Mail" in call[2] for call in calls)
    assert calls[-1][0] == "SEARCH"
