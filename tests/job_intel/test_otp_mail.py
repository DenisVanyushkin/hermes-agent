from __future__ import annotations

from datetime import datetime, timedelta, timezone

from job_intel.otp_mail import (
    LINKEDIN_CODE_PATTERN,
    MailOtpConfig,
    OtpResult,
    read_otp,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _message(*, sent_at: datetime, body: str) -> bytes:
    stamp = sent_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        f"From: security-noreply@linkedin.com\r\n"
        f"Subject: Here's your verification code\r\n"
        f"Date: {stamp}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


class FakeImap:
    def __init__(self, messages: list[bytes]) -> None:
        self._messages = messages
        self.searched: list[str] = []

    def __enter__(self) -> "FakeImap":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def login(self, address: str, password: str) -> None:
        return None

    def select(self, mailbox: str) -> None:
        return None

    def search(self, charset: object, criteria: str) -> tuple[str, list[bytes]]:
        self.searched.append(criteria)
        ids = b" ".join(str(i + 1).encode() for i in range(len(self._messages)))
        return "OK", [ids]

    def fetch(self, message_id: bytes, parts: str) -> tuple[str, list[tuple[bytes, bytes]]]:
        index = int(message_id) - 1
        return "OK", [(b"", self._messages[index])]


def _factory(messages: list[bytes]):
    def build(host: str, port: int) -> FakeImap:
        return FakeImap(messages)

    return build


CONFIG = MailOtpConfig(address="a@example.com", password="x", host="imap.example.com", port=993)


def test_reads_a_fresh_code() -> None:
    messages = [_message(sent_at=NOW - timedelta(minutes=1), body="Your code is 483920.")]

    result = read_otp(
        CONFIG,
        sender="linkedin.com",
        subject_hint="verification code",
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=timedelta(minutes=10),
        now=NOW,
        client_factory=_factory(messages),
    )

    assert result == OtpResult(code="483920", status="ok")


def test_stale_message_is_refused() -> None:
    """Старый код отправлять нельзя: он не подойдёт, попытка сгорит, а лимит
    в одну попытку в сутки сделает эту ошибку суточной."""
    messages = [_message(sent_at=NOW - timedelta(hours=3), body="Your code is 483920.")]

    result = read_otp(
        CONFIG,
        sender="linkedin.com",
        subject_hint="verification code",
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=timedelta(minutes=10),
        now=NOW,
        client_factory=_factory(messages),
    )

    assert result == OtpResult(code=None, status="no_fresh_message")


def test_unrelated_numbers_are_not_mistaken_for_a_code() -> None:
    """Прежний паттерн \\b(\\d{4,8})\\b брал первое попавшееся число."""
    messages = [
        _message(
            sent_at=NOW - timedelta(minutes=1),
            body="Order 55512345 shipped in 2026. Reference 9087654.",
        )
    ]

    result = read_otp(
        CONFIG,
        sender="linkedin.com",
        subject_hint="verification code",
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=timedelta(minutes=10),
        now=NOW,
        client_factory=_factory(messages),
    )

    assert result == OtpResult(code=None, status="no_code_in_message")


def test_missing_credentials_are_reported_not_swallowed() -> None:
    """Первопричина того, что тракт два месяца молчал: отсутствие кред было
    неотличимо от отсутствия письма."""
    result = read_otp(
        None,
        sender="linkedin.com",
        subject_hint="verification code",
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=timedelta(minutes=10),
        now=NOW,
        client_factory=_factory([]),
    )

    assert result == OtpResult(code=None, status="not_configured")


def test_search_is_never_widened_to_the_whole_inbox() -> None:
    messages = [_message(sent_at=NOW, body="Your code is 483920.")]
    client = FakeImap(messages)

    read_otp(
        CONFIG,
        sender="linkedin.com",
        subject_hint="verification code",
        pattern=LINKEDIN_CODE_PATTERN,
        max_age=timedelta(minutes=10),
        now=NOW,
        client_factory=lambda host, port: client,
    )

    assert all("ALL" not in criteria for criteria in client.searched)


def test_config_prefers_live_env_names_over_the_dead_ones() -> None:
    config = MailOtpConfig.from_env(
        {"EMAIL_ADDRESS": "live@example.com", "EMAIL_PASSWORD": "p", "EMAIL_IMAP_HOST": "imap.example.com"}
    )

    assert config is not None
    assert config.address == "live@example.com"
    assert config.host == "imap.example.com"


def test_config_is_none_when_nothing_is_set() -> None:
    assert MailOtpConfig.from_env({}) is None
