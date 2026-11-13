"""Side-effect-free grammar for carrier radio calls on one ship's mission frequency.

Calls must open with a receiver word of that carrier ("marshal", "tower", "paddles",
"departure", "approach", "boss", "mother", "carrier" or a word of the ship's
mission name), then the caller's callsign ("Viper 1 1") or modex ("two zero one",
"201", "zero one zero"; leading zeros are part of the modex), then one call.

The earliest command phrase wins; on an equal start the earlier table entry wins.
Positive calls that advance procedure are voided by any negation in the
transmission ("negative established") and turned into a cancel or wave-off by an
explicit cancel/abort or wave-off word anywhere ("ball, wave off" waves off).
"""
import re

from . import parser as _parser

ROLES = {"marshal": "marshal", "marshall": "marshal", "tower": "tower", "paddles": "paddles",
         "paddle": "paddles", "lso": "paddles", "departure": "departure", "approach": "approach",
         "boss": "tower", "strike": "marshal", "mother": None, "carrier": None}
_NAME_NOISE = {"cvn", "cv", "lha", "lhd", "uss", "ship", "group", "carrier", "the", "of"}

COMMANDS = [
    ("checkOut", re.compile(r"\bcheck(?:ing)? out\b")),
    ("cancel", re.compile(r"\b(?:cancel(?:led|ling|s)?|abort(?:ing|ed)?|belay)\b")),
    ("waveoff", re.compile(r"\bwav(?:e|ing) ?offs?\b")),
    ("readback", re.compile(r"\bread ?back\b|\breading back\b")),
    ("sayAgain", re.compile(r"\bsay again\b|\brepeat\b|\b(?:say|request(?:ing)?) (?:status|instructions)\b")),
    ("emergency", re.compile(r"\bmayday\b|\bpan pan\b|\bemergency\b|\blow (?:fuel|state)\b|\bbingo\b"
                             r"|\bminimum fuel\b")),
    ("departure", re.compile(r"\brequest(?:ing)? (?:departure|launch|startup|start up)\b"
                             r"|\bready (?:for|to) (?:launch|departure|depart)\b|\bdeparture request\b")),
    ("checkIn", re.compile(r"\bcheck(?:ing)? in\b|\binbound\b"
                           r"|\brequest(?:ing)? (?:recovery|landing|marshal|case)\b")),
    ("established", re.compile(r"\bestablished\b")),
    ("commencing", re.compile(r"\bcommenc(?:e|ing)\b|\bleaving (?:marshal|the stack)\b")),
    ("platform", re.compile(r"\bplatform\b")),
    ("initial", re.compile(r"\binitial\b")),
    ("break", re.compile(r"\bbreak(?:ing)?\b")),
    ("downwind", re.compile(r"\babeam\b|\bdownwind\b")),
    ("final", re.compile(r"\b(?:ten|10) miles?\b|\bsee me\b|\bdirty up\b|\bfinal\b")),
    ("clara", re.compile(r"\bclara\b|\b(?:no|negative) ball\b")),
    ("ball", re.compile(r"\bball\b")),
    ("bolter", re.compile(r"\bbolter(?:ing)?\b")),
    ("deckClear", re.compile(r"\bdeck clear\b|\bclear (?:of )?(?:the )?(?:landing area|angle|deck|foul line)\b")),
    ("trapped", re.compile(r"\btrapped\b|\btrap\b|\bon deck\b")),
    ("airborne", re.compile(r"\bairborne\b")),
    ("handoff", re.compile(r"\bdeparture complete\b|\bswitching\b"
                           r"|\brequest(?:ing)? (?:switch|handoff|frequency change)\b")),
]
INTENTS = tuple(name for name, _ in COMMANDS)
_NEUTRAL = {"checkOut", "cancel", "waveoff", "sayAgain", "clara"}
_CANCEL = dict(COMMANDS)["cancel"]
_WAVEOFF = dict(COMMANDS)["waveoff"]
_NEGATION = re.compile(r"\b(?:no|not|negative|dont|do not|unable|never)\b")
_MODEX_WORDS = {"oh": "0"}


def name_words(name):
    """Spoken receiver words of a mission ship name ("CVN-71 Theodore Roosevelt")."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _NAME_NOISE}


def _receiver_word(tok, names):
    if tok.n is not None:
        return False
    if tok.w in ROLES or tok.w in names:
        return True
    sound = _parser.phonetic(tok.w)
    return any(len(name) >= 6 and _parser._edits(sound, _parser.phonetic(name)) <= 1 for name in names)


def _modex(region):
    """Digits-only caller region ("two zero one", "201", "zero one zero"), else None."""
    digits = ""
    for tok in region:
        if tok.n is not None:
            digits += tok.d
        elif tok.w in _MODEX_WORDS:
            digits += _MODEX_WORDS[tok.w]
        else:
            return None
    return digits if 2 <= len(digits) <= 4 else None


def parse(text, names=(), known_groups=None):
    """Parsed call or ``None`` when the transmission is not addressed to this carrier."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        return None
    toks = _parser.tokenize(text)
    names = set(names)
    start, role = 0, None
    while start < min(len(toks), 3) and _receiver_word(toks[start], names):
        role = role or ROLES.get(toks[start].w)
        start += 1
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
    result = {"text": text, "intent": "None", "receiver": {"kind": "carrier", "role": role},
              "transmitter": None, "modex": None, "readback": None, "blocked": False,
              "startup": bool(re.search(r"\bstart ?up\b", joined))}
    if best is None:
        region = rest
        result["blocked"] = bool(_NEGATION.search(joined) or _CANCEL.search(joined) or _WAVEOFF.search(joined))
    else:
        region = rest[:sum(1 for o in offsets if o < best[0])]
        intent = best[2]
        scope = joined[:best[3]] if intent in ("readback", "emergency") else joined
        if intent not in _NEUTRAL:
            if _CANCEL.search(scope):
                intent = "cancel"
            elif _WAVEOFF.search(scope):
                intent = "waveoff"
            elif _NEGATION.search(scope):
                intent, result["blocked"] = "None", True
        result["intent"] = intent
        if intent == "readback":
            # Raw text after the keyword: tokenizing would turn "29.92" into a frequency marker.
            raw = re.search(r"\bread(?:ing)?[\s-]?back\b", text, re.IGNORECASE)
            result["readback"] = text[raw.end():] if raw else ""
    result["modex"] = _modex(region)
    if result["modex"] is None:
        result["transmitter"] = _parser.parse_callsign(region, False, known_groups)
    return result
