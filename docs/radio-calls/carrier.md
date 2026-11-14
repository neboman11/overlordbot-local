# Carrier radio calls — exact operator phrases

Pilot-side phrase guide for OverlordBot's native carrier controller (`overlord/carrier.py`, grammar `overlord/carrier_parser.py`), traced from source at `63ba92534ddc60fa3268fbe2549eb1e455ad7748` (app v0.1.0). It documents what this application accepts and answers. It is not real-world carrier procedure: there is no LSO grading, no Case I break-interval control and no autonomous ATC command beyond the replies shown here.

There are two complete charts. Each one keeps normal, alternative, retry, refusal, revocation and lifecycle paths together:

1. **Carrier launch**: startup, launch queue, release, launch, departure complete, and the return to recovery.
2. **Carrier recovery**: check-in, CASE I / II / III marshal, readback, holding, Charlie or commence, pattern or platform, ball, clara, wave-off, bolter, foul deck, trap, deck clear, and the handover to a new launch.

## How to read the charts

**Path colors:** green solid = normal progression; red = refusal/error; amber dashed = retry, recovery, emergency or cancellation; gray = optional status/shared checks. Calls and conditions remain explicit: color is not the only cue.

| Node prefix | Meaning |
|---|---|
| `YOU:` | Say the full quoted call. Replace each `[PLACEHOLDER]` with the current value and do not say the brackets. |
| `REPLY role:` | The controller's answer to your call. It is spoken as `[caller], [Carrier] [role], [message]`, for example `viper 1 1, Roosevelt marshal, readback correct, report established`. |
| `EVENT role:` | An unsolicited transmission the controller makes when mission state changes (assignment, Charlie, bolter, trap, launch, closure). |
| `WAIT` / `FLY` | A flight action or mission-observed condition. **No radio call.** Saying "airborne", "trapped" or "bolter" never substitutes for the mission telemetry. |
| `CHECK:` | A controller guard decision. |
| `RETRY` / `RETURN` / `GO TO` | Repeat the corrected call, rejoin the flow at a named node, or continue in the other chart. |

### Call grammar (applies to every `YOU` call)

1. **Receiver first.** Open with a word of the ship on its own mission frequency: a role (`marshal`, `tower`, `paddles`, `departure`, `approach`, `boss`, `strike`, `lso`), `mother`, `carrier`, or a ship-name word of four or more letters (`Roosevelt` for "CVN-71 Theodore Roosevelt"). Up to three receiver words are read, so `[CARRIER] marshal` works. The role word does not change how the call is handled: the controller chooses the replying role from your current state. The charts use the role that matches each phase.
2. **Your identity.** Then give your callsign (`Viper one one`) or your digits-only modex (`one zero one`, `201`; leading zeros count, so `zero one zero` is not `one zero`). The caller must resolve to exactly one catalogued **human** aircraft of the radio's coalition. If an SRS radio name is supplied, it must positively and uniquely identify the same pilot.
3. **One command.** Then one command phrase. The **earliest** command phrase in the transmission wins, so put the command first and do not chain two commands.
4. **No negation in positive calls.** In a call that advances procedure, any `no`, `not`, `negative`, `dont`, `do not`, `unable` or `never` voids the call ("negative abeam" gets `say again`). Any `cancel`, `abort` or `belay` anywhere turns it into **cancel**, and any `wave off` anywhere turns it into **wave-off** ("ball, wave off" is a wave-off). Check out, cancel, wave off, say again and clara are exempt from this rewrite. Readback and emergency only check the words up to their keyword.
5. **Placeholders:** `[CARRIER]` is the ship's spoken name word, `[OTHER_CARRIER]` is another mission carrier, `[CALLSIGN]` is your callsign or modex, and `[ANGELS]`, `[RADIAL]`, `[DME]`, `[EAT_MINUTE]` and `[ALTIMETER]` are the values the controller gave you. `[FUEL_STATE]` is optional information after `ball`.

### Readback formats

Always say the explicit word **readback**. "Roger" or "copy" is not a readback. The validator removes the labels (`radial`, `DME`, `angels`, `expected`, `approach`, `time`, `altimeter`, …) and filler words, then requires exactly these numbers, **in this order, with nothing extra**:

| Assignment | Numbers required, in order | Template |
|---|---|---|
| CASE I | angels, altimeter | `[CARRIER] marshal, [CALLSIGN], readback angels [ANGELS], altimeter [ALTIMETER]` |
| CASE II / III | radial, DME, angels, approach-time **minute**, altimeter | `[CARRIER] marshal, [CALLSIGN], readback [RADIAL] radial, [DME] DME, angels [ANGELS], approach time [EAT_MINUTE], altimeter [ALTIMETER]` |

- Do **not** read back the BRC or the final bearing. An extra number fails the readback (`negative readback, altimeter …`).
- The approach time is the **minute only** (`zero five`), not hours and minutes.
- The altimeter may be spoken as digits (`two niner niner two`) or as a decimal (`two nine point nine two`). `two nine point nine` fails.
- Numbers must be complete: `two one zero DME` is not `two one DME`.
- On a mismatch the controller names the **first** wrong field and its correct value, for example `negative readback, angels two, read back again`. Read back the whole set again.

| Spoken by marshal | Your readback |
|---|---|
| `CASE one recovery, BRC three five three, hold overhead at angels two, altimeter two nine nine two, read back angels and altimeter` | `Roosevelt marshal, Viper one one, readback angels two, altimeter two niner niner two` |
| `CASE three recovery, final bearing three four four, marshal on the one six four radial, two one DME, angels six, expected approach time zero five, altimeter two nine nine two, read back radial, DME, angels, approach time and altimeter` | `Roosevelt marshal, Viper one one, readback one six four radial, two one DME, angels six, approach time zero five, altimeter two niner niner two` |

The sample values come from the test fixture: CVN-71 at 43°N 41°E, true heading 000, deck angle 9°, 29.92 inHg, CASE III at 12:00 mission time. BRC and bearings are magnetic.

### Separation rules you will see

- **One shared deck/approach lane per ship.** A recovering aircraft holds it from its Charlie or commence until it says **deck clear** (or its session closes). A departing aircraft holds it from launch clearance until its launch is observed. Emergencies are served first, then enrolment order.
- CASE I holds overhead at angels 2–9 (within 10 nm, ±1000 ft). CASE II / III marshal on the final-bearing reciprocal radial at angels 6–13, DME = angels + 15 (within 6 nm of the fix, ±1000 ft). Approach times start at the next whole minute at least five minutes ahead and are one minute apart. CASE I aircraft and priority aircraft may commence before their approach time; others wait for it.
- A touchdown is not a trap. Only the mission's debounced on-deck state confirms a trap. Leaving the deck after a touchdown is a bolter. While an enrolled aircraft is touched down or on deck, the deck is foul, and anyone else at the ball is waved off.

## Chart 1 — Carrier launch (complete flow)

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
  %% Entry, lifecycle and session cleanup
    CL_W_SPAWN["WAIT: occupy a human aircraft on the [CARRIER] deck; tune the ship's mission radio frequency"]
    CL_W_HANDOVER["FROM RECOVERY CHART: trap confirmed on [CARRIER], still on deck"]
    CL_W_CLOSED["WAIT—NO CALL: dead, eject, disconnect or birth event; catalog loss; new incarnation or coalition; carrier dead, disabled or retuned; new mission epoch. Session closes silently"]
    CL_E_LOST["EVENT marshal: radar contact lost, carrier session closed, check in again"]
    CL_E_BADTRACK["EVENT marshal: tracking data invalid, carrier session closed, check in again"]
    CL_W_XFER["WAIT—NO CALL: a new airfield ATC enrolment of this aircraft releases the carrier session silently"]
    CL_RESTART["RETURN: make a new launch request; a respawned seat is a new occupant"]

  %% Every call: address, identity and link guards
    CL_C_GUARD{"CHECK every call: carrier_id, channel, coalition, receiver word, caller identity, SRS name, live link, owning carrier"}
    CL_N_SILENT["NO REPLY: wrong frequency, coalition or carrier_id, no unique enabled carrier on the channel, or call not opened with a receiver word of this carrier"]
    CL_R_NOID["REPLY marshal: i could not recognize your callsign or modex"]
    CL_R_UNKNOWN["REPLY marshal: unable, no player aircraft with that identity"]
    CL_R_AMBIG["REPLY marshal: more than one aircraft matches, say modex"]
    CL_R_SRS["REPLY marshal: unable, your radio name does not identify you"]
    CL_R_STALE["REPLY marshal: unable, no live carrier link"]
    CL_R_OTHER["REPLY marshal: unable, you are checked in with [OTHER_CARRIER]"]
    CL_R_SAYAGAIN["REPLY marshal: say again"]
    CL_R_NOSESSION["REPLY marshal: unable, check in first"]
    CL_Y_XCHECKOUT["YOU: [OTHER_CARRIER] departure, [CALLSIGN], checking out"]
    CL_R_XCHECKOUT["REPLY marshal on [OTHER_CARRIER]: copy, checked out"]
    CL_RETRY["RETRY: correct receiver, callsign or modex, SRS name, frequency or wording, then repeat the same call"]

  %% Startup and launch request
    CL_Y_STARTUP["YOU: [CARRIER] departure, [CALLSIGN], request startup"]
    CL_R_STARTUP["REPLY departure: copy, report ready for launch"]
    CL_Y_READY["YOU: [CARRIER] departure, [CALLSIGN], ready for launch"]
    CL_Y_REQDEP["YOU: [CARRIER] departure, [CALLSIGN], request departure"]
    CL_R_QUEUED["REPLY departure: CASE [CASE] departure, number [N] for launch, hold"]
    CL_R_QUEUED_MISSING["REPLY departure: departure, [MISSING_FACT], number [N] for launch, hold"]
    CL_R_LIMIT["REPLY departure: unable, carrier traffic limit reached"]
    CL_R_INAIR["REPLY marshal: unable, departure requires the aircraft on deck"]

  %% Queue, shared lane and release
    CL_W_QUEUE["WAIT on deck: release needs ready for launch, deck seen (on deck, not in air), CASE, altimeter and BRC known, live link and a free shared lane; emergency first, then request order"]
    CL_W_LANE["WAIT—NO CALL: lane held by a recovering aircraft (Charlie or commence until deck clear) or by an earlier departure until its launch is observed"]
    CL_W_STALE["WAIT—NO CALL: bridge stale; a launch clearance reverts to the queue, the lane is freed, earlier replies are void"]
    CL_E_CLEARED["EVENT departure: cleared for launch, CASE [CASE] departure, BRC [BRC], altimeter [ALTIMETER], report airborne"]
    CL_Y_SAYAGAIN["YOU: [CARRIER] departure, [CALLSIGN], say again"]
    CL_Y_EMERG["YOU: [CARRIER] departure, [CALLSIGN], emergency"]
    CL_R_EMERG["REPLY marshal: copy emergency, you have priority, [CURRENT_STATUS]"]
    CL_Y_CANCEL["YOU: [CARRIER] departure, [CALLSIGN], cancel departure"]
    CL_R_CANCEL["REPLY departure: copy, departure cancelled, checked out"]
    CL_Y_CHECKOUT["YOU: [CARRIER] departure, [CALLSIGN], checking out"]
    CL_R_CHECKOUT["REPLY marshal: copy, checked out"]
    CL_R_NOACTIVE["REPLY marshal: copy, no active carrier session"]

  %% Launch, airborne and departure complete
    CL_F_LAUNCH["FLY: catapult launch. Mission takeoff event, or in air after the deck was seen"]
    CL_E_AIRBORNE["EVENT departure: airborne, CASE [CASE] departure, report departure complete"]
    CL_E_UNCLEARED["EVENT departure: launch not cleared, airborne observed, report departure complete"]
    CL_E_NOTDECK["EVENT departure: unable departure, aircraft not on deck"]
    CL_E_FOREIGN["EVENT departure: launch not observed from this deck, departure closed"]
    CL_Y_AIRBORNE["YOU: [CARRIER] departure, [CALLSIGN], airborne"]
    CL_R_AIRB_EARLY["REPLY departure: roger, stand by for launch confirmation"]
    CL_R_AIRB_STATUS["REPLY departure: airborne, CASE [CASE] departure, report departure complete"]
    CL_Y_HANDOFF["YOU: [CARRIER] departure, [CALLSIGN], departure complete"]
    CL_R_HANDOFF["REPLY departure: departure complete, frequency change approved"]
    CL_R_HANDOFF_EARLY["REPLY departure: unable, launch not confirmed"]
    CL_F_10NM["FLY: reach 10 nm from the ship without calling"]
    CL_E_AUTOHANDOFF["EVENT departure: departure complete, frequency change approved"]
    CL_W_LANDED["WAIT—NO CALL: a landing observed after launch closes the departure silently"]
    CL_DONE["DONE: departure session closed; change frequency"]
    CL_Y_RETURN["YOU: [CARRIER] marshal, [CALLSIGN], checking in"]
    CL_E_NOTAIR["EVENT marshal: you are not airborne, recovery not required, check in when airborne"]
    CL_GO_RECOVERY["GO TO: Carrier recovery chart, check-in reply"]





  classDef you fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
  classDef reply fill:#f1f8e9,stroke:#558b2f
  classDef wait fill:#fff8e1,stroke:#f9a825,stroke-dasharray:4 3
  classDef refuse fill:#ffebee,stroke:#c62828
  class CL_Y_STARTUP,CL_Y_READY,CL_Y_REQDEP,CL_Y_SAYAGAIN,CL_Y_EMERG,CL_Y_CANCEL,CL_Y_CHECKOUT,CL_Y_XCHECKOUT,CL_Y_AIRBORNE,CL_Y_HANDOFF,CL_Y_RETURN you
  class CL_R_STARTUP,CL_R_QUEUED,CL_R_QUEUED_MISSING,CL_E_CLEARED,CL_R_EMERG,CL_R_CANCEL,CL_R_CHECKOUT,CL_R_NOACTIVE,CL_R_XCHECKOUT,CL_E_AIRBORNE,CL_R_AIRB_STATUS,CL_R_HANDOFF,CL_E_AUTOHANDOFF reply
  class CL_W_SPAWN,CL_W_HANDOVER,CL_W_CLOSED,CL_W_XFER,CL_W_QUEUE,CL_W_LANE,CL_W_STALE,CL_F_LAUNCH,CL_F_10NM,CL_W_LANDED,CL_DONE,CL_RESTART,CL_RETRY,CL_GO_RECOVERY wait
  class CL_N_SILENT,CL_R_NOID,CL_R_UNKNOWN,CL_R_AMBIG,CL_R_SRS,CL_R_STALE,CL_R_OTHER,CL_R_SAYAGAIN,CL_R_NOSESSION,CL_R_LIMIT,CL_R_INAIR,CL_E_UNCLEARED,CL_E_NOTDECK,CL_E_FOREIGN,CL_R_AIRB_EARLY,CL_R_HANDOFF_EARLY,CL_E_LOST,CL_E_BADTRACK,CL_E_NOTAIR refuse

  %% Stable edge IDs keep path colors attached to their transitions.
  CL_W_SPAWN edge_CL_W_SPAWN__CL_C_GUARD@--> CL_C_GUARD
  CL_W_HANDOVER edge_CL_W_HANDOVER__CL_Y_REQDEP@-->|"request from on deck: deck<br/>already proven"| CL_Y_REQDEP
  CL_C_GUARD edge_CL_C_GUARD__CL_N_SILENT@-->|"not addressed to this carrier<br/>radio"| CL_N_SILENT
  CL_C_GUARD edge_CL_C_GUARD__CL_R_NOID@-->|"callsign or modex not recognized"| CL_R_NOID
  CL_C_GUARD edge_CL_C_GUARD__CL_R_SAYAGAIN@-->|"no command and caller not<br/>recognized, or addressed with no<br/>command or a negated call"| CL_R_SAYAGAIN
  CL_C_GUARD edge_CL_C_GUARD__CL_R_UNKNOWN@-->|"no human aircraft matches"| CL_R_UNKNOWN
  CL_C_GUARD edge_CL_C_GUARD__CL_R_AMBIG@-->|"callsign matches two aircraft<br/>(modex heard: say callsign)"| CL_R_AMBIG
  CL_C_GUARD edge_CL_C_GUARD__CL_R_SRS@-->|"SRS name does not uniquely name<br/>you"| CL_R_SRS
  CL_C_GUARD edge_CL_C_GUARD__CL_R_STALE@-->|"bridge stale; only cancel and<br/>check out still act"| CL_R_STALE
  CL_C_GUARD edge_CL_C_GUARD__CL_R_OTHER@-->|"session on another ship;<br/>anything but request, check in<br/>or emergency"| CL_R_OTHER
  CL_C_GUARD edge_CL_C_GUARD__CL_R_NOSESSION@-->|"airborne or status call with no<br/>session"| CL_R_NOSESSION
  CL_C_GUARD edge_CL_C_GUARD__CL_Y_STARTUP@-->|"optional startup"| CL_Y_STARTUP
  CL_C_GUARD edge_CL_C_GUARD__CL_Y_REQDEP@-->|"ready now"| CL_Y_REQDEP
  CL_N_SILENT edge_CL_N_SILENT__CL_RETRY@--> CL_RETRY
  CL_R_NOID edge_CL_R_NOID__CL_RETRY@--> CL_RETRY
  CL_R_SAYAGAIN edge_CL_R_SAYAGAIN__CL_RETRY@--> CL_RETRY
  CL_R_UNKNOWN edge_CL_R_UNKNOWN__CL_RETRY@--> CL_RETRY
  CL_R_AMBIG edge_CL_R_AMBIG__CL_RETRY@-->|"repeat with the other identity<br/>form"| CL_RETRY
  CL_R_SRS edge_CL_R_SRS__CL_RETRY@--> CL_RETRY
  CL_R_STALE edge_CL_R_STALE__CL_RETRY@-->|"wait for the link"| CL_RETRY
  CL_R_NOSESSION edge_CL_R_NOSESSION__CL_Y_REQDEP@--> CL_Y_REQDEP
  CL_R_OTHER edge_CL_R_OTHER__CL_Y_XCHECKOUT@-->|"leave the other ship on its<br/>frequency"| CL_Y_XCHECKOUT
  CL_R_OTHER edge_CL_R_OTHER__CL_Y_REQDEP@-->|"or transfer: a request here<br/>closes the other ship's session"| CL_Y_REQDEP
  CL_Y_XCHECKOUT edge_CL_Y_XCHECKOUT__CL_R_XCHECKOUT@--> CL_R_XCHECKOUT
  CL_R_XCHECKOUT edge_CL_R_XCHECKOUT__CL_Y_REQDEP@--> CL_Y_REQDEP
  CL_RETRY edge_CL_RETRY__CL_C_GUARD@--> CL_C_GUARD
  CL_Y_STARTUP edge_CL_Y_STARTUP__CL_R_STARTUP@-->|"new or replacing session"| CL_R_STARTUP
  CL_Y_STARTUP edge_CL_Y_STARTUP__CL_R_LIMIT@-->|"64 sessions already"| CL_R_LIMIT
  CL_Y_STARTUP edge_CL_Y_STARTUP__CL_R_INAIR@-->|"existing recovery session<br/>tracked in the air"| CL_R_INAIR
  CL_R_STARTUP edge_CL_R_STARTUP__CL_Y_READY@--> CL_Y_READY
  CL_Y_READY edge_CL_Y_READY__CL_R_QUEUED@-->|"from startup or new request"| CL_R_QUEUED
  CL_Y_REQDEP edge_CL_Y_REQDEP__CL_R_QUEUED@--> CL_R_QUEUED
  CL_Y_REQDEP edge_CL_Y_REQDEP__CL_R_QUEUED_MISSING@-->|"CASE, altimeter or heading<br/>unknown"| CL_R_QUEUED_MISSING
  CL_Y_READY edge_CL_Y_READY__CL_R_QUEUED_MISSING@-->|"CASE, altimeter or heading<br/>unknown"| CL_R_QUEUED_MISSING
  CL_Y_REQDEP edge_CL_Y_REQDEP__CL_R_LIMIT@-->|"64 sessions already"| CL_R_LIMIT
  CL_Y_REQDEP edge_CL_Y_REQDEP__CL_R_INAIR@-->|"existing recovery session<br/>tracked in the air"| CL_R_INAIR
  CL_R_LIMIT edge_CL_R_LIMIT__CL_RETRY@-->|"try later"| CL_RETRY
  CL_R_INAIR edge_CL_R_INAIR__CL_GO_RECOVERY@-->|"land first, or stay in recovery"| CL_GO_RECOVERY
  CL_R_QUEUED edge_CL_R_QUEUED__CL_W_QUEUE@--> CL_W_QUEUE
  CL_R_QUEUED_MISSING edge_CL_R_QUEUED_MISSING__CL_W_QUEUE@-->|"facts arrive"| CL_W_QUEUE
  CL_W_QUEUE edge_CL_W_QUEUE__CL_W_LANE@-->|"lane busy"| CL_W_LANE
  CL_W_LANE edge_CL_W_LANE__CL_W_QUEUE@-->|"lane released"| CL_W_QUEUE
  CL_W_QUEUE edge_CL_W_QUEUE__CL_E_CLEARED@-->|"all release conditions met"| CL_E_CLEARED
  CL_E_CLEARED edge_CL_E_CLEARED__CL_W_STALE@-->|"link goes stale before launch"| CL_W_STALE
  CL_W_STALE edge_CL_W_STALE__CL_W_QUEUE@-->|"link returns"| CL_W_QUEUE
  CL_W_QUEUE edge_CL_W_QUEUE__CL_Y_SAYAGAIN@--> CL_Y_SAYAGAIN
  CL_Y_SAYAGAIN edge_CL_Y_SAYAGAIN__CL_R_STARTUP@-->|"startup"| CL_R_STARTUP
  CL_Y_SAYAGAIN edge_CL_Y_SAYAGAIN__CL_R_QUEUED@-->|"queued"| CL_R_QUEUED
  CL_Y_SAYAGAIN edge_CL_Y_SAYAGAIN__CL_E_CLEARED@-->|"released"| CL_E_CLEARED
  CL_Y_SAYAGAIN edge_CL_Y_SAYAGAIN__CL_R_AIRB_STATUS@-->|"launched"| CL_R_AIRB_STATUS
  CL_W_QUEUE edge_CL_W_QUEUE__CL_Y_EMERG@--> CL_Y_EMERG
  CL_Y_EMERG edge_CL_Y_EMERG__CL_R_EMERG@-->|"departure session: priority in<br/>launch order"| CL_R_EMERG
  CL_R_EMERG edge_CL_R_EMERG__CL_W_QUEUE@--> CL_W_QUEUE
  CL_Y_EMERG edge_CL_Y_EMERG__CL_E_NOTAIR@-->|"no session: enrols a recovery<br/>while on deck"| CL_E_NOTAIR
  CL_W_QUEUE edge_CL_W_QUEUE__CL_Y_CANCEL@--> CL_Y_CANCEL
  CL_W_QUEUE edge_CL_W_QUEUE__CL_Y_CHECKOUT@--> CL_Y_CHECKOUT
  CL_Y_CANCEL edge_CL_Y_CANCEL__CL_R_CANCEL@--> CL_R_CANCEL
  CL_Y_CHECKOUT edge_CL_Y_CHECKOUT__CL_R_CHECKOUT@--> CL_R_CHECKOUT
  CL_Y_CANCEL edge_CL_Y_CANCEL__CL_R_NOACTIVE@-->|"no session"| CL_R_NOACTIVE
  CL_Y_CHECKOUT edge_CL_Y_CHECKOUT__CL_R_NOACTIVE@-->|"no session"| CL_R_NOACTIVE
  CL_R_CANCEL edge_CL_R_CANCEL__CL_RESTART@--> CL_RESTART
  CL_R_CHECKOUT edge_CL_R_CHECKOUT__CL_RESTART@--> CL_RESTART
  CL_R_NOACTIVE edge_CL_R_NOACTIVE__CL_RESTART@--> CL_RESTART
  CL_RESTART edge_CL_RESTART__CL_C_GUARD@--> CL_C_GUARD
  CL_E_CLEARED edge_CL_E_CLEARED__CL_F_LAUNCH@--> CL_F_LAUNCH
  CL_W_QUEUE edge_CL_W_QUEUE__CL_F_LAUNCH@-->|"launch without clearance"| CL_F_LAUNCH
  CL_F_LAUNCH edge_CL_F_LAUNCH__CL_E_AIRBORNE@-->|"was cleared"| CL_E_AIRBORNE
  CL_F_LAUNCH edge_CL_F_LAUNCH__CL_E_UNCLEARED@-->|"deck seen but not cleared"| CL_E_UNCLEARED
  CL_F_LAUNCH edge_CL_F_LAUNCH__CL_E_NOTDECK@-->|"in air but never seen on deck"| CL_E_NOTDECK
  CL_F_LAUNCH edge_CL_F_LAUNCH__CL_E_FOREIGN@-->|"takeoff from another deck, or<br/>unknown deck never seen"| CL_E_FOREIGN
  CL_E_NOTDECK edge_CL_E_NOTDECK__CL_RESTART@--> CL_RESTART
  CL_E_FOREIGN edge_CL_E_FOREIGN__CL_RESTART@--> CL_RESTART
  CL_E_CLEARED edge_CL_E_CLEARED__CL_Y_AIRBORNE@--> CL_Y_AIRBORNE
  CL_Y_AIRBORNE edge_CL_Y_AIRBORNE__CL_R_AIRB_EARLY@-->|"launch not yet observed"| CL_R_AIRB_EARLY
  CL_R_AIRB_EARLY edge_CL_R_AIRB_EARLY__CL_F_LAUNCH@--> CL_F_LAUNCH
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_Y_HANDOFF@--> CL_Y_HANDOFF
  CL_E_UNCLEARED edge_CL_E_UNCLEARED__CL_Y_HANDOFF@--> CL_Y_HANDOFF
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_Y_AIRBORNE@--> CL_Y_AIRBORNE
  CL_Y_AIRBORNE edge_CL_Y_AIRBORNE__CL_R_AIRB_STATUS@-->|"launch observed"| CL_R_AIRB_STATUS
  CL_R_AIRB_STATUS edge_CL_R_AIRB_STATUS__CL_Y_HANDOFF@--> CL_Y_HANDOFF
  CL_Y_HANDOFF edge_CL_Y_HANDOFF__CL_R_HANDOFF@-->|"launch observed"| CL_R_HANDOFF
  CL_Y_HANDOFF edge_CL_Y_HANDOFF__CL_R_HANDOFF_EARLY@-->|"not yet observed"| CL_R_HANDOFF_EARLY
  CL_R_HANDOFF_EARLY edge_CL_R_HANDOFF_EARLY__CL_F_LAUNCH@--> CL_F_LAUNCH
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_F_10NM@--> CL_F_10NM
  CL_F_10NM edge_CL_F_10NM__CL_E_AUTOHANDOFF@--> CL_E_AUTOHANDOFF
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_W_LANDED@--> CL_W_LANDED
  CL_R_HANDOFF edge_CL_R_HANDOFF__CL_DONE@--> CL_DONE
  CL_E_AUTOHANDOFF edge_CL_E_AUTOHANDOFF__CL_DONE@--> CL_DONE
  CL_W_LANDED edge_CL_W_LANDED__CL_DONE@--> CL_DONE
  CL_DONE edge_CL_DONE__CL_Y_RETURN@-->|"returning to the ship"| CL_Y_RETURN
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_Y_RETURN@-->|"recover now instead"| CL_Y_RETURN
  CL_Y_RETURN edge_CL_Y_RETURN__CL_GO_RECOVERY@-->|"airborne: departure replaced by<br/>recovery"| CL_GO_RECOVERY
  CL_Y_RETURN edge_CL_Y_RETURN__CL_E_NOTAIR@-->|"still on deck"| CL_E_NOTAIR
  CL_E_NOTAIR edge_CL_E_NOTAIR__CL_RESTART@--> CL_RESTART
  CL_W_QUEUE edge_CL_W_QUEUE__CL_W_CLOSED@-.-> CL_W_CLOSED
  CL_E_AIRBORNE edge_CL_E_AIRBORNE__CL_W_CLOSED@-.-> CL_W_CLOSED
  CL_W_QUEUE edge_CL_W_QUEUE__CL_E_LOST@-.->|"aircraft omitted after its first<br/>watch"| CL_E_LOST
  CL_W_QUEUE edge_CL_W_QUEUE__CL_E_BADTRACK@-.->|"invalid aircraft sample"| CL_E_BADTRACK
  CL_W_QUEUE edge_CL_W_QUEUE__CL_W_XFER@-.-> CL_W_XFER
  CL_W_CLOSED edge_CL_W_CLOSED__CL_RESTART@--> CL_RESTART
  CL_E_LOST edge_CL_E_LOST__CL_RESTART@--> CL_RESTART
  CL_E_BADTRACK edge_CL_E_BADTRACK__CL_RESTART@--> CL_RESTART
  CL_W_XFER edge_CL_W_XFER__CL_RESTART@--> CL_RESTART

  classDef normalFlow stroke:#15803d,stroke-width:3px,stroke-dasharray:0
  classDef errorFlow stroke:#b91c1c,stroke-width:2.5px,stroke-dasharray:0
  classDef recoveryFlow stroke:#92400e,stroke-width:2px,stroke-dasharray:7 4
  classDef neutralFlow stroke:#64748b,stroke-width:1px,stroke-dasharray:0
  classDef pilotCall fill:#eff6ff,stroke:#1d4ed8,color:#172554
  classDef errorNode fill:#fef2f2,stroke:#b91c1c,color:#450a0a
  classDef recoveryNode fill:#fffbeb,stroke:#92400e,color:#451a03
  class edge_CL_W_SPAWN__CL_C_GUARD,edge_CL_Y_REQDEP__CL_R_QUEUED_MISSING,edge_CL_Y_READY__CL_R_QUEUED_MISSING,edge_CL_R_QUEUED_MISSING__CL_W_QUEUE,edge_CL_W_QUEUE__CL_Y_SAYAGAIN,edge_CL_Y_SAYAGAIN__CL_R_STARTUP,edge_CL_Y_SAYAGAIN__CL_R_QUEUED,edge_CL_Y_SAYAGAIN__CL_E_CLEARED,edge_CL_Y_SAYAGAIN__CL_R_AIRB_STATUS,edge_CL_Y_AIRBORNE__CL_R_AIRB_EARLY neutralFlow
  class edge_CL_W_HANDOVER__CL_Y_REQDEP,edge_CL_C_GUARD__CL_Y_STARTUP,edge_CL_C_GUARD__CL_Y_REQDEP,edge_CL_Y_STARTUP__CL_R_STARTUP,edge_CL_R_STARTUP__CL_Y_READY,edge_CL_Y_READY__CL_R_QUEUED,edge_CL_Y_REQDEP__CL_R_QUEUED,edge_CL_R_QUEUED__CL_W_QUEUE,edge_CL_W_QUEUE__CL_W_LANE,edge_CL_W_LANE__CL_W_QUEUE,edge_CL_W_QUEUE__CL_E_CLEARED,edge_CL_E_CLEARED__CL_F_LAUNCH,edge_CL_F_LAUNCH__CL_E_AIRBORNE,edge_CL_E_CLEARED__CL_Y_AIRBORNE,edge_CL_E_AIRBORNE__CL_Y_HANDOFF,edge_CL_E_AIRBORNE__CL_Y_AIRBORNE,edge_CL_Y_AIRBORNE__CL_R_AIRB_STATUS,edge_CL_R_AIRB_STATUS__CL_Y_HANDOFF,edge_CL_Y_HANDOFF__CL_R_HANDOFF,edge_CL_E_AIRBORNE__CL_F_10NM,edge_CL_F_10NM__CL_E_AUTOHANDOFF,edge_CL_R_HANDOFF__CL_DONE,edge_CL_E_AUTOHANDOFF__CL_DONE,edge_CL_DONE__CL_Y_RETURN,edge_CL_E_AIRBORNE__CL_Y_RETURN,edge_CL_Y_RETURN__CL_GO_RECOVERY normalFlow
  class edge_CL_C_GUARD__CL_N_SILENT,edge_CL_C_GUARD__CL_R_NOID,edge_CL_C_GUARD__CL_R_SAYAGAIN,edge_CL_C_GUARD__CL_R_UNKNOWN,edge_CL_C_GUARD__CL_R_AMBIG,edge_CL_C_GUARD__CL_R_SRS,edge_CL_C_GUARD__CL_R_STALE,edge_CL_C_GUARD__CL_R_OTHER,edge_CL_C_GUARD__CL_R_NOSESSION,edge_CL_Y_STARTUP__CL_R_LIMIT,edge_CL_Y_STARTUP__CL_R_INAIR,edge_CL_Y_REQDEP__CL_R_LIMIT,edge_CL_Y_REQDEP__CL_R_INAIR,edge_CL_E_CLEARED__CL_W_STALE,edge_CL_Y_EMERG__CL_E_NOTAIR,edge_CL_W_QUEUE__CL_F_LAUNCH,edge_CL_F_LAUNCH__CL_E_UNCLEARED,edge_CL_F_LAUNCH__CL_E_NOTDECK,edge_CL_F_LAUNCH__CL_E_FOREIGN,edge_CL_Y_HANDOFF__CL_R_HANDOFF_EARLY,edge_CL_Y_RETURN__CL_E_NOTAIR,edge_CL_W_QUEUE__CL_E_LOST,edge_CL_W_QUEUE__CL_E_BADTRACK errorFlow
  class edge_CL_N_SILENT__CL_RETRY,edge_CL_R_NOID__CL_RETRY,edge_CL_R_SAYAGAIN__CL_RETRY,edge_CL_R_UNKNOWN__CL_RETRY,edge_CL_R_AMBIG__CL_RETRY,edge_CL_R_SRS__CL_RETRY,edge_CL_R_STALE__CL_RETRY,edge_CL_R_NOSESSION__CL_Y_REQDEP,edge_CL_R_OTHER__CL_Y_XCHECKOUT,edge_CL_R_OTHER__CL_Y_REQDEP,edge_CL_Y_XCHECKOUT__CL_R_XCHECKOUT,edge_CL_R_XCHECKOUT__CL_Y_REQDEP,edge_CL_RETRY__CL_C_GUARD,edge_CL_R_LIMIT__CL_RETRY,edge_CL_R_INAIR__CL_GO_RECOVERY,edge_CL_W_STALE__CL_W_QUEUE,edge_CL_W_QUEUE__CL_Y_EMERG,edge_CL_Y_EMERG__CL_R_EMERG,edge_CL_R_EMERG__CL_W_QUEUE,edge_CL_W_QUEUE__CL_Y_CANCEL,edge_CL_W_QUEUE__CL_Y_CHECKOUT,edge_CL_Y_CANCEL__CL_R_CANCEL,edge_CL_Y_CHECKOUT__CL_R_CHECKOUT,edge_CL_Y_CANCEL__CL_R_NOACTIVE,edge_CL_Y_CHECKOUT__CL_R_NOACTIVE,edge_CL_R_CANCEL__CL_RESTART,edge_CL_R_CHECKOUT__CL_RESTART,edge_CL_R_NOACTIVE__CL_RESTART,edge_CL_RESTART__CL_C_GUARD,edge_CL_E_NOTDECK__CL_RESTART,edge_CL_E_FOREIGN__CL_RESTART,edge_CL_R_AIRB_EARLY__CL_F_LAUNCH,edge_CL_E_UNCLEARED__CL_Y_HANDOFF,edge_CL_R_HANDOFF_EARLY__CL_F_LAUNCH,edge_CL_E_AIRBORNE__CL_W_LANDED,edge_CL_W_LANDED__CL_DONE,edge_CL_E_NOTAIR__CL_RESTART,edge_CL_W_QUEUE__CL_W_CLOSED,edge_CL_E_AIRBORNE__CL_W_CLOSED,edge_CL_W_QUEUE__CL_W_XFER,edge_CL_W_CLOSED__CL_RESTART,edge_CL_E_LOST__CL_RESTART,edge_CL_E_BADTRACK__CL_RESTART,edge_CL_W_XFER__CL_RESTART recoveryFlow
  class CL_Y_XCHECKOUT,CL_Y_STARTUP,CL_Y_READY,CL_Y_REQDEP,CL_Y_SAYAGAIN,CL_Y_EMERG,CL_Y_CANCEL,CL_Y_CHECKOUT,CL_Y_AIRBORNE,CL_Y_HANDOFF,CL_Y_RETURN pilotCall
  class CL_N_SILENT,CL_R_NOID,CL_R_SAYAGAIN,CL_R_UNKNOWN,CL_R_AMBIG,CL_R_SRS,CL_R_STALE,CL_R_OTHER,CL_R_NOSESSION,CL_R_LIMIT,CL_R_INAIR,CL_W_STALE,CL_E_NOTAIR,CL_E_UNCLEARED,CL_E_NOTDECK,CL_E_FOREIGN,CL_R_HANDOFF_EARLY,CL_E_LOST,CL_E_BADTRACK errorNode
  class CL_W_CLOSED,CL_W_XFER,CL_RESTART,CL_Y_XCHECKOUT,CL_R_XCHECKOUT,CL_RETRY,CL_Y_EMERG,CL_R_EMERG,CL_Y_CANCEL,CL_R_CANCEL,CL_Y_CHECKOUT,CL_R_CHECKOUT,CL_R_NOACTIVE recoveryNode
```

### Launch phrase table

| Purpose | Exact call | Accepted when | Reply |
|---|---|---|---|
| Startup (optional) | `[CARRIER] departure, [CALLSIGN], request startup` | Any time you are not tracked airborne in a recovery | `copy, report ready for launch` |
| Ready (after startup) | `[CARRIER] departure, [CALLSIGN], ready for launch` | Startup session, or as a new request | `CASE [CASE] departure, number [N] for launch, hold` |
| Launch request (skip startup) | `[CARRIER] departure, [CALLSIGN], request departure` | As above | Same queue reply |
| Status | `[CARRIER] departure, [CALLSIGN], say again` | Departure session | Current phase reply. A revoked clearance is never replayed |
| Emergency | `[CARRIER] departure, [CALLSIGN], emergency` | Departure session | `copy emergency, you have priority, …` (launch order priority only) |
| Airborne report | `[CARRIER] departure, [CALLSIGN], airborne` | Departure session | Status only. The launch must be observed by the mission |
| Departure complete | `[CARRIER] departure, [CALLSIGN], departure complete` | Launch observed | `departure complete, frequency change approved` |
| Cancel | `[CARRIER] departure, [CALLSIGN], cancel departure` | Any session (works with a stale link) | `copy, departure cancelled, checked out` |
| Check out | `[CARRIER] departure, [CALLSIGN], checking out` | Any session (works with a stale link) | `copy, checked out` |

`request startup`, `ready for launch` and `request departure` are one intent (`departure`). `startup`/`start up` puts a new session in the startup phase. Any other form queues it as ready. A later ready call moves startup to the queue. A repeat while queued, released or launched just returns the status.

The queue reply leads with the missing fact when the mission lacks it: `departure, departure case not available, number one for launch, hold` (also `altimeter not available` or `carrier heading not available`). Release waits until the fact arrives.

### Launch example (test fixture)

```text
YOU:   Roosevelt departure, Viper one one, request departure
REPLY: viper 1 1, Roosevelt departure, CASE one departure, number one for launch, hold
EVENT: viper 1 1, Roosevelt departure, cleared for launch, CASE one departure, BRC three five three, altimeter two nine nine two, report airborne
(catapult launch observed)
EVENT: viper 1 1, Roosevelt departure, airborne, CASE one departure, report departure complete
YOU:   Roosevelt departure, Viper one one, departure complete
REPLY: viper 1 1, Roosevelt departure, departure complete, frequency change approved
```

## Chart 2 — Carrier recovery, CASE I / II / III with recycle paths (complete flow)

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
  %% Entry, lifecycle and session cleanup
    CR_F_INBOUND["FLY: airborne, tune the [CARRIER] mission radio frequency"]
    CR_W_FROMLAUNCH["FROM LAUNCH CHART: airborne after launch"]
    CR_W_CLOSED["WAIT—NO CALL: dead, eject, disconnect or birth event; catalog loss; new incarnation or coalition; carrier dead, disabled or retuned; new mission epoch. Session closes silently"]
    CR_E_LOST["EVENT marshal: radar contact lost, carrier session closed, check in again"]
    CR_E_BADTRACK["EVENT marshal: tracking data invalid, carrier session closed, check in again"]
    CR_E_OTHERDECK["EVENT marshal: landing not on this deck, recovery closed"]
    CR_W_FOREIGN["WAIT—NO CALL: takeoff event from another deck closes the recovery silently"]
    CR_W_XFER["WAIT—NO CALL: a new airfield ATC enrolment of this aircraft releases the carrier session silently"]
    CR_E_NOTAIR["EVENT marshal: you are not airborne, recovery not required, check in when airborne"]

  %% Every call: address, identity and link guards
    CR_C_GUARD{"CHECK every call: carrier_id, channel, coalition, receiver word, caller identity, SRS name, live link, owning carrier"}
    CR_N_SILENT["NO REPLY: wrong frequency, coalition or carrier_id, no unique enabled carrier on the channel, or call not opened with a receiver word of this carrier"]
    CR_R_NOID["REPLY marshal: i could not recognize your callsign or modex"]
    CR_R_UNKNOWN["REPLY marshal: unable, no player aircraft with that identity"]
    CR_R_AMBIG["REPLY marshal: more than one aircraft matches, say modex"]
    CR_R_SRS["REPLY marshal: unable, your radio name does not identify you"]
    CR_R_STALE["REPLY marshal: unable, no live carrier link"]
    CR_R_OTHER["REPLY marshal: unable, you are checked in with [OTHER_CARRIER]"]
    CR_R_SAYAGAIN["REPLY marshal: say again"]
    CR_R_NOSESSION["REPLY marshal: unable, check in first"]
    CR_Y_XCHECKOUT["YOU: [OTHER_CARRIER] marshal, [CALLSIGN], checking out"]
    CR_R_XCHECKOUT["REPLY marshal on [OTHER_CARRIER]: copy, checked out"]
    CR_RETRY["RETRY: correct receiver, callsign or modex, SRS name, frequency or wording (remove negation), then repeat the same call"]

  %% Check-in, marshal assignment and readback
    CR_Y_CHECKIN["YOU: [CARRIER] marshal, [CALLSIGN], checking in"]
    CR_Y_EMERG["YOU: [CARRIER] marshal, [CALLSIGN], mayday, low fuel"]
    CR_R_EMERG_LEAD["REPLY marshal: copy emergency, you have priority, followed by the stand-by or CASE assignment below"]
    CR_R_EMERG_STATUS["REPLY marshal: copy emergency, you have priority, [CURRENT_STATUS]"]
    CR_R_LIMIT["REPLY marshal: unable, carrier traffic limit reached"]
    CR_R_STANDBY["REPLY marshal: checked in, [MISSING_FACT], stand by"]
    CR_E_ASSIGN["EVENT marshal: the CASE assignment below, once facts or a stack slot are available"]
    CR_R_ASSIGN1["REPLY marshal: CASE one recovery, BRC [BRC], hold overhead at angels [ANGELS], altimeter [ALTIMETER], read back angels and altimeter"]
    CR_R_ASSIGN23["REPLY marshal: CASE [CASE] recovery, final bearing [FINAL], marshal on the [RADIAL] radial, [DME] DME, angels [ANGELS], expected approach time [EAT_MINUTE], altimeter [ALTIMETER], read back radial, DME, angels, approach time and altimeter"]
    CR_Y_RB1["YOU: [CARRIER] marshal, [CALLSIGN], readback angels [ANGELS], altimeter [ALTIMETER]"]
    CR_Y_RB23["YOU: [CARRIER] marshal, [CALLSIGN], readback [RADIAL] radial, [DME] DME, angels [ANGELS], approach time [EAT_MINUTE], altimeter [ALTIMETER]"]
    CR_R_RBBAD["REPLY marshal: negative readback, [FIRST_WRONG_FIELD], read back again"]
    CR_R_RBNONE["REPLY marshal: unable, no marshal instructions to read back"]
    CR_R_RBOK["REPLY marshal: readback correct, report established"]

  %% Holding, Charlie or commence (shared lane)
    CR_F_HOLD1["FLY CASE I: hold overhead within 10 nm at angels [ANGELS] plus or minus 1000 ft"]
    CR_F_HOLD23["FLY CASE II/III: hold within 6 nm of the [RADIAL] radial [DME] DME fix at angels [ANGELS] plus or minus 1000 ft"]
    CR_Y_EST["YOU: [CARRIER] marshal, [CALLSIGN], established"]
    CR_R_EST_NORB["REPLY marshal: unable, read back marshal instructions first"]
    CR_R_EST_NOASSIGN["REPLY marshal: unable, no marshal assignment"]
    CR_R_EST_NORADAR["REPLY marshal: unable, no radar contact, report established when airborne"]
    CR_R_EST_NOTHOLD["REPLY marshal: negative, not observed in holding, [HOLDING]"]
    CR_R_EST_OK["REPLY marshal: roger established, number [N]"]
    CR_W_LANE["WAIT—NO CALL: grant needs the free shared lane, no enrolled aircraft touched down or on deck, and CASE I, priority or the approach time reached; emergency first, then check-in order"]
    CR_E_CHARLIE["EVENT or appended REPLY marshal: Charlie, BRC [BRC], report commencing"]
    CR_E_COMMENCE["EVENT or appended REPLY marshal: commence, final bearing [FINAL], report commencing"]
    CR_Y_SAYAGAIN["YOU: [CARRIER] marshal, [CALLSIGN], say again"]
    CR_R_STATUS["REPLY by current role: current instruction, e.g. established, number [N], [HOLDING], continue holding"]
    CR_W_STALE["WAIT—NO CALL: bridge stale; a granted Charlie or commence not yet flown is revoked and the lane freed; earlier replies are void"]
    CR_Y_COMM["YOU: [CARRIER] marshal, [CALLSIGN], commencing"]
    CR_R_COMM_NEG["REPLY marshal: negative, continue holding, number [N]"]
    CR_R_COMM_I["REPLY marshal: roger commencing, report initial"]
    CR_R_COMM_III["REPLY marshal: roger commencing, report platform"]

  %% CASE II/III platform and ten miles; CASE I/II initial, break and abeam
    CR_Y_PLAT["YOU: [CARRIER] approach, [CALLSIGN], platform"]
    CR_R_PLAT2["REPLY marshal: roger platform, report initial"]
    CR_R_PLAT3["REPLY marshal: roger platform, report ten miles"]
    CR_Y_TEN["YOU: [CARRIER] approach, [CALLSIGN], ten miles"]
    CR_R_TEN["REPLY paddles: roger, call the ball"]
    CR_Y_INIT["YOU: [CARRIER] tower, [CALLSIGN], initial"]
    CR_R_INIT["REPLY marshal: roger initial, report the break"]
    CR_Y_BREAK["YOU: [CARRIER] tower, [CALLSIGN], break"]
    CR_R_BREAK["REPLY marshal: roger break, report abeam"]
    CR_Y_ABEAM["YOU: [CARRIER] tower, [CALLSIGN], abeam"]
    CR_R_ABEAM["REPLY paddles: roger abeam, call the ball"]
    CR_R_PAT_SEQ["REPLY by current role: unable, [CURRENT_INSTRUCTION]"]
    CR_R_PAT_POS["REPLY by current role: unable, position not confirmed, [CURRENT_INSTRUCTION]"]
    CR_R_PAT_RADAR["REPLY by current role: unable, no radar contact"]
    CR_R_PAT_NONE["REPLY by current role: unable, no approach in progress"]

  %% Ball, clara, wave-off, bolter and foul deck
    CR_Y_BALL["YOU: [CARRIER] paddles, [CALLSIGN], ball, [FUEL_STATE]"]
    CR_R_BALL["REPLY paddles: roger ball"]
    CR_R_BALL_POS["REPLY paddles: unable, position not confirmed, call the ball"]
    CR_R_BALL_FOUL["REPLY paddles: wave off, wave off, foul deck, [RECYCLE_INSTRUCTION]"]
    CR_Y_CLARA["YOU: [CARRIER] paddles, [CALLSIGN], clara"]
    CR_R_CLARA["REPLY paddles: roger clara, keep it coming"]
    CR_Y_WAVEOFF["YOU: [CARRIER] paddles, [CALLSIGN], waving off"]
    CR_R_WAVEOFF["REPLY paddles: roger wave off, [RECYCLE_INSTRUCTION]"]
    CR_E_FOUL["EVENT paddles: wave off, wave off, foul deck, [RECYCLE_INSTRUCTION]"]
    CR_F_TOUCH["FLY: touchdown. Mission land event on this deck (contact only, not yet a trap); the deck is now foul for others"]
    CR_F_LEAVE["FLY: airborne again after touchdown"]
    CR_E_BOLTER["EVENT paddles: bolter, bolter, [RECYCLE_INSTRUCTION]"]
    CR_Y_BOLTER["YOU: [CARRIER] paddles, [CALLSIGN], bolter"]
    CR_R_BOLTER["REPLY paddles: roger bolter, [RECYCLE_INSTRUCTION]"]
    CR_R_BOLTER_WAIT["REPLY paddles: roger, stand by for deck status"]
    CR_C_RECYCLE{"CHECK recycle: lane held? CASE?"}
    CR_R_RC_PATTERN["RECYCLE_INSTRUCTION: climb straight ahead, rejoin the pattern, report abeam"]
    CR_R_RC_III["RECYCLE_INSTRUCTION: climb straight ahead to one thousand two hundred feet, report ten miles"]
    CR_R_RC_MARSHAL["RECYCLE_INSTRUCTION: climb straight ahead, return to marshal, [HOLDING], report established"]
    CR_R_RC_CHECKIN["RECYCLE_INSTRUCTION: climb straight ahead, check in with marshal"]

  %% Trap, deck clear and departure handover
    CR_W_TRAP["WAIT: mission confirms on deck (stopped)"]
    CR_E_TRAP["EVENT tower: trap confirmed, clear the landing area, report deck clear"]
    CR_Y_TRAPPED["YOU: [CARRIER] tower, [CALLSIGN], trapped"]
    CR_R_TRAPPED_WAIT["REPLY by current role: roger, stand by for deck confirmation"]
    CR_R_TRAPPED_OK["REPLY tower: trap confirmed, clear the landing area, report deck clear"]
    CR_Y_DECKCLEAR["YOU: [CARRIER] tower, [CALLSIGN], deck clear"]
    CR_R_DECKCLEAR["REPLY tower: copy deck clear, recovery complete"]
    CR_R_NOTRAP["REPLY by current role: unable, no trap recorded"]
    CR_E_TAKEOFF["EVENT tower: airborne, recovery session closed"]
    CR_Y_HANDOVER["YOU: [CARRIER] departure, [CALLSIGN], request departure"]
    CR_GO_LAUNCH["GO TO: Carrier launch chart, queue reply (deck already proven)"]
    CR_DONE["DONE: recovery session closed; the lane passes to the next aircraft"]

  %% Instruction revocation, cancel and check out
    CR_W_FACTS["WAIT—NO CALL: CASE changes, or the ship turns 1 degree or more (BRC for CASE I, final bearing for CASE II/III), while assigned, holding or flying the approach"]
    CR_E_FACTS_APP["EVENT marshal: wave off, wave off, recovery instructions changed, climb straight ahead, new instructions, [NEW_ASSIGNMENT]"]
    CR_E_FACTS_GRANT["EVENT marshal: clearance cancelled, new instructions, [NEW_ASSIGNMENT]"]
    CR_E_FACTS_HOLD["EVENT marshal: new instructions, [NEW_ASSIGNMENT]"]
    CR_E_FACTS_NONE["EVENT marshal: recovery case no longer available, hold present position, stand by"]
    CR_E_FACTS_FULL["EVENT marshal: recovery instructions cancelled, hold present position, stand by"]
    CR_Y_CANCEL["YOU: [CARRIER] marshal, [CALLSIGN], cancel recovery"]
    CR_R_CANCEL["REPLY marshal: copy, recovery cancelled, checked out"]
    CR_Y_CHECKOUT["YOU: [CARRIER] marshal, [CALLSIGN], checking out"]
    CR_R_CHECKOUT["REPLY marshal: copy, checked out"]
    CR_R_NOACTIVE["REPLY marshal: copy, no active carrier session"]
    CR_RESTART["RETURN: check in again when ready; a respawned seat is a new occupant"]








  classDef you fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
  classDef reply fill:#f1f8e9,stroke:#558b2f
  classDef wait fill:#fff8e1,stroke:#f9a825,stroke-dasharray:4 3
  classDef refuse fill:#ffebee,stroke:#c62828
  class CR_Y_CHECKIN,CR_Y_EMERG,CR_Y_RB1,CR_Y_RB23,CR_Y_EST,CR_Y_SAYAGAIN,CR_Y_COMM,CR_Y_PLAT,CR_Y_TEN,CR_Y_INIT,CR_Y_BREAK,CR_Y_ABEAM,CR_Y_BALL,CR_Y_CLARA,CR_Y_WAVEOFF,CR_Y_BOLTER,CR_Y_TRAPPED,CR_Y_DECKCLEAR,CR_Y_HANDOVER,CR_Y_CANCEL,CR_Y_CHECKOUT,CR_Y_XCHECKOUT you
  class CR_R_EMERG_LEAD,CR_R_EMERG_STATUS,CR_E_ASSIGN,CR_R_ASSIGN1,CR_R_ASSIGN23,CR_R_RBOK,CR_R_EST_OK,CR_E_CHARLIE,CR_E_COMMENCE,CR_R_STATUS,CR_R_COMM_I,CR_R_COMM_III,CR_R_PLAT2,CR_R_PLAT3,CR_R_TEN,CR_R_INIT,CR_R_BREAK,CR_R_ABEAM,CR_R_BALL,CR_R_CLARA,CR_R_TRAPPED_OK,CR_E_TRAP,CR_R_DECKCLEAR,CR_R_CANCEL,CR_R_CHECKOUT,CR_R_NOACTIVE,CR_R_XCHECKOUT,CR_R_RC_PATTERN,CR_R_RC_III,CR_R_RC_MARSHAL,CR_R_RC_CHECKIN reply
  class CR_F_INBOUND,CR_W_FROMLAUNCH,CR_W_CLOSED,CR_W_FOREIGN,CR_W_XFER,CR_F_HOLD1,CR_F_HOLD23,CR_W_LANE,CR_W_STALE,CR_F_TOUCH,CR_F_LEAVE,CR_W_TRAP,CR_W_FACTS,CR_DONE,CR_RESTART,CR_RETRY,CR_GO_LAUNCH wait
  class CR_N_SILENT,CR_R_NOID,CR_R_UNKNOWN,CR_R_AMBIG,CR_R_SRS,CR_R_STALE,CR_R_OTHER,CR_R_SAYAGAIN,CR_R_NOSESSION,CR_R_LIMIT,CR_R_STANDBY,CR_R_RBBAD,CR_R_RBNONE,CR_R_EST_NORB,CR_R_EST_NOASSIGN,CR_R_EST_NORADAR,CR_R_EST_NOTHOLD,CR_R_COMM_NEG,CR_R_PAT_SEQ,CR_R_PAT_POS,CR_R_PAT_RADAR,CR_R_PAT_NONE,CR_R_BALL_POS,CR_R_BALL_FOUL,CR_R_WAVEOFF,CR_E_FOUL,CR_E_BOLTER,CR_R_BOLTER,CR_R_BOLTER_WAIT,CR_R_TRAPPED_WAIT,CR_R_NOTRAP,CR_E_TAKEOFF,CR_E_FACTS_APP,CR_E_FACTS_GRANT,CR_E_FACTS_HOLD,CR_E_FACTS_NONE,CR_E_FACTS_FULL,CR_E_OTHERDECK,CR_E_LOST,CR_E_BADTRACK,CR_E_NOTAIR refuse

  %% Stable edge IDs keep path colors attached to their transitions.
  CR_F_INBOUND edge_CR_F_INBOUND__CR_C_GUARD@--> CR_C_GUARD
  CR_W_FROMLAUNCH edge_CR_W_FROMLAUNCH__CR_C_GUARD@--> CR_C_GUARD
  CR_C_GUARD edge_CR_C_GUARD__CR_N_SILENT@-->|"not addressed to this carrier<br/>radio"| CR_N_SILENT
  CR_C_GUARD edge_CR_C_GUARD__CR_R_NOID@-->|"callsign or modex not recognized"| CR_R_NOID
  CR_C_GUARD edge_CR_C_GUARD__CR_R_SAYAGAIN@-->|"no command and caller not<br/>recognized, or addressed with no<br/>command or a negated call"| CR_R_SAYAGAIN
  CR_C_GUARD edge_CR_C_GUARD__CR_R_UNKNOWN@-->|"no human aircraft matches"| CR_R_UNKNOWN
  CR_C_GUARD edge_CR_C_GUARD__CR_R_AMBIG@-->|"callsign matches two aircraft<br/>(modex heard: say callsign)"| CR_R_AMBIG
  CR_C_GUARD edge_CR_C_GUARD__CR_R_SRS@-->|"SRS name does not uniquely name<br/>you"| CR_R_SRS
  CR_C_GUARD edge_CR_C_GUARD__CR_R_STALE@-->|"bridge stale; only cancel and<br/>check out still act"| CR_R_STALE
  CR_C_GUARD edge_CR_C_GUARD__CR_R_OTHER@-->|"session on another ship;<br/>anything but check in, request<br/>or emergency"| CR_R_OTHER
  CR_C_GUARD edge_CR_C_GUARD__CR_R_NOSESSION@-->|"procedure call with no session"| CR_R_NOSESSION
  CR_C_GUARD edge_CR_C_GUARD__CR_Y_CHECKIN@--> CR_Y_CHECKIN
  CR_C_GUARD edge_CR_C_GUARD__CR_Y_EMERG@-->|"emergency"| CR_Y_EMERG
  CR_N_SILENT edge_CR_N_SILENT__CR_RETRY@--> CR_RETRY
  CR_R_NOID edge_CR_R_NOID__CR_RETRY@--> CR_RETRY
  CR_R_SAYAGAIN edge_CR_R_SAYAGAIN__CR_RETRY@--> CR_RETRY
  CR_R_UNKNOWN edge_CR_R_UNKNOWN__CR_RETRY@--> CR_RETRY
  CR_R_AMBIG edge_CR_R_AMBIG__CR_RETRY@-->|"repeat with the other identity<br/>form"| CR_RETRY
  CR_R_SRS edge_CR_R_SRS__CR_RETRY@--> CR_RETRY
  CR_R_STALE edge_CR_R_STALE__CR_RETRY@-->|"wait for the link"| CR_RETRY
  CR_R_NOSESSION edge_CR_R_NOSESSION__CR_Y_CHECKIN@--> CR_Y_CHECKIN
  CR_R_OTHER edge_CR_R_OTHER__CR_Y_XCHECKOUT@-->|"leave the other ship on its<br/>frequency"| CR_Y_XCHECKOUT
  CR_R_OTHER edge_CR_R_OTHER__CR_Y_CHECKIN@-->|"or transfer: checking in here<br/>closes the other ship's session"| CR_Y_CHECKIN
  CR_Y_XCHECKOUT edge_CR_Y_XCHECKOUT__CR_R_XCHECKOUT@--> CR_R_XCHECKOUT
  CR_R_XCHECKOUT edge_CR_R_XCHECKOUT__CR_Y_CHECKIN@--> CR_Y_CHECKIN
  CR_RETRY edge_CR_RETRY__CR_C_GUARD@--> CR_C_GUARD
  CR_Y_CHECKIN edge_CR_Y_CHECKIN__CR_R_ASSIGN1@-->|"new session, CASE I facts"| CR_R_ASSIGN1
  CR_Y_CHECKIN edge_CR_Y_CHECKIN__CR_R_ASSIGN23@-->|"new session, CASE II or III<br/>facts"| CR_R_ASSIGN23
  CR_Y_CHECKIN edge_CR_Y_CHECKIN__CR_R_STANDBY@-->|"CASE, altimeter, heading or deck<br/>geometry missing, or stack full"| CR_R_STANDBY
  CR_Y_CHECKIN edge_CR_Y_CHECKIN__CR_R_LIMIT@-->|"64 sessions already"| CR_R_LIMIT
  CR_Y_CHECKIN edge_CR_Y_CHECKIN__CR_R_STATUS@-->|"already in recovery here: status<br/>only, no re-enrolment"| CR_R_STATUS
  CR_Y_EMERG edge_CR_Y_EMERG__CR_R_EMERG_LEAD@-->|"no session here: enrol with<br/>priority"| CR_R_EMERG_LEAD
  CR_Y_EMERG edge_CR_Y_EMERG__CR_R_EMERG_STATUS@-->|"existing session: priority set"| CR_R_EMERG_STATUS
  CR_Y_EMERG edge_CR_Y_EMERG__CR_R_LIMIT@-->|"64 sessions already"| CR_R_LIMIT
  CR_R_EMERG_LEAD edge_CR_R_EMERG_LEAD__CR_R_ASSIGN1@--> CR_R_ASSIGN1
  CR_R_EMERG_LEAD edge_CR_R_EMERG_LEAD__CR_R_ASSIGN23@--> CR_R_ASSIGN23
  CR_R_EMERG_LEAD edge_CR_R_EMERG_LEAD__CR_R_STANDBY@--> CR_R_STANDBY
  CR_R_EMERG_STATUS edge_CR_R_EMERG_STATUS__CR_W_LANE@--> CR_W_LANE
  CR_R_LIMIT edge_CR_R_LIMIT__CR_RETRY@--> CR_RETRY
  CR_R_STANDBY edge_CR_R_STANDBY__CR_E_ASSIGN@--> CR_E_ASSIGN
  CR_E_ASSIGN edge_CR_E_ASSIGN__CR_R_ASSIGN1@--> CR_R_ASSIGN1
  CR_E_ASSIGN edge_CR_E_ASSIGN__CR_R_ASSIGN23@--> CR_R_ASSIGN23
  CR_R_STANDBY edge_CR_R_STANDBY__CR_E_NOTAIR@-.->|"on deck, never seen airborne"| CR_E_NOTAIR
  CR_R_ASSIGN1 edge_CR_R_ASSIGN1__CR_E_NOTAIR@-.->|"on deck, never seen airborne"| CR_E_NOTAIR
  CR_E_NOTAIR edge_CR_E_NOTAIR__CR_RESTART@--> CR_RESTART
  CR_R_ASSIGN1 edge_CR_R_ASSIGN1__CR_Y_RB1@--> CR_Y_RB1
  CR_R_ASSIGN23 edge_CR_R_ASSIGN23__CR_Y_RB23@--> CR_Y_RB23
  CR_Y_RB1 edge_CR_Y_RB1__CR_R_RBBAD@-->|"wrong, missing or extra number"| CR_R_RBBAD
  CR_Y_RB23 edge_CR_Y_RB23__CR_R_RBBAD@-->|"wrong, missing, extra or out of<br/>order"| CR_R_RBBAD
  CR_R_RBBAD edge_CR_R_RBBAD__CR_Y_RB1@-->|"CASE I"| CR_Y_RB1
  CR_R_RBBAD edge_CR_R_RBBAD__CR_Y_RB23@-->|"CASE II/III"| CR_Y_RB23
  CR_Y_RB1 edge_CR_Y_RB1__CR_R_RBNONE@-->|"not awaiting a readback"| CR_R_RBNONE
  CR_Y_RB23 edge_CR_Y_RB23__CR_R_RBNONE@-->|"not awaiting a readback"| CR_R_RBNONE
  CR_R_RBNONE edge_CR_R_RBNONE__CR_Y_SAYAGAIN@--> CR_Y_SAYAGAIN
  CR_Y_RB1 edge_CR_Y_RB1__CR_R_RBOK@-->|"exact"| CR_R_RBOK
  CR_Y_RB23 edge_CR_Y_RB23__CR_R_RBOK@-->|"exact"| CR_R_RBOK
  CR_R_RBOK edge_CR_R_RBOK__CR_F_HOLD1@-->|"CASE I"| CR_F_HOLD1
  CR_R_RBOK edge_CR_R_RBOK__CR_F_HOLD23@-->|"CASE II/III"| CR_F_HOLD23
  CR_F_HOLD1 edge_CR_F_HOLD1__CR_Y_EST@--> CR_Y_EST
  CR_F_HOLD23 edge_CR_F_HOLD23__CR_Y_EST@--> CR_Y_EST
  CR_Y_EST edge_CR_Y_EST__CR_R_EST_NORB@-->|"assignment not yet read back"| CR_R_EST_NORB
  CR_Y_EST edge_CR_Y_EST__CR_R_EST_NOASSIGN@-->|"checked in without assignment,<br/>or a departure session"| CR_R_EST_NOASSIGN
  CR_Y_EST edge_CR_Y_EST__CR_R_EST_NORADAR@-->|"not tracked airborne"| CR_R_EST_NORADAR
  CR_Y_EST edge_CR_Y_EST__CR_R_EST_NOTHOLD@-->|"outside holding position or<br/>altitude"| CR_R_EST_NOTHOLD
  CR_Y_EST edge_CR_Y_EST__CR_R_STATUS@-->|"already past marshal: status"| CR_R_STATUS
  CR_R_EST_NORB edge_CR_R_EST_NORB__CR_Y_SAYAGAIN@--> CR_Y_SAYAGAIN
  CR_R_EST_NOASSIGN edge_CR_R_EST_NOASSIGN__CR_E_ASSIGN@--> CR_E_ASSIGN
  CR_R_EST_NORADAR edge_CR_R_EST_NORADAR__CR_Y_EST@--> CR_Y_EST
  CR_R_EST_NOTHOLD edge_CR_R_EST_NOTHOLD__CR_F_HOLD1@-->|"CASE I"| CR_F_HOLD1
  CR_R_EST_NOTHOLD edge_CR_R_EST_NOTHOLD__CR_F_HOLD23@-->|"CASE II/III"| CR_F_HOLD23
  CR_Y_EST edge_CR_Y_EST__CR_R_EST_OK@-->|"observed in holding"| CR_R_EST_OK
  CR_R_EST_OK edge_CR_R_EST_OK__CR_W_LANE@--> CR_W_LANE
  CR_W_LANE edge_CR_W_LANE__CR_E_CHARLIE@-->|"CASE I grant"| CR_E_CHARLIE
  CR_W_LANE edge_CR_W_LANE__CR_E_COMMENCE@-->|"CASE II/III grant"| CR_E_COMMENCE
  CR_W_LANE edge_CR_W_LANE__CR_Y_SAYAGAIN@--> CR_Y_SAYAGAIN
  CR_Y_SAYAGAIN edge_CR_Y_SAYAGAIN__CR_R_STATUS@--> CR_R_STATUS
  CR_R_STATUS edge_CR_R_STATUS__CR_W_LANE@-->|"rejoin the step it names"| CR_W_LANE
  CR_W_LANE edge_CR_W_LANE__CR_Y_COMM@-->|"commencing before the grant"| CR_Y_COMM
  CR_E_CHARLIE edge_CR_E_CHARLIE__CR_Y_COMM@--> CR_Y_COMM
  CR_E_COMMENCE edge_CR_E_COMMENCE__CR_Y_COMM@--> CR_Y_COMM
  CR_E_CHARLIE edge_CR_E_CHARLIE__CR_W_STALE@-->|"link stale before commencing"| CR_W_STALE
  CR_E_COMMENCE edge_CR_E_COMMENCE__CR_W_STALE@-->|"link stale before commencing"| CR_W_STALE
  CR_W_STALE edge_CR_W_STALE__CR_W_LANE@-->|"link returns"| CR_W_LANE
  CR_Y_COMM edge_CR_Y_COMM__CR_R_COMM_NEG@-->|"established, no grant yet"| CR_R_COMM_NEG
  CR_R_COMM_NEG edge_CR_R_COMM_NEG__CR_W_LANE@--> CR_W_LANE
  CR_Y_COMM edge_CR_Y_COMM__CR_R_COMM_I@-->|"granted, within 60 nm, CASE I or<br/>II"| CR_R_COMM_I
  CR_Y_COMM edge_CR_Y_COMM__CR_R_COMM_III@-->|"granted, within 60 nm, CASE III"| CR_R_COMM_III
  CR_Y_COMM edge_CR_Y_COMM__CR_R_PAT_SEQ@-->|"other holding phase"| CR_R_PAT_SEQ
  CR_Y_COMM edge_CR_Y_COMM__CR_R_PAT_POS@-->|"beyond 60 nm"| CR_R_PAT_POS
  CR_Y_COMM edge_CR_Y_COMM__CR_R_PAT_RADAR@-->|"not tracked airborne"| CR_R_PAT_RADAR
  CR_E_CHARLIE edge_CR_E_CHARLIE__CR_Y_INIT@-->|"CASE I may skip commencing"| CR_Y_INIT
  CR_R_COMM_I edge_CR_R_COMM_I__CR_Y_PLAT@-->|"CASE II"| CR_Y_PLAT
  CR_R_COMM_I edge_CR_R_COMM_I__CR_Y_INIT@-->|"CASE I or II"| CR_Y_INIT
  CR_R_COMM_III edge_CR_R_COMM_III__CR_Y_PLAT@--> CR_Y_PLAT
  CR_Y_PLAT edge_CR_Y_PLAT__CR_R_PLAT2@-->|"CASE II, within 35 nm"| CR_R_PLAT2
  CR_Y_PLAT edge_CR_Y_PLAT__CR_R_PLAT3@-->|"CASE III, within 35 nm"| CR_R_PLAT3
  CR_R_PLAT2 edge_CR_R_PLAT2__CR_Y_INIT@--> CR_Y_INIT
  CR_R_PLAT3 edge_CR_R_PLAT3__CR_Y_TEN@--> CR_Y_TEN
  CR_Y_TEN edge_CR_Y_TEN__CR_R_TEN@-->|"CASE III after platform, within<br/>15 nm"| CR_R_TEN
  CR_Y_INIT edge_CR_Y_INIT__CR_R_INIT@-->|"within 8 nm"| CR_R_INIT
  CR_R_INIT edge_CR_R_INIT__CR_Y_BREAK@--> CR_Y_BREAK
  CR_Y_BREAK edge_CR_Y_BREAK__CR_R_BREAK@-->|"within 5 nm"| CR_R_BREAK
  CR_R_BREAK edge_CR_R_BREAK__CR_Y_ABEAM@--> CR_Y_ABEAM
  CR_Y_ABEAM edge_CR_Y_ABEAM__CR_R_ABEAM@-->|"within 5 nm"| CR_R_ABEAM
  CR_R_ABEAM edge_CR_R_ABEAM__CR_Y_BALL@--> CR_Y_BALL
  CR_R_TEN edge_CR_R_TEN__CR_Y_BALL@--> CR_Y_BALL
  CR_Y_PLAT edge_CR_Y_PLAT__CR_R_PAT_SEQ@-->|"out of sequence or wrong CASE"| CR_R_PAT_SEQ
  CR_Y_TEN edge_CR_Y_TEN__CR_R_PAT_SEQ@-->|"out of sequence or wrong CASE"| CR_R_PAT_SEQ
  CR_Y_INIT edge_CR_Y_INIT__CR_R_PAT_SEQ@-->|"out of sequence or wrong CASE"| CR_R_PAT_SEQ
  CR_Y_BREAK edge_CR_Y_BREAK__CR_R_PAT_SEQ@-->|"out of sequence"| CR_R_PAT_SEQ
  CR_Y_ABEAM edge_CR_Y_ABEAM__CR_R_PAT_SEQ@-->|"out of sequence"| CR_R_PAT_SEQ
  CR_Y_PLAT edge_CR_Y_PLAT__CR_R_PAT_POS@-->|"beyond range"| CR_R_PAT_POS
  CR_Y_TEN edge_CR_Y_TEN__CR_R_PAT_POS@-->|"beyond range"| CR_R_PAT_POS
  CR_Y_INIT edge_CR_Y_INIT__CR_R_PAT_POS@-->|"beyond range"| CR_R_PAT_POS
  CR_Y_BREAK edge_CR_Y_BREAK__CR_R_PAT_POS@-->|"beyond range"| CR_R_PAT_POS
  CR_Y_ABEAM edge_CR_Y_ABEAM__CR_R_PAT_POS@-->|"beyond range"| CR_R_PAT_POS
  CR_Y_INIT edge_CR_Y_INIT__CR_R_PAT_NONE@-->|"no assignment, or a departure<br/>session"| CR_R_PAT_NONE
  CR_Y_INIT edge_CR_Y_INIT__CR_R_COMM_NEG@-->|"established, no grant yet"| CR_R_COMM_NEG
  CR_Y_BALL edge_CR_Y_BALL__CR_R_COMM_NEG@-->|"still established in holding"| CR_R_COMM_NEG
  CR_R_PAT_SEQ edge_CR_R_PAT_SEQ__CR_Y_SAYAGAIN@-->|"fly the step it names"| CR_Y_SAYAGAIN
  CR_R_PAT_POS edge_CR_R_PAT_POS__CR_RETRY@-->|"close in, repeat the same call"| CR_RETRY
  CR_R_PAT_RADAR edge_CR_R_PAT_RADAR__CR_RETRY@-->|"repeat when tracked"| CR_RETRY
  CR_R_PAT_NONE edge_CR_R_PAT_NONE__CR_Y_CHECKIN@--> CR_Y_CHECKIN
  CR_Y_BALL edge_CR_Y_BALL__CR_R_BALL@-->|"inside 2 nm, at or below 1500<br/>ft, within 45 degrees astern,<br/>deck clear"| CR_R_BALL
  CR_Y_BALL edge_CR_Y_BALL__CR_R_BALL_POS@-->|"position not confirmed"| CR_R_BALL_POS
  CR_R_BALL_POS edge_CR_R_BALL_POS__CR_Y_BALL@--> CR_Y_BALL
  CR_Y_BALL edge_CR_Y_BALL__CR_R_BALL_FOUL@-->|"enrolled aircraft touched down<br/>or on deck"| CR_R_BALL_FOUL
  CR_Y_BALL edge_CR_Y_BALL__CR_R_PAT_SEQ@-->|"before abeam or ten miles"| CR_R_PAT_SEQ
  CR_Y_BALL edge_CR_Y_BALL__CR_R_PAT_RADAR@-->|"not tracked airborne"| CR_R_PAT_RADAR
  CR_R_ABEAM edge_CR_R_ABEAM__CR_Y_CLARA@--> CR_Y_CLARA
  CR_R_TEN edge_CR_R_TEN__CR_Y_CLARA@--> CR_Y_CLARA
  CR_R_BALL edge_CR_R_BALL__CR_Y_CLARA@--> CR_Y_CLARA
  CR_Y_CLARA edge_CR_Y_CLARA__CR_R_CLARA@-->|"after abeam, ten miles or ball"| CR_R_CLARA
  CR_Y_CLARA edge_CR_Y_CLARA__CR_R_PAT_NONE@-->|"any other phase"| CR_R_PAT_NONE
  CR_R_CLARA edge_CR_R_CLARA__CR_Y_BALL@-->|"no state change; call the ball<br/>when sighted"| CR_Y_BALL
  CR_R_ABEAM edge_CR_R_ABEAM__CR_Y_WAVEOFF@--> CR_Y_WAVEOFF
  CR_R_TEN edge_CR_R_TEN__CR_Y_WAVEOFF@--> CR_Y_WAVEOFF
  CR_R_BALL edge_CR_R_BALL__CR_Y_WAVEOFF@--> CR_Y_WAVEOFF
  CR_Y_WAVEOFF edge_CR_Y_WAVEOFF__CR_R_WAVEOFF@-->|"after abeam, ten miles or ball"| CR_R_WAVEOFF
  CR_Y_WAVEOFF edge_CR_Y_WAVEOFF__CR_R_PAT_NONE@-->|"any other phase"| CR_R_PAT_NONE
  CR_R_BALL edge_CR_R_BALL__CR_E_FOUL@-->|"another enrolled aircraft<br/>touches down"| CR_E_FOUL
  CR_R_BALL edge_CR_R_BALL__CR_F_TOUCH@--> CR_F_TOUCH
  CR_F_TOUCH edge_CR_F_TOUCH__CR_E_OTHERDECK@-->|"lands on another or unknown deck"| CR_E_OTHERDECK
  CR_F_TOUCH edge_CR_F_TOUCH__CR_F_LEAVE@--> CR_F_LEAVE
  CR_F_LEAVE edge_CR_F_LEAVE__CR_E_BOLTER@--> CR_E_BOLTER
  CR_R_BALL edge_CR_R_BALL__CR_Y_BOLTER@--> CR_Y_BOLTER
  CR_F_TOUCH edge_CR_F_TOUCH__CR_Y_BOLTER@--> CR_Y_BOLTER
  CR_Y_BOLTER edge_CR_Y_BOLTER__CR_R_BOLTER@-->|"at ball or touchdown and tracked<br/>airborne"| CR_R_BOLTER
  CR_Y_BOLTER edge_CR_Y_BOLTER__CR_R_BOLTER_WAIT@-->|"touched down, not airborne"| CR_R_BOLTER_WAIT
  CR_Y_BOLTER edge_CR_Y_BOLTER__CR_R_PAT_SEQ@-->|"any other phase"| CR_R_PAT_SEQ
  CR_Y_BOLTER edge_CR_Y_BOLTER__CR_R_PAT_NONE@-->|"departure session"| CR_R_PAT_NONE
  CR_R_BOLTER_WAIT edge_CR_R_BOLTER_WAIT__CR_W_TRAP@--> CR_W_TRAP
  CR_R_BALL_FOUL edge_CR_R_BALL_FOUL__CR_C_RECYCLE@--> CR_C_RECYCLE
  CR_R_WAVEOFF edge_CR_R_WAVEOFF__CR_C_RECYCLE@--> CR_C_RECYCLE
  CR_E_FOUL edge_CR_E_FOUL__CR_C_RECYCLE@--> CR_C_RECYCLE
  CR_E_BOLTER edge_CR_E_BOLTER__CR_C_RECYCLE@--> CR_C_RECYCLE
  CR_R_BOLTER edge_CR_R_BOLTER__CR_C_RECYCLE@--> CR_C_RECYCLE
  CR_C_RECYCLE edge_CR_C_RECYCLE__CR_R_RC_PATTERN@-->|"lane held, CASE I or II"| CR_R_RC_PATTERN
  CR_C_RECYCLE edge_CR_C_RECYCLE__CR_R_RC_III@-->|"lane held, CASE III"| CR_R_RC_III
  CR_C_RECYCLE edge_CR_C_RECYCLE__CR_R_RC_MARSHAL@-->|"no lane, assignment kept"| CR_R_RC_MARSHAL
  CR_C_RECYCLE edge_CR_C_RECYCLE__CR_R_RC_CHECKIN@-->|"no lane, no assignment"| CR_R_RC_CHECKIN
  CR_R_RC_PATTERN edge_CR_R_RC_PATTERN__CR_Y_ABEAM@-->|"lane kept"| CR_Y_ABEAM
  CR_R_RC_III edge_CR_R_RC_III__CR_Y_TEN@-->|"lane kept"| CR_Y_TEN
  CR_R_RC_MARSHAL edge_CR_R_RC_MARSHAL__CR_F_HOLD1@-->|"CASE I"| CR_F_HOLD1
  CR_R_RC_MARSHAL edge_CR_R_RC_MARSHAL__CR_F_HOLD23@-->|"CASE II/III"| CR_F_HOLD23
  CR_R_RC_CHECKIN edge_CR_R_RC_CHECKIN__CR_Y_CHECKIN@-->|"status reply; assignment follows<br/>automatically"| CR_Y_CHECKIN
  CR_F_TOUCH edge_CR_F_TOUCH__CR_W_TRAP@--> CR_W_TRAP
  CR_W_TRAP edge_CR_W_TRAP__CR_E_TRAP@--> CR_E_TRAP
  CR_F_TOUCH edge_CR_F_TOUCH__CR_Y_TRAPPED@--> CR_Y_TRAPPED
  CR_Y_TRAPPED edge_CR_Y_TRAPPED__CR_R_TRAPPED_WAIT@-->|"trap not confirmed yet"| CR_R_TRAPPED_WAIT
  CR_R_TRAPPED_WAIT edge_CR_R_TRAPPED_WAIT__CR_W_TRAP@--> CR_W_TRAP
  CR_E_TRAP edge_CR_E_TRAP__CR_Y_TRAPPED@--> CR_Y_TRAPPED
  CR_Y_TRAPPED edge_CR_Y_TRAPPED__CR_R_TRAPPED_OK@-->|"on deck confirmed"| CR_R_TRAPPED_OK
  CR_E_TRAP edge_CR_E_TRAP__CR_Y_DECKCLEAR@-->|"lane still held"| CR_Y_DECKCLEAR
  CR_R_TRAPPED_OK edge_CR_R_TRAPPED_OK__CR_Y_DECKCLEAR@--> CR_Y_DECKCLEAR
  CR_F_TOUCH edge_CR_F_TOUCH__CR_Y_DECKCLEAR@-->|"before trap confirmation"| CR_Y_DECKCLEAR
  CR_Y_DECKCLEAR edge_CR_Y_DECKCLEAR__CR_R_NOTRAP@-->|"no confirmed trap"| CR_R_NOTRAP
  CR_R_NOTRAP edge_CR_R_NOTRAP__CR_W_TRAP@--> CR_W_TRAP
  CR_Y_DECKCLEAR edge_CR_Y_DECKCLEAR__CR_R_DECKCLEAR@-->|"on deck confirmed"| CR_R_DECKCLEAR
  CR_R_DECKCLEAR edge_CR_R_DECKCLEAR__CR_DONE@--> CR_DONE
  CR_E_TRAP edge_CR_E_TRAP__CR_E_TAKEOFF@-->|"takeoff event from the deck"| CR_E_TAKEOFF
  CR_E_TAKEOFF edge_CR_E_TAKEOFF__CR_DONE@--> CR_DONE
  CR_E_TRAP edge_CR_E_TRAP__CR_Y_HANDOVER@-->|"hot turnaround: replaces<br/>recovery, frees lane"| CR_Y_HANDOVER
  CR_DONE edge_CR_DONE__CR_Y_HANDOVER@-->|"new launch from deck"| CR_Y_HANDOVER
  CR_DONE edge_CR_DONE__CR_Y_CHECKIN@-->|"stay airborne and recover again"| CR_Y_CHECKIN
  CR_Y_HANDOVER edge_CR_Y_HANDOVER__CR_GO_LAUNCH@--> CR_GO_LAUNCH
  CR_R_ASSIGN1 edge_CR_R_ASSIGN1__CR_W_FACTS@-.-> CR_W_FACTS
  CR_W_LANE edge_CR_W_LANE__CR_W_FACTS@-.-> CR_W_FACTS
  CR_R_BALL edge_CR_R_BALL__CR_W_FACTS@-.-> CR_W_FACTS
  CR_W_FACTS edge_CR_W_FACTS__CR_E_FACTS_APP@-->|"flying approach phases"| CR_E_FACTS_APP
  CR_W_FACTS edge_CR_W_FACTS__CR_E_FACTS_GRANT@-->|"granted, still holding"| CR_E_FACTS_GRANT
  CR_W_FACTS edge_CR_W_FACTS__CR_E_FACTS_HOLD@-->|"assigned or holding"| CR_E_FACTS_HOLD
  CR_W_FACTS edge_CR_W_FACTS__CR_E_FACTS_NONE@-->|"CASE removed; may follow the<br/>wave-off or clearance-cancelled<br/>lead"| CR_E_FACTS_NONE
  CR_W_FACTS edge_CR_W_FACTS__CR_E_FACTS_FULL@-->|"cannot reassign, e.g. stack<br/>full; may follow the same lead"| CR_E_FACTS_FULL
  CR_E_FACTS_APP edge_CR_E_FACTS_APP__CR_Y_RB1@-->|"read back the new CASE I set"| CR_Y_RB1
  CR_E_FACTS_APP edge_CR_E_FACTS_APP__CR_Y_RB23@-->|"read back the new CASE II/III<br/>set"| CR_Y_RB23
  CR_E_FACTS_GRANT edge_CR_E_FACTS_GRANT__CR_Y_RB1@--> CR_Y_RB1
  CR_E_FACTS_GRANT edge_CR_E_FACTS_GRANT__CR_Y_RB23@--> CR_Y_RB23
  CR_E_FACTS_HOLD edge_CR_E_FACTS_HOLD__CR_Y_RB1@--> CR_Y_RB1
  CR_E_FACTS_HOLD edge_CR_E_FACTS_HOLD__CR_Y_RB23@--> CR_Y_RB23
  CR_E_FACTS_NONE edge_CR_E_FACTS_NONE__CR_E_ASSIGN@--> CR_E_ASSIGN
  CR_E_FACTS_FULL edge_CR_E_FACTS_FULL__CR_E_ASSIGN@--> CR_E_ASSIGN
  CR_W_LANE edge_CR_W_LANE__CR_Y_CANCEL@--> CR_Y_CANCEL
  CR_W_LANE edge_CR_W_LANE__CR_Y_CHECKOUT@--> CR_Y_CHECKOUT
  CR_Y_CANCEL edge_CR_Y_CANCEL__CR_R_CANCEL@-->|"recovery session, any phase"| CR_R_CANCEL
  CR_Y_CHECKOUT edge_CR_Y_CHECKOUT__CR_R_CHECKOUT@--> CR_R_CHECKOUT
  CR_Y_CANCEL edge_CR_Y_CANCEL__CR_R_NOACTIVE@-->|"no session"| CR_R_NOACTIVE
  CR_Y_CHECKOUT edge_CR_Y_CHECKOUT__CR_R_NOACTIVE@-->|"no session"| CR_R_NOACTIVE
  CR_R_CANCEL edge_CR_R_CANCEL__CR_RESTART@--> CR_RESTART
  CR_R_CHECKOUT edge_CR_R_CHECKOUT__CR_RESTART@--> CR_RESTART
  CR_R_NOACTIVE edge_CR_R_NOACTIVE__CR_RESTART@--> CR_RESTART
  CR_RESTART edge_CR_RESTART__CR_Y_CHECKIN@--> CR_Y_CHECKIN
  CR_W_LANE edge_CR_W_LANE__CR_W_CLOSED@-.-> CR_W_CLOSED
  CR_R_BALL edge_CR_R_BALL__CR_W_CLOSED@-.-> CR_W_CLOSED
  CR_W_LANE edge_CR_W_LANE__CR_E_LOST@-.->|"aircraft omitted after its first<br/>watch"| CR_E_LOST
  CR_W_LANE edge_CR_W_LANE__CR_E_BADTRACK@-.->|"invalid aircraft sample"| CR_E_BADTRACK
  CR_W_LANE edge_CR_W_LANE__CR_W_FOREIGN@-.-> CR_W_FOREIGN
  CR_W_LANE edge_CR_W_LANE__CR_F_TOUCH@-.->|"lands on this deck without the<br/>lane"| CR_F_TOUCH
  CR_W_LANE edge_CR_W_LANE__CR_W_XFER@-.-> CR_W_XFER
  CR_E_OTHERDECK edge_CR_E_OTHERDECK__CR_RESTART@--> CR_RESTART
  CR_W_CLOSED edge_CR_W_CLOSED__CR_RESTART@--> CR_RESTART
  CR_E_LOST edge_CR_E_LOST__CR_RESTART@--> CR_RESTART
  CR_E_BADTRACK edge_CR_E_BADTRACK__CR_RESTART@--> CR_RESTART
  CR_W_FOREIGN edge_CR_W_FOREIGN__CR_RESTART@--> CR_RESTART
  CR_W_XFER edge_CR_W_XFER__CR_RESTART@--> CR_RESTART

  classDef normalFlow stroke:#15803d,stroke-width:3px,stroke-dasharray:0
  classDef errorFlow stroke:#b91c1c,stroke-width:2.5px,stroke-dasharray:0
  classDef recoveryFlow stroke:#92400e,stroke-width:2px,stroke-dasharray:7 4
  classDef neutralFlow stroke:#64748b,stroke-width:1px,stroke-dasharray:0
  classDef pilotCall fill:#eff6ff,stroke:#1d4ed8,color:#172554
  classDef errorNode fill:#fef2f2,stroke:#b91c1c,color:#450a0a
  classDef recoveryNode fill:#fffbeb,stroke:#92400e,color:#451a03
  class edge_CR_F_INBOUND__CR_C_GUARD,edge_CR_W_FROMLAUNCH__CR_C_GUARD,edge_CR_Y_CHECKIN__CR_R_STANDBY,edge_CR_Y_CHECKIN__CR_R_STATUS,edge_CR_Y_EST__CR_R_STATUS,edge_CR_W_LANE__CR_Y_SAYAGAIN,edge_CR_Y_SAYAGAIN__CR_R_STATUS,edge_CR_R_STATUS__CR_W_LANE,edge_CR_R_ABEAM__CR_Y_CLARA,edge_CR_R_TEN__CR_Y_CLARA,edge_CR_R_BALL__CR_Y_CLARA,edge_CR_Y_CLARA__CR_R_CLARA,edge_CR_R_CLARA__CR_Y_BALL,edge_CR_F_TOUCH__CR_Y_TRAPPED,edge_CR_Y_TRAPPED__CR_R_TRAPPED_WAIT neutralFlow
  class edge_CR_C_GUARD__CR_N_SILENT,edge_CR_C_GUARD__CR_R_NOID,edge_CR_C_GUARD__CR_R_SAYAGAIN,edge_CR_C_GUARD__CR_R_UNKNOWN,edge_CR_C_GUARD__CR_R_AMBIG,edge_CR_C_GUARD__CR_R_SRS,edge_CR_C_GUARD__CR_R_STALE,edge_CR_C_GUARD__CR_R_OTHER,edge_CR_C_GUARD__CR_R_NOSESSION,edge_CR_Y_CHECKIN__CR_R_LIMIT,edge_CR_Y_EMERG__CR_R_LIMIT,edge_CR_R_STANDBY__CR_E_NOTAIR,edge_CR_R_ASSIGN1__CR_E_NOTAIR,edge_CR_Y_RB1__CR_R_RBBAD,edge_CR_Y_RB23__CR_R_RBBAD,edge_CR_Y_RB1__CR_R_RBNONE,edge_CR_Y_RB23__CR_R_RBNONE,edge_CR_Y_EST__CR_R_EST_NORB,edge_CR_Y_EST__CR_R_EST_NOASSIGN,edge_CR_Y_EST__CR_R_EST_NORADAR,edge_CR_Y_EST__CR_R_EST_NOTHOLD,edge_CR_E_CHARLIE__CR_W_STALE,edge_CR_E_COMMENCE__CR_W_STALE,edge_CR_Y_COMM__CR_R_COMM_NEG,edge_CR_Y_COMM__CR_R_PAT_SEQ,edge_CR_Y_COMM__CR_R_PAT_POS,edge_CR_Y_COMM__CR_R_PAT_RADAR,edge_CR_Y_PLAT__CR_R_PAT_SEQ,edge_CR_Y_TEN__CR_R_PAT_SEQ,edge_CR_Y_INIT__CR_R_PAT_SEQ,edge_CR_Y_BREAK__CR_R_PAT_SEQ,edge_CR_Y_ABEAM__CR_R_PAT_SEQ,edge_CR_Y_PLAT__CR_R_PAT_POS,edge_CR_Y_TEN__CR_R_PAT_POS,edge_CR_Y_INIT__CR_R_PAT_POS,edge_CR_Y_BREAK__CR_R_PAT_POS,edge_CR_Y_ABEAM__CR_R_PAT_POS,edge_CR_Y_INIT__CR_R_PAT_NONE,edge_CR_Y_INIT__CR_R_COMM_NEG,edge_CR_Y_BALL__CR_R_COMM_NEG,edge_CR_Y_BALL__CR_R_BALL_POS,edge_CR_Y_BALL__CR_R_BALL_FOUL,edge_CR_Y_BALL__CR_R_PAT_SEQ,edge_CR_Y_BALL__CR_R_PAT_RADAR,edge_CR_Y_CLARA__CR_R_PAT_NONE,edge_CR_Y_WAVEOFF__CR_R_PAT_NONE,edge_CR_R_BALL__CR_E_FOUL,edge_CR_F_TOUCH__CR_E_OTHERDECK,edge_CR_F_LEAVE__CR_E_BOLTER,edge_CR_Y_BOLTER__CR_R_PAT_SEQ,edge_CR_Y_BOLTER__CR_R_PAT_NONE,edge_CR_Y_DECKCLEAR__CR_R_NOTRAP,edge_CR_E_TRAP__CR_E_TAKEOFF,edge_CR_W_FACTS__CR_E_FACTS_APP,edge_CR_W_FACTS__CR_E_FACTS_GRANT,edge_CR_W_FACTS__CR_E_FACTS_NONE,edge_CR_W_FACTS__CR_E_FACTS_FULL,edge_CR_W_LANE__CR_E_LOST,edge_CR_W_LANE__CR_E_BADTRACK,edge_CR_W_LANE__CR_F_TOUCH,edge_CR_F_TOUCH__CR_Y_DECKCLEAR,edge_CR_W_LANE__CR_Y_COMM errorFlow
  class edge_CR_C_GUARD__CR_Y_CHECKIN,edge_CR_Y_CHECKIN__CR_R_ASSIGN1,edge_CR_Y_CHECKIN__CR_R_ASSIGN23,edge_CR_R_ASSIGN1__CR_Y_RB1,edge_CR_R_ASSIGN23__CR_Y_RB23,edge_CR_Y_RB1__CR_R_RBOK,edge_CR_Y_RB23__CR_R_RBOK,edge_CR_R_RBOK__CR_F_HOLD1,edge_CR_R_RBOK__CR_F_HOLD23,edge_CR_F_HOLD1__CR_Y_EST,edge_CR_F_HOLD23__CR_Y_EST,edge_CR_Y_EST__CR_R_EST_OK,edge_CR_R_EST_OK__CR_W_LANE,edge_CR_W_LANE__CR_E_CHARLIE,edge_CR_W_LANE__CR_E_COMMENCE,edge_CR_E_CHARLIE__CR_Y_COMM,edge_CR_E_COMMENCE__CR_Y_COMM,edge_CR_Y_COMM__CR_R_COMM_I,edge_CR_Y_COMM__CR_R_COMM_III,edge_CR_E_CHARLIE__CR_Y_INIT,edge_CR_R_COMM_I__CR_Y_PLAT,edge_CR_R_COMM_I__CR_Y_INIT,edge_CR_R_COMM_III__CR_Y_PLAT,edge_CR_Y_PLAT__CR_R_PLAT2,edge_CR_Y_PLAT__CR_R_PLAT3,edge_CR_R_PLAT2__CR_Y_INIT,edge_CR_R_PLAT3__CR_Y_TEN,edge_CR_Y_TEN__CR_R_TEN,edge_CR_Y_INIT__CR_R_INIT,edge_CR_R_INIT__CR_Y_BREAK,edge_CR_Y_BREAK__CR_R_BREAK,edge_CR_R_BREAK__CR_Y_ABEAM,edge_CR_Y_ABEAM__CR_R_ABEAM,edge_CR_R_ABEAM__CR_Y_BALL,edge_CR_R_TEN__CR_Y_BALL,edge_CR_Y_BALL__CR_R_BALL,edge_CR_R_BALL__CR_F_TOUCH,edge_CR_F_TOUCH__CR_W_TRAP,edge_CR_W_TRAP__CR_E_TRAP,edge_CR_E_TRAP__CR_Y_TRAPPED,edge_CR_Y_TRAPPED__CR_R_TRAPPED_OK,edge_CR_E_TRAP__CR_Y_DECKCLEAR,edge_CR_R_TRAPPED_OK__CR_Y_DECKCLEAR,edge_CR_Y_DECKCLEAR__CR_R_DECKCLEAR,edge_CR_R_DECKCLEAR__CR_DONE,edge_CR_E_TRAP__CR_Y_HANDOVER,edge_CR_DONE__CR_Y_HANDOVER,edge_CR_DONE__CR_Y_CHECKIN,edge_CR_Y_HANDOVER__CR_GO_LAUNCH normalFlow
  class edge_CR_C_GUARD__CR_Y_EMERG,edge_CR_N_SILENT__CR_RETRY,edge_CR_R_NOID__CR_RETRY,edge_CR_R_SAYAGAIN__CR_RETRY,edge_CR_R_UNKNOWN__CR_RETRY,edge_CR_R_AMBIG__CR_RETRY,edge_CR_R_SRS__CR_RETRY,edge_CR_R_STALE__CR_RETRY,edge_CR_R_NOSESSION__CR_Y_CHECKIN,edge_CR_R_OTHER__CR_Y_XCHECKOUT,edge_CR_R_OTHER__CR_Y_CHECKIN,edge_CR_Y_XCHECKOUT__CR_R_XCHECKOUT,edge_CR_R_XCHECKOUT__CR_Y_CHECKIN,edge_CR_RETRY__CR_C_GUARD,edge_CR_Y_EMERG__CR_R_EMERG_LEAD,edge_CR_Y_EMERG__CR_R_EMERG_STATUS,edge_CR_R_EMERG_LEAD__CR_R_ASSIGN1,edge_CR_R_EMERG_LEAD__CR_R_ASSIGN23,edge_CR_R_EMERG_LEAD__CR_R_STANDBY,edge_CR_R_EMERG_STATUS__CR_W_LANE,edge_CR_R_LIMIT__CR_RETRY,edge_CR_R_STANDBY__CR_E_ASSIGN,edge_CR_E_ASSIGN__CR_R_ASSIGN1,edge_CR_E_ASSIGN__CR_R_ASSIGN23,edge_CR_E_NOTAIR__CR_RESTART,edge_CR_R_RBBAD__CR_Y_RB1,edge_CR_R_RBBAD__CR_Y_RB23,edge_CR_R_RBNONE__CR_Y_SAYAGAIN,edge_CR_R_EST_NORB__CR_Y_SAYAGAIN,edge_CR_R_EST_NOASSIGN__CR_E_ASSIGN,edge_CR_R_EST_NORADAR__CR_Y_EST,edge_CR_R_EST_NOTHOLD__CR_F_HOLD1,edge_CR_R_EST_NOTHOLD__CR_F_HOLD23,edge_CR_W_STALE__CR_W_LANE,edge_CR_R_COMM_NEG__CR_W_LANE,edge_CR_R_PAT_SEQ__CR_Y_SAYAGAIN,edge_CR_R_PAT_POS__CR_RETRY,edge_CR_R_PAT_RADAR__CR_RETRY,edge_CR_R_PAT_NONE__CR_Y_CHECKIN,edge_CR_R_BALL_POS__CR_Y_BALL,edge_CR_R_ABEAM__CR_Y_WAVEOFF,edge_CR_R_TEN__CR_Y_WAVEOFF,edge_CR_R_BALL__CR_Y_WAVEOFF,edge_CR_Y_WAVEOFF__CR_R_WAVEOFF,edge_CR_F_TOUCH__CR_F_LEAVE,edge_CR_R_BALL__CR_Y_BOLTER,edge_CR_F_TOUCH__CR_Y_BOLTER,edge_CR_Y_BOLTER__CR_R_BOLTER,edge_CR_Y_BOLTER__CR_R_BOLTER_WAIT,edge_CR_R_BOLTER_WAIT__CR_W_TRAP,edge_CR_R_BALL_FOUL__CR_C_RECYCLE,edge_CR_R_WAVEOFF__CR_C_RECYCLE,edge_CR_E_FOUL__CR_C_RECYCLE,edge_CR_E_BOLTER__CR_C_RECYCLE,edge_CR_R_BOLTER__CR_C_RECYCLE,edge_CR_C_RECYCLE__CR_R_RC_PATTERN,edge_CR_C_RECYCLE__CR_R_RC_III,edge_CR_C_RECYCLE__CR_R_RC_MARSHAL,edge_CR_C_RECYCLE__CR_R_RC_CHECKIN,edge_CR_R_RC_PATTERN__CR_Y_ABEAM,edge_CR_R_RC_III__CR_Y_TEN,edge_CR_R_RC_MARSHAL__CR_F_HOLD1,edge_CR_R_RC_MARSHAL__CR_F_HOLD23,edge_CR_R_RC_CHECKIN__CR_Y_CHECKIN,edge_CR_R_TRAPPED_WAIT__CR_W_TRAP,edge_CR_R_NOTRAP__CR_W_TRAP,edge_CR_E_TAKEOFF__CR_DONE,edge_CR_R_ASSIGN1__CR_W_FACTS,edge_CR_W_LANE__CR_W_FACTS,edge_CR_R_BALL__CR_W_FACTS,edge_CR_W_FACTS__CR_E_FACTS_HOLD,edge_CR_E_FACTS_APP__CR_Y_RB1,edge_CR_E_FACTS_APP__CR_Y_RB23,edge_CR_E_FACTS_GRANT__CR_Y_RB1,edge_CR_E_FACTS_GRANT__CR_Y_RB23,edge_CR_E_FACTS_HOLD__CR_Y_RB1,edge_CR_E_FACTS_HOLD__CR_Y_RB23,edge_CR_E_FACTS_NONE__CR_E_ASSIGN,edge_CR_E_FACTS_FULL__CR_E_ASSIGN,edge_CR_W_LANE__CR_Y_CANCEL,edge_CR_W_LANE__CR_Y_CHECKOUT,edge_CR_Y_CANCEL__CR_R_CANCEL,edge_CR_Y_CHECKOUT__CR_R_CHECKOUT,edge_CR_Y_CANCEL__CR_R_NOACTIVE,edge_CR_Y_CHECKOUT__CR_R_NOACTIVE,edge_CR_R_CANCEL__CR_RESTART,edge_CR_R_CHECKOUT__CR_RESTART,edge_CR_R_NOACTIVE__CR_RESTART,edge_CR_RESTART__CR_Y_CHECKIN,edge_CR_W_LANE__CR_W_CLOSED,edge_CR_R_BALL__CR_W_CLOSED,edge_CR_W_LANE__CR_W_FOREIGN,edge_CR_W_LANE__CR_W_XFER,edge_CR_E_OTHERDECK__CR_RESTART,edge_CR_W_CLOSED__CR_RESTART,edge_CR_E_LOST__CR_RESTART,edge_CR_E_BADTRACK__CR_RESTART,edge_CR_W_FOREIGN__CR_RESTART,edge_CR_W_XFER__CR_RESTART recoveryFlow
  class CR_Y_XCHECKOUT,CR_Y_CHECKIN,CR_Y_EMERG,CR_Y_RB1,CR_Y_RB23,CR_Y_EST,CR_Y_SAYAGAIN,CR_Y_COMM,CR_Y_PLAT,CR_Y_TEN,CR_Y_INIT,CR_Y_BREAK,CR_Y_ABEAM,CR_Y_BALL,CR_Y_CLARA,CR_Y_WAVEOFF,CR_Y_BOLTER,CR_Y_TRAPPED,CR_Y_DECKCLEAR,CR_Y_HANDOVER,CR_Y_CANCEL,CR_Y_CHECKOUT pilotCall
  class CR_N_SILENT,CR_R_NOID,CR_R_SAYAGAIN,CR_R_UNKNOWN,CR_R_AMBIG,CR_R_SRS,CR_R_STALE,CR_R_OTHER,CR_R_NOSESSION,CR_R_LIMIT,CR_E_NOTAIR,CR_R_RBBAD,CR_R_RBNONE,CR_R_EST_NORB,CR_R_EST_NOASSIGN,CR_R_EST_NORADAR,CR_R_EST_NOTHOLD,CR_W_STALE,CR_R_COMM_NEG,CR_R_PAT_SEQ,CR_R_PAT_POS,CR_R_PAT_RADAR,CR_R_PAT_NONE,CR_R_BALL_POS,CR_R_BALL_FOUL,CR_E_FOUL,CR_E_OTHERDECK,CR_E_BOLTER,CR_R_NOTRAP,CR_E_TAKEOFF,CR_E_FACTS_APP,CR_E_FACTS_GRANT,CR_E_FACTS_NONE,CR_E_FACTS_FULL,CR_E_LOST,CR_E_BADTRACK errorNode
  class CR_W_CLOSED,CR_W_FOREIGN,CR_W_XFER,CR_Y_XCHECKOUT,CR_R_XCHECKOUT,CR_RETRY,CR_Y_EMERG,CR_R_EMERG_LEAD,CR_R_EMERG_STATUS,CR_Y_WAVEOFF,CR_R_WAVEOFF,CR_F_LEAVE,CR_Y_BOLTER,CR_R_BOLTER,CR_R_BOLTER_WAIT,CR_C_RECYCLE,CR_R_RC_PATTERN,CR_R_RC_III,CR_R_RC_MARSHAL,CR_R_RC_CHECKIN,CR_W_FACTS,CR_E_FACTS_HOLD,CR_Y_CANCEL,CR_R_CANCEL,CR_Y_CHECKOUT,CR_R_CHECKOUT,CR_R_NOACTIVE,CR_RESTART recoveryNode
```

### Recovery phrase table

| Purpose | Exact call | Accepted in phase | Reply |
|---|---|---|---|
| Check in | `[CARRIER] marshal, [CALLSIGN], checking in` | No recovery here (new session). Repeating it while in recovery only returns your status | CASE assignment, or `checked in, [MISSING_FACT], stand by` |
| Emergency | `[CARRIER] marshal, [CALLSIGN], mayday, low fuel` | Any time | `copy emergency, you have priority, …` (enrols you if you are not enrolled here) |
| CASE I readback | `[CARRIER] marshal, [CALLSIGN], readback angels [ANGELS], altimeter [ALTIMETER]` | Assigned | `readback correct, report established` |
| CASE II/III readback | `[CARRIER] marshal, [CALLSIGN], readback [RADIAL] radial, [DME] DME, angels [ANGELS], approach time [EAT_MINUTE], altimeter [ALTIMETER]` | Assigned | `readback correct, report established` |
| Established | `[CARRIER] marshal, [CALLSIGN], established` | Read back and observed in holding | `roger established, number [N]`, possibly followed by Charlie or commence |
| Status | `[CARRIER] marshal, [CALLSIGN], say again` | Any session | Current instruction |
| Commencing | `[CARRIER] marshal, [CALLSIGN], commencing` | After Charlie or commence | CASE I/II `roger commencing, report initial`, CASE III `roger commencing, report platform` |
| Platform | `[CARRIER] approach, [CALLSIGN], platform` | CASE II/III after commencing | CASE II `roger platform, report initial`, CASE III `roger platform, report ten miles` |
| Ten miles | `[CARRIER] approach, [CALLSIGN], ten miles` | CASE III after platform | `roger, call the ball` |
| Initial | `[CARRIER] tower, [CALLSIGN], initial` | CASE I after Charlie or commencing; CASE II after commencing or platform | `roger initial, report the break` |
| Break | `[CARRIER] tower, [CALLSIGN], break` | CASE I/II after initial | `roger break, report abeam` |
| Abeam | `[CARRIER] tower, [CALLSIGN], abeam` | CASE I/II after break | `roger abeam, call the ball` |
| Ball | `[CARRIER] paddles, [CALLSIGN], ball, [FUEL_STATE]` | After abeam (I/II) or ten miles (III), positioned | `roger ball` |
| Clara | `[CARRIER] paddles, [CALLSIGN], clara` | After abeam, ten miles or ball | `roger clara, keep it coming` (no state change) |
| Wave-off | `[CARRIER] paddles, [CALLSIGN], waving off` | After abeam, ten miles or ball | `roger wave off, [RECYCLE_INSTRUCTION]` |
| Bolter | `[CARRIER] paddles, [CALLSIGN], bolter` | At ball or touchdown **and** tracked airborne | `roger bolter, [RECYCLE_INSTRUCTION]` |
| Trapped | `[CARRIER] tower, [CALLSIGN], trapped` | Any (information only) | Status only. The trap is confirmed by the mission |
| Deck clear | `[CARRIER] tower, [CALLSIGN], deck clear` | Trap confirmed | `copy deck clear, recovery complete` (session closes, lane released) |
| Departure handover | `[CARRIER] departure, [CALLSIGN], request departure` | Trap confirmed (or after deck clear) | Launch queue reply (launch chart) |
| Cancel | `[CARRIER] marshal, [CALLSIGN], cancel recovery` | Any session (works with a stale link) | `copy, recovery cancelled, checked out` |
| Check out | `[CARRIER] marshal, [CALLSIGN], checking out` | Any session (works with a stale link) | `copy, checked out` |

Range limits for pattern calls (from the ship): commencing 60 nm, platform 35 nm, ten miles 15 nm, initial 8 nm, break and abeam 5 nm. The ball also needs: inside 2 nm, at or below 1500 ft, and within 45° of astern on the final bearing. A call already made (for example `initial` twice) only returns the current instruction.

### Recycle instructions (bolter, wave-off, foul deck)

| Situation | Instruction | Next call |
|---|---|---|
| Lane held, CASE I or II | `climb straight ahead, rejoin the pattern, report abeam` | `abeam` |
| Lane held, CASE III | `climb straight ahead to one thousand two hundred feet, report ten miles` | `ten miles` |
| Touched down without the lane, assignment kept | `climb straight ahead, return to marshal, [HOLDING], report established` | `established` |
| Touched down without the lane or an assignment | `climb straight ahead, check in with marshal` | Check-in returns status. The assignment follows automatically |

A bolter is announced by paddles (`bolter, bolter, …`) when the mission shows the aircraft airborne after a touchdown. Your own `bolter` call acts only when the track already shows you airborne. A recycle keeps the lane, so holding traffic keeps holding.

### Rules that are easy to get wrong

- **`cancel emergency` cancels the whole recovery.** Any cancel word ends the session (`copy, recovery cancelled, checked out`). There is **no** priority-withdrawal call. To drop priority, cancel and check in again. Do not say `emergency, cancel`: the emergency keyword comes first, so that call is handled as an emergency.
- **Checking in again is not a restart.** While you have a recovery session on this ship, `checking in` only returns your current instruction.
- **Wrong carrier.** With a session on another ship, any call here other than check in, launch request or emergency gets `unable, you are checked in with [OTHER_CARRIER]`. Check out on that ship's own frequency, or transfer by checking in here, which closes the old session.
- **Stale bridge.** With no fresh mission snapshot, every positive call gets `unable, no live carrier link`. Cancel and check out still work. A granted Charlie or commence that was not yet flown is revoked when the link goes stale.
- **Revocation.** If the CASE changes, or the ship's BRC (CASE I) or final bearing (CASE II/III) moves by 1° or more, every clearance built on the old facts ends. Read back the new set.
- **Trap and deck clear.** A touchdown fouls the deck, but only the mission's on-deck state confirms the trap. After `trap confirmed`, you still hold the lane until you call `deck clear`. Departures and the next recovery wait for that call.
- **Emergency while parked.** An emergency or check-in from an aircraft that is on deck and has never been seen airborne enrols it, but the next snapshot closes it with `you are not airborne, recovery not required, check in when airborne`.

### Recovery examples (test fixture)

CASE I:

```text
YOU:   Roosevelt marshal, Viper one one, checking in
REPLY: viper 1 1, Roosevelt marshal, CASE one recovery, BRC three five three, hold overhead at angels two, altimeter two nine nine two, read back angels and altimeter
YOU:   Roosevelt marshal, Viper one one, readback angels two, altimeter two niner niner two
REPLY: viper 1 1, Roosevelt marshal, readback correct, report established
YOU:   Roosevelt marshal, Viper one one, established
REPLY: viper 1 1, Roosevelt marshal, roger established, number one, Charlie, BRC three five three, report commencing
YOU:   Roosevelt marshal, Viper one one, commencing
REPLY: viper 1 1, Roosevelt marshal, roger commencing, report initial
YOU:   Roosevelt tower, Viper one one, initial
REPLY: viper 1 1, Roosevelt marshal, roger initial, report the break
YOU:   Roosevelt tower, Viper one one, break
REPLY: viper 1 1, Roosevelt marshal, roger break, report abeam
YOU:   Roosevelt tower, Viper one one, abeam
REPLY: viper 1 1, Roosevelt paddles, roger abeam, call the ball
YOU:   Roosevelt paddles, Viper one one, ball, five point six
REPLY: viper 1 1, Roosevelt paddles, roger ball
(touchdown, then mission confirms on deck)
EVENT: viper 1 1, Roosevelt tower, trap confirmed, clear the landing area, report deck clear
YOU:   Roosevelt tower, Viper one one, deck clear
REPLY: viper 1 1, Roosevelt tower, copy deck clear, recovery complete
```

CASE III (approach time 12:05 when checking in at 12:00):

```text
YOU:   Roosevelt marshal, Viper one one, checking in
REPLY: viper 1 1, Roosevelt marshal, CASE three recovery, final bearing three four four, marshal on the one six four radial, two one DME, angels six, expected approach time zero five, altimeter two nine nine two, read back radial, DME, angels, approach time and altimeter
YOU:   Roosevelt marshal, Viper one one, readback one six four radial, two one DME, angels six, approach time zero five, altimeter two niner niner two
REPLY: viper 1 1, Roosevelt marshal, readback correct, report established
YOU:   Roosevelt marshal, Viper one one, established
REPLY: viper 1 1, Roosevelt marshal, roger established, number one
EVENT: viper 1 1, Roosevelt marshal, commence, final bearing three four four, report commencing
YOU:   Roosevelt marshal, Viper one one, commencing
REPLY: viper 1 1, Roosevelt marshal, roger commencing, report platform
YOU:   Roosevelt approach, Viper one one, platform
REPLY: viper 1 1, Roosevelt marshal, roger platform, report ten miles
YOU:   Roosevelt approach, Viper one one, ten miles
REPLY: viper 1 1, Roosevelt paddles, roger, call the ball
YOU:   Roosevelt paddles, Viper one one, ball, five point six
REPLY: viper 1 1, Roosevelt paddles, roger ball
```

CASE II uses the CASE III marshal and readback, then `platform` → `roger platform, report initial` (or `initial` straight after commencing) and the CASE I `initial` / `break` / `abeam` / `ball` sequence.

## Source references

| Behaviour | Source |
|---|---|
| Command table, earliest-match, negation, cancel and wave-off rewrite, startup flag, readback text | `overlord/carrier_parser.py` 17-49, 86-136 |
| Identity and SRS name guards | `overlord/identities.py` 7-28, 35-53 |
| Radio routing, stale link, wrong carrier guard | `overlord/carrier.py` 982-1058 |
| Intent dispatch, enrolment, launch request | `overlord/carrier.py` 1060-1141 |
| Status replies per phase | `overlord/carrier.py` 1143-1175 |
| Readback validator | `overlord/carrier.py` 136-137, 342-384, 1189-1197; `overlord/jtac.py` 152-208 |
| Established, pattern, ball, clara, wave-off, bolter, trap, deck clear, airborne, handoff | `overlord/carrier.py` 1199-1317 |
| Mission observation, recycle, trap, foul deck, launch | `overlord/carrier.py` 607-722 |
| Assignment, scheduling, lane grant, revocation | `overlord/carrier.py` 726-869 |
| Stale link, lifecycle release, session transfer | `overlord/carrier.py` 447-460, 531-605; `overlord/brain.py` 160-181; `overlord/airfield.py` 1475-1476 |
| Exercised phrases | `tests/test_carrier.py` 120-141, 270-606, 734-801 |
