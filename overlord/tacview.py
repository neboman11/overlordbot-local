"""Tacview real-time telemetry client and ACMI 2.x world model.

The parser (:class:`World`) and the network client (:func:`run`) are
independent: tests and replays can feed lines straight into ``World.feed_line``.

Unit dicts returned by :meth:`World.snapshot` have the keys
``id, name, callsign, coalition, type, category, lat, lon, altitude, heading``
plus ``roll, pitch, group, pilot, tacview_type``.  Coalition is 0 (neutral or
unknown), 1 (red) or 2 (blue); anything that is not explicitly red or blue is 0.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
import math
import socket
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

PROTOCOL_LINES = ("XtraLib.Stream.0", "Tacview.RealTimeTelemetry.0")
CLIENT_NAME = "OverlordBot"
MAX_HANDSHAKE_BYTES = 4096
MAX_LINE_BYTES = 1 << 20

_COALITION_NAMES = {"red": 1, "blue": 2, "neutral": 0, "neutrals": 0}
_COLOR_NAMES = {"red": 1, "blue": 2}


# --------------------------------------------------------------------------
# Handshake helpers
# --------------------------------------------------------------------------

def crc64_we(data: bytes) -> int:
    """CRC-64/WE (poly 0x42F0E1EBA9EA3693, init/xorout all ones, not reflected).

    Check value for b"123456789" is 0x62EC59E3F1A4F00A.
    """
    crc = 0xFFFFFFFFFFFFFFFF
    for byte in data:
        crc ^= byte << 56
        for _ in range(8):
            crc = ((crc << 1) ^ 0x42F0E1EBA9EA3693) if crc & (1 << 63) else crc << 1
            crc &= 0xFFFFFFFFFFFFFFFF
    return crc ^ 0xFFFFFFFFFFFFFFFF


def password_hash(password: str) -> str:
    """Hex password hash per the Tacview real-time protocol docs.

    CRC-64/WE of the UTF-16 password text (no terminator).  With no password the
    hash of the string "0" is used.  Little-endian UTF-16 without BOM and
    lowercase unpadded hex are assumptions: the docs do not state them.
    """
    return format(crc64_we((password or "0").encode("utf-16-le")), "x")


def build_server_greeting(host_name: str = "OverlordReplay") -> bytes:
    return ("\n".join(PROTOCOL_LINES) + "\n" + host_name + "\n\0").encode("utf-8")


def build_client_handshake(password: str = "", client_name: str = CLIENT_NAME) -> bytes:
    return ("\n".join(PROTOCOL_LINES) + "\n" + client_name + "\n" + password_hash(password) + "\0").encode("utf-8")


def parse_handshake(payload: bytes) -> tuple[str, str]:
    """Validate a NUL-stripped handshake; return (peer name, password field).

    The server greeting has no password line; the field is then ''.
    Raises ValueError on a protocol mismatch.
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("handshake is not UTF-8") from exc
    lines = text.split("\n")
    if tuple(lines[:2]) != PROTOCOL_LINES:
        raise ValueError(f"unexpected Tacview protocol: {lines[:2]!r}")
    name = lines[2] if len(lines) > 2 else ""
    pw = lines[3] if len(lines) > 3 else ""
    return name, pw


class LineSplitter:
    """Incrementally split a byte stream into decoded text lines."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[str]:
        self._buf += data
        lines = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            lines.append(raw.rstrip(b"\r").decode("utf-8", errors="replace"))
        if len(self._buf) > MAX_LINE_BYTES:
            raise ValueError("Tacview line too long")
        return lines


# --------------------------------------------------------------------------
# ACMI parsing
# --------------------------------------------------------------------------

def _split_escaped(line: str) -> list[str]:
    """Split on unescaped commas and resolve backslash escapes."""
    parts: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _split_property(field: str) -> Optional[tuple[str, str]]:
    # Keys never contain '=' so the first one separates key and value.
    key, sep, value = field.partition("=")
    if not sep:
        return None
    return key, value


def _finite(text: str) -> Optional[float]:
    """Float or None when empty; raises ValueError if invalid / non-finite."""
    if text == "":
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite value")
    return value


def _category(type_tags: str) -> str:
    tags = {t.strip().lower() for t in type_tags.split("+") if t.strip()}
    if "aerodrome" in tags:
        return "Airfield"
    if "air" in tags:
        if tags & {"fixedwing"}:
            return "Airplane"
        if tags & {"rotorcraft", "rotarywing", "helicopter"}:
            return "Helicopter"
        return "Air"
    if tags & {"missile", "weapon", "bomb", "projectile", "rocket"}:
        return "Weapon"
    if "sea" in tags:
        return "Ship"
    if "ground" in tags:
        return "Ground"
    return ""


def _coalition(props: dict[str, str]) -> int:
    name = props.get("Coalition", "").strip().lower()
    if name in _COALITION_NAMES:
        return _COALITION_NAMES[name]
    color = props.get("Color", "").strip().lower()
    if color in _COLOR_NAMES:
        return _COLOR_NAMES[color]
    return 0


class _Obj:
    __slots__ = ("props", "lon", "lat", "alt", "heading", "roll", "pitch", "speed", "sample")

    def __init__(self) -> None:
        self.props: dict[str, str] = {}
        self.lon = self.lat = self.alt = None
        self.heading = self.roll = self.pitch = None
        self.speed = None  # m/s, None until two samples at distinct times
        self.sample = None  # (lat, lon, frame time)


def _ground_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(min(1.0, math.sqrt(a)))


class World:
    """Thread-safe in-memory world built from an ACMI 2.x line stream."""

    def __init__(self, stale_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.stale_seconds = float(stale_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._reset_locked()

    # -- state management ---------------------------------------------------

    def _reset_locked(self) -> None:
        self._generation = getattr(self, '_generation', -1) + 1
        self._objects: dict[str, _Obj] = {}
        self._ref_lon = 0.0
        self._ref_lat = 0.0
        self._time = 0.0
        self._last_update: Optional[float] = None
        self._pending: Optional[str] = None
        self._received_position = False

    def reset(self) -> None:
        """Drop all state (new connection or full replay)."""
        with self._lock:
            self._reset_locked()

    @contextmanager
    def locked_snapshot(self):
        """Keep a generation and its copied state coherent during a controller operation."""
        with self._lock:
            yield self._generation, self.snapshot()

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def ready(self) -> bool:
        """True once positions were received and the stream is not stale."""
        with self._lock:
            if not self._received_position or self._last_update is None:
                return False
            return (self._clock() - self._last_update) <= self.stale_seconds

    @property
    def time(self) -> float:
        with self._lock:
            return self._time

    # -- parsing -------------------------------------------------------------

    def feed_line(self, line: str) -> None:
        line = line.rstrip("\r\n")
        with self._lock:
            if self._pending is not None:
                line = self._pending + "\n" + line
                self._pending = None
            # Odd number of trailing backslashes: multi-line value continues.
            stripped = line.rstrip("\\")
            if (len(line) - len(stripped)) % 2 == 1:
                self._pending = line[:-1]
                return
            try:
                self._parse_line(line)
            except ValueError as exc:
                log.debug("ignoring bad ACMI line %r: %s", line[:200], exc)

    def feed_text(self, text: str) -> None:
        for line in text.splitlines():
            self.feed_line(line)

    def _touch(self) -> None:
        self._last_update = self._clock()

    def _parse_line(self, line: str) -> None:
        if not line or line.startswith("//"):
            return
        if line.startswith("#"):
            next_time = float(line[1:])
            if not math.isfinite(next_time):
                raise ValueError("non-finite time")
            for obj in self._objects.values():
                self._sample_speed(obj)
            self._time = next_time
            self._touch()
            return
        if line.startswith("-"):
            self._objects.pop(line[1:].strip().lower(), None)
            self._touch()
            return
        fields = _split_escaped(line)
        head = fields[0]
        if "=" in head:
            return  # file header (FileType=..., FileVersion=...)
        obj_id = head.strip().lower()
        if not obj_id:
            return
        props = [p for p in (_split_property(f) for f in fields[1:]) if p]
        if obj_id == "0":
            self._global(props)
            return
        self._update(obj_id, props)

    def _global(self, props: list[tuple[str, str]]) -> None:
        for key, value in props:
            if key == "ReferenceLongitude":
                v = _finite(value.strip())
                if v is not None:
                    self._ref_lon = v
            elif key == "ReferenceLatitude":
                v = _finite(value.strip())
                if v is not None:
                    self._ref_lat = v

    def _update(self, obj_id: str, props: list[tuple[str, str]]) -> None:
        # Validate coordinates first so a bad update changes nothing.
        transform = None
        for key, value in props:
            if key == "T":
                transform = self._parse_transform(value)
        obj = self._objects.get(obj_id)
        if obj is None:
            obj = self._objects[obj_id] = _Obj()
        for key, value in props:
            if key != "T":
                obj.props[key] = value
        if transform is not None:
            lon, lat, alt, roll, pitch, heading = transform
            if lon is not None:
                obj.lon = lon
            if lat is not None:
                obj.lat = lat
            if alt is not None:
                obj.alt = alt
            if roll is not None:
                obj.roll = roll
            if pitch is not None:
                obj.pitch = pitch
            if heading is not None:
                obj.heading = heading % 360.0
            self._received_position = True
        self._touch()

    def _sample_speed(self, obj: _Obj) -> None:
        """Ground speed (m/s) from consecutive positions at distinct frame times."""
        if obj.lon is None or obj.lat is None:
            return
        t = self._time
        prev = obj.sample
        if prev is not None:
            dt = t - prev[2]
            if dt > 0:
                obj.speed = _ground_distance(prev[0], prev[1], obj.lat, obj.lon) / dt
                obj.sample = (obj.lat, obj.lon, t)
            elif dt < 0:  # time went backwards: discard history
                obj.speed = None
                obj.sample = (obj.lat, obj.lon, t)
        else:
            obj.sample = (obj.lat, obj.lon, t)

    def _parse_transform(self, value: str):
        parts = value.split("|")
        n = len(parts)
        if n < 3:
            raise ValueError("T needs at least 3 fields")
        vals = [_finite(p.strip()) for p in parts]
        lon, lat, alt = vals[0], vals[1], vals[2]
        roll = pitch = heading = None
        if n == 6 or n >= 9:
            roll, pitch, heading = vals[3:6]
        # Projected flat-world heading (field 9) is not geographic true yaw.
        if lon is not None:
            lon = ((lon + self._ref_lon + 180.0) % 360.0) - 180.0
        if lat is not None:
            lat = lat + self._ref_lat
            if not -90.0 <= lat <= 90.0:
                raise ValueError("latitude out of range")
        return lon, lat, alt, roll, pitch, heading

    # -- queries -------------------------------------------------------------

    def snapshot(self, coalition: Optional[int] = None) -> list[dict]:
        """Copied unit list; ``coalition`` keeps only exactly that coalition."""
        out: list[dict] = []
        with self._lock:
            for obj_id, obj in self._objects.items():
                if obj.lon is None or obj.lat is None or obj.alt is None:
                    continue
                unit = self._unit(obj_id, obj)
                if coalition is not None and unit["coalition"] != coalition:
                    continue
                out.append(unit)
        return out

    @staticmethod
    def _unit(obj_id: str, obj: _Obj) -> dict:
        p = obj.props
        pilot = p.get("Pilot", "")
        callsign = p.get("CallSign", "") or pilot
        type_tags = p.get("Type", "")
        unit = {
            "id": obj_id,
            "name": pilot or callsign or p.get("Name", ""),
            "callsign": callsign,
            "coalition": _coalition(p),
            "type": p.get("Name", ""),
            "category": _category(type_tags),
            "lat": obj.lat,
            "lon": obj.lon,
            "altitude": obj.alt,
            "heading": obj.heading if obj.heading is not None else 0.0,
            "roll": obj.roll if obj.roll is not None else 0.0,
            "pitch": obj.pitch if obj.pitch is not None else 0.0,
            "group": p.get("Group", ""),
            "pilot": pilot,
            "tacview_type": type_tags,
        }
        if obj.speed is not None:
            unit["speed"] = obj.speed
        return unit

    # -- network -------------------------------------------------------------

    def run(self, host: str, port: int, password: str = "",
            stop_event: Optional[threading.Event] = None) -> None:
        """Connect, stream and reconnect until ``stop_event`` is set."""
        stop = stop_event or threading.Event()
        delay = 1.0
        while not stop.is_set():
            try:
                self._session(host, port, password, stop)
                delay = 1.0
            except (OSError, ValueError) as exc:
                if not stop.is_set():
                    log.warning("tacview %s:%s: %s", host, port, exc)
            self.reset()  # never keep state across a connection
            if stop.wait(delay):
                break
            delay = min(delay * 2, 10.0)

    def _session(self, host: str, port: int, password: str, stop: threading.Event) -> None:
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.settimeout(1.0)
            self.reset()
            rest = _read_greeting(sock, stop)
            sock.sendall(build_client_handshake(password))
            log.info("tacview connected to %s:%s", host, port)
            splitter = LineSplitter()
            data = rest
            while not stop.is_set():
                if data:
                    for line in splitter.feed(data):
                        self.feed_line(line)
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    data = b""
                    continue
                if not data:
                    raise OSError("connection closed by server")


def _read_greeting(sock: socket.socket, stop: threading.Event) -> bytes:
    """Read up to the NUL ending the server greeting; return trailing bytes."""
    buf = bytearray()
    deadline = time.monotonic() + 10.0
    while b"\0" not in buf:
        if stop.is_set():
            raise OSError("stopped")
        if len(buf) > MAX_HANDSHAKE_BYTES:
            raise ValueError("Tacview greeting too long")
        if time.monotonic() > deadline:
            raise OSError("timed out waiting for Tacview greeting")
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            continue
        if not chunk:
            raise OSError("closed during Tacview greeting")
        buf += chunk
    head, _, rest = bytes(buf).partition(b"\0")
    parse_handshake(head)
    return rest


def run(world: World, host: str, port: int, password: str = "",
        stop_event: Optional[threading.Event] = None) -> None:
    world.run(host, port, password, stop_event)
