# OverlordBot Local

Local-only voice AWACS/ATC for DCS World over SRS 2.4.1.0. Speech workers can run on another LAN machine; gateway stays near SRS. No Azure, LUIS, cloud speech/LLM APIs, Discord dependency, GPU requirement, or Kubernetes deployment changes.

## Services and runtime choice

- **radio-gateway**: current SRS EAM client, TCP control, UDP Opus, bounded per-speaker transmission assembly, STT → brain → TTS orchestration, radio playback.
- **brain**: Tacview ACMI 2.x state, deterministic grammar, AWACS/ATC responders and monitoring, original aircraft/airfield encyclopedia and phraseology.
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
```

Set comma-separated `STT_URLS` / `TTS_URLS` to remote worker base URLs. Set common `API_TOKEN` for Bearer authentication; use private LAN/VPN or a TLS proxy. Transport/5xx failures fail over, but invalid requests and redirects do not. Do not place STT on sunman's DCS cores.

ATC runway selection requires **explicit** `ATC_WIND_FROM_DEG` (number or per-airfield JSON); Tacview has no wind. Demo explicitly uses 0. Shared monitors provide warning-radius calls and ATC taxi/inbound follow-ups. Notifications preserve coalition, frequency, generation, controller revision and expiry; canceled/stale instructions are revalidated before playback.

## APIs

| Component | Request | Response |
|---|---|---|
| STT | `POST /transcribe`, `audio/pcm`, 16 kHz mono s16le, 0.1–15 s; optional `X-Prompt` | JSON `{text, confidence}` |
| TTS | `POST /synthesize`, JSON `{text, voice?}` | 16 kHz mono s16le `audio/pcm` |
| Brain | `POST /respond`, JSON `{text, coalition, channel?}` | JSON `{reply, parsed, notes}` |
| Brain | `POST /events`, `{coalition, channel}` | Channel-scoped notifications and mission-local vocabulary hints |
| Brain | `POST /validate-event`, returned event metadata | `{valid}` for immediate pre-playback validation |

All services expose `/healthz`, `/readyz`, `/metrics`. Stale telemetry refuses tactical requests rather than fabricating positions. Unknown/ambiguous calls remain unanswered or receive an explicit negative.

## Checks and evidence

```sh
python3 -m unittest discover -s tests -v
sh spike/run.sh       # historical old-client casing incompatibility
sh spike/audio.sh     # current SRS receive/WAV/playback proof
sh scripts/benchmark_remote.sh leafman  # isolated SSH/user-namespace benchmark; no Kubernetes
```

103 behavioral tests pass, with 525 individually checked parser corpus records and no skipped cases. Original labels remain preserved where 17 corrections/policy decisions are documented in `data/PROVENANCE.md`. Benchmarks include actual E5-2650 v2 execution, not just AVX-disabled builds on newer CPUs; QEMU Ivy Bridge additionally exercised both speech engines.

SRS audio gate: 16,000 samples sent, captured and played back; 98.64% returned 440 Hz tone energy. Original bot's `ExternalAwacsModePassword` casing does not match current SRS `ExternalAWACSModePassword`; rewrite implements current protocol directly.

Known limits include proper-name speech errors, no human-radio accuracy certification, finite bundled ATC coverage, explicit wind configuration, no terrain masking/radio decryption, and no mission-specific gRPC task-dispatch service. These are detailed in the handoff, not hidden behind fake successful replies.

## Upstream and licensing

Derived from [OverlordBot](https://gitlab.com/overlordbot/srs-bot), commit `c7d34d51bfe66faf6bb8ff9e22688351e99cee70`, copyright Jeffrey Jones. Modified source is offered at https://github.com/neboman11/overlordbot-local under **AGPL-3.0-or-later**; full terms in [LICENSE](LICENSE), asset provenance in [data/PROVENANCE.md](data/PROVENANCE.md).

SRS reference: [2.4.1.0](https://github.com/ciribob/DCS-SimpleRadioStandalone/tree/f7bdcd42d41eff1e912cd0465b8a22c78108c53c). Demo image preserves its GPL license/source offer. Speech dependency/model sources and licenses are in [speech/NOTICE.md](speech/NOTICE.md) and `models.lock.json`; every model/config/card has a commit-pinned URL and SHA256. Research-restricted Lessac voice is not shipped.

No Kubernetes APIs, objects, services, or argocd-definitions files were changed. Physical leafman measurements used temporary standalone user namespaces and no host package installation.
