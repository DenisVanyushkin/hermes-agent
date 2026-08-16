from __future__ import annotations

import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path


RELAY = Path(__file__).resolve().parents[2] / "scripts" / "browser-desktop-cdp-relay.py"


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while data := self.request.recv(65536):
            self.request.sendall(data)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_relay_moves_bytes_bidirectionally() -> None:
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler) as echo:
        echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
        echo_thread.start()
        relay_port = _free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                str(RELAY),
                "--listen-host",
                "127.0.0.1",
                "--listen-port",
                str(relay_port),
                "--target-host",
                "127.0.0.1",
                "--target-port",
                str(echo.server_address[1]),
                "--connect-timeout",
                "0.1",
            ]
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    client = socket.create_connection(("127.0.0.1", relay_port), timeout=1)
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            with client:
                time.sleep(0.3)
                client.sendall(b"linkedin-cdp")
                assert client.recv(64) == b"linkedin-cdp"
        finally:
            process.terminate()
            process.wait(timeout=5)
            echo.shutdown()
