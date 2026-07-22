#!/usr/bin/env python3
"""Compare the system DNS resolver against DNS-over-HTTPS and export the delta.

Why this exists: the Telegram adapter already works around a broken resolver by
discovering fallback IPs over DoH. This script turns that manual workaround into
a metric, so "the resolver is lying" can be distinguished from "the route is
down" at a glance.

Runs from the hermes crontab every minute; writes to the node_exporter textfile
collector directory. Deliberately depends only on the stdlib plus httpx — no
dnspython, because it is not installed and adding a dependency creates
rebase conflicts with upstream for no benefit.
"""
from __future__ import annotations

import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DOH_URL = "https://1.1.1.1/dns-query"
DOH_HEADERS = {"Accept": "application/dns-json"}
TEXTFILE_PATH = Path(
    os.environ.get("NET_PROBE_DNS_OUTPUT", "/var/lib/hermes-metrics/net_probe_dns.prom")
)
NAMES = [
    "api.telegram.org",
    "openrouter.ai",
    "chatgpt.com",
    "slack.com",
    "adilet.zan.kz",
]
TIMEOUT_SECONDS = 5.0
DNS_RECORD_TYPE_A = 1


@dataclass
class ProbeResult:
    name: str
    system_ips: set[str] = field(default_factory=set)
    system_seconds: float = 0.0
    system_ok: bool = False
    doh_ips: set[str] = field(default_factory=set)
    doh_seconds: float = 0.0
    doh_ok: bool = False


def resolve_system(name: str) -> tuple[set[str], float, bool]:
    """Resolve via the host's configured resolver using the stdlib."""
    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(name, None, family=socket.AF_INET)
    except (socket.gaierror, OSError):
        return set(), time.monotonic() - started, False
    ips = {info[4][0] for info in infos}
    return ips, time.monotonic() - started, True


def resolve_doh(name: str, client) -> tuple[set[str], float, bool]:
    """Resolve via Cloudflare DoH JSON API. `client` is any object with .get()."""
    started = time.monotonic()
    try:
        response = client.get(
            DOH_URL, params={"name": name, "type": "A"}, headers=DOH_HEADERS
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return set(), time.monotonic() - started, False
    ips = {
        answer["data"]
        for answer in payload.get("Answer", [])
        if answer.get("type") == DNS_RECORD_TYPE_A
    }
    return ips, time.monotonic() - started, True


def compare_answers(system_ips: set[str], doh_ips: set[str]) -> bool:
    """True when the two resolvers disagree entirely.

    Any intersection counts as agreement: CDNs routinely hand out different
    subsets of a large A-record pool, and treating that as a mismatch would
    make the metric fire constantly and get ignored.
    """
    return not (system_ips & doh_ips)


def render_metrics(results: list[ProbeResult]) -> str:
    seconds_lines: list[str] = []
    success_lines: list[str] = []
    mismatch_lines: list[str] = []
    for r in results:
        seconds_lines.append(
            f'hermes_dns_resolve_seconds{{name="{r.name}",resolver="system"}} {r.system_seconds:g}'
        )
        seconds_lines.append(
            f'hermes_dns_resolve_seconds{{name="{r.name}",resolver="doh"}} {r.doh_seconds:g}'
        )
        success_lines.append(
            f'hermes_dns_resolve_success{{name="{r.name}",resolver="system"}} {int(r.system_ok)}'
        )
        success_lines.append(
            f'hermes_dns_resolve_success{{name="{r.name}",resolver="doh"}} {int(r.doh_ok)}'
        )
        # Only comparable when BOTH resolvers answered. Emitting 0 otherwise
        # would assert agreement we never observed.
        if r.system_ok and r.doh_ok:
            mismatch = int(compare_answers(r.system_ips, r.doh_ips))
            mismatch_lines.append(f'hermes_dns_answer_mismatch{{name="{r.name}"}} {mismatch}')

    lines = [
        "# HELP hermes_dns_resolve_seconds DNS resolution time by resolver.",
        "# TYPE hermes_dns_resolve_seconds gauge",
        *seconds_lines,
        "# HELP hermes_dns_resolve_success 1 if the resolver answered, 0 otherwise.",
        "# TYPE hermes_dns_resolve_success gauge",
        *success_lines,
    ]
    # Headers for hermes_dns_answer_mismatch are themselves omitted when no
    # result qualifies, so the metric name never appears at all in that case
    # (not just the series) — matching "absence of data is honest" fully.
    if mismatch_lines:
        lines.extend(
            [
                "# HELP hermes_dns_answer_mismatch 1 when system and DoH answers are disjoint.",
                "# TYPE hermes_dns_answer_mismatch gauge",
                *mismatch_lines,
            ]
        )
    return "\n".join(lines) + "\n"


def write_atomically(path: Path, content: str) -> None:
    """Write via temp file + rename, so node_exporter never reads a partial file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def main() -> int:
    results = []
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for name in NAMES:
            system_ips, system_seconds, system_ok = resolve_system(name)
            doh_ips, doh_seconds, doh_ok = resolve_doh(name, client)
            results.append(
                ProbeResult(
                    name=name,
                    system_ips=system_ips,
                    system_seconds=system_seconds,
                    system_ok=system_ok,
                    doh_ips=doh_ips,
                    doh_seconds=doh_seconds,
                    doh_ok=doh_ok,
                )
            )
    write_atomically(TEXTFILE_PATH, render_metrics(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
