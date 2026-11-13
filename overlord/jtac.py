"""Live DCS JTAC controller on its own SRS channel, driven by a mission companion script.

The controller never invents mission values.  Every task, brief line, restriction,
target and controller fact comes from the DCS mission (``dcs/overlord_jtac.lua``)
through the server hook (``dcs/overlord_hook.lua``), which POSTs to the brain's
authenticated ``/dcs/exchange`` endpoint about once a second.  Mark and laser
requests stay pending until the mission acknowledges the actual DCS API result.

Bridge exchange, schema version 1
---------------------------------
Request (hook -> brain, JSON object)::

    {"version": 1,
     "epoch": "<1-64 chars [A-Za-z0-9._:-]>",   # new value per mission start
     "seq": <int, strictly increasing within the epoch>,
     "time": <mission model time, seconds>,
     "error": "<mission configuration problem>" | null,
     "tasks": [{"id": <1-99>, "name": "<text>", "coalition": 1 | 2,
                "controller": {"name": "<unit>", "alive": bool},
                "target": {"alive": bool, "total": int, "remaining": int,
                           "destroyed": bool,      # every element has affirmative DCS death evidence
                           "lat": float, "lon": float, "alt": float},   # position only while alive
                "brief": {"type": 1|2|3, "ip": text, "heading": 0-360, "offset": text,
                          "distance": nm, "elevation": ft, "description": text,
                          "location": text, "mark": text, "friendlies": text,
                          "egress": text, "remarks": text},             # absent line = missing
                "laser_code": 1111-1788, "marks": ["laser"|"smoke"|"ir"|"illumination"],
                "readback": ["elevation", "location", ...],             # brief keys to read back
                "attack_heading": [min_deg, max_deg],                   # optional restriction
                "require_mark": bool,
                "laser": {"on": bool, "command": "<id>" | null},
                "ir": {"on": bool, "command": "<id>" | null}}],
     "acks": [{"id": "<command id>", "status": "done"|"failed"|"cancelled"|"expired"|"rejected",
               "reason": "<text>"}]}

Response (brain -> hook)::

    {"version": 1, "accepted": true, "epoch": "<epoch>", "seq": <request seq echoed>,
     "commands": [{"id": "<id>", "op": "laser_on" | "laser_off" | "mark", "task": <id>,
                   "deadline": <mission model time>, "kind": "smoke"|"ir"|"illumination"}],  # kind: mark only
     "cancel": ["<id>", ...]}

    {"version": 1, "accepted": false, "error": "stale sequence" | "retired epoch",
     "epoch": "<epoch>", "seq": <request seq>, "commands": [], "cancel": []}

Sequence lifecycle: the mission numbers snapshots 1, 2, 3... per epoch.  The hook
POSTs snapshot N; the brain accepts it only if N exceeds the last accepted seq of
that epoch and echoes ``"seq": N``.  The hook hands that body to the mission on
its next call, and the mission runs its commands and renews its laser lease only
when epoch matches and seq equals the snapshot it issued last (N).  Anything else
(empty, rejected, malformed, replayed, previous epoch) renews nothing, so spots
expire ``leaseSeconds`` after the last accepted exchange.

Commands are resent until acknowledged; the mission deduplicates by id, refuses
them after their deadline or a cancel, and re-reports its stored acknowledgement.
``laser_off`` destroys every spot (laser and IR) of the task.  A new epoch drops
every session and command and retires the previous epoch for the life of the
process (bounded history), so a delayed or replayed snapshot of a finished
mission is refused.  Malformed payloads raise ``ValueError``.

Reply validation: radio answers carry ``validation`` = {"role": "jtac", "channel",
"coalition", "unit_id", "epoch", "generation", "revision", "target": bool,
"spot": "<command id>" | None, "spot_kind": "laser" | "ir" | None}; check it with
``validate_reply`` immediately before transmitting.  A ``spot`` reply stays valid
only while that exact spot is live in the latest snapshot.

Readback: the pilot reads the task's readback fields in brief order.  Numeric
lines (heading, distance, elevation) compare as complete signed decimal values
("six point five" is 6.5, "six five" is 65); text lines compare as whole runs
of digits, phonetic letters and words.  Line labels such as "elevation" and
"location" are optional separators; two numeric fields spoken with no label or
pause word between them form one number and are refused.  Extra or missing
content is refused, never guessed.
"""
from collections import OrderedDict, deque
from decimal import Decimal, InvalidOperation
import itertools
import re
import threading
import time
import uuid

from . import domain
from . import parser as _parser

COMMAND_TTL = 15            # seconds a mark/laser request may wait for its acknowledgement
EVENT_TTL = 10
MAX_RETIRED = 256           # retired mission epochs remembered for replay refusal
SESSION_IDLE = 3600
MAX_TASKS, MAX_ACKS, MAX_SESSIONS, MAX_OUTBOX, MAX_EVENTS = 32, 64, 64, 32, 32
MAX_REPLY_CHARS = 1000      # speech service limit; replies never truncate authored fields
MAX_LIST_CHARS = 600
TEXT_LIMITS = {"ip": 40, "offset": 20, "description": 120, "location": 60, "mark": 60, "friendlies": 80,
               "egress": 60, "remarks": 160}
MARK_KINDS = ("laser", "smoke", "ir", "illumination")
SPOTS = ("laser", "ir")
ACK_STATUSES = {"done", "failed", "cancelled", "expired", "rejected"}
LINES = {"ip": 1, "heading": 2, "offset": 2, "distance": 3, "elevation": 4, "description": 5,
         "location": 6, "mark": 7, "friendlies": 8, "egress": 9, "remarks": 10}
NUMERIC_LINES = {"heading", "distance", "elevation"}
REQUIRED_LINES = ("type", "ip", "heading", "distance", "elevation", "description", "location", "mark",
                  "friendlies", "egress")
_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_CHANNEL = re.compile(r"([12]):(\d{1,12}):(\d{1,2})")

_LETTERS = {"alpha": "a", "alfa": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
            "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliet": "j", "juliett": "j",
            "kilo": "k", "lima": "l", "mike": "m", "november": "n", "oscar": "o", "papa": "p",
            "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t", "uniform": "u", "victor": "v",
            "whiskey": "w", "whisky": "w", "xray": "x", "yankee": "y", "zulu": "z"}
_MARK_WORDS = {"smoke": "smoke", "sparkle": "ir", "ir": "ir", "infrared": "ir", "pointer": "ir",
               "illumination": "illumination", "illum": "illumination", "laser": "laser"}

# Earliest match wins; on an equal start the earlier entry wins.
COMMANDS = [
    ("checkOut", re.compile(r"\bcheck(?:ing)? out\b")),
    ("checkIn", re.compile(r"\bcheck(?:ing)? in\b|\bon station\b")),
    ("readback", re.compile(r"\bread ?back\b")),
    ("repeatLine", re.compile(r"\b(?:say again|repeat|confirm) (?:line|remarks)\b")),
    ("requestBrief", re.compile(
        r"\b(?:ready (?:for|to copy)|request(?:ing)?|send) (?:the )?(?:brief|briefing|(?:nine|9) ?line)\b"
        r"|\bready to copy\b")),
    ("requestTask", re.compile(r"\b(?:request(?:ing)?|ready for|need|say) (?:a |the )?(?:task|tasking)\b"
                               r"|\btask (?:number )?(?=(?:\d|one|two|three|four|five|six|seven|eight|nine|"
                               r"niner|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
                               r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b)")),
    ("laserOff", re.compile(r"\b(?:laser|lase|lasing|spot|sparkle) off\b"
                            r"|\b(?:terminate|stop|cease|kill|cancel|no|negative|belay) (?:the )?"
                            r"(?:laser|lase|lasing|spot|sparkle)\b")),
    ("requestLaser", re.compile(r"\b(?:request(?:ing)? |need )?(?:the )?(?:laser|lase|lasing)(?: on)?\b")),
    ("requestMark", re.compile(r"\b(?:request(?:ing)?|need) (?:a |the )?(?:mark|smoke|sparkle|ir pointer|"
                               r"illumination|illum)\b|\b(?:mark|smoke|sparkle) (?:the )?target\b")),
    ("abort", re.compile(r"\babort(?:ing)?\b")),
    ("requestBda", re.compile(r"\b(?:request(?:ing)?|say|send|ready for) (?:the )?(?:bda|b d a|battle damage)"
                              r"|\bbda\b")),
    ("attackComplete", re.compile(r"\boff (?:target|safe|dry|hot|left|right|north|south|east|west)\b"
                                  r"|\b(?:attack|pass) complete\b|\boff$")),
    ("inbound", re.compile(r"\b(?:rolling )?in (?:heading|from|hot|dry|now)\b|\b(?:rolling )?in$")),
    ("contact", re.compile(r"\b(?:contact|tally|visual)\b")),
    ("status", re.compile(r"\b(?:say|request(?:ing)?) (?:status|state)\b|\bstatus\b")),
]
_LASER_OFF = dict(COMMANDS)["laserOff"]
# Intents that advance procedure facts or start DCS actions.  A negation anywhere voids them
# (parse marks them ``blocked`` so no inference may reinterpret them); an explicit abort or
# cancel anywhere in the transmission turns the whole request into an abort.
_POSITIVE = {"checkIn", "requestTask", "requestBrief", "readback", "contact", "inbound", "requestMark",
             "requestLaser", "attackComplete"}
_ABORT = re.compile(r"\b(?:abort(?:ing)?|cancel(?:led|ling)?|belay)\b")
_NEGATION = re.compile(r"\b(?:no|not|negative|dont|do not|unable|lost|stop|cease|terminate)\b")

# Labels and acknowledgement words that only separate readback values.
_READBACK_FILLERS = {"feet", "foot", "ft", "elevation", "location", "grid", "line", "heading", "distance",
                     "miles", "mile", "nm", "nautical", "ip", "egress", "mark", "friendlies", "remarks",
                     "the", "and", "of", "is", "at", "a", "an", "copy", "roger", "read", "back", "readback",
                     "uh", "um", "over"}


def _readback_atoms(text):
    text = text.lower()
    text = re.sub(r"(?<![\w.])-(?=\d)", " minus ", re.sub(r"(?<![\w.])\+(?=\d)", " plus ", text))
    text = re.sub(r"(\d)\.(\d)", r"\1 point \2", text).replace("x-ray", "xray").replace("x ray", "xray")
    for tok in _parser.tokenize(text):
        if tok.n is not None:
            yield "num", tok.d
        elif tok.w in _LETTERS:
            yield "letter", _LETTERS[tok.w]
        elif tok.w in ("point", "decimal"):
            yield "point", tok.w
        elif tok.w in ("minus", "negative", "plus"):
            yield "sign", tok.w
        elif tok.w in _READBACK_FILLERS:
            yield "sep", None
        else:
            for piece in re.findall(r"\d+|[a-z]+", tok.w):
                yield ("num" if piece.isdigit() else "letter" if len(piece) == 1 else "word"), piece


def _readback_runs(text):
    """Complete readback values: signed decimal numbers, letter groups and words.

    "six point five" -> 6.5, "minus four five zero" -> -450, "kilo mike one two
    three four" -> km 1234; adjacent digits always join, so "4500" never equals 450.
    """
    runs, prev, pending = [], None, None
    for kind, value in _readback_atoms(text):
        if kind == "num":
            if prev == "num":
                runs[-1][1] += value
            elif prev == "point":
                runs[-1][1] += "." + value
            else:
                runs.append(["num", ("-" if prev == "sign" and pending != "plus" else "") + value])
            prev, pending = "num", None
            continue
        if pending:     # a sign or point word not followed by digits is an ordinary word
            runs.append(["word", pending])
            pending = None
        if kind == "point" and prev == "num" and "." not in runs[-1][1]:
            prev, pending = "point", value
        elif kind == "sign":
            prev, pending = "sign", value
        elif kind == "letter" and prev == "letter":
            runs[-1][1] += value
        elif kind == "sep":
            prev = "sep"
        else:
            runs.append(["letter" if kind == "letter" else "word", value])
            prev = "letter" if kind == "letter" else "word"
    if pending:
        runs.append(["word", pending])
    return [tuple(run) for run in runs]


def _same_number(spoken, value, key):
    try:
        heard = Decimal(spoken)
    except InvalidOperation:
        return False
    expected = Decimal(str(value))
    if key == "heading":
        return heard >= 0 and heard % 360 == expected % 360
    return heard == expected


def _readback_mismatch(text, task):
    """First readback field the transmission does not reproduce exactly, or None."""
    heard, position = _readback_runs(text), 0
    for key in task["readback"]:
        value = task["brief"][key]
        if key in NUMERIC_LINES:
            if position >= len(heard) or heard[position][0] != "num" \
                    or not _same_number(heard[position][1], value, key):
                return key
            position += 1
        else:
            expected = [run[1] for run in _readback_runs(value)]
            if [run[1] for run in heard[position:position + len(expected)]] != expected:
                return key
            position += len(expected)
    if position != len(heard) and task["readback"]:
        return task["readback"][-1]
    return None


def _say_digits(value):
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return " ".join("point" if c == "." else "minus" if c == "-" else domain._ONES[int(c)] for c in text)


# ---------------------------------------------------------------------------
# Bridge payload validation
# ---------------------------------------------------------------------------

def _num(value, lo, hi, integer=False):
    # Range check first: NaN and infinities fail it, and huge JSON integers never hit float().
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi \
            or (integer and value != int(value)):
        raise ValueError("invalid bridge number")
    return int(value) if integer else float(value)


def _text(value, limit=160):
    if not isinstance(value, str):
        raise ValueError("invalid bridge text")
    value = " ".join(value.split())
    if not 1 <= len(value) <= limit or not value.isprintable():
        raise ValueError("invalid bridge text")
    return value


def _list(value, limit):
    if value is None or value == {}:     # an empty Lua table may encode as {} or []
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("invalid bridge list")
    return value


def _bool(value):
    if not isinstance(value, bool):
        raise ValueError("invalid bridge flag")
    return value


def _command_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid command id")
    return value


def _laser_code(value):
    code = _num(value, 1111, 1788, integer=True)
    digits = str(code)
    if digits[0] != "1" or not "1" <= digits[1] <= "7" or not all("1" <= d <= "8" for d in digits[2:]):
        raise ValueError("invalid laser code")
    return code


def _spot(value):
    if value is None or value in ({}, []):
        return {"on": False, "command": None}
    if not isinstance(value, dict):
        raise ValueError("invalid spot")
    command = value.get("command")
    return {"on": _bool(value.get("on", False)), "command": None if command is None else _command_id(command)}


def _brief(raw):
    if raw is None or raw in ({}, []):
        raw = {}
    if not isinstance(raw, dict) or set(raw) - set(LINES) - {"type"}:
        raise ValueError("invalid brief")
    brief = {key: None for key in ("type", *LINES)}
    for key, value in raw.items():
        if value is None:
            continue
        if key == "type":
            brief[key] = _num(value, 1, 3, integer=True)
        elif key == "heading":
            brief[key] = _num(value, 0, 360, integer=True) % 360
        elif key == "distance":
            brief[key] = round(_num(value, 0, 100), 1)
        elif key == "elevation":
            brief[key] = _num(value, -1500, 30000, integer=True)
        else:
            brief[key] = _text(value, TEXT_LIMITS[key])
    return brief


def _task(raw):
    if not isinstance(raw, dict):
        raise ValueError("invalid task")
    controller, target = raw.get("controller"), raw.get("target")
    if not isinstance(controller, dict) or not isinstance(target, dict):
        raise ValueError("invalid task units")
    total = _num(target.get("total", 0), 0, 1000, integer=True)
    remaining = _num(target.get("remaining", 0), 0, total, integer=True)
    alive = _bool(target.get("alive"))
    destroyed = _bool(target.get("destroyed", False))
    position = None
    if alive:
        position = {"lat": _num(target.get("lat"), -90, 90), "lon": _num(target.get("lon"), -180, 180),
                    "alt": _num(target.get("alt", 0), -1000, 100000)}
    raw_marks = _list(raw.get("marks"), len(MARK_KINDS))
    marks = [m for m in raw_marks if isinstance(m, str) and m in MARK_KINDS]
    if len(marks) != len(raw_marks):
        raise ValueError("invalid mark kind")
    brief = _brief(raw.get("brief"))
    readback = _list(raw.get("readback"), len(LINES))
    if any(not isinstance(key, str) or key not in LINES or key == "offset" for key in readback):
        raise ValueError("invalid readback field")
    window = raw.get("attack_heading")
    if window is not None:
        window = _list(window, 2)
        if len(window) != 2:
            raise ValueError("invalid attack heading window")
        window = tuple(_num(v, 0, 360, integer=True) % 360 for v in window)
    code = raw.get("laser_code")
    return {
        "id": _num(raw.get("id"), 1, 99, integer=True),
        "name": _text(raw.get("name"), 40),
        "coalition": _num(raw.get("coalition"), 1, 2, integer=True),
        "controller": {"name": _text(controller.get("name"), 64), "alive": _bool(controller.get("alive"))},
        "target": {"alive": alive and remaining > 0, "total": total, "remaining": remaining,
                   "destroyed": destroyed and remaining == 0, "position": position},
        "brief": brief,
        "laser_code": None if code is None else _laser_code(code),
        "marks": marks,
        "readback": sorted(set(readback), key=lambda key: LINES[key]),
        "attack_heading": window,
        "require_mark": _bool(raw.get("require_mark", False)),
        "laser": _spot(raw.get("laser")),
        "ir": _spot(raw.get("ir")),
    }


def _heading_ok(heading, window):
    low, high = window
    return low <= heading <= high if low <= high else heading >= low or heading <= high


def _live(task):
    return task["target"]["alive"] and task["controller"]["alive"]


class _Session:
    __slots__ = ("key", "caller", "origin", "task", "phase", "contact", "cleared", "marked", "revision",
                 "touched", "cancel")

    def __init__(self, key, caller, revision):
        self.key, self.caller, self.origin, self.revision = key, caller, revision, revision
        self.task = None
        self.phase = "checked_in"      # checked_in -> assigned -> briefed -> readback
        self.contact = self.cleared = self.marked = False   # marked: acknowledged smoke/illumination
        self.touched = time.monotonic()
        self.cancel = None             # (revision, intent) of the latest abort/laser-off reply


class Controller:
    """JTAC procedure state for one SRS channel, synchronised with the DCS mission bridge."""

    commands = {
        "checkIn": "checking in",
        "requestTask": "request task",
        "requestBrief": "ready for brief",
        "contact": "contact",
        "requestMark": "request mark",
        "requestLaser": "request laser",
        "laserOff": "laser off",
        "abort": "abort",
        "attackComplete": "off target",
        "requestBda": "request bda",
        "status": "say status",
        "checkOut": "checking out",
    }

    def __init__(self, callsign="Axeman", channel="2:252000000:0", stale_seconds=10):
        match = _CHANNEL.fullmatch(channel or "")
        if not match:
            raise ValueError("JTAC channel must be coalition:frequency_hz:modulation")
        if not callsign or not re.fullmatch(r"[A-Za-z][A-Za-z ]{0,31}", callsign):
            raise ValueError("JTAC callsign must be letters")
        self.callsign, self.channel, self.coalition = callsign, channel, int(match.group(1))
        self.stale_seconds = stale_seconds
        self._key = _parser.norm(callsign).replace(" ", "")
        self._sound = _parser.phonetic(callsign)
        self._revisions = itertools.count(1)
        self._lock = threading.RLock()
        self._generation = None
        self._epoch, self._seq = None, 0
        self._retired = OrderedDict()   # epochs replaced by a newer mission start
        self.reset()

    # -- lifecycle --------------------------------------------------------

    def reset(self):
        """Forget the bridge snapshot, sessions and commands.

        The epoch/sequence replay boundary and retired epochs survive, so a reset
        never lets an old snapshot back in; the mission drops spots on its own lease.
        """
        with self._lock:
            self._model_time, self._received = 0.0, None
            self._tasks, self._error = {}, None
            self._drop_sessions()

    def _drop_sessions(self):
        self._sessions = {}
        self._outbox = OrderedDict()   # id -> command
        self._cancel = OrderedDict()   # id -> (time cancelled, task id, op, kind) until the mission acknowledges
        self._owners = OrderedDict()   # command id -> (session key, session origin)
        self._events = deque(maxlen=MAX_EVENTS)

    def reset_generation(self, generation):
        """Telemetry generation changed: unit ids are new, so close every session."""
        with self._lock:
            if self._generation is not None and generation != self._generation:
                for session in list(self._sessions.values()):
                    self._close(session)
            self._generation = generation

    @property
    def ready(self):
        with self._lock:
            return self._fresh()

    def _fresh(self):
        return self._received is not None and time.monotonic() - self._received <= self.stale_seconds

    # -- parsing ----------------------------------------------------------

    def _receiver_end(self, toks):
        for size in (3, 2, 1):
            words = toks[:size]
            if len(words) < size or any(t.n is not None for t in words):
                continue
            spoken = "".join(t.w for t in words)
            if spoken == self._key or (len(self._sound) >= 4 and _parser._edits(
                    _parser.phonetic(spoken), self._sound) <= (2 if len(self._sound) >= 6 else 1)):
                return size
        return 0

    def parse(self, text, units, coalition):
        """Side-effect-free parse; ``None`` unless the transmission starts with the JTAC callsign."""
        if not isinstance(text, str) or not text.strip() or len(text) > 2048:
            return None
        toks = _parser.tokenize(text)
        start = self._receiver_end(toks)
        if not start:
            return None
        while start < len(toks) and toks[start].w in ("this", "is", "from"):
            start += 1
        rest = toks[start:]
        offsets, pos = [], 0
        for tok in rest:
            offsets.append(pos)
            pos += len(tok.w) + 1
        joined = " ".join(t.w for t in rest)
        best = None
        for order, (intent, pattern) in enumerate(COMMANDS):
            found = pattern.search(joined)
            if found and (best is None or (found.start(), order) < best[:2]):
                best = (found.start(), order, intent, found.end())
        groups = domain._known_groups(units)
        result = {"text": text, "intent": "None", "receiver": {"kind": "jtac", "callsign": self.callsign},
                  "transmitter": None, "task": None, "line": None, "heading": None, "mark": None,
                  "readback": None, "blocked": False}
        if best is None:
            result["transmitter"] = _parser.parse_callsign(rest, False, groups)
            # Never let inference reinterpret a negated or cancelling utterance.
            result["blocked"] = bool(_NEGATION.search(joined) or _ABORT.search(joined))
            return result
        c_start = sum(1 for o in offsets if o < best[0])
        c_end = sum(1 for o in offsets if o < best[3])
        intent, after = best[2], rest[c_end:]
        # Readback content may legitimately contain these words, so only its prefix is scanned.
        scope = joined[:best[3]] if intent == "readback" else joined
        if intent in ("requestLaser", "requestMark") and _LASER_OFF.search(joined):
            intent = "laserOff"       # "request laser off" never selects a positive action
        elif intent in _POSITIVE and _ABORT.search(scope):
            intent = "abort"          # "request laser, abort"
        elif intent in _POSITIVE and _NEGATION.search(scope):
            intent = "None"           # "negative contact", "contact, negative", "no smoke"
            result["blocked"] = True
        result["intent"] = intent
        result["transmitter"] = _parser.parse_callsign(rest[:c_start], True, groups)
        numbers = [t for t in after if t.n is not None]
        if intent == "requestTask":
            result["task"] = numbers[0].n if numbers and 1 <= numbers[0].n <= 99 else None
        elif intent == "repeatLine":
            spoken = rest[c_start:c_end]
            if any(t.w == "remarks" for t in spoken):
                result["line"] = 10
            elif numbers and 1 <= numbers[0].n <= 9:
                result["line"] = numbers[0].n
        elif intent == "inbound":
            digits = ""
            for tok in after:
                if tok.n is None:
                    if digits:
                        break
                    continue
                digits += tok.d
            if digits and len(digits) <= 3 and int(digits) <= 360:
                result["heading"] = int(digits) % 360
        elif intent in ("requestMark", "requestLaser"):
            result["mark"] = "laser" if intent == "requestLaser" else next(
                (_MARK_WORDS[t.w] for t in rest[c_start:] if t.w in _MARK_WORDS and t.w != "laser"), None)
        elif intent == "readback":
            # Raw text after "readback": tokenizing drops the decimal point that "6.5" carries.
            raw = re.search(r"\bread[\s-]?back\b", text, re.IGNORECASE)
            result["readback"] = text[raw.end():] if raw else " ".join(t.w for t in after)
        return result

    # -- bridge -----------------------------------------------------------

    def exchange(self, payload):
        """Apply one hook snapshot plus acknowledgements and return pending commands."""
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("unsupported bridge payload")
        epoch = payload.get("epoch")
        if not isinstance(epoch, str) or not _ID.fullmatch(epoch):
            raise ValueError("invalid epoch")
        seq = _num(payload.get("seq"), 1, 2 ** 53, integer=True)
        model_time = _num(payload.get("time"), 0, 1e9)
        error = payload.get("error")
        error = None if error is None else _text(error, 240)
        tasks = {}
        for raw in _list(payload.get("tasks"), MAX_TASKS):
            task = _task(raw)
            if task["id"] in tasks:
                raise ValueError("duplicate task id")
            tasks[task["id"]] = task
        acks = []
        for raw in _list(payload.get("acks"), MAX_ACKS):
            if not isinstance(raw, dict) or not isinstance(raw.get("status"), str) \
                    or raw["status"] not in ACK_STATUSES:
                raise ValueError("invalid acknowledgement")
            reason = raw.get("reason")
            acks.append((_command_id(raw.get("id")), raw["status"], None if reason is None else _text(reason, 120)))
        with self._lock:
            if epoch in self._retired:
                return {"version": 1, "accepted": False, "error": "retired epoch", "epoch": epoch, "seq": seq,
                        "commands": [], "cancel": []}
            if epoch != self._epoch:
                if self._epoch is not None:
                    self._retired[self._epoch] = True
                    while len(self._retired) > MAX_RETIRED:
                        self._retired.popitem(last=False)
                self._drop_sessions()
                self._epoch, self._seq, self._tasks = epoch, 0, {}
            elif seq <= self._seq:
                return {"version": 1, "accepted": False, "error": "stale sequence", "epoch": epoch, "seq": seq,
                        "commands": [], "cancel": []}
            self._refresh()     # a stale gap invalidates procedure progress before the new timestamp hides it
            previous = self._tasks
            self._seq, self._model_time, self._received = seq, model_time, time.monotonic()
            self._tasks, self._error = tasks, error
            for command_id, status, reason in acks:
                self._acknowledge(command_id, status, reason)
            self._spot_changes(previous)
            self._refresh()
            return {"version": 1, "accepted": True, "epoch": epoch, "seq": seq,
                    "commands": self._pending_commands(), "cancel": list(self._cancel)}

    def _session_for(self, command_id):
        owner = self._owners.get(command_id)
        session = self._sessions.get(owner[0]) if owner else None
        return session if session and session.origin == owner[1] else None

    def _owns_spot(self, session, task, kind):
        state = task[kind]
        return session is not None and state["on"] and self._session_for(state["command"] or "") is session

    def _spot_in_flight(self, task_id):
        """A switch-on for this task may still execute: queued, or cancelled but not yet acknowledged."""
        return any(c["task"] == task_id and (c["op"] == "laser_on" or c["kind"] == "ir")
                   for c in self._outbox.values()) or any(
            entry[1] == task_id and (entry[2] == "laser_on" or entry[3] == "ir") for entry in self._cancel.values())

    def _acknowledge(self, command_id, status, reason):
        self._cancel.pop(command_id, None)
        command = self._outbox.pop(command_id, None)
        if command is None:
            return
        session = self._session_for(command_id)
        if session is None or session.task != command["task"] or status == "cancelled":
            return
        task = self._tasks.get(command["task"])
        kind = "laser" if command["op"] == "laser_on" else command["kind"]
        name = "sparkle" if kind == "ir" else kind
        if command["op"] == "laser_off":
            if command["spoken"]:
                self._event(session, "laser off" if status == "done" else f"unable laser off, {reason or status}")
        elif kind in SPOTS:
            if status == "done" and task and task[kind]["command"] == command_id and task[kind]["on"]:
                message = f"laser on, code {_say_digits(task['laser_code'])}" if kind == "laser" \
                    else "sparkle on the target"
                self._event(session, message, spot=(command_id, kind))
            else:
                self._event(session, f"unable {name}, {reason or ('spot not active' if status == 'done' else status)}")
        elif status == "done":
            session.marked = True
            self._event(session, f"{name} on the target", target=True)
        else:
            self._event(session, f"unable mark, {reason or status}")

    def _spot_changes(self, previous):
        """Report spots the mission terminated on its own (target or controller lost, lease expired)."""
        for task_id, old in previous.items():
            new = self._tasks.get(task_id)
            for kind in SPOTS:
                if old[kind]["on"] and not (new and new[kind]["on"]):
                    session = self._session_for(old[kind]["command"] or "")
                    if session and not any(c["task"] == task_id and c["op"] == "laser_off"
                                           for c in self._outbox.values()):
                        self._event(session, "spot terminated" if kind == "laser" else "sparkle terminated")

    def _pending_commands(self):
        now = time.monotonic()
        live = {session.task: session for session in self._sessions.values() if session.task is not None}
        # Spots not owned by a live session holding that task are always switched off.
        for task in self._tasks.values():
            if any(task[kind]["on"] and not self._owns_spot(live.get(task["id"]), task, kind) for kind in SPOTS):
                self._queue(None, task["id"], "laser_off")
        out = []
        for command_id, command in list(self._outbox.items()):
            remaining = command["expires"] - now
            if remaining <= 0:
                self._expire(command_id)
                continue
            item = {"id": command_id, "op": command["op"], "task": command["task"],
                    "deadline": round(self._model_time + remaining, 3)}
            if command["op"] == "mark":
                item["kind"] = command["kind"]
            out.append(item)
        return out

    def _expire(self, command_id):
        command = self._outbox.pop(command_id)
        session = self._session_for(command_id)
        self._cancel_id(command_id, command)
        if session and command["op"] != "laser_off":
            self._event(session, f"unable {'laser' if command['op'] == 'laser_on' else 'mark'}, no acknowledgement")

    def _cancel_id(self, command_id, command):
        """Cancel a command; a spot it still creates late has no owner and is switched off."""
        self._owners.pop(command_id, None)
        if command_id not in self._cancel:
            if len(self._cancel) >= MAX_OUTBOX:
                self._cancel.popitem(last=False)
            self._cancel[command_id] = (time.monotonic(), command["task"], command["op"], command["kind"])

    def _queue(self, session, task_id, op, kind=None, spoken=False):
        for command_id, command in self._outbox.items():
            if command["task"] == task_id and command["op"] == op and command["kind"] == kind:
                command["spoken"] = command["spoken"] or spoken
                return command_id
        if op == "laser_off":   # an off request supersedes queued switch-ons
            for command_id in [i for i, c in self._outbox.items()
                               if c["task"] == task_id and (c["op"] == "laser_on" or c["kind"] == "ir")]:
                self._cancel_id(command_id, self._outbox.pop(command_id))
        while len(self._outbox) >= MAX_OUTBOX:
            command_id, command = self._outbox.popitem(last=False)
            self._cancel_id(command_id, command)
        command_id = uuid.uuid4().hex
        self._outbox[command_id] = {"op": op, "task": task_id, "kind": kind, "spoken": spoken,
                                    "expires": time.monotonic() + COMMAND_TTL}
        if session is not None:
            if len(self._owners) >= 4 * MAX_OUTBOX:
                self._owners.popitem(last=False)
            self._owners[command_id] = (session.key, session.origin)
        return command_id

    # -- sessions ---------------------------------------------------------

    def _bump(self, session):
        session.revision = next(self._revisions)
        session.touched = time.monotonic()

    def _cancel_session_commands(self, session):
        for command_id in list(self._outbox):
            if self._session_for(command_id) is session and self._outbox[command_id]["op"] != "laser_off":
                self._cancel_id(command_id, self._outbox.pop(command_id))

    def _stop_spots(self, session, task):
        """Cancel the session's pending work and switch off any spot of the task that is or may become live."""
        in_flight = self._spot_in_flight(task["id"]) if task else False
        self._cancel_session_commands(session)
        if task and (in_flight or any(task[kind]["on"] for kind in SPOTS)):
            self._queue(session, task["id"], "laser_off")
            return True
        return False

    def _release_task(self, session):
        self._cancel_session_commands(session)
        session.task, session.phase = None, "checked_in"
        session.contact = session.cleared = session.marked = False

    def _close(self, session):
        self._release_task(session)
        self._sessions.pop(session.key, None)
        self._bump(session)   # orphaned spots are switched off by the next exchange

    def _invalidate(self, session, task):
        """Target/controller loss or a stale bridge: restart the procedure from a new brief."""
        pending = any(self._session_for(command_id) is session and command["op"] != "laser_off"
                      for command_id, command in self._outbox.items())
        if session.phase in ("briefed", "readback") or session.contact or session.cleared or session.marked \
                or pending:
            self._stop_spots(session, task)
            session.phase = "assigned"
            session.contact = session.cleared = session.marked = False
            self._bump(session)

    def _refresh(self):
        now = time.monotonic()
        fresh = self._fresh()
        for session in list(self._sessions.values()):
            if now - session.touched > SESSION_IDLE:
                self._close(session)
                continue
            if session.task is None:
                continue
            task = self._tasks.get(session.task)
            if task is None or task["coalition"] != session.key[0]:
                self._release_task(session)
                self._bump(session)
            elif not (fresh and _live(task)):
                self._invalidate(session, task)
            elif session.cleared and not self._mark_ok(session, task):
                session.cleared = False     # the live mark this clearance depended on is gone
                self._bump(session)

    def _mark_ok(self, session, task):
        """Mission-required mark: acknowledged smoke/illumination, or this session's spot still live."""
        return not task["require_mark"] or session.marked or any(self._owns_spot(session, task, k) for k in SPOTS)

    def _available(self, coalition, session):
        taken = {s.task for s in self._sessions.values() if s is not session and s.task is not None}
        return [task for task_id, task in sorted(self._tasks.items())
                if task["coalition"] == coalition and task_id not in taken and _live(task)]

    def _event(self, session, message, target=False, spot=None):
        coalition, unit_id = session.key
        self._events.append({"reply": domain._speech(session.caller, self.callsign, message), "role": "jtac",
                             "unit_id": unit_id, "coalition": coalition, "channel": self.channel,
                             "expires": time.time() + EVENT_TTL,
                             "validation": self._validation(session, target or spot is not None, spot)})

    def take_events(self, coalition, channel):
        """Asynchronous DCS acknowledgement notifications for the JTAC channel."""
        with self._lock:
            if channel != self.channel or coalition != self.coalition:
                return []
            now = time.time()
            out = [event for event in self._events if event["coalition"] == coalition and event["expires"] > now]
            self._events = deque((e for e in self._events if e["coalition"] != coalition and e["expires"] > now),
                                 maxlen=MAX_EVENTS)
            return out

    def _validation(self, session, target, spot=None, cancel=None):
        coalition, unit_id = session.key
        return {"role": "jtac", "channel": self.channel, "coalition": coalition, "unit_id": unit_id,
                "epoch": self._epoch, "generation": self._generation, "revision": session.revision,
                "target": target, "spot": spot[0] if spot else None, "spot_kind": spot[1] if spot else None,
                "safety_cancel": cancel is not None, "action": cancel}

    def validate_reply(self, validation):
        """True while a synthesised reply still describes current procedure and mission state."""
        if not isinstance(validation, dict) or validation.get("role") != "jtac":
            return False
        with self._lock:
            self._refresh()
            if validation.get("channel") != self.channel or validation.get("coalition") != self.coalition \
                    or validation.get("epoch") != self._epoch or validation.get("generation") != self._generation \
                    or not self._fresh():
                return False
            session = self._sessions.get((validation.get("coalition"), validation.get("unit_id")))
            if session is None or session.revision != validation.get("revision"):
                return False
            if validation.get("safety_cancel") is True:
                # Only the cancellation reply issued at exactly this revision qualifies.
                action = validation.get("action")
                return action in ("abort", "laserOff") and session.cancel == (session.revision, action) \
                    and not validation.get("target") and validation.get("spot") is None
            task = self._task_of(session)
            spot, kind = validation.get("spot"), validation.get("spot_kind")
            if spot is not None and not (kind in SPOTS and task and task[kind]["on"]
                                         and task[kind]["command"] == spot):
                return False
            return not validation.get("target") or bool(task and _live(task))

    # -- radio ------------------------------------------------------------

    def respond(self, text, coalition, channel, units, generation):
        """Answer one transmission heard on ``channel``; other channels and coalitions are ignored."""
        if channel != self.channel:
            return {"reply": None, "parsed": None, "notes": "Not the JTAC frequency"}
        if coalition != self.coalition:
            return {"reply": None, "parsed": None, "notes": "Coalition does not match the JTAC frequency"}
        if not isinstance(text, str) or len(text) > 2048:
            raise ValueError("invalid text")
        parsed = self.parse(text, units, coalition)
        if parsed is None:
            return {"reply": None, "parsed": None, "notes": "Transmission is not directed to the JTAC"}
        with self._lock:
            self.reset_generation(generation)
            live = {unit.get("id") for unit in units if unit.get("coalition") == coalition}
            for session in list(self._sessions.values()):
                if session.key[1] not in live:
                    self._close(session)
            self._refresh()
            reply = domain._Reply()
            unit = domain._determine_transmitter(parsed, reply, units, coalition)
            reply.responder = self.callsign
            if unit is None:
                return {"reply": reply.speech(), "parsed": parsed, "notes": reply.notes}
            key = (coalition, unit["id"])
            session = self._sessions.get(key)
            intent = parsed["intent"]
            answer = {"parsed": parsed, "notes": None}
            if intent == "None":
                reply.message = "say again"
            elif intent == "checkOut":
                if session:
                    self._close(session)
                reply.message = "copy, checked out"
            elif not self._fresh():
                answer["notes"] = "JTAC bridge snapshot missing or stale"
                if session and intent in ("abort", "laserOff"):
                    # Local cancellation still applies so a reconnect cannot run old work.
                    self._bump(session)
                    reply.message = getattr(self, "_" + intent)(session, parsed)[0] + ", no live mission link"
                else:
                    reply.message = "unable, no live mission link"
            elif intent == "checkIn":
                if session:
                    self._close(session)
                if len(self._sessions) >= MAX_SESSIONS:
                    self._close(min(self._sessions.values(), key=lambda s: s.touched))
                session = self._sessions[key] = _Session(key, reply.caller, next(self._revisions))
                reply.message = self._check_in(coalition, session)
                answer["validation"] = self._validation(session, False)
            elif session is None:
                reply.message = "unable, check in first"
            else:
                self._bump(session)
                message, target = getattr(self, "_" + intent)(session, parsed)
                reply.message = message
                if key in self._sessions and intent in ("abort", "laserOff"):
                    session.cancel = (session.revision, intent)
                    answer["validation"] = self._validation(session, False, cancel=intent)
                elif key in self._sessions:
                    answer["validation"] = self._validation(session, target)
            answer["reply"] = reply.speech()
            return answer

    def _task_list(self, tasks):
        names, size = [], 0
        for task in tasks:
            name = f"task {domain.number_to_words(task['id'])}, {task['name']}"
            if size + len(name) > MAX_LIST_CHARS:
                names.append(f"and {domain.number_to_words(len(tasks) - len(names))} more")
                break
            names.append(name)
            size += len(name) + 2
        return ", ".join(names)

    def _check_in(self, coalition, session):
        tasks = self._available(coalition, session)
        if not tasks:
            return "checked in, no tasks available"
        return f"checked in, available {self._task_list(tasks)}, request task when ready"

    def _task_of(self, session):
        return self._tasks.get(session.task) if session.task is not None else None

    def _requestTask(self, session, parsed):
        tasks = self._available(session.key[0], session)
        wanted = parsed["task"]
        if wanted is not None:
            chosen = next((t for t in tasks if t["id"] == wanted), None)
            if chosen is None:
                return f"unable, task {domain.number_to_words(wanted)} not available", False
        elif len(tasks) == 1:
            chosen = tasks[0]
        elif not tasks:
            return "no tasks available", False
        else:
            return f"say task number, available {self._task_list(tasks)}", False
        if session.task != chosen["id"]:
            previous = self._task_of(session)
            if previous:
                self._stop_spots(session, previous)
            self._release_task(session)
            session.task, session.phase = chosen["id"], "assigned"
        return f"task {domain.number_to_words(chosen['id'])}, {chosen['name']}, advise ready for brief", True

    def _line(self, task, number):
        b = task["brief"]
        if number == 1:
            return f"line one, IP {b['ip']}"
        if number == 2:
            return f"line two, heading {_say_digits(b['heading'])}" + (f", {b['offset']}" if b["offset"] else "")
        if number == 3:
            return f"line three, distance {_say_digits(b['distance'])}"
        if number == 4:
            return f"line four, elevation {_say_digits(b['elevation'])} feet"
        if number == 5:
            return f"line five, {b['description']}"
        if number == 6:
            return f"line six, {b['location']}"
        if number == 7:
            return f"line seven, mark {b['mark']}"
        if number == 8:
            return f"line eight, friendlies {b['friendlies']}"
        if number == 9:
            return f"line nine, egress {b['egress']}"
        return f"remarks, {b['remarks']}" if b["remarks"] else "no remarks"

    def _requestBrief(self, session, parsed):
        task = self._task_of(session)
        if task is None:
            return "unable, request task first", False
        if not _live(task):
            return "unable, target or controller not available", False
        missing = [key for key in REQUIRED_LINES if task["brief"][key] is None]
        if missing:
            where = "control type" if missing[0] == "type" else f"line {domain.number_to_words(LINES[missing[0]])}"
            return f"unable, task brief incomplete, missing {where}", False
        session.phase = "briefed"
        session.contact = session.cleared = False
        lines = [self._line(task, n) for n in range(1, 10)]
        if task["brief"]["remarks"]:
            lines.append(self._line(task, 10))
        readback = ", ".join("remarks" if LINES[k] == 10 else f"line {domain.number_to_words(LINES[k])}"
                             for k in task["readback"])
        tail = f"read back {readback}" if readback else "report contact"
        if not task["readback"]:
            session.phase = "readback"
        brief = f"type {domain.number_to_words(task['brief']['type'])} control, " + ", ".join(lines) + f", {tail}"
        if len(domain._speech(session.caller, self.callsign, brief)) > MAX_REPLY_CHARS:
            return f"brief exceeds one transmission, request lines one through nine individually, then {tail}", True
        return brief, True

    def _repeatLine(self, session, parsed):
        task = self._task_of(session)
        if task is None or session.phase in ("checked_in", "assigned"):
            return "unable, no brief issued", False
        if parsed["line"] is None:
            return "say line number", False
        return self._line(task, parsed["line"]), True

    def _readback(self, session, parsed):
        task = self._task_of(session)
        if task is None or session.phase in ("checked_in", "assigned"):
            return "unable, no brief issued", False
        mismatch = _readback_mismatch(parsed["readback"] or "", task)
        if mismatch:
            session.phase, session.contact, session.cleared = "briefed", False, False
            return f"negative readback, {self._line(task, LINES[mismatch])}", True
        session.phase = "readback"
        return "readback correct, report contact", True

    def _contact(self, session, parsed):
        task = self._task_of(session)
        if task is None or session.phase != "readback":
            return "unable, readback required", False
        if not _live(task):
            return "target or controller not available, request BDA", False
        session.contact = True
        return "copy contact, call in with heading" if task["attack_heading"] else "copy contact, call in", True

    def _inbound(self, session, parsed):
        task = self._task_of(session)
        reason = None
        if task is None:
            reason = "no task assigned"
        elif session.phase != "readback":
            reason = "readback required"
        elif not session.contact:
            reason = "report contact"
        elif not task["target"]["alive"]:
            reason = "target not active"
        elif not task["controller"]["alive"]:
            reason = "controller not available"
        elif task["attack_heading"] and parsed["heading"] is None:
            reason = "say heading"
        elif task["attack_heading"] and not _heading_ok(parsed["heading"], task["attack_heading"]):
            low, high = task["attack_heading"]
            reason = f"attack heading {_say_digits(low)} to {_say_digits(high)} only"
        elif not self._mark_ok(session, task):
            reason = "mark not confirmed"
        if reason:
            session.cleared = False
            return f"continue, no clearance, {reason}", False
        session.cleared = True
        return ("cleared to engage" if task["brief"]["type"] == 3 else "cleared hot"), True

    def _requestMark(self, session, parsed):
        task = self._task_of(session)
        if task is None or session.phase in ("checked_in", "assigned"):
            return "unable, no brief issued", False
        kind = parsed["mark"] or next((m for m in task["marks"] if m != "laser"), None) or (
            "laser" if "laser" in task["marks"] else None)
        if kind not in task["marks"]:
            return f"unable, {kind or 'mark'} not available for this task", False
        if not _live(task):
            return "unable, target or controller not available", False
        if kind == "laser":
            if task["laser_code"] is None:
                return "unable, no laser code assigned", False
            if self._owns_spot(session, task, "laser"):
                return f"laser on, code {_say_digits(task['laser_code'])}", True
            self._queue(session, task["id"], "laser_on")
            return f"stand by, laser code {_say_digits(task['laser_code'])} pending", True
        if kind == "ir" and self._owns_spot(session, task, "ir"):
            return "sparkle on the target", True
        self._queue(session, task["id"], "mark", kind=kind)
        return f"stand by, {'sparkle' if kind == 'ir' else kind} pending", True

    _requestLaser = _requestMark

    def _laserOff(self, session, parsed):
        task = self._task_of(session)
        if task is None:
            return "unable, no task assigned", False
        if self._stop_spots(session, task):
            self._queue(session, task["id"], "laser_off", spoken=True)
            return "copy, laser off pending", False
        return "laser is off", False

    def _abort(self, session, parsed):
        stopping = self._stop_spots(session, self._task_of(session))
        session.cleared = session.contact = False
        return ("copy abort, laser off pending" if stopping else "copy abort"), False

    def _attackComplete(self, session, parsed):
        cleared, session.cleared = session.cleared, False
        return ("copy off, request BDA when ready" if cleared else "copy off, no clearance was active"), False

    def _requestBda(self, session, parsed):
        task = self._task_of(session)
        if task is None:
            return "unable, no task assigned", False
        target = task["target"]
        if target["destroyed"]:
            return "BDA, target destroyed", False
        if target["remaining"] == 0:
            return "BDA unconfirmed, target not observed", False
        return (f"BDA, {domain.number_to_words(target['remaining'])} of "
                f"{domain.number_to_words(target['total'])} target elements remain"), False

    def _status(self, session, parsed):
        task = self._task_of(session)
        if task is None:
            return "checked in, no task assigned", False
        parts = [f"task {domain.number_to_words(task['id'])}, {task['name']}",
                 {"assigned": "awaiting brief", "briefed": "awaiting readback",
                  "readback": "readback complete"}[session.phase]]
        if session.contact:
            parts.append("contact")
        if session.cleared:
            parts.append("cleared")
        if task["laser"]["on"]:
            parts.append(f"laser on code {_say_digits(task['laser_code'])}" if task["laser_code"] else "laser on")
        elif any(c["task"] == task["id"] and c["op"] == "laser_on" for c in self._outbox.values()):
            parts.append("laser pending")
        if task["ir"]["on"]:
            parts.append("sparkle on")
        if not task["target"]["alive"]:
            parts.append("target not active")
        if not task["controller"]["alive"]:
            parts.append("controller not available")
        return ", ".join(parts), False
