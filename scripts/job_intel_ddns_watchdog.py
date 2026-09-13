#!/usr/bin/env python3
"""Refresh the LinkedIn WireGuard peer when its DDNS address changes."""

import argparse
from dataclasses import asdict, dataclass
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
from typing import Callable, Sequence


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


def _set_endpoint(command: Sequence[str]) -> None:
    subprocess.run(command, check=True)


def refresh_endpoint(
    *,
    config_text: str,
    resolve: Callable[[str], list[str]],
    run: Callable[[Sequence[str]], object],
    current_endpoint: Callable[[Sequence[str], str], str | None],
    namespace: str = "ln-eg",
    interface: str = "wg0-ln",
) -> RefreshResult:
    endpoint = _config_value(config_text, "Endpoint")
    peer_key = _config_value(config_text, "PublicKey")
    if not endpoint or not peer_key:
        raise RuntimeError("WireGuard config needs Endpoint and peer PublicKey")
    endpoint_host, endpoint_port = _split_endpoint(endpoint)

    # Do not call wg set before a successful host-side DNS result. An empty
    # resolution therefore leaves the live endpoint untouched by construction.
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

    show_command = ("ip", "netns", "exec", namespace, "wg", "show", interface, "endpoints")
    live_endpoint = current_endpoint(show_command, peer_key)
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
    set_command = list(show_command[:5]) + ["set", interface, "peer", peer_key, "endpoint", selected]
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
    parser.add_argument("--config", type=Path, default=Path("/etc/wireguard/wg0-ln.conf"))
    parser.add_argument("--namespace", default="ln-eg")
    parser.add_argument("--interface", default="wg0-ln")
    args = parser.parse_args(argv)

    try:
        result = refresh_endpoint(
            config_text=args.config.read_text(encoding="utf-8"),
            resolve=resolve_ipv4,
            run=_set_endpoint,
            current_endpoint=_read_current_endpoint,
            namespace=args.namespace,
            interface=args.interface,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ddns watchdog failed: {exc}")
        return 1

    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.status != "resolution_failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
