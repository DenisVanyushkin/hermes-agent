"""Fork: tell the model where the sandbox and the host filesystem meet.

Two true statements used to be told separately and never joined: "your
write_file/terminal tools run inside the sandbox, the host is irrelevant" (the
remote-backend hint) and "include MEDIA:/absolute/path to deliver a file"
(PLATFORM_HINTS). A file written to the sandbox's own $HOME satisfies both and
is still undeliverable: MEDIA paths are resolved by the gateway on the HOST, the
tag is dropped without an error, and the user receives nothing (2026-07-31,
"MEDIA:/root/social-connections-bft.md").

The mount table is read from the live TERMINAL_DOCKER_VOLUMES, so the hint
cannot drift from config.yaml. Moved from the pre-v0.22.5
agent/prompt_builder.py; build_environment_hints calls in at a marked site and
prompt_builder re-exports the names.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("agent.prompt_builder")


def _tenv(name: str, default: str = "") -> str:
    try:
        from agent.prompt_builder import _tenv_read
    except ImportError:
        return os.getenv(name, default)
    return _tenv_read(name, default)


def writable_delivery_mounts() -> list:
    """Return [(container_path, host_path)] for writable docker bind mounts.

    Read-only mounts are excluded: the cache directories are auto-mounted
    ``:ro``, so advertising them as a place to write deliverables would send
    the model straight into a permission error.
    """
    raw = (_tenv("TERMINAL_DOCKER_VOLUMES") or "").strip()
    if not raw:
        return []
    try:
        specs = json.loads(raw)
    except Exception:
        logger.debug("Could not parse TERMINAL_DOCKER_VOLUMES for the delivery hint")
        return []
    if not isinstance(specs, list):
        return []

    mounts = []
    for spec in specs:
        if not isinstance(spec, str):
            continue
        parts = spec.strip().split(":")
        if len(parts) < 2:
            continue
        options = []
        if len(parts) >= 3 and not parts[-1].startswith("/"):
            options = [o.strip() for o in parts[-1].split(",") if o.strip()]
            parts = parts[:-1]
        container_path = parts[-1]
        host_path = ":".join(parts[:-1])
        if not container_path.startswith("/") or not host_path:
            continue
        if "ro" in options:
            continue
        mounts.append((container_path.rstrip("/"), host_path.rstrip("/")))
    return mounts


# Host prefixes the delivery policy refuses outright. Mirrors
# gateway.platforms.base._MEDIA_DELIVERY_DENIED_PREFIXES -- importing the gateway
# here would drag the platform stack into every CLI session, so the list is
# mirrored and pinned by tests/agent/test_media_delivery_hint.py.
_UNDELIVERABLE_HOST_PREFIXES = (
    "/etc", "/proc", "/sys", "/dev", "/root", "/boot",
    "/var/log", "/var/lib", "/var/run",
)

# Container paths that are, by convention, the operator's export mount.
_EXPORT_CONTAINER_PATHS = ("/output", "/outputs")


def _host_path_is_deliverable(host_path: str) -> bool:
    return not any(
        host_path == prefix or host_path.startswith(prefix + "/")
        for prefix in _UNDELIVERABLE_HOST_PREFIXES
    )


def preferred_delivery_target(mounts):
    """Pick where the agent should write deliverables.

    Order of preference, best first:
      1. An identity mount (same path both sides) of the operator's export
         directory -- deliverable, and needs no path translation at all.
      2. The export mount itself: write the container path, emit the host one.
      3. Any other identity mount.
    Mounts whose host side the delivery policy would refuse are skipped at
    every step. Returns ``(kind, container_path, host_path)`` or None.
    """
    export = [(c, h) for c, h in mounts if c in _EXPORT_CONTAINER_PATHS]
    identity = [(c, h) for c, h in mounts if c == h]
    export_hosts = {h for _, h in export}

    for container, host in identity:
        if host in export_hosts and _host_path_is_deliverable(host):
            return ("identity", container, host)
    for container, host in export:
        if _host_path_is_deliverable(host):
            return ("export", container, host)
    for container, host in identity:
        if _host_path_is_deliverable(host):
            return ("identity", container, host)
    return None


_MEDIA_DELIVERY_BOUNDARY = (
    "File delivery crosses a boundary your tools do not: a MEDIA:/path tag is "
    "resolved by Hermes on the HOST filesystem, while write_file and terminal "
    "write inside this sandbox. A file you put in the sandbox's own $HOME "
    "(/root/report.md and friends) does not exist for the host, so the tag is "
    "dropped -- you will see no error, and the user will receive nothing."
)


def build_media_delivery_hint(backend: str | None = None) -> str | None:
    """Explain where the sandbox and host filesystems actually meet.

    Returns None for local backends, where there is no boundary to explain.
    """
    from agent.prompt_builder import _REMOTE_TERMINAL_BACKENDS

    if backend is None:
        backend = (_tenv("TERMINAL_ENV") or "local").strip().lower()
    if backend not in _REMOTE_TERMINAL_BACKENDS:
        return None

    lines = [_MEDIA_DELIVERY_BOUNDARY]

    if backend != "docker":
        lines.append(
            "Before promising a file, confirm with the operator which directory "
            "is shared with the Hermes host, and emit the HOST path in the "
            "MEDIA: tag. If no directory is shared, put the content in your "
            "reply instead of promising an attachment."
        )
        return "\n".join(lines)

    target = preferred_delivery_target(writable_delivery_mounts())

    if target and target[0] == "identity":
        _, container, _host = target
        lines.append(
            f"Write every file you intend to deliver to {container}/<name> and "
            f"reference exactly that path (MEDIA:{container}/<name>). This "
            f"directory has the same path inside the sandbox and on the host, "
            f"so nothing needs translating."
        )
    elif target:
        _, container, host = target
        lines.append(
            f"Write deliverables to {container}/<name> inside the sandbox. On "
            f"the host that same file is {host}/<name> -- emit the HOST path: "
            f"MEDIA:{host}/<name>."
        )
    else:
        lines.append(
            "No writable directory is shared with the host, so files produced "
            "here cannot be delivered at all. Do not promise the user a file -- "
            "put the content in your reply instead."
        )
    return "\n".join(lines)
