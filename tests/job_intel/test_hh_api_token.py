"""Token lifecycle: the application token is persistent and single-minted."""
import json
import os
import time

import pytest

from job_intel import hh_api


def test_reads_cached_token_without_calling_the_network(tmp_path, monkeypatch):
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"access_token": "CACHED", "minted_at": time.time()}))
    monkeypatch.setenv("JOB_INTEL_HH_TOKEN_CACHE", str(cache))

    monkeypatch.setattr(
        hh_api,
        "_post_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("POST /token must not be called when a cached token exists")
        ),
    )

    assert hh_api.get_app_token() == "CACHED"


def test_mints_and_persists_when_cache_is_absent(tmp_path, monkeypatch):
    cache = tmp_path / "tok.json"
    monkeypatch.setenv("JOB_INTEL_HH_TOKEN_CACHE", str(cache))
    monkeypatch.setenv("JOB_INTEL_HH_CLIENT_ID", "cid")
    monkeypatch.setenv("JOB_INTEL_HH_CLIENT_SECRET", "csec")
    monkeypatch.setattr(hh_api, "_post_token", lambda cid, sec: {"access_token": "FRESH"})

    assert hh_api.get_app_token() == "FRESH"
    assert json.loads(cache.read_text())["access_token"] == "FRESH"
    assert cache.stat().st_mode & 0o777 == 0o600


def test_refuses_to_remint_within_the_five_minute_floor(tmp_path, monkeypatch):
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"access_token": "OLD", "minted_at": time.time()}))
    monkeypatch.setenv("JOB_INTEL_HH_TOKEN_CACHE", str(cache))
    monkeypatch.setattr(
        hh_api,
        "_post_token",
        lambda cid, sec: (_ for _ in ()).throw(AssertionError("minted too soon")),
    )

    with pytest.raises(hh_api.HHTokenCooldown):
        hh_api.get_app_token(force_refresh=True)


def test_force_refresh_is_allowed_once_the_floor_has_passed(tmp_path, monkeypatch):
    cache = tmp_path / "tok.json"
    cache.write_text(json.dumps({"access_token": "OLD", "minted_at": time.time() - 400}))
    monkeypatch.setenv("JOB_INTEL_HH_TOKEN_CACHE", str(cache))
    monkeypatch.setenv("JOB_INTEL_HH_CLIENT_ID", "cid")
    monkeypatch.setenv("JOB_INTEL_HH_CLIENT_SECRET", "csec")


def test_cache_write_does_not_chmod_an_existing_parent(tmp_path):
    cache_dir = tmp_path / "state"
    cache_dir.mkdir()
    os.chmod(cache_dir, 0o770)
    cache = cache_dir / "tok.json"

    hh_api._write_token_cache(cache, {"access_token": "TOKEN"})

    assert cache.stat().st_mode & 0o777 == 0o600
    assert cache_dir.stat().st_mode & 0o777 == 0o770
