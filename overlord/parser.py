"""Deterministic brevity-code parser for OverlordBot radio transmissions.

Replaces the retired Azure LUIS application of the original OverlordBot.  The
entity model (receiver, transmitter, subject, qualifier, warning radius,
mission type/id) and the alias rules are ported from OverlordBot
``OverlordBot.Cognitive/LanguageUnderstanding`` and the labelled utterances,
phrase lists and list entities in ``OverlordBot.Core/Documentation/OverlordBot.lu``
(OverlordBot, Copyright (C) 2022 Jeffrey Jones, AGPL-3.0-or-later).  This
derived work is distributed under the same licence; see LICENSE and
data/PROVENANCE.md.

``parse(text)`` returns a plain dict:

``intent``
    One of ``None`` (string), ``radioCheck``, ``bogeyDope``, ``locationOfEntity``,
    ``setWarningRadius``, ``cancelWarningRadius``, ``readyToTaxi``,
    ``inboundToAirfield``, ``abortInbound``, ``picture``, ``declare``,
    ``missionAssignment``, ``missionJoin`` and the AWACS extensions
    ``friendlyPicture``, ``bullseyePicture``, ``tanker``, ``vectorHome``,
    ``divert`` and ``sayAgain`` (canonical phrases: friendly picture, bullseye
    picture, nearest tanker, vector home, nearest divert, say again).
``receiver``
    ``None`` (unknown) or ``{"kind": "awacs", "callsign"}``,
    ``{"kind": "commander", "callsign"}`` or
    ``{"kind": "airfield", "name", "spoken", "controller"}``.  Airfield names are
    canonical DCS names when the spoken name is a known alias, otherwise the
    lower-cased spoken words (original ``EntityAliasResolver`` behaviour).
``transmitter`` / ``subject``
    ``None`` or ``{"kind": "player"|"tanker", "group", "flight", "element"}``
    with ``-1`` for an unheard flight or element number and ``None`` for an
    unheard group name; a subject may also be ``{"kind": "airfield", ...}``.
``qualifier``
    bogey dope only: ``{"identifier", "attributes"}``.
``distance``, ``mission_type``, ``mission_id``, ``bearing``, ``range``
    intent-specific values or ``None``.
``reference``
    declare only: ``"bullseye"`` when the bearing and range were spoken from
    bullseye, otherwise ``"requester"``; ``None`` for other intents.

Speech-to-text output is matched phonetically where the vocabulary is closed:
command phrases, airfield names before a controller word or after a location
command, and (when the caller passes the live ``known_groups``) callsign group
names.  Open vocabulary (unknown group names) is taken as spoken.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterable, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _load(name: str):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


_VOCAB = _load("vocabulary.json")
_ALIASES = _load("aliases.json")
_AIRCRAFT = _load("aircraft.json")
_PHRASES = _VOCAB["phraselists"]


def norm(text: str) -> str:
    """Lower-case, hyphens/underscores to spaces, collapsed whitespace."""
    return " ".join(re.sub(r"[-_]", " ", text.lower()).split())


# Suffixes the original AirbasePronouncer drops when speaking airbase names.
AIRBASE_SUFFIXES = (" Airport", " AB", " AFB", " Intl", " Airfield", " International",
                    " Executive", " Airstrip", " Island")


def strip_airbase_suffixes(name: str) -> str:
    for suffix in AIRBASE_SUFFIXES:
        name = name.replace(suffix, "")
    return name


def _airfield_table() -> dict:
    table: dict = {}
    names = _VOCAB["airfield_names"]
    for canon in names:
        table.setdefault(norm(canon), canon)
        table.setdefault(norm(strip_airbase_suffixes(canon)), canon)
    for canon, synonyms in names.items():
        for s in synonyms:
            table.setdefault(norm(s), canon)
    for canon, synonyms in _ALIASES["airfields"].items():
        for s in synonyms:
            table.setdefault(norm(s), canon)
    for s in _VOCAB["parser_extensions"]["nearest_airfield"]:
        table.setdefault(norm(s), "nearest")
    return table


AIRFIELD_ALIASES = _airfield_table()
CALLSIGN_ALIASES = {a: c for c, al in _ALIASES["callsigns"].items() for a in al}
ATTRIBUTE_ALIASES = {a: c for c, al in _ALIASES["unit_attributes"].items() for a in al}
ATTRIBUTES = {norm(a): ATTRIBUTE_ALIASES.get(a, a) for a in _PHRASES["aircraftAttributes"]}
ATTRIBUTES.update({norm(a): c for a, c in ATTRIBUTE_ALIASES.items()})
MISSION_TYPES = {}
for _canon, _syns in _VOCAB["mission_types"].items():
    MISSION_TYPES[norm(_canon)] = _canon
    for _s in _syns:
        MISSION_TYPES[norm(_s)] = _canon
AWACS_CALLSIGNS = {norm(c).replace(" ", "") for c in _PHRASES["awacsCallsigns"]} | {
    norm(c).replace(" ", "") for c in _VOCAB["parser_extensions"]["awacs_callsigns"]}
COMMANDERS = {norm(c) for c in _PHRASES["commanderCallsigns"]}
TANKERS = {norm(c) for c in _PHRASES["tankerCallsigns"]}
CONTROLLERS = {norm(c) for c in _PHRASES["airfieldControllerNames"]}
TWO_WORD_GROUPS = {norm(c) for c in _PHRASES["callsignGroupNames"] if " " in c}


def _aircraft_aliases() -> dict:
    """Spoken NATO reporting name -> DCS type codes, from the ``aircraft`` and
    ``aircraft_variants`` sections of data/aliases.json.  An alias only names a type;
    it never carries a coalition."""
    by_code = {e["code"]: e for e in _AIRCRAFT}
    table: dict = {}
    for code, names in _ALIASES["aircraft"].items():
        for name in names:
            table[norm(name)] = list(by_code[code]["dcs_codes"])
    for name, codes in _ALIASES["aircraft_variants"].items():
        table[norm(name)] = list(codes)
    return table


AIRCRAFT_ALIAS_CODES = _aircraft_aliases()


def _identifier_vocab() -> set:
    vocab = {norm(x) for x in _PHRASES["aircraftIdentifiers"]}
    for entry in _AIRCRAFT:
        vocab.add(norm(entry["name"]))
        vocab.add(norm(entry["code"]))
        vocab.update(norm(c) for c in entry["dcs_codes"])
    vocab.update(AIRCRAFT_ALIAS_CODES)
    return vocab


IDENTIFIERS = _identifier_vocab()

# Words that carry no callsign information between a receiver and a callsign
# ("overlord, this is dolt 1-2").
FILLERS = set(_VOCAB["parser_extensions"]["callsign_fillers"])
# Hesitations skipped anywhere inside a callsign ("dolt, uh, 1-2", "dolt flight 1-2").
HESITATIONS = {"um", "uh", "er", "flight"}
# Function words and common verbs.  They are never a group name; directly before
# the callsign number they stand for a garbled digit or noise.
STOP_WORDS = FILLERS - {"flight", "please", "thanks"} | {
    "be", "are", "was", "were", "am", "in", "into", "by", "from", "as", "if", "not", "no", "do",
    "does", "did", "dont", "doesnt", "cant", "get", "got", "go", "going", "make", "made", "have",
    "has", "had", "say", "said", "check", "checking", "cause", "because", "that", "these",
    "those", "i", "im", "we", "your", "they", "he", "she", "me", "my", "our", "us", "than",
    "then", "there", "here", "up", "out", "off", "over", "under", "across", "about", "just",
    "will", "would", "could", "should", "can", "let", "lets", "which", "what",
    "who", "when", "where", "why", "how", "yes", "fore", "won", "ate", "such"}
# A link word between a group name and a single number replaced the flight digit;
# these are heard for a digit ("dodge four one" -> "dodge for one").
DIGIT_HOMOPHONES = {"for": 4, "fore": 4, "to": 2, "too": 2, "won": 1, "ate": 8}

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "niner": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}

INTENTS = ("radioCheck", "bogeyDope", "locationOfEntity", "setWarningRadius", "readyToTaxi",
           "inboundToAirfield", "cancelWarningRadius", "picture", "declare", "abortInbound",
           "missionAssignment", "missionJoin", "friendlyPicture", "bullseyePicture", "tanker",
           "vectorHome", "divert", "sayAgain")


class Tok:
    """One normalised word; numbers keep their digit string in ``d``."""

    __slots__ = ("w", "n", "d")

    def __init__(self, w: str, n: Optional[int] = None, d: Optional[str] = None):
        self.w = w
        self.n = n
        self.d = d

    @property
    def key(self) -> str:
        return self.d if self.d is not None else self.w

    @property
    def is_word(self) -> bool:
        return self.n is None and self.w.isalpha()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tok({self.w!r}, {self.n!r})"


FREQUENCY = "<frequency>"


def tokenize(text: str) -> list[Tok]:
    """Normalise STT text into word tokens with spoken numbers converted."""
    s = text.lower().replace("\u2019", "'")
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)            # 20,000 -> 20000
    s = re.sub(r"\b\d{2,3}\.\d{1,3}\b", f" {FREQUENCY} ", s)  # 249.5 is a frequency; "viper 2.2" is not
    s = re.sub(r"\b(what|where)'s\b", r"\1s", s)       # what's the nearest airfield
    s = re.sub(r"'s\b", "", s)                          # griffin's 31, overlord's
    s = s.replace("'", "")
    s = re.sub(r"[^a-z0-9<> ]", " ", s)                 # punctuation, / : - between numbers
    words = s.split()
    toks: list[Tok] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w.isdigit():
            toks.append(Tok(w, int(w), w))
            i += 1
            continue
        value, used = _number_words(words, i)
        if used:
            toks.append(Tok(" ".join(words[i:i + used]), value, str(value)))
            i += used
            continue
        toks.append(Tok(w))
        i += 1
    return toks


def _number_words(words: list[str], i: int) -> tuple[int, int]:
    """Greedy spoken-number reader; "one two" stays two numbers, "twenty five" is 25."""
    total = 0
    current = None
    j = i
    while j < len(words):
        w = words[j]
        if current is None and w in _UNITS:
            current = _UNITS[w]
            j += 1
            if not (j < len(words) and words[j] in ("hundred", "thousand")):
                break
        elif current is None and w in _TEENS:
            current = _TEENS[w]
            j += 1
            break
        elif current is None and w in _TENS:
            current = _TENS[w]
            j += 1
            if j < len(words) and words[j] in _UNITS and _UNITS[words[j]] > 0:
                current += _UNITS[words[j]]
                j += 1
            if not (j < len(words) and words[j] in ("hundred", "thousand")):
                break
        elif current is not None and w == "hundred":
            current *= 100
            j += 1
            rest, used = _tail(words, j)
            current += rest
            j += used
            if not (j < len(words) and words[j] == "thousand"):
                break
        elif current is not None and w == "thousand":
            total += current * 1000
            current = None
            j += 1
            rest, used = _tail(words, j)
            if used:
                total += rest
                j += used
            break
        else:
            break
    if j == i:
        return 0, 0
    return total + (current or 0), j - i


def _tail(words: list[str], j: int) -> tuple[int, int]:
    """Optional "and" + tens/units after hundred/thousand."""
    start = j
    if j < len(words) and words[j] == "and" and j + 1 < len(words) and (
            words[j + 1] in _TENS or words[j + 1] in _TEENS or words[j + 1] in _UNITS):
        j += 1
    if j < len(words) and words[j] in _TENS:
        v = _TENS[words[j]]
        j += 1
        if j < len(words) and words[j] in _UNITS and _UNITS[words[j]] > 0:
            v += _UNITS[words[j]]
            j += 1
        return v, j - start
    if j < len(words) and words[j] in _TEENS:
        return _TEENS[words[j]], j + 1 - start
    if j < len(words) and words[j] in _UNITS and j > start:
        return _UNITS[words[j]], j + 1 - start
    return 0, 0


def _match(toks: list[Tok], start: int, table, maxlen: int = 4, plural: bool = False):
    """Longest vocabulary match starting at ``start``: (length, value) or (0, None)."""
    for length in range(min(maxlen, len(toks) - start), 0, -1):
        key = " ".join(t.key for t in toks[start:start + length])
        if key in table:
            return length, (table[key] if isinstance(table, dict) else key)
        if plural and key.endswith("s") and key[:-1] in table:
            return length, (table[key[:-1]] if isinstance(table, dict) else key[:-1])
    return 0, None


# Command phrases, ported from the .lu phrase lists and the labelled utterances.
# Earliest match wins; on equal start the order below breaks the tie.
_MISSION_WORDS = "|".join(sorted({re.escape(k) for k in MISSION_TYPES}, key=len, reverse=True))
COMMANDS: list[tuple[str, re.Pattern]] = [
    # AWACS extensions.  They precede the original phrases so that "friendly picture" is not a
    # plain picture and "vector home" is not a location request when both start at the same word.
    ("sayAgain", re.compile(
        r"\bsay (?:that |your last |last )?again\b|\brepeat (?:that|your last|last)(?: transmission)?\b"
        r"|\bcome again\b")),
    ("friendlyPicture", re.compile(
        r"\bfriendl(?:y|ies) (?:aircraft |air |fighters )?picture\b|\bpicture (?:of |on )?(?:the )?friendl(?:y|ies)\b")),
    ("bullseyePicture", re.compile(
        r"\bbull(?:s ?eye|s) picture\b|\bpicture (?:from |on |at |off |relative to )?(?:the )?bull(?:s ?eye|s)\b")),
    ("vectorHome", re.compile(
        r"\b(?:request(?:ing)?|need|vectors?|directions?|headings?|bearings?|steer|bring|take|direct)"
        r"(?: a)?(?: vectors?)?(?: me| us)?(?: back)?(?: to| for)?(?: my| our| the)? "
        r"(?:home(?: plate| base| field| airfield)?|rtb)\b|\b(?:rtb|return to base)\b")),
    ("divert", re.compile(
        r"\b(?:(?:nearest|closest|request(?:ing)?) )?divert(?: (?:field|airfield|airport|base))?\b"
        r"|\balternate (?:field|airfield|airport|base)\b")),
    ("tanker", re.compile(
        r"\b(?:(?:nearest|closest|nearby|friendly) )+tankers?\b"
        r"|\b(?:request(?:ing)?|need|find|looking for) (?:a |the )?(?:(?:nearest|closest) )?tankers?\b"
        r"|\btanker (?:info|information|vectors?|position)\b")),
    ("abortInbound", re.compile(
        r"\b(?:abort(?:ing)?|cancel(?:l?ing|led)?) (?:inbound|approach)\b|\bwav(?:e|ing) ?off\b|\bgo(?:ing)? around\b")),
    ("cancelWarningRadius", re.compile(
        r"\b(?:cancel(?:led|ling)?|delete|stop|remove|halt|disable|clear) (?:the |my )?"
        r"(?:trip ?wire|warning(?: radius)?|warnings|perimeter|threat radius|radius)\b")),
    ("missionJoin", re.compile(r"\b(?:request )?(?:join(?:ing)?|fragging) (?:mission|tasking)\b")),
    ("missionAssignment", re.compile(
        r"\b(?:request(?:ing)?|set|assign)(?: \w+){0,4}? (?:mission|tasking|objective|target|task|frag order)\b"
        rf"|\btasking (?:{_MISSION_WORDS})\b")),
    ("setWarningRadius", re.compile(
        r"\bset (?:trip|warning|threat|radius|perimeter|fence)\w*|\btrip ?wire\b|\btrip (?:bar|prior|are|or)\b"
        r"|\bwarning (?:radius|range)\b|\bwarn me\b|\bradius\b|\bperimeter\b|\bfence\b"
        r"|\b(?:notify|tell me|shout|let me know) if\b|\bif (?:enemies|bandits|hostiles) get within\b")),
    ("readyToTaxi", re.compile(
        r"\b(?:(?:ready (?:to|for)|request(?:ing)?(?: permission to)?|permission to|radio(?: to)?) )?taxi\b")),
    ("radioCheck", re.compile(r"\b(?:radio ?check|radio ?shack|comms? check|mi(?:c|ke) check)\b")),
    ("bogeyDope", re.compile(
        r"\b(?:(?:request(?:ing)?(?: for)? )?(?:another )?(?:bogey|bogie|bogy|boogie|bug|but you) ?(?:dope|soap))\b"
        r"|\bbogey\b|\bshopping\b")),
    ("picture", re.compile(r"\bpicture\b")),
    ("declare", re.compile(r"\bdeclare\b")),
    ("inboundToAirfield", re.compile(r"\binbound\b")),
    ("locationOfEntity", re.compile(
        r"\b(?:bearing|vectors?|heading|direction|where|location|position|locate|whats the)\b")),
]


def phonetic(text: str) -> str:
    """Coarse English sound key: voicing, vowel quality and non-rhotic r are ignored.

    "an arper" and "anapa" share a key, "qtacey" is one edit from "kutaisi".
    """
    s = re.sub(r"[^a-z]", "", text.lower())
    s = s.replace("ph", "f").replace("ck", "k").replace("qu", "k").replace("gh", "")
    s = re.sub(r"c(?=[eiy])", "s", s)
    s = s.replace("c", "k").replace("q", "k").replace("x", "ks")
    s = re.sub(r"r(?=[^aeiouy]|$)", "", s)
    if len(s) > 1:
        s = s.replace("h", "")
    s = s.translate(str.maketrans("bdgvzwy", "ptkfsaa"))
    s = re.sub(r"[aeiou]+", "a", s)
    return re.sub(r"(.)\1+", r"\1", s)


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _sounds_like(spoken: str, target_key: str) -> int:
    """Edit distance between sound keys; a large number when too short to judge or
    when the first sounds differ (as in Soundex, the onset is rarely misheard)."""
    key = phonetic(spoken)
    if len(key) < 4 or len(target_key) < 4 or key[0] != target_key[0]:
        return 99
    return _edits(key, target_key)


def _closest(spoken: str, keyed: dict, raw: Optional[dict] = None):
    """Unique value whose sound key is within one edit (two for keys of six or more
    sounds) of ``spoken``, else within a third of the letters of a ``raw`` word."""
    best, best_values = None, set()
    for target_key, values in keyed.items():
        d = _sounds_like(spoken, target_key)
        if d > (2 if len(target_key) >= 6 else 1):
            continue
        if best is None or d < best:
            best, best_values = d, set(values)
        elif d == best:
            best_values.update(values)
    if best is None and raw:
        letters = re.sub(r"[^a-z]", "", spoken)
        for word, value in raw.items():
            d = _edits(letters, word)
            if len(letters) >= 4 and d <= len(word) // 3:
                if best is None or d < best:
                    best, best_values = d, {value}
                elif d == best:
                    best_values.add(value)
    return best_values.pop() if len(best_values) == 1 else None


def _keyed(names: dict) -> dict:
    """Phonetic key -> all candidates, preserving ties for fail-closed scoring."""
    out = {}
    for name, value in names.items():
        key = phonetic(name)
        if len(key) >= 4:
            out.setdefault(key, set()).add(value)
    return out


def _encyclopedia_airfields() -> set:
    """Canonical names of the airfields with bundled data (data/airfields)."""
    bundled = set()
    for name in os.listdir(os.path.join(DATA_DIR, "airfields")):
        if name.endswith(".json"):
            bundled.add(norm(strip_airbase_suffixes(name[:-5])))
    return {v for v in AIRFIELD_ALIASES.values() if norm(strip_airbase_suffixes(v)) in bundled}


# Airfields matched by sound: only those the bot serves (bundled data), so that a
# misheard Caucasus name is not taken for a similar-sounding airfield elsewhere.
_SERVED = _encyclopedia_airfields()
AIRFIELD_SOUNDS = _keyed({k: v for k, v in AIRFIELD_ALIASES.items() if v in _SERVED})
BULLSEYE_SOUND = phonetic("bullseye")

# Command phrases matched by sound when no phrase matches literally
# ("bobey dope", "bogey dote", "plug it up").
_SOUND_COMMANDS = [(intent, phonetic(phrase)) for intent, name in (
    ("bogeyDope", "bogeyDopeCommands"), ("radioCheck", "radioCheckCommands"), ("picture", "pictureCommands"),
    ("readyToTaxi", "readyToTaxiCommands"), ("abortInbound", "abortInboundCommands"))
    for phrase in _PHRASES[name] if len(phonetic(phrase)) >= 6]


def _find_command(joined: str, offsets: list[int], start_char: int = 0):
    best, repeat = None, None
    for rank, (intent, pattern) in enumerate(COMMANDS):
        m = pattern.search(joined, start_char)
        if not m:
            continue
        cand = (m.start(), rank, intent, m.end())
        if intent == "sayAgain":
            repeat = cand
            continue
        if best is None or cand[:2] < best[:2]:
            best = cand
    # "Say again bogey dope" repeats that explicit request, not an unrelated
    # cached reply. Bare "say again" remains a caller-scoped replay.
    if best is None:
        best = repeat
    if best is None:
        return None
    start_tok = _tok_index(offsets, best[0])
    end_tok = _tok_index(offsets, best[3] - 1) + 1
    return best[2], start_tok, end_tok


def _sound_command(toks: list[Tok]):
    """Earliest run of 1-4 words sounding like a command phrase: (intent, start, end) or None."""
    for i in range(len(toks)):
        best = None
        for length in range(1, 5):
            span = toks[i:i + length]
            if len(span) < length or not all(t.is_word for t in span):
                break
            spoken = "".join(t.w for t in span)
            for intent, key in _SOUND_COMMANDS:
                d = _sounds_like(spoken, key)
                if d <= (2 if len(key) >= 8 else 1) and (best is None or d < best[0]):
                    best = (d, intent, i + length)
        if best:
            return best[1], i, best[2]
    return None


def _tok_index(offsets: list[int], char: int) -> int:
    idx = 0
    for i, off in enumerate(offsets):
        if off <= char:
            idx = i
        else:
            break
    return idx


def _awacs_set(bot_callsign: Optional[str]) -> set:
    awacs = set(AWACS_CALLSIGNS)
    if bot_callsign:
        awacs.add(norm(bot_callsign).replace(" ", ""))
    return awacs


def _airfield_before(toks: list[Tok], i: int, awacs: set):
    """Airfield name spoken before the controller word at ``i``: (start, name, spoken)."""
    for back in range(min(4, i), 0, -1):
        length, canon = _match(toks, i - back, AIRFIELD_ALIASES, maxlen=back)
        if length == back:
            return i - back, canon, " ".join(x.w for x in toks[i - back:i])
    for back in range(min(3, i), 0, -1):
        span = toks[i - back:i]
        if not all(t.is_word for t in span):
            continue
        canon = _closest("".join(t.w for t in span), AIRFIELD_SOUNDS)
        if canon:
            return i - back, canon, " ".join(t.w for t in span)
    if i > 0 and toks[i - 1].is_word and toks[i - 1].w not in FILLERS and toks[i - 1].w not in awacs:
        return i - 1, toks[i - 1].w, toks[i - 1].w
    return i, None, None


def _receivers(toks: list[Tok], bot_callsign: Optional[str]):
    """All receiver candidates as (start, end, receiver dict), sorted by start."""
    awacs = _awacs_set(bot_callsign)
    out = []
    for i, t in enumerate(toks):
        if t.n is not None:
            continue
        two = t.w + toks[i + 1].w if i + 1 < len(toks) and toks[i + 1].n is None else None
        if two and two in awacs:
            out.append((i, i + 2, {"kind": "awacs", "callsign": two}))
        elif t.w in awacs:
            out.append((i, i + 1, {"kind": "awacs", "callsign": t.w}))
        elif t.w in COMMANDERS:
            out.append((i, i + 1, {"kind": "commander", "callsign": t.w}))
        elif t.w in CONTROLLERS:
            start, name, spoken = _airfield_before(toks, i, awacs)
            out.append((start, i + 1, {"kind": "airfield", "name": name, "spoken": spoken,
                                       "controller": t.w}))
    if toks and toks[0].n is None:
        length, canon = _match(toks, 0, AIRFIELD_ALIASES)
        if length and canon != "nearest" and not (length < len(toks) and toks[length].w in CONTROLLERS):
            spoken = " ".join(x.w for x in toks[:length])
            out.append((0, length, {"kind": "airfield", "name": canon, "spoken": spoken, "controller": None}))
    out.sort(key=lambda r: r[0])
    return out


def _resolve_group(words: list[str], known_groups: Optional[set]) -> Optional[str]:
    """Group name from the words before the callsign number (last word, or a two-word name)."""
    if not words:
        return None
    if known_groups:
        for size in (2, 1):
            for end in range(len(words), size - 1, -1):
                joined = "".join(words[end - size:end])
                if CALLSIGN_ALIASES.get(joined, joined) in known_groups:
                    return CALLSIGN_ALIASES.get(joined, joined)
    pick = words[-1:]
    if len(words) >= 2 and " ".join(words[-2:]) in TWO_WORD_GROUPS:
        pick = words[-2:]
    group = CALLSIGN_ALIASES.get("".join(pick), "".join(pick))
    if known_groups and group not in known_groups:
        heard = _closest(group, _keyed({g: g for g in known_groups}), {g: g for g in known_groups})
        if heard:
            return heard
    return group


def _strip_noise(words: list[str], bot_key: Optional[str]) -> list[str]:
    """Drop words up to a garbled bot callsign ("of lord", "overlay") and leading fillers."""
    cut = 0
    for i in range(len(words)):
        for size in (1, 2):
            if bot_key and i + size <= len(words) and len(bot_key) >= 5 \
                    and _sounds_like("".join(words[i:i + size]), bot_key) <= 1:
                cut = i + size
    words = words[cut:]
    while words and words[0] in FILLERS:
        words = words[1:]
    return words


def _is_callsign_number(t: Tok) -> bool:
    """Flight/element digits: "12", "1", "00"; "011" is no callsign."""
    return t.n is not None and not (t.d.startswith("0") and len(t.d) > 2)


def parse_callsign(toks: list[Tok], allow_group_only: bool,
                   known_groups: Optional[set] = None, bot_key: Optional[str] = None) -> Optional[dict]:
    """Group name + flight + element from a token region.

    Ported from IntentDetails.SetPlayerCallsign with the LU labelling rules:
    "12" is flight 1 element 2, "1 2" likewise, a lone number is the flight, and
    a function word between group name and a single number is the lost flight
    digit (flight -1, or the digit it sounds like: "for" is 4).
    """
    region = [t for t in toks if t.w != FREQUENCY and t.w not in HESITATIONS]
    nums = [i for i, t in enumerate(region) if _is_callsign_number(t)]
    if not nums:
        words = _strip_noise([t.w for t in region if t.is_word], bot_key)
        while words and words[-1] in STOP_WORDS:
            words = words[:-1]
        if not allow_group_only or not words or len(words) > 2 or len(region) > 4 \
                or any(w in STOP_WORDS for w in words):
            return None
        if len(words) == 2 and " ".join(words) not in TWO_WORD_GROUPS and not (
                known_groups and "".join(words) in known_groups):
            return None
        group = _resolve_group(words, known_groups)
        return {"kind": "tanker" if group in TANKERS else "player", "group": group,
                "flight": -1, "element": -1}
    n = next((i for i in nums if i > 0 and region[i - 1].is_word), nums[0])
    pre: list[str] = []
    for t in reversed(region[:n]):
        if not t.is_word:
            break
        pre.insert(0, t.w)
    pre = _strip_noise(pre, bot_key)
    digits = region[n].d
    second = region[n + 1] if n + 1 < len(region) and _is_callsign_number(region[n + 1]) \
        and len(digits) == 1 else None
    link = pre.pop() if pre and pre[-1] in STOP_WORDS else None
    group = _resolve_group(pre, known_groups) if pre and pre[-1] not in STOP_WORDS else None
    if len(digits) >= 2:
        flight, element = int(digits[0]), int(digits[1:])
    elif second is not None:
        flight, element = int(digits), int(second.d)
    elif link is not None and group is not None:
        flight, element = DIGIT_HOMOPHONES.get(link, -1), int(digits)
    else:
        flight, element = int(digits), -1
    kind = "tanker" if group in TANKERS else "player"
    return {"kind": kind, "group": group, "flight": flight, "element": element}


def _subject(toks: list[Tok], known_groups: Optional[set], bot_key: Optional[str]) -> Optional[dict]:
    rest = [t for t in toks if not (t.n is None and t.w in FILLERS | {"friendly", "is", "to", "for"})]
    for i in range(len(rest)):
        if rest[i].n is not None:
            continue
        length, canon = _match(rest, i, AIRFIELD_ALIASES)
        if length and not (i + length < len(rest) and rest[i + length].n is not None):
            return {"kind": "airfield", "name": canon,
                    "spoken": " ".join(t.w for t in rest[i:i + length])}
    callsign = parse_callsign(rest, allow_group_only=False, known_groups=known_groups, bot_key=bot_key)
    if callsign and (callsign["group"] or len(str(callsign["element"])) == 1):
        # "where is 1 2" names a flight; "vector 411" is a heading, not a callsign.
        return callsign
    words = []
    for t in rest:
        if t.w in ("airbase", "airfield", "base", "please") or not t.is_word:
            break
        words.append(t.w)
    if not words:
        return None
    if len(words) == 1 and words[0] in TANKERS:
        return {"kind": "tanker", "group": words[0], "flight": -1, "element": -1}
    spoken_words = []
    for t in toks:
        if t.w in ("airbase", "airfield", "base", "please") or not t.is_word:
            break
        spoken_words.append(t.w)
    for start in range(len(spoken_words)):  # fillers may be part of a misheard name ("an arper")
        for size in range(min(3, len(spoken_words) - start), 0, -1):
            canon = _closest("".join(spoken_words[start:start + size]), AIRFIELD_SOUNDS)
            if canon:
                return {"kind": "airfield", "name": canon, "spoken": " ".join(spoken_words[start:start + size])}
    spoken = " ".join(words[:3])
    return {"kind": "airfield", "name": spoken, "spoken": spoken}


def _qualifier(toks: list[Tok]) -> dict:
    identifier = None
    attributes: set = set()
    i = 0
    while i < len(toks):
        length, attr = _match(toks, i, ATTRIBUTES, maxlen=2)
        if length:
            attributes.add(attr)
            i += length
            continue
        if identifier is None:
            length, _ = _match(toks, i, IDENTIFIERS, maxlen=3, plural=True)
            if length:
                identifier = "-".join(t.key for t in toks[i:i + length])
                i += length
                continue
        i += 1
    # TransmissionParser.SetQualifier: range classes only make sense for fighters.
    if attributes and "fighter" not in attributes:
        attributes -= {"short range", "medium range", "long range"}
    return {"identifier": identifier, "attributes": sorted(attributes)}


def _distance(toks: list[Tok]) -> Optional[int]:
    value = next((t.n for t in toks if t.n is not None), None)
    if value is None:
        return None
    # SetWarningRadiusTransmission: "set warning to 30" is heard as 230 and
    # "set warning for 40" as 440.  The original subtracted 500 for the latter,
    # which produced a negative radius; 400 is the evident intent.
    if 200 <= value < 300:
        return value - 200
    if 400 <= value < 500:
        return value - 400
    return value


def _declare(toks: list[Tok]) -> tuple[Optional[int], Optional[int], str]:
    """``declare [bullseye] 134 for 25``: magnetic bearing, range and their reference point.

    Bearing digits may be spoken one by one ("zero four five thirty" is 045 for 30).
    """
    reference = "requester"
    words = [t for t in toks if t.n is None]
    for i in range(len(words)):
        for size in (1, 2):
            spoken = "".join(t.w for t in words[i:i + size])
            if len(words[i:i + size]) == size and (
                    spoken in ("bullseye", "bulls", "bull") or _sounds_like(spoken, BULLSEYE_SOUND) <= 1):
                reference = "bullseye"
    seq = [t for t in toks if t.n is not None]
    if not seq:
        return None, None, reference
    if len(seq[0].d) == 1:
        run = ""
        while seq and len(seq[0].d) == 1 and len(run) < 3 and (len(seq) > 1):
            run += seq.pop(0).d
        bearing = int(run)
    else:
        bearing = seq.pop(0).n
    if seq and seq[0].w == "four" and len(seq) > 1:
        seq.pop(0)
    rng = seq[0].n if seq else None
    if bearing > 360 or not rng:
        return None, None, reference
    return bearing, rng, reference


def _bogey_where(after: list[Tok]) -> bool:
    """ "where (are the) flankers": a location request for an aircraft type is a bogey dope."""
    for i in range(len(after)):
        if _match(after, i, IDENTIFIERS, maxlen=3, plural=True)[0] or _match(after, i, ATTRIBUTES, maxlen=2)[0]:
            return True
    return False


def _identifier_tokens(after: list[Tok]) -> set:
    """Indexes of tokens spoken as part of an aircraft identifier ("flogger j two"), so that
    a digit that belongs to a reporting-name variant is not taken for a callsign number."""
    covered: set = set()
    i = 0
    while i < len(after):
        length, _ = _match(after, i, IDENTIFIERS, maxlen=3, plural=True)
        if length:
            covered.update(range(i, i + length))
            i += length
        else:
            i += 1
    return covered


def _chatter(before: list[Tok]) -> bool:
    """Without a receiver, a request follows its callsign directly; talk in between
    ("apollo 1 1, if you are on this net, when you do the warning radius") is chatter."""
    last = max((i for i, t in enumerate(before) if t.n is not None), default=None)
    if last is None:
        return False
    return sum(1 for t in before[last + 1:] if t.w not in HESITATIONS) > 2


def parse(text: str, bot_callsign: Optional[str] = "overlord",
          known_groups: Optional[Iterable[str]] = None) -> dict:
    """Parse one transmission.  ``known_groups`` are the live callsign group names."""
    groups = {norm(g).replace(" ", "") for g in known_groups} if known_groups else None
    result = {"text": text, "intent": "None", "receiver": None, "transmitter": None, "subject": None,
              "qualifier": None, "distance": None, "mission_type": None, "mission_id": None,
              "bearing": None, "range": None, "reference": None}
    toks = tokenize(text)
    if not toks:
        return result
    offsets, pos = [], 0
    for t in toks:
        offsets.append(pos)
        pos += len(t.w) + 1
    joined = " ".join(t.w for t in toks)
    command = _find_command(joined, offsets)
    if command is None or command[0] == "sayAgain":
        command = _sound_command(toks) or command
    if command is None:
        return result
    bot_key = phonetic(bot_callsign) if bot_callsign else None
    intent, c_start, c_end = command

    def speakers(c_start):
        receivers = [r for r in _receivers(toks, bot_callsign) if r[1] <= c_start]
        receiver = receivers[0] if receivers else None
        if receiver:
            transmitter = parse_callsign(toks[receiver[1]:c_start], True, groups, bot_key)
            if transmitter is None and receiver[0] > 0:
                transmitter = parse_callsign(toks[:receiver[0]], False, groups, bot_key)
        else:
            # A lone leading word is a group name ("cowboy, bogey dope").
            transmitter = parse_callsign(toks[:c_start], c_start == 1, groups, bot_key)
        return receiver, transmitter

    receiver, transmitter = speakers(c_start)
    if transmitter is None and receiver is not None:
        # "kutaisi tower, inbound, arrow 11 inbound": the command is repeated after the callsign.
        again = _find_command(joined, offsets, offsets[c_end - 1] + len(toks[c_end - 1].w))
        if again and again[0] == intent:
            r2, t2 = speakers(again[1])
            if t2 is not None:
                c_start, c_end = again[1], again[2]
                receiver, transmitter = r2, t2
    if any(t.w == "traffic" for t in toks[:c_start]):
        return result  # "<airfield> traffic, ..." is a self-announcement, not a request
    if receiver is None and transmitter is None:
        return result
    if receiver is None and _chatter(toks[:c_start]):
        return result
    after = toks[c_end:]
    if intent == "locationOfEntity" and toks[c_start].w == "where" and _bogey_where(after) \
            and not any(_match(after, i, AIRFIELD_ALIASES)[0] for i in range(len(after))) \
            and not any(_is_callsign_number(t) for i, t in enumerate(after)
                        if i not in _identifier_tokens(after)):
        intent = "bogeyDope"
        c_start = c_end
    elif intent == "locationOfEntity" and any(t.w in ("tanker", "tankers") for t in after) \
            and (_subject(after, groups, bot_key) or {}).get("kind") != "tanker":
        # "vector to the nearest tanker": the generic word, not a named tanker callsign
        intent = "tanker"
        c_start = c_end
    result["intent"] = intent
    result["receiver"] = receiver[2] if receiver else None
    result["transmitter"] = transmitter
    if intent == "locationOfEntity":
        result["subject"] = _subject(after, groups, bot_key)
    elif intent == "bogeyDope":
        start = receiver[1] if receiver else 0
        tail = toks[start:c_start] + after
        region = toks[start:c_start]
        nums = [i for i, t in enumerate(region) if t.n is not None]
        if transmitter and nums:
            tail = region[nums[-1] + 1:] + after
        elif transmitter:
            tail = after
        result["qualifier"] = _qualifier(tail)
    elif intent == "setWarningRadius":
        result["distance"] = _distance(toks[c_start:])
    elif intent == "missionAssignment":
        span = toks[c_start:]
        for i in range(len(span)):
            length, canon = _match(span, i, MISSION_TYPES)
            if length:
                result["mission_type"] = canon
                break
    elif intent == "missionJoin":
        digits = "".join(t.d for t in after if t.n is not None)
        result["mission_id"] = int(digits) if digits else None
    elif intent == "declare":
        result["bearing"], result["range"], result["reference"] = _declare(after)
    return result
