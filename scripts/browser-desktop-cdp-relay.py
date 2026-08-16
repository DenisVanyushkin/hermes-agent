#!/usr/bin/env python3
"""Small TCP relay used to expose namespace-local Chromium CDP to localhost."""

from __future__ import annotations

import argparse
import select
import socket
import socketserver
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    connect_timeout: float


class RelayHandler(socketserver.BaseRequestHandler):
    target: Target

    def handle(self) -> None:
        with socket.create_connection(
            (self.target.host, self.target.port), timeout=self.target.connect_timeout
        ) as upstream:
            upstream.settimeout(None)
            peers = {self.request: upstream, upstream: self.request}
            while True:
                readable, _, _ = select.select(tuple(peers), (), ())
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    peers[source].sendall(data)


class ThreadingRelay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", required=True, type=_port)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=_port)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    args = parser.parse_args()

    handler = type(
        "ConfiguredRelayHandler",
        (RelayHandler,),
        {"target": Target(args.target_host, args.target_port, args.connect_timeout)},
    )
    with ThreadingRelay((args.listen_host, args.listen_port), handler) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
