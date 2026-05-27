"""Tests for secrets management."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from trading_autopilot.secrets import (
    CredentialError,
    CredentialLeakError,
    _is_env_var_secret,
    assert_no_credential_leak,
    clear_cache,
    get_api_credentials,
    load_credentials,
    redact_secrets,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def creds_dir(tmp_path: Path) -> Path:
    """Create a properly permissioned credentials directory."""
    d = tmp_path / "trading-autopilot"
    d.mkdir(mode=0o700)
    return d


@pytest.fixture
def creds_file(creds_dir: Path) -> Path:
    """Create a valid credentials file."""
    f = creds_dir / "binance.env"
    f.write_text(
        "BINANCE_API_KEY=test_key_abc123\n"
        "BINANCE_API_SECRET=test_secret_xyz789\n"
    )
    f.chmod(0o600)
    return f


class TestIsEnvVarSecret:
    def test_api_key(self):
        assert _is_env_var_secret("BINANCE_API_KEY") is True

    def test_api_secret(self):
        assert _is_env_var_secret("BINANCE_API_SECRET") is True

    def test_secret_key(self):
        assert _is_env_var_secret("MY_SECRET_KEY") is True

    def test_password(self):
        assert _is_env_var_secret("DB_PASSWORD") is True

    def test_non_secret(self):
        assert _is_env_var_secret("BINANCE_BASE_URL") is False

    def test_non_secret_symbol(self):
        assert _is_env_var_secret("TRADING_SYMBOL") is False


class TestLoadCredentials:
    def test_loads_valid_file(self, creds_file: Path):
        creds = load_credentials(str(creds_file))
        assert creds["BINANCE_API_KEY"] == "test_key_abc123"
        assert creds["BINANCE_API_SECRET"] == "test_secret_xyz789"

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(CredentialError, match="not found"):
            load_credentials(str(tmp_path / "nonexistent.env"))

    def test_missing_api_key(self, creds_dir: Path):
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_SECRET=only_secret\n")
        f.chmod(0o600)
        with pytest.raises(CredentialError, match="BINANCE_API_KEY"):
            load_credentials(str(f))

    def test_missing_api_secret(self, creds_dir: Path):
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_KEY=only_key\n")
        f.chmod(0o600)
        with pytest.raises(CredentialError, match="BINANCE_API_SECRET"):
            load_credentials(str(f))

    def test_empty_api_key(self, creds_dir: Path):
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_KEY=\nBINANCE_API_SECRET=secret\n")
        f.chmod(0o600)
        with pytest.raises(CredentialError, match="BINANCE_API_KEY"):
            load_credentials(str(f))

    def test_wrong_file_permissions(self, creds_dir: Path):
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_KEY=key\nBINANCE_API_SECRET=secret\n")
        f.chmod(0o644)
        with pytest.raises(CredentialError, match="permissions"):
            load_credentials(str(f))

    def test_wrong_dir_permissions(self, creds_dir: Path):
        creds_dir.chmod(0o755)
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_KEY=key\nBINANCE_API_SECRET=secret\n")
        f.chmod(0o600)
        with pytest.raises(CredentialError, match="permissions"):
            load_credentials(str(f))

    def test_strips_quotes(self, creds_dir: Path):
        f = creds_dir / "binenv"
        f.write_text(
            "BINANCE_API_KEY=\"quoted_key\"\n"
            "BINANCE_API_SECRET='quoted_secret'\n"
        )
        f.chmod(0o600)
        creds = load_credentials(str(f))
        assert creds["BINANCE_API_KEY"] == "quoted_key"
        assert creds["BINANCE_API_SECRET"] == "quoted_secret"

    def test_ignores_comments_and_blanks(self, creds_dir: Path):
        f = creds_dir / "binance.env"
        f.write_text(
            "# This is a comment\n"
            "\n"
            "BINANCE_API_KEY=key\n"
            "BINANCE_API_SECRET=secret\n"
        )
        f.chmod(0o600)
        creds = load_credentials(str(f))
        assert len(creds) == 2

    def test_caching(self, creds_file: Path):
        creds1 = load_credentials(str(creds_file), use_cache=True)
        creds2 = load_credentials(str(creds_file), use_cache=True)
        assert creds1 == creds2


class TestGetApiCredentials:
    def test_returns_tuple(self, creds_file: Path):
        key, secret = get_api_credentials(str(creds_file))
        assert key == "test_key_abc123"
        assert secret == "test_secret_xyz789"


class TestClearCache:
    def test_clears_cached_credentials(self, creds_file: Path):
        load_credentials(str(creds_file), use_cache=True)
        clear_cache()
        # After clearing, should re-load from file
        creds = load_credentials(str(creds_file))
        assert creds["BINANCE_API_KEY"] == "test_key_abc123"


class TestRedactSecrets:
    def test_no_redaction_when_no_cache(self):
        result = redact_secrets("some text with key_abc123")
        assert result == "some text with key_abc123"

    def test_redacts_credential_values(self, creds_file: Path):
        load_credentials(str(creds_file), use_cache=True)
        text = "The key is test_key_abc123 and secret is test_secret_xyz789"
        result = redact_secrets(text)
        assert "test_key_abc123" not in result
        assert "test_secret_xyz789" not in result
        assert "<redacted>" in result

    def test_empty_text(self, creds_file: Path):
        load_credentials(str(creds_file), use_cache=True)
        assert redact_secrets("") == ""


class TestAssertNoCredentialLeak:
    def test_passes_clean_text(self, creds_file: Path):
        load_credentials(str(creds_file), use_cache=True)
        result = assert_no_credential_leak("clean output", context="test")
        assert result == "clean output"

    def test_raises_on_leak(self, creds_file: Path):
        load_credentials(str(creds_file), use_cache=True)
        with pytest.raises(CredentialLeakError, match="SECURITY INCIDENT"):
            assert_no_credential_leak(
                "output containing test_key_abc123", context="journal"
            )

    def test_no_cache_passes_anyway(self):
        # Without loaded cache, no check is performed
        result = assert_no_credential_leak("anything here", context="test")
        assert result == "anything here"

    def test_short_values_ignored(self, creds_dir: Path):
        """Values <= 4 chars are not scanned (would cause too many false positives)."""
        f = creds_dir / "binance.env"
        f.write_text("BINANCE_API_KEY=abce\nBINANCE_API_SECRET=wxyz\n")
        f.chmod(0o600)
        load_credentials(str(f), use_cache=True)
        # Short values should not trigger leak detection
        result = assert_no_credential_leak("output with abce in it", context="test")
        assert result == "output with abce in it"
