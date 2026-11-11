# OverlordBot Local

Local-only voice AWACS/ATC for DCS World over SRS 2.4.1.0. Speech workers can run on another LAN machine; gateway stays near SRS. No Azure, LUIS, cloud speech APIs, Discord dependency, DCS modification, or Kubernetes manifests.

## Architecture and runtime

- `radio-gateway`: current SRS EAM client, TCP control, UDP Opus, per-speaker end-of-transmission segmentation, STT → brain → TTS orchestration, radio playback.
- `brain`: Tacview ACMI 2.x state, deterministic brevity parser, ported domain logic and phraseology, bundled aircraft/airfield encyclopedia.
- `stt-worker`: stateless HTTP boundary around persistent local whisper.cpp model.
- `tts-worker`: stateless HTTP boundary around local Piper voice.

Python standard library was chosen for network/control/domain services: direct, inspectable protocols, no web framework/dependency stack, straightforward port of the small domain model. Native whisper.cpp, Piper/ONNX and libopus do compute-heavy work. Independent HTTP endpoints allow workers to move off the DCS host without shared storage or session affinity. No optional LLM is required.

## Run locally

Linux amd64; Docker Compose or Podman + podman-compose; internet needed only for building/downloading pinned dependencies/models. Inference does not need internet. The local stack uses an **internal container network**, with no runtime internet route or published host ports.

```sh
sh scripts/stack.sh build
sh scripts/stack.sh verify
sh scripts/stack.sh down
```

`verify` launches SRS, replayed Tacview, all four services, synthesizes radio calls using real local Piper, sends them over SRS, receives spoken replies, and retranscribes those replies for assertions. Generated audio and measured results go in ignored `artifacts/`. This is a synthetic corpus, not human voice or live DCS certification.

To run services separately:

```sh
python -m overlord radio-gateway
python -m overlord brain
python -m overlord stt-worker
python -m overlord tts-worker
```

Set `STT_URLS` and `TTS_URLS` to comma-separated remote worker base URLs. Endpoints fail over on transport errors and server failures, not malformed requests. Set `API_TOKEN` identically on services for Bearer authentication. Use a private LAN/VPN or TLS proxy; SRS and Tacview native protocols are not encrypted.

## Worker contracts

- `POST /transcribe`: `Content-Type: audio/pcm`, mono signed 16-bit little-endian PCM at **16000 Hz**, 0.1–15 seconds. Optional `X-Prompt` header. Returns JSON `{text, confidence}`; confidence may be null when unavailable from the engine.
- `POST /synthesize`: JSON `{text, voice?}`. Returns `audio/pcm`, mono s16le at **16000 Hz**.
- `POST /respond` on brain: JSON `{text, coalition}`; returns `{reply, parsed}`. Unaddressed or unknown calls have no reply. Stale telemetry returns 503 rather than fabricated contacts.
- All services: `/healthz`, `/readyz`, `/metrics`. HTTP POST bodies capped at 1 MiB, concurrent in-flight processing capped at four per process. Native engine calls are serialized by each model adapter.

## Verification

```sh
python3 -m unittest discover -s tests -v
sh spike/run.sh     # historical old-client casing incompatibility reproduction
sh spike/audio.sh   # real current-client SRS audio/WAV round trip
```

The audio spike passed against official SRS 2.4.1.0: 16,000 input samples, 16,000 captured samples, 16,000 playback samples, 98.64% returned 440 Hz tone energy. See `spike/evidence/audio-roundtrip.json`. `spike/audio.sh` uses host `/usr/lib/libopus.so.0`; normal service images install libopus themselves.

The old client used `ExternalAwacsModePassword`; SRS now expects `ExternalAWACSModePassword`. That old-client incompatibility is preserved as historical evidence, **not a blocker for this rewrite**, which implements current protocol. Successful EAM authentication alone is not audio proof; the separate audio spike exercises UDP Opus in both directions.

Telemetry parser/replay tests cover deltas, reference offsets, sparse coordinates, deletion, coalition filtering, invalid/non-finite coordinates, stale state, reset/reconnect, and password handshake. Real mission-specific exporter behavior and human speech accuracy still require operator validation.

## Upstream and license

Derived from [OverlordBot](https://gitlab.com/overlordbot/srs-bot), revision `c7d34d51bfe66faf6bb8ff9e22688351e99cee70`, copyright Jeffrey Jones. Licensed AGPL-3.0-or-later; [LICENSE](LICENSE) contains full terms. Modified source is offered at https://github.com/neboman11/overlordbot-local. Data/case provenance is recorded with the ported assets.

SRS protocol reference: [2.4.1.0](https://github.com/ciribob/DCS-SimpleRadioStandalone/tree/f7bdcd42d41eff1e912cd0465b8a22c78108c53c). Its unmodified Linux server is downloaded from a pinned release URL and checked against SHA256 `46268dd35204d04426974f66d25eaddaebbaa6b133a7bd227675fae30ed88127`. The official server attempts update checks and telemetry and logs its EAM test password; the integration network blocks outbound access. Its observed shutdown `ObjectDisposedException` occurs after successful radio exchanges.

No clusters or argocd-definitions were changed. Deployment and all measured limitations are documented in the deployment handoff when packaging verification is complete.
