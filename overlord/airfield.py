"""Native airfield ATC: independent ground/tower/approach control per DCS airdrome.

Facts come only from the DCS mission (``dcs/overlord_airfield.lua``) through the
server hook, which POSTs ``/airfield/exchange`` about once a second.  Radios are the
airdrome's published native radios (installed terrain ``Radio.lua``), runways the
native ``Airbase:getRunways`` geometry, weather the mission's.  Nothing here
allocates a frequency or invents a runway, taxiway, wind, altimeter or procedure.

Bridge exchange, schema version 1
---------------------------------
Request (hook -> brain)::

    {"version": 1, "epoch": "<1-64 [A-Za-z0-9._:-]>", "seq": <int, increasing per epoch>,
     "time": <model seconds>, "abs_time": <seconds since midnight>, "date": {"year", "month", "day"},
     "error": "<mission problems>" (optional),
     "airfields": [{"id", "name", "coalition": 0|1|2, "alive": bool, "point": {x, y, z}, "lat", "lon",
                    "wind_from_deg", "wind_speed_mps", "altimeter_inhg",          # optional each
                    "radios": [{"frequency_hz", "modulation": 0|1, "roles": ["ground"|"tower"|"approach"],
                                "aliases": [str]}],
                    "runways": [{"id", "name", "opposite_name"?, "point", "heading_deg": TRUE of name end,
                                 "length_m", "width_m", "closed"?}],
                    "active_runway"?: designator, "pattern"?: "left"|"right"}],
     "players": [{"unit_id", "unit_name", "player_name", "callsign", "modex", "coalition",
                  "incarnation", "human": true}],                  # identity catalog only
     "aircraft": [{"unit_id", "incarnation", "airfield_id", "point", "lat", "lon", "velocity",
                   "heading_deg": TRUE, "in_air": bool, "ground_airfield_id": id | null,
                   "events": [{"id", "type": land|takeoff|dead|eject|disconnect|birth,
                               "airfield_id": id | null, "time"}]}]}

Response: ``{"version": 1, "accepted": bool, "epoch", "seq", "watch": [{"unit_id", "incarnation",
"airfield_id"}], "error"?}``.

A malformed envelope (version, epoch, seq, time, date, list sizes, duplicate ids) raises
``ValueError`` before any state changes; one invalid airfield, player or aircraft record is
dropped alone and reported in ``registry()["errors"]``.  Calm wind is ``wind_speed_mps`` 0
without a direction; any other missing weather is unavailable, never defaulted.

Radios: every published radio of a coalition-owned live airdrome is a canonical
``coalition:Hz:modulation`` channel.  Airdromes publishing the same channel share one registry
radio (one transmitter); a call on a shared channel must name the airfield before it can enrol.
Reserved channels (AWACS/JTAC base, carrier dynamic) and ambiguous near-identical channels are
disabled with an explicit error, never remapped.

Control (deterministic, conservative): one movement authorisation per airfield across all its
runways (line-up, take-off, crossing, landing), granted to the first candidate eligible on
current geometry in one total order (emergency, arrivals, departures, call order), only while no
controlled aircraft stands on any runway and only on a channel with tower service.  Clearances
need a complete readback; one left unread for 60 model seconds lapses and is not reissued until
the pilot asks again.  Positions, line-up, take-off, touchdown and vacating are proven only by the
mission track, never by the pilot's words.  Replies carry ``validation`` like the carrier
controller: "state" replies need a fresh link and the same session revision, "notice" replies the
same radio, epoch and (when addressed) the same occupant and enrolment generation, and "facts"
replies (wind, altimeter, runway in use) additionally a fresh link and exactly the spoken facts.
"""
from collections import OrderedDict, deque
import datetime as _dt
import itertools
import math
import re
import threading
import time

from . import airfield_parser, domain
from .carrier import (FREQUENCY_GUARD_HZ, _altimeter, _bearing, _digits, _entities, _id,
                      _known_groups, _number_matches, _optional, _player, _point, _velocity, parse_channel)
from .identities import identify_player, sender_identifies
from .jtac import _bool, _list, _num, _readback_runs, _text

REPLY_TTL, EVENT_TTL = 15, 10
MAX_REPLY_CHARS = 1000
MAX_AIRFIELDS, MAX_RADIOS, MAX_RUNWAYS, MAX_ALIASES = 256, 16, 16, 16
MAX_PLAYERS, MAX_AIRCRAFT, MAX_LIFECYCLE = 256, 64, 32
MAX_TEXT_BYTES = 64
MAX_SESSIONS = 64
MAX_RETIRED = 256
MAX_DIAGNOSTICS = 16
MAX_NOTIFICATIONS = 32          # per airfield and channel
MAX_SEEN_EVENTS = 128
MAX_VOCABULARY = 96
EVENT_TYPES = {"land", "takeoff", "dead", "eject", "disconnect", "birth"}
TERMINAL_EVENTS = {"dead", "eject", "disconnect", "birth"}
ROLE_ORDER = ("ground", "tower", "approach")
FEET_PER_METRE = 3.28084
KNOTS_PER_MPS = 1.943844
METRES_PER_NM = domain.METRES_PER_NM

CALM_KNOTS = 3                  # below this the wind is calm and selects no runway end
DESIGNATOR_TOLERANCE_DEG = 20   # native label vs magnetic heading of that end
STRIP_MERGE_M = 100             # records of one physical strip (both ends reported separately)
RUNWAY_SIDE_MARGIN_M = 25       # occupancy footprint beyond the paved edge
RUNWAY_END_MARGIN_M = 60
HOLD_ACROSS_M = 400             # holding point: outside the footprint, this far from the edge at most
HOLD_BEFORE_M, HOLD_INTO_M = 400, 900
LINEUP_ALONG_M = 900
LINEUP_HEADING_DEG = 30
ROLLING_SPEED_MPS = 15
FINAL_MAX_NM, FINAL_MAX_AGL_FT = 10, 3000
FINAL_CONE_DEG, FINAL_CONE_BASE_M, FINAL_HEADING_DEG = 30, 300, 45
PATTERN_MAX_NM, PATTERN_MAX_AGL_FT = 6, 3000
HANDOFF_NM = 10
READBACK_TIMEOUT = 60           # model seconds a runway clearance waits for its readback
ALTIMETER_CHANGE = 0.005
ROUTE_SOURCE_M = 300            # nearest bundled taxi point must be this close to the aircraft
ENCYCLOPEDIA_MATCH_M = 3000     # bundled airfield must be the native airdrome
MAX_ROUTE_NAMES = 8

DEPARTURE_GROUND = ("startup_requested", "startup", "taxi_clearance", "taxi", "crossing_requested",
                    "crossing_clearance", "crossing", "holding_short", "lineup_clearance", "lineup",
                    "lined_up", "takeoff_clearance", "cleared_takeoff")
ARRIVAL_AIRBORNE = ("inbound_standby", "arrival_briefing", "inbound", "downwind", "base", "final",
                    "landing_clearance", "cleared_land")
LANE_CLEARANCE = {"crossing_clearance": "crossing_requested", "lineup_clearance": "holding_short",
                  "takeoff_clearance": "lined_up", "landing_clearance": "final"}
TAKEOFF_GRANTED = ("takeoff_clearance", "cleared_takeoff")
LANDING_GRANTED = ("landing_clearance", "cleared_land")
ENROLLING = ("startup", "taxi", "inbound", "emergency")
VOCABULARY = ["ground", "tower", "approach", "request startup", "request taxi", "holding short",
              "ready for departure", "lined up", "request crossing", "runway", "left", "right", "center",
              "readback", "hold short", "line up and wait", "cleared for takeoff", "cleared to land",
              "altimeter", "airborne", "switching", "inbound", "downwind", "base", "final", "going around",
              "vacated", "taxi to parking", "say again", "request wind", "mayday", "cancel", "checking out"]
_DESIGNATOR = re.compile(r"(0?[1-9]|[12][0-9]|3[0-6])([LRC]?)")
_SIDE_WORDS = {"L": "left", "R": "right", "C": "center"}


# ---------------------------------------------------------------------------
# Bridge payload validation
# ---------------------------------------------------------------------------

def _utf8(value, limit=MAX_TEXT_BYTES):
    value = _text(value, limit)
    if len(value.encode("utf-8")) > limit:
        raise ValueError("bridge text too long")
    return value


def _parse_designator(value):
    """("09", "L") from a native label ("9", "09L"), else None."""
    if not isinstance(value, str):
        return None
    match = _DESIGNATOR.fullmatch(value.strip().upper())
    return (f"{int(match.group(1)):02d}", match.group(2)) if match else None


def reciprocal(designator):
    number, side = int(designator[:2]), designator[2:]
    return f"{(number + 17) % 36 + 1:02d}" + {"L": "R", "R": "L"}.get(side, side)


def _radio_record(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid airfield radio")
    roles = _list(raw.get("roles"), len(ROLE_ORDER))
    if not roles or any(role not in ROLE_ORDER for role in roles) or len(set(roles)) != len(roles):
        raise ValueError("invalid airfield radio roles")
    return {"frequency_hz": _num(raw.get("frequency_hz"), 1000000, 2000000000, integer=True),
            "modulation": _num(raw.get("modulation"), 0, 1, integer=True), "roles": set(roles),
            "aliases": [_utf8(alias) for alias in _list(raw.get("aliases"), MAX_ALIASES)]}


def _strip(raw, field, when):
    """One physical runway; a label inconsistent with native geometry is kept unusable, not renamed."""
    if not isinstance(raw, dict):
        raise ValueError("invalid runway")
    strip = {"id": _id(raw.get("id")), "point": _point(raw.get("point")),
             "heading": _num(raw.get("heading_deg"), 0, 360) % 360,
             "length": _num(raw.get("length_m"), 1, 10000), "width": _num(raw.get("width_m"), 1, 500),
             "closed": _bool(raw.get("closed", False)), "ends": {}, "label_error": None}
    label = _parse_designator(raw.get("name"))
    if label is None:
        strip["label_error"] = "runway label invalid"
        return strip
    name = label[0] + label[1]
    opposite = raw.get("opposite_name")
    if opposite is not None and (_parse_designator(opposite) is None
                                 or "".join(_parse_designator(opposite)) != reciprocal(name)):
        strip["label_error"] = "runway reciprocal label inconsistent"
        return strip
    magnetic = domain.true_to_magnetic(field["lat"], field["lon"], strip["heading"], when)
    if domain.diff_heading(round(magnetic) % 360, int(name[:2]) * 10 % 360) > DESIGNATOR_TOLERANCE_DEG:
        strip["label_error"] = "runway label does not match native heading"
        return strip
    strip["ends"] = {name: strip["heading"], reciprocal(name): (strip["heading"] + 180) % 360}
    return strip


def _airfield(raw, when):
    if not isinstance(raw, dict):
        raise ValueError("invalid airfield")
    lat = _num(raw.get("lat"), -90, 90)
    if abs(lat) == 90:
        raise ValueError("airfield latitude at a pole")
    entry = {"id": _id(raw.get("id")), "name": _utf8(raw.get("name")),
             "coalition": _num(raw.get("coalition"), 0, 2, integer=True), "alive": _bool(raw.get("alive")),
             "point": _point(raw.get("point")), "lat": lat, "lon": _num(raw.get("lon"), -180, 180),
             "wind_from": _optional(raw.get("wind_from_deg"), 0, 360),
             "wind_speed": _optional(raw.get("wind_speed_mps"), 0, 100),
             "altimeter": _optional(raw.get("altimeter_inhg"), 25, 32.5),
             "pattern": raw.get("pattern"), "active": None, "notes": []}
    if entry["wind_from"] is not None:
        entry["wind_from"] %= 360
    if entry["pattern"] not in (None, "left", "right"):
        raise ValueError("invalid traffic pattern")
    active = raw.get("active_runway")
    if active is not None:
        label = _parse_designator(active)
        entry["active"] = label[0] + label[1] if label else "invalid"
    radios = OrderedDict()
    for radio in (_radio_record(r) for r in _list(raw.get("radios"), MAX_RADIOS)):
        key = (radio["frequency_hz"], radio["modulation"])
        if key in radios:       # the same native channel listed twice: one radio, all its roles
            radios[key]["roles"] |= radio["roles"]
            radios[key]["aliases"] += [a for a in radio["aliases"] if a not in radios[key]["aliases"]]
        else:
            radios[key] = radio
    entry["radios"] = list(radios.values())
    strips, seen = [], set()
    for raw_strip in _list(raw.get("runways"), MAX_RUNWAYS):
        strip = _strip(raw_strip, entry, when)
        if strip["id"] in seen:
            raise ValueError("duplicate runway id")
        seen.add(strip["id"])
        strips.append(strip)
    entry["strips"] = _merge_strips(strips)
    for strip in entry["strips"]:
        if strip["label_error"]:
            entry["notes"].append(f"runway {strip['id']}: {strip['label_error']}")
    return entry


def _merge_strips(strips):
    """One entry per physical strip; conflicting or duplicated labels make every claimant unusable."""
    merged = []
    for strip in strips:
        twin = next((m for m in merged if math.hypot(m["point"]["x"] - strip["point"]["x"],
                                                     m["point"]["z"] - strip["point"]["z"]) <= STRIP_MERGE_M
                     and domain.diff_heading(round(m["heading"]) % 180, round(strip["heading"]) % 180) <= 5
                     and abs(m["length"] - strip["length"]) <= 0.1 * max(m["length"], strip["length"])), None)
        if twin is None:
            merged.append(strip)
        elif twin["label_error"] or strip["label_error"] or set(twin["ends"]) != set(strip["ends"]):
            twin["label_error"] = twin["label_error"] or strip["label_error"] or "conflicting runway labels"
            twin["ends"] = {}
    claims = {}
    for strip in merged:
        for name in strip["ends"]:
            claims.setdefault(name, []).append(strip)
    for name, owners in claims.items():
        if len(owners) > 1:
            for strip in owners:
                strip["label_error"], strip["ends"] = "duplicate runway designator " + name, {}
    return merged


def _aircraft(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid aircraft")
    events = []
    for event in _list(raw.get("events"), MAX_LIFECYCLE):
        if (not isinstance(event, dict) or not isinstance(event.get("type"), str)
                or event["type"] not in EVENT_TYPES):
            raise ValueError("invalid lifecycle event")
        place = event.get("airfield_id")
        events.append({"id": _num(event.get("id"), 0, 2 ** 53, integer=True), "type": event["type"],
                       "airfield_id": None if place is None else _id(place),
                       "time": _num(event.get("time"), 0, 1e9)})
    ground = raw.get("ground_airfield_id")
    lat = _num(raw.get("lat"), -90, 90)
    return {"unit_id": _id(raw.get("unit_id")), "incarnation": _id(raw.get("incarnation")),
            "airfield_id": _id(raw.get("airfield_id")), "point": _point(raw.get("point")),
            "lat": lat, "lon": _num(raw.get("lon"), -180, 180), "velocity": _velocity(raw.get("velocity")),
            "heading": _num(raw.get("heading_deg"), 0, 360) % 360, "in_air": _bool(raw.get("in_air")),
            "ground_airfield_id": None if ground is None else _id(ground),
            "events": sorted(events, key=lambda e: e["id"])}


def _snapshot(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("unsupported airfield bridge payload")
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
    when = day + _dt.timedelta(seconds=abs_time)
    epoch = _id(payload.get("epoch"))
    seq = _num(payload.get("seq"), 1, 2 ** 53, integer=True)
    model = _num(payload.get("time"), 0, 1e9)
    diagnostics = []
    error = payload.get("error")
    try:
        error = None if error is None else _text(error, 240)
        if error is not None and not error.isascii():
            raise ValueError("invalid bridge text")
    except ValueError:
        error = None
        diagnostics.append("mission error text rejected: invalid bridge text")
    fields, _ = _entities(payload.get("airfields"), MAX_AIRFIELDS, "id", lambda raw: _airfield(raw, when),
                          "airfield", diagnostics)
    players, _ = _entities(payload.get("players"), MAX_PLAYERS, "unit_id", _player, "player", diagnostics)
    aircraft, rejected = _entities(payload.get("aircraft"), MAX_AIRCRAFT, "unit_id", _aircraft, "aircraft",
                                   diagnostics)
    for field in fields.values():
        diagnostics += [f"airfield {field['id']} {note}" for note in field["notes"]]
    if len(diagnostics) > MAX_DIAGNOSTICS:
        diagnostics[MAX_DIAGNOSTICS:] = [f"{len(diagnostics) - MAX_DIAGNOSTICS} more problems"]
    return {"epoch": epoch, "seq": seq, "time": model, "abs_time": abs_time, "when": when, "error": error,
            "airfields": fields, "players": players, "aircraft": aircraft, "rejected_aircraft": rejected,
            "diagnostics": diagnostics}


# ---------------------------------------------------------------------------
# Geometry (DCS x north, z east, metres)
# ---------------------------------------------------------------------------

def _local(origin, heading, point):
    """(along, across) of ``point`` from ``origin`` on a true ``heading``."""
    rad = math.radians(heading)
    north, east = point["x"] - origin["x"], point["z"] - origin["z"]
    return north * math.cos(rad) + east * math.sin(rad), -north * math.sin(rad) + east * math.cos(rad)


def _in_footprint(strip, point):
    along, across = _local(strip["point"], strip["heading"], point)
    return (abs(along) <= strip["length"] / 2 + RUNWAY_END_MARGIN_M
            and abs(across) <= strip["width"] / 2 + RUNWAY_SIDE_MARGIN_M)


def _from_threshold(strip, heading, point):
    """(along, across) from the threshold of the runway end flown on ``heading``."""
    rad = math.radians(heading)
    half = strip["length"] / 2
    threshold = {"x": strip["point"]["x"] - math.cos(rad) * half, "z": strip["point"]["z"] - math.sin(rad) * half}
    return _local(threshold, heading, point)


def _end(field, designator):
    for strip in field["strips"]:
        if designator in strip["ends"]:
            return strip, strip["ends"][designator]
    return None, None


def _on_any_runway(field, point):
    return any(_in_footprint(strip, point) for strip in field["strips"])


def _agl_ft(field, track):
    return (track["point"]["y"] - field["point"]["y"]) * FEET_PER_METRE


def _at_hold(field, designator, track):
    strip, heading = _end(field, designator)
    if strip is None or track is None or track["in_air"] or _on_any_runway(field, track["point"]):
        return False
    along, across = _from_threshold(strip, heading, track["point"])
    return -HOLD_BEFORE_M <= along <= HOLD_INTO_M and abs(across) <= strip["width"] / 2 + HOLD_ACROSS_M


def _at_crossing(field, designator, track):
    strip, _ = _end(field, designator)
    if strip is None or track is None or track["in_air"] or _on_any_runway(field, track["point"]):
        return False
    along, across = _local(strip["point"], strip["heading"], track["point"])
    return abs(along) <= strip["length"] / 2 and abs(across) <= strip["width"] / 2 + HOLD_ACROSS_M


def _lined_up(field, designator, track):
    strip, heading = _end(field, designator)
    if strip is None or track is None or track["in_air"]:
        return False
    along, across = _from_threshold(strip, heading, track["point"])
    return (-RUNWAY_END_MARGIN_M <= along <= LINEUP_ALONG_M and abs(across) <= strip["width"] / 2
            and domain.diff_heading(round(track["heading"]), round(heading)) <= LINEUP_HEADING_DEG)


def _on_final(field, designator, track):
    """Airborne on the extended centreline before the threshold, inbound, within range and AGL."""
    strip, heading = _end(field, designator)
    if strip is None or track is None or not track["in_air"]:
        return False
    along, across = _from_threshold(strip, heading, track["point"])
    agl = _agl_ft(field, track)
    return (-FINAL_MAX_NM * METRES_PER_NM <= along < 0
            and abs(across) <= -along * math.tan(math.radians(FINAL_CONE_DEG)) + FINAL_CONE_BASE_M
            and domain.diff_heading(round(track["heading"]), round(heading)) <= FINAL_HEADING_DEG
            and 0 <= agl <= FINAL_MAX_AGL_FT)


def _in_pattern(field, track):
    return (track is not None and track["in_air"] and 0 <= _agl_ft(field, track) <= PATTERN_MAX_AGL_FT
            and _distance_nm(field, track) <= PATTERN_MAX_NM)


def _distance_nm(field, track):
    return math.hypot(track["point"]["x"] - field["point"]["x"],
                      track["point"]["z"] - field["point"]["z"]) / METRES_PER_NM


def _rolling(track):
    return track is not None and not track["in_air"] \
        and math.hypot(track["velocity"]["x"], track["velocity"]["z"]) >= ROLLING_SPEED_MPS


def _encyclopedia_designator(name):
    text = name.lower().replace("runway", " ")
    digits = "".join(re.findall(r"\d", text))
    side = next((code for code, word in _SIDE_WORDS.items() if re.search(rf"\b{word}\b", text)), "")
    return airfield_parser.designator(int(digits), side) if digits and len(digits) <= 2 else None


def _named_route(field, track, designator=None):
    """Taxiway names from bundled airfield data, only when it is genuinely this airdrome and the
    route reaches the target without using any runway; otherwise None (never a guessed path)."""
    nav = domain.encyclopedia_airfield(field["name"])
    if nav is None or track is None or not nav.taxi_points \
            or domain.distance_m(nav.lat, nav.lon, field["lat"], field["lon"]) > ENCYCLOPEDIA_MATCH_M:
        return None
    source = min(nav.taxi_points, key=lambda p: domain.distance_m(track["lat"], track["lon"], p["lat"], p["lon"]))
    if domain.distance_m(track["lat"], track["lon"], source["lat"], source["lon"]) > ROUTE_SOURCE_M:
        return None
    paths = nav.shortest_paths(source)
    if designator is not None:
        targets = [r for r in nav.runways if _encyclopedia_designator(r["name"]) == designator]
    else:
        targets = [v for v in nav.vertices if v["kind"] == "parking"]
    options = [paths[id(t)] for t in targets if id(t) in paths and paths[id(t)][1]]
    if not options:
        return None
    names = []
    for _, _, tag in min(options, key=lambda option: option[0])[1]:
        if not isinstance(tag, str) or not tag.strip() or "runway" in tag.lower() or not tag.isprintable():
            return None
        if not names or names[-1] != tag:
            names.append(tag)
    return names if len(names) <= MAX_ROUTE_NAMES else None


# ---------------------------------------------------------------------------
# Phraseology
# ---------------------------------------------------------------------------

def say_runway(designator):
    side = _SIDE_WORDS.get(designator[2:])
    return f"runway {_digits(designator[:2])}" + (f" {side}" if side else "")


def _say_frequency(hz):
    text = f"{hz / 1e6:.3f}".rstrip("0")
    text += "0" if text.endswith(".") else ""
    return " ".join("decimal" if c == "." else domain._ONES[int(c)] for c in text)


def _say_altimeter(value):
    return "altimeter not available" if value is None else f"altimeter {_altimeter(value)}"


_READBACK_TERMS = {
    "taxi": ("hold short", re.compile(r"\bhold(?:ing)? short\b"), None),
    "taxi_in": ("hold short of all runways", re.compile(r"\bhold(?:ing)? short\b"), None),
    "lineup": ("line up and wait", re.compile(r"\bline(?:d|ing)? up\b.*\bwait(?:ing)?\b"),
               re.compile(r"\btake ?off\b|\bland(?:ing)?\b|\bcleared\b")),
    "takeoff": ("cleared for takeoff", re.compile(r"\bcleared (?:for )?take ?off\b"), re.compile(r"\bland(?:ing)?\b")),
    "landing": ("cleared to land", re.compile(r"\bcleared (?:to )?land\b"), re.compile(r"\btake ?off\b")),
    "crossing": ("cross", re.compile(r"\bcross(?:ing)?\b"), re.compile(r"\btake ?off\b|\bland(?:ing)?\b|\bline")),
    "arrival": (None, None, re.compile(r"\bcleared\b")),
}


def readback_problem(text, pending):
    """What a readback misses or gets wrong, or None when complete and exact.

    Every labelled runway must be exactly the cleared one (number and side); the clearance term
    must be present and no conflicting term; an arrival briefing also needs the exact altimeter.
    Any negation or cancel/abort/belay word voids the readback.
    """
    text = text or ""
    # Clearance terms on the raw words: the shared readback runs drop separator words such as "line".
    words = " ".join(re.findall(r"[a-z]+", text.lower().replace("'", "").replace("\u2019", "")))
    if airfield_parser.NEGATION.search(words) or airfield_parser.CANCEL.search(words):
        return "negative readback, read back the clearance again"
    runs = _readback_runs(text)
    runways = []
    for index, (kind, value) in enumerate(runs):
        if kind == "word" and value == "runway":
            number = runs[index + 1] if index + 1 < len(runs) else None
            if number is None or number[0] != "num" or "." in number[1] or not number[1].isdigit() \
                    or not 1 <= len(number[1]) <= 2:
                runways.append(None)
                continue
            side = runs[index + 2] if index + 2 < len(runs) else None
            code = {"left": "L", "l": "L", "right": "R", "r": "R", "center": "C", "centre": "C", "c": "C"}.get(
                side[1] if side and side[0] in ("word", "letter") else "", "")
            runways.append(airfield_parser.designator(int(number[1]), code))
    expected = pending.get("runway")
    if expected is not None and (not runways or any(r != expected for r in runways)):
        return f"{say_runway(expected)}"
    if expected is None and runways:
        return "no runway in that clearance"
    label, required, conflict = _READBACK_TERMS[pending["kind"]]
    if required is not None and not required.search(words):
        return label
    if conflict is not None and conflict.search(words):
        return label or "runway and altimeter only"
    if pending.get("altimeter") is not None:
        values = [runs[i + 1] for i, (kind, value) in enumerate(runs)
                  if kind == "word" and value in ("altimeter", "qnh") and i + 1 < len(runs)]
        if not values or any(v[0] != "num" or not _number_matches(v[1], pending["altimeter"], "altimeter")
                             for v in values):
            return _say_altimeter(pending["altimeter"])
    return None


class _Session:
    __slots__ = ("unit_id", "incarnation", "airfield_id", "channel", "coalition", "caller", "kind", "phase",
                 "revision", "order", "since", "issued_seq", "track", "track_seq", "seen", "seen_order",
                 "priority", "runway", "pending", "cross", "resume", "entered", "vacating", "airborne_seen",
                 "expired")

    def __init__(self, player, field_id, channel, caller, kind, revision, order, since):
        self.unit_id, self.incarnation = player["unit_id"], player["incarnation"]
        self.airfield_id, self.channel, self.coalition = field_id, channel, player["coalition"]
        self.caller, self.kind, self.revision, self.order, self.since = caller, kind, revision, order, since
        self.phase = "startup_requested" if kind == "departure" else "inbound_standby"
        self.issued_seq = None
        self.track, self.track_seq = None, None
        self.seen, self.seen_order = set(), deque()
        self.priority = self.entered = self.vacating = self.airborne_seen = False
        self.expired = False        # a runway clearance lapsed unread: no new grant until the pilot asks
        self.runway = None          # runway the current instructions are built on
        self.pending = None         # clearance awaiting readback
        self.cross = None           # runway designator of a crossing request
        self.resume = None          # taxi phase ("taxi" or "taxi_in") to resume after the crossing


class AirfieldService:
    """Independent native airfield controllers, synchronised with the mission bridge."""

    def __init__(self, *, reserved_channels=(), stale_seconds=10):
        self._base_reserved = self._parse_reserved(reserved_channels)
        self._dynamic_reserved = ()
        if not 0 < stale_seconds <= 300:
            raise ValueError("stale_seconds must be within 0-300")
        self.stale_seconds = stale_seconds
        self._lock = threading.RLock()
        self._revisions = itertools.count(1)
        self._orders = itertools.count(1)
        self._note_ids = itertools.count(1)
        self._retired = OrderedDict()
        self._reset_epoch(None)

    @staticmethod
    def _parse_reserved(channels):
        parsed = []
        for channel in channels or ():
            if channel:
                canonical = parse_channel(channel)
                if canonical not in parsed:
                    parsed.append(canonical)
        return tuple(parsed)

    def _reset_epoch(self, epoch):
        self._epoch, self._seq, self._received = epoch, 0, None
        self._suspended = False
        self._fields, self._players, self._error, self._diagnostics = OrderedDict(), {}, None, []
        self._time, self._when = 0.0, None
        self._sessions = {}
        self._control, self._notes = {}, {}
        self._generations = {}

    # -- freshness ---------------------------------------------------------

    @property
    def ready(self):
        with self._lock:
            return self._fresh()

    def _fresh(self):
        return self._received is not None and time.monotonic() - self._received <= self.stale_seconds

    def _refresh(self):
        """Stale bridge: revoke unstarted clearances once; every outstanding state reply becomes invalid."""
        if self._received is None or self._fresh() or self._suspended:
            return
        self._suspended = True
        for session in self._sessions.values():
            field = self._fields.get(session.airfield_id)
            if field is not None:
                self._revoke_unstarted(session, field)
            if session.phase == "taxi_clearance":
                session.phase, session.pending = "startup", None
            elif session.phase == "arrival_briefing":
                session.phase, session.pending = "inbound_standby", None
            session.issued_seq = None
            self._bump(session)

    # -- bridge ------------------------------------------------------------

    def exchange(self, payload):
        snap = _snapshot(payload)
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
            self._classify(snap["airfields"])
            self._refresh()
            self._seq, self._received, self._suspended = seq, time.monotonic(), False
            self._apply(snap)
            watch = []
            for session in self._sessions.values():
                if session.issued_seq is None:
                    session.issued_seq = seq
                watch.append({"unit_id": session.unit_id, "incarnation": session.incarnation,
                              "airfield_id": session.airfield_id})
            return {"version": 1, "accepted": True, "epoch": epoch, "seq": seq, "watch": watch}

    @staticmethod
    def _rejected(reason, epoch, seq):
        return {"version": 1, "accepted": False, "error": reason, "epoch": epoch, "seq": seq, "watch": []}

    def set_reserved_channels(self, channels):
        """Replace the dynamic reservations (carrier radios); constructor reservations always stay."""
        parsed = self._parse_reserved(channels)
        with self._lock:
            self._dynamic_reserved = parsed
            self._classify(self._fields)
            for fid, field in self._fields.items():
                if field["enabled"]:
                    self._control.setdefault(fid, {"runway": None, "lane": None, "altimeter": field["altimeter"]})
            self._drop_unpublished()
            for fid in self._control:
                self._schedule(self._fields[fid])

    def _reserved(self):
        return self._base_reserved + tuple(c for c in self._dynamic_reserved if c not in self._base_reserved)

    def _classify(self, fields):
        """Channel of every published radio, disabled with a reason when it cannot be served."""
        reserved = [(c, int(c.split(":")[1])) for c in self._reserved()]
        for field in fields.values():
            field["spoken"] = domain.pronounce_airbase(field["name"])
            keys = airfield_parser.name_keys(field["name"])
            for radio in field["radios"]:
                for alias in radio["aliases"]:
                    keys |= airfield_parser.name_keys(alias)
                coalition = field["coalition"]
                radio["channel"] = (f"{coalition}:{radio['frequency_hz']}:{radio['modulation']}"
                                    if coalition in (1, 2) else None)
                radio["error"] = None
                if not field["alive"]:
                    radio["error"] = "airfield not alive"
                elif coalition not in (1, 2):
                    radio["error"] = "neutral airfield, no coalition radio service"
                else:
                    for channel, frequency in reserved:
                        if abs(frequency - radio["frequency_hz"]) < FREQUENCY_GUARD_HZ:
                            radio["error"] = f"frequency within {FREQUENCY_GUARD_HZ} Hz of reserved channel {channel}"
                            break
            field["keys"] = keys
        live = [(field, radio) for field in fields.values() for radio in field["radios"] if radio["channel"]]
        for field, radio in live:
            if radio["error"] is None and any(
                    other["channel"] != radio["channel"] and f["coalition"] == field["coalition"]
                    and abs(other["frequency_hz"] - radio["frequency_hz"]) < FREQUENCY_GUARD_HZ
                    for f, other in live):
                radio["error"] = f"frequency within {FREQUENCY_GUARD_HZ} Hz of another airfield channel"
        for field in fields.values():
            for radio in field["radios"]:
                radio["enabled"] = radio["error"] is None
            field["roles"] = {}
            for radio in field["radios"]:
                if radio["enabled"]:
                    field["roles"].setdefault(radio["channel"], set()).update(radio["roles"])
            if not field["alive"]:
                field["error"] = "airfield not alive"
            elif field["coalition"] not in (1, 2):
                field["error"] = "neutral airfield, no coalition radio service"
            elif not field["radios"]:
                field["error"] = "no published radio"
            elif not field["roles"]:
                field["error"] = "no usable published radio"
            else:
                field["error"] = None
            field["enabled"] = field["error"] is None

    def _apply(self, snap):
        self._time, self._when = snap["time"], snap["when"]
        self._error, self._diagnostics = snap["error"], snap["diagnostics"]
        previous = self._fields
        self._fields, self._players = snap["airfields"], snap["players"]
        for fid in list(self._control):
            old, new = previous.get(fid), self._fields.get(fid)
            if new is None or not new["enabled"] or old is None or old["coalition"] != new["coalition"]:
                del self._control[fid]
        for fid, field in self._fields.items():
            if field["enabled"]:
                self._control.setdefault(fid, {"runway": None, "lane": None, "altimeter": field["altimeter"]})
        self._drop_unpublished()
        for session in list(self._sessions.values()):
            player = self._players.get(session.unit_id)
            if player is None or player["incarnation"] != session.incarnation \
                    or player["coalition"] != session.coalition:
                self._release(session)
        self._generations = {uid: gen for uid, gen in self._generations.items()
                             if uid in self._players or uid in self._sessions}
        for uid in snap["rejected_aircraft"]:
            session = self._sessions.get(uid)
            if session is not None:
                self._release(session, "tracking data invalid, airfield session closed, call again")
        reported = set()
        for uid, record in snap["aircraft"].items():
            session = self._sessions.get(uid)
            if session and record["incarnation"] == session.incarnation \
                    and record["airfield_id"] == session.airfield_id:
                reported.add(uid)
                self._observe(session, record)
        for session in list(self._sessions.values()):
            if session.unit_id not in reported and session.issued_seq is not None \
                    and snap["seq"] > session.issued_seq + 1:
                self._release(session, "radar contact lost, airfield session closed, call again")
        for fid in self._control:
            self._schedule(self._fields[fid])

    def _drop_unpublished(self):
        """Sessions and queued speech on a channel its airfield no longer serves end without remapping."""
        for fid in list(self._control):
            if not self._fields[fid]["enabled"]:
                del self._control[fid]
        for key in list(self._notes):
            fid, channel = key
            if fid not in self._control or channel not in self._fields[fid]["roles"]:
                del self._notes[key]
        for session in list(self._sessions.values()):
            field = self._fields.get(session.airfield_id)
            if session.airfield_id not in self._control or session.channel not in field["roles"] \
                    or field["coalition"] != session.coalition:
                self._release(session)

    # -- lifecycle ---------------------------------------------------------

    def _bump(self, session):
        session.revision = next(self._revisions)

    def _free_lane(self, session):
        control = self._control.get(session.airfield_id)
        if control is not None and control["lane"] == session.unit_id:
            control["lane"] = None

    def _holds_lane(self, session):
        control = self._control.get(session.airfield_id)
        return control is not None and control["lane"] == session.unit_id

    def _release(self, session, notice=None):
        if self._sessions.get(session.unit_id) is not session:
            return
        del self._sessions[session.unit_id]
        self._free_lane(session)
        self._bump(session)
        if notice and (session.airfield_id, session.channel) in self._notes_keys():
            self._push(session, notice, self._notice(session.airfield_id, session.channel, session.unit_id,
                                                     session.incarnation))

    def _notes_keys(self):
        return {(fid, channel) for fid in self._control for channel in self._fields[fid]["roles"]}

    def _current(self, session):
        """Track reported in the latest snapshot, else None: no stale movement fallback."""
        return session.track if session.track_seq == self._seq else None

    def _occupying(self, session, field):
        """Physically on a runway (latest known position, conservatively) or touched down unconfirmed."""
        if session.phase == "touchdown":
            return True
        track = session.track
        return track is not None and not track["in_air"] and _on_any_runway(field, track["point"])

    def _ground_here(self, session):
        track = self._current(session)
        return track is not None and not track["in_air"] and track["ground_airfield_id"] == session.airfield_id

    def _observe(self, session, record):
        field = self._fields[session.airfield_id]
        for event in record["events"]:
            if event["id"] in session.seen:
                continue
            session.seen.add(event["id"])
            session.seen_order.append(event["id"])
            if len(session.seen_order) > MAX_SEEN_EVENTS:
                session.seen.discard(session.seen_order.popleft())
            if event["time"] < session.since:
                continue            # history from before this enrolment is not this flight's
            kind, place = event["type"], event["airfield_id"]
            if kind in TERMINAL_EVENTS:
                self._release(session)
                return
            if kind == "land":
                if session.kind == "arrival" and session.phase in ARRIVAL_AIRBORNE:
                    # A land event after enrolment is a real landing (a parked aircraft never sends one).
                    if place != field["id"]:
                        self._release(session, f"landing not observed at {field['spoken']}, arrival closed")
                        return
                    session.airborne_seen = True
                    self._touchdown(session)
                elif session.kind == "departure" and session.phase == "airborne":
                    self._release(session)
                    return
            elif place is not None and place != field["id"]:
                self._release(session)
                return
            elif session.kind == "departure" and session.phase in DEPARTURE_GROUND:
                self._takeoff(session, field)
            elif session.kind == "arrival" and session.phase in ("touchdown", "landed"):
                self._touch_and_go(session, field)
            elif session.kind == "arrival" and session.phase not in ARRIVAL_AIRBORNE:
                self._release(session, "airborne, arrival closed")
                return
        if self._sessions.get(session.unit_id) is not session:
            return
        session.track, session.track_seq = record, self._seq
        if record["in_air"]:
            session.airborne_seen = True
        if session.kind == "departure":
            self._observe_departure(session, field, record)
        elif session.phase == "touchdown":
            if record["in_air"]:
                self._touch_and_go(session, field)
            elif record["ground_airfield_id"] == field["id"]:
                session.phase = "landed"
                self._bump(session)
                self._event(session, "tower", "landing observed, vacate the runway, report vacated")
        elif session.phase in ARRIVAL_AIRBORNE and not record["in_air"]:
            if not session.airborne_seen:
                # Parked when it checked in: never a landing later, unless it is an emergency on the ground.
                if session.priority and record["ground_airfield_id"] == field["id"]:
                    self._free_lane(session)
                    session.phase, session.pending = "landed", None
                    self._bump(session)
                    self._event(session, "tower", "emergency on the ground, vacate any runway, report vacated")
                else:
                    self._release(session, "you are not airborne, arrival not required, report inbound when airborne")
                return
            if record["ground_airfield_id"] == field["id"]:
                self._touchdown(session)
                session.phase = "landed"
                self._event(session, "tower", "landing observed, vacate the runway, report vacated")
            elif record["ground_airfield_id"] is not None:
                self._release(session, f"landing not observed at {field['spoken']}, arrival closed")
                return
        if session.phase == "crossing":
            strip, _ = _end(field, session.cross)
            if strip is not None and _in_footprint(strip, record["point"]):
                session.entered = True
            elif session.entered and not record["in_air"] and not _on_any_runway(field, record["point"]):
                self._event(session, "ground", self._crossing_vacated(session))
        if session.vacating and not self._occupying(session, field) and not record["in_air"]:
            self._observed_vacated(session, field)
        elif session.phase == "landed" and not record["in_air"] and not _on_any_runway(field, record["point"]):
            self._free_lane(session)
            session.phase = "vacated"
            self._bump(session)
            self._event(session, "ground", "runway vacated, request taxi to parking")

    def _observe_departure(self, session, field, record):
        if record["in_air"] and session.phase in DEPARTURE_GROUND:
            self._takeoff(session, field)
        elif session.phase == "startup_requested":
            if self._ground_here(session):
                session.phase = "startup"
                self._bump(session)
                self._event(session, "ground", self._status(session, field))
            elif record["ground_airfield_id"] is not None:
                self._release(session, f"unable, not observed on the ground at {field['spoken']}, departure closed")
        elif session.phase == "lineup" and _lined_up(field, session.runway, record):
            session.phase = "lined_up"
            self._bump(session)
        elif session.phase == "airborne" and _distance_nm(field, record) >= HANDOFF_NM:
            self._release(session, "leaving the zone, frequency change approved")

    def _touchdown(self, session):
        """Mission land event at this airfield; the lane stays held until the runway is vacated."""
        session.phase, session.pending = "touchdown", None
        self._bump(session)
        if not self._holds_lane(session):
            self._event(session, "tower", "landing without clearance observed")

    def _touch_and_go(self, session, field):
        """Airborne again after a touchdown: the old landing instructions end; it goes around."""
        self._free_lane(session)
        self._event(session, "tower", "airborne observed, go around, " + self._reentry(session, field))

    def _takeoff(self, session, field):
        if session.phase == "startup_requested":
            self._release(session, f"unable, not observed on the ground at {field['spoken']}, departure closed")
            return
        cleared = session.phase == "cleared_takeoff"
        self._free_lane(session)            # only ever this aircraft's own lane
        session.phase, session.pending, session.vacating = "airborne", None, False
        self._bump(session)
        if cleared:
            self._event(session, "tower", self._airborne_text(field, session))
        else:
            self._event(session, "tower", "takeoff not cleared, airborne observed, report leaving the zone")

    def _airborne_text(self, field, session):
        lead = "airborne"
        channel = self._contact_channel(field, session, "approach")
        if channel:
            lead += f", contact {field['spoken']} approach {_say_frequency(int(channel.split(':')[1]))}"
        return lead + ", report leaving the zone"

    def _reentry(self, session, field):
        """Go-around: back to the start of the visual arrival on the current runway."""
        session.pending, session.vacating = None, False
        runway = self._control[field["id"]]["runway"] if field["id"] in self._control else None
        session.runway = runway
        self._bump(session)
        if runway is None:
            session.phase = "inbound_standby"
            return "runway in use not available, remain clear of the airfield, stand by"
        session.phase = "inbound"
        if field["pattern"]:
            return f"fly runway heading, re-enter {field['pattern']} downwind {say_runway(runway)}, report downwind"
        return f"fly runway heading, report final {say_runway(runway)}"

    # -- scheduling --------------------------------------------------------

    def _on(self, field):
        return [s for s in self._sessions.values() if s.airfield_id == field["id"]]

    @staticmethod
    def _queue_key(session):
        return (not session.priority, 0 if session.kind == "arrival" else 1, session.order)

    def _rank(self, session, field):
        waiting = sorted((s for s in self._on(field) if s.phase in ("final", "holding_short", "crossing_requested")),
                         key=self._queue_key)
        return waiting.index(session) + 1 if session in waiting else 1

    def _select_runway(self, field, current):
        """(designator, reason): configured active runway, else wind; calm picks a fixed real runway."""
        ends = {name: (strip, heading) for strip in field["strips"] if not strip["closed"]
                for name, heading in strip["ends"].items()}
        if field["active"] is not None:
            if field["active"] in ends:
                return field["active"], None
            return None, "configured active runway not usable"
        if not ends:
            return None, "no usable runway"
        speed, wind = field["wind_speed"], field["wind_from"]
        calm = speed is not None and speed * KNOTS_PER_MPS < CALM_KNOTS
        if not calm and (speed is None or wind is None):
            return None, "wind information not available, unable to determine the runway in use"
        if current in ends and (calm or domain.diff_heading(round(wind), round(ends[current][1])) < 90):
            return current, None            # no tailwind: keep it rather than flap between ends
        if calm:
            return min(ends, key=lambda name: (-ends[name][0]["length"], name)), None
        return min(ends, key=lambda name: (-round(math.cos(math.radians(wind - ends[name][1])), 6),
                                           -ends[name][0]["length"], name)), None

    def _contact_channel(self, field, session, role):
        """Another channel of this airfield that publishes ``role`` while the session's does not."""
        if role in field["roles"].get(session.channel, ()):
            return None
        return next((channel for channel, roles in sorted(field["roles"].items()) if role in roles), None)

    def _schedule(self, field, speaker=None):
        """Revoke what current facts no longer support, then grant the airfield's one movement lane."""
        if not self._fresh() or field["id"] not in self._control or not field["enabled"]:
            return None
        control = self._control[field["id"]]
        spoken = None

        def tell(session, role, message):
            nonlocal spoken
            if session is speaker:
                spoken = message if spoken is None else f"{spoken}, {message}"
            else:
                self._event(session, role, message)

        runway, _ = self._select_runway(field, control["runway"])
        control["runway"] = runway
        altimeter_changed = (control["altimeter"] is None) != (field["altimeter"] is None) or (
            field["altimeter"] is not None and abs(control["altimeter"] - field["altimeter"]) >= ALTIMETER_CHANGE)
        control["altimeter"] = field["altimeter"]
        sessions = sorted(self._on(field), key=self._queue_key)
        for session in sessions:
            if session.vacating or session.phase in ("airborne", "cancelled", "touchdown", "landed", "vacated",
                                                     "taxi_in_clearance", "taxi_in", "startup_requested"):
                continue
            stale = session.runway is not None and session.runway != runway
            if session.kind == "arrival":
                rebrief = stale or (session.phase == "inbound_standby" and runway is not None
                                    and field["altimeter"] is not None) or (
                    altimeter_changed and session.phase == "arrival_briefing")
                if rebrief and session.phase in ARRIVAL_AIRBORNE:
                    lead = "go around, " if session.phase in LANDING_GRANTED else ""
                    lead += "runway changed, " if stale else ""
                    if session.phase in LANDING_GRANTED or session.phase == "final":
                        self._free_lane(session)
                    message = self._brief(session, field)
                    if session.phase != "inbound_standby" or stale or lead:
                        tell(session, self._arrival_role(session, field), lead + message)
            elif stale:
                if self._occupying(session, field):
                    session.vacating = True
                    session.phase, session.pending, session.runway = "startup", None, None
                    self._bump(session)
                    tell(session, "tower", "runway changed, clearance cancelled, vacate the runway, then request taxi")
                else:
                    self._free_lane(session)
                    tell(session, "ground", "runway changed, " + self._taxi_clearance(session, field))
        lane = self._sessions.get(control["lane"]) if control["lane"] else None
        if control["lane"] and lane is None:
            control["lane"] = lane = None
        if lane is not None and lane.pending is not None and lane.phase in LANE_CLEARANCE \
                and self._time - lane.pending["issued"] > READBACK_TIMEOUT:
            message = self._revoke_unstarted(lane, field)
            if message:
                # Expired: no new grant until the pilot asks again (never a renewed unattended clearance).
                lane.expired = True
                tell(lane, "tower", "no readback, " + message)
            lane = self._sessions.get(control["lane"]) if control["lane"] else None
        if lane is not None and not self._has_tower(field, lane):
            message = self._revoke_unstarted(lane, field, reason="tower service not available on this frequency")
            if message is None and not lane.expired:
                # Keep physically started movement/occupancy, not its obsolete tower authority.
                lane.pending, lane.expired = None, True
                self._bump(lane)
            if message:
                tell(lane, "ground", message)
            lane = self._sessions.get(control["lane"]) if control["lane"] else None
        occupiers = [s for s in sessions if s.unit_id in self._sessions and self._occupying(s, field)]
        if lane is not None and any(o is not lane for o in occupiers):
            message = self._revoke_unstarted(lane, field, reason="runway occupied")
            if message:
                tell(lane, "tower", message)
            lane = self._sessions.get(control["lane"]) if control["lane"] else None
        if lane is not None and lane.phase == "lined_up" and not lane.vacating and not lane.expired \
                and lane.runway == runway and self._has_tower(field, lane) \
                and all(o is lane for o in occupiers) and self._current(lane) is not None \
                and _lined_up(field, runway, self._current(lane)):
            self._grant(lane, field, "takeoff", "takeoff_clearance",
                        f"{say_runway(runway)}, {self._wind(field)}, cleared for takeoff, read back")
            tell(lane, "tower", lane.pending["text"])
        if control["lane"] is None and not occupiers and runway is not None:
            # One total order (emergency, arrivals, departures, call order); the first candidate that is
            # eligible on current geometry gets the lane.
            for session in sessions:
                if session.unit_id not in self._sessions or not self._eligible(session, field, runway):
                    continue
                if session.phase == "final":
                    self._grant(session, field, "landing", "landing_clearance",
                                f"{say_runway(runway)}, {self._wind(field)}, cleared to land, read back")
                elif session.phase == "holding_short":
                    self._grant(session, field, "lineup", "lineup_clearance",
                                f"{say_runway(runway)}, line up and wait, read back")
                else:
                    self._grant(session, field, "crossing", "crossing_clearance",
                                f"cross {say_runway(session.cross)}, report vacated, read back")
                tell(session, "tower", session.pending["text"])
                break
        return spoken

    def _grant(self, session, field, kind, phase, text):
        self._control[field["id"]]["lane"] = session.unit_id
        session.phase = phase
        session.pending = {"kind": kind, "runway": session.cross if kind == "crossing" else session.runway,
                           "altimeter": None, "text": text, "issued": self._time}
        self._bump(session)

    def _has_tower(self, field, session):
        """Runway authority needs tower service on the session's own channel."""
        return "tower" in field["roles"].get(session.channel, ())

    def _eligible(self, session, field, runway):
        track = self._current(session)
        if track is None or session.vacating or session.expired or not self._has_tower(field, session):
            return False
        if session.phase == "final":
            return session.runway == runway and _on_final(field, runway, track)
        if session.phase == "holding_short":
            return session.runway == runway and _at_hold(field, runway, track)
        return session.phase == "crossing_requested" and _at_crossing(field, session.cross, track)

    def _revoke_unstarted(self, session, field, reason="clearance cancelled"):
        """End a runway clearance that has not physically started; returns the instruction, if any.

        A lane holder standing on a runway keeps the lane (never bumped); only a take-off clearance
        not yet rolling is cancelled.
        """
        if not self._holds_lane(session):
            return None
        if self._occupying(session, field):
            if session.phase in TAKEOFF_GRANTED and not _rolling(session.track):
                session.phase, session.pending = "lined_up", None
                self._bump(session)
                return f"hold position, takeoff clearance cancelled, {reason}"
            return None
        if session.phase in LANDING_GRANTED or session.phase == "final":
            self._free_lane(session)
            return "go around, " + reason + ", " + self._reentry(session, field)
        if session.phase in ("crossing_clearance", "crossing"):
            self._free_lane(session)
            session.phase, session.pending, session.entered = "crossing_requested", None, False
            self._bump(session)
            return f"hold short {say_runway(session.cross)}, crossing clearance cancelled, {reason}"
        if session.phase in ("lineup_clearance", "lineup", "lined_up") + TAKEOFF_GRANTED:
            self._free_lane(session)
            session.phase, session.pending = "holding_short", None
            self._bump(session)
            return f"hold short {say_runway(session.runway)}, line up clearance cancelled, {reason}"
        return None

    def _brief(self, session, field):
        """Arrival briefing from current facts; needs readback of runway and altimeter."""
        runway = self._control[field["id"]]["runway"]
        session.runway = runway
        self._bump(session)
        if runway is None or field["altimeter"] is None:
            session.phase, session.pending = "inbound_standby", None
            reason = self._select_runway(field, None)[1] if runway is None else "altimeter not available"
            return f"{reason}, remain clear of the airfield, stand by"
        visual = (f"enter {field['pattern']} downwind {say_runway(runway)}, report downwind" if field["pattern"]
                  else f"report final {say_runway(runway)}")
        text = (f"{say_runway(runway)} in use, {self._wind(field)}, {_say_altimeter(field['altimeter'])}, "
                f"no published approach available, make a visual approach, {visual}, "
                "read back runway and altimeter")
        session.phase = "arrival_briefing"
        session.pending = {"kind": "arrival", "runway": runway, "altimeter": field["altimeter"], "text": text,
                           "issued": self._time}
        return text

    def _taxi_clearance(self, session, field):
        runway = self._control[field["id"]]["runway"]
        session.runway, session.cross, session.entered = runway, None, False
        self._bump(session)
        if runway is None:
            session.phase, session.pending = "startup", None
            return f"{self._select_runway(field, None)[1]}, hold position"
        route = _named_route(field, self._current(session), runway)
        path = (f"via {', '.join(route)}" if route
                else "named taxi route not available, taxi at your discretion")
        text = (f"taxi to holding point {say_runway(runway)} {path}, hold short of all runways, "
                f"{_say_altimeter(field['altimeter'])}, read back")
        session.phase = "taxi_clearance"
        session.pending = {"kind": "taxi", "runway": runway, "altimeter": None, "text": text, "issued": self._time}
        return text

    def _wind(self, field):
        speed, wind = field["wind_speed"], field["wind_from"]
        if speed is not None and speed * KNOTS_PER_MPS < CALM_KNOTS:
            return "wind calm"
        if speed is None or wind is None:
            return "wind not available"
        magnetic = round(domain.true_to_magnetic(field["lat"], field["lon"], wind, self._when)) % 360
        return f"wind {_bearing(magnetic)} at {domain.number_to_words(round(speed * KNOTS_PER_MPS))}"

    def _information(self, field):
        runway, reason = self._select_runway(field, self._control[field["id"]]["runway"])
        parts = [f"{say_runway(runway)} in use" if runway else reason, self._wind(field),
                 _say_altimeter(field["altimeter"])]
        if field["pattern"] and runway:
            parts.append(f"{field['pattern']} traffic")
        return ", ".join(parts)

    # -- notifications and validation -----------------------------------------

    def _validation(self, session):
        field = self._fields[session.airfield_id]
        return {"role": "airfield", "kind": "state", "epoch": self._epoch, "airfield_id": field["id"],
                "channel": session.channel, "coalition": field["coalition"], "unit_id": session.unit_id,
                "incarnation": session.incarnation, "revision": session.revision, "generation": None,
                "expires": time.time() + REPLY_TTL}

    def _generation(self, unit_id):
        if unit_id not in self._generations:
            self._generations[unit_id] = next(self._orders)
        return self._generations[unit_id]

    def _notice(self, field_id, channel, unit_id, incarnation=None):
        """Radio-bound reply (airfield ``None``: the shared channel itself), bound to the occupant."""
        return {"role": "airfield", "kind": "notice", "epoch": self._epoch, "airfield_id": field_id,
                "channel": channel, "coalition": int(channel.split(":")[0]), "unit_id": unit_id,
                "incarnation": incarnation, "revision": None,
                "generation": None if unit_id is None else self._generation(unit_id),
                "expires": time.time() + REPLY_TTL}

    def _speech(self, caller, speaker, message):
        reply = domain._speech(caller, speaker, message)
        if len(reply) > MAX_REPLY_CHARS:
            reply = domain._speech("last transmitter", speaker, "say again")
        return reply

    def _push(self, session, message, validation, role=None):
        field = self._fields[session.airfield_id]
        validation["expires"] = time.time() + EVENT_TTL
        role = self._speaking_role(field, session.channel, role or self._phase_role(session))
        notes = self._notes.setdefault((field["id"], session.channel), deque(maxlen=MAX_NOTIFICATIONS))
        notes.append({"id": next(self._note_ids), "validation": validation,
                      "reply": self._speech(session.caller, f"{field['spoken']} {role}", message)})

    def _event(self, session, role, message):
        self._push(session, message, self._validation(session), role)

    @staticmethod
    def _speaking_role(field, channel, wanted):
        roles = field["roles"].get(channel, ())
        return wanted if wanted in roles else next((r for r in ROLE_ORDER if r in roles), wanted)

    def _phase_role(self, session):
        if session.phase in ("lineup_clearance", "lineup", "lined_up", "takeoff_clearance", "cleared_takeoff",
                             "crossing_clearance", "crossing", "holding_short", "final", "landing_clearance",
                             "cleared_land", "touchdown", "landed", "cancelled"):
            return "tower"
        if session.kind == "arrival" and session.phase in ARRIVAL_AIRBORNE:
            return self._arrival_role(session, self._fields[session.airfield_id])
        return "approach" if session.phase == "airborne" else "ground"

    @staticmethod
    def _arrival_role(session, field):
        return "approach" if "approach" in field["roles"].get(session.channel, ()) else "tower"

    def _channel_valid(self, channel, coalition):
        return isinstance(channel, str) and isinstance(coalition, int) and not isinstance(coalition, bool) \
            and bool(self._members(channel, coalition))

    def validate_reply(self, validation):
        """True while a synthesised airfield reply still matches mission and procedure state."""
        if not isinstance(validation, dict) or validation.get("role") != "airfield":
            return False
        with self._lock:
            self._refresh()
            channel, coalition = validation.get("channel"), validation.get("coalition")
            expires, field_id = validation.get("expires"), validation.get("airfield_id")
            if validation.get("epoch") != self._epoch or not self._channel_valid(channel, coalition) \
                    or isinstance(expires, bool) or not isinstance(expires, (int, float)) \
                    or not expires > time.time():
                return False
            if field_id is not None:
                field = self._fields.get(field_id) if isinstance(field_id, str) else None
                if field is None or field_id not in self._control or field["coalition"] != coalition \
                        or channel not in field["roles"]:
                    return False
            unit_id = validation.get("unit_id")
            kind = validation.get("kind")
            if kind in ("notice", "facts"):
                if kind == "facts" and (field_id is None or not self._fresh()
                                        or validation.get("facts") != self._information(field)):
                    return False        # spoken live facts: only while fresh and still exactly true
                if unit_id is None:
                    return True
                generation, incarnation = validation.get("generation"), validation.get("incarnation")
                player = self._players.get(unit_id) if isinstance(unit_id, str) else None
                return (player is not None and isinstance(incarnation, str)
                        and player["incarnation"] == incarnation
                        and isinstance(generation, int) and not isinstance(generation, bool)
                        and self._generations.get(unit_id) == generation)
            if validation.get("kind") != "state" or not self._fresh() or not isinstance(unit_id, str) \
                    or field_id is None:
                return False
            session = self._sessions.get(unit_id)
            revision = validation.get("revision")
            return (session is not None and session.airfield_id == field_id and session.channel == channel
                    and session.incarnation == validation.get("incarnation")
                    and isinstance(revision, int) and not isinstance(revision, bool)
                    and session.revision == revision)

    def take_events(self, request):
        """Pending notifications of every airfield on one physical channel, in issue order."""
        coalition, channel = self._radio(request)
        with self._lock:
            self._refresh()
            members = self._members(channel, coalition)
            if not members:
                return {"events": [], "vocabulary": []}
            notes = []
            for field in members:
                notes += self._notes.pop((field["id"], channel), ())
            now = time.time()
            events = [dict(note["validation"], reply=note["reply"], ttl_seconds=note["validation"]["expires"] - now)
                      for note in sorted(notes, key=lambda n: n["id"]) if self.validate_reply(note["validation"])]
            names = []
            for field in members:
                names += [field["spoken"]] + [a for r in field["radios"] if r["channel"] == channel
                                              for a in r["aliases"]]
            callsigns = sorted({p["callsign"] for p in self._players.values()
                                if p["coalition"] == coalition and p["callsign"]})[:32]
            vocabulary = list(dict.fromkeys(names + VOCABULARY + callsigns))[:MAX_VOCABULARY]
            return {"events": events, "vocabulary": vocabulary}

    def release_unit(self, unit_id):
        """Cross-role transfer: end this aircraft's airfield session and every outstanding notice to it."""
        with self._lock:
            if not isinstance(unit_id, str):
                return False
            if unit_id in self._generations:
                self._generations[unit_id] = next(self._orders)
            session = self._sessions.get(unit_id)
            if session is None:
                return False
            self._release(session)
            return True

    # -- registry and routing --------------------------------------------------

    def registry(self):
        with self._lock:
            self._refresh()
            groups = OrderedDict()
            for field in self._fields.values():
                for radio in field["radios"]:
                    if radio["channel"] is None:
                        continue
                    group = groups.setdefault(radio["channel"], {
                        "id": radio["channel"], "name": f"Airfield ATC {radio['frequency_hz'] / 1e6:.3f}",
                        "coalition": field["coalition"], "frequency_hz": radio["frequency_hz"],
                        "modulation": radio["modulation"], "channel": radio["channel"],
                        "enabled": False, "error": None})
                    if field["enabled"] and radio["enabled"]:
                        group["enabled"], group["error"] = True, None
                    elif not group["enabled"] and group["error"] is None:
                        group["error"] = radio["error"] or field["error"]
            radios = list(groups.values())
            airfields = [{"id": f["id"], "name": f["name"], "coalition": f["coalition"], "alive": f["alive"],
                          "enabled": f["enabled"], "error": f["error"],
                          "runway": self._control.get(f["id"], {}).get("runway"),
                          "radios": [{"channel": r["channel"], "frequency_hz": r["frequency_hz"],
                                      "modulation": r["modulation"], "roles": sorted(r["roles"]),
                                      "enabled": r["enabled"], "error": r["error"]} for r in f["radios"]]}
                         for f in self._fields.values()]
            errors = ([self._error] if self._error else []) + [
                f"airfield {f['id']} {f['name']}: {f['error']}" for f in self._fields.values() if f["error"]] + [
                f"airfield {f['id']} radio {r['channel'] or r['frequency_hz']}: {r['error']}"
                for f in self._fields.values() if f["enabled"] for r in f["radios"] if r["error"]] + list(
                self._diagnostics)
            return {"ready": self._fresh(), "epoch": self._epoch, "radios": radios, "airfields": airfields,
                    "errors": errors}

    @staticmethod
    def _radio(request):
        if not isinstance(request, dict) or request.get("airfield") is not True:
            raise ValueError("invalid airfield request")
        coalition = request.get("coalition")
        if isinstance(coalition, bool) or not isinstance(coalition, (int, str)) or str(coalition) not in ("1", "2"):
            raise ValueError("invalid coalition")
        channel = parse_channel(request.get("channel"))
        if int(channel.split(":")[0]) != int(coalition):
            raise ValueError("radio channel coalition mismatch")
        return int(coalition), channel

    def _members(self, channel, coalition):
        """Airfields this coalition's channel serves."""
        return [f for fid, f in self._fields.items() if fid in self._control and f["coalition"] == coalition
                and channel in f["roles"]]

    # -- radio ---------------------------------------------------------------

    def respond(self, request):
        """Answer one transmission heard on a native airfield channel."""
        coalition, channel = self._radio(request)
        text, sender = request.get("text"), request.get("sender_callsign")
        if not isinstance(text, str) or len(text) > 2048:
            raise ValueError("invalid text")
        if sender is not None and (not isinstance(sender, str) or len(sender) > 128):
            raise ValueError("invalid sender callsign")
        with self._lock:
            self._refresh()
            fresh = self._fresh()
            members = self._members(channel, coalition)
            if not members:
                return {"reply": None, "parsed": None, "notes": "No airfield radio on this channel"}
            frequency, modulation = channel.split(":")[1:]
            # Every airdrome publishing this physical frequency, so a captured one is refused, not ignored.
            sharing = {fid: f for fid, f in self._fields.items()
                       if any(str(r["frequency_hz"]) == frequency and str(r["modulation"]) == modulation
                              for r in f["radios"])}
            # Every known airfield name is recognised, so an explicitly named field that does not publish
            # this channel is refused rather than silently becoming the channel's default field.
            parsed = airfield_parser.parse(text, {fid: f["keys"] for fid, f in self._fields.items()},
                                           _known_groups(self._players.values()))
            if parsed is None:
                return {"reply": None, "parsed": None, "notes": "Transmission is not addressed to an airfield"}
            answer = {"parsed": parsed, "notes": None}
            player, caller, problem = identify_player(self._players.values(), parsed, coalition)
            uid = player["unit_id"] if player else None
            incarnation = player["incarnation"] if player else None
            session = self._sessions.get(uid) if uid else None
            if session is not None and session.incarnation != incarnation:
                self._release(session)
                session = None
            field, speaker, refusal = self._addressed(parsed, members, sharing, session, coalition)
            if field is None and refusal is None:
                return {"reply": None, "parsed": parsed, "notes": "Named airfield does not publish this channel"}
            if refusal is not None:
                answer.update(reply=self._speech(caller, speaker, refusal), notes=refusal,
                              validation=self._notice(None, channel, uid, incarnation))
                return answer
            roles = field["roles"][channel]
            role = parsed["receiver"]["role"]
            if role is not None and role not in roles:
                other = self._contact_channel(field, _Probe(channel), role)
                message = f"unable, {field['spoken']} {role} is not on this frequency" + (
                    f", contact {field['spoken']} {role} {_say_frequency(int(other.split(':')[1]))}" if other else "")
                answer.update(reply=self._speech(caller, self._speaker(field, channel, None), message),
                              notes="Controller role not published on this channel",
                              validation=self._notice(field["id"], channel, uid, incarnation))
                return answer
            if player is None:
                message = "say again" if parsed["intent"] == "None" and caller == "last transmitter" else problem
                answer.update(reply=self._speech(caller, self._speaker(field, channel, role), message), notes=problem,
                              validation=self._notice(field["id"], channel, None))
                return answer
            if sender is not None and not sender_identifies(self._players.values(), sender, player):
                answer.update(reply=self._speech(caller, self._speaker(field, channel, role),
                                                 "unable, your radio name does not identify you"),
                              notes="SRS radio name does not uniquely identify the spoken caller",
                              validation=self._notice(field["id"], channel, uid, incarnation))
                return answer
            intent = parsed["intent"]
            enrolled = False
            if not fresh and intent not in ("cancel", "checkOut", "cancelEmergency"):
                message, answer["notes"] = "unable, no live airfield link", "Airfield bridge snapshot missing or stale"
            elif session is not None and session.airfield_id != field["id"]:
                other = self._fields.get(session.airfield_id)
                name = other["spoken"] if other else "another airfield"
                if intent not in ENROLLING:
                    message = f"unable, you are working {name}"
                elif other is not None and (self._occupying(session, other) or self._holds_lane(session)):
                    message = f"unable, you hold a runway clearance at {name}, cancel it first"
                else:
                    message, enrolled = self._handle(intent, parsed, player, caller, field, channel, None)
                session = None if not enrolled else session
            else:
                if session is not None and session.channel != channel and intent != "information":
                    # Same airfield, other band or role: physical phase and lane stay; old speech ends.
                    session.channel, session.caller = channel, caller
                    self._generations[uid] = next(self._orders)
                    self._bump(session)
                message, enrolled = self._handle(intent, parsed, player, caller, field, channel, session)
            session = self._sessions.get(uid)
            informational = intent == "information" and fresh
            if session is not None and session.airfield_id == field["id"] and fresh and not informational:
                extra = self._schedule(field, speaker=session)
                if extra:
                    message = f"{message}, {extra}"
            elif fresh:
                self._schedule(field)
            session = self._sessions.get(uid)
            own = session is not None and session.airfield_id == field["id"] and session.channel == channel
            spoken_role = role or (self._phase_role(session) if own else None)
            answer["reply"] = self._speech(caller, self._speaker(field, channel, spoken_role), message)
            if informational:
                # Live facts, never an instruction: valid only while fresh and every spoken fact still holds.
                answer["validation"] = dict(self._notice(field["id"], channel, uid, incarnation), kind="facts",
                                            facts=self._information(field))
            elif own and fresh:
                answer["validation"] = self._validation(session)
            else:
                answer["validation"] = self._notice(field["id"], channel, uid, incarnation)
            if enrolled and own:
                answer["transfer_unit_id"] = uid
            return answer

    def _speaker(self, field, channel, role):
        return f"{field['spoken']} {self._speaking_role(field, channel, role)}"

    def _addressed(self, parsed, members, sharing, session, coalition):
        """(airfield, speaker for a refusal, refusal message); (None, None, None) = not this radio's call."""
        receiver = parsed["receiver"]
        member_ids = {f["id"] for f in members}
        if receiver["named"]:
            named = set(receiver["fields"])
            relevant = named & (member_ids | set(sharing))
            if not relevant:
                return None, None, None         # explicitly another airfield's radio: never a default
            if len(named) > 1:
                return None, "Tower", "more than one airfield matches that name, say again with the full name"
            if named & member_ids:
                return self._fields[next(iter(named))], None, None
            target = sharing[next(iter(named))]
            if target["coalition"] != coalition and target["alive"]:
                owner = "neutral" if target["coalition"] == 0 else "not under your coalition's control"
                return None, target["spoken"], f"unable, {target['spoken']} is {owner}, no service"
            return None, None, None
        role = (receiver["role"] or "tower").capitalize()
        if receiver.get("unresolved"):
            # Unrecognised words before the callsign may be a misheard airfield: never assume one.
            return None, role, "say again with the airfield name"
        if session is not None and session.airfield_id in member_ids:
            return self._fields[session.airfield_id], None, None
        if len(members) == 1:
            return members[0], None, None
        names = " or ".join(f["spoken"] for f in members[:3])
        return None, role, f"say which airfield you are calling, {names}"

    def _register(self, player, caller, field, channel, kind, track=None):
        current = self._sessions.get(player["unit_id"])
        if current is None and len(self._sessions) >= MAX_SESSIONS:
            return None
        if current is not None:
            self._release(current)
        uid = player["unit_id"]
        session = _Session(player, field["id"], channel, caller, kind, next(self._revisions), next(self._orders),
                           self._time)
        if current is not None and current.airfield_id == field["id"] and track is not None:
            session.track, session.track_seq = track, current.track_seq
            session.airborne_seen = current.airborne_seen
            session.seen, session.seen_order = current.seen, current.seen_order
        self._generations[uid] = session.order
        self._sessions[uid] = session
        return session

    def _handle(self, intent, parsed, player, caller, field, channel, session):
        """(message, enrolled) for one call from an identified caller of this airfield."""
        own = session if session is not None and session.airfield_id == field["id"] else None
        if intent == "cancelEmergency":
            if own is None or not own.priority:
                return "roger, no emergency on record", False
            own.priority = False
            self._bump(own)
            return "roger, emergency cancelled, " + self._status(own, field), False
        if intent in ("cancel", "checkOut"):
            if own is None:
                return "copy, no active airfield session", False
            if self._occupying(own, field):
                own.phase, own.pending, own.vacating = "cancelled", None, True
                self._bump(own)
                return "clearance cancelled, vacate the runway, report vacated", False
            self._release(own)
            return ("copy, checked out" if intent == "checkOut" else f"copy, {own.kind} cancelled, checked out"), False
        if intent == "information":
            return self._information(field), False
        if intent == "None":
            return "say again", False
        if intent == "emergency":
            return self._emergency(player, caller, field, channel, own)
        if intent == "startup":
            return self._departure(player, caller, field, channel, own, taxi=False)
        if intent == "taxi":
            if own is not None and own.kind == "arrival" and own.phase == "vacated":
                return self._taxi_in(own, field), False
            return self._departure(player, caller, field, channel, own, taxi=True)
        if intent == "inbound":
            return self._arrival(player, caller, field, channel, own)
        if own is None:
            return "unable, no active session, state your request", False
        if intent == "sayAgain":
            return self._status(own, field), False
        return getattr(self, "_" + intent)(own, parsed, field), False

    def _emergency(self, player, caller, field, channel, own):
        if own is None:
            session = self._register(player, caller, field, channel, "arrival")
            if session is None:
                return "unable, airfield traffic limit reached", False
            session.priority = True
            return "copy emergency, you have priority, " + self._brief(session, field), True
        own.priority = True
        self._bump(own)
        if own.kind == "departure" and own.phase in TAKEOFF_GRANTED + ("lined_up", "lineup") \
                and self._occupying(own, field):
            own.phase, own.pending, own.vacating = "startup", None, True
            return "copy emergency, takeoff aborted, vacate the runway when able, report vacated", True
        return "copy emergency, you have priority, " + self._status(own, field), True

    def _departure(self, player, caller, field, channel, own, taxi):
        if own is not None and own.kind == "departure":
            if own.phase in ("taxi_in_clearance", "taxi_in") and self._ground_here(own) \
                    and not self._holds_lane(own) and not self._occupying(own, field):
                # Returned toward parking, now departing again from an observed ground position here.
                own.phase, own.pending, own.runway, own.expired = "startup", None, None, False
                self._bump(own)
            if taxi and own.phase in ("startup", "taxi_clearance"):
                if not self._ground_here(own):
                    return "unable, not observed on the ground here", False
                return self._taxi_clearance(own, field), False
            return self._status(own, field), False
        track = None
        if own is not None:
            track = self._current(own)
            if own.kind == "arrival" and not (own.phase in ("vacated", "taxi_in_clearance", "taxi_in")
                                              and self._ground_here(own)):
                return "unable, departure requires the aircraft on the ground clear of the runway", False
        session = self._register(player, caller, field, channel, "departure", track)
        if session is None:
            return "unable, airfield traffic limit reached", False
        if not self._ground_here(session):
            return f"standby, not yet observed on the ground at {field['spoken']}", True
        session.phase = "startup"
        if taxi:
            return self._taxi_clearance(session, field), True
        return self._status(session, field), True

    def _arrival(self, player, caller, field, channel, own):
        if own is not None and own.kind == "arrival":
            return self._status(own, field), False
        track = None
        if own is not None:
            track = self._current(own)
            if track is None or not track["in_air"]:
                return "unable, you are on the ground", False
        session = self._register(player, caller, field, channel, "arrival", track)
        if session is None:
            return "unable, airfield traffic limit reached", False
        return self._brief(session, field), True

    def _taxi_in(self, session, field):
        if self._holds_lane(session) or self._occupying(session, field):
            return "vacate the runway first, report vacated"
        if not self._ground_here(session):
            return "unable, not observed on the ground here"
        route = _named_route(field, self._current(session))
        path = (f"taxi to parking via {', '.join(route)}" if route
                else "named taxi route not available, taxi to parking at your discretion")
        text = f"{path}, hold short of all runways, read back"
        session.phase, session.cross, session.runway = "taxi_in_clearance", None, None
        session.pending = {"kind": "taxi_in", "runway": None, "altimeter": None, "text": text, "issued": self._time}
        self._bump(session)
        return text

    def _tower_note(self, field, session):
        """None with tower service here; else the handoff, or why no runway clearance can be given."""
        if self._has_tower(field, session):
            return None
        contact = self._contact_channel(field, session, "tower")
        if contact:
            return f"contact {field['spoken']} tower {_say_frequency(int(contact.split(':')[1]))}"
        return f"unable, no tower service at {field['spoken']}, runway clearances not available"

    def _status(self, session, field):
        """Current instruction rendered from present state; never replays a revoked clearance."""
        phase = session.phase
        runway = say_runway(session.runway) if session.runway else None
        if (phase in LANE_CLEARANCE or phase in ("crossing", "lineup") + TAKEOFF_GRANTED + LANDING_GRANTED) \
                and (session.expired or not self._has_tower(field, session)):
            return self._tower_note(field, session) or "previous runway clearance no longer current, report intentions"
        if session.pending is not None:
            return session.pending["text"]
        if session.vacating or phase in ("cancelled", "landed"):
            return "vacate the runway, report vacated"
        if phase == "startup_requested":
            return "standby, not yet observed on the ground here"
        if phase == "startup":
            return f"startup approved, {_say_altimeter(field['altimeter'])}, report ready to taxi"
        if phase == "taxi":
            return f"taxi to holding point {runway}, hold short of all runways, report holding short"
        tower = self._tower_note(field, session) if phase in ("crossing_requested", "holding_short", "lined_up",
                                                              "final") else None
        if phase == "crossing_requested":
            if tower or session.expired:
                return f"hold short {say_runway(session.cross)}, " + (tower or "request crossing again when ready")
            return (f"hold short {say_runway(session.cross)}, number "
                    f"{domain.number_to_words(self._rank(session, field))} for crossing")
        if phase == "crossing":
            return f"cross {say_runway(session.cross)}, report vacated"
        if phase == "holding_short":
            if tower or session.expired:
                return f"hold short {runway}, " + (tower or "report ready for departure when ready")
            return f"hold short {runway}, number {domain.number_to_words(self._rank(session, field))} for departure"
        if phase == "lineup":
            return f"line up and wait {runway}, report lined up"
        if phase == "lined_up":
            if tower or session.expired:
                return f"hold position {runway}, " + (tower or "report ready for takeoff when ready")
            return f"hold position {runway}, stand by for takeoff clearance"
        if phase == "cleared_takeoff":
            return f"{runway}, cleared for takeoff"
        if phase == "airborne":
            return self._airborne_text(field, session)
        if phase == "inbound_standby":
            reason = (self._select_runway(field, None)[1] if self._control[field["id"]]["runway"] is None
                      else "altimeter not available")
            return f"{reason}, remain clear of the airfield, stand by"
        if phase == "inbound":
            return (f"report {field['pattern']} downwind {runway}" if field["pattern"] else f"report final {runway}")
        if phase == "downwind":
            return "report base"
        if phase == "base":
            return f"report final {runway}"
        if phase == "final":
            if tower:
                return tower
            return f"number {domain.number_to_words(self._rank(session, field))}, continue approach {runway}"
        if phase == "cleared_land":
            return f"{runway}, cleared to land"
        if phase == "touchdown":
            return "stand by for landing confirmation"
        if phase == "vacated":
            return "request taxi to parking"
        return "taxi to parking, hold short of all runways, report parked"

    # -- procedure calls ------------------------------------------------------

    def _readback(self, session, parsed, field):
        if session.pending is None:
            return "unable, no clearance to read back"
        problem = readback_problem(parsed["readback"], session.pending)
        if problem:
            return f"negative readback, {problem}, read back again"
        kind = session.pending["kind"]
        session.pending = None
        session.phase = {"taxi": "taxi", "taxi_in": "taxi_in", "lineup": "lineup", "takeoff": "cleared_takeoff",
                         "landing": "cleared_land", "crossing": "crossing", "arrival": "inbound"}[kind]
        self._bump(session)
        if kind == "lineup" and _lined_up(field, session.runway, self._current(session)):
            session.phase = "lined_up"
        return "readback correct, " + self._status(session, field) if kind in ("taxi", "arrival", "lineup",
                                                                                "taxi_in", "crossing") \
            else "readback correct"

    def _holdingShort(self, session, parsed, field):
        if session.kind != "departure":
            return "unable, no departure in progress"
        if session.phase in ("lineup", "lined_up"):
            return self._linedUp(session, parsed, field)
        if session.phase == "taxi_clearance":
            return "unable, read back taxi clearance first"
        if session.phase == "holding_short" and session.expired:
            session.expired = False         # a fresh request after a lapsed clearance
            self._bump(session)
            return self._status(session, field)
        if session.phase != "taxi":
            return self._status(session, field)
        if not _at_hold(field, session.runway, self._current(session)):
            return f"negative, not observed at the holding point {say_runway(session.runway)}, report holding short"
        session.phase, session.expired = "holding_short", False
        self._bump(session)
        return self._status(session, field)

    def _linedUp(self, session, parsed, field):
        if session.kind != "departure":
            return "unable, no departure in progress"
        if session.phase == "lineup":
            if not _lined_up(field, session.runway, self._current(session)):
                return f"negative, not observed lined up {say_runway(session.runway)}"
            session.phase = "lined_up"
            self._bump(session)
        elif session.phase == "lined_up" and session.expired:
            session.expired = False
            self._bump(session)
        return self._status(session, field)

    def _crossing(self, session, parsed, field):
        if session.phase not in ("taxi", "holding_short", "crossing_requested", "taxi_in"):
            return "unable, " + self._status(session, field)
        target = parsed["runway"]
        if target is None or _end(field, target)[0] is None:
            return "unable, say the runway to cross"
        if not _at_crossing(field, target, self._current(session)):
            return f"negative, not observed holding short {say_runway(target)}"
        if session.phase != "crossing_requested":
            session.resume = "taxi_in" if session.phase == "taxi_in" else "taxi"
        session.phase, session.cross, session.entered, session.expired = "crossing_requested", target, False, False
        self._bump(session)
        return self._status(session, field)

    def _crossing_vacated(self, session):
        """Measured clear of every runway after the crossing: back to the taxi it interrupted."""
        self._free_lane(session)
        session.phase, session.entered, session.cross = session.resume or "taxi", False, None
        self._bump(session)
        if session.phase == "taxi_in":
            return "runway vacated, continue taxi to parking, hold short of all runways"
        return (f"runway vacated, continue taxi to holding point {say_runway(session.runway)}, "
                "hold short of all runways")

    def _vacated(self, session, parsed, field):
        track = self._current(session)
        if track is None or track["in_air"] or _on_any_runway(field, track["point"]):
            return "negative, not observed clear of the runway"
        if session.phase == "crossing" and session.entered:
            return "roger, " + self._crossing_vacated(session)
        if session.vacating:
            self._observed_vacated(session, field)
            if session.unit_id not in self._sessions:
                return "roger, runway vacated, checked out"
            return "roger, " + self._status(session, field)
        if session.phase == "landed":
            self._free_lane(session)
            session.phase = "vacated"
            self._bump(session)
            return "roger, request taxi to parking"
        return self._status(session, field)

    def _observed_vacated(self, session, field):
        self._free_lane(session)
        session.vacating = False
        self._bump(session)
        if session.phase == "cancelled":
            self._release(session, "runway vacated, checked out")

    def _taxiIn(self, session, parsed, field):
        if session.kind == "departure" and session.phase in ("startup", "taxi_clearance", "taxi", "holding_short",
                                                             "crossing_requested"):
            return self._taxi_in(session, field)
        if session.kind == "arrival" and session.phase in ("vacated", "landed", "taxi_in_clearance"):
            return self._taxi_in(session, field)
        return "unable, " + self._status(session, field)

    def _airborne(self, session, parsed, field):
        if session.kind == "departure" and session.phase == "airborne":
            return self._status(session, field)
        return "negative, airborne not observed"

    def _handoff(self, session, parsed, field):
        if session.kind != "departure" or session.phase != "airborne":
            return "unable, " + self._status(session, field)
        self._release(session)
        return "frequency change approved, good day"

    def _pattern_call(self, session, field, call, allowed):
        if session.kind != "arrival":
            return "unable, no arrival in progress"
        if session.phase == "arrival_briefing":
            return "unable, read back runway and altimeter first"
        if session.phase == call:
            return self._status(session, field)
        if session.phase not in allowed:
            return "unable, " + self._status(session, field)
        track = self._current(session)
        if call == "final":
            if not _on_final(field, session.runway, track):
                return f"negative, not observed on final {say_runway(session.runway)}, " + self._status(session, field)
        elif not _in_pattern(field, track):
            return "negative, position not confirmed, " + self._status(session, field)
        session.phase, session.expired = call, False
        self._bump(session)
        return f"roger {call}, " + self._status(session, field)

    def _downwind(self, session, parsed, field):
        if not field["pattern"]:
            return "unable, no published traffic pattern, " + self._status(session, field)
        return self._pattern_call(session, field, "downwind", ("inbound",))

    def _base(self, session, parsed, field):
        if not field["pattern"]:
            return "unable, no published traffic pattern, " + self._status(session, field)
        return self._pattern_call(session, field, "base", ("downwind",))

    def _final(self, session, parsed, field):
        return self._pattern_call(session, field, "final", ("inbound", "downwind", "base"))

    def _goAround(self, session, parsed, field):
        if session.kind != "arrival" or session.phase not in ARRIVAL_AIRBORNE + ("touchdown",):
            return "unable, no approach in progress"
        track = self._current(session)
        if session.phase == "touchdown" and (track is None or not track["in_air"]):
            return "stand by for landing confirmation"
        self._free_lane(session)
        return "roger going around, " + self._reentry(session, field)

    def _landed(self, session, parsed, field):
        if session.kind == "arrival" and session.phase in ("touchdown", "landed", "vacated"):
            return self._status(session, field)
        return "negative, touchdown not observed"


class _Probe:
    """Channel holder for ``_contact_channel`` before a session exists."""
    __slots__ = ("channel",)

    def __init__(self, channel):
        self.channel = channel
