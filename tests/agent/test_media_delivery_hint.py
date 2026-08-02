"""Sandbox → host media-delivery hint (fix A).

THE BUG: the agent was asked for a file, wrote it with ``write_file`` — which
runs *inside* the Docker sandbox, where $HOME is ``/root`` — and answered
``MEDIA:/root/social-connections-bft.md``. MEDIA paths are resolved by the
gateway on the HOST, where that file does not exist, so the tag was dropped
and the user received an empty message.

The prompt contained both halves of the contradiction and never joined them:
``build_environment_hints`` says "your file tools operate inside this docker
environment, the host is irrelevant", while every PLATFORM_HINTS entry says
"include MEDIA:/absolute/path". Nothing said the MEDIA path must be
host-visible, nor where the two filesystems meet.
"""

import json

import pytest

import agent.prompt_builder as _pb


@pytest.fixture(autouse=True)
def _deterministic_backend(monkeypatch):
    """No live docker probe, no WSL, no cached probe result."""
    monkeypatch.setattr(_pb, "is_wsl", lambda: False)
    monkeypatch.setattr(_pb, "_probe_remote_backend", lambda _t: None)
    monkeypatch.delenv("TERMINAL_DOCKER_VOLUMES", raising=False)
    _pb._clear_backend_probe_cache()
    yield
    _pb._clear_backend_probe_cache()


class TestWritableDeliveryMounts:
    """The mount table is read from live config, never hardcoded."""

    def test_returns_writable_bind_mounts(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/home/h/.hermes/cache/documents:/output",
        ]))
        assert ("/output", "/home/h/.hermes/cache/documents") in _pb.writable_delivery_mounts()

    def test_skips_read_only_mounts(self, monkeypatch):
        """Cache dirs are auto-mounted ``:ro`` — the agent cannot write there,
        so they must never be advertised as a place to put deliverables."""
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/home/h/.hermes/cache/images:/root/.hermes/cache/images:ro",
        ]))
        assert _pb.writable_delivery_mounts() == []

    def test_tolerates_malformed_table(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", "not json at all")
        assert _pb.writable_delivery_mounts() == []


class TestMediaDeliveryHint:
    def test_absent_on_local_backend(self, monkeypatch):
        """No sandbox boundary → no boundary warning."""
        monkeypatch.delenv("TERMINAL_ENV", raising=False)
        assert _pb.build_media_delivery_hint() is None
        assert "resolved on the Hermes host" not in _pb.build_environment_hints()

    def test_names_identity_mount_as_the_target(self, monkeypatch):
        """An identity mount (same path both sides) needs no translation and is
        the safest thing to tell the model to use."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/home/h/.hermes/cache/documents:/output",
            "/home/h/.hermes/cache/documents:/home/h/.hermes/cache/documents",
        ]))
        hint = _pb.build_media_delivery_hint()
        assert "/home/h/.hermes/cache/documents" in hint
        assert "MEDIA:/home/h/.hermes/cache/documents/" in hint

    def test_maps_export_mount_to_host_path_when_no_identity_mount(self, monkeypatch):
        """With only /output, the model must be told to WRITE the container
        path but EMIT the host path — emitting /output/x.md would be dropped."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/home/h/.hermes/cache/documents:/output",
        ]))
        hint = _pb.build_media_delivery_hint()
        assert "/output" in hint
        assert "MEDIA:/home/h/.hermes/cache/documents/" in hint

    def test_warns_when_no_writable_mount_exists(self, monkeypatch):
        """Without a shared mount, promising a file is a promise that cannot be
        kept — say so instead of letting the model discover it silently."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([]))
        hint = _pb.build_media_delivery_hint()
        assert "cannot be delivered" in hint.lower()

    def test_hint_states_the_boundary_itself(self, monkeypatch):
        """The regression: the sandbox $HOME is not a deliverable location."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        hint = _pb.build_media_delivery_hint()
        assert "host" in hint.lower()
        assert "$HOME" in hint

    def test_wired_into_environment_hints(self, monkeypatch):
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/srv/out:/srv/out",
        ]))
        result = _pb.build_environment_hints()
        assert "Terminal backend: docker" in result
        assert "MEDIA:/srv/out/" in result

    def test_non_docker_remote_backend_still_gets_the_boundary_warning(self, monkeypatch):
        """ssh/modal have no docker volume table, but the boundary is real."""
        monkeypatch.setenv("TERMINAL_ENV", "ssh")
        hint = _pb.build_media_delivery_hint()
        assert hint is not None
        assert "host" in hint.lower()


class TestDeliveryTargetSelection:
    """THE SECOND BUG, caught by running the hint against the live mount table:
    "first identity mount wins" picked /var/lib/browser-desktop — shared with
    the host, writable, and under /var/lib, which the delivery policy denies.
    The hint would have sent the model to a directory whose files are dropped
    exactly like the sandbox $HOME they came from."""

    def test_denied_prefixes_match_policy(self):
        """The mirrored denylist must not drift from the real one."""
        from gateway.platforms.base import _MEDIA_DELIVERY_DENIED_PREFIXES
        assert _pb._UNDELIVERABLE_HOST_PREFIXES == _MEDIA_DELIVERY_DENIED_PREFIXES

    def test_skips_identity_mount_under_a_denied_prefix(self):
        target = _pb.preferred_delivery_target([
            ("/var/lib/browser-desktop", "/var/lib/browser-desktop"),
            ("/srv/out", "/srv/out"),
        ])
        assert target == ("identity", "/srv/out", "/srv/out")

    def test_identity_view_of_the_export_dir_wins(self):
        """Both properties at once: it is the operator's declared export
        directory AND needs no translation."""
        target = _pb.preferred_delivery_target([
            ("/other", "/other"),
            ("/output", "/home/h/.hermes/cache/documents"),
            ("/home/h/.hermes/cache/documents", "/home/h/.hermes/cache/documents"),
        ])
        assert target == ("identity", "/home/h/.hermes/cache/documents",
                          "/home/h/.hermes/cache/documents")

    def test_export_mount_beats_an_unrelated_identity_mount(self):
        target = _pb.preferred_delivery_target([
            ("/other", "/other"),
            ("/output", "/home/h/.hermes/cache/documents"),
        ])
        assert target == ("export", "/output", "/home/h/.hermes/cache/documents")

    def test_all_mounts_denied_yields_nothing(self):
        assert _pb.preferred_delivery_target([("/root/x", "/root/x")]) is None

    def test_hint_matches_the_live_style_mount_table(self, monkeypatch):
        """Regression against the real config.yaml shape."""
        monkeypatch.setenv("TERMINAL_ENV", "docker")
        monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
            "/home/h/.hermes/config.yaml:/root/.hermes/config.yaml",
            "/home/h/.hermes/cache/documents:/output",
            "/var/lib/browser-desktop:/var/lib/browser-desktop",
            "/home/h/.hermes/cache/documents:/home/h/.hermes/cache/documents",
        ]))
        hint = _pb.build_media_delivery_hint()
        assert "MEDIA:/home/h/.hermes/cache/documents/" in hint
        assert "browser-desktop" not in hint
