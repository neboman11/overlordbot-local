# JTAC radio calls: what you say to Axeman

This guide lists the exact transmissions the live JTAC controller (`overlord/jtac.py`, routed by `overlord/brain.py`) accepts on its own SRS frequency, and what it says back. It was traced from source after `2e6b83c`. It describes what this implementation does. It is not real-world JTAC, JFIRE or NATO close air support procedure, and it adds no phrase the code does not accept.

There is one chart. It covers the whole procedure in a single connected flow: check in, task selection, nine-line brief, line repeats, readback and retries, contact, marks and laser (pending, acknowledged, failed, terminated), inbound call and clearance, off target, BDA, abort, laser off, status, check out, and the mission changes that silently reset your progress.

Validation manifest: [`jtac-calls.json`](jtac-calls.json). It gives sample values for every `YOU` node, taken from the test fixture `tests/test_jtac.py`. `tests/test_radio_call_docs.py` parses every call with the real JTAC grammar and replays the main path, refusals included, against the controller to check the replies printed here.

## How to read the chart

**Path colors:** green solid = normal progression; red = refusal/error; amber dashed = retry, recovery or cancellation; gray = optional status/shared checks. Calls and conditions remain explicit: color is not the only cue.

| Shape | Meaning |
|---|---|
| `YOU:` followed by **bold words** (rectangle) | **Your transmission.** Say only the words after `YOU:`, replacing placeholders. Do not say `YOU:` or the brackets. |
| `ID(["REPLY: …"])` (stadium) | Axeman's answer to your call. It is spoken as `[CALLSIGN], Axeman, <message>`, for example `viper 1 1, Axeman, readback correct, report contact`. |
| `ID(["EVENT: …"])` (stadium) | A transmission Axeman makes unprompted when the mission acknowledges a mark, laser or laser-off, or switches a spot off on its own. |
| `ID{{"WAIT/FLY — NO CALL: …"}}` (hexagon) | Nothing to say. The mission bridge, not your words, moves the procedure on. `NO TRANSMISSION` hexagons change your state silently. |
| `ID(("…"))` (circle) | Entry or exit. |
| Dotted arrow `-.->` | RETRY or RETURN: fix the cause, then make the call the arrow points to. |

Pilot claims never replace mission state. `contact` is required before clearance but is your report, not a check of what you see. `cleared hot` said by you is not a command and gets `say again`. Marks and spots count only once the mission acknowledges the real DCS result.

## Operator rules (apply to every call)

1. **Call shape:** `Axeman, [CALLSIGN], <one command>`. Open with the JTAC callsign. Up to three words are tried as the receiver, and a close phonetic match is accepted (`jtac.py` 474-483). The callsign is the server's `JTAC_CALLSIGN` setting, `Axeman` by default; replace it if your server uses another. `this is` / `from` after the callsign are skipped. Then your flight callsign (`Viper one one`; a modex is **not** accepted here), then **one** command phrase. The command phrase that starts earliest wins, and on a tie the earlier entry of the grammar list wins (`jtac.py` 114-143, 502-505). Do not chain two commands.
2. **Frequency and coalition.** Axeman only answers on its own SRS channel (default 252.000 MHz AM, coalition 2) and only to aircraft of that coalition. Anything else gets no reply (`jtac.py` 838-848).
3. **Identity.** Your callsign must match exactly one live unit of your coalition. Otherwise: `say again your callsign`, `i could not fully recognize your callsign`, `i could not find you on scope` or `I found more than one unit with a matching callsign` (`domain.py` 1172-1198).
4. **Check in first.** Every procedure call before `checking in` gets `unable, check in first`. `checking out` works with or without a session. Checking in **again** closes your old session first: your task, brief and readback are dropped (`jtac.py` 879-887).
5. **Negation voids positive calls.** In `checking in`, `request task`, `ready for brief`, `readback`, `contact`, `in`, `request mark/smoke/sparkle/illumination` and `request laser`, and `off target`, any `no`, `not`, `negative`, `dont`, `do not`, `unable`, `lost`, `stop`, `cease` or `terminate` makes the call `say again` (`jtac.py` 146-151, 520-526). `negative contact` is not a report. For a readback, only the words before `readback` are checked.
6. **Abort and cancel.** `abort` cancels contact and clearance and switches off any spot of your task. An `abort`, `cancel` or `belay` anywhere in a positive call turns the whole call into an abort (`request laser, abort`). `cancel` / `belay` alone is **not** a command (`say again`). `request laser off`, `cancel the laser`, `negative laser`, `terminate laser`, `no laser` are all **laser off**, never a laser request (`jtac.py` 127-129, 520-521).
7. **Readbacks start with the word `readback`.** `roger` or `copy` is not a readback. Read back only the lines named after `read back` in the brief, in brief order, with nothing extra. See [Readback dictionary](#readback-dictionary).
8. **Live mission link required.** If the mission bridge snapshot is missing or older than `DCS_STALE_SECONDS` (10 s default), every call except `checking out`, `abort` and `laser off` gets `unable, no live mission link`. `abort` and `laser off` still cancel locally and end `…, no live mission link` (`jtac.py` 869-878). If Tacview telemetry is stale, the brain gives **no reply at all** except to `abort`, `laser off` and `checking out` (`brain.py` 191-193).
9. **A stale gap or a lost target/controller resets you to `awaiting brief`.** Clearance, contact, readback and acknowledged marks are dropped and spots are switched off, without any transmission. Ask for the brief again, read back, report contact (`jtac.py` 747-775).
10. **Marks and laser are pending until acknowledged.** `stand by, … pending` only means the request is queued. The mission must acknowledge the DCS result within 15 s, and Axeman then transmits an EVENT. A required mark is satisfied by an acknowledged smoke or illumination, or your own spot still live (`jtac.py` 86, 623-648, 777-779). See [Marks and laser](#marks-and-laser).
11. **Local intent fallback.** With an LLM configured, an unrecognised call may be rewritten only into `ready for brief`, `request BDA` or `say status`. Everything else needs the phrases below (`brain.py` 194-213).

## Placeholder dictionary

Sample values come from the test fixture `tests/test_jtac.py` (player `Viper 1-1`, task `one`, `armor column`, brief type two, IP Dog, heading 270 offset left, 6.5 nm, elevation 450 ft, location `KM 1234`, laser code 1688, marks laser and smoke, readback elevation and location, attack heading 240-300, mark required). The real mission values come from `dcs/jtac-config.example.lua` style configuration.

| Placeholder | Pilot says | Sample | Source |
|---|---|---|---|
| `[CALLSIGN]` | Your flight callsign, digits spoken separately | `Viper one one` | `jtac.py` 528; `domain.py` 1172-1198 |
| `[TASK]` | Task number from the task list, 1-99 | `one` | `jtac.py` 123-126, 530-531 |
| `[LINE_NUMBER]` | Brief line one to nine (`remarks` has its own call) | `four` | `jtac.py` 119, 532-537 |
| `[READBACK_VALUES]` | The values of the lines named after `read back`, in brief order | `elevation four five zero, kilo mike one two three four` | `jtac.py` 227-244 |
| `[MARK_KIND]` | `smoke`, `sparkle` (or `IR pointer`), `illumination`, or `mark` to let Axeman pick | `smoke` | `jtac.py` 111-112, 131-132, 1047-1048 |
| `[ATTACK_HEADING]` | Your inbound heading, up to three digits | `two seven zero` | `jtac.py` 538-547 |

Reply-only placeholders (you never say these):

| Placeholder | Axeman says |
|---|---|
| `[TASK_LIST]` | `task <n>, <name>` for each live task of your coalition no other pilot holds, comma separated, ending `and <n> more` past 600 characters (`jtac.py` 901-910) |
| `[TASK_NAME]` | Mission-authored task name, for example `armor column` |
| `[CONTROL_TYPE]` | `one`, `two` or `three` |
| `[IP]`, `[OFFSET]`, `[DESCRIPTION]`, `[LOCATION]`, `[MARK]`, `[FRIENDLIES]`, `[EGRESS]`, `[REMARKS]` | Mission-authored brief text, verbatim. Offset and remarks are optional |
| `[HEADING]`, `[DISTANCE]`, `[ELEVATION]` | Brief numbers spoken digit by digit, for example `two seven zero`, `six point five`, `four five zero` (`jtac.py` 247-249) |
| `[READBACK_LINES]` | The lines you must read back, for example `line four, line six` (`remarks` for line ten) |
| `[LINE_TEXT]` | One brief line as Axeman speaks it, for example `line four, elevation four five zero feet`, `line six, KM 1234` (`jtac.py` 942-962) |
| `[MISSING_LINE]` | `control type` or `line <n>`: the first required line the mission did not author |
| `[MARK_NAME]` | `smoke`, `illumination` or `sparkle` (IR). `request mark` picks the first non-laser mark the task offers, else the laser |
| `[LASER_CODE]` | Four digits, for example `one six eight eight` |
| `[REASON]` | The mission's failure reason, or the status `failed`, `rejected`, `expired`, or `spot not active` |
| `[NO_CLEARANCE_REASON]` | See [Clearance conditions](#clearance-conditions) |
| `[REMAINING]`, `[TOTAL]` | Target elements still alive / authored element count |
| `[PHASE]` | `awaiting brief`, `awaiting readback` or `readback complete` |

## Chart — JTAC procedure: check in to check out

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
  J_START(("START: human aircraft in the mission, radio on the JTAC frequency, mission bridge live"))
  J_END(("END: JTAC session closed"))

  %% Guards on EVERY call below: nothing changes, fix the cause and RETRY the same call
    J_G_HUB{{"Any YOU call in this chart can first meet one of these"}}
    J_G_SILENT{{"NO REPLY: call not opened with Axeman, not on the JTAC frequency, other coalition, or Tacview telemetry stale — while telemetry is stale only abort, laser off and checking out are answered"}}
    J_G_ID(["REPLY: say again your callsign — addressed to station calling — or — i could not fully recognize your callsign — or — i could not find you on scope — or — I found more than one unit with a matching callsign"])
    J_G_STALE(["REPLY: unable, no live mission link — the mission bridge snapshot is missing or older than the stale limit, 10 s by default"])
    J_G_NONE(["REPLY: say again — no command phrase, a positive call voided by a negation, or a bare cancel — or — unable, say again using standard phraseology — only with the local intent fallback configured"])
    J_G_NOSESSION(["REPLY: unable, check in first"])

  %% 1 · Check in
    J_CHECKIN["`YOU: **Axeman, [CALLSIGN], checking in**`"]
    J_CHECKIN_R(["REPLY: checked in, available [TASK_LIST], request task when ready"])
    J_CHECKIN_NONE(["REPLY: checked in, no tasks available"])
    J_W_TASKS{{"WAIT — NO CALL: the mission must publish a live task for your coalition that no other pilot holds — no announcement is made"}}

  %% 2 · Task selection
    J_REQ_TASK["`YOU: **Axeman, [CALLSIGN], request task**`"]
    J_REQ_TASK_N["`YOU: **Axeman, [CALLSIGN], request task [TASK]**`"]
    J_TASK_R(["REPLY: task [TASK], [TASK_NAME], advise ready for brief"])
    J_TASK_PICK(["REPLY: say task number, available [TASK_LIST]"])
    J_TASK_NA(["REPLY: unable, task [TASK] not available"])
    J_TASK_NONE(["REPLY: no tasks available"])

  %% 3 · Nine-line brief
    J_BRIEF["`YOU: **Axeman, [CALLSIGN], ready for brief**`"]
    J_BRIEF_R(["REPLY: type [CONTROL_TYPE] control, line one, IP [IP], line two, heading [HEADING], [OFFSET], line three, distance [DISTANCE], line four, elevation [ELEVATION] feet, line five, [DESCRIPTION], line six, [LOCATION], line seven, mark [MARK], line eight, friendlies [FRIENDLIES], line nine, egress [EGRESS], remarks, [REMARKS], read back [READBACK_LINES] — the offset and remarks are spoken only when the mission authored them"])
    J_BRIEF_NORB(["REPLY: type [CONTROL_TYPE] control, line one, IP [IP], … line nine, egress [EGRESS], report contact — task authored with no readback lines: the readback step is already complete"])
    J_BRIEF_LONG(["REPLY: brief exceeds one transmission, request lines one through nine individually, then read back [READBACK_LINES] — or — brief exceeds one transmission, request lines one through nine individually, then report contact"])
    J_BRIEF_NOTASK(["REPLY: unable, request task first"])
    J_BRIEF_DEAD(["REPLY: unable, target or controller not available"])
    J_BRIEF_INC(["REPLY: unable, task brief incomplete, missing [MISSING_LINE]"])

  %% 4 · Repeat a brief line
    J_REPEAT["`YOU: **Axeman, [CALLSIGN], say again line [LINE_NUMBER]**`"]
    J_REPEAT_RMK["`YOU: **Axeman, [CALLSIGN], say again remarks**`"]
    J_REPEAT_R(["REPLY: [LINE_TEXT] — for example line four, elevation four five zero feet — or — remarks, [REMARKS] — or — no remarks"])
    J_REPEAT_NUM(["REPLY: say line number"])
    J_NOBRIEF(["REPLY: unable, no brief issued"])

  %% 5 · Readback
    J_RB["`YOU: **Axeman, [CALLSIGN], readback [READBACK_VALUES]**`"]
    J_RB_OK(["REPLY: readback correct, report contact"])
    J_RB_BAD(["REPLY: negative readback, [LINE_TEXT] — names the first line that did not match and repeats it"])

  %% 6 · Contact
    J_CONTACT["`YOU: **Axeman, [CALLSIGN], contact**`"]
    J_CONTACT_HDG(["REPLY: copy contact, call in with heading"])
    J_CONTACT_R(["REPLY: copy contact, call in"])
    J_CONTACT_NORB(["REPLY: unable, readback required"])

  %% 7 · Marks and laser: pending until the mission acknowledges the DCS result
    J_MARK["`YOU: **Axeman, [CALLSIGN], request [MARK_KIND]**`"]
    J_LASER["`YOU: **Axeman, [CALLSIGN], request laser**`"]
    J_MARK_PEND(["REPLY: stand by, [MARK_NAME] pending"])
    J_LASER_PEND(["REPLY: stand by, laser code [LASER_CODE] pending"])
    J_MARK_LIVE(["REPLY: laser on, code [LASER_CODE] — or — sparkle on the target — your own spot is already live, nothing new is sent"])
    J_MARK_NA(["REPLY: unable, [MARK_NAME] not available for this task — or — unable, mark not available for this task — or — unable, no laser code assigned"])
    J_W_ACK{{"WAIT — NO CALL: the mission runs the mark or laser and acknowledges it — the request lapses after 15 s without an acknowledgement"}}
    J_EV_LASER(["EVENT: laser on, code [LASER_CODE]"])
    J_EV_SPARKLE(["EVENT: sparkle on the target"])
    J_EV_MARK(["EVENT: [MARK_NAME] on the target — smoke or illumination"])
    J_EV_FAIL(["EVENT: unable laser, [REASON] — or — unable sparkle, [REASON] — or — unable mark, [REASON] — or — unable laser, no acknowledgement — or — unable mark, no acknowledgement"])
    J_EV_TERM(["EVENT: spot terminated — or — sparkle terminated — the mission switched your spot off: target or controller lost, or its lease expired"])

  %% 8 · Inbound and clearance
    J_IN_HDG["`YOU: **Axeman, [CALLSIGN], in heading [ATTACK_HEADING]**`"]
    J_IN["`YOU: **Axeman, [CALLSIGN], in**`"]
    J_CLEARED(["REPLY: cleared hot — or — cleared to engage — type three control"])
    J_NOCLR(["REPLY: continue, no clearance, [NO_CLEARANCE_REASON]"])
    J_FLY{{"FLY — NO CALL: run the attack inside the briefed restrictions"}}
    J_X_CLR{{"NO TRANSMISSION: the acknowledged mark or own live spot this mark-required clearance depended on is gone — the clearance is withdrawn silently"}}

  %% 9 · Off target and BDA
    J_OFF["`YOU: **Axeman, [CALLSIGN], off target**`"]
    J_OFF_R(["REPLY: copy off, request BDA when ready"])
    J_OFF_NOCLR(["REPLY: copy off, no clearance was active"])
    J_BDA["`YOU: **Axeman, [CALLSIGN], request BDA**`"]
    J_BDA_KILL(["REPLY: BDA, target destroyed"])
    J_BDA_REM(["REPLY: BDA, [REMAINING] of [TOTAL] target elements remain"])
    J_BDA_UNC(["REPLY: BDA unconfirmed, target not observed"])
    J_NOTASK(["REPLY: unable, no task assigned"])

  %% Mission changes: no call, no transmission
    J_X_LOST{{"NO TRANSMISSION: target or controller lost, or the bridge goes stale — clearance, contact, readback and marks are dropped, spots switched off, the task is kept awaiting a new brief"}}
    J_X_TASK{{"NO TRANSMISSION: the mission withdraws the task — your session stays checked in with no task"}}
    J_X_CLOSED{{"NO TRANSMISSION: your aircraft leaves the mission, a new mission epoch or telemetry generation starts, or one hour passes without a call — session closed"}}

  %% At any checked-in point: status, abort, laser off, check out
    J_ANY{{"At any enrolled point of this chart"}}
    J_STATUS["`YOU: **Axeman, [CALLSIGN], say status**`"]
    J_STATUS_R(["REPLY: task [TASK], [TASK_NAME], [PHASE] — then, as they apply: contact, cleared, laser on code [LASER_CODE] or laser pending, sparkle on, target not active, controller not available"])
    J_STATUS_NOTASK(["REPLY: checked in, no task assigned"])
    J_ABORT["`YOU: **Axeman, [CALLSIGN], abort**`"]
    J_ABORT_R(["REPLY: copy abort — or — copy abort, laser off pending — contact and clearance cancelled, brief and readback kept"])
    J_LOFF["`YOU: **Axeman, [CALLSIGN], laser off**`"]
    J_LOFF_PEND(["REPLY: copy, laser off pending"])
    J_LOFF_OFF(["REPLY: laser is off"])
    J_EV_LOFF(["EVENT: laser off — or — unable laser off, [REASON] — usually followed by spot terminated or sparkle terminated for each spot that went off"])
    J_STALE_CXL(["REPLY: copy abort, no live mission link — or — copy abort, laser off pending, no live mission link — or — copy, laser off pending, no live mission link — or — laser is off, no live mission link — or — unable, no task assigned, no live mission link — cancellation still applies locally"])
    J_CHECKOUT["`YOU: **Axeman, [CALLSIGN], checking out**`"]
    J_CHECKOUT_R(["REPLY: copy, checked out"])

  J_START edge_J_START__J_G_HUB@--> J_G_HUB
  J_G_HUB edge_J_G_HUB__J_G_SILENT@--> J_G_SILENT
  J_G_HUB edge_J_G_HUB__J_G_ID@--> J_G_ID
  J_G_HUB edge_J_G_HUB__J_G_STALE@--> J_G_STALE
  J_G_HUB edge_J_G_HUB__J_G_NONE@--> J_G_NONE
  J_G_HUB edge_J_G_HUB__J_G_NOSESSION@--> J_G_NOSESSION
  J_G_SILENT edge_J_G_SILENT__J_START@-.->|"RETRY: Axeman first, JTAC frequency,<br/>wait for telemetry"| J_START
  J_G_ID edge_J_G_ID__J_START@-.->|"RETRY: say your full callsign"| J_START
  J_G_STALE edge_J_G_STALE__J_START@-.->|"RETRY when the mission link returns"| J_START
  J_G_NONE edge_J_G_NONE__J_START@-.->|"RETRY with one listed command phrase"| J_START
  J_G_NOSESSION edge_J_G_NOSESSION__J_CHECKIN@-.->|"check in first"| J_CHECKIN
  J_START edge_J_START__J_CHECKIN@--> J_CHECKIN
  J_CHECKIN edge_J_CHECKIN__J_CHECKIN_R@-->|"live unheld task for your coalition"| J_CHECKIN_R
  J_CHECKIN_R edge_J_CHECKIN_R__J_REQ_TASK@--> J_REQ_TASK
  J_CHECKIN_R edge_J_CHECKIN_R__J_REQ_TASK_N@-->|"pick one from the list"| J_REQ_TASK_N
  J_CHECKIN edge_J_CHECKIN__J_CHECKIN_NONE@-->|"no live unheld task"| J_CHECKIN_NONE
  J_CHECKIN_NONE edge_J_CHECKIN_NONE__J_W_TASKS@--> J_W_TASKS
  J_W_TASKS edge_J_W_TASKS__J_REQ_TASK@-.->|"later: ask for a task"| J_REQ_TASK
  J_REQ_TASK edge_J_REQ_TASK__J_TASK_R@-->|"exactly one task available"| J_TASK_R
  J_REQ_TASK_N edge_J_REQ_TASK_N__J_TASK_R@-->|"that task is live and unheld"| J_TASK_R
  J_REQ_TASK edge_J_REQ_TASK__J_TASK_PICK@-->|"several tasks available"| J_TASK_PICK
  J_TASK_PICK edge_J_TASK_PICK__J_REQ_TASK_N@-.->|"RETRY naming the number"| J_REQ_TASK_N
  J_REQ_TASK edge_J_REQ_TASK__J_TASK_NONE@-->|"none available"| J_TASK_NONE
  J_TASK_NONE edge_J_TASK_NONE__J_W_TASKS@-.-> J_W_TASKS
  J_REQ_TASK_N edge_J_REQ_TASK_N__J_TASK_NA@-->|"unknown, dead, other coalition<br/>or held by another pilot"| J_TASK_NA
  J_TASK_NA edge_J_TASK_NA__J_REQ_TASK@-.->|"RETRY: hear the list again"| J_REQ_TASK
  J_TASK_R edge_J_TASK_R__J_BRIEF@--> J_BRIEF
  J_BRIEF edge_J_BRIEF__J_BRIEF_R@-->|"task has readback lines"| J_BRIEF_R
  J_BRIEF edge_J_BRIEF__J_BRIEF_NORB@-->|"task has no readback lines"| J_BRIEF_NORB
  J_BRIEF edge_J_BRIEF__J_BRIEF_LONG@-->|"brief longer than one transmission"| J_BRIEF_LONG
  J_BRIEF edge_J_BRIEF__J_BRIEF_NOTASK@-->|"no task assigned"| J_BRIEF_NOTASK
  J_BRIEF_NOTASK edge_J_BRIEF_NOTASK__J_REQ_TASK@-.-> J_REQ_TASK
  J_BRIEF edge_J_BRIEF__J_BRIEF_DEAD@-->|"target or controller not alive"| J_BRIEF_DEAD
  J_BRIEF_DEAD edge_J_BRIEF_DEAD__J_BDA@-.->|"find out what happened"| J_BDA
  J_BRIEF_DEAD edge_J_BRIEF_DEAD__J_REQ_TASK_N@-.->|"choose another task"| J_REQ_TASK_N
  J_BRIEF edge_J_BRIEF__J_BRIEF_INC@-->|"mission omitted a required line"| J_BRIEF_INC
  J_BRIEF_INC edge_J_BRIEF_INC__J_REQ_TASK_N@-.->|"choose another task"| J_REQ_TASK_N
  J_BRIEF_R edge_J_BRIEF_R__J_RB@--> J_RB
  J_BRIEF_R edge_J_BRIEF_R__J_REPEAT@-->|"missed a line"| J_REPEAT
  J_BRIEF_R edge_J_BRIEF_R__J_REPEAT_RMK@-->|"missed the remarks"| J_REPEAT_RMK
  J_BRIEF_NORB edge_J_BRIEF_NORB__J_CONTACT@--> J_CONTACT
  J_BRIEF_LONG edge_J_BRIEF_LONG__J_REPEAT@-->|"ask for each line, one to nine"| J_REPEAT
  J_BRIEF_LONG edge_J_BRIEF_LONG__J_REPEAT_RMK@-->|"then the remarks"| J_REPEAT_RMK
  J_REPEAT edge_J_REPEAT__J_REPEAT_R@-->|"line one to nine"| J_REPEAT_R
  J_REPEAT_RMK edge_J_REPEAT_RMK__J_REPEAT_R@--> J_REPEAT_R
  J_REPEAT edge_J_REPEAT__J_REPEAT_NUM@-->|"no number, or not one to nine"| J_REPEAT_NUM
  J_REPEAT_NUM edge_J_REPEAT_NUM__J_REPEAT@-.-> J_REPEAT
  J_REPEAT edge_J_REPEAT__J_NOBRIEF@-->|"no brief issued yet"| J_NOBRIEF
  J_REPEAT_RMK edge_J_REPEAT_RMK__J_NOBRIEF@-->|"no brief issued yet"| J_NOBRIEF
  J_NOBRIEF edge_J_NOBRIEF__J_BRIEF@-.-> J_BRIEF
  J_REPEAT_R edge_J_REPEAT_R__J_RB@-.->|"RETURN: read back"| J_RB
  J_RB edge_J_RB__J_RB_OK@-->|"every readback value, in brief order, nothing extra"| J_RB_OK
  J_RB edge_J_RB__J_RB_BAD@-->|"first value wrong, missing or extra"| J_RB_BAD
  J_RB_BAD edge_J_RB_BAD__J_RB@-.->|"RETRY: read back the whole set again"| J_RB
  J_RB edge_J_RB__J_NOBRIEF@-->|"no brief issued yet"| J_NOBRIEF
  J_RB_OK edge_J_RB_OK__J_CONTACT@--> J_CONTACT
  J_RB_OK edge_J_RB_OK__J_MARK@-->|"mark first, optional"| J_MARK
  J_RB_OK edge_J_RB_OK__J_LASER@-->|"laser first, optional"| J_LASER
  J_CONTACT edge_J_CONTACT__J_CONTACT_HDG@-->|"task has an attack heading window"| J_CONTACT_HDG
  J_CONTACT edge_J_CONTACT__J_CONTACT_R@-->|"no attack heading window"| J_CONTACT_R
  J_CONTACT edge_J_CONTACT__J_CONTACT_NORB@-->|"readback not complete"| J_CONTACT_NORB
  J_CONTACT_NORB edge_J_CONTACT_NORB__J_RB@-.->|"brief still current: read back"| J_RB
  J_CONTACT_NORB edge_J_CONTACT_NORB__J_BRIEF@-.->|"brief dropped: new brief"| J_BRIEF
  J_CONTACT_HDG edge_J_CONTACT_HDG__J_IN_HDG@--> J_IN_HDG
  J_CONTACT_R edge_J_CONTACT_R__J_IN@--> J_IN
  J_CONTACT_HDG edge_J_CONTACT_HDG__J_MARK@-->|"task requires a mark"| J_MARK
  J_CONTACT_HDG edge_J_CONTACT_HDG__J_LASER@-->|"task requires a mark"| J_LASER
  J_CONTACT_R edge_J_CONTACT_R__J_MARK@-->|"task requires a mark"| J_MARK
  J_CONTACT_R edge_J_CONTACT_R__J_LASER@-->|"task requires a mark"| J_LASER
  J_MARK edge_J_MARK__J_MARK_PEND@-->|"smoke, illumination or sparkle offered"| J_MARK_PEND
  J_LASER edge_J_LASER__J_LASER_PEND@-->|"laser offered with a code"| J_LASER_PEND
  J_MARK edge_J_MARK__J_MARK_LIVE@-->|"your sparkle is already on"| J_MARK_LIVE
  J_LASER edge_J_LASER__J_MARK_LIVE@-->|"your laser is already on"| J_MARK_LIVE
  J_MARK edge_J_MARK__J_MARK_NA@-->|"that mark not offered by the task"| J_MARK_NA
  J_LASER edge_J_LASER__J_MARK_NA@-->|"no laser or no code"| J_MARK_NA
  J_MARK edge_J_MARK__J_NOBRIEF@-->|"no brief issued"| J_NOBRIEF
  J_LASER edge_J_LASER__J_NOBRIEF@-->|"no brief issued"| J_NOBRIEF
  J_MARK_NA edge_J_MARK_NA__J_MARK@-.->|"RETRY another mark from line seven"| J_MARK
  J_MARK_PEND edge_J_MARK_PEND__J_W_ACK@--> J_W_ACK
  J_LASER_PEND edge_J_LASER_PEND__J_W_ACK@--> J_W_ACK
  J_W_ACK edge_J_W_ACK__J_EV_LASER@-->|"laser on"| J_EV_LASER
  J_W_ACK edge_J_W_ACK__J_EV_SPARKLE@-->|"IR pointer on"| J_EV_SPARKLE
  J_W_ACK edge_J_W_ACK__J_EV_MARK@-->|"smoke or illumination placed"| J_EV_MARK
  J_W_ACK edge_J_W_ACK__J_EV_FAIL@-->|"failed, rejected, expired or<br/>no acknowledgement in 15 s"| J_EV_FAIL
  J_EV_FAIL edge_J_EV_FAIL__J_MARK@-.->|"RETRY"| J_MARK
  J_EV_FAIL edge_J_EV_FAIL__J_LASER@-.->|"RETRY"| J_LASER
  J_EV_LASER edge_J_EV_LASER__J_CONTACT@-->|"contact not yet reported"| J_CONTACT
  J_EV_SPARKLE edge_J_EV_SPARKLE__J_CONTACT@-->|"contact not yet reported"| J_CONTACT
  J_EV_MARK edge_J_EV_MARK__J_CONTACT@-->|"contact not yet reported"| J_CONTACT
  J_EV_LASER edge_J_EV_LASER__J_IN_HDG@-->|"contact reported"| J_IN_HDG
  J_EV_SPARKLE edge_J_EV_SPARKLE__J_IN_HDG@-->|"contact reported"| J_IN_HDG
  J_EV_MARK edge_J_EV_MARK__J_IN_HDG@-->|"contact reported"| J_IN_HDG
  J_EV_LASER edge_J_EV_LASER__J_IN@-->|"contact reported, no window"| J_IN
  J_EV_SPARKLE edge_J_EV_SPARKLE__J_IN@-->|"contact reported, no window"| J_IN
  J_EV_MARK edge_J_EV_MARK__J_IN@-->|"contact reported, no window"| J_IN
  J_MARK_LIVE edge_J_MARK_LIVE__J_IN_HDG@--> J_IN_HDG
  J_MARK_LIVE edge_J_MARK_LIVE__J_IN@-->|"no window"| J_IN
  J_EV_LASER edge_J_EV_LASER__J_EV_TERM@-->|"mission ends the spot"| J_EV_TERM
  J_EV_SPARKLE edge_J_EV_SPARKLE__J_EV_TERM@-->|"mission ends the spot"| J_EV_TERM
  J_EV_TERM edge_J_EV_TERM__J_LASER@-.->|"RETRY the laser"| J_LASER
  J_EV_TERM edge_J_EV_TERM__J_MARK@-.->|"RETRY a mark"| J_MARK
  J_IN_HDG edge_J_IN_HDG__J_CLEARED@-->|"readback, contact, heading in<br/>window, mark confirmed if required"| J_CLEARED
  J_IN edge_J_IN__J_CLEARED@-->|"readback, contact, no window,<br/>mark confirmed if required"| J_CLEARED
  J_IN_HDG edge_J_IN_HDG__J_NOCLR@-->|"any prerequisite missing"| J_NOCLR
  J_IN edge_J_IN__J_NOCLR@-->|"any prerequisite missing"| J_NOCLR
  J_NOCLR edge_J_NOCLR__J_CONTACT@-.->|"report contact"| J_CONTACT
  J_NOCLR edge_J_NOCLR__J_IN_HDG@-.->|"say heading — or — fly inside the window"| J_IN_HDG
  J_NOCLR edge_J_NOCLR__J_MARK@-.->|"mark not confirmed"| J_MARK
  J_NOCLR edge_J_NOCLR__J_LASER@-.->|"mark not confirmed"| J_LASER
  J_NOCLR edge_J_NOCLR__J_RB@-.->|"readback required"| J_RB
  J_NOCLR edge_J_NOCLR__J_REQ_TASK@-.->|"no task assigned"| J_REQ_TASK
  J_CLEARED edge_J_CLEARED__J_FLY@--> J_FLY
  J_CLEARED edge_J_CLEARED__J_X_CLR@-->|"required mark ends"| J_X_CLR
  J_X_CLR edge_J_X_CLR__J_MARK@-.->|"new mark, then call in again"| J_MARK
  J_FLY edge_J_FLY__J_OFF@--> J_OFF
  J_OFF edge_J_OFF__J_OFF_R@-->|"clearance was active"| J_OFF_R
  J_OFF edge_J_OFF__J_OFF_NOCLR@-->|"no active clearance"| J_OFF_NOCLR
  J_OFF_R edge_J_OFF_R__J_BDA@--> J_BDA
  J_OFF_NOCLR edge_J_OFF_NOCLR__J_BDA@--> J_BDA
  J_BDA edge_J_BDA__J_BDA_KILL@-->|"every element has DCS death evidence"| J_BDA_KILL
  J_BDA edge_J_BDA__J_BDA_REM@-->|"elements remain"| J_BDA_REM
  J_BDA edge_J_BDA__J_BDA_UNC@-->|"none remain, deaths not confirmed"| J_BDA_UNC
  J_BDA edge_J_BDA__J_NOTASK@-->|"no task assigned"| J_NOTASK
  J_NOTASK edge_J_NOTASK__J_REQ_TASK@-.-> J_REQ_TASK
  J_BDA_REM edge_J_BDA_REM__J_IN_HDG@-->|"reattack: contact still stands"| J_IN_HDG
  J_BDA_REM edge_J_BDA_REM__J_IN@-->|"reattack, no window"| J_IN
  J_BDA_KILL edge_J_BDA_KILL__J_REQ_TASK_N@-->|"next task"| J_REQ_TASK_N
  J_BDA_KILL edge_J_BDA_KILL__J_CHECKOUT@--> J_CHECKOUT
  J_BDA_UNC edge_J_BDA_UNC__J_REQ_TASK_N@-->|"next task"| J_REQ_TASK_N
  J_BDA_UNC edge_J_BDA_UNC__J_CHECKOUT@--> J_CHECKOUT
  J_BRIEF_R edge_J_BRIEF_R__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_RB_OK edge_J_RB_OK__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_CONTACT_HDG edge_J_CONTACT_HDG__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_CONTACT_R edge_J_CONTACT_R__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_CLEARED edge_J_CLEARED__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_EV_LASER edge_J_EV_LASER__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_EV_SPARKLE edge_J_EV_SPARKLE__J_X_LOST@-->|"target, controller or link lost"| J_X_LOST
  J_X_LOST edge_J_X_LOST__J_BDA@-->|"target destroyed"| J_BDA
  J_X_LOST edge_J_X_LOST__J_BRIEF@-.->|"still alive: new brief, readback, contact"| J_BRIEF
  J_TASK_R edge_J_TASK_R__J_X_TASK@-->|"task withdrawn"| J_X_TASK
  J_RB_OK edge_J_RB_OK__J_X_TASK@-->|"task withdrawn"| J_X_TASK
  J_X_TASK edge_J_X_TASK__J_REQ_TASK@-.-> J_REQ_TASK
  J_ANY edge_J_ANY__J_X_CLOSED@--> J_X_CLOSED
  J_X_CLOSED edge_J_X_CLOSED__J_START@-.-> J_START
  J_CHECKIN_R edge_J_CHECKIN_R__J_ANY@--> J_ANY
  J_CHECKIN_NONE edge_J_CHECKIN_NONE__J_ANY@--> J_ANY
  J_TASK_R edge_J_TASK_R__J_ANY@--> J_ANY
  J_BRIEF_R edge_J_BRIEF_R__J_ANY@--> J_ANY
  J_RB_OK edge_J_RB_OK__J_ANY@--> J_ANY
  J_CONTACT_R edge_J_CONTACT_R__J_ANY@--> J_ANY
  J_CONTACT_HDG edge_J_CONTACT_HDG__J_ANY@--> J_ANY
  J_MARK_PEND edge_J_MARK_PEND__J_ANY@--> J_ANY
  J_LASER_PEND edge_J_LASER_PEND__J_ANY@--> J_ANY
  J_CLEARED edge_J_CLEARED__J_ANY@--> J_ANY
  J_OFF_R edge_J_OFF_R__J_ANY@--> J_ANY
  J_BDA_REM edge_J_BDA_REM__J_ANY@--> J_ANY
  J_ANY edge_J_ANY__J_STATUS@--> J_STATUS
  J_ANY edge_J_ANY__J_ABORT@--> J_ABORT
  J_ANY edge_J_ANY__J_LOFF@--> J_LOFF
  J_ANY edge_J_ANY__J_CHECKOUT@--> J_CHECKOUT
  J_ANY edge_J_ANY__J_CHECKIN@-->|"check in again: drops your task"| J_CHECKIN
  J_STATUS edge_J_STATUS__J_STATUS_R@-->|"task assigned"| J_STATUS_R
  J_STATUS edge_J_STATUS__J_STATUS_NOTASK@-->|"no task"| J_STATUS_NOTASK
  J_STATUS_R edge_J_STATUS_R__J_ANY@-.->|"RETURN to the current step"| J_ANY
  J_STATUS_NOTASK edge_J_STATUS_NOTASK__J_REQ_TASK@-.-> J_REQ_TASK
  J_ABORT edge_J_ABORT__J_ABORT_R@-->|"live link"| J_ABORT_R
  J_ABORT_R edge_J_ABORT_R__J_CONTACT@-.->|"RETURN: contact, then call in again"| J_CONTACT
  J_ABORT_R edge_J_ABORT_R__J_EV_LOFF@-->|"laser off pending"| J_EV_LOFF
  J_LOFF edge_J_LOFF__J_LOFF_PEND@-->|"a spot is on or may still come on"| J_LOFF_PEND
  J_LOFF edge_J_LOFF__J_LOFF_OFF@-->|"nothing on or queued"| J_LOFF_OFF
  J_LOFF edge_J_LOFF__J_NOTASK@-->|"no task assigned"| J_NOTASK
  J_LOFF_PEND edge_J_LOFF_PEND__J_EV_LOFF@--> J_EV_LOFF
  J_LOFF_OFF edge_J_LOFF_OFF__J_ANY@-.->|"RETURN"| J_ANY
  J_EV_LOFF edge_J_EV_LOFF__J_ANY@-.->|"RETURN"| J_ANY
  J_ABORT edge_J_ABORT__J_STALE_CXL@-->|"link stale"| J_STALE_CXL
  J_LOFF edge_J_LOFF__J_STALE_CXL@-->|"link stale"| J_STALE_CXL
  J_STALE_CXL edge_J_STALE_CXL__J_START@-.->|"RETURN when the link is back"| J_START
  J_START edge_J_START__J_CHECKOUT@-->|"no session needed"| J_CHECKOUT
  J_CHECKOUT edge_J_CHECKOUT__J_CHECKOUT_R@-->|"always, even with the link stale"| J_CHECKOUT_R
  J_CHECKOUT_R edge_J_CHECKOUT_R__J_END@--> J_END

  classDef normalFlow stroke:#15803d,stroke-width:3px,stroke-dasharray:0
  classDef errorFlow stroke:#b91c1c,stroke-width:2.5px,stroke-dasharray:0
  classDef recoveryFlow stroke:#92400e,stroke-width:2px,stroke-dasharray:7 4
  classDef neutralFlow stroke:#64748b,stroke-width:1px,stroke-dasharray:0
  classDef pilotCall fill:#eff6ff,stroke:#1d4ed8,color:#172554
  classDef errorNode fill:#fef2f2,stroke:#b91c1c,color:#450a0a
  classDef recoveryNode fill:#fffbeb,stroke:#92400e,color:#451a03
  class edge_J_START__J_G_HUB,edge_J_BRIEF_R__J_REPEAT,edge_J_BRIEF_R__J_REPEAT_RMK,edge_J_RB_OK__J_MARK,edge_J_RB_OK__J_LASER,edge_J_CONTACT_HDG__J_MARK,edge_J_CONTACT_HDG__J_LASER,edge_J_CONTACT_R__J_MARK,edge_J_CONTACT_R__J_LASER,edge_J_MARK__J_MARK_LIVE,edge_J_LASER__J_MARK_LIVE,edge_J_OFF__J_OFF_NOCLR,edge_J_OFF_NOCLR__J_BDA,edge_J_BDA__J_BDA_UNC,edge_J_BDA_UNC__J_REQ_TASK_N,edge_J_BDA_UNC__J_CHECKOUT,edge_J_CHECKIN_R__J_ANY,edge_J_CHECKIN_NONE__J_ANY,edge_J_TASK_R__J_ANY,edge_J_BRIEF_R__J_ANY,edge_J_RB_OK__J_ANY,edge_J_CONTACT_R__J_ANY,edge_J_CONTACT_HDG__J_ANY,edge_J_MARK_PEND__J_ANY,edge_J_LASER_PEND__J_ANY,edge_J_CLEARED__J_ANY,edge_J_OFF_R__J_ANY,edge_J_BDA_REM__J_ANY,edge_J_ANY__J_STATUS,edge_J_ANY__J_ABORT,edge_J_ANY__J_LOFF,edge_J_ANY__J_CHECKOUT,edge_J_ANY__J_CHECKIN,edge_J_STATUS__J_STATUS_R,edge_J_STATUS__J_STATUS_NOTASK,edge_J_LOFF__J_LOFF_OFF neutralFlow
  class edge_J_G_HUB__J_G_SILENT,edge_J_G_HUB__J_G_ID,edge_J_G_HUB__J_G_STALE,edge_J_G_HUB__J_G_NONE,edge_J_G_HUB__J_G_NOSESSION,edge_J_CHECKIN__J_CHECKIN_NONE,edge_J_REQ_TASK__J_TASK_PICK,edge_J_REQ_TASK__J_TASK_NONE,edge_J_REQ_TASK_N__J_TASK_NA,edge_J_BRIEF__J_BRIEF_NOTASK,edge_J_BRIEF__J_BRIEF_DEAD,edge_J_BRIEF__J_BRIEF_INC,edge_J_REPEAT__J_REPEAT_NUM,edge_J_REPEAT__J_NOBRIEF,edge_J_REPEAT_RMK__J_NOBRIEF,edge_J_RB__J_RB_BAD,edge_J_RB__J_NOBRIEF,edge_J_CONTACT__J_CONTACT_NORB,edge_J_MARK__J_MARK_NA,edge_J_LASER__J_MARK_NA,edge_J_MARK__J_NOBRIEF,edge_J_LASER__J_NOBRIEF,edge_J_W_ACK__J_EV_FAIL,edge_J_EV_LASER__J_EV_TERM,edge_J_EV_SPARKLE__J_EV_TERM,edge_J_IN_HDG__J_NOCLR,edge_J_IN__J_NOCLR,edge_J_CLEARED__J_X_CLR,edge_J_BDA__J_NOTASK,edge_J_BRIEF_R__J_X_LOST,edge_J_RB_OK__J_X_LOST,edge_J_CONTACT_HDG__J_X_LOST,edge_J_CONTACT_R__J_X_LOST,edge_J_CLEARED__J_X_LOST,edge_J_EV_LASER__J_X_LOST,edge_J_EV_SPARKLE__J_X_LOST,edge_J_TASK_R__J_X_TASK,edge_J_RB_OK__J_X_TASK,edge_J_ANY__J_X_CLOSED,edge_J_LOFF__J_NOTASK,edge_J_ABORT__J_STALE_CXL,edge_J_LOFF__J_STALE_CXL errorFlow
  class edge_J_G_SILENT__J_START,edge_J_G_ID__J_START,edge_J_G_STALE__J_START,edge_J_G_NONE__J_START,edge_J_G_NOSESSION__J_CHECKIN,edge_J_W_TASKS__J_REQ_TASK,edge_J_TASK_PICK__J_REQ_TASK_N,edge_J_TASK_NONE__J_W_TASKS,edge_J_TASK_NA__J_REQ_TASK,edge_J_BRIEF_NOTASK__J_REQ_TASK,edge_J_BRIEF_DEAD__J_BDA,edge_J_BRIEF_DEAD__J_REQ_TASK_N,edge_J_BRIEF_INC__J_REQ_TASK_N,edge_J_REPEAT_NUM__J_REPEAT,edge_J_NOBRIEF__J_BRIEF,edge_J_REPEAT_R__J_RB,edge_J_RB_BAD__J_RB,edge_J_CONTACT_NORB__J_RB,edge_J_CONTACT_NORB__J_BRIEF,edge_J_MARK_NA__J_MARK,edge_J_EV_FAIL__J_MARK,edge_J_EV_FAIL__J_LASER,edge_J_EV_TERM__J_LASER,edge_J_EV_TERM__J_MARK,edge_J_NOCLR__J_CONTACT,edge_J_NOCLR__J_IN_HDG,edge_J_NOCLR__J_MARK,edge_J_NOCLR__J_LASER,edge_J_NOCLR__J_RB,edge_J_NOCLR__J_REQ_TASK,edge_J_X_CLR__J_MARK,edge_J_NOTASK__J_REQ_TASK,edge_J_X_LOST__J_BRIEF,edge_J_X_TASK__J_REQ_TASK,edge_J_X_CLOSED__J_START,edge_J_STATUS_R__J_ANY,edge_J_STATUS_NOTASK__J_REQ_TASK,edge_J_ABORT_R__J_CONTACT,edge_J_LOFF_OFF__J_ANY,edge_J_EV_LOFF__J_ANY,edge_J_STALE_CXL__J_START recoveryFlow
  class edge_J_START__J_CHECKIN,edge_J_CHECKIN__J_CHECKIN_R,edge_J_CHECKIN_R__J_REQ_TASK,edge_J_CHECKIN_R__J_REQ_TASK_N,edge_J_CHECKIN_NONE__J_W_TASKS,edge_J_REQ_TASK__J_TASK_R,edge_J_REQ_TASK_N__J_TASK_R,edge_J_TASK_R__J_BRIEF,edge_J_BRIEF__J_BRIEF_R,edge_J_BRIEF__J_BRIEF_NORB,edge_J_BRIEF__J_BRIEF_LONG,edge_J_BRIEF_R__J_RB,edge_J_BRIEF_NORB__J_CONTACT,edge_J_BRIEF_LONG__J_REPEAT,edge_J_BRIEF_LONG__J_REPEAT_RMK,edge_J_REPEAT__J_REPEAT_R,edge_J_REPEAT_RMK__J_REPEAT_R,edge_J_RB__J_RB_OK,edge_J_RB_OK__J_CONTACT,edge_J_CONTACT__J_CONTACT_HDG,edge_J_CONTACT__J_CONTACT_R,edge_J_CONTACT_HDG__J_IN_HDG,edge_J_CONTACT_R__J_IN,edge_J_MARK__J_MARK_PEND,edge_J_LASER__J_LASER_PEND,edge_J_MARK_PEND__J_W_ACK,edge_J_LASER_PEND__J_W_ACK,edge_J_W_ACK__J_EV_LASER,edge_J_W_ACK__J_EV_SPARKLE,edge_J_W_ACK__J_EV_MARK,edge_J_EV_LASER__J_CONTACT,edge_J_EV_SPARKLE__J_CONTACT,edge_J_EV_MARK__J_CONTACT,edge_J_EV_LASER__J_IN_HDG,edge_J_EV_SPARKLE__J_IN_HDG,edge_J_EV_MARK__J_IN_HDG,edge_J_EV_LASER__J_IN,edge_J_EV_SPARKLE__J_IN,edge_J_EV_MARK__J_IN,edge_J_MARK_LIVE__J_IN_HDG,edge_J_MARK_LIVE__J_IN,edge_J_IN_HDG__J_CLEARED,edge_J_IN__J_CLEARED,edge_J_CLEARED__J_FLY,edge_J_FLY__J_OFF,edge_J_OFF__J_OFF_R,edge_J_OFF_R__J_BDA,edge_J_BDA__J_BDA_KILL,edge_J_BDA__J_BDA_REM,edge_J_BDA_REM__J_IN_HDG,edge_J_BDA_REM__J_IN,edge_J_BDA_KILL__J_REQ_TASK_N,edge_J_BDA_KILL__J_CHECKOUT,edge_J_X_LOST__J_BDA,edge_J_ABORT__J_ABORT_R,edge_J_ABORT_R__J_EV_LOFF,edge_J_LOFF__J_LOFF_PEND,edge_J_LOFF_PEND__J_EV_LOFF,edge_J_START__J_CHECKOUT,edge_J_CHECKOUT__J_CHECKOUT_R,edge_J_CHECKOUT_R__J_END normalFlow
  class J_CHECKIN,J_REQ_TASK,J_REQ_TASK_N,J_BRIEF,J_REPEAT,J_REPEAT_RMK,J_RB,J_CONTACT,J_MARK,J_LASER,J_IN_HDG,J_IN,J_OFF,J_BDA,J_STATUS,J_ABORT,J_LOFF,J_CHECKOUT pilotCall
  class J_G_SILENT,J_G_ID,J_G_STALE,J_G_NONE,J_G_NOSESSION,J_TASK_NA,J_TASK_NONE,J_BRIEF_NOTASK,J_BRIEF_DEAD,J_BRIEF_INC,J_REPEAT_NUM,J_NOBRIEF,J_RB_BAD,J_CONTACT_NORB,J_MARK_NA,J_EV_FAIL,J_EV_TERM,J_NOCLR,J_X_CLR,J_NOTASK,J_X_LOST,J_X_TASK,J_X_CLOSED errorNode
  class J_TASK_PICK,J_BRIEF_LONG,J_ABORT_R,J_LOFF_PEND,J_LOFF_OFF,J_EV_LOFF,J_STALE_CXL recoveryNode
```

## Readback dictionary

`_readback_mismatch` (`jtac.py` 227-244) checks the text after the first `readback` / `read back`. Before it runs, `_readback` (997-1006) requires a brief (`unable, no brief issued`).

1. The mission decides which lines are read back. The brief ends `read back line four, line six` (or `remarks` for line ten). With no readback lines it ends `report contact` and you go straight to contact.
2. Say the values **in brief order**: IP, heading, distance, elevation, description, location, mark, friendlies, egress, remarks. The offset is never read back.
3. Numeric lines (heading, distance, elevation) compare as complete signed decimals: `six point five` or `6.5` is 6.5, `six five` is 65, `minus one two zero` is -120. A heading also matches modulo 360.
4. Text lines compare as whole runs of digits, phonetic letters and words: `KM 1234` is read back `kilo mike one two three four` (`x-ray` and `x ray` both mean X).
5. Labels and filler are ignored separators: `feet foot ft elevation location grid line heading distance miles mile nm nautical ip egress mark friendlies remarks the and of is at a an copy roger read back readback uh um over` (`jtac.py` 154-157). **Two numeric values in a row need a label between them:** `heading two seven zero elevation four five zero` passes, `two seven zero, four five zero` is heard as one number and fails.
6. Extra or missing content fails. The reply names the **first** failing line and repeats it: `negative readback, line four, elevation four five zero feet`. Read back the whole set again.
7. A failed readback after a correct one puts you back to `awaiting readback` and drops contact and clearance.

| Brief line | Axeman says | You read back (sample) |
|---|---|---|
| 1 IP | `line one, IP [IP]` | `IP dog` |
| 2 heading | `line two, heading [HEADING], [OFFSET]` | `heading two seven zero` |
| 3 distance | `line three, distance [DISTANCE]` | `distance six point five` |
| 4 elevation | `line four, elevation [ELEVATION] feet` | `elevation four five zero` |
| 5 description | `line five, [DESCRIPTION]` | the description words |
| 6 location | `line six, [LOCATION]` | `kilo mike one two three four` |
| 7 mark | `line seven, mark [MARK]` | `laser code one six eight eight` |
| 8 friendlies | `line eight, friendlies [FRIENDLIES]` | `south three kilometers` |
| 9 egress | `line nine, egress [EGRESS]` | `north to IP cat` |
| remarks | `remarks, [REMARKS]` | the remarks words |

Fixture readback (lines four and six): `Axeman, Viper one one, readback elevation four five zero, kilo mike one two three four`.

## Marks and laser

| You ask | Axeman replies | Then (EVENT) | Counts as the required mark? |
|---|---|---|---|
| `request laser` | `stand by, laser code [LASER_CODE] pending` | `laser on, code [LASER_CODE]` / `unable laser, [REASON]` / `unable laser, no acknowledgement` | Yes, while your spot stays live |
| `request sparkle` / `request IR pointer` | `stand by, sparkle pending` | `sparkle on the target` / `unable sparkle, [REASON]` / `unable mark, no acknowledgement` | Yes, while your spot stays live |
| `request smoke` / `request illumination` | `stand by, smoke pending` / `stand by, illumination pending` | `smoke on the target` / `illumination on the target` / `unable mark, [REASON]` / `unable mark, no acknowledgement` | Yes, once acknowledged, until the next reset |
| `request mark` | as above for the first non-laser mark the task offers, else the laser | as above | as above |
| Same request while your spot is live | `laser on, code [LASER_CODE]` / `sparkle on the target` | — | — |
| `laser off` | `copy, laser off pending` or `laser is off` | `laser off` / `unable laser off, [REASON]` | Ends a spot-based mark |

- A mark the task does not offer gets `unable, <mark> not available for this task`. A laser with no authored code gets `unable, no laser code assigned` (`jtac.py` 1047-1056).
- Asking again while a request is pending re-sends the same `stand by` reply. Nothing new is queued (`jtac.py` 696-700).
- `laser off` switches off **every** spot of your task, laser and IR. `abort` does the same.
- The mission may end a spot by itself (target or controller lost, lease expired). You then hear `spot terminated` or `sparkle terminated`, and a clearance that depended on that spot is withdrawn silently (`jtac.py` 650-659, 773-775).
- EVENTs are held for 10 s and checked against current state just before they are spoken. A stale event is dropped, not spoken late (`jtac.py` 87, 786-802; `brain.py` 335-341).

## Clearance conditions

`in heading [ATTACK_HEADING]` / `in` is checked in this order (`jtac.py` 1017-1041). The first unmet condition is the reason in `continue, no clearance, [NO_CLEARANCE_REASON]`:

| Reason | Fix |
|---|---|
| `no task assigned` | `request task` |
| `readback required` | read back (or ask for the brief again after a reset) |
| `report contact` | `contact` |
| `target not active` / `controller not available` | request BDA or another task |
| `say heading` | the task has an attack heading window: say `in heading <three digits>` |
| `attack heading <low> to <high> only` | turn into the window, then call in again |
| `mark not confirmed` | the task requires a mark: get an acknowledged smoke/illumination or a live laser/sparkle first |

With every condition met: `cleared hot`, or `cleared to engage` for a type three control. After `off target` the clearance ends. Contact still stands, so a reattack only needs a new `in` call.

## Calls that are refusals in the wrong context

| Call | Wrong context reply |
|---|---|
| Anything except `checking in` / `checking out` with no session | `unable, check in first` |
| `ready for brief` with no task | `unable, request task first` |
| `say again line …` / `readback …` / `request mark` / `request laser` before a brief | `unable, no brief issued` |
| `contact` before a correct readback | `unable, readback required` |
| `off target` with no active clearance | `copy off, no clearance was active` |
| `laser off` / `request BDA` with no task | `unable, no task assigned` |
| `say again` alone, `cancel` alone, `cleared hot`, `negative contact` | `say again` |

## Intent coverage (15 JTAC intents)

| Intent (`jtac.py` 115-143) | Canonical phrase | Chart nodes |
|---|---|---|
| checkOut | `checking out` | J_CHECKOUT |
| checkIn | `checking in` (also `on station`) | J_CHECKIN |
| readback | `readback …` | J_RB |
| repeatLine | `say again line <n>` / `say again remarks` (also `repeat`, `confirm`) | J_REPEAT, J_REPEAT_RMK |
| requestBrief | `ready for brief` / `ready to copy` (also `request nine line`, `send the brief`) | J_BRIEF |
| requestTask | `request task` / `request task <n>` | J_REQ_TASK, J_REQ_TASK_N |
| laserOff | `laser off` (also `spot off`, `sparkle off`, `cancel the laser`, `terminate laser`) | J_LOFF |
| requestLaser | `request laser` | J_LASER |
| requestMark | `request smoke` / `request sparkle` / `request illumination` / `request mark` | J_MARK |
| abort | `abort` | J_ABORT |
| requestBda | `request BDA` (also `say BDA`, `battle damage`) | J_BDA |
| attackComplete | `off target` (also `off safe`, `off dry`, `off`, `attack complete`, `pass complete`) | J_OFF |
| inbound | `in heading <n>` / `in` (also `in hot`, `rolling in`, `in from`) | J_IN_HDG, J_IN |
| contact | `contact` (also `tally`, `visual`) | J_CONTACT |
| status | `say status` | J_STATUS |

## Source cross-reference

| Topic | Source |
|---|---|
| Procedure summary, bridge protocol, readback rules | `overlord/jtac.py` 1-73 |
| Grammar, earliest-match rule, negation/abort scope | 111-151, 474-555 |
| Readback validator | 154-244 |
| Task, brief, mark and laser validation | 256-385 |
| Acknowledgements, spot loss, expiry, command queue | 559-717 |
| Session reset, invalidation, stale refresh, mark requirement | 719-784 |
| Events and reply validation | 786-836 |
| Call dispatch, check in, live-link guard | 838-899 |
| Handlers (task, brief, line, readback, contact, inbound, marks, laser off, abort, off, BDA, status) | 901-1118 |
| Brain routing, telemetry guard, local intent fallback, events | `overlord/brain.py` 183-213, 335-367 |
| Caller identity replies | `overlord/domain.py` 1172-1198 |
| Mission configuration fields | `dcs/jtac-config.example.lua` |
| Behaviour tests | `tests/test_jtac.py` |
