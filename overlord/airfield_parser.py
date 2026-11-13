"""Side-effect-free grammar for native airfield radio calls (ground, tower, approach).

A call opens with receiver words: a spoken name of an airfield published on the
radio ("Kutaisi", "Krasnodar Center", a native radio alias) and/or a controller role
("ground", "tower", "approach"), then the caller's callsign ("Viper 1 1") or modex,
then one call.  Airfield names are supplied per radio by the controller: nothing
here knows a fixed list of airfields.

The earliest command phrase wins; on an equal start the earlier table entry wins.
Calls that advance procedure are voided by a negation in the transmission
("not ready for takeoff") and turned into a cancel by an explicit cancel word.
For emergencies and readbacks only words *before* the keyword count, so an
emergency reason ("mayday, aborting takeoff", "gear not down") never cancels
anything, while "cancel emergency" / "negative emergency" only ends the emergency.
Readbacks need the explicit word "readback" ("read back runway two seven, hold
short"); their content is checked by the controller, value by value.
"""
import re

from . import parser as _parser

ROLES = {"ground": "ground", "tower": "tower", "approach": "approach"}
_NAME_NOISE = {"airfield", "airport", "airbase", "air", "base", "ab", "afb", "intl", "international",
               "field", "the", "of", "airstrip", "executive", "island", "ground", "tower", "approach"}
_EMERGENCY_WORDS = r"(?:emergency|mayday|pan pan)"

COMMANDS = [
    ("cancelEmergency", re.compile(
        r"\b(?:cancel(?:led|ling|s)?|terminat(?:e|ed|ing)|no longer|negative|not(?: an?)?|no|belay)"
        rf" (?:declaring )?(?:the |an |my |our )?{_EMERGENCY_WORDS}\b"
        rf"|\b{_EMERGENCY_WORDS} (?:cancel(?:led)?|terminated|over)\b")),
    ("emergency", re.compile(r"\bmayday\b|\bpan pan\b|\bemergency\b|\blow fuel\b|\bminimum fuel\b|\bbingo\b"
                             r"|\b(?:abort|reject)(?:ing|ed)? (?:the )?take ?off\b")),
    ("checkOut", re.compile(r"\bcheck(?:ing)? out\b|\bshutting down\b|\bshut ?down\b|\bparked\b"
                            r"|\bin (?:the )?(?:parking|chocks)\b")),
    ("cancel", re.compile(r"\b(?:cancel(?:led|ling|s)?|abort(?:ing|ed)?|belay)\b")),
    ("goAround", re.compile(r"\bgo(?:ing)? around\b|\bmissed approach\b")),
    ("readback", re.compile(r"\bread ?back\b|\breading back\b")),
    ("sayAgain", re.compile(r"\bsay again\b|\brepeat\b|\b(?:say|request(?:ing)?) (?:status|instructions)\b")),
    ("information", re.compile(
        r"\b(?:request(?:ing)?|say|what is|whats|check) (?:the )?(?:current )?(?:wind|winds|weather|altimeter"
        r"|qnh|active(?: runway)?|runway in use|information|airfield status|field status)\b|\bradio check\b")),
    ("taxiIn", re.compile(r"\b(?:request(?:ing)? )?taxi (?:back )?(?:to|for) (?:the )?(?:parking|ramp|apron|stand"
                          r"|chocks)\b|\brequest(?:ing)? parking\b")),
    ("startup", re.compile(r"\brequest(?:ing)? (?:start ?up|engine start)\b|\bready (?:to|for) start(?: ?up)?\b")),
    ("taxi", re.compile(r"\brequest(?:ing)? taxi\b|\bready (?:to|for) taxi\b")),
    ("crossing", re.compile(r"\brequest(?:ing)? (?:to )?cross(?:ing)?\b")),
    ("vacated", re.compile(r"\bvacated\b|\bclear of (?:the )?runway\b|\boff the runway\b")),
    ("holdingShort", re.compile(r"\bholding short\b|\b(?:at|approaching) (?:the )?holding point\b"
                                r"|\bready (?:for )?(?:departure|take ?off)\b")),
    ("linedUp", re.compile(r"\blined up\b|\bin position\b")),
    ("airborne", re.compile(r"\bairborne\b")),
    ("handoff", re.compile(r"\bswitching\b|\brequest(?:ing)? (?:switch|handoff|frequency change)\b"
                           r"|\bleaving (?:the )?(?:zone|frequency|pattern|control zone)\b")),
    ("inbound", re.compile(r"\binbound\b|\bcheck(?:ing)? in\b|\brequest(?:ing)? (?:landing|approach|recovery"
                           r"|full stop)\b|\bfor landing\b")),
    ("downwind", re.compile(r"\bdownwind\b")),
    ("base", re.compile(r"\bbase\b")),
    ("final", re.compile(r"\bfinal\b")),
    ("landed", re.compile(r"\btouch(?:ed)? ?down\b|\blanded\b")),
]
INTENTS = tuple(name for name, _ in COMMANDS)
# Calls that never advance procedure: no negation turns them into something else.
_NEUTRAL = {"cancelEmergency", "checkOut", "cancel", "sayAgain", "information"}
CANCEL = dict(COMMANDS)["cancel"]
NEGATION = re.compile(r"\b(?:no|not|negative|dont|do not|unable|never|cannot|cant)\b")
_SIDES = {"left": "L", "right": "R", "center": "C", "centre": "C", "l": "L", "r": "R", "c": "C"}
_MODEX_WORDS = {"oh": "0"}


def name_keys(name):
    """Spoken word tuples naming an airfield: the whole name and each distinctive word."""
    keys = set()
    for text in (name or "", _parser.strip_airbase_suffixes(name or "")):
        words = tuple(re.findall(r"[a-z0-9]+", _parser.norm(text)))
        words = tuple(w for w in words if w not in ("ab", "afb", "intl"))
        if words:
            keys.add(words)
            keys.update((w,) for w in words if len(w) >= 4 and w not in _NAME_NOISE and not w.isdigit())
    return keys


def designator(number, side=None):
    """Normalised runway designator ("09", "27L") or None for an impossible number."""
    if not isinstance(number, int) or not 1 <= number <= 36:
        return None
    return f"{number:02d}{side or ''}"


def runway_mentions(toks):
    """Runway designators labelled by the word "runway" ("runway two seven left", "runway 09L")."""
    found = []
    for index, tok in enumerate(toks):
        if tok.w != "runway":
            continue
        digits, side, j = "", None, index + 1
        while j < len(toks) and toks[j].n is not None and len(digits) < 2:
            digits += toks[j].d
            j += 1
        if not digits and j < len(toks):
            match = re.fullmatch(r"(\d{1,2})([lrc])", toks[j].w)
            if match:
                digits, side = match.group(1), _SIDES[match.group(2)]
                j += 1
        elif j < len(toks) and toks[j].w in _SIDES and len(toks[j].w) > 1:
            side = _SIDES[toks[j].w]
        if digits and len(digits) <= 2:
            value = designator(int(digits), side)
            if value:
                found.append(value)
    return found


def _match_name(toks, start, names):
    """(airfield ids, tokens used) of the longest exact name at ``start``, else a unique sound-alike word."""
    best, used = set(), 0
    for fid, keys in names.items():
        for key in keys:
            size = len(key)
            if size >= used and start + size <= len(toks) \
                    and all(toks[start + k].n is None and toks[start + k].w == key[k] for k in range(size)):
                if size > used:
                    best, used = set(), size
                best.add(fid)
    if used or toks[start].n is not None:
        return best, used
    sound = _parser.phonetic(toks[start].w)
    for fid, keys in names.items():
        if any(len(key) == 1 and len(key[0]) >= 6 and _parser._edits(sound, _parser.phonetic(key[0])) <= 1
               for key in keys):
            best.add(fid)
    return best, (1 if best else 0)


def _modex(region):
    digits = ""
    for tok in region:
        if tok.n is not None:
            digits += tok.d
        elif tok.w in _MODEX_WORDS:
            digits += _MODEX_WORDS[tok.w]
        else:
            return None
    return digits if 2 <= len(digits) <= 4 else None


def _unresolved_prefix(region, transmitter):
    """Words between the receiver and the callsign that are neither filler nor the callsign group:
    an unrecognised receiver ("Tower Mapel Viper 1 1") must not be dropped as caller noise."""
    if not transmitter or transmitter.get("group") is None:
        return False
    words = []
    for tok in region:
        if tok.n is not None:
            break
        if tok.w not in _parser.FILLERS and tok.w not in _parser.HESITATIONS and tok.w != _parser.FREQUENCY:
            words.append(tok.w)
    two = "".join(words[-2:])
    size = 2 if len(words) >= 2 and _parser.CALLSIGN_ALIASES.get(two, two) == transmitter["group"] else 1
    return len(words) > size


def parse(text, names, known_groups=None):
    """Parsed call, or ``None`` when the transmission does not open with an airfield receiver.

    ``names``: airfield id -> spoken word tuples (see ``name_keys``) of every known airfield.
    ``receiver.fields`` is a JSON-safe, sorted list of every field the spoken name could be;
    more than one means the name is ambiguous.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        return None
    toks = _parser.tokenize(text)
    start, role, fields, named = 0, None, set(), False
    while start < len(toks) and start < 8:
        tok = toks[start]
        if tok.n is None and tok.w in ROLES:
            role = role or ROLES[tok.w]
            start += 1
            continue
        matched, used = _match_name(toks, start, names)
        if not used:
            break
        fields |= matched
        named = True
        start += used
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
    result = {"text": text, "intent": "None",
              "receiver": {"kind": "airfield", "role": role, "fields": sorted(fields), "named": named},
              "transmitter": None, "modex": None, "readback": None, "blocked": False,
              "runway": None}
    if best is None:
        region = rest
        result["blocked"] = bool(NEGATION.search(joined) or CANCEL.search(joined))
    else:
        region = rest[:sum(1 for o in offsets if o < best[0])]
        intent = best[2]
        scope = joined[:best[0]] if intent in ("readback", "emergency") else joined
        if intent not in _NEUTRAL:
            if CANCEL.search(scope):
                intent = "cancel"
            elif NEGATION.search(scope):
                intent, result["blocked"] = "None", True
        result["intent"] = intent
        if intent == "readback":
            # Raw text after the keyword: tokenizing would turn "29.92" into a frequency marker.
            raw = re.search(r"\bread(?:ing)?[\s-]?back\b", text, re.IGNORECASE)
            result["readback"] = text[raw.end():] if raw else ""
        mentions = runway_mentions(rest[sum(1 for o in offsets if o < best[0]):])
        result["runway"] = mentions[0] if mentions else None
    result["modex"] = _modex(region)
    if result["modex"] is None:
        result["transmitter"] = _parser.parse_callsign(region, False, known_groups)
        result["receiver"]["unresolved"] = _unresolved_prefix(region, result["transmitter"])
    else:
        result["receiver"]["unresolved"] = False
    return result
