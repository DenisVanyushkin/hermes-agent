#!/usr/bin/env python3
"""Refresh the LinkedIn WireGuard peer when its DDNS address changes."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from typing import Callable, Sequence


DEFAULT_STATE_PATH = Path("/run/job-intel-ddns/linkedin-endpoint.json")


@dataclass(frozen=True)
class RefreshResult:
    status: str
    endpoint_host: str
    endpoint_port: str
    peer_key: str
    resolved_addresses: tuple[str, ...]
    current_endpoint: str | None
    selected_endpoint: str | None = None
    command: tuple[str, ...] = ()


def _config_value(config_text: str, key: str) -> str:
    prefix = f"{key.lower()}="
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip().lower() == key.lower():
            return value.strip()
    return ""


def _split_endpoint(value: str) -> tuple[str, str]:
    value = value.strip()
    if value.startswith("["):
        host, separator, remainder = value[1:].partition("]:")
        if separator and remainder:
            return host, remainder
    host, separator, port = value.rpartition(":")
    if not separator or not host or not port:
        raise ValueError(f"invalid endpoint: {value!r}")
    return host, port


def resolve_ipv4(host: str) -> list[str]:
    """Resolve on the caller's namespace; this process must run on the host."""
    addresses: list[str] = []
    for item in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM):
        address = str(item[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _endpoint_address(endpoint: str | None) -> tuple[str, str] | None:
    if not endpoint or endpoint in {"none", "off"}:
        return None
    try:
        host, port = _split_endpoint(endpoint)
        return str(ipaddress.ip_address(host)), port
    except (ValueError, TypeError):
        return None


def _read_current_endpoint(command: Sequence[str], peer_key: str) -> str | None:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == peer_key:
            return fields[1] if len(fields) > 1 else None
    raise RuntimeError(f"WireGuard peer was not found: {peer_key}")


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def resolve_and_store(
    *,
    config_text: str,
    resolve: Callable[[str], list[str]],
    state_path: Path,
    now: str | None = None,
) -> RefreshResult:
    endpoint = _config_value(config_text, "Endpoint")
    peer_key = _config_value(config_text, "PublicKey")
    if not endpoint or not peer_key:
        raise RuntimeError("WireGuard config needs Endpoint and peer PublicKey")
    endpoint_host, endpoint_port = _split_endpoint(endpoint)

    resolved = tuple(resolve(endpoint_host))
    if not resolved:
        return RefreshResult(
            status="resolution_failed",
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            peer_key=peer_key,
            resolved_addresses=resolved,
            current_endpoint=None,
        )

    _write_state(
        state_path,
        {
            "version": 1,
            "endpoint_host": endpoint_host,
            "endpoint_port": endpoint_port,
            "peer_key": peer_key,
            "resolved_addresses": list(resolved),
            "resolved_at": now or datetime.now(timezone.utc).isoformat(),
        },
    )
    return RefreshResult(
        status="resolved",
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        peer_key=peer_key,
        resolved_addresses=resolved,
        current_endpoint=None,
        selected_endpoint=f"{resolved[0]}:{endpoint_port}",
    )


def apply_endpoint(
    *,
    state_text: str,
    run: Callable[[Sequence[str]], object],
    current_endpoint: Callable[[str, str], str | None],
    interface: str = "wg0-ln",
) -> RefreshResult:
    try:
        state = json.loads(state_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid DDNS state JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise RuntimeError("DDNS state must be a JSON object")
    endpoint_host = str(state.get("endpoint_host", ""))
    endpoint_port = str(state.get("endpoint_port", ""))
    peer_key = str(state.get("peer_key", ""))
    raw_addresses = state.get("resolved_addresses")
    if not endpoint_port or not peer_key or not isinstance(raw_addresses, list):
        raise RuntimeError("DDNS state is missing endpoint_port, peer_key, or resolved_addresses")
    resolved = tuple(str(address) for address in raw_addresses if str(address))
    if not resolved:
        raise RuntimeError("DDNS state has no resolved addresses")

    live_endpoint = current_endpoint(interface, peer_key)
    live_parts = _endpoint_address(live_endpoint)
    if live_parts is not None and live_parts[0] in resolved and live_parts[1] == endpoint_port:
        return RefreshResult(
            status="unchanged",
            endpoint_host=endpoint_host,
            endpoint_port=endpoint_port,
            peer_key=peer_key,
            resolved_addresses=resolved,
            current_endpoint=live_endpoint,
        )

    selected = f"{resolved[0]}:{endpoint_port}"
    set_command = ["wg", "set", interface, "peer", peer_key, "endpoint", selected]
    run(set_command)
    return RefreshResult(
        status="updated",
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        peer_key=peer_key,
        resolved_addresses=resolved,
        current_endpoint=live_endpoint,
        selected_endpoint=selected,
        command=tuple(set_command),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("resolve", "apply"), default="resolve")
    parser.add_argument("--config", type=Path, default=Path("/etc/wireguard/wg0-ln.conf"))
    parser.add_argument("--interface", default="wg0-ln")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(argv)

    try:
        if args.mode == "resolve":
            result = resolve_and_store(
                config_text=args.config.read_text(encoding="utf-8"),
                resolve=resolve_ipv4,
                state_path=args.state,
            )
        else:
            result = apply_endpoint(
                state_text=args.state.read_text(encoding="utf-8"),
                run=lambda command: subprocess.run(command, check=True),
                current_endpoint=lambda interface, peer: _read_current_endpoint(
                    ["wg", "show", interface, "endpoints"], peer
                ),
                interface=args.interface,
            )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ddns watchdog failed: {exc}")
        return 1

    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status != "resolution_failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
