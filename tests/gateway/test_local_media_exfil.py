"""MEDIA delivery never turns this deployment's sandbox mounts into an exfil channel.

Upstream v0.22.5 translates a container path through every docker volume, read-
only ones included. The production mounts include the agent repo (whose .env
holds EMAIL_PASSWORD) at /workspace/live-hermes:ro and an SSH key dir at
/opt/hermes-webui/ssh:ro; the media denylist covers neither. These tests use the
same mount shapes against throwaway host dirs.
"""

from __future__ import annotations

import json

import pytest

from gateway.platforms.base import validate_media_delivery_path


@pytest.fixture
def prod_shaped_mounts(tmp_path, monkeypatch):
    repo = tmp_path / "hermes-agent"
    repo.mkdir()
    (repo / ".env").write_text("EMAIL_PASSWORD=hunter2\n")
    ssh = tmp_path / "webui-ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    webui = tmp_path / "webui-repo"
    webui.mkdir()
    (webui / "settings.py").write_text("SECRET_KEY = 'x'\n")
    out = tmp_path / "documents"
    out.mkdir()
    (out / "report.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", json.dumps([
        f"{repo}:/workspace/live-hermes:ro",
        f"{ssh}:/opt/hermes-webui/ssh:ro",
        f"{webui}:/workspace/hermes-webui:rw",
        f"{out}:/output",
    ]))
    return out


@pytest.mark.parametrize("container_path", [
    "/workspace/live-hermes/.env",
    "/opt/hermes-webui/ssh/id_ed25519",
    "/workspace/hermes-webui/settings.py",
])
def test_a_mounted_host_file_outside_the_export_mount_is_never_delivered(prod_shaped_mounts, container_path):
    assert validate_media_delivery_path(container_path) is None


def test_the_export_mount_still_delivers(prod_shaped_mounts):
    assert validate_media_delivery_path("/output/report.pdf") == str((prod_shaped_mounts / "report.pdf").resolve())
