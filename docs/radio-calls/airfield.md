# Airfield radio calls: what you say to native airfield ATC

This guide lists the exact transmissions the native airfield controller (`overlord/airfield.py`, grammar `overlord/airfield_parser.py`) accepts, and what it says back. It was traced from source at `63ba92534ddc60fa3268fbe2549eb1e455ad7748` (app v0.1.0). It describes what this implementation does. It is not real-world FAA, ICAO or NATO procedure, and it adds no phrase the code does not accept.

There are two charts. Each covers one whole procedure in a single connected flow: normal steps, alternatives, refusals, readback retries, revocations, frequency changes, cancellations and session closures.

1. [Departure](#chart-1--departure-startup-to-leaving-the-zone): startup, taxi, runway crossing, holding point, line-up, take-off, airborne handover, plus return to parking, abort and re-departure.
2. [Arrival](#chart-2--arrival-check-in-to-parking): check-in and briefing, published pattern or straight-in, landing clearance, go-around and touch-and-go, vacate, taxi to parking with crossings, check-out.

Validation manifest: [`airfield-calls.json`](airfield-calls.json). It gives sample values for every `YOU` node, parser expectations, readback validator inputs, and branch coverage with source lines.

## How to read the charts

**Path colors:** green solid = normal progression; red = refusal/error; amber dashed = retry, recovery, emergency or cancellation; gray = optional status/shared checks. Calls and conditions remain explicit: color is not the only cue.

| Shape | Meaning |
|---|---|
| `YOU:` followed by **bold words** (rectangle) | **Your transmission.** Say only the words after `YOU:`, replacing placeholders. Do not say `YOU:` or the brackets. |
| `ID(["ATC: …"])` (stadium) | The controller's reply, or an `ATC event` it transmits unprompted. Replies arrive as `[CALLSIGN], [FIELD] [role], <message>`. |
| `ID{{"WAIT/FLY/TAXI — NO CALL: …"}}` (hexagon) | Nothing to say. Mission telemetry (the bridge), not your words, moves the procedure on. |
| `ID(("…"))` (circle) | Entry, exit, or hand-over to the other chart. |
| Dotted arrow `-.->` | RETRY or RETURN: fix the cause, then make the call the arrow points to. |

Pilot claims never replace telemetry. `holding short`, `lined up`, `vacated`, `final`, `downwind`, `base`, `airborne` and `landed` only report a position. Each is accepted only when the latest bridge sample measures that position. Otherwise the reply starts with `negative`. `airborne` and `landed` just confirm what the controller has already seen. They never authorise anything.

## Operator rules (apply to every call)

1. **Call shape:** `[FIELD] <role>, [CALLSIGN], <one command>`. Open with the airfield name and/or the role (`ground`, `tower`, `approach`). Put your callsign (`Viper one one`) or modex (`one zero one`) next, then **one** command phrase. The parser takes the command phrase that starts earliest, so put nothing else before it. With two commands in one call, only the first counts (`airfield_parser.py` 9, 196-200).
2. **Name the airfield.** If one radio frequency is shared by several airfields, a call without a name gets `say which airfield you are calling, …`. The exception is a call from an aircraft that already has a session on one of them. Unrecognised words before your callsign get `say again with the airfield name`, and a name that matches more than one field gets `… say again with the full name` (`airfield.py` 1482-1509). If you name an airfield that does not publish this frequency, there is no reply.
3. **Use a role the frequency publishes.** If you call `tower` on a ground-only frequency, the reply is `unable, [FIELD] tower is not on this frequency, contact [FIELD] tower [TOWER_FREQ]`, and you are not enrolled (1413-1422). Runway clearances (line-up, take-off, crossing, landing) need **tower service on the frequency your session is on** (1086-1088). Calling on a different frequency of the same airfield moves your session there. Your phase and any runway you hold are kept, and replies queued for the old frequency become invalid (1449-1453). `request information` / `radio check` never move the session.
4. **Identity.** Only human slots in the mission catalog can be served. AI slots, unknown callsigns and modexes, and ambiguous callsigns are refused without enrolling. If SRS sends your radio name, it must identify the same pilot (1399-1433, `identities.py` 7-53).
5. **Readbacks start with the word `readback`.** `roger`/`wilco` is never a readback. Say the cleared runway exactly (number and side) and the clearance term. Say no other runway, no negation (`no, not, negative, dont, do not, unable, never, cannot, cant`) and no `cancel/abort/belay` anywhere in the readback (`airfield.py` 465-518). See [Readback dictionary](#readback-dictionary).
6. **Unread clearances lapse.** A line-up, take-off, crossing or landing clearance not read back within 60 model seconds is cancelled (`no readback, …`). It is **not re-issued until you ask again** (1030-1037).
7. **Live link required.** With a stale or missing mission bridge, every call except `cancel`, `checking out` and `cancel emergency` gets `unable, no live airfield link` (1436-1437). Clearances not yet started are revoked without a transmission, and earlier replies become invalid (588-602).
8. **Emergency vs cancel.** `cancel emergency` only clears your priority, and your session continues. `cancel` / `checking out` end the session, or tell you to vacate first if you are on a runway (1531-1545).
9. **Keep common calls on your current session frequency.** For `[CURRENT_ROLE]`, say a role published on that same frequency (`ground`, `tower` or `approach`). Do not retune just to ask for status, cancel or declare an emergency: moving to a frequency without tower service revokes runway authority. With no session, use a published role on your selected frequency. A departure emergency while occupying the runway in `lineup`, `lined_up` or a granted takeoff phase **aborts the takeoff**, even without saying `aborting`.
10. **Optional services:** Ground and Approach may be absent. The role labels name procedure areas, not mandatory receivers. `[CURRENT_ROLE]` uses any role actually published on your selected frequency: normally Ground for taxi, Approach for arrival, or Tower when those services are absent. Follow an explicit handoff if given. Runway clearances still require Tower service on the session frequency.

## Placeholder dictionary

Sample values are taken from the test fixture `tests/test_airfield.py` (airfield `Ironwood`, runway 09/27 with crossing strip 18/36, altimeter 29.92, player `Viper 1-1`, modex `101`). This lets every `YOU` node be replayed against `Base` in that file.

| Placeholder | Pilot says | Sample | Source |
|---|---|---|---|
| `[FIELD]` | Airfield name or a published radio alias of a field on this frequency | `Ironwood` (real terrain example: `Kutaisi`) | `airfield_parser.py` 71-80, 163-187; `airfield.py` 656-660 |
| `[CALLSIGN]` | Your flight callsign, digits spoken separately (`one one`), or your modex digits | `Viper one one` / `one zero one` | `airfield_parser.py` 224-227; `identities.py` 7-28 |
| `[CURRENT_ROLE]` | A role published on your current frequency; keep the session frequency unchanged for common calls | `tower` on tower frequency; `ground` on ground frequency | `airfield.py` 1413-1422, 1449-1453, 1575-1578 |
| `[RUNWAY]` | Runway in use from ATC, spoken `runway <digits> [left/right/center]` | `zero nine` | `airfield.py` 450-452, 954-973 |
| `[CROSS_RUNWAY]` | The runway you are holding short of to cross | `one eight` | `airfield_parser.py` 90-111; `airfield.py` 1758-1770 |
| `[ALTIMETER]` | Altimeter from ATC, inHg as four digits (`niner` accepted) or `29.92` | `two niner niner two` | `airfield.py` 461-462, 512-517; `carrier.py` 328-352 |

Reply-only placeholders (you never say these):

| Placeholder | Controller text |
|---|---|
| `[WIND]` | `wind <magnetic three digits> at <knots>`, or `wind calm` (under 3 kt), or `wind not available` (1164-1171) |
| `[TAXI_ROUTE]` | Bundled taxiway names. Without a verified route ATC says `named taxi route not available, taxi at your discretion` (taxi out), or `named taxi route not available, taxi to parking at your discretion` (taxi in) (419-443, 1155-1157, 1627-1629) |
| `[PATTERN]` | `left` or `right` when the mission publishes a pattern (199, 202-203) |
| `[VISUAL_INSTRUCTION]` | `enter [PATTERN] downwind runway [RUNWAY], report downwind`, or without a pattern `report final runway [RUNWAY]` (1138-1139) |
| `[TOWER_FREQ]`, `[APPROACH_FREQ]` | Another published frequency of this airfield, spoken digit by digit with `decimal`, for example `one two two decimal zero` (455-458, 975-979) |
| `[N]` | Queue position `one`, `two`, … (emergency first, then arrivals, then departures, then call order) (945-952) |
| `[RUNWAY_REASON]` | `wind information not available, unable to determine the runway in use` / `configured active runway not usable` / `no usable runway` / `altimeter not available` (954-967, 1134-1137) |
| `[REVOKE_REASON]` | `clearance cancelled` (unread for 60 s; the event then starts `no readback, `), `runway occupied`, `tower service not available on this frequency`. A stale bridge revokes the same clearances **without any transmission** (588-602, 1030-1052, 1100-1127) |
| `[READBACK_PROBLEM]` | See [Readback dictionary](#readback-dictionary) |
| `[CURRENT_INSTRUCTION]` | The present-state instruction from the [say-again table](#say-again--current-instruction-table) |
| `[OTHER_FIELD]` | Another airfield's spoken name |

## Chart 1 — Departure: startup to leaving the zone

```mermaid
---
config:
  htmlLabels: false
  fontSize: 14
  themeVariables:
    fontSize: 14px
  flowchart:
    nodeSpacing: 12
    rankSpacing: 24
    padding: 8
    wrappingWidth: 180
    htmlLabels: false
---
flowchart TD
  D_START(("START: human aircraft parked at [FIELD]"))
  D_END(("END: airfield session closed"))
  D_TO_ARR(("Continue in ARRIVAL chart at the briefing readback"))

  %% Guards on EVERY call below: no enrolment, fix and RETRY the same call
    D_G_HUB{{"Any YOU call in this chart can first meet one of these"}}
    D_G_ID(["ATC: last transmitter, i could not recognize your callsign or modex — or — more than one aircraft matches, say modex — or — unable, no player aircraft with that identity — or — unable, your radio name does not identify you"])
    D_G_WHICH(["ATC: Ground, say which airfield you are calling, [FIELD] or [OTHER_FIELD] — or — Ground, say again with the airfield name — or — Tower, more than one airfield matches that name, say again with the full name"])
    D_G_ROLE(["ATC: unable, [FIELD] tower is not on this frequency, contact [FIELD] tower [TOWER_FREQ]"])
    D_G_COAL(["ATC: unable, [OTHER_FIELD] is not under your coalition's control, no service — or — unable, [OTHER_FIELD] is neutral, no service"])
    D_G_STALE(["ATC: unable, no live airfield link — only cancel, checking out and cancel emergency still accepted"])
    D_G_BUSY(["ATC: unable, you are working [OTHER_FIELD] — or — unable, you hold a runway clearance at [OTHER_FIELD], cancel it first — or — unable, airfield traffic limit reached"])
    D_G_NONE(["ATC: say again — no command recognised, or a procedure call voided by a negation"])
    D_G_SILENT{{"NO REPLY: call not opened with an airfield receiver, named airfield not on this frequency, or frequency not an airfield radio"}}

  %% Facts: any time, before or during a session — never enrols, never moves the session
    D_INFO["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request information**`"]
    D_RCHECK["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], radio check**`"]
    D_INFO_R(["ATC: runway [RUNWAY] in use, [WIND], altimeter [ALTIMETER], [PATTERN] traffic"])

  %% 1 · Ground: startup
    D_STARTUP["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request startup**`"]
    D_STARTUP_OK(["ATC: startup approved, altimeter [ALTIMETER], report ready to taxi"])
    D_STARTUP_SB(["ATC: standby, not yet observed on the ground at [FIELD]"])
    D_W_GROUND{{"WAIT — NO CALL: next bridge sample must show you on the ground at [FIELD]"}}
    D_STARTUP_EV(["ATC event: startup approved, altimeter [ALTIMETER], report ready to taxi"])
    D_STARTUP_CLOSED(["ATC event: unable, not observed on the ground at [FIELD], departure closed"])
    D_STARTUP_ARR(["ATC: unable, departure requires the aircraft on the ground clear of the runway"])

  %% 2 · Ground: taxi clearance — the startup call is optional
    D_TAXI["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request taxi**`"]
    D_TAXI_CLR(["ATC: taxi to holding point runway [RUNWAY] via [TAXI_ROUTE], hold short of all runways, altimeter [ALTIMETER], read back"])
    D_TAXI_NORWY(["ATC: [RUNWAY_REASON], hold position"])
    D_TAXI_NOTGND(["ATC: unable, not observed on the ground here — or — standby, not yet observed on the ground at [FIELD]"])
    D_TAXI_RB["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], readback taxi holding point runway [RUNWAY], hold short of all runways**`"]
    D_TAXI_OK(["ATC: readback correct, taxi to holding point runway [RUNWAY], hold short of all runways, report holding short"])

  %% Readback outcomes shared by every readback in this chart
    D_RB_BAD(["ATC: negative readback, [READBACK_PROBLEM], read back again"])
    D_RB_NONE(["ATC: unable, no clearance to read back"])

  %% 3 · Runway crossing: taxi out or taxi back to parking
    D_W_CROSSPT{{"TAXI — NO CALL: stop beside runway [CROSS_RUNWAY], off every runway, within 400 m of its edge"}}
    D_CROSS["`YOU: **[FIELD] tower, [CALLSIGN], request crossing runway [CROSS_RUNWAY]**`"]
    D_CROSS_NEG(["ATC: negative, not observed holding short runway [CROSS_RUNWAY] — or — unable, say the runway to cross"])
    D_CROSS_Q(["ATC: hold short runway [CROSS_RUNWAY], number [N] for crossing"])
    D_CROSS_TWR(["ATC: hold short runway [CROSS_RUNWAY], contact [FIELD] tower [TOWER_FREQ] — or — hold short runway [CROSS_RUNWAY], unable, no tower service at [FIELD], runway clearances not available"])
    D_CROSS_CLR(["ATC: cross runway [CROSS_RUNWAY], report vacated, read back"])
    D_CROSS_RB["`YOU: **[FIELD] tower, [CALLSIGN], readback cross runway [CROSS_RUNWAY]**`"]
    D_CROSS_OK(["ATC: readback correct, cross runway [CROSS_RUNWAY], report vacated"])
    D_CROSS_REV(["ATC event: no readback, hold short runway [CROSS_RUNWAY], crossing clearance cancelled, clearance cancelled — or — hold short runway [CROSS_RUNWAY], crossing clearance cancelled, [REVOKE_REASON]"])
    D_W_CROSSING{{"TAXI — NO CALL: cross — telemetry must see you enter runway [CROSS_RUNWAY] and then clear of every runway"}}
    D_CROSS_EV(["ATC event: runway vacated, continue taxi to holding point runway [RUNWAY], hold short of all runways — returning to parking: runway vacated, continue taxi to parking, hold short of all runways"])
    D_CROSS_VAC["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], vacated runway [CROSS_RUNWAY]**`"]
    D_CROSS_DONE(["ATC: roger, runway vacated, continue taxi to holding point runway [RUNWAY], hold short of all runways — returning to parking: roger, runway vacated, continue taxi to parking, hold short of all runways"])

  %% 4 · Tower: holding point, line up, takeoff
    D_W_HOLD{{"TAXI — NO CALL: stop at holding point runway [RUNWAY], up to 400 m before the threshold or beside any part of it, off every runway"}}
    D_HOLD["`YOU: **[FIELD] tower, [CALLSIGN], holding short runway [RUNWAY]**`"]
    D_HOLD_NEG(["ATC: negative, not observed at the holding point runway [RUNWAY], report holding short"])
    D_HOLD_RBFIRST(["ATC: unable, read back taxi clearance first"])
    D_HOLD_Q(["ATC: hold short runway [RUNWAY], number [N] for departure"])
    D_HOLD_TWR(["ATC: hold short runway [RUNWAY], contact [FIELD] tower [TOWER_FREQ] — or — hold short runway [RUNWAY], unable, no tower service at [FIELD], runway clearances not available"])
    D_READY_DEP["`YOU: **[FIELD] tower, [CALLSIGN], ready for departure runway [RUNWAY]**`"]
    D_LU_CLR(["ATC: runway [RUNWAY], line up and wait, read back"])
    D_LU_RB["`YOU: **[FIELD] tower, [CALLSIGN], readback runway [RUNWAY], line up and wait**`"]
    D_LU_OK(["ATC: readback correct, line up and wait runway [RUNWAY], report lined up"])
    D_W_LINEUP{{"TAXI — NO CALL: line up anywhere on runway [RUNWAY] from the threshold to the far end, heading within 30 degrees"}}
    D_LINEDUP["`YOU: **[FIELD] tower, [CALLSIGN], lined up runway [RUNWAY]**`"]
    D_LINEDUP_NEG(["ATC: negative, not observed lined up runway [RUNWAY]"])
    D_LINEDUP_HOLD(["ATC: hold position runway [RUNWAY], stand by for takeoff clearance"])
    D_READY_TO["`YOU: **[FIELD] tower, [CALLSIGN], ready for takeoff runway [RUNWAY]**`"]
    D_TO_CLR(["ATC: runway [RUNWAY], [WIND], cleared for takeoff, read back"])
    D_TO_RB["`YOU: **[FIELD] tower, [CALLSIGN], readback runway [RUNWAY], cleared for takeoff**`"]
    D_TO_OK(["ATC: readback correct"])
    D_W_ROLL{{"FLY — NO CALL: take off — the mission takeoff event or an in-air sample ends your runway lane"}}

  %% Revocations: controller-initiated, RETURN edges
    D_X_LU(["ATC event: no readback, hold short runway [RUNWAY], line up clearance cancelled, clearance cancelled — or — hold short runway [RUNWAY], line up clearance cancelled, [REVOKE_REASON]"])
    D_X_TO(["ATC event: no readback, hold position, takeoff clearance cancelled, clearance cancelled — or — hold position, takeoff clearance cancelled, [REVOKE_REASON]"])
    D_X_RWY_TAXI(["ATC event: runway changed, taxi to holding point runway [RUNWAY] via [TAXI_ROUTE], hold short of all runways, altimeter [ALTIMETER], read back"])
    D_X_RWY_OCC(["ATC event: runway changed, clearance cancelled, vacate the runway, then request taxi"])
    D_X_STALE{{"NO TRANSMISSION: bridge stale — clearances not yet started are revoked, an unread taxi clearance falls back to startup, every earlier reply becomes invalid"}}

  %% 5 · Airborne and leaving the zone
    D_AIR_EV(["ATC event: airborne, contact [FIELD] approach [APPROACH_FREQ], report leaving the zone — or, approach on this frequency or none published — airborne, report leaving the zone"])
    D_AIR_UNCLR(["ATC event: takeoff not cleared, airborne observed, report leaving the zone"])
    D_AIRBORNE["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], airborne**`"]
    D_AIRBORNE_R(["ATC: airborne, report leaving the zone — confirms the observed takeoff, authorises nothing"])
    D_AIRBORNE_NEG(["ATC: negative, airborne not observed"])
    D_HANDOFF["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], switching**`"]
    D_HANDOFF_R(["ATC: frequency change approved, good day"])
    D_HANDOFF_EARLY(["ATC: unable, [CURRENT_INSTRUCTION]"])
    D_W_10NM{{"FLY — NO CALL: beyond 10 NM from [FIELD]"}}
    D_10NM_R(["ATC event: leaving the zone, frequency change approved"])
    D_RETURN["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], inbound landing**`"]

  %% Return to parking, re-departure, check-out
    D_TAXIIN["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request taxi to parking**`"]
    D_TAXIIN_NEG(["ATC: vacate the runway first, report vacated — or — unable, not observed on the ground here — or — unable, [CURRENT_INSTRUCTION]"])
    D_TAXIIN_CLR(["ATC: taxi to parking via [TAXI_ROUTE], hold short of all runways, read back"])
    D_TAXIIN_RB["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], readback taxi to parking, hold short all runways**`"]
    D_TAXIIN_OK(["ATC: readback correct, taxi to parking, hold short of all runways, report parked"])
    D_W_PARK{{"TAXI — NO CALL: to parking — cross any runway only after crossing clearance and readback"}}
    D_CHECKOUT["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], checking out**`"]
    D_CHECKOUT_R(["ATC: copy, checked out"])

  %% Any enrolled point: status, cancel, emergency
    D_ANY{{"At any enrolled point of this chart"}}
    D_SAYAGAIN["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], say again**`"]
    D_SAYAGAIN_R(["ATC: [CURRENT_INSTRUCTION] — rebuilt from present state, never a lapsed or revoked clearance"])
    D_NOSESSION(["ATC: unable, no active session, state your request"])
    D_CANCEL["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], cancel**`"]
    D_CANCEL_R(["ATC: copy, departure cancelled, checked out"])
    D_CANCEL_NONE(["ATC: copy, no active airfield session"])
    D_CANCEL_OCC(["ATC: clearance cancelled, vacate the runway, report vacated"])
    D_EMER["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], mayday mayday mayday, low fuel**`"]
    D_EMER_R(["ATC: copy emergency, you have priority, [CURRENT_INSTRUCTION]"])
    D_EMER_NEW(["ATC: copy emergency, you have priority, runway [RUNWAY] in use, … read back runway and altimeter — opens an ARRIVAL session"])
    D_ABORT["`YOU: **[FIELD] tower, [CALLSIGN], mayday, aborting takeoff**`"]
    D_ABORT_R(["ATC: copy emergency, takeoff aborted, vacate the runway when able, report vacated"])
    D_CANCEL_EMER["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], cancel emergency**`"]
    D_CANCEL_EMER_R(["ATC: roger, emergency cancelled, [CURRENT_INSTRUCTION] — or — roger, no emergency on record"])
    D_W_VAC{{"TAXI — NO CALL: leave every runway footprint"}}
    D_VAC["`YOU: **[FIELD] tower, [CALLSIGN], vacated**`"]
    D_VAC_NEG(["ATC: negative, not observed clear of the runway"])
    D_VAC_CXL(["ATC: roger, runway vacated, checked out"])
    D_VAC_CXL_EV(["ATC event: runway vacated, checked out"])
    D_VAC_START(["ATC: roger, startup approved, altimeter [ALTIMETER], report ready to taxi"])

  %% Lifecycle closures: RETURN to a fresh call
    D_L_SILENT{{"NO TRANSMISSION: dead, eject, disconnect or birth event — new incarnation or coalition — event at another airfield — land event after takeoff — airfield or frequency no longer served — new mission epoch — session moved to a carrier"}}
    D_L_LOST(["ATC event: radar contact lost, airfield session closed, call again"])
    D_L_BAD(["ATC event: tracking data invalid, airfield session closed, call again"])

  %% Stable edge IDs keep path colors attached to their transitions.
  D_START edge_D_START__D_G_HUB@--> D_G_HUB
  D_G_HUB edge_D_G_HUB__D_G_ID@--> D_G_ID
  D_G_HUB edge_D_G_HUB__D_G_WHICH@--> D_G_WHICH
  D_G_HUB edge_D_G_HUB__D_G_ROLE@--> D_G_ROLE
  D_G_HUB edge_D_G_HUB__D_G_COAL@--> D_G_COAL
  D_G_HUB edge_D_G_HUB__D_G_STALE@--> D_G_STALE
  D_G_HUB edge_D_G_HUB__D_G_BUSY@--> D_G_BUSY
  D_G_HUB edge_D_G_HUB__D_G_NONE@--> D_G_NONE
  D_G_HUB edge_D_G_HUB__D_G_SILENT@--> D_G_SILENT
  D_G_ID edge_D_G_ID__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_WHICH edge_D_G_WHICH__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_ROLE edge_D_G_ROLE__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_COAL edge_D_G_COAL__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_STALE edge_D_G_STALE__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_BUSY edge_D_G_BUSY__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_NONE edge_D_G_NONE__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_G_SILENT edge_D_G_SILENT__D_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| D_START
  D_START edge_D_START__D_INFO@--> D_INFO
  D_START edge_D_START__D_RCHECK@--> D_RCHECK
  D_INFO edge_D_INFO__D_INFO_R@--> D_INFO_R
  D_RCHECK edge_D_RCHECK__D_INFO_R@--> D_INFO_R
  D_INFO_R edge_D_INFO_R__D_START@-.->|"RETURN to your current step"| D_START
  D_START edge_D_START__D_STARTUP@--> D_STARTUP
  D_STARTUP edge_D_STARTUP__D_STARTUP_OK@-->|"sampled on the ground here"| D_STARTUP_OK
  D_STARTUP edge_D_STARTUP__D_STARTUP_SB@-->|"no ground sample here yet:<br/>enrolled"| D_STARTUP_SB
  D_STARTUP_SB edge_D_STARTUP_SB__D_W_GROUND@--> D_W_GROUND
  D_W_GROUND edge_D_W_GROUND__D_STARTUP_EV@-->|"ground sample here"| D_STARTUP_EV
  D_W_GROUND edge_D_W_GROUND__D_STARTUP_CLOSED@-->|"on the ground at another<br/>airfield, or a takeoff event"| D_STARTUP_CLOSED
  D_STARTUP_CLOSED edge_D_STARTUP_CLOSED__D_START@-.->|"RETURN"| D_START
  D_STARTUP edge_D_STARTUP__D_STARTUP_ARR@-->|"your arrival session is not yet<br/>vacated and on the ground"| D_STARTUP_ARR
  D_STARTUP_ARR edge_D_STARTUP_ARR__D_START@-.->|"RETRY after vacating"| D_START
  D_START edge_D_START__D_TAXI@-->|"startup call optional"| D_TAXI
  D_STARTUP_OK edge_D_STARTUP_OK__D_TAXI@--> D_TAXI
  D_STARTUP_EV edge_D_STARTUP_EV__D_TAXI@--> D_TAXI
  D_TAXI edge_D_TAXI__D_TAXI_CLR@-->|"runway known, ground sample here"| D_TAXI_CLR
  D_TAXI_CLR edge_D_TAXI_CLR__D_TAXI_RB@--> D_TAXI_RB
  D_TAXI_RB edge_D_TAXI_RB__D_TAXI_OK@-->|"exact"| D_TAXI_OK
  D_TAXI edge_D_TAXI__D_TAXI_NORWY@-->|"no runway in use"| D_TAXI_NORWY
  D_TAXI_NORWY edge_D_TAXI_NORWY__D_TAXI@-.->|"RETRY when wind or active runway<br/>available"| D_TAXI
  D_TAXI edge_D_TAXI__D_TAXI_NOTGND@-->|"no ground sample here"| D_TAXI_NOTGND
  D_TAXI_NOTGND edge_D_TAXI_NOTGND__D_W_GROUND@-.->|"WAIT then RETRY"| D_W_GROUND
  D_TAXI_RB edge_D_TAXI_RB__D_RB_BAD@-->|"wrong or missing runway or term,<br/>negation, cancel word"| D_RB_BAD
  D_CROSS_RB edge_D_CROSS_RB__D_RB_BAD@-->|"wrong or missing runway or term,<br/>negation, cancel word"| D_RB_BAD
  D_LU_RB edge_D_LU_RB__D_RB_BAD@-->|"wrong or missing runway or term,<br/>negation, cancel word"| D_RB_BAD
  D_TO_RB edge_D_TO_RB__D_RB_BAD@-->|"wrong or missing runway or term,<br/>negation, cancel word"| D_RB_BAD
  D_TAXIIN_RB edge_D_TAXIIN_RB__D_RB_BAD@-->|"wrong or missing runway or term,<br/>negation, cancel word"| D_RB_BAD
  D_RB_BAD edge_D_RB_BAD__D_TAXI_RB@-.->|"RETRY taxi readback"| D_TAXI_RB
  D_RB_BAD edge_D_RB_BAD__D_CROSS_RB@-.->|"RETRY crossing readback"| D_CROSS_RB
  D_RB_BAD edge_D_RB_BAD__D_LU_RB@-.->|"RETRY line-up readback"| D_LU_RB
  D_RB_BAD edge_D_RB_BAD__D_TO_RB@-.->|"RETRY takeoff readback"| D_TO_RB
  D_RB_BAD edge_D_RB_BAD__D_TAXIIN_RB@-.->|"RETRY parking readback"| D_TAXIIN_RB
  D_CROSS_RB edge_D_CROSS_RB__D_RB_NONE@-->|"clearance already lapsed or<br/>revoked"| D_RB_NONE
  D_LU_RB edge_D_LU_RB__D_RB_NONE@-->|"clearance already lapsed or<br/>revoked"| D_RB_NONE
  D_TO_RB edge_D_TO_RB__D_RB_NONE@-->|"clearance already lapsed or<br/>revoked"| D_RB_NONE
  D_TAXI_OK edge_D_TAXI_OK__D_W_CROSSPT@-->|"route crosses a runway"| D_W_CROSSPT
  D_W_CROSSPT edge_D_W_CROSSPT__D_CROSS@--> D_CROSS
  D_CROSS edge_D_CROSS__D_CROSS_NEG@-->|"not measured at that crossing,<br/>or no valid runway named"| D_CROSS_NEG
  D_CROSS_NEG edge_D_CROSS_NEG__D_CROSS@-.->|"RETRY"| D_CROSS
  D_CROSS edge_D_CROSS__D_CROSS_Q@-->|"lane busy or not first in order"| D_CROSS_Q
  D_CROSS edge_D_CROSS__D_CROSS_TWR@-->|"no tower on this frequency"| D_CROSS_TWR
  D_CROSS_TWR edge_D_CROSS_TWR__D_CROSS@-.->|"retune to tower, RETRY"| D_CROSS
  D_CROSS edge_D_CROSS__D_CROSS_CLR@-->|"first, lane free, runways clear,<br/>tower here"| D_CROSS_CLR
  D_CROSS_Q edge_D_CROSS_Q__D_CROSS_CLR@-->|"WAIT — granted as an event when<br/>eligible"| D_CROSS_CLR
  D_CROSS_CLR edge_D_CROSS_CLR__D_CROSS_RB@--> D_CROSS_RB
  D_CROSS_RB edge_D_CROSS_RB__D_CROSS_OK@-->|"exact"| D_CROSS_OK
  D_CROSS_OK edge_D_CROSS_OK__D_W_CROSSING@--> D_W_CROSSING
  D_CROSS_CLR edge_D_CROSS_CLR__D_CROSS_REV@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| D_CROSS_REV
  D_CROSS_OK edge_D_CROSS_OK__D_CROSS_REV@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| D_CROSS_REV
  D_CROSS_REV edge_D_CROSS_REV__D_CROSS@-.->|"after no readback: RETRY the<br/>request — otherwise WAIT for a<br/>new grant"| D_CROSS
  D_W_CROSSING edge_D_W_CROSSING__D_CROSS_EV@-->|"measured clear: no call needed"| D_CROSS_EV
  D_W_CROSSING edge_D_W_CROSSING__D_CROSS_VAC@--> D_CROSS_VAC
  D_CROSS_VAC edge_D_CROSS_VAC__D_CROSS_DONE@-->|"entered and now measured clear"| D_CROSS_DONE
  D_CROSS_VAC edge_D_CROSS_VAC__D_CROSS_OK@-->|"not yet entered: reply is cross<br/>runway [CROSS_RUNWAY], report<br/>vacated"| D_CROSS_OK
  D_CROSS_VAC edge_D_CROSS_VAC__D_VAC_NEG@-->|"still on a runway"| D_VAC_NEG
  D_TAXI_OK edge_D_TAXI_OK__D_W_HOLD@--> D_W_HOLD
  D_CROSS_EV edge_D_CROSS_EV__D_W_HOLD@-->|"taxiing out"| D_W_HOLD
  D_CROSS_DONE edge_D_CROSS_DONE__D_W_HOLD@-->|"taxiing out"| D_W_HOLD
  D_W_HOLD edge_D_W_HOLD__D_HOLD@--> D_HOLD
  D_HOLD edge_D_HOLD__D_HOLD_NEG@-->|"not measured at the holding<br/>point"| D_HOLD_NEG
  D_HOLD_NEG edge_D_HOLD_NEG__D_HOLD@-.->|"RETRY"| D_HOLD
  D_HOLD edge_D_HOLD__D_HOLD_RBFIRST@-->|"taxi readback still owed"| D_HOLD_RBFIRST
  D_HOLD_RBFIRST edge_D_HOLD_RBFIRST__D_TAXI_RB@-.->|"RETRY"| D_TAXI_RB
  D_HOLD edge_D_HOLD__D_HOLD_Q@-->|"lane busy or not first"| D_HOLD_Q
  D_HOLD edge_D_HOLD__D_HOLD_TWR@-->|"no tower on this frequency"| D_HOLD_TWR
  D_HOLD_TWR edge_D_HOLD_TWR__D_READY_DEP@-.->|"retune to the tower frequency"| D_READY_DEP
  D_READY_DEP edge_D_READY_DEP__D_HOLD_Q@-->|"queued"| D_HOLD_Q
  D_HOLD edge_D_HOLD__D_LU_CLR@-->|"first, lane free, runways clear,<br/>tower here: appended to the<br/>reply"| D_LU_CLR
  D_READY_DEP edge_D_READY_DEP__D_LU_CLR@-->|"first, lane free, runways clear,<br/>tower here: appended to the<br/>reply"| D_LU_CLR
  D_HOLD_Q edge_D_HOLD_Q__D_LU_CLR@-->|"WAIT — granted as an event when<br/>eligible"| D_LU_CLR
  D_LU_CLR edge_D_LU_CLR__D_LU_RB@--> D_LU_RB
  D_LU_RB edge_D_LU_RB__D_LU_OK@-->|"exact, not yet lined up"| D_LU_OK
  D_LU_OK edge_D_LU_OK__D_W_LINEUP@--> D_W_LINEUP
  D_LU_RB edge_D_LU_RB__D_LINEDUP_HOLD@-->|"exact, already measured lined up"| D_LINEDUP_HOLD
  D_W_LINEUP edge_D_W_LINEUP__D_TO_CLR@-->|"measured lined up: takeoff<br/>clearance arrives as an event,<br/>no call needed"| D_TO_CLR
  D_W_LINEUP edge_D_W_LINEUP__D_LINEDUP@--> D_LINEDUP
  D_LINEDUP edge_D_LINEDUP__D_LINEDUP_NEG@-->|"not measured lined up"| D_LINEDUP_NEG
  D_LINEDUP_NEG edge_D_LINEDUP_NEG__D_W_LINEUP@-.->|"RETRY after lining up"| D_W_LINEUP
  D_LINEDUP edge_D_LINEDUP__D_LINEDUP_HOLD@-->|"measured lined up"| D_LINEDUP_HOLD
  D_READY_TO edge_D_READY_TO__D_LINEDUP_HOLD@-->|"lined up, earlier clearance<br/>lapsed"| D_LINEDUP_HOLD
  D_LINEDUP_HOLD edge_D_LINEDUP_HOLD__D_TO_CLR@-->|"runway yours, tower here:<br/>appended"| D_TO_CLR
  D_TO_CLR edge_D_TO_CLR__D_TO_RB@--> D_TO_RB
  D_TO_RB edge_D_TO_RB__D_TO_OK@-->|"exact"| D_TO_OK
  D_TO_OK edge_D_TO_OK__D_W_ROLL@--> D_W_ROLL
  D_LU_CLR edge_D_LU_CLR__D_X_LU@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| D_X_LU
  D_LU_OK edge_D_LU_OK__D_X_LU@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| D_X_LU
  D_X_LU edge_D_X_LU__D_READY_DEP@-.->|"after no readback: ask again"| D_READY_DEP
  D_X_LU edge_D_X_LU__D_HOLD_Q@-.->|"runway occupied: WAIT, regranted<br/>automatically"| D_HOLD_Q
  D_TO_CLR edge_D_TO_CLR__D_X_TO@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — on the runway, not<br/>rolling"| D_X_TO
  D_TO_OK edge_D_TO_OK__D_X_TO@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — on the runway, not<br/>rolling"| D_X_TO
  D_X_TO edge_D_X_TO__D_READY_TO@-.->|"ask again"| D_READY_TO
  D_TAXI_OK edge_D_TAXI_OK__D_X_RWY_TAXI@-->|"wind selects another runway —<br/>not on a runway"| D_X_RWY_TAXI
  D_HOLD_Q edge_D_HOLD_Q__D_X_RWY_TAXI@-->|"wind selects another runway —<br/>not on a runway"| D_X_RWY_TAXI
  D_LU_CLR edge_D_LU_CLR__D_X_RWY_TAXI@-->|"wind selects another runway —<br/>not on a runway"| D_X_RWY_TAXI
  D_CROSS_Q edge_D_CROSS_Q__D_X_RWY_TAXI@-->|"wind selects another runway —<br/>not on a runway"| D_X_RWY_TAXI
  D_X_RWY_TAXI edge_D_X_RWY_TAXI__D_TAXI_RB@-->|"read back the new runway"| D_TAXI_RB
  D_LU_OK edge_D_LU_OK__D_X_RWY_OCC@-->|"wind selects another runway<br/>while on the runway"| D_X_RWY_OCC
  D_TO_CLR edge_D_TO_CLR__D_X_RWY_OCC@-->|"wind selects another runway<br/>while on the runway"| D_X_RWY_OCC
  D_X_RWY_OCC edge_D_X_RWY_OCC__D_W_VAC@--> D_W_VAC
  D_TAXI_CLR edge_D_TAXI_CLR__D_X_STALE@-->|"bridge goes stale"| D_X_STALE
  D_LU_CLR edge_D_LU_CLR__D_X_STALE@-->|"bridge goes stale"| D_X_STALE
  D_TO_CLR edge_D_TO_CLR__D_X_STALE@-->|"bridge goes stale"| D_X_STALE
  D_CROSS_CLR edge_D_CROSS_CLR__D_X_STALE@-->|"bridge goes stale"| D_X_STALE
  D_X_STALE edge_D_X_STALE__D_SAYAGAIN@-.->|"link returns: ask for current<br/>instruction on same frequency"| D_SAYAGAIN
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_TAXI@-.->|"revoked unread taxi clearance<br/>only: startup, request taxi"| D_TAXI
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_CROSS_Q@-.->|"crossing requested, still queued"| D_CROSS_Q
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_CROSS_RB@-.->|"crossing already granted: read<br/>back current clearance"| D_CROSS_RB
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_READY_DEP@-.->|"holding short: await regrant or<br/>ready departure"| D_READY_DEP
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_READY_TO@-.->|"lined up: await regrant or ready<br/>takeoff"| D_READY_TO
  D_W_ROLL edge_D_W_ROLL__D_AIR_EV@--> D_AIR_EV
  D_TAXI_OK edge_D_TAXI_OK__D_AIR_UNCLR@-->|"takes off without a takeoff<br/>clearance"| D_AIR_UNCLR
  D_HOLD_Q edge_D_HOLD_Q__D_AIR_UNCLR@-->|"takes off without a takeoff<br/>clearance"| D_AIR_UNCLR
  D_LU_OK edge_D_LU_OK__D_AIR_UNCLR@-->|"takes off without a takeoff<br/>clearance"| D_AIR_UNCLR
  D_AIR_EV edge_D_AIR_EV__D_AIRBORNE@--> D_AIRBORNE
  D_AIR_EV edge_D_AIR_EV__D_HANDOFF@--> D_HANDOFF
  D_AIR_EV edge_D_AIR_EV__D_W_10NM@--> D_W_10NM
  D_AIR_EV edge_D_AIR_EV__D_RETURN@--> D_RETURN
  D_AIR_UNCLR edge_D_AIR_UNCLR__D_AIRBORNE@--> D_AIRBORNE
  D_AIR_UNCLR edge_D_AIR_UNCLR__D_HANDOFF@--> D_HANDOFF
  D_AIR_UNCLR edge_D_AIR_UNCLR__D_W_10NM@--> D_W_10NM
  D_AIR_UNCLR edge_D_AIR_UNCLR__D_RETURN@--> D_RETURN
  D_AIRBORNE edge_D_AIRBORNE__D_AIRBORNE_R@-->|"airborne phase"| D_AIRBORNE_R
  D_AIRBORNE edge_D_AIRBORNE__D_AIRBORNE_NEG@-->|"before observed takeoff"| D_AIRBORNE_NEG
  D_HANDOFF edge_D_HANDOFF__D_HANDOFF_R@-->|"airborne phase"| D_HANDOFF_R
  D_HANDOFF_R edge_D_HANDOFF_R__D_END@--> D_END
  D_HANDOFF edge_D_HANDOFF__D_HANDOFF_EARLY@-->|"before observed takeoff"| D_HANDOFF_EARLY
  D_W_10NM edge_D_W_10NM__D_10NM_R@--> D_10NM_R
  D_10NM_R edge_D_10NM_R__D_END@--> D_END
  D_RETURN edge_D_RETURN__D_TO_ARR@-->|"airborne: new arrival session<br/>with a briefing"| D_TO_ARR
  D_STARTUP_OK edge_D_STARTUP_OK__D_TAXIIN@-->|"abandon before the runway"| D_TAXIIN
  D_TAXI_OK edge_D_TAXI_OK__D_TAXIIN@-->|"abandon before the runway"| D_TAXIIN
  D_HOLD_Q edge_D_HOLD_Q__D_TAXIIN@-->|"abandon before the runway"| D_TAXIIN
  D_CROSS_Q edge_D_CROSS_Q__D_TAXIIN@-->|"abandon before the runway"| D_TAXIIN
  D_TAXIIN edge_D_TAXIIN__D_TAXIIN_CLR@-->|"on the ground here, no runway<br/>held"| D_TAXIIN_CLR
  D_TAXIIN_CLR edge_D_TAXIIN_CLR__D_TAXIIN_RB@--> D_TAXIIN_RB
  D_TAXIIN edge_D_TAXIIN__D_TAXIIN_NEG@-->|"lane holder, on a runway, not on<br/>ground here, or line-up/takeoff<br/>phase"| D_TAXIIN_NEG
  D_TAXIIN_RB edge_D_TAXIIN_RB__D_TAXIIN_OK@-->|"exact"| D_TAXIIN_OK
  D_TAXIIN_OK edge_D_TAXIIN_OK__D_W_PARK@--> D_W_PARK
  D_W_PARK edge_D_W_PARK__D_W_CROSSPT@--> D_W_CROSSPT
  D_CROSS_EV edge_D_CROSS_EV__D_W_PARK@-->|"returning to parking"| D_W_PARK
  D_CROSS_DONE edge_D_CROSS_DONE__D_W_PARK@-->|"returning to parking"| D_W_PARK
  D_W_PARK edge_D_W_PARK__D_CHECKOUT@--> D_CHECKOUT
  D_CHECKOUT edge_D_CHECKOUT__D_CHECKOUT_R@--> D_CHECKOUT_R
  D_CHECKOUT_R edge_D_CHECKOUT_R__D_END@--> D_END
  D_W_PARK edge_D_W_PARK__D_TAXI@-->|"depart again from here"| D_TAXI
  D_W_PARK edge_D_W_PARK__D_STARTUP@-->|"or restart the departure"| D_STARTUP
  D_STARTUP_SB edge_D_STARTUP_SB__D_ANY@--> D_ANY
  D_STARTUP_OK edge_D_STARTUP_OK__D_ANY@--> D_ANY
  D_TAXI_OK edge_D_TAXI_OK__D_ANY@--> D_ANY
  D_HOLD_Q edge_D_HOLD_Q__D_ANY@--> D_ANY
  D_LU_OK edge_D_LU_OK__D_ANY@--> D_ANY
  D_TO_OK edge_D_TO_OK__D_ANY@--> D_ANY
  D_TAXIIN_OK edge_D_TAXIIN_OK__D_ANY@--> D_ANY
  D_ANY edge_D_ANY__D_SAYAGAIN@--> D_SAYAGAIN
  D_ANY edge_D_ANY__D_CANCEL@--> D_CANCEL
  D_ANY edge_D_ANY__D_EMER@--> D_EMER
  D_ANY edge_D_ANY__D_CANCEL_EMER@--> D_CANCEL_EMER
  D_START edge_D_START__D_SAYAGAIN@--> D_SAYAGAIN
  D_START edge_D_START__D_CANCEL@--> D_CANCEL
  D_START edge_D_START__D_EMER@--> D_EMER
  D_START edge_D_START__D_CANCEL_EMER@--> D_CANCEL_EMER
  D_SAYAGAIN edge_D_SAYAGAIN__D_SAYAGAIN_R@-->|"enrolled"| D_SAYAGAIN_R
  D_SAYAGAIN_R edge_D_SAYAGAIN_R__D_ANY@-.->|"RETURN to the current step"| D_ANY
  D_SAYAGAIN edge_D_SAYAGAIN__D_NOSESSION@-->|"no session"| D_NOSESSION
  D_NOSESSION edge_D_NOSESSION__D_START@-.->|"RETURN"| D_START
  D_RB_NONE edge_D_RB_NONE__D_SAYAGAIN@-.->|"check what is current"| D_SAYAGAIN
  D_CANCEL edge_D_CANCEL__D_CANCEL_R@-->|"not on a runway"| D_CANCEL_R
  D_CANCEL_R edge_D_CANCEL_R__D_END@--> D_END
  D_CANCEL edge_D_CANCEL__D_CANCEL_NONE@-->|"no session"| D_CANCEL_NONE
  D_CANCEL edge_D_CANCEL__D_CANCEL_OCC@-->|"on a runway"| D_CANCEL_OCC
  D_CANCEL_OCC edge_D_CANCEL_OCC__D_W_VAC@--> D_W_VAC
  D_EMER edge_D_EMER__D_EMER_R@-->|"other enrolled states: first in<br/>runway order"| D_EMER_R
  D_EMER edge_D_EMER__D_ABORT_R@-->|"occupying runway in lineup,<br/>lined-up or granted takeoff<br/>phase: automatically aborts"| D_ABORT_R
  D_EMER_R edge_D_EMER_R__D_ANY@-.->|"RETURN to the current step"| D_ANY
  D_EMER edge_D_EMER__D_EMER_NEW@-->|"no session"| D_EMER_NEW
  D_EMER_NEW edge_D_EMER_NEW__D_TO_ARR@--> D_TO_ARR
  D_LU_OK edge_D_LU_OK__D_ABORT@--> D_ABORT
  D_LINEDUP_HOLD edge_D_LINEDUP_HOLD__D_ABORT@--> D_ABORT
  D_TO_CLR edge_D_TO_CLR__D_ABORT@--> D_ABORT
  D_TO_OK edge_D_TO_OK__D_ABORT@--> D_ABORT
  D_ABORT edge_D_ABORT__D_ABORT_R@-->|"line-up or takeoff phase, on the<br/>runway"| D_ABORT_R
  D_ABORT_R edge_D_ABORT_R__D_W_VAC@--> D_W_VAC
  D_ABORT edge_D_ABORT__D_EMER_R@-->|"not on the runway"| D_EMER_R
  D_CANCEL_EMER edge_D_CANCEL_EMER__D_CANCEL_EMER_R@--> D_CANCEL_EMER_R
  D_CANCEL_EMER_R edge_D_CANCEL_EMER_R__D_ANY@-.->|"RETURN, priority removed"| D_ANY
  D_W_VAC edge_D_W_VAC__D_VAC_CXL_EV@-->|"after cancel: measured clear"| D_VAC_CXL_EV
  D_VAC_CXL_EV edge_D_VAC_CXL_EV__D_END@--> D_END
  D_W_VAC edge_D_W_VAC__D_TAXI@-->|"after abort or runway change:<br/>measured clear, no transmission"| D_TAXI
  D_W_VAC edge_D_W_VAC__D_VAC@--> D_VAC
  D_VAC edge_D_VAC__D_VAC_CXL@-->|"after cancel, measured clear"| D_VAC_CXL
  D_VAC_CXL edge_D_VAC_CXL__D_END@--> D_END
  D_VAC edge_D_VAC__D_VAC_START@-->|"after abort or runway change,<br/>measured clear"| D_VAC_START
  D_VAC_START edge_D_VAC_START__D_TAXI@--> D_TAXI
  D_VAC edge_D_VAC__D_VAC_NEG@-->|"still on a runway or no current<br/>sample"| D_VAC_NEG
  D_VAC_NEG edge_D_VAC_NEG__D_W_VAC@-.->|"RETRY when clear"| D_W_VAC
  D_ANY edge_D_ANY__D_L_SILENT@--> D_L_SILENT
  D_ANY edge_D_ANY__D_L_LOST@--> D_L_LOST
  D_ANY edge_D_ANY__D_L_BAD@--> D_L_BAD
  D_L_SILENT edge_D_L_SILENT__D_START@-.->|"RETURN: start again with a valid<br/>call"| D_START
  D_L_LOST edge_D_L_LOST__D_START@-.->|"RETURN: start again with a valid<br/>call"| D_START
  D_L_BAD edge_D_L_BAD__D_START@-.->|"RETURN: start again with a valid<br/>call"| D_START

  classDef normalFlow stroke:#15803d,stroke-width:3px,stroke-dasharray:0
  classDef errorFlow stroke:#b91c1c,stroke-width:2.5px,stroke-dasharray:0
  classDef recoveryFlow stroke:#92400e,stroke-width:2px,stroke-dasharray:7 4
  classDef neutralFlow stroke:#64748b,stroke-width:1px,stroke-dasharray:0
  classDef pilotCall fill:#eff6ff,stroke:#1d4ed8,color:#172554
  classDef errorNode fill:#fef2f2,stroke:#b91c1c,color:#450a0a
  classDef recoveryNode fill:#fffbeb,stroke:#92400e,color:#451a03
  class edge_D_START__D_G_HUB,edge_D_START__D_INFO,edge_D_START__D_RCHECK,edge_D_INFO__D_INFO_R,edge_D_RCHECK__D_INFO_R,edge_D_INFO_R__D_START,edge_D_STARTUP_SB__D_ANY,edge_D_STARTUP_OK__D_ANY,edge_D_TAXI_OK__D_ANY,edge_D_HOLD_Q__D_ANY,edge_D_LU_OK__D_ANY,edge_D_TO_OK__D_ANY,edge_D_TAXIIN_OK__D_ANY,edge_D_ANY__D_SAYAGAIN,edge_D_START__D_SAYAGAIN,edge_D_SAYAGAIN__D_SAYAGAIN_R,edge_D_SAYAGAIN_R__D_ANY,edge_D_NOSESSION__D_START neutralFlow
  class edge_D_G_HUB__D_G_ID,edge_D_G_HUB__D_G_WHICH,edge_D_G_HUB__D_G_ROLE,edge_D_G_HUB__D_G_COAL,edge_D_G_HUB__D_G_STALE,edge_D_G_HUB__D_G_BUSY,edge_D_G_HUB__D_G_NONE,edge_D_G_HUB__D_G_SILENT,edge_D_W_GROUND__D_STARTUP_CLOSED,edge_D_STARTUP__D_STARTUP_ARR,edge_D_TAXI__D_TAXI_NORWY,edge_D_TAXI__D_TAXI_NOTGND,edge_D_TAXI_RB__D_RB_BAD,edge_D_CROSS_RB__D_RB_BAD,edge_D_LU_RB__D_RB_BAD,edge_D_TO_RB__D_RB_BAD,edge_D_TAXIIN_RB__D_RB_BAD,edge_D_CROSS_RB__D_RB_NONE,edge_D_LU_RB__D_RB_NONE,edge_D_TO_RB__D_RB_NONE,edge_D_CROSS__D_CROSS_NEG,edge_D_CROSS__D_CROSS_TWR,edge_D_CROSS_CLR__D_CROSS_REV,edge_D_CROSS_OK__D_CROSS_REV,edge_D_CROSS_VAC__D_VAC_NEG,edge_D_HOLD__D_HOLD_NEG,edge_D_HOLD__D_HOLD_RBFIRST,edge_D_HOLD__D_HOLD_TWR,edge_D_LINEDUP__D_LINEDUP_NEG,edge_D_LU_CLR__D_X_LU,edge_D_LU_OK__D_X_LU,edge_D_TO_CLR__D_X_TO,edge_D_TO_OK__D_X_TO,edge_D_TAXI_OK__D_X_RWY_TAXI,edge_D_HOLD_Q__D_X_RWY_TAXI,edge_D_LU_CLR__D_X_RWY_TAXI,edge_D_CROSS_Q__D_X_RWY_TAXI,edge_D_LU_OK__D_X_RWY_OCC,edge_D_TO_CLR__D_X_RWY_OCC,edge_D_TAXI_CLR__D_X_STALE,edge_D_LU_CLR__D_X_STALE,edge_D_TO_CLR__D_X_STALE,edge_D_CROSS_CLR__D_X_STALE,edge_D_TAXI_OK__D_AIR_UNCLR,edge_D_HOLD_Q__D_AIR_UNCLR,edge_D_LU_OK__D_AIR_UNCLR,edge_D_AIRBORNE__D_AIRBORNE_NEG,edge_D_HANDOFF__D_HANDOFF_EARLY,edge_D_TAXIIN__D_TAXIIN_NEG,edge_D_SAYAGAIN__D_NOSESSION,edge_D_ANY__D_L_SILENT,edge_D_ANY__D_L_LOST,edge_D_ANY__D_L_BAD errorFlow
  class edge_D_G_ID__D_START,edge_D_G_WHICH__D_START,edge_D_G_ROLE__D_START,edge_D_G_COAL__D_START,edge_D_G_STALE__D_START,edge_D_G_BUSY__D_START,edge_D_G_NONE__D_START,edge_D_G_SILENT__D_START,edge_D_STARTUP_CLOSED__D_START,edge_D_STARTUP_ARR__D_START,edge_D_TAXI_NORWY__D_TAXI,edge_D_TAXI_NOTGND__D_W_GROUND,edge_D_RB_BAD__D_TAXI_RB,edge_D_RB_BAD__D_CROSS_RB,edge_D_RB_BAD__D_LU_RB,edge_D_RB_BAD__D_TO_RB,edge_D_RB_BAD__D_TAXIIN_RB,edge_D_CROSS_NEG__D_CROSS,edge_D_CROSS_TWR__D_CROSS,edge_D_CROSS_REV__D_CROSS,edge_D_HOLD_NEG__D_HOLD,edge_D_HOLD_RBFIRST__D_TAXI_RB,edge_D_HOLD_TWR__D_READY_DEP,edge_D_LINEDUP_NEG__D_W_LINEUP,edge_D_READY_TO__D_LINEDUP_HOLD,edge_D_X_LU__D_READY_DEP,edge_D_X_LU__D_HOLD_Q,edge_D_X_TO__D_READY_TO,edge_D_X_RWY_TAXI__D_TAXI_RB,edge_D_X_RWY_OCC__D_W_VAC,edge_D_X_STALE__D_SAYAGAIN,edge_D_SAYAGAIN_R__D_TAXI,edge_D_SAYAGAIN_R__D_CROSS_Q,edge_D_SAYAGAIN_R__D_CROSS_RB,edge_D_SAYAGAIN_R__D_READY_DEP,edge_D_SAYAGAIN_R__D_READY_TO,edge_D_AIR_UNCLR__D_AIRBORNE,edge_D_AIR_UNCLR__D_HANDOFF,edge_D_AIR_UNCLR__D_W_10NM,edge_D_AIR_UNCLR__D_RETURN,edge_D_STARTUP_OK__D_TAXIIN,edge_D_TAXI_OK__D_TAXIIN,edge_D_HOLD_Q__D_TAXIIN,edge_D_CROSS_Q__D_TAXIIN,edge_D_TAXIIN__D_TAXIIN_CLR,edge_D_TAXIIN_CLR__D_TAXIIN_RB,edge_D_TAXIIN_RB__D_TAXIIN_OK,edge_D_TAXIIN_OK__D_W_PARK,edge_D_W_PARK__D_W_CROSSPT,edge_D_CROSS_EV__D_W_PARK,edge_D_CROSS_DONE__D_W_PARK,edge_D_W_PARK__D_CHECKOUT,edge_D_CHECKOUT__D_CHECKOUT_R,edge_D_CHECKOUT_R__D_END,edge_D_W_PARK__D_TAXI,edge_D_W_PARK__D_STARTUP,edge_D_ANY__D_CANCEL,edge_D_ANY__D_EMER,edge_D_ANY__D_CANCEL_EMER,edge_D_START__D_CANCEL,edge_D_START__D_EMER,edge_D_START__D_CANCEL_EMER,edge_D_RB_NONE__D_SAYAGAIN,edge_D_CANCEL__D_CANCEL_R,edge_D_CANCEL_R__D_END,edge_D_CANCEL__D_CANCEL_NONE,edge_D_CANCEL__D_CANCEL_OCC,edge_D_CANCEL_OCC__D_W_VAC,edge_D_EMER__D_EMER_R,edge_D_EMER__D_ABORT_R,edge_D_EMER_R__D_ANY,edge_D_EMER__D_EMER_NEW,edge_D_EMER_NEW__D_TO_ARR,edge_D_LU_OK__D_ABORT,edge_D_LINEDUP_HOLD__D_ABORT,edge_D_TO_CLR__D_ABORT,edge_D_TO_OK__D_ABORT,edge_D_ABORT__D_ABORT_R,edge_D_ABORT_R__D_W_VAC,edge_D_ABORT__D_EMER_R,edge_D_CANCEL_EMER__D_CANCEL_EMER_R,edge_D_CANCEL_EMER_R__D_ANY,edge_D_W_VAC__D_VAC_CXL_EV,edge_D_VAC_CXL_EV__D_END,edge_D_W_VAC__D_TAXI,edge_D_W_VAC__D_VAC,edge_D_VAC__D_VAC_CXL,edge_D_VAC_CXL__D_END,edge_D_VAC__D_VAC_START,edge_D_VAC_START__D_TAXI,edge_D_VAC__D_VAC_NEG,edge_D_VAC_NEG__D_W_VAC,edge_D_L_SILENT__D_START,edge_D_L_LOST__D_START,edge_D_L_BAD__D_START recoveryFlow
  class edge_D_START__D_STARTUP,edge_D_STARTUP__D_STARTUP_OK,edge_D_STARTUP__D_STARTUP_SB,edge_D_STARTUP_SB__D_W_GROUND,edge_D_W_GROUND__D_STARTUP_EV,edge_D_START__D_TAXI,edge_D_STARTUP_OK__D_TAXI,edge_D_STARTUP_EV__D_TAXI,edge_D_TAXI__D_TAXI_CLR,edge_D_TAXI_CLR__D_TAXI_RB,edge_D_TAXI_RB__D_TAXI_OK,edge_D_TAXI_OK__D_W_CROSSPT,edge_D_W_CROSSPT__D_CROSS,edge_D_CROSS__D_CROSS_Q,edge_D_CROSS__D_CROSS_CLR,edge_D_CROSS_Q__D_CROSS_CLR,edge_D_CROSS_CLR__D_CROSS_RB,edge_D_CROSS_RB__D_CROSS_OK,edge_D_CROSS_OK__D_W_CROSSING,edge_D_W_CROSSING__D_CROSS_EV,edge_D_W_CROSSING__D_CROSS_VAC,edge_D_CROSS_VAC__D_CROSS_DONE,edge_D_CROSS_VAC__D_CROSS_OK,edge_D_TAXI_OK__D_W_HOLD,edge_D_CROSS_EV__D_W_HOLD,edge_D_CROSS_DONE__D_W_HOLD,edge_D_W_HOLD__D_HOLD,edge_D_HOLD__D_HOLD_Q,edge_D_READY_DEP__D_HOLD_Q,edge_D_HOLD__D_LU_CLR,edge_D_READY_DEP__D_LU_CLR,edge_D_HOLD_Q__D_LU_CLR,edge_D_LU_CLR__D_LU_RB,edge_D_LU_RB__D_LU_OK,edge_D_LU_OK__D_W_LINEUP,edge_D_LU_RB__D_LINEDUP_HOLD,edge_D_W_LINEUP__D_TO_CLR,edge_D_W_LINEUP__D_LINEDUP,edge_D_LINEDUP__D_LINEDUP_HOLD,edge_D_LINEDUP_HOLD__D_TO_CLR,edge_D_TO_CLR__D_TO_RB,edge_D_TO_RB__D_TO_OK,edge_D_TO_OK__D_W_ROLL,edge_D_W_ROLL__D_AIR_EV,edge_D_AIR_EV__D_AIRBORNE,edge_D_AIR_EV__D_HANDOFF,edge_D_AIR_EV__D_W_10NM,edge_D_AIR_EV__D_RETURN,edge_D_AIRBORNE__D_AIRBORNE_R,edge_D_HANDOFF__D_HANDOFF_R,edge_D_HANDOFF_R__D_END,edge_D_W_10NM__D_10NM_R,edge_D_10NM_R__D_END,edge_D_RETURN__D_TO_ARR normalFlow
  class D_INFO,D_RCHECK,D_STARTUP,D_TAXI,D_TAXI_RB,D_CROSS_RB,D_LU_RB,D_TO_RB,D_TAXIIN_RB,D_CROSS,D_CROSS_VAC,D_HOLD,D_READY_DEP,D_LINEDUP,D_READY_TO,D_SAYAGAIN,D_AIRBORNE,D_HANDOFF,D_RETURN,D_TAXIIN,D_CHECKOUT,D_CANCEL,D_EMER,D_ABORT,D_CANCEL_EMER,D_VAC pilotCall
  class D_G_ID,D_G_WHICH,D_G_ROLE,D_G_COAL,D_G_STALE,D_G_BUSY,D_G_NONE,D_G_SILENT,D_STARTUP_CLOSED,D_STARTUP_ARR,D_TAXI_NORWY,D_TAXI_NOTGND,D_RB_BAD,D_RB_NONE,D_CROSS_NEG,D_CROSS_TWR,D_CROSS_REV,D_VAC_NEG,D_HOLD_NEG,D_HOLD_RBFIRST,D_HOLD_TWR,D_LINEDUP_NEG,D_X_LU,D_X_TO,D_X_RWY_TAXI,D_X_RWY_OCC,D_X_STALE,D_AIR_UNCLR,D_AIRBORNE_NEG,D_HANDOFF_EARLY,D_TAXIIN_NEG,D_NOSESSION,D_L_SILENT,D_L_LOST,D_L_BAD errorNode
  class D_CANCEL,D_CANCEL_R,D_CANCEL_NONE,D_CANCEL_OCC,D_EMER,D_EMER_R,D_EMER_NEW,D_ABORT,D_ABORT_R,D_CANCEL_EMER,D_CANCEL_EMER_R,D_W_VAC,D_VAC_CXL_EV,D_VAC_CXL,D_VAC_START recoveryNode
```

## Chart 2 — Arrival: check-in to parking

```mermaid
---
config:
  htmlLabels: false
  fontSize: 14
  themeVariables:
    fontSize: 14px
  flowchart:
    nodeSpacing: 12
    rankSpacing: 24
    padding: 8
    wrappingWidth: 180
    htmlLabels: false
---
flowchart TD
  A_START(("START: airborne, inbound to [FIELD]"))
  A_FROM_DEP(("From DEPARTURE chart: airborne departure calls inbound, or no-session emergency"))
  A_END(("END: airfield session closed"))
  A_TO_DEP(("Continue in DEPARTURE chart at the taxi clearance or startup reply"))

  %% Guards on EVERY call below: no enrolment, fix and RETRY the same call
    A_G_HUB{{"Any YOU call in this chart can first meet one of these"}}
    A_G_ID(["ATC: last transmitter, i could not recognize your callsign or modex — or — more than one aircraft matches, say modex — or — unable, no player aircraft with that identity — or — unable, your radio name does not identify you"])
    A_G_WHICH(["ATC: Approach, say which airfield you are calling, [FIELD] or [OTHER_FIELD] — or — Approach, say again with the airfield name — or — Tower, more than one airfield matches that name, say again with the full name"])
    A_G_ROLE(["ATC: unable, [FIELD] approach is not on this frequency, contact [FIELD] approach [APPROACH_FREQ]"])
    A_G_COAL(["ATC: unable, [OTHER_FIELD] is not under your coalition's control, no service — or — unable, [OTHER_FIELD] is neutral, no service"])
    A_G_STALE(["ATC: unable, no live airfield link — only cancel, checking out and cancel emergency still accepted"])
    A_G_BUSY(["ATC: unable, you are working [OTHER_FIELD] — or — unable, you hold a runway clearance at [OTHER_FIELD], cancel it first — or — unable, airfield traffic limit reached"])
    A_G_NONE(["ATC: say again — no command recognised, or a procedure call voided by a negation"])
    A_G_SILENT{{"NO REPLY: call not opened with an airfield receiver, named airfield not on this frequency, or frequency not an airfield radio"}}

  %% Facts: any time, before or during a session — never enrols, never moves the session
    A_INFO["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request information**`"]
    A_RCHECK["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], radio check**`"]
    A_INFO_R(["ATC: runway [RUNWAY] in use, [WIND], altimeter [ALTIMETER], [PATTERN] traffic"])

  %% 1 · Approach: check-in and arrival briefing
    A_INBOUND["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], inbound landing**`"]
    A_BRIEF(["ATC: runway [RUNWAY] in use, [WIND], altimeter [ALTIMETER], no published approach available, make a visual approach, [VISUAL_INSTRUCTION], read back runway and altimeter"])
    A_STANDBY(["ATC: [RUNWAY_REASON], remain clear of the airfield, stand by"])
    A_W_STANDBY{{"FLY — NO CALL: remain clear of the airfield — ATC transmits the briefing once runway and altimeter are known"}}
    A_INB_GROUND(["ATC: unable, you are on the ground"])
    A_NOT_AIR(["ATC event: you are not airborne, arrival not required, report inbound when airborne"])
    A_ARR_RB["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], readback runway [RUNWAY], altimeter [ALTIMETER]**`"]
    A_ARR_OK(["ATC: readback correct, report [PATTERN] downwind runway [RUNWAY] — or, no published pattern — readback correct, report final runway [RUNWAY]"])
    A_REBRIEF(["ATC event: runway changed, runway [RUNWAY] in use, … read back runway and altimeter — or, if cleared to land — go around, runway changed, runway [RUNWAY] in use, … — or, altimeter changed during the briefing — runway [RUNWAY] in use, … read back runway and altimeter"])

  %% Readback outcomes shared by every readback in this chart
    A_RB_BAD(["ATC: negative readback, [READBACK_PROBLEM], read back again"])
    A_RB_NONE(["ATC: unable, no clearance to read back"])

  %% 2 · Published pattern — skip to final for a straight-in
    A_W_PATTERN{{"FLY — NO CALL: enter [PATTERN] downwind, within 6 NM of [FIELD] and below 3000 ft above the field"}}
    A_DOWNWIND["`YOU: **[FIELD] tower, [CALLSIGN], downwind runway [RUNWAY]**`"]
    A_DOWNWIND_R(["ATC: roger downwind, report base"])
    A_W_BASE{{"FLY — NO CALL: turn base, still within 6 NM and below 3000 ft above the field"}}
    A_BASE["`YOU: **[FIELD] tower, [CALLSIGN], base runway [RUNWAY]**`"]
    A_BASE_R(["ATC: roger base, report final runway [RUNWAY]"])
    A_POS_NEG(["ATC: negative, position not confirmed, [CURRENT_INSTRUCTION]"])
    A_NOPATTERN(["ATC: unable, no published traffic pattern, [CURRENT_INSTRUCTION]"])
    A_ORDER(["ATC: unable, [CURRENT_INSTRUCTION] — call out of sequence"])
    A_BRIEF_FIRST(["ATC: unable, read back runway and altimeter first"])

  %% 3 · Tower: final and landing clearance
    A_W_FINAL{{"FLY — NO CALL: on the extended centreline of runway [RUNWAY] inside 10 NM, below 3000 ft above the field, heading within 45 degrees"}}
    A_FINAL["`YOU: **[FIELD] tower, [CALLSIGN], final runway [RUNWAY]**`"]
    A_FINAL_NEG(["ATC: negative, not observed on final runway [RUNWAY], [CURRENT_INSTRUCTION]"])
    A_FINAL_Q(["ATC: roger final, number [N], continue approach runway [RUNWAY]"])
    A_FINAL_TWR(["ATC: roger final, contact [FIELD] tower [TOWER_FREQ] — or — roger final, unable, no tower service at [FIELD], runway clearances not available"])
    A_LAND_CLR(["ATC, appended to the final reply or as an event: runway [RUNWAY], [WIND], cleared to land, read back"])
    A_LAND_RB["`YOU: **[FIELD] tower, [CALLSIGN], readback runway [RUNWAY], cleared to land**`"]
    A_LAND_OK(["ATC: readback correct"])
    A_X_STALE{{"NO TRANSMISSION: bridge stale — an unstarted landing or crossing clearance is revoked, a cleared arrival falls back to re-entry, an unread briefing falls back to standby, every earlier reply becomes invalid"}}
    A_X_GA(["ATC event: no readback, go around, clearance cancelled, fly runway heading, … — or — go around, [REVOKE_REASON], fly runway heading, re-enter [PATTERN] downwind runway [RUNWAY], report downwind — or — …, fly runway heading, report final runway [RUNWAY]"])

  %% Go-around and touch-and-go
    A_GOAROUND["`YOU: **[FIELD] tower, [CALLSIGN], going around**`"]
    A_GA_R(["ATC: roger going around, fly runway heading, re-enter [PATTERN] downwind runway [RUNWAY], report downwind — or, no pattern — roger going around, fly runway heading, report final runway [RUNWAY]"])
    A_GA_NORWY(["ATC: roger going around, runway in use not available, remain clear of the airfield, stand by"])
    A_GA_WAIT(["ATC: stand by for landing confirmation"])
    A_GA_UNABLE(["ATC: unable, no approach in progress"])
    A_TNG_EV(["ATC event: airborne observed, go around, fly runway heading, re-enter [PATTERN] downwind runway [RUNWAY], report downwind — or — …, report final runway [RUNWAY]"])

  %% 4 · Touchdown, roll-out, vacate
    A_W_TOUCH{{"FLY — NO CALL: land — the mission land event at [FIELD] starts the touchdown"}}
    A_UNCLR_LAND(["ATC event: landing without clearance observed"])
    A_W_ROLLOUT{{"ROLL OUT — NO CALL: an on-ground sample at [FIELD] confirms the landing"}}
    A_LANDED_EV(["ATC event: landing observed, vacate the runway, report vacated"])
    A_LANDED["`YOU: **[FIELD] tower, [CALLSIGN], landed**`"]
    A_LANDED_R(["ATC: stand by for landing confirmation — or — vacate the runway, report vacated — or — request taxi to parking — confirms telemetry only"])
    A_LANDED_NEG_CALL["`YOU: **[FIELD] tower, [CALLSIGN], landed**`"]
    A_LANDED_NEG(["ATC: negative, touchdown not observed"])
    A_W_VACATE{{"TAXI — NO CALL: leave every runway footprint"}}
    A_VAC_EV(["ATC event: runway vacated, request taxi to parking"])
    A_VAC["`YOU: **[FIELD] tower, [CALLSIGN], vacated runway [RUNWAY]**`"]
    A_VAC_R(["ATC: roger, request taxi to parking"])
    A_VAC_NEG(["ATC: negative, not observed clear of the runway"])

  %% 5 · Ground: taxi to parking with runway crossings, check-out, re-departure
    A_TAXIIN["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request taxi to parking**`"]
    A_TAXI_VAC["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request taxi**`"]
    A_TAXIIN_NEG(["ATC: vacate the runway first, report vacated — or — unable, not observed on the ground here — or — unable, [CURRENT_INSTRUCTION]"])
    A_TAXIIN_CLR(["ATC: taxi to parking via [TAXI_ROUTE], hold short of all runways, read back"])
    A_TAXIIN_RB["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], readback taxi to parking, hold short all runways**`"]
    A_TAXIIN_OK(["ATC: readback correct, taxi to parking, hold short of all runways, report parked"])
    A_W_PARK{{"TAXI — NO CALL: to parking — holds short of every runway"}}
    A_W_CROSSPT{{"TAXI — NO CALL: stop beside runway [CROSS_RUNWAY], off every runway, within 400 m of its edge"}}
    A_CROSS["`YOU: **[FIELD] tower, [CALLSIGN], request crossing runway [CROSS_RUNWAY]**`"]
    A_CROSS_NEG(["ATC: negative, not observed holding short runway [CROSS_RUNWAY] — or — unable, say the runway to cross"])
    A_CROSS_Q(["ATC: hold short runway [CROSS_RUNWAY], number [N] for crossing — or, no tower here — hold short runway [CROSS_RUNWAY], contact [FIELD] tower [TOWER_FREQ]"])
    A_CROSS_CLR(["ATC: cross runway [CROSS_RUNWAY], report vacated, read back"])
    A_CROSS_RB["`YOU: **[FIELD] tower, [CALLSIGN], readback cross runway [CROSS_RUNWAY]**`"]
    A_CROSS_OK(["ATC: readback correct, cross runway [CROSS_RUNWAY], report vacated"])
    A_CROSS_REV(["ATC event: no readback, hold short runway [CROSS_RUNWAY], crossing clearance cancelled, clearance cancelled — or — hold short runway [CROSS_RUNWAY], crossing clearance cancelled, [REVOKE_REASON]"])
    A_W_CROSSING{{"TAXI — NO CALL: cross — telemetry must see you enter runway [CROSS_RUNWAY] and then clear of every runway"}}
    A_CROSS_EV(["ATC event: runway vacated, continue taxi to parking, hold short of all runways"])
    A_CROSS_VAC["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], vacated runway [CROSS_RUNWAY]**`"]
    A_CROSS_DONE(["ATC: roger, runway vacated, continue taxi to parking, hold short of all runways"])
    A_CHECKOUT["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], checking out**`"]
    A_CHECKOUT_R(["ATC: copy, checked out"])
    A_REDEP_TAXI["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request taxi**`"]
    A_REDEP_START["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], request startup**`"]

  %% Any enrolled point: status, cancel, emergency
    A_ANY{{"At any enrolled point of this chart"}}
    A_SAYAGAIN["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], say again**`"]
    A_SAYAGAIN_R(["ATC: [CURRENT_INSTRUCTION] — rebuilt from present state, never a lapsed or revoked clearance"])
    A_NOSESSION(["ATC: unable, no active session, state your request"])
    A_CANCEL["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], cancel**`"]
    A_CANCEL_R(["ATC: copy, arrival cancelled, checked out"])
    A_CANCEL_NONE(["ATC: copy, no active airfield session"])
    A_CANCEL_OCC(["ATC: clearance cancelled, vacate the runway, report vacated"])
    A_W_CXL_VAC{{"TAXI — NO CALL: leave every runway footprint"}}
    A_CXL_VAC_EV(["ATC event: runway vacated, checked out"])
    A_CXL_VAC["`YOU: **[FIELD] tower, [CALLSIGN], vacated**`"]
    A_CXL_VAC_R(["ATC: roger, runway vacated, checked out"])
    A_EMER["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], mayday mayday mayday, low fuel**`"]
    A_EMER_R(["ATC: copy emergency, you have priority, [CURRENT_INSTRUCTION]"])
    A_EMER_NEW_R(["ATC: copy emergency, you have priority, runway [RUNWAY] in use, [WIND], altimeter [ALTIMETER], no published approach available, make a visual approach, [VISUAL_INSTRUCTION], read back runway and altimeter"])
    A_EMER_GROUND(["ATC event: emergency on the ground, vacate any runway, report vacated"])
    A_CANCEL_EMER["`YOU: **[FIELD] [CURRENT_ROLE], [CALLSIGN], cancel emergency**`"]
    A_CANCEL_EMER_R(["ATC: roger, emergency cancelled, [CURRENT_INSTRUCTION] — or — roger, no emergency on record"])

  %% Lifecycle closures: RETURN to a fresh call
    A_L_SILENT{{"NO TRANSMISSION: dead, eject, disconnect or birth event — new incarnation or coalition — airfield or frequency no longer served — new mission epoch — session moved to a carrier"}}
    A_L_ELSE(["ATC event: landing not observed at [FIELD], arrival closed"])
    A_L_AIR(["ATC event: airborne, arrival closed"])
    A_L_LOST(["ATC event: radar contact lost, airfield session closed, call again"])
    A_L_BAD(["ATC event: tracking data invalid, airfield session closed, call again"])

  %% Stable edge IDs keep path colors attached to their transitions.
  A_START edge_A_START__A_G_HUB@--> A_G_HUB
  A_G_HUB edge_A_G_HUB__A_G_ID@--> A_G_ID
  A_G_HUB edge_A_G_HUB__A_G_WHICH@--> A_G_WHICH
  A_G_HUB edge_A_G_HUB__A_G_ROLE@--> A_G_ROLE
  A_G_HUB edge_A_G_HUB__A_G_COAL@--> A_G_COAL
  A_G_HUB edge_A_G_HUB__A_G_STALE@--> A_G_STALE
  A_G_HUB edge_A_G_HUB__A_G_BUSY@--> A_G_BUSY
  A_G_HUB edge_A_G_HUB__A_G_NONE@--> A_G_NONE
  A_G_HUB edge_A_G_HUB__A_G_SILENT@--> A_G_SILENT
  A_G_ID edge_A_G_ID__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_WHICH edge_A_G_WHICH__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_ROLE edge_A_G_ROLE__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_COAL edge_A_G_COAL__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_STALE edge_A_G_STALE__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_BUSY edge_A_G_BUSY__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_NONE edge_A_G_NONE__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_G_SILENT edge_A_G_SILENT__A_START@-.->|"RETRY: fix name, role,<br/>frequency, callsign or wait for<br/>link, then repeat the call"| A_START
  A_START edge_A_START__A_INFO@--> A_INFO
  A_START edge_A_START__A_RCHECK@--> A_RCHECK
  A_INFO edge_A_INFO__A_INFO_R@--> A_INFO_R
  A_RCHECK edge_A_RCHECK__A_INFO_R@--> A_INFO_R
  A_INFO_R edge_A_INFO_R__A_START@-.->|"RETURN to your current step"| A_START
  A_START edge_A_START__A_INBOUND@--> A_INBOUND
  A_FROM_DEP edge_A_FROM_DEP__A_INBOUND@--> A_INBOUND
  A_INBOUND edge_A_INBOUND__A_BRIEF@-->|"runway and altimeter known:<br/>enrolled"| A_BRIEF
  A_INBOUND edge_A_INBOUND__A_STANDBY@-->|"runway or altimeter unavailable:<br/>enrolled"| A_STANDBY
  A_STANDBY edge_A_STANDBY__A_W_STANDBY@--> A_W_STANDBY
  A_W_STANDBY edge_A_W_STANDBY__A_BRIEF@-->|"facts available: event"| A_BRIEF
  A_INBOUND edge_A_INBOUND__A_INB_GROUND@-->|"you hold a departure session and<br/>are on the ground"| A_INB_GROUND
  A_INBOUND edge_A_INBOUND__A_NOT_AIR@-->|"enrolled, then sampled on the<br/>ground here without having flown"| A_NOT_AIR
  A_NOT_AIR edge_A_NOT_AIR__A_START@-.->|"RETURN"| A_START
  A_BRIEF edge_A_BRIEF__A_ARR_RB@--> A_ARR_RB
  A_ARR_RB edge_A_ARR_RB__A_ARR_OK@-->|"exact runway and altimeter"| A_ARR_OK
  A_BRIEF edge_A_BRIEF__A_W_STANDBY@-.->|"bridge stale: back to standby,<br/>no transmission"| A_W_STANDBY
  A_REBRIEF edge_A_REBRIEF__A_ARR_RB@--> A_ARR_RB
  A_ARR_RB edge_A_ARR_RB__A_RB_BAD@-->|"wrong or missing value, extra<br/>runway, cleared word in briefing<br/>readback, negation, cancel word"| A_RB_BAD
  A_LAND_RB edge_A_LAND_RB__A_RB_BAD@-->|"wrong or missing value, extra<br/>runway, cleared word in briefing<br/>readback, negation, cancel word"| A_RB_BAD
  A_CROSS_RB edge_A_CROSS_RB__A_RB_BAD@-->|"wrong or missing value, extra<br/>runway, cleared word in briefing<br/>readback, negation, cancel word"| A_RB_BAD
  A_TAXIIN_RB edge_A_TAXIIN_RB__A_RB_BAD@-->|"wrong or missing value, extra<br/>runway, cleared word in briefing<br/>readback, negation, cancel word"| A_RB_BAD
  A_RB_BAD edge_A_RB_BAD__A_ARR_RB@-.->|"RETRY briefing readback"| A_ARR_RB
  A_RB_BAD edge_A_RB_BAD__A_LAND_RB@-.->|"RETRY landing readback"| A_LAND_RB
  A_RB_BAD edge_A_RB_BAD__A_CROSS_RB@-.->|"RETRY crossing readback"| A_CROSS_RB
  A_RB_BAD edge_A_RB_BAD__A_TAXIIN_RB@-.->|"RETRY parking readback"| A_TAXIIN_RB
  A_LAND_RB edge_A_LAND_RB__A_RB_NONE@-->|"clearance already lapsed or<br/>revoked"| A_RB_NONE
  A_CROSS_RB edge_A_CROSS_RB__A_RB_NONE@-->|"clearance already lapsed or<br/>revoked"| A_RB_NONE
  A_ARR_OK edge_A_ARR_OK__A_W_PATTERN@-->|"pattern published"| A_W_PATTERN
  A_W_PATTERN edge_A_W_PATTERN__A_DOWNWIND@--> A_DOWNWIND
  A_DOWNWIND edge_A_DOWNWIND__A_DOWNWIND_R@-->|"measured in the pattern"| A_DOWNWIND_R
  A_DOWNWIND_R edge_A_DOWNWIND_R__A_W_BASE@--> A_W_BASE
  A_W_BASE edge_A_W_BASE__A_BASE@--> A_BASE
  A_BASE edge_A_BASE__A_BASE_R@-->|"measured in the pattern, after<br/>downwind"| A_BASE_R
  A_DOWNWIND edge_A_DOWNWIND__A_POS_NEG@-->|"outside 6 NM or 3000 ft"| A_POS_NEG
  A_BASE edge_A_BASE__A_POS_NEG@-->|"outside 6 NM or 3000 ft"| A_POS_NEG
  A_POS_NEG edge_A_POS_NEG__A_W_PATTERN@-.->|"RETRY in position"| A_W_PATTERN
  A_DOWNWIND edge_A_DOWNWIND__A_NOPATTERN@-->|"no published pattern"| A_NOPATTERN
  A_BASE edge_A_BASE__A_NOPATTERN@-->|"no published pattern"| A_NOPATTERN
  A_BASE edge_A_BASE__A_ORDER@-->|"before downwind"| A_ORDER
  A_ORDER edge_A_ORDER__A_W_PATTERN@-.->|"RETURN"| A_W_PATTERN
  A_DOWNWIND edge_A_DOWNWIND__A_BRIEF_FIRST@-->|"briefing readback still owed"| A_BRIEF_FIRST
  A_BASE edge_A_BASE__A_BRIEF_FIRST@-->|"briefing readback still owed"| A_BRIEF_FIRST
  A_FINAL edge_A_FINAL__A_BRIEF_FIRST@-->|"briefing readback still owed"| A_BRIEF_FIRST
  A_BRIEF_FIRST edge_A_BRIEF_FIRST__A_ARR_RB@-.->|"RETRY"| A_ARR_RB
  A_ARR_OK edge_A_ARR_OK__A_W_FINAL@-->|"no pattern: straight-in"| A_W_FINAL
  A_BASE_R edge_A_BASE_R__A_W_FINAL@--> A_W_FINAL
  A_NOPATTERN edge_A_NOPATTERN__A_W_FINAL@--> A_W_FINAL
  A_DOWNWIND_R edge_A_DOWNWIND_R__A_W_FINAL@-->|"base report optional: when<br/>measured on final"| A_W_FINAL
  A_W_PATTERN edge_A_W_PATTERN__A_W_FINAL@-->|"straight-in also allowed with a<br/>pattern"| A_W_FINAL
  A_W_FINAL edge_A_W_FINAL__A_FINAL@--> A_FINAL
  A_FINAL edge_A_FINAL__A_FINAL_NEG@-->|"not measured on final"| A_FINAL_NEG
  A_FINAL_NEG edge_A_FINAL_NEG__A_W_FINAL@-.->|"RETRY on final"| A_W_FINAL
  A_FINAL edge_A_FINAL__A_FINAL_Q@-->|"queued, or a runway occupied"| A_FINAL_Q
  A_FINAL edge_A_FINAL__A_FINAL_TWR@-->|"no tower on this frequency"| A_FINAL_TWR
  A_FINAL_TWR edge_A_FINAL_TWR__A_FINAL@-.->|"retune to tower, RETRY the final<br/>call"| A_FINAL
  A_FINAL edge_A_FINAL__A_LAND_CLR@-->|"first, lane free, runways clear,<br/>tower here"| A_LAND_CLR
  A_FINAL_Q edge_A_FINAL_Q__A_LAND_CLR@-->|"WAIT — granted as an event when<br/>eligible"| A_LAND_CLR
  A_LAND_CLR edge_A_LAND_CLR__A_LAND_RB@--> A_LAND_RB
  A_LAND_RB edge_A_LAND_RB__A_LAND_OK@-->|"exact"| A_LAND_OK
  A_LAND_CLR edge_A_LAND_CLR__A_X_GA@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost"| A_X_GA
  A_LAND_OK edge_A_LAND_OK__A_X_GA@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost"| A_X_GA
  A_X_GA edge_A_X_GA__A_W_PATTERN@-.->|"after no readback: fly the<br/>circuit and report again"| A_W_PATTERN
  A_X_GA edge_A_X_GA__A_W_FINAL@-.->|"straight-in"| A_W_FINAL
  A_LAND_CLR edge_A_LAND_CLR__A_X_STALE@-->|"bridge goes stale"| A_X_STALE
  A_LAND_OK edge_A_LAND_OK__A_X_STALE@-->|"bridge goes stale"| A_X_STALE
  A_CROSS_CLR edge_A_CROSS_CLR__A_X_STALE@-->|"bridge goes stale"| A_X_STALE
  A_X_STALE edge_A_X_STALE__A_SAYAGAIN@-.->|"after the link returns: check<br/>what is current"| A_SAYAGAIN
  A_ARR_OK edge_A_ARR_OK__A_REBRIEF@-->|"wind selects another runway, or<br/>altimeter changes during the<br/>briefing"| A_REBRIEF
  A_FINAL_Q edge_A_FINAL_Q__A_REBRIEF@-->|"wind selects another runway, or<br/>altimeter changes during the<br/>briefing"| A_REBRIEF
  A_LAND_OK edge_A_LAND_OK__A_REBRIEF@-->|"wind selects another runway, or<br/>altimeter changes during the<br/>briefing"| A_REBRIEF
  A_W_PATTERN edge_A_W_PATTERN__A_GOAROUND@--> A_GOAROUND
  A_W_FINAL edge_A_W_FINAL__A_GOAROUND@--> A_GOAROUND
  A_FINAL_Q edge_A_FINAL_Q__A_GOAROUND@--> A_GOAROUND
  A_LAND_OK edge_A_LAND_OK__A_GOAROUND@--> A_GOAROUND
  A_GOAROUND edge_A_GOAROUND__A_GA_R@-->|"airborne arrival phase"| A_GA_R
  A_GA_R edge_A_GA_R__A_W_PATTERN@-.->|"pattern"| A_W_PATTERN
  A_GA_R edge_A_GA_R__A_W_FINAL@-.->|"straight-in"| A_W_FINAL
  A_GOAROUND edge_A_GOAROUND__A_GA_NORWY@-->|"no runway in use"| A_GA_NORWY
  A_GA_NORWY edge_A_GA_NORWY__A_W_STANDBY@--> A_W_STANDBY
  A_GOAROUND edge_A_GOAROUND__A_GA_WAIT@-->|"touched down, still on the<br/>ground"| A_GA_WAIT
  A_GOAROUND edge_A_GOAROUND__A_GA_UNABLE@-->|"rolled out, vacated, or taxiing"| A_GA_UNABLE
  A_TNG_EV edge_A_TNG_EV__A_W_PATTERN@-.->|"pattern"| A_W_PATTERN
  A_TNG_EV edge_A_TNG_EV__A_W_FINAL@-.->|"straight-in"| A_W_FINAL
  A_LAND_OK edge_A_LAND_OK__A_W_TOUCH@--> A_W_TOUCH
  A_W_TOUCH edge_A_W_TOUCH__A_W_ROLLOUT@-->|"land event here, landing lane<br/>held"| A_W_ROLLOUT
  A_W_TOUCH edge_A_W_TOUCH__A_UNCLR_LAND@-->|"land event here without a<br/>landing clearance"| A_UNCLR_LAND
  A_UNCLR_LAND edge_A_UNCLR_LAND__A_W_ROLLOUT@--> A_W_ROLLOUT
  A_FINAL_Q edge_A_FINAL_Q__A_W_TOUCH@-->|"lands without clearance"| A_W_TOUCH
  A_W_FINAL edge_A_W_FINAL__A_W_TOUCH@-->|"lands without clearance"| A_W_TOUCH
  A_W_ROLLOUT edge_A_W_ROLLOUT__A_LANDED_EV@-->|"on-ground sample here"| A_LANDED_EV
  A_LANDED_EV edge_A_LANDED_EV__A_W_VACATE@--> A_W_VACATE
  A_W_ROLLOUT edge_A_W_ROLLOUT__A_TNG_EV@-->|"airborne again"| A_TNG_EV
  A_W_ROLLOUT edge_A_W_ROLLOUT__A_LANDED@--> A_LANDED
  A_W_VACATE edge_A_W_VACATE__A_LANDED@--> A_LANDED
  A_LANDED edge_A_LANDED__A_LANDED_R@-->|"touchdown, landed or vacated<br/>phase"| A_LANDED_R
  A_W_TOUCH edge_A_W_TOUCH__A_LANDED_NEG_CALL@--> A_LANDED_NEG_CALL
  A_LANDED_NEG_CALL edge_A_LANDED_NEG_CALL__A_LANDED_NEG@-->|"still airborne: no touchdown<br/>observed"| A_LANDED_NEG
  A_LANDED_NEG edge_A_LANDED_NEG__A_W_TOUCH@-.->|"RETURN"| A_W_TOUCH
  A_W_VACATE edge_A_W_VACATE__A_VAC_EV@-->|"measured clear: no call needed"| A_VAC_EV
  A_W_VACATE edge_A_W_VACATE__A_VAC@--> A_VAC
  A_VAC edge_A_VAC__A_VAC_R@-->|"measured clear"| A_VAC_R
  A_VAC edge_A_VAC__A_VAC_NEG@-->|"still on a runway, in the air,<br/>or no current sample"| A_VAC_NEG
  A_VAC_NEG edge_A_VAC_NEG__A_W_VACATE@-.->|"RETRY when clear"| A_W_VACATE
  A_VAC_EV edge_A_VAC_EV__A_TAXIIN@--> A_TAXIIN
  A_VAC_EV edge_A_VAC_EV__A_TAXI_VAC@--> A_TAXI_VAC
  A_VAC_R edge_A_VAC_R__A_TAXIIN@--> A_TAXIIN
  A_VAC_R edge_A_VAC_R__A_TAXI_VAC@--> A_TAXI_VAC
  A_LANDED_EV edge_A_LANDED_EV__A_TAXIIN@-->|"may ask before vacating"| A_TAXIIN
  A_TAXIIN edge_A_TAXIIN__A_TAXIIN_CLR@-->|"on the ground here, off the<br/>runway"| A_TAXIIN_CLR
  A_TAXI_VAC edge_A_TAXI_VAC__A_TAXIIN_CLR@-->|"on the ground here, off the<br/>runway"| A_TAXIIN_CLR
  A_TAXIIN_CLR edge_A_TAXIIN_CLR__A_TAXIIN_RB@--> A_TAXIIN_RB
  A_TAXIIN edge_A_TAXIIN__A_TAXIIN_NEG@-->|"still on the runway or holding<br/>the lane, or no ground sample"| A_TAXIIN_NEG
  A_TAXIIN_NEG edge_A_TAXIIN_NEG__A_W_VACATE@-.->|"RETRY after vacating"| A_W_VACATE
  A_TAXIIN_RB edge_A_TAXIIN_RB__A_TAXIIN_OK@-->|"exact"| A_TAXIIN_OK
  A_TAXIIN_OK edge_A_TAXIIN_OK__A_W_PARK@--> A_W_PARK
  A_W_PARK edge_A_W_PARK__A_W_CROSSPT@-->|"route crosses a runway"| A_W_CROSSPT
  A_W_CROSSPT edge_A_W_CROSSPT__A_CROSS@--> A_CROSS
  A_CROSS edge_A_CROSS__A_CROSS_NEG@-->|"not measured at that crossing,<br/>or no valid runway named"| A_CROSS_NEG
  A_CROSS_NEG edge_A_CROSS_NEG__A_CROSS@-.->|"RETRY"| A_CROSS
  A_CROSS edge_A_CROSS__A_CROSS_Q@-->|"queued, or no tower on this<br/>frequency"| A_CROSS_Q
  A_CROSS_Q edge_A_CROSS_Q__A_CROSS@-.->|"retune to tower and RETRY, or<br/>WAIT for the event"| A_CROSS
  A_CROSS edge_A_CROSS__A_CROSS_CLR@-->|"first, lane free, runways clear,<br/>tower here"| A_CROSS_CLR
  A_CROSS_Q edge_A_CROSS_Q__A_CROSS_CLR@-->|"WAIT — granted as an event when<br/>eligible"| A_CROSS_CLR
  A_CROSS_CLR edge_A_CROSS_CLR__A_CROSS_RB@--> A_CROSS_RB
  A_CROSS_RB edge_A_CROSS_RB__A_CROSS_OK@-->|"exact"| A_CROSS_OK
  A_CROSS_OK edge_A_CROSS_OK__A_W_CROSSING@--> A_W_CROSSING
  A_CROSS_CLR edge_A_CROSS_CLR__A_CROSS_REV@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| A_CROSS_REV
  A_CROSS_OK edge_A_CROSS_OK__A_CROSS_REV@-->|"clearance unread after 60 s, or<br/>another aircraft on a runway, or<br/>tower lost — not yet on the<br/>runway"| A_CROSS_REV
  A_CROSS_REV edge_A_CROSS_REV__A_CROSS@-.->|"after no readback: RETRY the<br/>request — otherwise WAIT"| A_CROSS
  A_W_CROSSING edge_A_W_CROSSING__A_CROSS_EV@-->|"measured clear: no call needed"| A_CROSS_EV
  A_CROSS_EV edge_A_CROSS_EV__A_W_PARK@--> A_W_PARK
  A_W_CROSSING edge_A_W_CROSSING__A_CROSS_VAC@--> A_CROSS_VAC
  A_CROSS_VAC edge_A_CROSS_VAC__A_CROSS_DONE@-->|"entered and now measured clear"| A_CROSS_DONE
  A_CROSS_DONE edge_A_CROSS_DONE__A_W_PARK@--> A_W_PARK
  A_CROSS_VAC edge_A_CROSS_VAC__A_VAC_NEG@-->|"still on a runway"| A_VAC_NEG
  A_W_PARK edge_A_W_PARK__A_CHECKOUT@--> A_CHECKOUT
  A_CHECKOUT edge_A_CHECKOUT__A_CHECKOUT_R@--> A_CHECKOUT_R
  A_CHECKOUT_R edge_A_CHECKOUT_R__A_END@--> A_END
  A_W_PARK edge_A_W_PARK__A_REDEP_TAXI@--> A_REDEP_TAXI
  A_REDEP_TAXI edge_A_REDEP_TAXI__A_TO_DEP@-->|"taxi-in phase, on the ground<br/>here: new departure with taxi<br/>clearance"| A_TO_DEP
  A_VAC_R edge_A_VAC_R__A_REDEP_START@--> A_REDEP_START
  A_W_PARK edge_A_W_PARK__A_REDEP_START@--> A_REDEP_START
  A_REDEP_START edge_A_REDEP_START__A_TO_DEP@-->|"vacated or taxi-in, on the<br/>ground here: new departure,<br/>startup approved"| A_TO_DEP
  A_ARR_OK edge_A_ARR_OK__A_ANY@--> A_ANY
  A_W_STANDBY edge_A_W_STANDBY__A_ANY@--> A_ANY
  A_FINAL_Q edge_A_FINAL_Q__A_ANY@--> A_ANY
  A_LAND_OK edge_A_LAND_OK__A_ANY@--> A_ANY
  A_LANDED_EV edge_A_LANDED_EV__A_ANY@--> A_ANY
  A_TAXIIN_OK edge_A_TAXIIN_OK__A_ANY@--> A_ANY
  A_ANY edge_A_ANY__A_SAYAGAIN@--> A_SAYAGAIN
  A_ANY edge_A_ANY__A_CANCEL@--> A_CANCEL
  A_ANY edge_A_ANY__A_EMER@--> A_EMER
  A_ANY edge_A_ANY__A_CANCEL_EMER@--> A_CANCEL_EMER
  A_START edge_A_START__A_EMER@--> A_EMER
  A_FROM_DEP edge_A_FROM_DEP__A_EMER@--> A_EMER
  A_START edge_A_START__A_SAYAGAIN@--> A_SAYAGAIN
  A_START edge_A_START__A_CANCEL@--> A_CANCEL
  A_SAYAGAIN edge_A_SAYAGAIN__A_SAYAGAIN_R@-->|"enrolled"| A_SAYAGAIN_R
  A_SAYAGAIN_R edge_A_SAYAGAIN_R__A_ANY@-.->|"RETURN to the current step"| A_ANY
  A_SAYAGAIN edge_A_SAYAGAIN__A_NOSESSION@-->|"no session"| A_NOSESSION
  A_NOSESSION edge_A_NOSESSION__A_START@-.->|"RETURN"| A_START
  A_RB_NONE edge_A_RB_NONE__A_SAYAGAIN@-.->|"check what is current"| A_SAYAGAIN
  A_CANCEL edge_A_CANCEL__A_CANCEL_R@-->|"not on a runway"| A_CANCEL_R
  A_CANCEL_R edge_A_CANCEL_R__A_END@--> A_END
  A_CANCEL edge_A_CANCEL__A_CANCEL_NONE@-->|"no session"| A_CANCEL_NONE
  A_CANCEL edge_A_CANCEL__A_CANCEL_OCC@-->|"touched down or on a runway"| A_CANCEL_OCC
  A_CANCEL_OCC edge_A_CANCEL_OCC__A_W_CXL_VAC@--> A_W_CXL_VAC
  A_W_CXL_VAC edge_A_W_CXL_VAC__A_CXL_VAC_EV@-->|"measured clear"| A_CXL_VAC_EV
  A_CXL_VAC_EV edge_A_CXL_VAC_EV__A_END@--> A_END
  A_W_CXL_VAC edge_A_W_CXL_VAC__A_CXL_VAC@--> A_CXL_VAC
  A_CXL_VAC edge_A_CXL_VAC__A_CXL_VAC_R@-->|"measured clear"| A_CXL_VAC_R
  A_CXL_VAC_R edge_A_CXL_VAC_R__A_END@--> A_END
  A_CXL_VAC edge_A_CXL_VAC__A_VAC_NEG@-->|"still on a runway"| A_VAC_NEG
  A_EMER edge_A_EMER__A_EMER_R@-->|"enrolled: first in the runway<br/>order"| A_EMER_R
  A_EMER_R edge_A_EMER_R__A_ANY@-.->|"RETURN to the current step"| A_ANY
  A_EMER edge_A_EMER__A_EMER_NEW_R@-->|"no session: priority arrival<br/>opened"| A_EMER_NEW_R
  A_EMER_NEW_R edge_A_EMER_NEW_R__A_ARR_RB@--> A_ARR_RB
  A_EMER_NEW_R edge_A_EMER_NEW_R__A_EMER_GROUND@-->|"sampled on the ground here,<br/>never airborne"| A_EMER_GROUND
  A_EMER_GROUND edge_A_EMER_GROUND__A_W_VACATE@--> A_W_VACATE
  A_CANCEL_EMER edge_A_CANCEL_EMER__A_CANCEL_EMER_R@--> A_CANCEL_EMER_R
  A_CANCEL_EMER_R edge_A_CANCEL_EMER_R__A_ANY@-.->|"RETURN, priority removed"| A_ANY
  A_ANY edge_A_ANY__A_L_SILENT@--> A_L_SILENT
  A_ANY edge_A_ANY__A_L_LOST@--> A_L_LOST
  A_ANY edge_A_ANY__A_L_BAD@--> A_L_BAD
  A_W_TOUCH edge_A_W_TOUCH__A_L_ELSE@-->|"lands at, or is sampled on the<br/>ground at, another airfield"| A_L_ELSE
  A_W_PARK edge_A_W_PARK__A_L_AIR@-->|"takes off again after vacating"| A_L_AIR
  A_L_SILENT edge_A_L_SILENT__A_START@-.->|"RETURN: start again with a valid<br/>call"| A_START
  A_L_ELSE edge_A_L_ELSE__A_START@-.->|"RETURN: start again with a valid<br/>call"| A_START
  A_L_AIR edge_A_L_AIR__A_START@-.->|"RETURN: start again with a valid<br/>call"| A_START
  A_L_LOST edge_A_L_LOST__A_START@-.->|"RETURN: start again with a valid<br/>call"| A_START
  A_L_BAD edge_A_L_BAD__A_START@-.->|"RETURN: start again with a valid<br/>call"| A_START

  classDef normalFlow stroke:#15803d,stroke-width:3px,stroke-dasharray:0
  classDef errorFlow stroke:#b91c1c,stroke-width:2.5px,stroke-dasharray:0
  classDef recoveryFlow stroke:#92400e,stroke-width:2px,stroke-dasharray:7 4
  classDef neutralFlow stroke:#64748b,stroke-width:1px,stroke-dasharray:0
  classDef pilotCall fill:#eff6ff,stroke:#1d4ed8,color:#172554
  classDef errorNode fill:#fef2f2,stroke:#b91c1c,color:#450a0a
  classDef recoveryNode fill:#fffbeb,stroke:#92400e,color:#451a03
  class edge_A_START__A_G_HUB,edge_A_START__A_INFO,edge_A_START__A_RCHECK,edge_A_INFO__A_INFO_R,edge_A_RCHECK__A_INFO_R,edge_A_INFO_R__A_START,edge_A_ARR_OK__A_ANY,edge_A_W_STANDBY__A_ANY,edge_A_FINAL_Q__A_ANY,edge_A_LAND_OK__A_ANY,edge_A_LANDED_EV__A_ANY,edge_A_TAXIIN_OK__A_ANY,edge_A_ANY__A_SAYAGAIN,edge_A_START__A_SAYAGAIN,edge_A_SAYAGAIN__A_SAYAGAIN_R,edge_A_SAYAGAIN_R__A_ANY,edge_A_NOSESSION__A_START neutralFlow
  class edge_A_G_HUB__A_G_ID,edge_A_G_HUB__A_G_WHICH,edge_A_G_HUB__A_G_ROLE,edge_A_G_HUB__A_G_COAL,edge_A_G_HUB__A_G_STALE,edge_A_G_HUB__A_G_BUSY,edge_A_G_HUB__A_G_NONE,edge_A_G_HUB__A_G_SILENT,edge_A_INBOUND__A_INB_GROUND,edge_A_INBOUND__A_NOT_AIR,edge_A_BRIEF__A_W_STANDBY,edge_A_ARR_RB__A_RB_BAD,edge_A_LAND_RB__A_RB_BAD,edge_A_CROSS_RB__A_RB_BAD,edge_A_TAXIIN_RB__A_RB_BAD,edge_A_LAND_RB__A_RB_NONE,edge_A_CROSS_RB__A_RB_NONE,edge_A_DOWNWIND__A_POS_NEG,edge_A_BASE__A_POS_NEG,edge_A_DOWNWIND__A_NOPATTERN,edge_A_BASE__A_NOPATTERN,edge_A_BASE__A_ORDER,edge_A_DOWNWIND__A_BRIEF_FIRST,edge_A_BASE__A_BRIEF_FIRST,edge_A_FINAL__A_BRIEF_FIRST,edge_A_FINAL__A_FINAL_NEG,edge_A_FINAL__A_FINAL_TWR,edge_A_LAND_CLR__A_X_GA,edge_A_LAND_OK__A_X_GA,edge_A_LAND_CLR__A_X_STALE,edge_A_LAND_OK__A_X_STALE,edge_A_CROSS_CLR__A_X_STALE,edge_A_ARR_OK__A_REBRIEF,edge_A_FINAL_Q__A_REBRIEF,edge_A_LAND_OK__A_REBRIEF,edge_A_GOAROUND__A_GA_NORWY,edge_A_GOAROUND__A_GA_UNABLE,edge_A_W_TOUCH__A_UNCLR_LAND,edge_A_FINAL_Q__A_W_TOUCH,edge_A_W_FINAL__A_W_TOUCH,edge_A_LANDED_NEG_CALL__A_LANDED_NEG,edge_A_VAC__A_VAC_NEG,edge_A_TAXIIN__A_TAXIIN_NEG,edge_A_CROSS__A_CROSS_NEG,edge_A_CROSS_CLR__A_CROSS_REV,edge_A_CROSS_OK__A_CROSS_REV,edge_A_CROSS_VAC__A_VAC_NEG,edge_A_SAYAGAIN__A_NOSESSION,edge_A_CXL_VAC__A_VAC_NEG,edge_A_ANY__A_L_SILENT,edge_A_ANY__A_L_LOST,edge_A_ANY__A_L_BAD,edge_A_W_TOUCH__A_L_ELSE,edge_A_W_PARK__A_L_AIR,edge_A_W_TOUCH__A_LANDED_NEG_CALL errorFlow
  class edge_A_G_ID__A_START,edge_A_G_WHICH__A_START,edge_A_G_ROLE__A_START,edge_A_G_COAL__A_START,edge_A_G_STALE__A_START,edge_A_G_BUSY__A_START,edge_A_G_NONE__A_START,edge_A_G_SILENT__A_START,edge_A_NOT_AIR__A_START,edge_A_RB_BAD__A_ARR_RB,edge_A_RB_BAD__A_LAND_RB,edge_A_RB_BAD__A_CROSS_RB,edge_A_RB_BAD__A_TAXIIN_RB,edge_A_POS_NEG__A_W_PATTERN,edge_A_ORDER__A_W_PATTERN,edge_A_BRIEF_FIRST__A_ARR_RB,edge_A_NOPATTERN__A_W_FINAL,edge_A_FINAL_NEG__A_W_FINAL,edge_A_FINAL_TWR__A_FINAL,edge_A_X_GA__A_W_PATTERN,edge_A_X_GA__A_W_FINAL,edge_A_X_STALE__A_SAYAGAIN,edge_A_W_PATTERN__A_GOAROUND,edge_A_W_FINAL__A_GOAROUND,edge_A_FINAL_Q__A_GOAROUND,edge_A_LAND_OK__A_GOAROUND,edge_A_GOAROUND__A_GA_R,edge_A_GA_R__A_W_PATTERN,edge_A_GA_R__A_W_FINAL,edge_A_GA_NORWY__A_W_STANDBY,edge_A_GOAROUND__A_GA_WAIT,edge_A_TNG_EV__A_W_PATTERN,edge_A_TNG_EV__A_W_FINAL,edge_A_UNCLR_LAND__A_W_ROLLOUT,edge_A_W_ROLLOUT__A_TNG_EV,edge_A_LANDED_NEG__A_W_TOUCH,edge_A_VAC_NEG__A_W_VACATE,edge_A_TAXIIN_NEG__A_W_VACATE,edge_A_CROSS_NEG__A_CROSS,edge_A_CROSS_Q__A_CROSS,edge_A_CROSS_REV__A_CROSS,edge_A_ANY__A_CANCEL,edge_A_ANY__A_EMER,edge_A_ANY__A_CANCEL_EMER,edge_A_START__A_EMER,edge_A_FROM_DEP__A_EMER,edge_A_START__A_CANCEL,edge_A_RB_NONE__A_SAYAGAIN,edge_A_CANCEL__A_CANCEL_R,edge_A_CANCEL_R__A_END,edge_A_CANCEL__A_CANCEL_NONE,edge_A_CANCEL__A_CANCEL_OCC,edge_A_CANCEL_OCC__A_W_CXL_VAC,edge_A_W_CXL_VAC__A_CXL_VAC_EV,edge_A_CXL_VAC_EV__A_END,edge_A_W_CXL_VAC__A_CXL_VAC,edge_A_CXL_VAC__A_CXL_VAC_R,edge_A_CXL_VAC_R__A_END,edge_A_EMER__A_EMER_R,edge_A_EMER_R__A_ANY,edge_A_EMER__A_EMER_NEW_R,edge_A_EMER_NEW_R__A_ARR_RB,edge_A_EMER_NEW_R__A_EMER_GROUND,edge_A_EMER_GROUND__A_W_VACATE,edge_A_CANCEL_EMER__A_CANCEL_EMER_R,edge_A_CANCEL_EMER_R__A_ANY,edge_A_L_SILENT__A_START,edge_A_L_ELSE__A_START,edge_A_L_AIR__A_START,edge_A_L_LOST__A_START,edge_A_L_BAD__A_START recoveryFlow
  class edge_A_START__A_INBOUND,edge_A_FROM_DEP__A_INBOUND,edge_A_INBOUND__A_BRIEF,edge_A_INBOUND__A_STANDBY,edge_A_STANDBY__A_W_STANDBY,edge_A_W_STANDBY__A_BRIEF,edge_A_BRIEF__A_ARR_RB,edge_A_ARR_RB__A_ARR_OK,edge_A_REBRIEF__A_ARR_RB,edge_A_ARR_OK__A_W_PATTERN,edge_A_W_PATTERN__A_DOWNWIND,edge_A_DOWNWIND__A_DOWNWIND_R,edge_A_DOWNWIND_R__A_W_BASE,edge_A_W_BASE__A_BASE,edge_A_BASE__A_BASE_R,edge_A_ARR_OK__A_W_FINAL,edge_A_BASE_R__A_W_FINAL,edge_A_DOWNWIND_R__A_W_FINAL,edge_A_W_PATTERN__A_W_FINAL,edge_A_W_FINAL__A_FINAL,edge_A_FINAL__A_FINAL_Q,edge_A_FINAL__A_LAND_CLR,edge_A_FINAL_Q__A_LAND_CLR,edge_A_LAND_CLR__A_LAND_RB,edge_A_LAND_RB__A_LAND_OK,edge_A_LAND_OK__A_W_TOUCH,edge_A_W_TOUCH__A_W_ROLLOUT,edge_A_W_ROLLOUT__A_LANDED_EV,edge_A_LANDED_EV__A_W_VACATE,edge_A_W_ROLLOUT__A_LANDED,edge_A_W_VACATE__A_LANDED,edge_A_LANDED__A_LANDED_R,edge_A_W_VACATE__A_VAC_EV,edge_A_W_VACATE__A_VAC,edge_A_VAC__A_VAC_R,edge_A_VAC_EV__A_TAXIIN,edge_A_VAC_EV__A_TAXI_VAC,edge_A_VAC_R__A_TAXIIN,edge_A_VAC_R__A_TAXI_VAC,edge_A_LANDED_EV__A_TAXIIN,edge_A_TAXIIN__A_TAXIIN_CLR,edge_A_TAXI_VAC__A_TAXIIN_CLR,edge_A_TAXIIN_CLR__A_TAXIIN_RB,edge_A_TAXIIN_RB__A_TAXIIN_OK,edge_A_TAXIIN_OK__A_W_PARK,edge_A_W_PARK__A_W_CROSSPT,edge_A_W_CROSSPT__A_CROSS,edge_A_CROSS__A_CROSS_Q,edge_A_CROSS__A_CROSS_CLR,edge_A_CROSS_Q__A_CROSS_CLR,edge_A_CROSS_CLR__A_CROSS_RB,edge_A_CROSS_RB__A_CROSS_OK,edge_A_CROSS_OK__A_W_CROSSING,edge_A_W_CROSSING__A_CROSS_EV,edge_A_CROSS_EV__A_W_PARK,edge_A_W_CROSSING__A_CROSS_VAC,edge_A_CROSS_VAC__A_CROSS_DONE,edge_A_CROSS_DONE__A_W_PARK,edge_A_W_PARK__A_CHECKOUT,edge_A_CHECKOUT__A_CHECKOUT_R,edge_A_CHECKOUT_R__A_END,edge_A_W_PARK__A_REDEP_TAXI,edge_A_REDEP_TAXI__A_TO_DEP,edge_A_VAC_R__A_REDEP_START,edge_A_W_PARK__A_REDEP_START,edge_A_REDEP_START__A_TO_DEP normalFlow
  class A_INFO,A_RCHECK,A_INBOUND,A_ARR_RB,A_LAND_RB,A_CROSS_RB,A_TAXIIN_RB,A_DOWNWIND,A_BASE,A_FINAL,A_SAYAGAIN,A_GOAROUND,A_LANDED,A_LANDED_NEG_CALL,A_VAC,A_TAXIIN,A_TAXI_VAC,A_CROSS,A_CROSS_VAC,A_CHECKOUT,A_REDEP_TAXI,A_REDEP_START,A_CANCEL,A_CXL_VAC,A_EMER,A_CANCEL_EMER pilotCall
  class A_G_ID,A_G_WHICH,A_G_ROLE,A_G_COAL,A_G_STALE,A_G_BUSY,A_G_NONE,A_G_SILENT,A_INB_GROUND,A_NOT_AIR,A_RB_BAD,A_RB_NONE,A_POS_NEG,A_NOPATTERN,A_ORDER,A_BRIEF_FIRST,A_FINAL_NEG,A_FINAL_TWR,A_X_GA,A_X_STALE,A_REBRIEF,A_GA_NORWY,A_GA_UNABLE,A_UNCLR_LAND,A_LANDED_NEG,A_VAC_NEG,A_TAXIIN_NEG,A_CROSS_NEG,A_CROSS_REV,A_NOSESSION,A_L_SILENT,A_L_LOST,A_L_BAD,A_L_ELSE,A_L_AIR errorNode
  class A_GOAROUND,A_GA_R,A_GA_WAIT,A_CANCEL,A_CANCEL_R,A_CANCEL_NONE,A_CANCEL_OCC,A_W_CXL_VAC,A_CXL_VAC_EV,A_CXL_VAC,A_CXL_VAC_R,A_EMER,A_EMER_R,A_EMER_NEW_R,A_CANCEL_EMER,A_CANCEL_EMER_R,A_EMER_GROUND recoveryNode
```

## Readback dictionary

`readback_problem` (`airfield.py` 477-518) checks the text after the word `readback`. Before it runs, `_readback` (1709-1724) requires a pending clearance (`unable, no clearance to read back`). The checks apply in this order:

1. Any negation word (`no, not, negative, dont, do not, unable, never, cannot, cant`) or `cancel/abort/belay` voids the readback. The reply is `negative readback, negative readback, read back the clearance again, read back again`.
2. Every `runway <number> [side]` must equal the cleared runway (otherwise `[READBACK_PROBLEM]` = `runway <cleared>`). A runway clearance needs at least one. A parking readback must contain none (`no runway in that clearance`).
3. The required term must be present and no conflicting term may appear (otherwise `[READBACK_PROBLEM]` is the term label).
4. For the briefing, every number after `altimeter`/`qnh` must equal the briefed altimeter (otherwise `altimeter <briefed>`).

| Clearance | ATC issues | YOU read back (canonical) | Required | Rejected if present | After `readback correct` | Lines |
|---|---|---|---|---|---|---|
| Taxi out (`taxi`) | `taxi to holding point runway [RUNWAY] …, hold short of all runways, altimeter …, read back` | `readback taxi to holding point runway [RUNWAY], hold short of all runways` | cleared runway, `hold short` | other runway | `taxi to holding point runway [RUNWAY], hold short of all runways, report holding short` | 466, 1148-1162 |
| Taxi to parking (`taxi_in`) | `taxi to parking …, hold short of all runways, read back` | `readback taxi to parking, hold short of all runways` | `hold short` | any `runway <n>` | `taxi to parking, hold short of all runways, report parked` | 467, 1622-1634 |
| Line-up (`lineup`) | `runway [RUNWAY], line up and wait, read back` | `readback runway [RUNWAY], line up and wait` | cleared runway, `line up … wait` | `takeoff`, `land`, `cleared` | `line up and wait runway [RUNWAY], report lined up` (or `hold position …` if already lined up) | 468-469, 1069-1071 |
| Take-off (`takeoff`) | `runway [RUNWAY], [WIND], cleared for takeoff, read back` | `readback runway [RUNWAY], cleared for takeoff` | cleared runway, `cleared (for) takeoff` | `land`/`landing` | nothing more | 470, 1057-1058 |
| Crossing (`crossing`) | `cross runway [CROSS_RUNWAY], report vacated, read back` | `readback cross runway [CROSS_RUNWAY]` | crossing runway, `cross` | `takeoff`, `land`, `line…` | `cross runway [CROSS_RUNWAY], report vacated` | 472, 1072-1074 |
| Landing (`landing`) | `runway [RUNWAY], [WIND], cleared to land, read back` | `readback runway [RUNWAY], cleared to land` | cleared runway, `cleared (to) land` | `takeoff` | nothing more | 471, 1066-1068 |
| Arrival briefing (`arrival`) | `runway [RUNWAY] in use, …, altimeter [ALTIMETER], …, read back runway and altimeter` | `readback runway [RUNWAY], altimeter [ALTIMETER]` | cleared runway, exact altimeter (`2992` digits or `29.92`) | `cleared` (`runway and altimeter only`) | `report [PATTERN] downwind runway [RUNWAY]` or `report final runway [RUNWAY]` | 473, 1129-1146 |

The parser only captures the text after the first `readback` / `read back` / `reading back` (`airfield_parser.py` 38, 218-221). For a readback, only words *before* the keyword can turn it into `cancel` or void it. Words after it are checked by the validator above.

## Say-again / current-instruction table

Any status reply (`say again`, a repeated request, an `unable, …` prefix) is rebuilt by `_status` (`airfield.py` 1645-1705) from the present phase. It never replays a lapsed or revoked clearance.

| State | `[CURRENT_INSTRUCTION]` |
|---|---|
| Runway clearance lapsed, or tower lost, on its phase | `contact [FIELD] tower [TOWER_FREQ]` / `unable, no tower service at [FIELD], runway clearances not available` / `previous runway clearance no longer current, report intentions` |
| Clearance awaiting readback | the clearance text again, ending `read back` |
| Vacating, cancelled, landed | `vacate the runway, report vacated` |
| startup_requested | `standby, not yet observed on the ground here` |
| startup | `startup approved, altimeter [ALTIMETER], report ready to taxi` |
| taxi | `taxi to holding point runway [RUNWAY], hold short of all runways, report holding short` |
| crossing_requested | `hold short runway [CROSS_RUNWAY], number [N] for crossing` (or `…, request crossing again when ready` after a lapse) |
| crossing | `cross runway [CROSS_RUNWAY], report vacated` |
| holding_short | `hold short runway [RUNWAY], number [N] for departure` (or `…, report ready for departure when ready` after a lapse) |
| lineup | `line up and wait runway [RUNWAY], report lined up` |
| lined_up | `hold position runway [RUNWAY], stand by for takeoff clearance` (or `…, report ready for takeoff when ready` after a lapse) |
| cleared_takeoff | `runway [RUNWAY], cleared for takeoff` |
| airborne | `airborne[, contact [FIELD] approach [APPROACH_FREQ]], report leaving the zone` |
| inbound_standby | `[RUNWAY_REASON], remain clear of the airfield, stand by` |
| inbound | `report [PATTERN] downwind runway [RUNWAY]` / `report final runway [RUNWAY]` |
| downwind | `report base` |
| base | `report final runway [RUNWAY]` |
| final | `number [N], continue approach runway [RUNWAY]` (or the tower note) |
| cleared_land | `runway [RUNWAY], cleared to land` |
| touchdown | `stand by for landing confirmation` |
| vacated | `request taxi to parking` |
| taxi_in_clearance / taxi_in | the pending clearance, then `taxi to parking, hold short of all runways, report parked` |

## Calls that are refusals in the wrong context

These intents are accepted by the grammar everywhere, but outside their chart position they only produce a status or a refusal. They are not authorisations (`airfield.py` 1726-1870):

| Call | Wrong context reply |
|---|---|
| `holding short` / `ready for departure` / `lined up` during an arrival | `unable, no departure in progress` |
| `downwind` / `base` / `final` during a departure | `unable, no arrival in progress` |
| `switching` before the observed takeoff, or during an arrival | `unable, [CURRENT_INSTRUCTION]` |
| `airborne` before an observed departure takeoff | `negative, airborne not observed` |
| `landed` before an observed touchdown | `negative, touchdown not observed` |
| `going around` with no airborne approach | `unable, no approach in progress` |
| `request crossing …` outside taxi, holding short, crossing request or taxi-in | `unable, [CURRENT_INSTRUCTION]` |
| `request taxi to parking` in a line-up/takeoff/airborne/arrival-airborne phase | `unable, [CURRENT_INSTRUCTION]` |
| Any procedure call with no session (except startup, taxi, inbound, emergency, information, cancel, checking out, cancel emergency) | `unable, no active session, state your request` |

## Intent coverage (22 parser intents)

| Intent (`airfield_parser.py` 27-61) | Canonical phrase | Departure nodes | Arrival nodes |
|---|---|---|---|
| cancelEmergency | `cancel emergency` | D_CANCEL_EMER | A_CANCEL_EMER |
| emergency | `mayday mayday mayday, low fuel` / `mayday, aborting takeoff` | D_EMER, D_ABORT | A_EMER |
| checkOut | `checking out` | D_CHECKOUT | A_CHECKOUT |
| cancel | `cancel` | D_CANCEL | A_CANCEL |
| goAround | `going around` | — | A_GOAROUND |
| readback | `readback …` | D_TAXI_RB, D_CROSS_RB, D_LU_RB, D_TO_RB, D_TAXIIN_RB | A_ARR_RB, A_LAND_RB, A_CROSS_RB, A_TAXIIN_RB |
| sayAgain | `say again` | D_SAYAGAIN | A_SAYAGAIN |
| information | `request information` / `radio check` | D_INFO, D_RCHECK | A_INFO, A_RCHECK |
| taxiIn | `request taxi to parking` | D_TAXIIN | A_TAXIIN |
| startup | `request startup` | D_STARTUP | A_REDEP_START |
| taxi | `request taxi` | D_TAXI | A_TAXI_VAC, A_REDEP_TAXI |
| crossing | `request crossing runway [CROSS_RUNWAY]` | D_CROSS | A_CROSS |
| vacated | `vacated` / `vacated runway …` | D_CROSS_VAC, D_VAC | A_VAC, A_CROSS_VAC, A_CXL_VAC |
| holdingShort | `holding short runway …` / `ready for departure …` / `ready for takeoff …` | D_HOLD, D_READY_DEP, D_READY_TO | — |
| linedUp | `lined up runway …` | D_LINEDUP | — |
| airborne | `airborne` | D_AIRBORNE | — |
| handoff | `switching` | D_HANDOFF | — |
| inbound | `inbound for landing` | D_RETURN | A_INBOUND |
| downwind | `downwind runway …` | — | A_DOWNWIND |
| base | `base runway …` | — | A_BASE |
| final | `final runway …` | — | A_FINAL |
| landed | `landed` | — | A_LANDED, A_LANDED_NEG_CALL |

## Source cross-reference

| Topic | Source |
|---|---|
| Grammar, earliest-match rule, negation/cancel scope | `overlord/airfield_parser.py` 1-17, 27-66, 163-230 |
| Receiver, identity, role, link guards; channel move; facts validation | `overlord/airfield.py` 1373-1509 |
| Intent dispatch | 1528-1564 |
| Emergency, departure, arrival enrolment, taxi-in | 1566-1634 |
| Status rendering, tower note | 1636-1705 |
| Procedure handlers (readback, holding short, lined up, crossing, vacated, taxiIn, airborne, handoff, pattern, go-around, landed) | 1709-1870 |
| Readback validator and terms | 465-518 |
| Scheduler: runway selection, revocations, lapse, one-lane grants | 954-1127 |
| Briefing, taxi clearance, wind, information | 1129-1179 |
| Telemetry observation, touchdown, touch-and-go, takeoff, airborne text, re-entry | 796-938 |
| Stale link, epoch, snapshot application, unpublished channels, release | 588-630, 701-776, 1306-1317 |
| Geometry thresholds (hold, crossing, lined up, final, pattern, rolling) | 87-105, 323-409 |
| Identity correlation | `overlord/identities.py` 7-53 |
| Native routing and cross-role release | `overlord/brain.py` 162-181 |
| Behaviour tests | `tests/test_airfield.py` 106-807 |
