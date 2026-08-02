"""Sandbox → host MEDIA delivery: path translation (B) and visible failure (C).

THE BUG (2026-07-31): the agent wrote a Markdown file with ``write_file`` —
which executes inside the Docker sandbox, where $HOME is ``/root`` — and
replied ``MEDIA:/root/social-connections-bft.md``. ``validate_media_delivery_path``
resolves paths on the HOST, found nothing, and dropped the tag. The reply
consisted of nothing but that tag, so the user received an empty message and
the only trace was a WARNING that mislabelled the cause as "unsafe".

B: paths under a mount the operator actually shared (the ``/output`` export
   mount, the auto-mounted ``/root/.hermes/cache/*`` dirs) are translated to
   their host equivalent BEFORE validation, so they deliver.
C: a rejection is classified honestly and surfaced to the user instead of
   dissolving into an empty message.
"""

import asyncio
import json
import logging
import os

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    SendResult,
    classify_media_delivery_rejection,
    format_media_delivery_failure_notice,
    validate_media_delivery_path,
)
from gateway.session import SessionSource, build_session_key


@pytest.fixture(autouse=True)
def _non_strict(monkeypatch):
    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
    monkeypatch.delenv("TERMINAL_DOCKER_VOLUMES", raising=False)
    monkeypatch.delenv("TERMINAL_ENV", raising=False)


def _docker(monkeypatch, volumes):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps(volumes))


# ---------------------------------------------------------------------------
# B — container → host translation
# ---------------------------------------------------------------------------

class TestContainerPathTranslation:
    def test_export_mount_path_resolves_to_host_file(self, tmp_path, monkeypatch):
        report = tmp_path / "report.md"
        report.write_text("# report")
        _docker(monkeypatch, [f"{tmp_path}:/output"])

        assert validate_media_delivery_path("/output/report.md") == str(report.resolve())

    def test_nested_export_mount_path(self, tmp_path, monkeypatch):
        nested = tmp_path / "sub" / "deep.md"
        nested.parent.mkdir()
        nested.write_text("x")
        _docker(monkeypatch, [f"{tmp_path}:/output"])

        assert validate_media_delivery_path("/output/sub/deep.md") == str(nested.resolve())

    def test_auto_mounted_cache_path_resolves_to_host_file(self, tmp_path, monkeypatch):
        """An inbound document is handed to the agent as
        /root/.hermes/cache/documents/<x>. Handing that same path back must
        deliver, even though /root is on the host denylist — the denylist is
        about HOST paths, and this one is a container path."""
        doc = tmp_path / "inbound.pdf"
        doc.write_bytes(b"%PDF-1.4")
        _docker(monkeypatch, [])
        monkeypatch.setattr(
            "tools.credential_files.get_cache_directory_mounts",
            lambda container_base="/root/.hermes": [
                {"host_path": str(tmp_path), "container_path": "/root/.hermes/cache/documents"}
            ],
        )

        got = validate_media_delivery_path("/root/.hermes/cache/documents/inbound.pdf")
        assert got == str(doc.resolve())

    def test_the_incident_path_is_still_rejected(self, monkeypatch, tmp_path):
        """/root/<name>.md is the sandbox $HOME — shared with nothing. There is
        no host file to deliver, and inventing one would be worse."""
        _docker(monkeypatch, [f"{tmp_path}:/output"])
        assert validate_media_delivery_path("/root/social-connections-bft.md") is None

    def test_no_translation_on_local_backend(self, tmp_path, monkeypatch):
        """Without a sandbox there is no container path to translate; /output
        must keep meaning /output."""
        (tmp_path / "report.md").write_text("x")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([f"{tmp_path}:/output"]))
        monkeypatch.delenv("TERMINAL_ENV", raising=False)

        assert validate_media_delivery_path("/output/report.md") is None

    def test_denylist_still_applies_to_the_translated_path(self, monkeypatch):
        """Translation must not become a denylist bypass: a mount whose host
        side is a denied prefix stays undeliverable."""
        if not os.path.isfile("/etc/hostname"):
            pytest.skip("no /etc/hostname on this host")
        _docker(monkeypatch, ["/etc:/output"])

        assert validate_media_delivery_path("/output/hostname") is None

    def test_read_only_export_mount_is_not_translated(self, tmp_path, monkeypatch):
        """A :ro mount cannot hold anything the agent produced this turn; the
        agent-visible cache translator covers the inbound direction instead."""
        (tmp_path / "report.md").write_text("x")
        _docker(monkeypatch, [f"{tmp_path}:/output:ro"])

        assert validate_media_delivery_path("/output/report.md") is None

    def test_untranslatable_prefix_is_not_guessed(self, tmp_path, monkeypatch):
        """Only the documented export mounts translate — an arbitrary rw mount
        of a source tree must not become a delivery channel by accident."""
        (tmp_path / "secret.md").write_text("x")
        _docker(monkeypatch, [f"{tmp_path}:/workspace/repo"])

        assert validate_media_delivery_path("/workspace/repo/secret.md") is None


# ---------------------------------------------------------------------------
# C — honest classification + visible failure
# ---------------------------------------------------------------------------

class TestRejectionClassification:
    def test_deliverable_path_has_no_rejection(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("x")
        assert classify_media_delivery_rejection(str(f)) is None

    def test_sandbox_only_path_is_named_as_such(self, monkeypatch):
        """The incident's cause. NOT 'unsafe' — nothing about it was unsafe."""
        _docker(monkeypatch, [])
        assert classify_media_delivery_rejection("/root/report.md") == "sandbox_only"

    def test_missing_path_on_local_backend_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TERMINAL_ENV", raising=False)
        assert classify_media_delivery_rejection(str(tmp_path / "nope.md")) == "missing_on_host"

    def test_existing_but_denied_path_is_blocked(self, monkeypatch):
        if not os.path.isfile("/etc/hostname"):
            pytest.skip("no /etc/hostname on this host")
        assert classify_media_delivery_rejection("/etc/hostname") == "blocked_by_policy"


class TestPartitionReportsRejections:
    def test_media_partition_returns_accepted_and_rejected(self, tmp_path, monkeypatch):
        good = tmp_path / "good.png"
        good.write_bytes(b"\x89PNG")
        _docker(monkeypatch, [])

        accepted, rejected = BasePlatformAdapter.partition_media_delivery_paths([
            (str(good), False),
            ("/root/ghost.md", False),
        ])

        assert accepted == [(str(good.resolve()), False)]
        assert rejected == [("/root/ghost.md", "sandbox_only")]

    def test_filter_wrapper_keeps_its_contract(self, tmp_path, monkeypatch):
        good = tmp_path / "good.png"
        good.write_bytes(b"\x89PNG")
        assert BasePlatformAdapter.filter_media_delivery_paths([
            (str(good), False), ("/root/ghost.md", False),
        ]) == [(str(good.resolve()), False)]

    def test_log_names_the_real_reason_not_unsafe(self, monkeypatch, caplog):
        _docker(monkeypatch, [])
        with caplog.at_level(logging.WARNING, logger="gateway.platforms.base"):
            BasePlatformAdapter.filter_media_delivery_paths([("/root/ghost.md", False)])
        assert "sandbox_only" in caplog.text
        assert "/root/ghost.md" in caplog.text


class TestFailureNotice:
    def test_notice_mentions_path_and_cause(self):
        notice = format_media_delivery_failure_notice([("/root/report.md", "sandbox_only")])
        assert "/root/report.md" in notice
        assert notice.strip()

    def test_empty_rejections_produce_no_notice(self):
        assert format_media_delivery_failure_notice([]) == ""


# ---------------------------------------------------------------------------
# C — end to end: the user must not receive silence
# ---------------------------------------------------------------------------

class _DummyAdapter(BasePlatformAdapter):
    def __init__(self, platform: Platform):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), platform)
        self.sent: list = []

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append({"chat_id": chat_id, "content": content})
        return SendResult(success=True, message_id="msg-1")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


async def _hold_typing(_chat_id, interval=2.0, metadata=None, stop_event=None):
    if stop_event is not None:
        await stop_event.wait()
    else:
        await asyncio.Event().wait()


class TestUserSeesTheFailure:
    @pytest.mark.asyncio
    async def test_reply_that_is_only_a_dead_media_tag_is_not_silence(self, monkeypatch):
        """The exact incident: the whole reply was
        ``MEDIA:/root/social-connections-bft.md`` (37 chars). Stripping the tag
        left an empty message and the user got nothing but a quota footer."""
        _docker(monkeypatch, [])
        adapter = _DummyAdapter(Platform.TELEGRAM)
        adapter._keep_typing = _hold_typing

        async def handler(_event):
            return "MEDIA:/root/social-connections-bft.md"

        adapter.set_message_handler(handler)
        event = MessageEvent(
            text="send me that file",
            source=SessionSource(platform=Platform.TELEGRAM, chat_id="111", chat_type="dm"),
            message_id="m1",
        )

        await adapter._process_message_background(event, build_session_key(event.source))

        assert len(adapter.sent) == 1, f"expected a delivery-failure notice, got {adapter.sent}"
        assert "social-connections-bft.md" in adapter.sent[0]["content"]
