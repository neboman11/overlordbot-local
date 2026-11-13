# Data provenance

## OverlordBot (AGPL-3.0-or-later)

Source: https://gitlab.com/overlordbot/srs-bot, commit
`c7d34d51bfe66faf6bb8ff9e22688351e99cee70`. OverlordBot is an AWACS/ATC bot for DCS World,
Copyright (C) 2022 Jeffrey Jones, licensed under the GNU Affero General Public License version 3
or (at your option) any later version. These files are derived from it and are distributed under
the same licence (see `LICENSE`).

The YAML files were converted to JSON with PyYAML. Values, order and spelling are unchanged.

| File here | Original file |
|---|---|
| `aircraft.json` | `OverlordBot.Encyclopedia/Data/Encyclopedia/Aircraft.yaml` (76 entries) |
| `airfields/*.json` | `OverlordBot.Encyclopedia/Data/Encyclopedia/Airfields/*.yaml` (25 airfields: positions, runways, parking spots, junctions, waypoints, navigation paths) |
| `milstd2525d.json` | `OverlordBot.Encyclopedia/Data/Encyclopedia/MilStd2525d/{Air,Land,Sea}.yaml` (85/229/30 entries) |
| `aliases.json` | `OverlordBot.Cognitive/Data/Aliases/{Airfields,Callsigns,UnitAttributes}.yaml` |
| `vocabulary.json` `airfield_names`, `mission_types`, `phraselists` | list entities `airfieldName` and `missionTypes` and all `@ phraselist` lines of `OverlordBot.Core/Documentation/OverlordBot.lu` |

The `aircraft` and `aircraft_variants` sections of `aliases.json` are additions by
overlordbot-local, not part of the upstream alias conversion. Original aircraft
labels remain in `aircraft.json`; added reporting-name aliases do not encode
coalition or identification status. External reference documents are not bundled.

`vocabulary.json` `parser_extensions` is **not** from OverlordBot. overlordbot-local added it:

- `nearest_airfield` adds the synonyms "nearest", "closest", "nearest base" and "closest base" for
  the list value `nearest`.
- `awacs_callsigns` adds `wizard`, which is labelled as an AWACS callsign in the `.lu` utterances
  but missing from the phrase list. It also adds `anyface`, which `AwacsResponder` accepts.
- `callsign_fillers` lists words the parser skips between a receiver and a callsign
  ("overlord, this is dolt 1-2").

`tests/fixtures/parser_cases.json` is also derived from OverlordBot, except for the `stt-*` cases:

- It has 489 `lu-*` cases, one for every labelled utterance in `OverlordBot.lu`. `labelled` is the
  original line; the expected entities were extracted from its LUIS labels.
- It has 12 `cs-*` cases, which are text versions of all 20 tests in
  `OverlordBot.Cognitive.Tests/TransmissionParser/{ReceiverParsing,TransmitterParsing}` (the
  missing-request, missing-receiver and missing-transmitter JSON variants share one text). A missing
  element is "Dolt 1" (a lone number is the flight); a missing flight is "Dolt on 2" (a function
  word in place of the flight digit).
- It has 24 `stt-*` cases: the whisper.cpp tiny/base/small transcripts in
  `artifacts/speech-benchmark.json` of Piper LJSpeech audio. Their expected entities are the
  meaning of the spoken reference, not a LUIS label, and `known_groups` lists the scenario's
  callsign groups.
- The 4 `sendTransport` utterances expect intent `None`, because the original `ITransmission.Intents`
  has no such intent.
- Airfield names are canonical (alias-resolved) where the original kept the spoken name.
- Every case is checked. 17 cases have a `correction` (category, reason, fields) and keep the
  extracted original in `label`:
  - 7 `label`: the LUIS label omits or garbles a spoken entity (receiver, transmitter, radius,
    an airfield name that includes "ground").
  - 4 `homophone`: "for"/"too" before a single number is the flight digit 4/2. The benchmark shows
    "Dodge four one" transcribed as "Dodge for one"; LUIS labelled flight unknown, element 1.
  - 2 `policy`: a request with neither receiver nor transmitter ("radio check", "bogey dope") is
    intent `None`. The `.lu` labels both ways and the original C# kept the intent.
  - 4 `ambiguous`: a word directly before the callsign number, or before a link word, is read as
    the group name ("king be 11", "fair across 21", "or kitute. one", "archer or one one") where
    LUIS left it unlabelled. "speed or 1 two" (lu-radioCheck-052) is labelled the same way the
    parser reads it.
- 3 `lu-*` cases pass `known_groups` because only the live unit list resolves them: "war crime"
  is not in the phrase list, and "airfix wild one" / "messenger random one" have two words
  before a lone number.

Phonetic matching in `overlord/parser.py` is not from OverlordBot (LUIS learned STT variants from
the labelled utterances). It is bounded to closed vocabularies: command phrases from the `.lu`
phrase lists, airfields with bundled data (`airfields/*.json`), and the caller's `known_groups`.

## NOAA World Magnetic Model WMM2025 (public domain, US Government work)

- Source: https://www.ncei.noaa.gov/sites/default/files/2024-12/WMM2025COF.zip. The zip has sha256
  `2e76569370d081f2cd7919490218bd094ca9afde347b198eff5621e0af460d03`.
- `WMM2025.COF` has sha256 `dfa8597825af4e0b87ff4198a5b4fb661b3c49f4cd090cd0164e0259b075582f`. It
  is valid from 2025.0 to 2030.0. It replaces the WMM data that OverlordBot got through Geo's
  `WmmGeomagnetismCalculator` for true-to-magnetic bearings.
- `WMM2025_TestValues.txt` has sha256
  `e6975b093dddeb6153e0b23cc418425c438167e7c5b1dd795da379cb654f5819`. It contains NOAA's
  verification values, which `tests/test_domain.py` checks.

## Data limitations (Tacview real-time telemetry instead of DCS-gRPC)

Each limitation below gives an explicit negative reply. None of them gives a guessed answer.

- **Wind / active runway.** Tacview carries no wind. OverlordBot read airbase wind from DCS-gRPC.
  Unless the caller passes `wind=` to `handle`, taxi and inbound calls answer "wind information
  is unavailable, unable to determine the active runway".
- **ATC coverage.** Taxi and approach routing exists only for the 25 encyclopedia airfields, which
  include Caucasus, Al Dhafra, Ras Al Khaimah, Incirlik and Ramat David. Any other airfield
  answers "There are no ATC services currently available at this airfield."
- **ATC follow-up control.** `AtcMonitor` ports the `AtcMonitor` and `ControlledAircraft` state
  machine: hold short, line up and wait, take-off clearance, runway crossing, the inbound heading
  updates, base, final, short final, exit runway and taxi to ramp. It is active only when `handle`
  receives `atc_monitor=` and the monitor is polled with `check(units)`. Only a successful taxi or
  inbound reply starts tracking. A negative reply (no wind, no route, no ATC data) does not.
  Transitions use the original thresholds against Tacview position, derived `speed` (m/s) and
  `altitude` (metres MSL), with the encyclopedia airfield elevation and runway node chains.
  Limits:
  - A missing or zero speed sample never advances a speed-dependent step. Take-off roll,
    airborne, touchdown and every approach step stay where they are.
  - Runway conflicts consider only aircraft tracked by the monitor. Traffic that never called ATC
    is invisible, as in the original. Takeoff and crossing calls stay "at your discretion".
  - As in the original, runway ends are separate destinations. An aircraft departing 04 is not
    held for one landing on 22. A runway crossing is detected only at runway-end vertices, not at
    intermediate runway junctions. A lined-up aircraft waits only for landed or rolling traffic.
  - Divergences from the original: passed taxi points are dropped up to the closest point. The
    original kept skipped points, which could turn "hold short" of the departure runway into
    "cross runway". Waypoint names match "Entry"/"Initial" case-insensitively, because the data
    has `initial-0 4` and `INITIAL 0 7`. A route too short for the next approach leg ends control
    instead of failing. One tracked entry exists per unit across all airfields. Heading updates
    are rounded to the nearest degree, like the original `ToString("000")`.
- **Airfield ownership.** Tacview reports neutral and unknown coalition both as 0. Airfields are
  therefore called "Hostile airfield" only when their coalition is explicitly the opposing one,
  and "Neutral airfield" is never said. Encyclopedia airfields that Tacview does not report have
  no ownership.
- **Airfield list.** The location of an airfield comes from Tacview aerodrome and ship objects
  first, then from the encyclopedia. The other names in the `.lu` list have no bundled
  coordinates and answer "I could not find …".
- **Speed.** Tacview sends no speed. `World` derives `speed` from successive samples. A unit with
  no speed sample (parked, or just spawned) is treated as stationary. Such units are not reported
  as bogeys and do not trigger tripwires, matching the original `Speed > 25` m/s filter.
- **Line of sight.** The original's DEM terrain masking (`restrictedLineOfSight`) is not ported,
  because there is no terrain data.
- **Missions.** Mission assignment and mission join were forwarded to a server-specific gRPC
  service. No such service exists here, so a heard mission type or ID answers "unable to forward
  request", and nothing is assigned.
- **Tankers.** Vectors to tankers answer "we do not yet support vectors to tankers", as in the
  original.
- **Warning radius.** This needs a `WarningMonitor` that `handle` receives as `monitor=` and that
  is polled with `check(units)`. Without one, the reply is "unable, warning radius monitoring is
  not available".
- **Picture and declare.** OverlordBot refused both. These implementations are new. Picture lists
  up to 3 hostile groups nearest first, using the bogey-dope grouping rules. Declare classifies
  airborne aircraft within 5 nm of the declared bearing and range as hostile, friendly, bogey
  (coalition 0), furball or clean.
- **Pronunciation.** The Azure SSML phonemes from `AirbasePronouncer` and the `<break>` tags in taxi
  instructions are dropped, because Piper reads plain text.
