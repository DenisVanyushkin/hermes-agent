"""Email adapter robustness against malformed IMAP responses (salvage of #2794).

Validates that:
- Malformed IMAP fetch responses are skipped instead of aborting the batch
  (UIDs are marked seen before fetch, so an abort permanently loses messages)
- Message-ID generation handles a missing '@' in EMAIL_ADDRESS
"""

import os
import unittest
import uuid
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch


def _make_adapter(address="hermes@test.com"):
    from gateway.config import PlatformConfig

    with patch.dict(os.environ, {
        "EMAIL_ADDRESS": address,
        "EMAIL_PASSWORD": "secret",
        "EMAIL_IMAP_HOST": "imap.test.com",
        "EMAIL_SMTP_HOST": "smtp.test.com",
    }):
        from plugins.platforms.email.adapter import EmailAdapter

        adapter = EmailAdapter(PlatformConfig(enabled=True))
    return adapter


def _raw_email(sender="user@test.com", subject="Hello"):
    msg = MIMEText("Test body", "plain", "utf-8")
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4().hex[:8]}@test.com>"
    return msg.as_bytes()


class TestImapResponseGuard(unittest.TestCase):
    """_fetch_new_messages skips messages with unexpected IMAP structure."""

    def _fetch_with(self, fetch_responses):
        adapter = _make_adapter()
        uids = b" ".join(
            str(i + 1).encode() for i in range(len(fetch_responses))
        )
        fetch_iter = iter(fetch_responses)

        def uid_handler(command, *args):
            if command == "search":
                return ("OK", [uids])
            if command == "fetch":
                return next(fetch_iter)
            return ("NO", [])

        mock_imap = MagicMock()
        mock_imap.uid.side_effect = uid_handler
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            return adapter._fetch_new_messages()

    def test_normal_response_parses(self):
        results = self._fetch_with([("OK", [(b"1 (RFC822 {123}", _raw_email())])])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sender_addr"], "user@test.com")

    def test_none_element_skipped(self):
        results = self._fetch_with([("OK", [None])])
        self.assertEqual(results, [])

    def test_empty_list_skipped(self):
        results = self._fetch_with([("OK", [])])
        self.assertEqual(results, [])

    def test_bare_bytes_element_skipped(self):
        # Single bytes item instead of a (header, payload) tuple
        results = self._fetch_with([("OK", [b"not-a-tuple"])])
        self.assertEqual(results, [])

    def test_non_bytes_payload_skipped(self):
        results = self._fetch_with([("OK", [(b"1", None)])])
        self.assertEqual(results, [])

    def test_malformed_does_not_abort_batch(self):
        """A malformed response mid-batch must not lose the messages after it."""
        results = self._fetch_with([
            ("OK", [None]),                                # UID 1 malformed
            ("OK", [(b"2 (RFC822 {123}", _raw_email())]),  # UID 2 fine
        ])
        self.assertEqual(len(results), 1)


class TestImapConnectionCleanup(unittest.TestCase):
    """Every IMAP connection is torn down on ALL exit paths.

    A graceful ``logout()`` on a timed-out/half-open socket can fail without
    ever shutting the TCP session down, leaving the server holding an idle
    connection until ITS timeout. Poll cycles every ~15s stack these up into
    ``[ALERT] Too many simultaneous connections``. Guarantee a hard socket
    close so the server-side session is released immediately.
    """

    def test_fetch_forces_shutdown_when_logout_fails(self):
        adapter = _make_adapter()
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [b""])  # no unseen messages
        mock_imap.logout.side_effect = OSError("broken pipe")
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            adapter._fetch_new_messages()  # must not raise
        # logout() failed, so the socket must be force-closed instead of leaked
        mock_imap.shutdown.assert_called_once()

    def test_fetch_timeout_closes_connection(self):
        adapter = _make_adapter()
        mock_imap = MagicMock()
        mock_imap.login.side_effect = TimeoutError("read operation timed out")
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            adapter._fetch_new_messages()  # must not raise
        # The connection was created but login timed out — it MUST be torn down.
        self.assertTrue(
            mock_imap.logout.called or mock_imap.shutdown.called,
            "timed-out IMAP connection was not closed (session leak)",
        )

    def test_connect_closes_socket_on_login_failure(self):
        import asyncio

        adapter = _make_adapter()
        mock_imap = MagicMock()
        mock_imap.login.side_effect = OSError(
            "b'[ALERT] Too many simultaneous connections. (Failure)'"
        )
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            ok = asyncio.run(adapter.connect())
        self.assertFalse(ok)
        # connect() opened a socket then login failed — it MUST NOT leak it.
        self.assertTrue(
            mock_imap.logout.called or mock_imap.shutdown.called,
            "connect() leaked the IMAP socket on login failure",
        )


class TestMessageIdDomain(unittest.TestCase):
    """Message-ID generation tolerates EMAIL_ADDRESS without '@'."""

    def test_normal_address(self):
        adapter = _make_adapter("hermes@example.org")
        self.assertEqual(adapter._message_id_domain(), "example.org")

    def test_address_without_at(self):
        adapter = _make_adapter("not-an-email")
        self.assertEqual(adapter._message_id_domain(), "localhost")

    def test_address_trailing_at(self):
        adapter = _make_adapter("weird@")
        self.assertEqual(adapter._message_id_domain(), "localhost")


if __name__ == "__main__":
    unittest.main()
