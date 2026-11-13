"""Carrier operations radio: one independent controller per mission carrier frequency.

Facts come only from the DCS mission (``dcs/overlord_carrier.lua``) through the
server hook, which POSTs ``/carrier/exchange`` about once a second.  Each ship's
radio is the ship UNIT frequency (Hz) and modulation configured in the mission;
nothing here allocates, remaps or invents a frequency.

Bridge exchange, schema version 1
---------------------------------
Request (hook -> brain)::

    {"version": 1, "epoch": "<1-64 [A-Za-z0-9._:-]>", "seq": <int, increasing per epoch>,
     "time": <model seconds>, "abs_time": <mission seconds since midnight, [0, 86400)>,
     "date": {"year", "month", "day"}, "error": "<mission problems>" | null,
     "carriers": [{"id", "name", "type", "coalition": 1|2, "alive": bool,
                   "frequency_hz": int | null, "modulation": 0|1,
                   "point": {x, y, z}, "velocity": {x, y, z}, "heading_deg": TRUE,
                   "lat", "lon",                       # geometry null when not alive
                   "recovery_case": 1|2|3|null, "altimeter_inhg", "ceiling_ft", "visibility_nm",
                   "deck_angle_deg", "deck_height_m"}],                 # each nullable
     "players": [{"unit_id", "unit_name", "player_name", "callsign", "modex", "coalition",
                  "incarnation", "human": true}],     # identity catalog only, never membership
     "aircraft": [{"unit_id", "incarnation", "carrier_id", "point", "velocity", "heading_deg",
                   "in_air": bool, "on_deck": bool,
                   "events": [{"id": int, "type": "land"|"takeoff"|"dead"|"eject"|"disconnect"|"birth",
                               "carrier_id": str | null, "time": number}]}]}

Response::

    {"version": 1, "accepted": true, "epoch", "seq", "watch": [{"unit_id", "incarnation", "carrier_id"}]}
    {"version": 1, "accepted": false, "error": "stale sequence" | "retired epoch", "epoch", "seq", "watch": []}

DCS coordinates: x north, z east, y altitude MSL, metres.  An empty Lua table may
arrive as ``{}``.  A malformed envelope (version, epoch, seq, time, date, list sizes,
duplicate ids) raises ``ValueError`` before any state changes; an invalid individual
carrier, player or aircraft record is dropped alone and reported in ``registry()["errors"]``
(an invalid sample of a watched aircraft closes that aircraft's session).

Membership: only an explicit radio call (check in / inbound / request recovery,
request departure / launch / startup, or an emergency) by a caller who resolves to
exactly one catalogued human aircraft enrols it, on that ship only.  ``watch``
asks the mission to report that aircraft; a newly watched aircraft may be absent
from snapshots up to the one after the first response that listed it, and any
later absence, catalog loss, incarnation change or lifecycle loss closes it.

Separation policy (deterministic, conservative): one deck/approach lane per
ship.  A recovering aircraft holds it from its Charlie/commence until it reports
the deck clear (or leaves); a departing aircraft from its launch clearance until
its launch is observed.  Lane order: emergency first, then enrolment order.
CASE I holds overhead at angels 2-9; CASE II/III marshal on the final-bearing
reciprocal radial at angels 6-13, DME = angels + 15, approach times one minute
apart.  Bearings are spoken magnetic (WMM at the mission date/time); BRC is the
ship's true heading converted to magnetic, final bearing = heading - deck angle.

A land contact is not a trap: only the mission's debounced ``on_deck`` confirms
it, and leaving the deck after a contact is a bolter.  Pilot claims never
announce aircraft actions.  Replies carry ``validation``; ``validate_reply``
accepts "state" replies only while the bridge is fresh and the aircraft's
session still has that revision, "notice" replies while the radio and epoch are
unchanged and, when addressed to an aircraft, the same occupant (catalog incarnation and
enrolment generation) is still in that seat.  A supplied ``sender_callsign`` (SRS radio
name) must positively and uniquely name the spoken caller; this is identity correlation,
not authentication.
"""
from collections import OrderedDict, deque
import datetime as _dt
from decimal import Decimal, InvalidOperation
import itertools
import math
import re
import threading
import time

from . import carrier_parser
from . import domain
from .identities import identify_player, sender_identifies
from .jtac import _ID, _bool, _list, _num, _readback_runs, _text

FREQUENCY_GUARD_HZ = 200        # receive windows closer than this conflict
REPLY_TTL, EVENT_TTL = 15, 10
MAX_REPLY_CHARS = 1000
MAX_CARRIERS, MAX_PLAYERS, MAX_AIRCRAFT, MAX_LIFECYCLE = 32, 256, 64, 32
MAX_SESSIONS = 64
MAX_RETIRED = 256
MAX_DIAGNOSTICS = 16            # rejected-record messages kept for the registry
MAX_NOTIFICATIONS = 32          # per carrier
MAX_SEEN_EVENTS = 128           # per aircraft
EVENT_TYPES = {"land", "takeoff", "dead", "eject", "disconnect", "birth"}
TERMINAL_EVENTS = {"dead", "eject", "disconnect", "birth"}
FEET_PER_METRE = 3.28084
METRES_PER_NM = domain.METRES_PER_NM
CASE_ONE_ANGELS = tuple(range(2, 10))
MARSHAL_ANGELS = tuple(range(6, 14))
DME_OFFSET = 15
APPROACH_INTERVAL = 60
FIRST_APPROACH_DELAY = 300
REASSIGN_DEG = 1                # a changed spoken recovery course invalidates the old instructions
ALTITUDE_TOLERANCE_FT = 1000
OVERHEAD_NM = 10
FIX_TOLERANCE_NM = 6
CALL_RANGE_NM = {"commencing": 60, "platform": 35, "initial": 8, "break": 5, "downwind": 5, "final": 15}
BALL_MAX_NM, BALL_MAX_FT, BALL_SECTOR_DEG = 2, 1500, 45
HANDOFF_NM = 10

HOLDING = ("checked_in", "assigned", "marshal", "established")
APPROACH = ("commencing", "platform", "initial", "break", "downwind", "final", "ball")
AIRBORNE_RECOVERY = HOLDING + APPROACH
PRE_LAUNCH = ("startup", "queued", "released")
# Pattern call -> {case: phases it may follow}.  "initial" from "established" also needs the lane.
PATTERN = {
    "commencing": {1: ("established",), 2: ("established",), 3: ("established",)},
    "platform": {2: ("commencing",), 3: ("commencing",)},
    "initial": {1: ("established", "commencing"), 2: ("commencing", "platform")},
    "break": {1: ("initial",), 2: ("initial",)},
    "downwind": {1: ("break",), 2: ("break",)},
    "final": {3: ("platform",)},
    "ball": {1: ("downwind",), 2: ("downwind",), 3: ("final",)},
}
PROMPT = {
    "commencing": {1: "report initial", 2: "report initial", 3: "report platform"},
    "platform": {2: "report initial", 3: "report ten miles"},
    "initial": {1: "report the break", 2: "report the break"},
    "break": {1: "report abeam", 2: "report abeam"},
    "downwind": {1: "call the ball", 2: "call the ball"},
    "final": {3: "call the ball"},
}
ACK = {"commencing": "roger commencing", "platform": "roger platform", "initial": "roger initial",
       "break": "roger break", "downwind": "roger abeam", "final": "roger"}
VOCABULARY = ["marshal", "tower", "paddles", "departure", "checking in", "request departure",
              "ready for launch", "readback", "angels", "radial", "DME", "altimeter", "established",
              "commencing", "platform", "initial", "break", "abeam", "ten miles", "ball", "clara",
              "wave off", "bolter", "deck clear", "airborne", "departure complete", "say again",
              "checking out"]
_CHANNEL = re.compile(r"([12]):(\d{1,12}):([01])")
_MODEX = re.compile(r"[0-9A-Za-z]{0,8}")
_READBACK_LABELS = re.compile(r"\b(?:radial|d\s*m\s*e|angels?|expected|approach|time|eat|altimeter|marshal"
                              r"|holding|hold|overhead|on|minutes?|inches|correct)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Bridge payload validation
# ---------------------------------------------------------------------------

def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid bridge identifier")
    return value


def _label(value, limit=64):
    """Possibly empty printable text (callsign, unit name)."""
    if value is None or value == "":
        return ""
    return _text(value, limit)


def _optional(value, lo, hi, integer=False):
    return None if value is None else _num(value, lo, hi, integer)


def _vector(value, horizontal, vertical):
    if not isinstance(value, dict):
        raise ValueError("invalid bridge vector")
    return {"x": _num(value.get("x"), -horizontal, horizontal),
            "y": _num(value.get("y"), *vertical),
            "z": _num(value.get("z"), -horizontal, horizontal)}


def _point(value):
    return _vector(value, 1e7, (-2000, 100000))


def _velocity(value):
    return _vector(value, 2000, (-2000, 2000))


def _carrier(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid carrier")
    alive = _bool(raw.get("alive"))
    entry = {
        "id": _id(raw.get("id")), "name": _text(raw.get("name"), 64), "type": _text(raw.get("type"), 64),
        "coalition": _num(raw.get("coalition"), 1, 2, integer=True), "alive": alive,
        "frequency_hz": _optional(raw.get("frequency_hz"), 1000000, 2000000000, integer=True),
        "modulation": _num(raw.get("modulation", 0), 0, 1, integer=True),
        "point": None, "velocity": None, "heading": None, "lat": None, "lon": None,
        "case": _optional(raw.get("recovery_case"), 1, 3, integer=True),
        "altimeter": _optional(raw.get("altimeter_inhg"), 25, 32.5),
        "ceiling": _optional(raw.get("ceiling_ft"), 0, 100000),
        "visibility": _optional(raw.get("visibility_nm"), 0, 100),
        "deck_angle": _optional(raw.get("deck_angle_deg"), -15, 15),
        "deck_height": _optional(raw.get("deck_height_m"), 0, 100),
    }
    if alive:
        lat = _num(raw.get("lat"), -90, 90)
        if abs(lat) == 90:
            raise ValueError("carrier latitude at a pole")
        entry.update(point=_point(raw.get("point")), velocity=_velocity(raw.get("velocity")),
                     heading=_num(raw.get("heading_deg"), 0, 360) % 360,
                     lat=lat, lon=_num(raw.get("lon"), -180, 180))
    return entry


def _player(raw):
    """Catalog entry, or None for a non-human occupant (never enrolable)."""
    if not isinstance(raw, dict):
        raise ValueError("invalid player")
    modex = raw.get("modex", "")
    if not isinstance(modex, str) or not _MODEX.fullmatch(modex):
        raise ValueError("invalid modex")
    entry = {"unit_id": _id(raw.get("unit_id")), "unit_name": _label(raw.get("unit_name")),
             "player_name": _text(raw.get("player_name"), 64), "callsign": _label(raw.get("callsign")),
             "modex": modex, "coalition": _num(raw.get("coalition"), 1, 2, integer=True),
             "incarnation": _id(raw.get("incarnation"))}
    return entry if _bool(raw.get("human")) else None


def _aircraft(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid aircraft")
    events = []
    for event in _list(raw.get("events"), MAX_LIFECYCLE):
        if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
                or event["type"] not in EVENT_TYPES):
            raise ValueError("invalid lifecycle event")
        place = event.get("carrier_id")
        events.append({"id": _num(event.get("id"), 0, 2 ** 53, integer=True), "type": event["type"],
                       "carrier_id": None if place is None else _id(place),
                       "time": _num(event.get("time"), 0, 1e9)})
    return {"unit_id": _id(raw.get("unit_id")), "incarnation": _id(raw.get("incarnation")),
            "carrier_id": _id(raw.get("carrier_id")), "point": _point(raw.get("point")),
            "velocity": _velocity(raw.get("velocity")), "heading": _num(raw.get("heading_deg"), 0, 360) % 360,
            "in_air": _bool(raw.get("in_air")), "on_deck": _bool(raw.get("on_deck")),
            "events": sorted(events, key=lambda e: e["id"])}


def _record_id(raw, key):
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _entities(raw_list, limit, key, parse, kind, diagnostics):
    """Parse each record on its own; an invalid record is rejected alone, never the whole snapshot.

    Returns (valid entries by id, ids of rejected records).  Duplicate ids stay a whole-payload
    error because they make every record of that id ambiguous.
    """
    valid, rejected, seen = OrderedDict(), set(), set()
    for index, raw in enumerate(_list(raw_list, limit)):
        rid = _record_id(raw, key)
        if rid is not None:
            if rid in seen:
                raise ValueError(f"duplicate {kind} id")
            seen.add(rid)
        try:
            entry = parse(raw)
        except ValueError as error:
            if rid is not None:
                rejected.add(rid)
            diagnostics.append(f"{kind} {rid if rid is not None else '#' + str(index + 1)} rejected: {error}")
            continue
        if entry is not None:
            valid[rid] = entry
    return valid, rejected


def _snapshot(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("unsupported carrier bridge payload")
    date = payload.get("date")
    if not isinstance(date, dict):
        raise ValueError("invalid mission date")
    try:
        day = _dt.datetime(_num(date.get("year"), 1900, 2200, integer=True),
                           _num(date.get("month"), 1, 12, integer=True),
                           _num(date.get("day"), 1, 31, integer=True), tzinfo=_dt.timezone.utc)
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid mission date") from error
    abs_time = _num(payload.get("abs_time"), 0, 86400)
    if abs_time >= 86400:
        raise ValueError("invalid mission time of day")
    diagnostics = []
    error = payload.get("error")
    try:
        error = None if error is None else _text(error, 240)
    except ValueError:              # informational only; never worth losing every carrier over
        error = None
        diagnostics.append("mission error text rejected: invalid bridge text")
    carriers, _ = _entities(payload.get("carriers"), MAX_CARRIERS, "id", _carrier, "carrier", diagnostics)
    players, _ = _entities(payload.get("players"), MAX_PLAYERS, "unit_id", _player, "player", diagnostics)
    aircraft, rejected_aircraft = _entities(payload.get("aircraft"), MAX_AIRCRAFT, "unit_id", _aircraft,
                                            "aircraft", diagnostics)
    if len(diagnostics) > MAX_DIAGNOSTICS:
        diagnostics[MAX_DIAGNOSTICS:] = [f"{len(diagnostics) - MAX_DIAGNOSTICS} more records rejected"]
    return {"epoch": _id(payload.get("epoch")), "seq": _num(payload.get("seq"), 1, 2 ** 53, integer=True),
            "time": _num(payload.get("time"), 0, 1e9), "abs_time": abs_time,
            "when": day + _dt.timedelta(seconds=abs_time),
            "error": error, "carriers": carriers,
            "players": players, "aircraft": aircraft, "rejected_aircraft": rejected_aircraft,
            "diagnostics": diagnostics}


def parse_channel(value):
    """Canonical ``coalition:frequency_hz:modulation`` (same rules as the brain), else ValueError."""
    if not isinstance(value, str):
        raise ValueError("radio channel required")
    parts = value.split(":")
    if len(parts) != 3 or not all(part.isdecimal() and len(part) <= 12 for part in parts):
        raise ValueError("expected coalition:frequency:modulation radio channel")
    coalition, frequency, modulation = map(int, parts)
    if coalition not in (1, 2) or not 1000000 <= frequency <= 2000000000 or modulation not in (0, 1):
        raise ValueError("invalid coalition/frequency/modulation")
    return f"{coalition}:{frequency}:{modulation}"


# ---------------------------------------------------------------------------
# Phraseology
# ---------------------------------------------------------------------------

def _digits(text):
    return " ".join(domain._ONES[int(c)] for c in text)


def _bearing(value):
    return _digits(f"{(value % 360) or 360:03d}")


def _altimeter(value):
    return _digits(f"{value:.2f}".replace(".", ""))


def _angels(value):
    return domain.number_to_words(value)


def _spoken_name(name):
    words = re.findall(r"[A-Za-z]{3,}", name)
    words = [w for w in words if w.lower() not in ("cvn", "uss", "lha", "lhd", "carrier", "ship")] or words
    return words[-1].capitalize() if words else "Mother"


def _number_matches(heard, expected, kind):
    try:
        value = Decimal(heard)
    except InvalidOperation:
        return False
    if kind == "heading":
        return value >= 0 and value == value.to_integral_value() and value % 360 == Decimal(expected) % 360
    if kind == "altimeter":
        exact = Decimal(f"{expected:.2f}")
        return value == exact or heard == f"{expected:.2f}".replace(".", "")
    return value == Decimal(expected)


def _readback_fields(assign):
    if assign["case"] == 1:
        return [("angels", assign["angels"], "int"), ("altimeter", assign["altimeter"], "altimeter")]
    return [("radial", assign["radial"], "heading"), ("DME", assign["dme"], "int"),
            ("angels", assign["angels"], "int"), ("approach time", assign["eat_minute"], "int"),
            ("altimeter", assign["altimeter"], "altimeter")]


def _readback_mismatch(text, assign):
    """First field not reproduced as a complete number, in order, with nothing extra."""
    heard = _readback_runs(_READBACK_LABELS.sub(" line ", text or ""))
    fields = _readback_fields(assign)
    for index, (label, value, kind) in enumerate(fields):
        if index >= len(heard) or heard[index][0] != "num" or not _number_matches(heard[index][1], value, kind):
            return label, value, kind
    if len(heard) != len(fields):
        return fields[-1]
    return None


def _say_field(label, value, kind):
    if kind == "heading":
        return f"{_bearing(value)} radial"
    if kind == "altimeter":
        return f"altimeter {_altimeter(value)}"
    if label == "DME":
        return f"{_digits(str(value))} DME"
    if label == "approach time":
        return f"expected approach time {_digits(f'{value:02d}')}"
    return f"angels {_angels(value)}"


def _known_groups(players):
    return domain._known_groups([{"pilot": p["player_name"], "callsign": p["callsign"]} for p in players])


class _Session:
    __slots__ = ("unit_id", "incarnation", "carrier_id", "coalition", "caller", "kind", "phase", "revision",
                 "issued_seq", "track", "seen", "seen_order", "priority", "order", "assign", "granted",
                 "ready", "deck_seen", "airborne_seen")

    def __init__(self, player, carrier_id, caller, kind, revision, order):
        self.unit_id, self.incarnation = player["unit_id"], player["incarnation"]
        self.carrier_id, self.coalition, self.caller, self.kind = carrier_id, player["coalition"], caller, kind
        self.phase = "checked_in" if kind == "recovery" else "queued"
        self.revision, self.order = revision, order
        self.issued_seq = None          # seq of the first accepted response that listed this watch
        self.track = None               # latest mission report (A record)
        self.seen, self.seen_order = set(), deque()
        self.priority = self.granted = self.ready = self.deck_seen = False
        self.airborne_seen = False      # tracked airborne, or an own-deck landing, since enrolment
        self.assign = None              # marshal assignment (recovery)


class CarrierService:
    """Independent carrier radios and per-ship traffic, synchronised with the mission bridge."""

    def __init__(self, *, reserved_channels=(), stale_seconds=10):
        self._reserved = []
        for channel in reserved_channels or ():
            if channel:
                match = _CHANNEL.fullmatch(parse_channel(channel))
                self._reserved.append((channel, int(match.group(1)), int(match.group(2)), int(match.group(3))))
        if not 0 < stale_seconds <= 300:
            raise ValueError("stale_seconds must be within 0-300")
        self.stale_seconds = stale_seconds
        self._lock = threading.RLock()
        self._revisions = itertools.count(1)
        self._orders = itertools.count(1)
        self._retired = OrderedDict()
        self._epoch, self._seq, self._received = None, 0, None
        self._reset_epoch(None)

    def _reset_epoch(self, epoch):
        self._epoch, self._seq, self._received = epoch, 0, None
        self._suspended = False
        self._carriers, self._players, self._error, self._diagnostics = OrderedDict(), {}, None, []
        self._time, self._abs_time, self._when = 0.0, 0.0, None
        self._sessions = {}
        self._lanes, self._notes = {}, {}
        self._generations = {}

    # -- freshness ---------------------------------------------------------

    @property
    def ready(self):
        with self._lock:
            return self._fresh()

    def _fresh(self):
        return self._received is not None and time.monotonic() - self._received <= self.stale_seconds

    def _refresh(self):
        """Stale bridge: suspend positive control once; every outstanding reply becomes invalid."""
        if self._received is None or self._fresh() or self._suspended:
            return
        self._suspended = True
        for session in self._sessions.values():
            if session.kind == "departure" and session.phase == "released":
                session.phase = "queued"
                self._free_lane(session)
            elif session.granted and session.phase == "established":
                session.granted = False
                self._free_lane(session)
            session.issued_seq = None     # tracking re-handshakes after the link returns
            self._bump(session)

    # -- bridge ------------------------------------------------------------

    def exchange(self, payload):
        snap = _snapshot(payload)
        # Pure precompute: anything that can fail runs before freshness or sequence state is committed.
        self._classify(snap["carriers"], snap["when"])
        epoch, seq = snap["epoch"], snap["seq"]
        with self._lock:
            if epoch in self._retired:
                return self._rejected("retired epoch", epoch, seq)
            if epoch != self._epoch:
                if self._epoch is not None:
                    self._retired[self._epoch] = True
                    while len(self._retired) > MAX_RETIRED:
                        self._retired.popitem(last=False)
                self._reset_epoch(epoch)
            elif seq <= self._seq:
                return self._rejected("stale sequence", epoch, seq)
            self._refresh()
            self._seq, self._received, self._suspended = seq, time.monotonic(), False
            self._apply(snap)
            watch = []
            for session in self._sessions.values():
                if session.issued_seq is None:
                    session.issued_seq = seq
                watch.append({"unit_id": session.unit_id, "incarnation": session.incarnation,
                              "carrier_id": session.carrier_id})
            return {"version": 1, "accepted": True, "epoch": epoch, "seq": seq, "watch": watch}

    @staticmethod
    def _rejected(reason, epoch, seq):
        return {"version": 1, "accepted": False, "error": reason, "epoch": epoch, "seq": seq, "watch": []}

    def _classify(self, carriers, when):
        for entry in carriers.values():
            entry["spoken"] = _spoken_name(entry["name"])
            entry["names"] = carrier_parser.name_words(entry["name"])
            frequency = entry["frequency_hz"]
            entry["channel"] = None if frequency is None else \
                f"{entry['coalition']}:{frequency}:{entry['modulation']}"
            entry["error"] = None
            if frequency is None:
                entry["error"] = "no valid mission radio frequency"
            elif not entry["alive"]:
                entry["error"] = "carrier not alive"
            else:
                for channel, _, reserved, _ in self._reserved:
                    if abs(reserved - frequency) < FREQUENCY_GUARD_HZ:
                        entry["error"] = f"frequency within {FREQUENCY_GUARD_HZ} Hz of reserved channel {channel}"
                        break
        live = [e for e in carriers.values() if e["alive"] and e["frequency_hz"] is not None]
        for entry in live:
            clashes = [other["id"] for other in live if other is not entry
                       and abs(other["frequency_hz"] - entry["frequency_hz"]) < FREQUENCY_GUARD_HZ]
            if clashes and entry["error"] is None:
                entry["error"] = f"frequency within {FREQUENCY_GUARD_HZ} Hz of carrier " + ", ".join(clashes)
        for entry in carriers.values():
            entry["enabled"] = entry["error"] is None
            geometry = entry["alive"] and entry["heading"] is not None
            entry["brc"] = self._magnetic(entry, entry["heading"], when) if geometry else None
            final_true = (entry["heading"] - entry["deck_angle"]) % 360 \
                if geometry and entry["deck_angle"] is not None else None
            entry["final_true"] = final_true
            entry["final"] = None if final_true is None else self._magnetic(entry, final_true, when)

    @staticmethod
    def _magnetic(carrier, true_bearing, when):
        return round(domain.true_to_magnetic(carrier["lat"], carrier["lon"], true_bearing, when)) % 360

    def _apply(self, snap):
        self._time, self._abs_time, self._when = snap["time"], snap["abs_time"], snap["when"]
        self._error, self._diagnostics = snap["error"], snap["diagnostics"]
        previous = self._carriers
        carriers = snap["carriers"]
        self._carriers, self._players = carriers, snap["players"]
        dropped = set()
        for cid in list(self._lanes):
            old, new = previous.get(cid), carriers.get(cid)
            if not new or not new["enabled"] or (old and old["channel"] != new["channel"]):
                dropped.add(cid)
                self._lanes.pop(cid)
                self._notes.pop(cid, None)
        for cid, entry in carriers.items():
            if entry["enabled"]:
                self._lanes.setdefault(cid, None)
                self._notes.setdefault(cid, deque(maxlen=MAX_NOTIFICATIONS))
        for session in list(self._sessions.values()):
            player = self._players.get(session.unit_id)
            if session.carrier_id in dropped or session.carrier_id not in self._lanes or player is None \
                    or player["incarnation"] != session.incarnation or player["coalition"] != session.coalition:
                self._release(session)
        self._generations = {uid: gen for uid, gen in self._generations.items()
                             if uid in self._players or uid in self._sessions}
        for uid in snap["rejected_aircraft"]:
            session = self._sessions.get(uid)
            if session is not None:
                # An unusable sample says nothing trustworthy about this aircraft: fail closed for it alone.
                self._release(session, ("marshal", "tracking data invalid, carrier session closed, check in again"))
        reported = set()
        for uid, record in snap["aircraft"].items():
            session = self._sessions.get(uid)
            if session and record["incarnation"] == session.incarnation \
                    and record["carrier_id"] == session.carrier_id:
                reported.add(uid)
                self._observe(session, record)
        for session in list(self._sessions.values()):
            if session.unit_id not in reported and session.issued_seq is not None \
                    and snap["seq"] > session.issued_seq + 1:
                self._release(session, ("marshal", "radar contact lost, carrier session closed, check in again"))
        for cid in self._lanes:
            self._schedule(self._carriers[cid])

    # -- lifecycle ---------------------------------------------------------

    def _bump(self, session):
        session.revision = next(self._revisions)

    def _free_lane(self, session):
        if self._lanes.get(session.carrier_id) == session.unit_id:
            self._lanes[session.carrier_id] = None

    def _release(self, session, notice=None):
        if self._sessions.get(session.unit_id) is not session:
            return
        del self._sessions[session.unit_id]
        self._free_lane(session)
        self._bump(session)
        if notice and session.carrier_id in self._notes:
            self._push(session, notice[0], notice[1],
                       self._notice(session.carrier_id, session.unit_id, session.incarnation))

    def release_unit(self, unit_id):
        """Transfer a human to another controller, revoking queued state and released-session notices."""
        with self._lock:
            session = self._sessions.get(unit_id)
            if session is not None:
                self._release(session)
            if unit_id in self._generations:
                self._generations[unit_id] = next(self._orders)
            if session is not None:
                carrier = self._carriers.get(session.carrier_id)
                if carrier is not None:
                    self._schedule(carrier)
            return session is not None

    def _observe(self, session, record):
        carrier = self._carriers[session.carrier_id]
        for event in record["events"]:
            if event["id"] in session.seen:
                continue
            session.seen.add(event["id"])
            session.seen_order.append(event["id"])
            if len(session.seen_order) > MAX_SEEN_EVENTS:
                session.seen.discard(session.seen_order.popleft())
            # A landing counts only when the mission names this ship; a null place is an airfield or
            # unknown deck.  A null takeoff place is accepted only from a deck already proven to be ours.
            kind, place = event["type"], event["carrier_id"]
            foreign = place is not None and place != session.carrier_id
            if kind in TERMINAL_EVENTS:
                self._release(session)
                return
            if kind == "land":
                if session.kind == "recovery" and session.phase in AIRBORNE_RECOVERY:
                    if place != session.carrier_id:
                        self._release(session, ("marshal", "landing not on this deck, recovery closed"))
                        return
                    session.phase, session.airborne_seen = "touchdown", True
                    self._bump(session)
                    self._deck_fouled(carrier, session)
                elif session.kind == "departure" and session.phase == "airborne":
                    self._release(session)
                    return
            elif session.kind == "recovery":
                if foreign:
                    self._release(session)
                    return
                if session.phase == "touchdown":
                    self._bolter_observed(session)
                elif session.phase == "on_deck":
                    self._release(session, ("tower", "airborne, recovery session closed"))
                    return
            elif session.phase in PRE_LAUNCH:
                if foreign or not session.deck_seen:
                    self._release(session, ("departure", "launch not observed from this deck, departure closed"))
                    return
                self._launch(session, carrier)
        session.track = record
        if record["in_air"]:
            session.airborne_seen = True
        if session.kind == "recovery":
            if session.phase == "touchdown":
                if record["in_air"]:
                    self._bolter_observed(session)
                elif record["on_deck"]:
                    self._trap(session)
            elif session.phase in AIRBORNE_RECOVERY and not record["in_air"]:
                # The mission confirms on_deck only after seconds stopped, so a delayed flag on an aircraft
                # never seen airborne (nor landing here) since enrolment is a parked aircraft, not a trap.
                if not session.airborne_seen:
                    self._release(session, ("marshal", "you are not airborne, recovery not required, "
                                                       "check in when airborne"))
                elif record["on_deck"]:
                    self._trap(session)
        else:
            if record["on_deck"] and not record["in_air"]:
                session.deck_seen = True
            if session.phase in PRE_LAUNCH and record["in_air"]:
                if session.deck_seen:
                    self._launch(session, carrier)
                else:
                    self._release(session, ("departure", "unable departure, aircraft not on deck"))
            elif session.phase == "airborne" and carrier["point"] is not None \
                    and self._relative(carrier, record)[0] >= HANDOFF_NM:
                self._release(session, ("departure", "departure complete, frequency change approved"))

    def _recycle(self, session):
        """Bolter/wave-off: the old landing clearance ends; returns the instruction for the new phase."""
        self._bump(session)
        if not session.granted:
            # Touched down without the lane: back to holding, never into the pattern.
            if session.assign is None:
                session.phase = "checked_in"
                return "climb straight ahead, check in with marshal"
            session.phase = "marshal"
            return f"climb straight ahead, return to marshal, {self._holding(session)}, report established"
        if session.assign["case"] == 3:
            session.phase = "platform"
            return "climb straight ahead to one thousand two hundred feet, report ten miles"
        session.phase = "break"
        return "climb straight ahead, rejoin the pattern, report abeam"

    def _bolter_observed(self, session):
        self._event(session, "paddles", "bolter, bolter, " + self._recycle(session))

    def _trap(self, session):
        session.phase, session.assign, session.granted, session.priority = "on_deck", None, False, False
        self._bump(session)
        self._event(session, "tower", "trap confirmed, clear the landing area, report deck clear")
        self._deck_fouled(self._carriers[session.carrier_id], session)

    def _deck_fouled(self, carrier, by):
        """An enrolled aircraft is on this deck: the ball clearance of anyone else ends at once."""
        for other in self._on(carrier):
            if other is not by and other.kind == "recovery" and other.phase == "ball":
                self._event(other, "paddles", "wave off, wave off, foul deck, " + self._recycle(other))

    def _launch(self, session, carrier):
        cleared = session.phase == "released"
        self._free_lane(session)        # only ever this aircraft's own lane
        session.phase = "airborne"
        self._bump(session)
        if cleared:
            self._event(session, "departure", self._airborne_text(carrier))
        else:
            self._event(session, "departure", "launch not cleared, airborne observed, report departure complete")

    @staticmethod
    def _airborne_text(carrier):
        case = carrier["case"]
        return (f"airborne, CASE {domain.number_to_words(case)} departure, report departure complete"
                if case else "airborne, report departure complete")

    # -- scheduling --------------------------------------------------------

    def _on(self, carrier):
        return [s for s in self._sessions.values() if s.carrier_id == carrier["id"]]

    @staticmethod
    def _queue_key(session):
        return (not session.priority, session.order)

    def _rank(self, session, carrier):
        if session.kind == "recovery":
            queue = [s for s in self._on(carrier) if s.kind == "recovery" and s.phase in HOLDING and not s.granted]
        else:
            queue = [s for s in self._on(carrier) if s.kind == "departure" and s.phase in ("startup", "queued")]
        queue.sort(key=self._queue_key)
        return queue.index(session) + 1 if session in queue else 1

    def _missing(self, carrier, kind):
        if carrier["case"] is None:
            return f"{kind} case not available"
        if carrier["altimeter"] is None:
            return "altimeter not available"
        if carrier["brc"] is None:
            return "carrier heading not available"
        if kind == "recovery" and carrier["case"] > 1 and carrier["final"] is None:
            return "deck geometry not available"
        return None

    def _assign(self, session, carrier):
        """Marshal assignment from current mission facts; returns the reason it is impossible, or None."""
        reason = self._missing(carrier, "recovery")
        if reason:
            return reason
        case = carrier["case"]
        others = [s.assign for s in self._on(carrier) if s is not session and s.assign and s.assign["case"] == case]
        used = {a["angels"] for a in others}
        angels = next((a for a in (CASE_ONE_ANGELS if case == 1 else MARSHAL_ANGELS) if a not in used), None)
        if angels is None:
            return "marshal stack full"
        assign = {"case": case, "angels": angels, "altimeter": carrier["altimeter"], "brc": carrier["brc"]}
        if case > 1:
            # Approach times compare on monotonic mission model time; the time of day wraps at midnight.
            first = self._time + math.ceil((self._abs_time + FIRST_APPROACH_DELAY) / 60) * 60 - self._abs_time
            eat_model = max([first] + [a["eat_model"] + APPROACH_INTERVAL for a in others])
            assign.update(final=carrier["final"], radial=(carrier["final"] + 180) % 360,
                          radial_true=(carrier["final_true"] + 180) % 360, dme=angels + DME_OFFSET,
                          eat_model=eat_model,
                          eat_minute=int((self._abs_time + eat_model - self._time) // 60) % 60)
        session.assign, session.phase, session.granted = assign, "assigned", False
        self._bump(session)
        return None

    def _assignment_text(self, session, carrier):
        a = session.assign
        if a["case"] == 1:
            # CASE I holds overhead: the BRC is information, always the ship's current one.
            brc = carrier["brc"] if carrier["brc"] is not None else a["brc"]
            return (f"CASE one recovery, BRC {_bearing(brc)}, hold overhead at angels {_angels(a['angels'])}, "
                    f"altimeter {_altimeter(a['altimeter'])}, read back angels and altimeter")
        return (f"CASE {domain.number_to_words(a['case'])} recovery, final bearing {_bearing(a['final'])}, "
                f"{self._holding(session)}, altimeter {_altimeter(a['altimeter'])}, "
                "read back radial, DME, angels, approach time and altimeter")

    def _holding(self, session):
        a = session.assign
        if a["case"] == 1:
            return f"hold overhead at angels {_angels(a['angels'])}"
        minute = f"{a['eat_minute']:02d}"
        return (f"marshal on the {_bearing(a['radial'])} radial, {_digits(str(a['dme']))} DME, "
                f"angels {_angels(a['angels'])}, expected approach time {_digits(minute)}")

    def _grant_text(self, session, carrier):
        if session.kind == "departure":
            return (f"cleared for launch, CASE {domain.number_to_words(carrier['case'])} departure, "
                    f"BRC {_bearing(carrier['brc'])}, altimeter {_altimeter(carrier['altimeter'])}, report airborne")
        if session.assign["case"] == 1:
            return f"Charlie, BRC {_bearing(carrier['brc'])}, report commencing"
        return f"commence, final bearing {_bearing(session.assign['final'])}, report commencing"

    def _foul_deck(self, carrier, session=None):
        """An enrolled aircraft touched down here (confirmed or not yet stopped) fouls the deck."""
        return any(s.phase in ("touchdown", "on_deck") and s is not session for s in self._on(carrier))

    @staticmethod
    def _facts_changed(session, carrier):
        """CASE or the spoken recovery course changed since this assignment."""
        a = session.assign
        course = "brc" if a["case"] == 1 else "final"
        return (carrier["case"] != a["case"] or carrier[course] is None
                or domain.diff_heading(a[course], carrier[course]) >= REASSIGN_DEG)

    def _schedule(self, carrier, speaker=None):
        """Advance holding/queued traffic of one ship; returns the speaker's own instruction, if any."""
        if not self._fresh() or carrier["id"] not in self._lanes or not carrier["alive"]:
            return None
        spoken = None

        def tell(session, role, message):
            nonlocal spoken
            if session is speaker:
                spoken = message if spoken is None else f"{spoken}, {message}"
            else:
                self._event(session, role, message)

        for session in sorted((s for s in self._on(carrier) if s.kind == "recovery" and s.assign is not None
                               and s.phase in AIRBORNE_RECOVERY), key=self._queue_key):
            if not self._facts_changed(session, carrier):
                continue
            # Every clearance built on the old facts ends, whether holding, cleared or already flying it.
            lead = ("wave off, wave off, recovery instructions changed, climb straight ahead, "
                    if session.phase in APPROACH else "clearance cancelled, " if session.granted else "")
            self._free_lane(session)
            session.granted, session.assign = False, None
            if carrier["case"] is not None and self._assign(session, carrier) is None:
                tell(session, "marshal", lead + "new instructions, " + self._assignment_text(session, carrier))
            else:
                session.phase = "checked_in"
                self._bump(session)
                reason = ("recovery case no longer available" if carrier["case"] is None
                          else "recovery instructions cancelled")
                tell(session, "marshal", f"{lead}{reason}, hold present position, stand by")
        for session in sorted((s for s in self._on(carrier) if s.kind == "recovery" and s.assign is None
                               and s.phase == "checked_in"), key=self._queue_key):
            if self._assign(session, carrier) is None:
                tell(session, "marshal", self._assignment_text(session, carrier))
        if self._lanes[carrier["id"]] is None:
            for session in sorted(self._on(carrier), key=self._queue_key):
                if self._eligible(session, carrier):
                    self._lanes[carrier["id"]] = session.unit_id
                    if session.kind == "departure":
                        session.phase = "released"
                    else:
                        session.granted = True
                    self._bump(session)
                    tell(session, "departure" if session.kind == "departure" else "marshal",
                         self._grant_text(session, carrier))
                    break
        return spoken

    def _eligible(self, session, carrier):
        track = session.track
        if session.kind == "recovery":
            return (session.phase == "established" and not session.granted and not self._foul_deck(carrier)
                    and (session.assign["case"] == 1 or session.priority or self._time >= session.assign["eat_model"]))
        return (session.phase == "queued" and session.ready and session.deck_seen and track is not None
                and track["on_deck"] and not track["in_air"] and self._missing(carrier, "departure") is None)

    # -- notifications and validation -----------------------------------------

    def _validation(self, session):
        carrier = self._carriers[session.carrier_id]
        return {"role": "carrier", "kind": "state", "epoch": self._epoch, "carrier_id": carrier["id"],
                "channel": carrier["channel"], "coalition": carrier["coalition"], "unit_id": session.unit_id,
                "incarnation": session.incarnation, "revision": session.revision, "generation": None,
                "expires": time.time() + REPLY_TTL}

    def _notice(self, carrier_id, unit_id, incarnation=None):
        """Radio-bound reply; when addressed to an aircraft, bound to that occupant's enrolment and incarnation."""
        carrier = self._carriers[carrier_id]
        generation = None if unit_id is None else self._generations.setdefault(unit_id, 0)
        return {"role": "carrier", "kind": "notice", "epoch": self._epoch, "carrier_id": carrier_id,
                "channel": carrier["channel"], "coalition": carrier["coalition"], "unit_id": unit_id,
                "incarnation": incarnation, "revision": None, "generation": generation,
                "expires": time.time() + REPLY_TTL}

    def _speech(self, caller, carrier, role, message):
        reply = domain._speech(caller, f"{carrier['spoken']} {role}", message)
        if len(reply) > MAX_REPLY_CHARS:
            reply = domain._speech("last transmitter", f"{carrier['spoken']} {role}", "say again")
        return reply

    def _push(self, session, role, message, validation):
        carrier = self._carriers[session.carrier_id]
        validation["expires"] = time.time() + EVENT_TTL
        self._notes[session.carrier_id].append(
            {"reply": self._speech(session.caller, carrier, role, message), "validation": validation})

    def _event(self, session, role, message):
        self._push(session, role, message, self._validation(session))

    def validate_reply(self, validation):
        """True while a synthesised carrier reply still matches the mission and procedure state."""
        if not isinstance(validation, dict) or validation.get("role") != "carrier":
            return False
        with self._lock:
            self._refresh()
            carrier_id = validation.get("carrier_id")
            carrier = self._carriers.get(carrier_id) if isinstance(carrier_id, str) else None
            expires = validation.get("expires")
            if carrier is None or not carrier["enabled"] or validation.get("epoch") != self._epoch \
                    or validation.get("channel") != carrier["channel"] \
                    or validation.get("coalition") != carrier["coalition"] \
                    or isinstance(expires, bool) or not isinstance(expires, (int, float)) \
                    or not expires > time.time():
                return False
            unit_id = validation.get("unit_id")
            if validation.get("kind") == "notice":
                if unit_id is None:
                    return True
                generation, incarnation = validation.get("generation"), validation.get("incarnation")
                player = self._players.get(unit_id) if isinstance(unit_id, str) else None
                # The original occupant must still be in that seat: no notice reaches a respawn or new pilot.
                return (player is not None and isinstance(incarnation, str)
                        and player["incarnation"] == incarnation
                        and isinstance(generation, int) and not isinstance(generation, bool)
                        and self._generations.get(unit_id) == generation)
            if validation.get("kind") != "state" or not self._fresh() or not isinstance(unit_id, str):
                return False
            session = self._sessions.get(unit_id)
            revision = validation.get("revision")
            return (session is not None and session.carrier_id == carrier_id
                    and session.incarnation == validation.get("incarnation")
                    and isinstance(revision, int) and not isinstance(revision, bool)
                    and session.revision == revision)

    def take_events(self, request):
        """Pending notifications for one carrier radio, each still valid now."""
        coalition, channel, carrier_id = self._radio(request)
        with self._lock:
            self._refresh()
            carrier, _ = self._resolve(coalition, channel, carrier_id)
            if carrier is None:
                return {"events": [], "vocabulary": []}
            notes = self._notes.get(carrier["id"], deque())
            self._notes[carrier["id"]] = deque(maxlen=MAX_NOTIFICATIONS)
            now = time.time()
            events = [dict(note["validation"], reply=note["reply"], ttl_seconds=note["validation"]["expires"] - now)
                      for note in notes if self.validate_reply(note["validation"])]
            callsigns = sorted({p["callsign"] for p in self._players.values()
                                if p["coalition"] == carrier["coalition"] and p["callsign"]})[:32]
            return {"events": events, "vocabulary": [carrier["spoken"]] + VOCABULARY + callsigns}

    # -- registry and routing --------------------------------------------------

    def registry(self):
        with self._lock:
            self._refresh()
            carriers = [{key: entry[key] for key in ("id", "name", "type", "coalition", "frequency_hz",
                                                     "modulation", "channel", "enabled", "error")}
                        for entry in self._carriers.values()]
            errors = ([self._error] if self._error else []) + [
                f"carrier {entry['id']} {entry['name']}: {entry['error']}"
                for entry in self._carriers.values() if entry["error"]] + list(self._diagnostics)
            return {"ready": self._fresh(), "epoch": self._epoch, "carriers": carriers, "errors": errors}


    @staticmethod
    def _radio(request):
        if not isinstance(request, dict):
            raise ValueError("invalid carrier request")
        coalition = request.get("coalition", 2)
        if isinstance(coalition, bool) or not isinstance(coalition, (int, str)) or str(coalition) not in ("1", "2"):
            raise ValueError("invalid coalition")
        carrier_id = request.get("carrier_id")
        if carrier_id is not None:
            _id(carrier_id)
        return int(coalition), parse_channel(request.get("channel")), carrier_id

    def _resolve(self, coalition, channel, carrier_id):
        if carrier_id is not None:
            carrier = self._carriers.get(carrier_id)
            if carrier is None or not carrier["enabled"]:
                return None, "Carrier radio unavailable"
            if carrier["channel"] != channel or carrier["coalition"] != coalition:
                return None, "Carrier does not own this channel and coalition"
            return carrier, None
        matches = [c for c in self._carriers.values() if c["enabled"] and c["channel"] == channel]
        if len(matches) != 1 or matches[0]["coalition"] != coalition:
            return None, "No unique carrier radio on this channel"
        return matches[0], None

    # -- radio ---------------------------------------------------------------

    def respond(self, request):
        """Answer one transmission heard on a carrier radio; no other controller is involved."""
        coalition, channel, carrier_id = self._radio(request)
        text, sender = request.get("text"), request.get("sender_callsign")
        if not isinstance(text, str) or len(text) > 2048:
            raise ValueError("invalid text")
        if sender is not None and (not isinstance(sender, str) or len(sender) > 128):
            raise ValueError("invalid sender callsign")
        with self._lock:
            self._refresh()
            # One freshness decision per transmission: a positive reply is always state-bound, and the
            # validator re-checks actual freshness before playback.
            fresh = self._fresh()
            carrier, problem = self._resolve(coalition, channel, carrier_id)
            if carrier is None:
                return {"reply": None, "parsed": None, "notes": problem}
            parsed = carrier_parser.parse(text, carrier["names"], _known_groups(self._players.values()))
            if parsed is None:
                return {"reply": None, "parsed": None, "notes": "Transmission is not addressed to this carrier"}
            answer = {"parsed": parsed, "notes": None}
            player, caller, problem = identify_player(self._players.values(), parsed, coalition)
            intent = parsed["intent"]
            if player is None:
                message = "say again" if intent == "None" and caller == "last transmitter" else problem
                answer.update(reply=self._speech(caller, carrier, "marshal", message), notes=problem,
                              validation=self._notice(carrier["id"], None))
                return answer
            uid = player["unit_id"]
            if sender is not None and not sender_identifies(self._players.values(), sender, player):
                answer.update(reply=self._speech(caller, carrier, "marshal",
                                                 "unable, your radio name does not identify you"),
                              notes="SRS radio name does not uniquely identify the spoken caller",
                              validation=self._notice(carrier["id"], uid, player["incarnation"]))
                return answer
            session = self._sessions.get(uid)
            if session and session.incarnation != player["incarnation"]:
                self._release(session)
                session = None
            if not fresh and intent not in ("cancel", "checkOut"):
                role, message = "marshal", "unable, no live carrier link"
                answer["notes"] = "Carrier bridge snapshot missing or stale"
            elif session and session.carrier_id != carrier["id"] \
                    and intent not in ("checkIn", "departure", "emergency"):
                other = self._carriers.get(session.carrier_id)
                role, message = "marshal", f"unable, you are checked in with {other['spoken'] if other else 'another carrier'}"
                session = None
            else:
                role, message = self._handle(intent, parsed, player, caller, carrier, session)
                session = self._sessions.get(uid)
                if session is not None and session.carrier_id == carrier["id"]:
                    extra = self._schedule(carrier, speaker=session)
                    if extra:
                        message = f"{message}, {extra}"
                else:
                    self._schedule(carrier)
                    session = None
            answer["reply"] = self._speech(caller, carrier, role, message)
            if session is not None and session.carrier_id == carrier["id"] and fresh:
                answer["validation"] = self._validation(session)
            else:
                answer["validation"] = self._notice(carrier["id"], uid, player["incarnation"])
            return answer

    def _register(self, player, caller, carrier, kind):
        current = self._sessions.get(player["unit_id"])
        if current is None and len(self._sessions) >= MAX_SESSIONS:
            return None
        if current is not None:
            self._release(current)
        uid = player["unit_id"]
        session = _Session(player, carrier["id"], caller, kind, next(self._revisions), next(self._orders))
        self._generations[uid] = session.order
        self._sessions[uid] = session
        return session

    def _handle(self, intent, parsed, player, caller, carrier, session):
        own = session if session is not None and session.carrier_id == carrier["id"] else None
        if intent in ("cancel", "checkOut"):
            if own is None:
                return "marshal", "copy, no active carrier session"
            self._release(own)
            if intent == "checkOut":
                return "marshal", "copy, checked out"
            return ("marshal" if own.kind == "recovery" else "departure"), f"copy, {own.kind} cancelled, checked out"
        if intent == "checkIn":
            if own is not None and own.kind == "recovery":
                return "marshal", self._status(own, carrier)
            return self._enrol_recovery(player, caller, carrier, priority=False)
        if intent == "emergency":
            if own is None:
                return self._enrol_recovery(player, caller, carrier, priority=True)
            own.priority = True
            self._bump(own)
            return "marshal", "copy emergency, you have priority, " + self._status(own, carrier)
        if intent == "departure":
            return self._departure(parsed, player, caller, carrier, own)
        if intent == "None":
            return "marshal", "say again"
        if own is None:
            return "marshal", "unable, check in first"
        if intent == "sayAgain":
            return self._role(own), self._status(own, carrier)
        return getattr(self, "_" + intent)(own, parsed, carrier)

    @staticmethod
    def _role(session):
        if session.kind == "departure":
            return "departure"
        if session.phase in ("downwind", "final", "ball", "touchdown"):
            return "paddles"
        if session.phase == "on_deck":
            return "tower"
        return "marshal"

    def _enrol_recovery(self, player, caller, carrier, priority):
        session = self._register(player, caller, carrier, "recovery")
        if session is None:
            return "marshal", "unable, carrier traffic limit reached"
        session.priority = priority
        reason = self._assign(session, carrier)
        lead = "copy emergency, you have priority, " if priority else ""
        if reason:
            return "marshal", f"{lead}checked in, {reason}, stand by"
        return "marshal", lead + self._assignment_text(session, carrier)

    def _departure(self, parsed, player, caller, carrier, own):
        ready = not parsed["startup"]
        if own is not None and own.kind == "departure":
            if ready and not own.ready and own.phase == "startup":
                own.ready, own.phase = True, "queued"
                self._bump(own)
            return "departure", self._status(own, carrier)
        if own is not None and own.kind == "recovery" and own.phase != "on_deck" \
                and own.track is not None and own.track["in_air"]:
            return "marshal", "unable, departure requires the aircraft on deck"
        on_deck = own is not None and own.phase == "on_deck"
        session = self._register(player, caller, carrier, "departure")
        if session is None:
            return "departure", "unable, carrier traffic limit reached"
        session.ready = ready
        session.phase = "queued" if ready else "startup"
        if on_deck:
            session.deck_seen = True
            session.track = own.track
        return "departure", self._status(session, carrier)

    def _status(self, session, carrier):
        """Current instruction rendered from present state; never replays a revoked clearance."""
        phase, a = session.phase, session.assign
        if session.kind == "departure":
            if phase == "startup":
                return "copy, report ready for launch"
            if phase == "queued":
                reason = self._missing(carrier, "departure")
                lead = f"departure, {reason}" if reason else (
                    f"CASE {domain.number_to_words(carrier['case'])} departure")
                return f"{lead}, number {domain.number_to_words(self._rank(session, carrier))} for launch, hold"
            if phase == "released":
                return self._grant_text(session, carrier)
            return self._airborne_text(carrier)
        if phase == "checked_in":
            reason = self._missing(carrier, "recovery") or "marshal stack full"
            return f"checked in, {reason}, stand by"
        if phase == "assigned":
            return self._assignment_text(session, carrier)
        if phase == "marshal":
            return f"{self._holding(session)}, report established"
        if phase == "established":
            if session.granted:
                return self._grant_text(session, carrier)
            return (f"established, number {domain.number_to_words(self._rank(session, carrier))}, "
                    f"{self._holding(session)}, continue holding")
        if phase in PROMPT:
            return PROMPT[phase][a["case"]]
        if phase == "ball":
            return "roger ball"
        if phase == "touchdown":
            return "stand by for deck status"
        return "trap confirmed, clear the landing area, report deck clear"

    # -- procedure calls ------------------------------------------------------

    def _relative(self, carrier, track):
        """(range nm, true bearing ship->aircraft, altitude ft) on DCS x=north, z=east."""
        north = track["point"]["x"] - carrier["point"]["x"]
        east = track["point"]["z"] - carrier["point"]["z"]
        return (math.hypot(north, east) / METRES_PER_NM, math.degrees(math.atan2(east, north)) % 360,
                track["point"]["y"] * FEET_PER_METRE)

    def _tracked(self, session, carrier):
        return session.track is not None and session.track["in_air"] and carrier["point"] is not None

    def _readback(self, session, parsed, carrier):
        if session.kind != "recovery" or session.phase != "assigned":
            return "marshal", "unable, no marshal instructions to read back"
        wrong = _readback_mismatch(parsed["readback"], session.assign)
        if wrong:
            return "marshal", f"negative readback, {_say_field(*wrong)}, read back again"
        session.phase = "marshal"
        self._bump(session)
        return "marshal", "readback correct, report established"

    def _established(self, session, parsed, carrier):
        if session.kind != "recovery" or session.phase in ("checked_in", "assigned"):
            return "marshal", "unable, " + ("read back marshal instructions first" if session.phase == "assigned"
                                            else "no marshal assignment")
        if session.phase != "marshal":
            return self._role(session), self._status(session, carrier)
        if not self._tracked(session, carrier):
            return "marshal", "unable, no radar contact, report established when airborne"
        a = session.assign
        distance, bearing, altitude = self._relative(carrier, session.track)
        if a["case"] == 1:
            near = distance <= OVERHEAD_NM
        else:
            fix = math.radians(a["radial_true"])
            north = session.track["point"]["x"] - carrier["point"]["x"] - math.cos(fix) * a["dme"] * METRES_PER_NM
            east = session.track["point"]["z"] - carrier["point"]["z"] - math.sin(fix) * a["dme"] * METRES_PER_NM
            near = math.hypot(north, east) / METRES_PER_NM <= FIX_TOLERANCE_NM
        if not near or abs(altitude - a["angels"] * 1000) > ALTITUDE_TOLERANCE_FT:
            return "marshal", f"negative, not observed in holding, {self._holding(session)}"
        session.phase = "established"
        self._bump(session)
        return "marshal", f"roger established, number {domain.number_to_words(self._rank(session, carrier))}"

    def _pattern(self, session, call, carrier):
        if session.kind != "recovery" or session.assign is None:
            return self._role(session), "unable, no approach in progress"
        case = session.assign["case"]
        if session.phase == call:
            return self._role(session), self._status(session, carrier)
        allowed = PATTERN[call].get(case, ())
        if session.phase not in allowed or (session.phase == "established" and not session.granted):
            if session.phase in HOLDING:
                return "marshal", (f"negative, continue holding, number "
                                   f"{domain.number_to_words(self._rank(session, carrier))}"
                                   if session.phase == "established" else "unable, " + self._status(session, carrier))
            return self._role(session), "unable, " + self._status(session, carrier)
        if not self._tracked(session, carrier):
            return self._role(session), "unable, no radar contact"
        distance, bearing, altitude = self._relative(carrier, session.track)
        if call == "ball":
            final = carrier["final_true"] if carrier["final_true"] is not None else carrier["heading"]
            astern = domain.diff_heading(round(bearing), round((final + 180) % 360)) <= BALL_SECTOR_DEG
            if distance > BALL_MAX_NM or altitude > BALL_MAX_FT or not astern:
                return "paddles", "unable, position not confirmed, " + self._status(session, carrier)
            if self._foul_deck(carrier, session):
                return "paddles", "wave off, wave off, foul deck, " + self._recycle(session)
            session.phase = "ball"
            self._bump(session)
            return "paddles", "roger ball"
        if distance > CALL_RANGE_NM[call]:
            return self._role(session), "unable, position not confirmed, " + self._status(session, carrier)
        session.phase = call
        self._bump(session)
        return self._role(session), f"{ACK[call]}, {PROMPT[call][case]}"

    def _commencing(self, session, parsed, carrier):
        return self._pattern(session, "commencing", carrier)

    def _platform(self, session, parsed, carrier):
        return self._pattern(session, "platform", carrier)

    def _initial(self, session, parsed, carrier):
        return self._pattern(session, "initial", carrier)

    def _break(self, session, parsed, carrier):
        return self._pattern(session, "break", carrier)

    def _downwind(self, session, parsed, carrier):
        return self._pattern(session, "downwind", carrier)

    def _final(self, session, parsed, carrier):
        return self._pattern(session, "final", carrier)

    def _ball(self, session, parsed, carrier):
        return self._pattern(session, "ball", carrier)

    def _clara(self, session, parsed, carrier):
        if session.kind == "recovery" and session.phase in ("downwind", "final", "ball"):
            return "paddles", "roger clara, keep it coming"
        return self._role(session), "unable, no approach in progress"

    def _waveoff(self, session, parsed, carrier):
        if session.kind == "recovery" and session.phase in ("downwind", "final", "ball"):
            return "paddles", "roger wave off, " + self._recycle(session)
        return self._role(session), "unable, no approach in progress"

    def _bolter(self, session, parsed, carrier):
        """Pilot bolter report: ends the landing clearance only while the track shows the aircraft airborne."""
        if session.kind != "recovery":
            return self._role(session), "unable, no approach in progress"
        if session.phase in ("ball", "touchdown") and self._tracked(session, carrier):
            return "paddles", "roger bolter, " + self._recycle(session)
        if session.phase == "touchdown":
            return "paddles", "roger, stand by for deck status"
        return self._role(session), "unable, " + self._status(session, carrier)

    def _trapped(self, session, parsed, carrier):
        if session.phase == "on_deck":
            return "tower", self._status(session, carrier)
        return self._role(session), "roger, stand by for deck confirmation"

    def _deckClear(self, session, parsed, carrier):
        if session.kind != "recovery" or session.phase != "on_deck":
            return self._role(session), "unable, no trap recorded"
        self._release(session)
        return "tower", "copy deck clear, recovery complete"

    def _airborne(self, session, parsed, carrier):
        if session.kind != "departure":
            return self._role(session), "unable, no departure in progress"
        if session.phase == "airborne":
            return "departure", self._status(session, carrier)
        return "departure", "roger, stand by for launch confirmation"

    def _handoff(self, session, parsed, carrier):
        if session.kind != "departure" or session.phase != "airborne":
            return self._role(session), "unable, launch not confirmed"
        self._release(session)
        return "departure", "departure complete, frequency change approved"
