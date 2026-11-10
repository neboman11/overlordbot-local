# OverlordBot rewrite: fully local voice AWACS/ATC for DCS

## Goal

Rewrite OverlordBot (https://gitlab.com/overlordbot/srs-bot, C#, ~11k lines, unmaintained since
2024) so that **all speech and language processing runs locally**, with no cloud services, and
the heavy processing can run on a different machine from the game server.

Players talk to the bot over SRS radio on configured frequencies. It answers with synthesized
speech: AWACS (bogey dope, picture, declare, vectors) and ATC (inbound, ready to taxi, airfield
location), the same features the original had.

## Why a rewrite rather than a port

| Original dependency | Status | Local replacement |
|---|---|---|
| Azure Cognitive Services Speech (STT + TTS) | Cloud, paid | STT: whisper.cpp or faster-whisper. TTS: Piper |
| Azure LUIS (intent/entity extraction) | **Retired by Microsoft 2025-10-01** | Deterministic brevity-code grammar parser; optional small local LLM fallback |
| DCS-gRPC (unit positions) | Mod DLL inside DCS | Tacview real-time telemetry (already running) or DCS-gRPC (see below) |
| .NET 5/6 | End of life | Whatever the rewrite chooses (see constraints) |
| Discord.Net (logging/admin) | Works | Optional; DCSServerBot already covers Discord admin |

Worth keeping from the original: the domain logic in `OverlordBot.Core` (`Awacs/`, `Atc/`,
`Responders/`: `BogeyDopeHandler`, `InboundToAirfieldHandler`, `LocationOfAirfieldHandler`,
`LocationOfUnitHandler`, `ReadyToTaxiHandler`, `WarningRadiusHandler`, `MissionJoinHandler`,
`MissionAssignmentHandler`), the phraseology in `OverlordBot.Cognitive/SpeechOutput`, the
transmission-parsing test cases in `OverlordBot.Cognitive.Tests` (they are the spec for the new
parser), and `OverlordBot.Encyclopedia` (aircraft/airfield data).

## The environment it must run in

Already deployed by `argocd-definitions` (`dcs/dcs-server`, apartment cluster, namespace `dcs`):

| Interface | Where | Notes |
|---|---|---|
| SRS server 2.4.1.0 | `dcs-server.dcs.svc.cluster.local:5002` TCP+UDP (in cluster), `192.168.1.39:5002` (LAN), public IP port 5002 (forwarded) | Linux SRS server. Config in `DCS.server/SRS/server.cfg`. **Check that the SRS client protocol in the original `OverlordBot.SimpleRadio` still matches SRS 2.4.x** (sync/handshake JSON over TCP, Opus voice over UDP); expect changes since 2.0. EAM (external AWACS mode) passwords can be enabled via `SRS_EAM_BLUE_PASSWORD` / `SRS_EAM_RED_PASSWORD` on the SRS sidecar; EAM is the intended way for a bot to join a coalition. |
| Tacview real-time telemetry | `dcs-server.dcs.svc.cluster.local:42674` TCP | ACMI 2.x text stream, no password at the moment. Gives positions, headings, altitudes, coalition, types of all objects. This is the **preferred state source** (no extra mod in DCS). Only listens while a mission is running unpaused. |
| DCS-gRPC (optional) | not installed | Richer API (airbases, groups, events). Would be installed into `DCS.server/Mods/Tech/DCS-gRPC` + `Scripts/Hooks` like Tacview. **Unverified under Wine.** Only pursue if Tacview data is insufficient. |
| Terrain/airfield data | Bundled with the bot | Original uses DEM.Net + `OverlordBot.TerrainDownloader` for line-of-sight; Encyclopedia for airfields/runways. |
| Discord | DCSServerBot owns the bot user | If the rewrite wants Discord output, use a separate bot token. |

### Compute available for STT/TTS

No GPUs in the apartment cluster.

| Node | Cluster | CPU | Notes |
|---|---|---|---|
| sunman | apartment | 2x Xeon Silver 4215 (AVX-512) | Control plane, runs DCS on 4 exclusive cores. **Do not put STT here.** |
| leafman, stickman | apartment | 2x Xeon E5-2650 v2, 16 cores, **AVX but no AVX2**, 64 GB | ~80% idle. Best in-cluster target for STT/TTS. |
| flowerman | apartment | same as above | Runs Home Assistant (USB dongle); fine for light work. |
| treeman | home (other site) | i7-5820K (AVX2), 12 cores, Intel GPU (`gpu.intel.com/i915`) | Different site over the internet; only for batch/non-latency work or if latency is acceptable. |
| User's desktop | apartment LAN | unknown, likely has a GPU | Could host a "worker" process if the protocol allows remote workers. |

AVX-only CPUs rule out builds compiled with AVX2 baselines (some prebuilt CTranslate2/ONNX
wheels); whisper.cpp compiled with `-mavx -mf16c` works. Benchmark on leafman before choosing a
model size. ATC/AWACS calls are short (2–6 s); target **< 1.5 s from end of transmission to
start of reply**.

## Target architecture

```
SRS server ─UDP Opus─> radio-gateway ──audio segment──> stt-worker (leafman/stickman/desktop)
                          │                                 │ text
                          │<──────────── tts-worker <── reply text ── brain
                          │                                 ^
Tacview RT 42674 ──ACMI──> brain (game state, parser, AWACS/ATC logic) ┘
```

Split into three processes (one repo, separate container images or one image with subcommands):

1. **radio-gateway**: SRS client (one per coalition/frequency set), VAD/end-of-transmission
   detection on the radio stream, Opus decode/encode, plays replies back on the right frequency
   and modulation. Stays close to the SRS server (any apartment node; latency-sensitive).
2. **brain**: game state from Tacview RT (or DCS-gRPC), transmission parser (callsign, bot
   callsign, intent, entities), the ported AWACS/ATC responders, phraseology generation.
   Lightweight; can live with the gateway.
3. **stt-worker / tts-worker**: stateless HTTP or gRPC services (`POST /transcribe` with 16 kHz
   PCM → text + confidence; `POST /synthesize` with text + voice → PCM). Horizontally
   replaceable and **placeable on another node or machine**. That's the "move the work elsewhere"
   requirement. Gateway/brain take worker URLs from config and fail over between them.

Language understanding: start with a **deterministic grammar parser** driven by the original
test cases (brevity codes are a small closed vocabulary: "<bot> <callsign> bogey dope", "request
picture", "inbound", "ready to taxi"). Bias STT with an initial prompt/grammar of callsigns,
airfield names and brevity words. Only add an optional local LLM (llama.cpp, small model) as a
fallback for unparsed transmissions if testing shows it's needed; it must be optional.

## Milestones

1. **Protocol spike**: minimal SRS 2.4 client that connects to a local SRS server (the Linux
   server binary from https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/tag/2.4.1.0
   runs fine in a container), joins a frequency, records a transmission to WAV, and plays a WAV
   back. This is the riskiest piece; do it first.
2. **State spike**: parse a recorded Tacview RT stream (record one from the live server during a
   session, or generate ACMI) into an in-memory world model with coalition filtering.
3. **STT/TTS workers**: whisper.cpp + Piper services; benchmark model sizes on an AVX-only Xeon
   (leafman class) and report latency per utterance.
4. **Parser**: port the transmission-parsing tests; make them pass against STT output of a small
   recorded phrase corpus (record your own voice and friends' voices over SRS).
5. **Responders**: port bogey dope, picture/declare, location of airfield, inbound/ATC; unit tests
   against canned world states.
6. **End to end**: compose file or kind/k3s test that runs SRS server + gateway + brain + workers
   with a replayed ACMI stream and scripted radio calls; assert spoken replies.
7. **Packaging**: container images published to `ghcr.io/neboman11/<name>` pinned by tag/digest,
   with health endpoints and Prometheus metrics (transmissions, STT latency, parse failures).

## Non-goals (for now)

- Cloud services of any kind.
- Replacing DCSServerBot's Discord features.
- Running anything on sunman besides what's already there.

## Handoff contract (what to give back for deployment)

When the rewrite is ready, provide in its repo a `DEPLOY.md` containing:

1. **Images**: full references with tags (and digests) for each component, linux/amd64, CPU flags
   required (must run on AVX-without-AVX2 for the worker if it's meant for leafman/stickman).
2. **Components table**: name, command/args, listening ports (protocol), outbound connections
   (SRS host:port, Tacview host:port, worker URLs), whether it must be near SRS.
3. **Configuration**: every env var / config-file key with default and meaning; which values are
   secrets (SRS EAM passwords, Tacview RT password if enabled, optional Discord token).
4. **Resources**: measured CPU/memory per component at idle and under load, model files and
   their sizes, where models are downloaded from (pinned URL + sha256) or baked into the image,
   persistent storage needs (none preferred).
5. **Server-side changes needed**, e.g. "enable SRS EAM with passwords X/Y", "set Tacview RT
   password", "install DCS-gRPC version N", "SRS frequencies/coalitions the bot should listen on".
6. **Health/readiness**: HTTP paths or TCP checks per component; metrics endpoint.
7. **Verification steps**: how to confirm it works once deployed (e.g. radio call on 251.0 AM
   "Overlord, Viper 1-1, bogey dope" → expected reply).
8. **Known limits**: accuracy, latency numbers from the benchmarks, unsupported calls.
