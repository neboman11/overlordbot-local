#!/usr/bin/env python3
"""Serve an ACMI file as a Tacview real-time telemetry server.

The header is sent once, followed by one complete recording. Closing the socket
forces a clean world reset before the client reconnects for another replay.
"""

from __future__ import annotations

import argparse
import logging
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from overlord import tacview  # noqa: E402

log = logging.getLogger("replay_tacview")


def load_acmi(path: str) -> tuple[list[str], list[list[str]]]:
    """Split ACMI into (header lines, frames); each frame starts with its #time line."""
    header: list[str] = []
    frames: list[list[str]] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if line.startswith("#"):
                frames.append([line])
            elif frames:
                frames[-1].append(line)
            else:
                header.append(line)
    if not frames:
        raise SystemExit(f"{path}: no frames (# lines) found")
    return header, frames


def _read_client_handshake(sock, limit: int = tacview.MAX_HANDSHAKE_BYTES) -> bytes:
    buf = bytearray()
    while b"\0" not in buf:
        if len(buf) > limit:
            raise ValueError("handshake too long")
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("closed during handshake")
        buf += chunk
    return bytes(buf).partition(b"\0")[0]


def make_handler(header, frames, interval: float, password: str, stop: threading.Event):
    expected = tacview.password_hash(password)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock = self.request
            sock.settimeout(10)
            try:
                sock.sendall(tacview.build_server_greeting())
                _, got = tacview.parse_handshake(_read_client_handshake(sock))
                if password and got != expected:
                    log.warning("%s: bad password", self.client_address)
                    return
                sock.sendall(("\n".join(header) + "\n").encode("utf-8"))
                for frame in frames:
                    if stop.is_set():
                        return
                    sock.sendall(("\n".join(frame) + "\n").encode("utf-8"))
                    if stop.wait(interval):
                        return
            except (OSError, ValueError) as exc:
                log.info("%s: %s", self.client_address, exc)

    return Handler


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host: str, port: int, path: str, interval: float, password: str,
          stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    header, frames = load_acmi(path)
    with Server((host, port), make_handler(header, frames, interval, password, stop)) as srv:
        log.info("serving %s on %s:%s", path, host, srv.server_address[1])
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            stop.wait()
        finally:
            srv.shutdown()


def main(argv: list[str] | None = None) -> int:
    default_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "replay.acmi")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=42674)
    ap.add_argument("--file", default=default_file)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--password", default="")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        serve(args.host, args.port, args.file, args.interval, args.password)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
