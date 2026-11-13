# OverlordBot Local

Local-only voice AWACS, airfield/carrier ATC, and JTAC for DCS World over SRS 2.4.1.0. Speech and optional LLM workers can run on another LAN machine; gateways stay near SRS. No Azure, LUIS, cloud speech/LLM APIs, Discord dependency, GPU requirement, or Kubernetes deployment changes.

## Services and runtime choice

- **radio-gateway**: current SRS EAM client, TCP control, UDP Opus, bounded per-speaker transmission assembly, STT → brain → TTS orchestration, radio playback.
- **brain**: Tacview ACMI 2.x state, deterministic grammar and reply templates, AWACS/ATC controllers, optional local intent classification, and live DCS JTAC/carrier/airfield procedures.
- **carrier-gateways**: optional supervisor creating an independent SRS client and speech queue for each enabled mission carrier.
- **airfield-gateways**: optional mission-native airfield radios, with independent field controllers and one serialized SRS client per physical coalition/frequency/modulation channel.
- **stt-worker**: stateless HTTP API around a persistent local whisper.cpp model, built for AVX/F16C without AVX2/FMA.
- **tts-worker**: stateless HTTP API around persistent local Piper/ONNX inference, public-domain LJSpeech voice, native audio resampled to 16 kHz.

Python standard library keeps control/network/domain services inspectable and dependency-light; native whisper.cpp, Piper/ONNX and libopus handle compute. Independent HTTP workers need neither shared storage nor player/session affinity. Gateway/brain stay lightweight and models move away from DCS-exclusive cores.

## Run the complete local stack

Requirements: Linux amd64, Docker Compose or Podman + podman-compose. Internet is needed for pinned build inputs/model downloads, **not inference**. The demo uses an internal container network without published ports or runtime internet access.

```sh
sh scripts/stack.sh build
sh scripts/stack.sh verify
sh scripts/stack.sh down
```

Verification starts real SRS 2.4.1.0, a replayed Tacview stream, and all four services. It replays SHA256-checked, fixed **synthetic Piper caller recordings** over SRS; real STT parses them, brain calculates replies, and real Piper replies are received over SRS and retranscribed for distinct bogey-dope, picture, airfield-location and inbound-ATC assertions. This is a curated functional corpus, **not human speech accuracy certification**. Caller recordings are in `fixtures/radio`; regeneration uses `scripts/record_corpus.py` inside the pinned speech image. Results/WAVs go to ignored `artifacts/`.

`up` and `verify` recreate demo containers so rebuilt images cannot silently leave old services running. Real players/friends' recordings should be added before operational accuracy acceptance.

## Deploy or move workers elsewhere

See **[DEPLOY.md](DEPLOY.md)** for exact published tags/digests, every configuration key/secret, measured resources and latency, server changes, health checks, and limits. [benchmarks/](benchmarks/) contains raw physical Xeon/Ryzen measurements and release provenance.

```sh
python -m overlord radio-gateway
python -m overlord brain
python -m overlord stt-worker
python -m overlord tts-worker
python -m overlord carrier-gateways  # optional mission-driven carrier radios
python -m overlord airfield-gateways # optional mission-native ground/tower/approach
```

Set comma-separated `STT_URLS` / `TTS_URLS` to remote worker base URLs. Set common `API_TOKEN` for Bearer authentication; use private LAN/VPN or a TLS proxy. Transport/5xx failures fail over, but invalid requests and redirects do not. Do not place STT on sunman's DCS cores.

Legacy shared-radio `atc` runway selection requires **explicit** `ATC_WIND_FROM_DEG` (number or per-airfield JSON); Tacview has no wind. Native `airfield` ATC instead uses live DCS weather and runway geometry. Shared monitors provide warning-radius calls and legacy ATC taxi/inbound follow-ups. Notifications preserve coalition, frequency, generation, controller revision and expiry; canceled/stale instructions are revalidated before playback.

## Local NLP, expanded AWACS, and JTAC

Standard radio calls bypass the LLM. Set `LLM_URL` to a local OpenAI-compatible chat-completions server and `LLM_MODEL` to an installed model to interpret unmatched, explicitly addressed requests. The model selects an allowlisted command; code resolves callers, reads current telemetry, enforces procedure state, and renders fixed replies. Invalid output, unavailable inference, or missing mission facts never become invented tactical information.
JTAC NLP is limited to brief, status, and BDA queries. Check-in, task selection, contact reports, readbacks, marking/laser actions, and clearance prerequisites require deterministic radio grammar; negated procedure statements never enter inference.

Additional AWACS calls include `friendly picture`, `bullseye picture`, `nearest tanker`, `vector home`, `nearest divert`, and `say again`. Configure `AWACS_BULLSEYE` as `[latitude, longitude]` and `AWACS_HOME_AIRFIELD` explicitly. Say-again recalculates informational responses from fresh telemetry and expires after 30 seconds.

JTAC uses **a second SRS gateway on a separate frequency**: default AWACS/ATC 251 MHz AM, JTAC/Axeman 252 MHz AM. Enable `BRAIN_ROLES=awacs,atc,jtac`, configure a fresh `DCS_BRIDGE_TOKEN`, and start the `jtac` Compose profile. The brain rejects cross-frequency controller traffic; STT/TTS workers can be shared.

The companion Lua mission script and server hook in `dcs/` provide live task state, marking, and laser control. JTAC uses mission-authored briefs/restrictions, checks readbacks and contact reports, and supports abort and BDA/status. Marking/laser success requires a DCS acknowledgement; stale state blocks progression. Secrets remain server-side, not inside downloadable missions. See [DEPLOY.md](DEPLOY.md) for installation, exact settings, command sequence, and verification limits.

No downloaded reference documents or research corpus are shipped. Optional local research belongs in ignored `.research/`.

## Independent carrier radios

Enable the `carrier` brain role and Compose profile, configure both `API_TOKEN`
and `DCS_BRIDGE_TOKEN`, install `dcs/overlord_carrier.lua` in the mission, and enable
`features = {"jtac", "carriers"}` in the private server hook (or `{"carriers"}` alone).
See [DEPLOY.md](DEPLOY.md#independent-carrier-operations) for installation and calls.

Each carrier uses its **existing mission ship-unit frequency**, including
Supercarriers. No frequency list or allocator is configured in the bot. Every
carrier has its own radio, aircraft sessions and recovery/departure lane.
Frequencies less than 200 Hz apart, including enabled AWACS/JTAC channels, are
disabled with diagnostics rather than silently reassigned.

Only humans who check in are enrolled; the carrier companion never polls AI or
unregistered aircraft movement. Calls bind to the aircraft's current incarnation
and carrier. Respawn, disconnect, reassignment, radio changes and mission restart
invalidate old calls; stale state blocks live clearances.

Fixed templates cover CASE I/II/III marshal, strict readbacks, sequencing, approach
reports, ball/clara, waveoff/bolter, observed touchdown/deck clear, startup/launch
and departure handoff. CASE and weather must be supplied explicitly; carrier
procedures never fall back to an LLM. This is simplified simulation control, not
full LSO or autopilot automation.


## Independent airfield ATC

Enable `BRAIN_ROLES=awacs,jtac,carrier,airfield` as needed and the Compose `airfield` profile. Both `API_TOKEN` and `DCS_BRIDGE_TOKEN` are required. Install the updated shared server hook and `dcs/overlord_airfield.lua`; see [deployment instructions](DEPLOY.md#independent-airfield-atc). Native `airfield` supersedes legacy shared-radio `atc` when both are listed.

Each native DCS airfield keeps its own radio roles, human check-ins, runway occupancy, movement queue, clearances and readbacks. Separate frequencies stay separate; fields sharing a native channel share one serialized speech gateway, not controller state. Name the field on first contact. Native carrier channels and AWACS/JTAC channels are reserved against conflicting airfield use.

Supported procedures include startup, taxi, hold short, runway crossing, line-up, takeoff, visual inbound/pattern/final, landing, go-around, vacated, taxi-in, parking, emergency priority and published-frequency handoff. Fixed templates and strict runway/clearance readbacks authorize movement; an LLM never invents clearances, routes, frequencies, wind or altimeter settings. Use the explicit word **“readback”** followed by the actual clearance terms and assigned runway; arrival briefings also require the received altimeter.

The server hook privately reads the installation's native `Radio.lua`. No copied frequency table, downloaded references, mission desanitization or Tacview connection is needed for native ATC. Optional `dcs/airfield-config.example.lua` corrects native runway labels/closures and supplies an explicit pattern or active runway; it cannot override radios.

Only checked-in humans are movement-polled. AI and unregistered aircraft are deliberately excluded, so this is not complete airport traffic separation. Unknown physical runway strips still block conflicting clearances. No named taxi route, instrument approach or terrain-safe vector is fabricated when mission data lacks it.

## APIs

| Component | Request | Response |
|---|---|---|
| STT | `POST /transcribe`, `audio/pcm`, 16 kHz mono s16le, 0.1–15 s; optional `X-Prompt` | JSON `{text, confidence}` |
| TTS | `POST /synthesize`, JSON `{text, voice?}` | 16 kHz mono s16le `audio/pcm` |
| Brain | `POST /respond`, JSON `{text, coalition, channel?, carrier_id?, airfield?, sender_callsign?}` | JSON `{reply, parsed, notes, validation?}`; native requests explicitly select `carrier_id` or `airfield:true` |
| Brain | `POST /events`, `{coalition, channel, carrier_id?, airfield?}` | Channel/controller-scoped notifications and mission-local vocabulary hints |
| Brain | `POST /validate-event`, returned event metadata | `{valid}` for immediate pre-playback validation |
| Brain | `POST /dcs/exchange`, companion protocol JSON; `X-DCS-Token` required, plus common API token when set | Live JTAC snapshots, acknowledgements, and bounded mission commands |
| Brain | `POST /carrier/exchange`, carrier companion JSON; `X-DCS-Token` and common API token required | Carrier registry, checked-in human snapshots, and bounded watch subscriptions |
| Brain | `POST /carrier/registry`, `{}`; common API token required | Mission-native radios, enabled/error status and bridge freshness |
| Carrier supervisor | `POST /status`, `{}`; common API token required | Secret-free per-carrier connection/status diagnostics |
| Brain | `POST /airfield/exchange`, airfield companion JSON; both tokens required | Native airfields/radios/weather and checked-in human watch subscriptions |
| Brain | `POST /airfield/registry`, `{}`; common API token required | Physical radio channels, independent field status and bridge freshness |
| Airfield supervisor | `POST /status`, `{}`; common API token required | Secret-free per-channel connection/status diagnostics |

All services expose `/healthz`, `/readyz`, `/metrics`. AWACS, legacy ATC and JTAC require fresh Tacview telemetry; native carrier/airfield controllers require their own fresh mission snapshots instead. Mixed-role brain readiness reflects telemetry-dependent roles, while each native radio supervisor checks its own registry. Unknown/ambiguous calls remain unanswered or receive an explicit negative.

## Checks and evidence

Release verification: **394 Python tests and 76 offline Lua cases passed**. A local **SRS 2.4.1.0** run exercised separate-field voice paths, shared-frequency consolidation to one gateway, independent human watches, both authentication gates, native readiness without Tacview, and zero gateway pipeline errors. DCS snapshots and speech workers were synthetic; actual DCS mission acceptance and human STT accuracy remain unverified.

```sh
python3 -m unittest discover -s tests -v
sh spike/run.sh       # historical old-client casing incompatibility
luajit tests/carrier_harness.lua && luajit tests/jtac_harness.lua  # offline DCS API doubles
sh spike/audio.sh     # current SRS receive/WAV/playback proof
sh scripts/benchmark_remote.sh leafman  # isolated SSH/user-namespace benchmark; no Kubernetes
```

The published baseline passed 103 behavioral tests, including 525 individually checked parser corpus records. New functionality has additional behavioral coverage in `tests/`. Original labels remain preserved where 17 corrections/policy decisions are documented in `data/PROVENANCE.md`. Historical benchmarks include actual E5-2650 v2 execution, not just AVX-disabled builds on newer CPUs; QEMU Ivy Bridge additionally exercised both speech engines.

Carrier verification passed 79 focused Python tests and 41 offline Lua harness
cases. A native SRS 2.4.1.0 two-carrier smoke verified separate audio channels,
mission frequency changes and revoked instructions without Tacview. That smoke
used synthetic STT/TTS HTTP workers; it is not in-game DCS or human speech acceptance.

SRS audio gate: 16,000 samples sent, captured and played back; 98.64% returned 440 Hz tone energy. Original bot's `ExternalAwacsModePassword` casing does not match current SRS `ExternalAWACSModePassword`; rewrite implements current protocol directly.

Known limits include proper-name speech errors, no human-radio accuracy certification, finite bundled ATC coverage, explicit wind configuration, and no terrain masking/radio decryption. Live JTAC/carrier control requires the companion installed in a configured mission; Lua/API harness checks are not in-game DCS acceptance. Full LSO automation and autonomous attack planning are not provided. See deployment guidance for details.

## Upstream and licensing

Derived from [OverlordBot](https://gitlab.com/overlordbot/srs-bot), commit `c7d34d51bfe66faf6bb8ff9e22688351e99cee70`, copyright Jeffrey Jones. Modified source is offered at https://github.com/neboman11/overlordbot-local under **AGPL-3.0-or-later**; full terms in [LICENSE](LICENSE), asset provenance in [data/PROVENANCE.md](data/PROVENANCE.md).

SRS reference: [2.4.1.0](https://github.com/ciribob/DCS-SimpleRadioStandalone/tree/f7bdcd42d41eff1e912cd0465b8a22c78108c53c). Demo image preserves its GPL license/source offer. Speech dependency/model sources and licenses are in [speech/NOTICE.md](speech/NOTICE.md) and `models.lock.json`; every model/config/card has a commit-pinned URL and SHA256. Research-restricted Lessac voice is not shipped.

Historical physical leafman benchmarks used temporary standalone user namespaces, without Kubernetes changes or host package installation. The GitOps deployment described in [DEPLOY.md](DEPLOY.md#independent-airfield-atc) adds native controller workloads and private DCS hook mounts while preserving persistent volumes.
