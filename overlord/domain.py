"""AWACS and ATC responders ported from OverlordBot to a Tacview world model.

Ported from OverlordBot ``OverlordBot.Core`` (``Awacs/``, ``Atc/``,
``Responders/``, ``Util/``), ``OverlordBot.Cognitive/SpeechOutput`` and
``OverlordBot.Encyclopedia`` (OverlordBot, Copyright (C) 2022 Jeffrey Jones,
AGPL-3.0-or-later).  This derived work is distributed under the same licence;
see LICENSE and data/PROVENANCE.md.

Entry point: :func:`handle`.  Units are World.snapshot dicts
(``id, name, callsign, coalition, type, category, lat, lon, altitude, heading``
plus optional ``pilot``, ``group`` and ``speed`` in m/s).

Magnetic bearings use the NOAA World Magnetic Model WMM2025 (data/WMM2025.COF),
as the original used WMM through ``WmmGeomagnetismCalculator``.

Differences forced by the data source are documented in data/PROVENANCE.md
("Data limitations"); every one produces an explicit negative reply instead of
a guessed answer.
"""

from __future__ import annotations

import datetime as _dt
import heapq
import json
import math
import os
import re
import threading
from typing import Iterable, Mapping, Optional, Union

from . import parser as _parser

DATA_DIR = _parser.DATA_DIR

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
EARTH_RADIUS_M = 6371008.8          # IUGG mean radius: direct problem and non-converging inverse fallback
METRES_PER_NM = 1852.0
FEET_PER_METRE_ORIGINAL = 3.28      # OverlordBot rounds altitudes with 3.28 ft/m

AWACS_ONLY_MESSAGE = "this is an AYWACS frequency and we only respond to AYWACS calls"
ATC_ONLY_MESSAGE = "this is an ATC frequency and we only respond to ATC calls"
NO_ATC_MESSAGE = "There are no ATC services currently available at this airfield."
NO_WIND_MESSAGE = "wind information is unavailable, unable to determine the active runway"
NO_MONITOR_MESSAGE = "unable, warning radius monitoring is not available"
MISSION_UNAVAILABLE_MESSAGE = "unable to forward request"
BULLSEYE_UNAVAILABLE_MESSAGE = "unable, bullseye reference unavailable"

AWACS_INTENTS = {"bogeyDope", "locationOfEntity", "setWarningRadius", "declare", "picture",
                 "friendlyPicture", "bullseyePicture", "tanker", "vectorHome", "divert", "sayAgain"}
VALID_CONTROLLER_NAMES = ("ground", "tower", "approach", "center")
AIR_CATEGORIES = {"Airplane", "Helicopter", "Air"}
AWACS_TYPES = {"A-50", "E-2C", "E-2D", "E-3A", "KJ-2000"}   # GrpcDatastore.UnitRepository

# BogeyDopeHandler.MetricAirframes: these airframes get kilometres / metres.
METRIC_AIRFRAMES = {
    "MiG-29A", "MiG-29S", "Su-27", "Su-33", "J-11A", "MiG-21Bis", "MiG-19P", "MiG-15", "MiG-15bis",
    "MiG-15Bis", "Su-25", "Su-25T", "Mi-8MT", "Ka-50", "Mi-24P", "L-39C", "L-39ZA", "AJS37",
    "SA342L", "SA342M", "SA342Minigun", "SA342Mistral",
}

CARDINAL_POINTS = ("NORTH", "NORTH EAST", "EAST", "SOUTH EAST", "SOUTH", "SOUTH WEST", "WEST",
                   "NORTH WEST", "NORTH")

HOSTILE_MIN_SPEED = 25      # m/s, UnitRepository.FindHostileAircraft
STATIONARY_SPEED = 10       # m/s, WarningRadiusChecker
MERGED_BOUNDARY_NM = 3      # WarningRadiusChecker.MergedBoundary
GROUP_RADIUS_NM = 5         # BogeyDopeHandler group membership radius
DECLARE_RADIUS_NM = 5       # contacts within this of a declared point are "on" it
PICTURE_MAX_GROUPS = 3


def _load(name: str):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


AIRCRAFT = _load("aircraft.json")
MILSTD = _load("milstd2525d.json")
AIR_DCS_CODES = {code for entry in MILSTD["air"] for code in entry["dcs_codes"]}
# Friendly tanker selection is grounded on type: the data's tanker entries plus the DCS
# tanker type names that data/aircraft.json does not list.
TANKER_TYPES = {code for entry in AIRCRAFT if "tanker" in entry["attributes"] for code in entry["dcs_codes"]} | {
    "KC135MPRS", "KC130", "IL-78M"}


def _load_airfields() -> list:
    folder = os.path.join(DATA_DIR, "airfields")
    return [_load(os.path.join("airfields", f)) for f in sorted(os.listdir(folder)) if f.endswith(".json")]


# --------------------------------------------------------------------------
# World Magnetic Model (NOAA WMM2025, valid 2025.0-2030.0)
# --------------------------------------------------------------------------

def _load_wmm():
    with open(os.path.join(DATA_DIR, "WMM2025.COF"), encoding="ascii") as f:
        lines = f.read().splitlines()
    epoch = float(lines[0].split()[0])
    coefficients = []
    for line in lines[1:]:
        if line.startswith("9999"):
            break
        n, m, g, h, gd, hd = line.split()
        coefficients.append((int(n), int(m), float(g), float(h), float(gd), float(hd)))
    return epoch, coefficients


WMM_EPOCH, WMM_COEFFICIENTS = _load_wmm()
WMM_MAX_DEGREE = max(c[0] for c in WMM_COEFFICIENTS)


def decimal_year(when: _dt.datetime) -> float:
    start = _dt.datetime(when.year, 1, 1, tzinfo=when.tzinfo)
    end = _dt.datetime(when.year + 1, 1, 1, tzinfo=when.tzinfo)
    return when.year + (when - start) / (end - start)


def magnetic_field(lat: float, lon: float, alt_km: float, year: float) -> tuple[float, float, float]:
    """WMM north (X), east (Y), down (Z) components in nT at a geodetic position."""
    a, f, re_ = 6378.137, 1 / 298.257223563, 6371.2
    b = a * (1 - f)
    rlat, rlon = math.radians(lat), math.radians(lon)
    a2, b2 = a * a, b * b
    c2, c4 = a2 - b2, a2 * a2 - b2 * b2
    sl, cl = math.sin(rlat), math.cos(rlat)
    sl2, cl2 = sl * sl, cl * cl
    q = math.sqrt(a2 - c2 * sl2)
    q1 = alt_km * q
    q2 = ((q1 + a2) / (q1 + b2)) ** 2
    ct = sl / math.sqrt(q2 * cl2 + sl2)
    st = math.sqrt(1 - ct * ct)
    r = math.sqrt(alt_km * alt_km + 2 * q1 + (a2 * a2 - c4 * sl2) / (q * q))
    d = math.sqrt(a2 * cl2 + b2 * sl2)
    ca, sa = (alt_km + d) / r, c2 * cl * sl / (r * d)
    size = WMM_MAX_DEGREE + 1
    p = [[0.0] * size for _ in range(size)]
    dp = [[0.0] * size for _ in range(size)]
    s = [[0.0] * size for _ in range(size)]
    p[0][0] = s[0][0] = 1.0
    for n in range(1, size):
        s[n][0] = s[n - 1][0] * (2 * n - 1) / n
        for m in range(1, n + 1):
            s[n][m] = s[n][m - 1] * math.sqrt((n - m + 1) * (2 if m == 1 else 1) / (n + m))
        for m in range(n + 1):
            if m == n:
                p[n][n] = st * p[n - 1][n - 1]
                dp[n][n] = st * dp[n - 1][n - 1] + ct * p[n - 1][n - 1]
            else:
                k = ((n - 1) ** 2 - m * m) / ((2 * n - 1) * (2 * n - 3)) if n > 1 else 0.0
                p2 = p[n - 2][m] if n > 1 else 0.0
                dp2 = dp[n - 2][m] if n > 1 else 0.0
                p[n][m] = ct * p[n - 1][m] - k * p2
                dp[n][m] = ct * dp[n - 1][m] - st * p[n - 1][m] - k * dp2
    dt = year - WMM_EPOCH
    x = y = z = 0.0
    for n, m, g, h, gd, hd in WMM_COEFFICIENTS:
        g = (g + dt * gd) * s[n][m]
        h = (h + dt * hd) * s[n][m]
        ar = (re_ / r) ** (n + 2)
        cm, sm = math.cos(m * rlon), math.sin(m * rlon)
        x += ar * (g * cm + h * sm) * dp[n][m]
        y += ar * m * (g * sm - h * cm) * p[n][m] / st
        z -= ar * (n + 1) * (g * cm + h * sm) * p[n][m]
    return x * ca + z * sa, y, z * ca - x * sa


def declination(lat: float, lon: float, when: _dt.datetime, alt_km: float = 0.0) -> float:
    x, y, _ = magnetic_field(lat, lon, alt_km, decimal_year(when))
    return math.degrees(math.atan2(y, x))


def true_to_magnetic(lat: float, lon: float, true_bearing: float, when: _dt.datetime) -> float:
    """Geospatial.TrueToMagnetic."""
    magnetic = true_bearing - declination(lat, lon, when)
    return magnetic % 360


# --------------------------------------------------------------------------
# Great-circle geometry
# --------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _spherical_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    yy = math.sin(dlmb) * math.cos(p2)
    xx = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return math.degrees(math.atan2(yy, xx)) % 360


def inverse(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Vincenty inverse on WGS84: (distance metres, initial true bearing degrees)."""
    if lat1 == lat2 and lon1 == lon2:
        return 0.0, 0.0
    a, f = WGS84_A, WGS84_F
    b = a * (1 - f)
    u1 = math.atan((1 - f) * math.tan(math.radians(lat1)))
    u2 = math.atan((1 - f) * math.tan(math.radians(lat2)))
    big_l = math.radians(lon2 - lon1)
    lam = big_l
    su1, cu1, su2, cu2 = math.sin(u1), math.cos(u1), math.sin(u2), math.cos(u2)
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        sin_s = math.hypot(cu2 * sl, cu1 * su2 - su1 * cu2 * cl)
        if sin_s == 0:
            return 0.0, 0.0
        cos_s = su1 * su2 + cu1 * cu2 * cl
        sigma = math.atan2(sin_s, cos_s)
        sin_a = cu1 * cu2 * sl / sin_s
        cos2_a = 1 - sin_a * sin_a
        cos_2sm = cos_s - 2 * su1 * su2 / cos2_a if cos2_a else 0.0
        c = f / 16 * cos2_a * (4 + f * (4 - 3 * cos2_a))
        prev = lam
        lam = big_l + (1 - c) * f * sin_a * (sigma + c * sin_s * (cos_2sm + c * cos_s * (-1 + 2 * cos_2sm ** 2)))
        if abs(lam - prev) < 1e-12:
            break
    else:  # nearly antipodal: Vincenty does not converge
        return _haversine_m(lat1, lon1, lat2, lon2), _spherical_bearing(lat1, lon1, lat2, lon2)
    u_sq = cos2_a * (a * a - b * b) / (b * b)
    big_a = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    big_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    delta = big_b * sin_s * (cos_2sm + big_b / 4 * (cos_s * (-1 + 2 * cos_2sm ** 2)
                                                     - big_b / 6 * cos_2sm * (-3 + 4 * sin_s ** 2) * (-3 + 4 * cos_2sm ** 2)))
    distance = b * big_a * (sigma - delta)
    azimuth = math.degrees(math.atan2(cu2 * math.sin(lam), cu1 * su2 - su1 * cu2 * math.cos(lam))) % 360
    return distance, azimuth


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return inverse(lat1, lon1, lat2, lon2)[0]


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing from point 1 to point 2 (Geo CalculateGreatCircleLine.Bearing12)."""
    return inverse(lat1, lon1, lat2, lon2)[1]


def destination(lat: float, lon: float, true_bearing: float, range_m: float) -> tuple[float, float]:
    """Geospatial.CalculatePointFromSource."""
    p1, l1 = math.radians(lat), math.radians(lon)
    ang, brg = range_m / EARTH_RADIUS_M, math.radians(true_bearing)
    p2 = math.asin(math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(brg))
    l2 = l1 + math.atan2(math.sin(brg) * math.sin(ang) * math.cos(p1), math.cos(ang) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def _pos(x) -> tuple[float, float]:
    return x["lat"], x["lon"]


def dist_nm(a, b) -> float:
    return distance_m(*_pos(a), *_pos(b)) / METRES_PER_NM


def dist_km(a, b) -> float:
    return distance_m(*_pos(a), *_pos(b)) / 1000.0


def true_bearing(a, b) -> float:
    return bearing_deg(*_pos(a), *_pos(b))


def diff_heading(a: int, b: int) -> int:
    return min((a - b) % 360, (b - a) % 360)


# --------------------------------------------------------------------------
# Phraseology (Responders/Reply.cs, SpeechOutput)
# --------------------------------------------------------------------------

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def number_to_words(number: int) -> str:
    """Humanizer ``ToWords`` (en-US) as used by Reply.AltitudeToWords."""
    if number == 0:
        return "zero"
    if number < 0:
        return "minus " + number_to_words(-number)
    parts = []
    for scale, word in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand"), (100, "hundred")):
        if number >= scale:
            parts.append(f"{number_to_words(number // scale)} {word}")
            number %= scale
    if number:
        if parts:
            parts.append("and")
        if number < 20:
            parts.append(_ONES[number])
        else:
            parts.append(_TENS[number // 10] + (f"-{_ONES[number % 10]}" if number % 10 else ""))
    return " ".join(parts)


def bearing_to_words(bearing: int) -> str:
    return " ".join(f"{bearing % 360:03d}")


def range_to_words(range_km: int, metric: bool) -> str:
    return str(range_km if metric else int(range_km / 1.852))


def round_altitude(altitude_m: float, metric: bool) -> int:
    if metric:
        value = int(round(altitude_m / 100.0)) * 100
    else:
        value = int(round(altitude_m * FEET_PER_METRE_ORIGINAL / 1000.0)) * 1000
    return 500 if value == 0 else value


def altitude_to_words(altitude_m: float, metric: bool) -> str:
    return number_to_words(round_altitude(altitude_m, metric))


def comments_to_words(altitude_m: float, speed: Optional[float]) -> list[str]:
    comments = []
    if altitude_m > 12192:          # 40,000 ft
        comments.append("high")
    if speed is not None:
        if speed > 463:             # 900 knots
            comments.append("very fast")
        elif speed > 308:           # 600 knots
            comments.append("fast")
    return comments


def aircraft_name(code: str) -> str:
    """Aircraft.GetNameByDcsCode."""
    entry = next((e for e in AIRCRAFT if code in e["dcs_codes"] or e["code"] == code), None)
    return entry["name"] if entry else code


def aircraft_code(code: str) -> str:
    """Aircraft.GetCodeByDcsCode."""
    entry = next((e for e in AIRCRAFT if code in e["dcs_codes"]), None)
    return entry["code"] if entry else code


def unit_counts_to_words(group: list) -> list[str]:
    counts: dict = {}
    for contact in sorted(group, key=lambda u: aircraft_name(u["type"])):
        code = aircraft_code(contact["type"])
        counts[code] = counts.get(code, 0) + 1
    return [aircraft_name(code) if count == 1 else f"{count}-ship {aircraft_name(code)}"
            for code, count in sorted(counts.items(), key=lambda kv: kv[1])]


def aspect(bearing: int, heading: int) -> str:
    """ATP 3-52.4 aspect (BogeyDopeHandler.GetAspect)."""
    angle = diff_heading(bearing, heading)
    cardinal = CARDINAL_POINTS[int(round((heading % 360) / 45))]
    if angle <= 65:
        return f"DRAG {cardinal}"
    if angle < 115:
        return f"BEAM {cardinal}"
    if angle < 155:
        return f"FLANK {cardinal}"
    return "HOT"


_PRONUNCIATIONS = {"anapa-vityazevo": "anapa", "krasnodar-center": "kras center",
                   "krasnodar-pashkovsky": "kras pash"}


def pronounce_airbase(name: str) -> str:
    """AirbasePronouncer.PronounceAirbase without the Azure-only SSML phonemes."""
    return _PRONUNCIATIONS.get(name.lower(), _parser.strip_airbase_suffixes(name))


def ambiguous_callsign(player: dict) -> str:
    group = player.get("group") or "x"
    flight = str(player["flight"]) if player.get("flight", -1) >= 0 else "x"
    element = str(player["element"]) if player.get("element", -1) >= 0 else "x"
    return f"{group} {flight} {element}"


def _speech(*sections) -> Optional[str]:
    parts = [s for s in sections if s is not None]
    return ", ".join(parts) if parts else None


# --------------------------------------------------------------------------
# Unit queries (GrpcDatastore repositories)
# --------------------------------------------------------------------------

def _text(unit, key) -> str:
    return str(unit.get(key) or "").lower()


def is_metric(unit) -> bool:
    return unit.get("type") in METRIC_AIRFRAMES


def is_air(unit) -> bool:
    return unit.get("category") in AIR_CATEGORIES or unit.get("type") in AIR_DCS_CODES


def is_airborne(unit) -> bool:
    """Original: Speed > 25 m/s.  Units without a speed sample are treated as stationary."""
    speed = unit.get("speed")
    return speed is not None and speed > HOSTILE_MIN_SPEED


def opposing(coalition: int) -> Optional[int]:
    return {1: 2, 2: 1}.get(coalition)


def callsign_matches(unit, group: str, flight: int, element: int) -> bool:
    """PilotOrCallsignMatches, with digit boundaries so "dolt 1-1" is not "dolt 1-12"."""
    g = re.escape(group.lower())
    pattern = re.compile(rf"(?<![a-z]){g} ?{flight}-?{element}(?!\d)")
    return any(pattern.search(_text(unit, key).replace("  ", " ")) for key in ("pilot", "callsign"))


class DuplicateCallsign(Exception):
    pass


def _unique(matches: list):
    if len(matches) > 1:
        raise DuplicateCallsign()
    return matches[0] if matches else None


def find_player(units, player: dict, coalition: Optional[int]):
    """PlayerRepository.FindByCallsign, restricted to the frequency's coalition."""
    return _unique([u for u in units if u.get("pilot")
                    and (coalition not in (1, 2) or u.get("coalition") == coalition)
                    and callsign_matches(u, player["group"], player["flight"], player["element"])])


def find_unit_in_coalition(units, player: dict, coalition: int):
    """UnitRepository.FindByCallsignAndCoalition."""
    return _unique([u for u in units if u.get("coalition") == coalition
                    and callsign_matches(u, player["group"], player["flight"], player["element"])])


def hostile_aircraft(units, source, codes: Optional[list] = None) -> list:
    """UnitRepository.FindHostileAircraft: airborne aircraft of the opposing coalition, nearest first."""
    enemy = opposing(source.get("coalition"))
    if enemy is None:
        return []
    found = [u for u in units if u.get("coalition") == enemy and is_air(u) and is_airborne(u)
             and (not codes or u.get("type") in codes)]
    return sorted(found, key=lambda u: distance_m(*_pos(source), *_pos(u)))


def find_awacs(units, pilot: str):
    """UnitRepository.FindAwacs."""
    return next((u for u in units if u.get("type") in AWACS_TYPES and is_air(u)
                 and (str(u.get("pilot") or "").startswith(pilot) or str(u.get("name") or "").startswith(pilot))),
                None)


def codes_by_identifier(identifier: str) -> Optional[list]:
    """Aircraft.GetCodesByIdentifier, accepting spoken plurals ("flankers")."""
    for ident in (identifier, identifier[:-1] if identifier.endswith("s") else None):
        if not ident:
            continue
        key = _parser.norm(ident)
        if key in _parser.AIRCRAFT_ALIAS_CODES:
            return list(_parser.AIRCRAFT_ALIAS_CODES[key])
        for e in AIRCRAFT:
            if key in (_parser.norm(e["name"]), _parser.norm(e["code"])) or any(
                    key == _parser.norm(c) for c in e["dcs_codes"]):
                return list(e["dcs_codes"])
    return None


def codes_by_attributes(attributes: Iterable[str]) -> list:
    wanted = set(attributes)
    return [c for e in AIRCRAFT if wanted <= set(e["attributes"]) for c in e["dcs_codes"]]


# --------------------------------------------------------------------------
# AWACS details (BogeyDopeHandler, BoundaryChecker, MergedChecker)
# --------------------------------------------------------------------------

def _group_of(source, closest, contacts) -> list:
    group = [c for c in contacts if c is not closest
             and dist_nm(closest, c) < GROUP_RADIUS_NM
             and diff_heading(int(closest["heading"]), int(c["heading"])) < 90
             and abs(dist_nm(source, closest) - dist_nm(source, c)) < 3]
    group.append(closest)
    return group


def bra_sections(source, contacts: list, when: _dt.datetime) -> tuple[list[str], list]:
    """BogeyDopeDetails.ToSpeech without the leading "bra"; returns (sections, group)."""
    closest = contacts[0]
    true_brg = true_bearing(source, closest)
    bearing = int(true_to_magnetic(source["lat"], source["lon"], true_brg, when))
    range_km = int(dist_km(source, closest))
    metric = is_metric(source)
    group = _group_of(source, closest, contacts)
    sections = [bearing_to_words(bearing), range_to_words(range_km, metric),
                altitude_to_words(closest["altitude"], metric),
                aspect(int(true_brg), int(closest["heading"]))]
    sections += comments_to_words(closest["altitude"], closest.get("speed"))
    sections += unit_counts_to_words(group)
    return sections, group


def _location_of_airfield(source, airfield, when) -> str:
    bearing = int(true_to_magnetic(source["lat"], source["lon"], true_bearing(source, airfield), when))
    range_km = int(dist_km(source, airfield))
    status = None
    coalition = airfield.get("coalition")
    # Tacview reports neutral and unknown ownership both as 0, so only an
    # explicit opposing coalition is announced ("Neutral airfield" is not).
    if coalition in (1, 2) and coalition != source.get("coalition"):
        status = "Hostile airfield"
    return _speech(status, pronounce_airbase(airfield["name"]), f"bearing {bearing_to_words(bearing)}",
                   range_to_words(range_km, is_metric(source)))


def _location_of_unit(source, subject, when) -> str:
    bearing = int(true_to_magnetic(source["lat"], source["lon"], true_bearing(source, subject), when))
    if is_metric(source):
        rng = int(dist_km(source, subject))
        altitude = int(round(subject["altitude"] / 100.0)) * 100
    else:
        rng = int(dist_nm(source, subject))
        altitude = int(round(subject["altitude"] * FEET_PER_METRE_ORIGINAL / 1000.0)) * 1000
    if altitude < 1000:
        angels = 1
    else:
        angels = (altitude + 1000 - altitude % 1000 if altitude % 1000 >= 500 else altitude - altitude % 1000) // 1000
    return f"Bra, {bearing_to_words(bearing)}, {rng}, Angels {angels}"


# --------------------------------------------------------------------------
# Airfields (Encyclopedia.Airfield + AirfieldStatus + TaxiRouter)
# --------------------------------------------------------------------------

class NoPath(Exception):
    pass


class Airfield:
    """Navigation graph of one encyclopedia airfield."""

    def __init__(self, data: dict):
        self.name = data["name"]
        self.lat, self.lon, self.altitude = data["lat"], data["lon"], data["alt"]
        self.runways = [dict(r, kind="runway") for r in data.get("runways") or []]
        parking = [dict(p, kind="parking") for p in data.get("parking_spots") or []]
        junctions = [dict(j, kind="junction") for j in data.get("junctions") or []]
        self.waypoints = [dict(w, kind="waypoint") for w in data.get("waypoints") or []]
        self.vertices = self.runways + parking + junctions + self.waypoints
        by_name: dict = {}
        for v in self.vertices:
            by_name.setdefault(v["name"], v)
        self.edges: dict = {id(v): [] for v in self.vertices}
        self.edge_count = 0
        for path in data.get("navigation_paths") or []:
            source, target = by_name.get(path["source"]), by_name.get(path["target"])
            if source is None or target is None:
                continue  # AirfieldStatus.BuildTaxiGraph logs and skips unknown endpoints
            self.edges[id(source)].append((target, path["name"], path["cost"]))
            self.edge_count += 1
        self.taxi_points = [v for v in self.vertices if v["kind"] != "waypoint"]
        self.runway_nodes = {id(r): self._runway_nodes(r) for r in self.runways}

    def _runway_nodes(self, runway) -> list:
        """AirfieldStatus.BuildTaxiGraph: the "Runway"-tagged chain walked from one runway end."""
        nodes = [runway]
        node = runway
        while True:
            node = next((t for t, tag, _ in self.edges[id(node)]
                         if tag == "Runway" and all(t is not n for n in nodes)), None)
            if node is None:
                return nodes
            nodes.append(node)
            if node["kind"] == "runway":
                return nodes

    def runways_containing(self, point) -> list:
        """Runways (each direction) whose node chain contains ``point``."""
        return [r for r in self.runways if any(n is point for n in self.runway_nodes[id(r)])]

    def shortest_paths(self, source) -> dict:
        """Dijkstra from ``source``: vertex id -> (cost, [(from, to, tag), ...])."""
        best = {id(source): (0, [])}
        heap = [(0, 0, source)]
        counter = 1
        done = set()
        while heap:
            cost, _, vertex = heapq.heappop(heap)
            if id(vertex) in done:
                continue
            done.add(id(vertex))
            for target, tag, weight in self.edges[id(vertex)]:
                new = cost + weight
                if id(target) not in best or new < best[id(target)][0]:
                    best[id(target)] = (new, best[id(vertex)][1] + [(vertex, target, tag)])
                    heapq.heappush(heap, (new, counter, target))
                    counter += 1
        return best

    def active_runways(self, wind_heading: float) -> list:
        """ActiveRunwayDecider: runways within 90 degrees of the wind direction."""
        return [r for r in self.runways if diff_heading(int(wind_heading), int(r["heading"])) < 90]

    def taxi_route(self, unit, wind_heading: float) -> tuple[str, list, list]:
        """TaxiRouter.Process: (destination runway name, taxiway names, taxi points ending at the runway)."""
        if not self.taxi_points:
            raise NoPath("Unknown Destination")
        source = min(self.taxi_points, key=lambda p: distance_m(*_pos(unit), *_pos(p)))
        runways = self.active_runways(wind_heading)
        if not runways:
            raise NoPath("the active runway")
        paths = self.shortest_paths(source)
        options = [(paths[id(r)][0], i, r) for i, r in enumerate(runways) if id(r) in paths and paths[id(r)][1]]
        if not options:
            raise NoPath(runways[0]["name"] if len(runways) == 1 else "the active runway")
        _, _, runway = min(options, key=lambda o: (o[0], o[1]))
        edges = paths[id(runway)][1]
        taxiways = []
        for _, _, tag in edges:
            if not taxiways or taxiways[-1] != tag:
                taxiways.append(tag)
        return runway["name"], taxiways, [edge[0] for edge in edges] + [edges[-1][1]]

    def taxi_instructions(self, unit, wind_heading: float) -> tuple[str, list]:
        """TaxiRouter.Process: (destination runway name, taxiway names)."""
        return self.taxi_route(unit, wind_heading)[:2]

    def approach_route(self, unit, wind_heading: float) -> list:
        """AirfieldStatus.GetApproachRoute: entry waypoint ... runway."""
        runways = self.active_runways(wind_heading)
        candidates = []
        for entry in (w for w in self.waypoints if "entry" in w["name"].lower()):
            paths = self.shortest_paths(entry)
            for runway in runways:
                if id(runway) in paths and paths[id(runway)][1]:
                    candidates.append(paths[id(runway)])
        if not candidates:
            raise NoPath("the active runway")
        cheapest = min(c[0] for c in candidates)
        cheapest_paths = [c[1] for c in candidates if c[0] == cheapest]
        path = min(cheapest_paths, key=lambda p: distance_m(*_pos(p[0][0]), *_pos(unit)))
        return [edge[0] for edge in path] + [path[-1][1]]


AIRFIELDS = [Airfield(d) for d in _load_airfields()]


def _airfield_key(name: str) -> str:
    return _parser.norm(_parser.strip_airbase_suffixes(name))


def encyclopedia_airfield(name: Optional[str]) -> Optional[Airfield]:
    if not name:
        return None
    key = _airfield_key(name)
    return next((a for a in AIRFIELDS if _airfield_key(a.name) == key), None)


def _world_airfields(units) -> list:
    return [u for u in units if u.get("category") in ("Airfield", "Ship")]


def find_airfield(units, name: str):
    """AirfieldRepository.FindByName over Tacview aerodromes/ships, then the encyclopedia."""
    key = _airfield_key(name)
    for u in _world_airfields(units):
        if key and key in (_airfield_key(str(u.get("name") or "")), _airfield_key(str(u.get("type") or ""))):
            return {"name": name if u.get("category") == "Ship" else u.get("name") or name,
                    "lat": u["lat"], "lon": u["lon"], "coalition": u.get("coalition")}
    af = encyclopedia_airfield(name)
    if af:
        return {"name": af.name, "lat": af.lat, "lon": af.lon, "coalition": None}
    return None


def nearest_airfield(units, source, exclude_coalition: Optional[int] = None):
    """AirfieldRepository.FindByClosest (land airfields, any coalition).

    ``exclude_coalition`` (1 or 2) skips airfields explicitly owned by that coalition."""
    listed = [{"name": u.get("name") or u.get("type"), "lat": u["lat"], "lon": u["lon"],
               "coalition": u.get("coalition")} for u in units if u.get("category") == "Airfield"]
    seen = {_airfield_key(o["name"]) for o in listed}
    options = [o for o in listed if exclude_coalition not in (1, 2) or o["coalition"] != exclude_coalition]
    options += [{"name": a.name, "lat": a.lat, "lon": a.lon, "coalition": None}
                for a in AIRFIELDS if _airfield_key(a.name) not in seen]
    return min(options, key=lambda o: distance_m(*_pos(source), *_pos(o)))


# --------------------------------------------------------------------------
# Warning radius monitoring (AwacsMonitor + WarningRadiusChecker)
# --------------------------------------------------------------------------

class WarningMonitor:
    """Tripwire / merged warnings; call :meth:`check` about once per second."""

    def __init__(self):
        self._lock = threading.Lock()
        self._players: dict = {}
        self._serial = 0

    def set(self, unit: dict, callsign: str, distance: int) -> None:
        with self._lock:
            self._serial += 1
            self._players[unit["id"]] = {"callsign": callsign, "distance": distance,
                                         "coalition": unit.get("coalition"),
                                         "merged": set(), "boundary": set(), "revision": self._serial}

    def cancel(self, unit_id: str) -> None:
        with self._lock:
            self._players.pop(unit_id, None)

    def revisions(self):
        with self._lock:
            return {key: entry["revision"] for key, entry in self._players.items()}

    def monitored(self) -> dict:
        with self._lock:
            return {k: v["distance"] for k, v in self._players.items()}

    def check(self, units: list, when: Optional[_dt.datetime] = None,
              awacs_pilot: Optional[str] = None) -> list[dict]:
        """Returns ``[{"unit_id", "coalition", "reply"}]`` for warnings due now."""
        when = when or _dt.datetime.now(_dt.timezone.utc)
        if awacs_pilot and find_awacs(units, awacs_pilot) is None:
            return []
        by_id = {u["id"]: u for u in units}
        out = []
        with self._lock:
            for unit_id, entry in list(self._players.items()):
                unit = by_id.get(unit_id)
                if unit is None or unit.get("coalition") != entry["coalition"]:
                    del self._players[unit_id]
                    continue
                speed = unit.get("speed")
                if speed is None or speed < STATIONARY_SPEED:
                    continue  # on the ground at a busy airfield: do not warn
                contacts = hostile_aircraft(units, unit)
                live = {c["id"] for c in contacts}
                entry["merged"] &= live
                entry["boundary"] &= live
                reply = self._merged(unit, contacts, entry, when)
                if reply is None and entry["distance"] >= MERGED_BOUNDARY_NM:
                    reply = self._boundary(unit, contacts, entry, when)
                if reply:
                    out.append({"unit_id": unit_id, "coalition": entry["coalition"],
                                "reply": _speech(entry["callsign"], reply)})
        return out

    @staticmethod
    def _merged(unit, contacts, entry, when) -> Optional[str]:
        fresh = [c for c in contacts if c["id"] not in entry["merged"]
                 and dist_nm(unit, c) < MERGED_BOUNDARY_NM + 1]
        if not fresh or int(dist_nm(unit, fresh[0])) > MERGED_BOUNDARY_NM:
            return None
        closest = fresh[0]
        bearing = int(true_to_magnetic(unit["lat"], unit["lon"], true_bearing(unit, closest), when))
        group = [c for c in fresh if c is not closest and dist_nm(closest, c) < GROUP_RADIUS_NM] + [closest]
        ids = {c["id"] for c in group}
        entry["merged"] |= ids
        entry["boundary"] |= ids
        return _speech("merged", bearing_to_words(bearing), altitude_to_words(closest["altitude"], is_metric(unit)),
                       *unit_counts_to_words(group))

    @staticmethod
    def _boundary(unit, contacts, entry, when) -> Optional[str]:
        reported = entry["merged"] | entry["boundary"]
        fresh = [c for c in contacts if c["id"] not in reported and dist_nm(unit, c) < entry["distance"] + 6]
        if not fresh:
            return None
        metric = is_metric(unit)
        closest_distance = int(dist_km(unit, fresh[0]) if metric else dist_nm(unit, fresh[0]))
        if closest_distance > entry["distance"]:
            return None
        sections, group = bra_sections(unit, fresh, when)
        entry["boundary"] |= {c["id"] for c in group}
        return _speech("threat", "bra", *sections)


# --------------------------------------------------------------------------
# ATC follow-up control (AtcMonitor + ControlledAircraft)
# --------------------------------------------------------------------------

TAXI_CONTROL_RADIUS_NM = 5      # CheckProgress: taxiing aircraft further than this from the airfield are dropped
TAKEOFF_ROLL_SPEED = 30         # m/s, CheckClearedTakeoff
AIRBORNE_SPEED = 60             # m/s, CheckTakeoffRoll
AIRBORNE_HEIGHT = 33            # metres above airfield elevation, CheckTakeoffRoll
TOUCHDOWN_HEIGHT = 10           # metres above airfield elevation, CheckTouchdown
TOUCHDOWN_SPEED = 40            # m/s, CheckTouchdown
NAV_LINE_SPACING_M = 0.2 * 1852.001
NAV_LINE_POINTS = 15

# ControlledAircraft.ConfigureStateMachine: state -> {trigger: next state}.
ATC_TRANSITIONS = {
    "Parked": {"StartTaxi": "TaxiToRunway"},
    "TaxiToRunway": {"HoldShort": "HoldingShort", "LineUpAndWait": "LinedUpAndWaiting",
                     "ClearTakeoff": "ClearedForTakeoff"},
    "HoldingShort": {"LineUpAndWait": "LinedUpAndWaiting", "StartTaxi": "TaxiToRunway",
                     "ClearTakeoff": "ClearedForTakeoff"},
    "LinedUpAndWaiting": {"ClearTakeoff": "ClearedForTakeoff"},
    "ClearedForTakeoff": {"StartTakeoffRoll": "Rolling"},
    "Rolling": {"Takeoff": "Outbound"},
    "Outbound": {"LeaveAtcControl": "Flying"},
    "Flying": {"StartInbound": "Inbound"},
    "Inbound": {"TurnBase": "Base", "Abort": "Aborted", "StartInbound": "Inbound"},
    "Base": {"TurnFinal": "Final", "Abort": "Aborted"},
    "Final": {"EnterShortFinal": "ShortFinal", "Abort": "Aborted"},
    "ShortFinal": {"Touchdown": "Landed", "Abort": "Aborted"},
    "Landed": {"LeaveRunway": "TaxiToRamp", "Abort": "Aborted"},
    "TaxiToRamp": {},
    "Aborted": {"StartInbound": "Inbound"},
}
INBOUND_STATES = ("Inbound", "Base", "Final", "ShortFinal")
STATES_REQUIRING_HOLD_SHORT = ("Base", "Final", "ShortFinal", "LinedUpAndWaiting", "ClearedForTakeoff")
STATES_PREVENTING_RUNWAY_CROSSING = ("Base", "Final", "ShortFinal", "Landed", "LinedUpAndWaiting",
                                     "ClearedForTakeoff", "Rolling")
STATES_REQUIRING_LINE_UP_AND_WAIT = ("Landed", "Rolling")


class ControlledAircraft:
    """One aircraft under ATC control and its remaining navigation points (the last is the runway)."""

    def __init__(self, unit: dict, coalition: int, callsign: str, airfield: Airfield, points: list, state: str):
        self.unit_id = unit["id"]
        self.unit = unit
        self.coalition = coalition
        self.callsign = callsign
        self.airfield = airfield
        self.points = list(points)
        self.current = self.points[0]
        self.next = None
        self.line: list = []
        self.state = state
        self.update_sent = 0

    @property
    def destination(self):
        return self.points[-1]


class AtcMonitor:
    """Follow-up ATC calls for aircraft that received taxi or inbound instructions.

    :func:`handle` with ``atc_monitor=`` starts tracking on a successful taxi or
    inbound reply and stops it on "abort inbound".  Call :meth:`check` with the
    current world about every 100 ms - 1 s; transitions use only telemetry
    position, ``speed`` (m/s) and ``altitude`` (metres MSL) plus encyclopedia
    geometry, and conflicts consider only aircraft tracked here.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._aircraft: dict = {}
        self._out: list = []
        self._when: Optional[_dt.datetime] = None
        self._serial = 0
        self._command_revisions = {}

    def tracked(self) -> dict:
        """``{unit_id: state}`` of every aircraft under control."""
        with self._lock:
            return {k: v.state for k, v in self._aircraft.items()}

    def revisions(self):
        with self._lock:
            return {key: f"{self._command_revisions[key]}:{aircraft.state}"
                    for key, aircraft in self._aircraft.items()}

    def taxi(self, unit: dict, callsign: str, airfield: Airfield, points: list) -> None:
        """AtcMonitor.ProcessTransmission(ReadyToTaxi): ``points`` from :meth:`Airfield.taxi_route`."""
        self._start(ControlledAircraft(unit, unit.get("coalition"), callsign, airfield, points, "Parked"), "StartTaxi")

    def inbound(self, unit: dict, callsign: str, airfield: Airfield, route: list) -> None:
        """AtcMonitor.ProcessTransmission(InboundToAirfield): ``route`` from :meth:`Airfield.approach_route`."""
        position = {"name": "Inbound Position", "kind": "position", "lat": unit["lat"], "lon": unit["lon"]}
        self._start(ControlledAircraft(unit, unit.get("coalition"), callsign, airfield, [position] + list(route),
                                       "Flying"), "StartInbound")

    def abort(self, unit_id: str) -> None:
        """AtcMonitor.ProcessTransmission(AbortInbound): only inbound/landing states can abort."""
        with self._lock:
            aircraft = self._aircraft.get(unit_id)
            if aircraft is not None:
                self._fire(aircraft, "Abort")

    def _start(self, aircraft: ControlledAircraft, trigger: str) -> None:
        with self._lock:
            self._serial += 1
            self._command_revisions[aircraft.unit_id] = self._serial
            self._aircraft.pop(aircraft.unit_id, None)
            self._aircraft[aircraft.unit_id] = aircraft
            self._out = []  # the taxi and inbound entry actions transmit nothing
            self._fire(aircraft, trigger)

    def check(self, units: list, when: Optional[_dt.datetime] = None) -> list[dict]:
        """AtcMonitor.CheckProgress: ``[{"unit_id", "coalition", "reply"}]`` for calls due now."""
        by_id = {u["id"]: u for u in units}
        out: list = []
        with self._lock:
            self._out, self._when = out, when or _dt.datetime.now(_dt.timezone.utc)
            airfields = []
            for aircraft in self._aircraft.values():
                if all(a is not aircraft.airfield for a in airfields):
                    airfields.append(aircraft.airfield)
            for airfield in airfields:
                players = [a for a in self._aircraft.values() if a.airfield is airfield]
                for aircraft in self._sorted(players, by_id):
                    if self._aircraft.get(aircraft.unit_id) is aircraft:
                        self._progress(aircraft, by_id.get(aircraft.unit_id))
        return out

    @staticmethod
    def _sorted(players: list, by_id: dict) -> list:
        """AtcMonitor.SortPlayers: inbound, lined up, holding short, then the rest nearest their next point."""
        def gap(a):
            unit = by_id.get(a.unit_id, a.unit)
            return distance_m(*_pos(unit), *_pos(a.current))
        inbound = [a for a in players if a.state in INBOUND_STATES]
        lined_up = [a for a in players if a.state == "LinedUpAndWaiting"]
        holding = sorted((a for a in players if a.state == "HoldingShort"), key=gap)
        ordered = inbound + lined_up + holding
        return ordered + sorted((a for a in players if all(a is not o for o in ordered)), key=gap)

    def _progress(self, aircraft: ControlledAircraft, unit: Optional[dict]) -> None:
        if (unit is None or unit.get("coalition") != aircraft.coalition
                or aircraft.state == "TaxiToRunway" and aircraft.airfield.taxi_points
                and dist_nm(aircraft.airfield.taxi_points[0], unit) > TAXI_CONTROL_RADIUS_NM):
            self._remove(aircraft)
            return
        aircraft.unit = unit
        check = {
            "TaxiToRunway": self._check_taxi, "HoldingShort": self._check_holding_short,
            "LinedUpAndWaiting": self._check_lined_up, "ClearedForTakeoff": self._check_cleared_takeoff,
            "Rolling": self._check_takeoff_roll, "Inbound": self._check_inbound,
            "Base": self._check_approaching_final, "Final": self._check_approaching_short_final,
            "ShortFinal": self._check_touchdown, "Landed": self._check_left_runway,
        }.get(aircraft.state)
        if check:
            check(aircraft)

    # -- state machine -------------------------------------------------------

    def _fire(self, aircraft: ControlledAircraft, trigger: str) -> None:
        target = ATC_TRANSITIONS[aircraft.state].get(trigger)
        if target is None:
            return  # Stateless OnUnhandledTrigger: logged and ignored
        aircraft.state = target
        entry = {
            "TaxiToRunway": self._enter_taxi, "HoldingShort": self._enter_holding_short,
            "LinedUpAndWaiting": self._enter_line_up, "ClearedForTakeoff": self._enter_cleared_takeoff,
            "Outbound": self._remove, "Inbound": self._enter_inbound, "Base": self._enter_base,
            "Final": self._enter_final, "ShortFinal": self._enter_short_final, "Landed": self._enter_landed,
            "TaxiToRamp": self._enter_taxi_to_ramp, "Aborted": self._remove,
        }.get(target)
        if entry:
            entry(aircraft)

    def _remove(self, aircraft: ControlledAircraft) -> None:
        if self._aircraft.get(aircraft.unit_id) is aircraft:
            del self._aircraft[aircraft.unit_id]

    def _say(self, aircraft: ControlledAircraft, text: str) -> None:
        self._out.append({"unit_id": aircraft.unit_id, "coalition": aircraft.coalition,
                          "reply": f"{aircraft.callsign}, {text}"})

    def _traffic(self, aircraft: ControlledAircraft, states: tuple, runways: list) -> bool:
        return any(other.state in states and any(other.destination is r for r in runways)
                   for other in self._aircraft.values() if other.airfield is aircraft.airfield)

    def _heading_to_next(self, aircraft: ControlledAircraft, comment: str = "") -> None:
        """TransmitHeadingToNextWaypoint."""
        unit = aircraft.unit
        heading = int(round(true_to_magnetic(unit["lat"], unit["lon"], true_bearing(unit, aircraft.next),
                                             self._when))) % 360
        self._say(aircraft, f"fly heading {bearing_to_words(heading)} for {aircraft.next['name']}{comment}")

    def _update_navigation_points(self, aircraft: ControlledAircraft) -> bool:
        """UpdateNavigationPoints: advance one point and build the line of points that counts as reaching it."""
        if len(aircraft.points) < 2:
            self._remove(aircraft)  # the original indexes past the route here; stop controlling instead
            return False
        aircraft.current, aircraft.next = aircraft.points[0], aircraft.points[1]
        aircraft.points = aircraft.points[1:]
        if "initial" in aircraft.next["name"].lower():
            along = true_bearing(aircraft.next, aircraft.destination)   # extended runway course
            directions = (along, (along + 180) % 360)
        else:
            travel = true_bearing(aircraft.current, aircraft.next)      # perpendicular to the leg
            directions = ((travel - 90) % 360, (travel + 90) % 360)
        aircraft.line = [aircraft.next]
        for side, direction in zip(("Left", "Right"), directions):
            for i in range(1, NAV_LINE_POINTS + 1):
                lat, lon = destination(aircraft.next["lat"], aircraft.next["lon"], direction, NAV_LINE_SPACING_M * i)
                aircraft.line.append({"name": f"{side} {i}", "lat": lat, "lon": lon})
        return True

    @staticmethod
    def _time_to_next(aircraft: ControlledAircraft) -> Optional[float]:
        """TimeToNextNavigationPoint in seconds; ``None`` without a positive speed sample."""
        speed = aircraft.unit.get("speed")
        if speed is None or speed <= 0:
            return None
        return min(distance_m(*_pos(aircraft.unit), *_pos(p)) for p in aircraft.line) / speed

    # -- entry actions -------------------------------------------------------

    @staticmethod
    def _enter_taxi(aircraft: ControlledAircraft) -> None:
        aircraft.current = aircraft.points[0]

    def _enter_holding_short(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, "hold short of runway")

    def _enter_line_up(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, "Line up and wait")

    def _enter_cleared_takeoff(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, f"Take-off {aircraft.current['name']} at your discretion")

    def _enter_inbound(self, aircraft: ControlledAircraft) -> None:
        aircraft.update_sent = 0
        self._update_navigation_points(aircraft)

    def _enter_base(self, aircraft: ControlledAircraft) -> None:
        if not self._update_navigation_points(aircraft):
            return
        feet = dist_nm(aircraft.next, aircraft.destination) * 300
        altitude = int(math.floor(feet / 100 + 0.5)) * 100
        self._heading_to_next(aircraft, f", descend {altitude}, reduce speed your discretion")
        aircraft.update_sent = 0

    def _enter_final(self, aircraft: ControlledAircraft) -> None:
        if self._update_navigation_points(aircraft):
            self._say(aircraft, f"Turn final {aircraft.destination['name']}, approach speed and altitude your discretion")

    def _enter_short_final(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, f"Check gear, land {aircraft.destination['name']} at your discretion")

    def _enter_landed(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, "Exit runway when able")

    def _enter_taxi_to_ramp(self, aircraft: ControlledAircraft) -> None:
        self._say(aircraft, "Taxi to ramp")
        self._remove(aircraft)

    # -- periodic checks -----------------------------------------------------

    def _check_taxi(self, aircraft: ControlledAircraft) -> None:
        """CheckTaxiProgress."""
        unit = aircraft.unit
        closest = min(aircraft.points, key=lambda p: distance_m(*_pos(p), *_pos(unit)))
        if closest is aircraft.current:
            return
        # Drop every point before the closest one, so skipped points cannot linger in the route.
        aircraft.points = aircraft.points[next(i for i, p in enumerate(aircraft.points) if p is closest):]
        aircraft.current = closest
        if closest["kind"] != "runway":
            return
        if closest is aircraft.destination:
            if self._traffic(aircraft, STATES_REQUIRING_HOLD_SHORT, [closest]):
                self._fire(aircraft, "HoldShort")
            elif self._traffic(aircraft, STATES_REQUIRING_LINE_UP_AND_WAIT, [closest]):
                self._fire(aircraft, "LineUpAndWait")
            else:
                self._fire(aircraft, "ClearTakeoff")
            return
        if self._traffic(aircraft, STATES_PREVENTING_RUNWAY_CROSSING, aircraft.airfield.runways_containing(closest)):
            self._fire(aircraft, "HoldShort")
        else:
            self._say(aircraft, "cross runway at your discretion")

    def _check_holding_short(self, aircraft: ControlledAircraft) -> None:
        """CheckHoldingShort."""
        if len(aircraft.points) == 1:
            if self._traffic(aircraft, STATES_REQUIRING_HOLD_SHORT, [aircraft.current]):
                return
            if self._traffic(aircraft, STATES_REQUIRING_LINE_UP_AND_WAIT, [aircraft.current]):
                self._fire(aircraft, "LineUpAndWait")
            else:
                self._fire(aircraft, "ClearTakeoff")
            return
        crossed = aircraft.airfield.runways_containing(aircraft.current)
        if not self._traffic(aircraft, STATES_PREVENTING_RUNWAY_CROSSING, crossed):
            self._say(aircraft, "cross runway at your discretion")
            self._fire(aircraft, "StartTaxi")

    def _check_lined_up(self, aircraft: ControlledAircraft) -> None:
        """CheckLinedUpAndWaiting."""
        if not self._traffic(aircraft, STATES_REQUIRING_LINE_UP_AND_WAIT, [aircraft.current]):
            self._fire(aircraft, "ClearTakeoff")

    def _check_cleared_takeoff(self, aircraft: ControlledAircraft) -> None:
        speed = aircraft.unit.get("speed")
        if speed is not None and speed > TAKEOFF_ROLL_SPEED:
            self._fire(aircraft, "StartTakeoffRoll")

    def _check_takeoff_roll(self, aircraft: ControlledAircraft) -> None:
        speed, altitude = aircraft.unit.get("speed"), aircraft.unit.get("altitude")
        if (speed is not None and altitude is not None and speed > AIRBORNE_SPEED
                and altitude > aircraft.airfield.altitude + AIRBORNE_HEIGHT):
            self._fire(aircraft, "Takeoff")

    def _check_inbound(self, aircraft: ControlledAircraft) -> None:
        seconds = self._time_to_next(aircraft)
        if seconds is None or "entry" not in aircraft.next["name"].lower():
            return
        if seconds < 10:
            self._fire(aircraft, "TurnBase")
            return
        if aircraft.update_sent == 0 and 45 <= seconds < 90:
            aircraft.update_sent += 1
            self._heading_to_next(aircraft)
        if aircraft.update_sent == 1 and 20 <= seconds < 45:
            aircraft.update_sent += 1
            self._heading_to_next(aircraft, ", descend and maintain 1000")

    def _check_approaching_final(self, aircraft: ControlledAircraft) -> None:
        seconds = self._time_to_next(aircraft)
        if seconds is not None and seconds < 25:
            self._fire(aircraft, "TurnFinal")

    def _check_approaching_short_final(self, aircraft: ControlledAircraft) -> None:
        seconds = self._time_to_next(aircraft)
        if seconds is not None and seconds < 30:
            self._fire(aircraft, "EnterShortFinal")

    def _check_touchdown(self, aircraft: ControlledAircraft) -> None:
        speed, altitude = aircraft.unit.get("speed"), aircraft.unit.get("altitude")
        if (speed is not None and altitude is not None and speed < TOUCHDOWN_SPEED
                and altitude < aircraft.airfield.altitude + TOUCHDOWN_HEIGHT):
            self._fire(aircraft, "Touchdown")

    def _check_left_runway(self, aircraft: ControlledAircraft) -> None:
        closest = min(aircraft.airfield.taxi_points, key=lambda p: distance_m(*_pos(p), *_pos(aircraft.unit)))
        if "runway" not in closest["name"].lower():
            self._fire(aircraft, "LeaveRunway")


# --------------------------------------------------------------------------
# Responders
# --------------------------------------------------------------------------

class _Reply:
    def __init__(self):
        self.caller: Optional[str] = None
        self.responder: Optional[str] = None
        self.message: Optional[str] = None
        self.notes: Optional[str] = None
        self.stop = False

    def speech(self) -> Optional[str]:
        if self.message is None:
            return None
        return _speech(self.caller, self.responder, self.message)


def _determine_transmitter(t: dict, reply: _Reply, units, coalition):
    """BaseResponder.DetermineTransmitter."""
    player = t.get("transmitter")
    if not player or (player.get("group") is None and player["flight"] < 0 and player["element"] < 0):
        reply.caller = "last transmitter"
        reply.message = "i could not recognize your callsign"
        reply.notes = "Could not find a transmitter callsign in the transmission"
        reply.stop = True
        return None
    if player.get("group") is None or player["flight"] < 0 or player["element"] < 0:
        reply.caller = ambiguous_callsign(player)
        reply.message = "i could not fully recognize your callsign"
        reply.notes = "Could not find a full transmitter callsign in the transmission"
        reply.stop = True
        return None
    reply.caller = f"{player['group']} {player['flight']} {player['element']}"
    try:
        unit = find_player(units, player, coalition)
    except DuplicateCallsign:
        reply.message = "I found more than one unit with a matching callsign"
        reply.stop = True
        return None
    if unit is None:
        reply.message = "i could not find you on scope"
        reply.notes = "No live player unit with this callsign in its pilot name or callsign"
        reply.stop = True
    return unit


def _awacs(t: dict, units, coalition, bot_callsign, when, monitor, awacs_pilot,
           bullseye=None, home_airfield=None) -> _Reply:
    reply = _Reply()
    receiver = t["receiver"]["callsign"]
    callsign = _parser.norm(bot_callsign).replace(" ", "") if bot_callsign else None
    if callsign and receiver not in (callsign, "anyface"):
        reply.notes = f'Transmission was not addressed to "{callsign}". Receiver callsign was "{receiver}"'
        return reply
    reply.responder = callsign or ("overlord" if receiver == "anyface" else receiver)
    awacs = None
    if awacs_pilot:
        awacs = find_awacs(units, awacs_pilot)
        if awacs is None:
            reply.message = f"{reply.responder} is not on station"
            reply.notes = f"{awacs_pilot} is the AWACS unit prefix and no unit with that prefix is alive"
            return reply
    unit = _determine_transmitter(t, reply, units, coalition)
    if reply.stop:
        return reply
    intent = t["intent"]
    if intent == "bogeyDope":
        q = t.get("qualifier") or {}
        codes = None
        if q.get("identifier"):
            codes = codes_by_identifier(q["identifier"])
        elif q.get("attributes"):
            codes = codes_by_attributes(q["attributes"])
        contacts = hostile_aircraft(units, unit, codes)
        if opposing(unit.get("coalition")) is None:
            reply.message = "unable, your coalition is unknown"
        elif contacts:
            sections, _ = bra_sections(unit, contacts, when)
            reply.message = _speech("bra", *sections)
        else:
            reply.message = "Nothing on Scope"
    elif intent == "radioCheck":
        reply.message = "5 by 5"
    elif intent == "locationOfEntity":
        reply.message = _location_of_entity(t, unit, units, when)
    elif intent == "setWarningRadius":
        distance = t.get("distance")
        if monitor is None:
            reply.message = NO_MONITOR_MESSAGE
        elif distance is None or distance < 0:
            reply.message = "i could not catch the distance"
        else:
            monitor.set(unit, reply.caller, distance)
            if distance > 5:
                measurement = "kilometers" if is_metric(unit) else "nautical miles"
                reply.message = f"copy, warning {distance} {measurement}"
            else:
                reply.message = "copy, merge calls only"
            reply.notes = "Distance units are those of the transmitter's airframe, spoken units are ignored"
    elif intent == "cancelWarningRadius":
        if monitor is None:
            reply.message = NO_MONITOR_MESSAGE
        else:
            monitor.cancel(unit["id"])
            reply.message = "copy, cancelling warnings"
    elif intent in ("readyToTaxi", "inboundToAirfield"):
        reply.message = AWACS_ONLY_MESSAGE
    elif intent == "missionAssignment":
        if not t.get("mission_type"):
            reply.message = "I did not hear the mission type"
        else:
            reply.message = MISSION_UNAVAILABLE_MESSAGE
            reply.notes = "No mission/tasking service exists for this server; nothing was assigned"
    elif intent == "missionJoin":
        if not t.get("mission_id"):
            reply.message = "I did not catch the mission ID"
        else:
            reply.message = MISSION_UNAVAILABLE_MESSAGE
            reply.notes = "No mission/tasking service exists for this server; nothing was joined"
    elif intent == "picture":
        reply.message = _picture(unit, units, when)
    elif intent == "declare":
        reply.message = _declare(t, unit, units, when, bullseye)
    elif intent == "friendlyPicture":
        reply.message = _friendly_picture(unit, units, when)
    elif intent == "bullseyePicture":
        reply.message = _bullseye_picture(unit, units, when, bullseye)
    elif intent == "tanker":
        reply.message = _nearest_tanker(unit, units, when)
    elif intent == "vectorHome":
        reply.message = _vector_home(unit, units, when, home_airfield)
    elif intent == "divert":
        reply.message = _divert(unit, units, when)
    elif intent == "sayAgain":
        reply.message = "unable, no previous transmission to repeat"
        reply.notes = "say again is answered from the per-caller cache by the brain; none exists here"
    else:
        reply.notes = f"Unsupported intent {intent} on AWACS"
    return reply


def _location_of_entity(t, unit, units, when) -> str:
    subject = t.get("subject")
    if not subject:
        return "I could not determine what you are looking for"
    if subject["kind"] == "airfield":
        if subject["name"] == "nearest":
            airfield = nearest_airfield(units, unit)
        else:
            airfield = find_airfield(units, subject["name"])
        if airfield is None:
            return f"I could not find {subject['name']}"
        return _location_of_airfield(unit, airfield, when)
    if subject["kind"] == "tanker":
        return "we do not yet support vectors to tankers"
    if subject.get("group") is None or subject["flight"] < 0 or subject["element"] < 0:
        return f"I could not find {ambiguous_callsign(subject)}"
    try:
        target = find_unit_in_coalition(units, subject, unit.get("coalition"))
    except DuplicateCallsign:
        return "I found more than one unit with a matching callsign"
    if target is None:
        return f"I could not find {ambiguous_callsign(subject)}"
    return _location_of_unit(unit, target, when)


def _cluster(source, contacts) -> list[list]:
    """Cluster contacts with the bogey-dope group rules, groups nearest to ``source`` first."""
    remaining = sorted(contacts, key=lambda u: distance_m(*_pos(source), *_pos(u)))
    groups = []
    while remaining:
        group = _group_of(source, remaining[0], remaining)
        ids = {id(c) for c in group}
        remaining = [c for c in remaining if id(c) not in ids]
        groups.append(sorted(group, key=lambda c: dist_nm(source, c)))
    return groups


def picture_groups(source, units) -> list[list]:
    """Hostile airborne contacts clustered with the bogey-dope group rules, nearest first."""
    return _cluster(source, hostile_aircraft(units, source))


def _picture(unit, units, when) -> str:
    if opposing(unit.get("coalition")) is None:
        return "unable, your coalition is unknown"
    groups = picture_groups(unit, units)
    if not groups:
        return "clean"
    sections = ["single group" if len(groups) == 1 else f"{len(groups)} groups"]
    for group in groups[:PICTURE_MAX_GROUPS]:
        details, _ = bra_sections(unit, group, when)
        sections += ["bra", *details]
    return _speech(*sections)


def find_bullseye(units, coalition):
    """The coalition's Tacview bullseye object (``Navaid+Static+Bullseye``)."""
    if coalition not in (1, 2):
        return None
    return next((u for u in units if u.get("coalition") == coalition
                 and "Bullseye" in str(u.get("tacview_type") or "").split("+")), None)


def _declare(t, unit, units, when, bullseye=None) -> str:
    bearing, rng = t.get("bearing"), t.get("range")
    if bearing is None or not rng:
        return "unable to declare, say bearing and range"
    origin = unit
    if t.get("reference") == "bullseye":
        origin = bullseye_reference(units, unit.get("coalition"), bullseye)
        if origin is None:
            return BULLSEYE_UNAVAILABLE_MESSAGE
    metres = rng * (1000.0 if is_metric(unit) else METRES_PER_NM)
    true_brg = (bearing + declination(origin["lat"], origin["lon"], when)) % 360
    lat, lon = destination(origin["lat"], origin["lon"], true_brg, metres)
    point = {"lat": lat, "lon": lon}
    near = [u for u in units if u is not unit and u.get("id") != unit.get("id") and is_air(u)
            and is_airborne(u) and dist_nm(point, u) <= DECLARE_RADIUS_NM]
    own, enemy = unit.get("coalition"), opposing(unit.get("coalition"))
    hostile = [u for u in near if enemy is not None and u.get("coalition") == enemy]
    friendly = [u for u in near if u.get("coalition") == own and own in (1, 2)]
    unknown = [u for u in near if u not in hostile and u not in friendly]
    if hostile and friendly:
        return "furball"
    if hostile:
        return _speech("hostile", *unit_counts_to_words(hostile))
    if friendly:
        return "friendly"
    if unknown:
        return "bogey"
    return "clean"


def _valid_point(point) -> Optional[dict]:
    try:
        lat, lon = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return None
    return {"lat": lat, "lon": lon}


def bullseye_reference(units, coalition, configured=None) -> Optional[dict]:
    """Bullseye position for ``coalition``: its live Tacview object, else the configured
    ``(lat, lon)``, else ``None`` (the callers answer with an explicit "unable")."""
    live = find_bullseye(units, coalition)
    if live is not None:
        return live
    return _valid_point(configured) if configured is not None else None


def _track(heading: float) -> str:
    return f"track {CARDINAL_POINTS[int(round((heading % 360) / 45))].lower()}"


def _group_headline(groups: list) -> str:
    return "single group" if len(groups) == 1 else f"{len(groups)} groups"


def friendly_aircraft(units, source) -> list:
    """Airborne aircraft of the caller's own coalition, nearest first, without the caller."""
    own = source.get("coalition")
    if own not in (1, 2):
        return []
    found = [u for u in units if u.get("coalition") == own and u is not source
             and (u.get("id") is None or u.get("id") != source.get("id"))
             and is_air(u) and is_airborne(u)]
    return sorted(found, key=lambda u: distance_m(*_pos(source), *_pos(u)))


def friendly_tankers(units, source) -> list:
    """Airborne friendly aircraft of a known tanker type, nearest first.  Nothing else
    (callsign, radio, TACAN, fuel) is known from telemetry."""
    return [u for u in friendly_aircraft(units, source) if u.get("type") in TANKER_TYPES]


def _friendly_picture(unit, units, when) -> str:
    if unit.get("coalition") not in (1, 2):
        return "unable, your coalition is unknown"
    groups = _cluster(unit, friendly_aircraft(units, unit))
    if not groups:
        return "no friendly aircraft on scope"
    sections = ["friendly", _group_headline(groups)]
    for group in groups[:PICTURE_MAX_GROUPS]:
        details, _ = bra_sections(unit, group, when)
        sections += ["bra", *details]
    return _speech(*sections)


def _bullseye_picture(unit, units, when, bullseye) -> str:
    if opposing(unit.get("coalition")) is None:
        return "unable, your coalition is unknown"
    origin = bullseye_reference(units, unit.get("coalition"), bullseye)
    if origin is None:
        return BULLSEYE_UNAVAILABLE_MESSAGE
    source = {"lat": origin["lat"], "lon": origin["lon"], "coalition": unit.get("coalition")}
    groups = _cluster(source, hostile_aircraft(units, source))
    if not groups:
        return "clean"
    metric = is_metric(unit)
    sections = [_group_headline(groups)]
    for group in groups[:PICTURE_MAX_GROUPS]:
        closest = group[0]
        bearing = int(true_to_magnetic(source["lat"], source["lon"], true_bearing(source, closest), when))
        sections += ["bullseye", bearing_to_words(bearing), range_to_words(int(dist_km(source, closest)), metric),
                     altitude_to_words(closest["altitude"], metric), _track(closest["heading"]),
                     *comments_to_words(closest["altitude"], closest.get("speed")),
                     *unit_counts_to_words(group)]
    return _speech(*sections)


_TANKER_CALLSIGN = re.compile(r"(?<![a-z])(" + "|".join(sorted(re.escape(c) for c in _parser.TANKERS)) + r") ?(\d)-?(\d)(?!\d)")


def _tanker_callsign(tanker) -> Optional[str]:
    for key in ("callsign", "pilot", "name"):
        m = _TANKER_CALLSIGN.search(str(tanker.get(key) or "").lower())
        if m:
            return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return None


def _nearest_tanker(unit, units, when) -> str:
    if unit.get("coalition") not in (1, 2):
        return "unable, your coalition is unknown"
    tankers = friendly_tankers(units, unit)
    if not tankers:
        return "unable, no friendly tanker on scope"
    tanker = tankers[0]
    return _speech("nearest tanker", _tanker_callsign(tanker) or aircraft_name(tanker["type"]),
                   _location_of_unit(unit, tanker, when))


def _vector_home(unit, units, when, home_airfield) -> str:
    if not home_airfield or not str(home_airfield).strip():
        return "unable, home airfield is not configured"
    airfield = find_airfield(units, str(home_airfield))
    if airfield is None:
        return "unable, home airfield is not known"
    own = unit.get("coalition")
    if own in (1, 2) and airfield.get("coalition") == opposing(own):
        return "unable, home airfield is not friendly"
    return _speech("vector home", _location_of_airfield(unit, airfield, when))


def _divert(unit, units, when) -> str:
    airfield = nearest_airfield(units, unit, exclude_coalition=opposing(unit.get("coalition")))
    return _speech("nearest divert", _location_of_airfield(unit, airfield, when))


def _wind_for(wind, airfield: Airfield) -> Optional[float]:
    if wind is None:
        return None
    if isinstance(wind, (int, float)):
        return float(wind)
    key = _airfield_key(airfield.name)
    for name, heading in wind.items():
        if _airfield_key(name) == key:
            return float(heading)
    return None


def _atc(t: dict, units, coalition, when, wind, atc_monitor) -> _Reply:
    reply = _Reply()
    receiver = t["receiver"]
    unit = _determine_transmitter(t, reply, units, coalition)
    name = receiver.get("name") or "ATC"
    reply.responder = " ".join(x for x in (pronounce_airbase(name), receiver.get("controller")) if x)
    if reply.stop:
        return reply
    airfield = encyclopedia_airfield(receiver.get("name"))
    if airfield is None or not airfield.runways or airfield.edge_count == 0:
        reply.message = NO_ATC_MESSAGE
        reply.notes = f"{receiver.get('name')} has no taxi/approach data in the encyclopedia"
        return reply
    if receiver.get("controller") not in VALID_CONTROLLER_NAMES:
        reply.message = None
        reply.notes = "ATC transmissions must be <AIRFIELD> followed by ground, tower, approach or center"
        return reply
    intent = t["intent"]
    if intent in AWACS_INTENTS:
        reply.message = ATC_ONLY_MESSAGE
    elif intent == "abortInbound":
        reply.message = "Copy abort, call inbound again when ready"
        if atc_monitor is not None:
            atc_monitor.abort(unit["id"])
    elif intent == "radioCheck":
        reply.message = "5 by 5"
    elif intent in ("readyToTaxi", "inboundToAirfield"):
        wind_heading = _wind_for(wind, airfield)
        if wind_heading is None:
            reply.message = NO_WIND_MESSAGE
            reply.notes = "Tacview telemetry carries no wind; pass wind={airfield: heading} to handle()"
        elif intent == "readyToTaxi":
            reply.message, points = _taxi(airfield, unit, wind_heading)
            if points and atc_monitor is not None:
                atc_monitor.taxi(unit, reply.caller, airfield, points)
        else:
            reply.message, route = _inbound(airfield, unit, wind_heading, when)
            if route and atc_monitor is not None:
                atc_monitor.inbound(unit, reply.caller, airfield, route)
    else:
        reply.notes = f"Unsupported intent {intent} on ATC"
    return reply


def _taxi(airfield: Airfield, unit, wind_heading: float) -> tuple[str, Optional[list]]:
    """ReadyToTaxiHandler (fixed "taxi to"/"via" wording instead of random variants): (reply, taxi points)."""
    if not airfield.active_runways(wind_heading):
        return "We could not find any active runways.", None
    try:
        destination_name, taxiways, points = airfield.taxi_route(unit, wind_heading)
    except NoPath as exc:
        return f"We could not find a path from your position to {exc.args[0]}.", None
    text = f"taxi to {destination_name}"
    if taxiways:
        text += f" via {', '.join(taxiways)}"
    return text + ".", points


def _inbound(airfield: Airfield, unit, wind_heading: float, when) -> tuple[str, Optional[list]]:
    """InboundToAirfieldHandler: (reply, approach route)."""
    if not airfield.active_runways(wind_heading):
        return "We could not find any active runways.", None
    try:
        route = airfield.approach_route(unit, wind_heading)
    except NoPath:
        return "We could not find an approach route to the active runway.", None
    heading = int(true_to_magnetic(unit["lat"], unit["lon"], true_bearing(unit, route[0]), when))
    return (f"fly heading {bearing_to_words(heading)}, descend and maintain 2,000, reduce speed below 2 5 0 knots, "
            f"for vectors to {route[-1]['name']}, {route[0]['name']}"), route


def _known_groups(units) -> set:
    groups = set()
    for u in units:
        for key in ("pilot", "callsign"):
            m = re.match(r"\s*([A-Za-z][A-Za-z ]*?)\s*\d", str(u.get(key) or ""))
            if m:
                groups.add(m.group(1).lower().replace(" ", ""))
    return groups


def handle(text: str, coalition: int, units: list, bot_callsign: Optional[str] = "Overlord", *,
           roles: Iterable[str] = ("awacs", "atc"), monitor: Optional[WarningMonitor] = None,
           wind: Union[None, float, Mapping[str, float]] = None,
           awacs_pilot: Optional[str] = None, when: Optional[_dt.datetime] = None,
           atc_monitor: Optional[AtcMonitor] = None,
           bullseye: Optional[tuple[float, float]] = None,
           home_airfield: Optional[str] = None) -> dict:
    """Parse ``text`` and build the spoken reply.

    ``coalition`` is the coalition of the SRS client the call arrived on; the
    transmitter must be a player of that coalition (when it is 1 or 2).
    ``bot_callsign`` restricts AWACS replies to that callsign (or "anyface");
    ``None`` answers any AWACS callsign.  ``roles`` selects AWACS and/or ATC.
    ``monitor`` enables tripwire calls, ``wind`` (degrees the wind blows
    from, global or per airfield) enables runway selection, ``awacs_pilot``
    requires a live AWACS whose pilot/name starts with it, ``when`` fixes the
    magnetic model date.  ``atc_monitor`` starts follow-up control (hold short,
    line up, take-off, base/final/short-final) for successful taxi and inbound
    calls and ends it on "abort inbound"; poll it with ``AtcMonitor.check``.
    ``bullseye`` is a configured ``(lat, lon)`` used for bullseye references when the
    coalition's own Tacview bullseye object is not in the telemetry; without either the
    reply is an explicit "unable".  ``home_airfield`` is the configured name of the field
    "vector home" refers to; it must be a known airfield, never a guess.

    Returns ``{"reply": str | None, "parsed": dict | None, "notes": str | None}``.
    """
    if not text or not text.strip():
        return {"reply": None, "parsed": None, "notes": None}
    when = when or _dt.datetime.now(_dt.timezone.utc)
    parsed = _parser.parse(text, bot_callsign, _known_groups(units))
    roles = set(roles)
    receiver = parsed["receiver"]
    if parsed["intent"] == "None":
        return {"reply": None, "parsed": parsed, "notes": "Transmission is not directed to the bot"}
    if receiver is None:
        return {"reply": None, "parsed": parsed, "notes": "Could not determine the receiver"}
    if receiver["kind"] == "awacs" and "awacs" in roles:
        reply = _awacs(parsed, units, coalition, bot_callsign, when, monitor, awacs_pilot,
                       bullseye=bullseye, home_airfield=home_airfield)
    elif receiver["kind"] == "airfield" and "atc" in roles:
        reply = _atc(parsed, units, coalition, when, wind, atc_monitor)
    else:
        return {"reply": None, "parsed": parsed, "notes": f"Not addressed to a {'/'.join(sorted(roles))} controller"}
    return {"reply": reply.speech(), "parsed": parsed, "notes": reply.notes}
