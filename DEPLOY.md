# Deployment handoff

## Status and scope

Local-only SRS 2.4.1.0 AWACS/ATC rewrite. Deployment is separate: **no Kubernetes API calls, objects, services, or argocd-definitions files were touched**. Physical Xeon benchmarks used existing SSH access to leafman, an isolated unprivileged user/mount/PID namespace, and temporary files removed afterward; no host packages or daemons were installed.

The current-client SRS gate passed: EAM blue authentication, 251 MHz AM UDP Opus reception into a 16 kHz WAV, then WAV playback over SRS to a second client. Evidence: `spike/evidence/audio-roundtrip.json`. Historical old-client JSON casing mismatch is not a rewrite blocker.

## Images and CPU requirements

Images are built for `linux/amd64` by `.github/workflows/images.yml`. Action references are pinned to full commit SHAs. Runtime commands are shared-image subcommands, not four duplicate images.

Published source: `3a3e881f22e8a58845cb745544f0855459eef051`. [Release workflow 43077999368](https://github.com/neboman11/overlordbot-local/actions/runs/43077999368) passed all 103 tests, published all images, then anonymously pulled those public packages on a fresh runner and passed four actual SRS speech calls. Machine-readable references: `benchmarks/images.json`. Pin digests, not mutable local `:dev` tags.

| Components | Full image reference (tag + digest) |
|---|---|
| radio-gateway, brain, demo Tacview replay | `ghcr.io/neboman11/overlordbot-local-runtime:3a3e881f22e8a58845cb745544f0855459eef051@sha256:9bb5ee49cb359589b1324d4bd96a9437ab0b46d41a8937aa0f413b063c778681` |
| stt-worker, tts-worker | `ghcr.io/neboman11/overlordbot-local-speech:3a3e881f22e8a58845cb745544f0855459eef051@sha256:57572acd93079c414f21ad2f20527f70bb753675d9473de9c9c7bfce9c414a12` |
| demo SRS 2.4.1.0 | `ghcr.io/neboman11/overlordbot-local-srs:3a3e881f22e8a58845cb745544f0855459eef051@sha256:b2cfb9fa8dafcef895cba2bfaee6df7f07cae5572f40875e4c36cad36d065451` |

- Runtime image: gateway, brain and local Tacview replay. Python/libopus; no AVX2 baseline.
- Speech image: both workers; baked whisper tiny.en and Piper LJSpeech voice. whisper.cpp explicitly enables AVX, F16C and SSE4.2 and disables AVX2, FMA, BMI2, AVX-512, AMX, native tuning, GPU backends, BLAS and dynamic CPU variants. Static binary instruction audit runs during the build. Host libraries and ONNX Runtime use runtime dispatch.
- SRS image: unmodified official 2.4.1.0 Linux release, SHA256 verified. Only needed for local verification, not alongside the already deployed SRS server. GPL source offer and license are included in the image.

## Components and connections

| Component | Command | Listening ports | Outbound connections | Near SRS? |
|---|---|---|---|---|
| radio-gateway | `python -m overlord radio-gateway` | HTTP TCP 8080 | SRS TCP+UDP 5002; STT HTTP 8082; brain HTTP 8081; TTS HTTP 8083 | Yes, latency-sensitive |
| brain | `python -m overlord brain` | HTTP TCP 8081 | Tacview RT TCP 42674 | Preferred, lightweight |
| stt-worker | `python -m overlord stt-worker` | HTTP TCP 8082; internal whisper loopback TCP 18080 | None during inference | No; another LAN machine/node is supported |
| tts-worker | `python -m overlord tts-worker` | HTTP TCP 8083 | None during inference | No; another LAN machine/node is supported |
| SRS test server | `/usr/local/bin/srs-server --clientExportPath /tmp/clients.json` plus EAM flags | TCP+UDP 5002 | Upstream server attempts update/telemetry egress; blocked in demo | Demo only |
| Tacview replay | `python scripts/replay_tacview.py --file fixtures/replay.acmi` | TCP 42674 | None | Demo only |

Worker URLs are comma-separated ordered failover lists. Transport failures and HTTP 5xx move to the next endpoint; HTTP 4xx and redirects are rejected, not retried. Workers keep models in memory, but no per-player/session state. Scale independent instances and point gateway at their URLs. Inference calls are bounded and CPU workloads must be scheduled away from DCS-exclusive cores. **Do not place STT on sunman.**

## Configuration

Environment variables are read at process startup. Paths are image-internal unless a model mount is provided. HTTP POSTs accept an optional common Bearer token. Use private LAN/VPN or TLS proxy; do not expose native SRS/Tacview or worker inference endpoints indiscriminately.

| Key | Default | Meaning | Secret? |
|---|---|---|---|
| `HTTP_HOST` | `0.0.0.0` | Bind address, all four services | No |
| `HTTP_PORT` | gateway 8080; brain 8081; STT 8082; TTS 8083 | HTTP listen port; set explicitly if overriding image healthcheck default | No |
| `API_TOKEN` | empty | Common Bearer token for POST authentication; empty disables auth, suitable only for isolated demo | **Yes** |
| `SRS_HOST` | `srs` | Existing SRS hostname/address | No |
| `SRS_PORT` | `5002` | SRS TCP and UDP port | No |
| `SRS_EAM_PASSWORD` | empty | Password for selected SRS coalition; required for successful EAM | **Yes** |
| `COALITION` | `2` | 1 red, 2 blue | No |
| `SRS_FREQUENCY_HZ` | `251000000` | Single radio frequency, Hz | No |
| `SRS_MODULATION` | `0` | 0 AM, 1 FM | No |
| `BOT_CALLSIGN` | `Overlord` | Gateway display name and brain parser receiver | No |
| `EOT_SECONDS` | `0.2` | Silence between UDP packets declaring end of transmission, accepted range 0.08–2 | No |
| `STT_PROMPT` | `Overlord, Viper one one, bogey dope, request picture, declare, inbound, ready to taxi, airfield, BRAA.` | Whisper vocabulary bias; tailor callsigns/airfields for mission | No |
| `STT_URLS` | `http://stt-worker:8082` | Ordered remote STT base URLs, comma-separated | No; do not embed credentials in URLs |
| `TTS_URLS` | `http://tts-worker:8083` | Ordered remote TTS base URLs, comma-separated | No |
| `BRAIN_URL` | `http://brain:8081` | Brain base URL | No |
| `TACVIEW_HOST` | `tacview` | Existing real-time telemetry hostname | No |
| `TACVIEW_PORT` | `42674` | Telemetry TCP port | No |
| `TACVIEW_PASSWORD` | empty | Tacview password, encoded CRC-64/WE over UTF-16LE for handshake | **Yes** |
| `TACVIEW_STALE_SECONDS` | `30` | Readiness cutoff since telemetry activity; disconnected state is reset | No |
| `BRAIN_ROLES` | `awacs,atc` | Comma-separated enabled controller roles | No |
| `ATC_WIND_FROM_DEG` | empty | Explicit wind-from degrees 0–359, or JSON airfield-name-to-degrees map; absent wind refuses active-runway selection. Demo explicitly sets 0. | No |
| `AWACS_PILOT` | empty | Optional live AWACS pilot/name prefix required for AWACS station availability | No |
| `WHISPER_MODEL` | `/models/ggml-tiny.en.bin` | STT model file, baked by default | No |
| `WHISPER_SERVER` | `/usr/local/bin/whisper-server` | Persistent local native server binary | No |
| `WHISPER_THREADS` | `4` | STT CPU threads | No |
| `WHISPER_PORT` | `18080` | Internal native server loopback port, not an external worker API | No |
| `WHISPER_STARTUP_TIMEOUT` | `120` | Model loading deadline, seconds | No |
| `WHISPER_REQUEST_TIMEOUT` | `60` | Internal native inference deadline, seconds | No |
| `PIPER_MODEL` | `/models/en_US-ljspeech-medium.onnx` | Loaded TTS model | No |
| `PIPER_CONFIG` | `PIPER_MODEL` + `.json` | Piper model metadata/config | No |
| `PIPER_VOICE` | `en_US-ljspeech-medium` | Sole allowed voice identifier per worker | No |
| `PIPER_THREADS` | `0` | ONNX intra-op threads; 0 is engine default. **Set 4 on Xeon**; demo uses 4. | No |

No Discord token is used or supported; DCSServerBot remains its owner. No LLM credentials, Azure credentials, DCS-gRPC endpoint or cloud fallback exists.

Auxiliary configuration:

- `CONTAINER_RUNTIME=podman`: `spike/run.sh` and `spike/audio.sh`; override with `docker`.
- `scripts/stack.sh` selects installed podman-compose, otherwise Docker Compose; generates a fresh disposable `SRS_EAM_PASSWORD` when absent. `.env` is ignored by git. It accepts `build`, `up`, `verify`, `down`.
- `scripts/replay_tacview.py`: `--host 0.0.0.0`, `--port 42674`, `--file fixtures/replay.acmi`, `--interval 1`, `--password ''`. Password is secret; replay only.
- `scripts/benchmark_speech.py`: `--models tiny,base,small`, `--models-dir` from `MODEL_DIR` or `/models`, `--threads` from `WHISPER_THREADS` or 4 (comma-list supported), `--runs 3`, `--port` from `WHISPER_PORT` or 18080, `--fetch`, `--prompt`, `--output`.
- `scripts/measure_stack.py`: `--runtime podman|docker`, `--output artifacts/resources.json`, then actual load command after `--`.
- `scripts/record_corpus.py`: no arguments; run inside speech image with `/output` mounted to `fixtures/radio`. Uses baked LJSpeech model and zero synthesis noise, writes 16 kHz mono WAVs plus SHA256/text manifest. These are fixed synthetic functional inputs, not human accuracy measurements.
- Dockerfile build inputs: pinned `PYTHON_IMAGE`; speech `WHISPER_CPP_REPOSITORY` and `WHISPER_CPP_COMMIT` from `models.lock.json`. `WHISPER_CPP_COMMIT` in speech runtime is provenance, not an inference selector. `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=/app` are fixed image settings. SRS image fixes `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` and writable `DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/dotnet`.

## Models, licenses and storage

`models.lock.json` is the authoritative inventory: **every downloaded model/config/card has an immutable commit URL, SHA256, size and license**. `scripts/fetch_models.py` verifies bytes before accepting a download. Python dependencies are exact versions plus hashes in `requirements-speech.txt`. Runtime inference makes no download requests.

| Asset | Bytes | Use |
|---|---:|---|
| whisper tiny.en | 77,704,715 | Default baked STT |
| whisper base.en | 147,964,211 | Benchmark/optional mounted replacement |
| whisper small.en | 487,614,201 | Benchmark/optional mounted replacement |
| Piper LJSpeech medium | 63,531,379 | Default baked TTS |
| Piper config | 4,972 | Native 22050 Hz, accurately resampled to 16000 Hz PCM |
| Piper model card | 517 | License/provenance |

Pinned whisper source: `https://github.com/ggml-org/whisper.cpp`, commit `d1be6fde11ac6e0407606b4e42fe72d34add8037`. Whisper model URLs use Hugging Face revision `5359861c739e955e79d9a303bcbc70fb988958b1`; voice URLs use `rhasspy/piper-voices` revision `cf910fb59f1d79ab3b625a63993a108751c6c25d`. Full URLs and SHA256 values are committed in `models.lock.json`, not mutable branch references.

Pinned download URLs and SHA256:

- `ggml-tiny.en.bin`: https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-tiny.en.bin
  - `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`
- `ggml-base.en.bin`: https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.en.bin
  - `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002`
- `ggml-small.en.bin`: https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small.en.bin
  - `c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d`
- `en_US-ljspeech-medium.onnx`: https://huggingface.co/rhasspy/piper-voices/resolve/cf910fb59f1d79ab3b625a63993a108751c6c25d/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
  - `6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22`
- `en_US-ljspeech-medium.onnx.json`: https://huggingface.co/rhasspy/piper-voices/resolve/cf910fb59f1d79ab3b625a63993a108751c6c25d/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json
  - `141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860`
- Voice model card: https://huggingface.co/rhasspy/piper-voices/resolve/cf910fb59f1d79ab3b625a63993a108751c6c25d/en/en_US/ljspeech/medium/MODEL_CARD
  - `fbee1529c89d36b3fe76d7e9f3f832dce17f44900a52d76a9bda735654766b4d`

No PVC, database or shared worker storage is needed. Models are baked read-only; worker processes need writable temporary space. Gateway buffers at most 32 active speakers, 15 seconds per segment, and four queued transmissions. Domain/game state is ephemeral and rebuilt from Tacview. Restart loses transient state. Demo artifacts and optional benchmark model downloads are local verification files, not required deployment volumes.

Source is AGPL-3.0-or-later with original Jeffrey Jones attribution and ported asset provenance. whisper.cpp/Whisper weights are MIT; Piper engine is GPL-3.0-or-later; selected LJSpeech voice/dataset are public domain. See `speech/NOTICE.md`, `data/PROVENANCE.md`, `LICENSE`, and SRS image's included source offer. The research-restricted Lessac voice is not shipped.

## Measured latency and resources

Physical **Intel Xeon E5-2650 v2**, 16 visible logical CPUs, four whisper threads, AVX/F16C present and AVX2/FMA/BMI absent. Eight synthetic brevity utterances, roughly 2.6–5.4 seconds each; model already resident, OS page cache not forcibly cold. Full per-utterance text/timing, build flags and raw results: `benchmarks/speech-xeon-avx.json`.

| STT model | Warm median seconds | Warm max seconds | Process RSS MiB | Raw word-error rate |
|---|---:|---:|---:|---:|
| tiny.en | 0.9598 | 0.9894 | 204.7 | 46.7% |
| base.en | 2.1228 | 2.2064 | 296.5 | 42.5% |
| small.en | 7.8550 | 8.0988 | 691.1 | 39.4% |

Raw WER includes spoken-number versus digit differences and proper-name errors; it is **not command correctness or human-radio accuracy**. Parser checks separately cover 525 records, including the original corpus and 24 measured model transcripts. No human/friends' recordings were supplied or present upstream: PLAN milestone 4's human-recording acceptance remains pending. It must not be inferred from synthetic checks.

Piper LJSpeech on the same physical Xeon:

| `PIPER_THREADS` | Warm synthesis median seconds | Warm max seconds | Process RSS MiB |
|---|---:|---:|---:|
| 0 (runtime default) | 0.9272 | 1.4063 | 343.2 |
| 4 (recommended) | 0.2639 | 0.4215 | 290.5 |

Four-thread result: `benchmarks/speech-xeon-tuned.json`; tiny STT median 0.9539 seconds. **Choose tiny.en and set both thread counts to 4 for leafman/stickman.** A rough sum of separately measured medians plus the 0.2-second EOT timeout is 1.418 seconds, excluding LAN/HTTP/codec/reply-length variation. This is an estimate, **not a measured full-Xeon radio guarantee**; base/small do not meet the 1.5-second target on that CPU. QEMU Ivy Bridge additionally passed both speech engines (`benchmarks/avx-compatibility.json`); emulated duration is not hardware latency.

Complete real SRS → STT → brain → Piper → SRS verification on **Ryzen 7 7700X**, same AVX-only whisper build, four-thread speech workers, fixed SHA-checked synthetic caller WAVs:

| Call | End of caller transmission to first received reply packet, seconds |
|---|---:|
| Bogey dope | 0.6606 |
| Picture | 0.6877 |
| Sukhumi location | 0.6629 |
| Anapa inbound ATC | 1.0036 |

All four distinct spoken replies passed, including inbound runway/vector content. `benchmarks/e2e-ryzen.json` preserves recognized replies and audio levels. Packet timing excludes the player's audio/jitter-buffer delay. Published images also pass four-call GitHub Actions integration; those hosted-runner latencies were 1.3834–1.7329 seconds (`benchmarks/e2e-github-actions.json`), including brevity calls above the 1.5-second target. That run proves functionality, not the target SLO; it is neither a physical Xeon nor LAN deployment measurement.

Measured **container** resources on Ryzen during that successful four-call run: three ready-idle samples, 52 load samples, approximately one-second sampling. CPU derives from cumulative CPU-time differences; **100% = one core**. Memory is decimal MB, not MiB. Raw data: `benchmarks/resources-ryzen.json`; derived table: `benchmarks/resources-summary.json`.

| Component | Idle CPU mean % | Load CPU observed peak % | Idle memory mean MB | Load memory observed peak MB |
|---|---:|---:|---:|---:|
| radio-gateway | 0.06 | 16.34 | 25.48 | 37.99 |
| brain | 0.00 | 15.39 | 27.01 | 38.41 |
| stt-worker (tiny) | 0.00 | 133.37 | 305.70 | 316.90 |
| tts-worker | 0.00 | 94.43 | 567.73 | 592.60 |
| local SRS server | 0.03 | 11.11 | 55.72 | 58.78 |
| Tacview replay | 0.00 | 7.01 | 18.58 | 18.87 |

Short sampling rounds idle values to zero and averages sub-second inference bursts. These are observed demo peaks, not worst-case reservations; allow memory headroom and four runnable cores per worker when reproducing four-thread latency. Load-test longer/concurrent replies before imposing production limits. Bare-process Xeon RSS above is not interchangeable with complete-container memory.

## Server-side changes

1. Keep existing SRS 2.4.1.0 server on TCP+UDP 5002. Enable External AWACS Mode and set separate non-empty coalition passwords through the existing sidecar's `SRS_EAM_BLUE_PASSWORD` / `SRS_EAM_RED_PASSWORD` settings. Supply matching selected password as gateway `SRS_EAM_PASSWORD`. Do not put actual passwords in this document.
2. Enable coalition audio security; gateway additionally checks sender coalition metadata and rejects unknown/wrong-coalition packets. Select mission frequency/modulation (default 251.000 AM blue). Run another gateway for another coalition/frequency set.
3. Tacview RT exporter must expose TCP 42674 while a mission is running unpaused. Set a password if reachable beyond trusted network; match `TACVIEW_PASSWORD`. Preserve aircraft pilot/callsign and coalition/color properties. No live telemetry recording was taken from the cluster.
4. No DCS-gRPC installation is required for implemented telemetry-driven behavior. Richer DCS event/assignment APIs are not silently invented from ACMI.
5. Place speech workers on leafman/stickman or desktop with direct LAN HTTP connectivity, not on sunman's DCS cores. Do not expose the whisper-native loopback port; expose only the stateless worker API.

## Health and metrics

Every service exposes `GET /healthz` for process liveness, `GET /readyz` for dependencies/model/state, and `GET /metrics` in Prometheus text format.

- Gateway ready: connected to SRS and at least one ready endpoint for STT, TTS and brain. It reconnects after DNS/connection failure.
- Brain ready: telemetry has positioned units and remains fresh. Disconnect/reconnect clears world state; stale telemetry is refused.
- STT ready: own persistent native process alive and its model-loaded `/health` succeeds.
- TTS ready: model loaded and warm-up produced real audio.
- Test SRS: TCP 5002 connect check; complete capability check is EAM + UDP audio, not TCP alone.
- Replay: TCP 42674 connect check; complete state check is brain readiness/fixture assertions.

Metrics include `overlord_transmissions_total`, `overlord_stt_seconds_sum/count`, `overlord_tts_seconds_sum/count`, `overlord_reply_start_seconds_sum/count`, parse failures, dropped transmissions, invalid packets, coalition rejections, SRS disconnects and pipeline/service errors. Gateway emits JSON reply logs including transcript, generated reply and end-of-transmission-to-reply-start latency. These logs contain player speech; apply appropriate retention/privacy policy. No real credentials are logged by our services; the upstream SRS test server does print its disposable password.

HTTP admission is bounded to 32 connections before thread creation, four in-flight POST bodies/operations, 1 MiB body length, and an absolute 30-second header/body deadline. Redirects are disabled so shared Bearer tokens cannot be forwarded to another origin.

## Verification steps

1. Build and run `sh scripts/stack.sh build` then `sh scripts/stack.sh verify`. Demo uses local SRS, replayed ACMI, real local STT/TTS and actual radio packets. No runtime egress route exists on its internal network.
2. Verify all `/readyz` endpoints return 200; `/healthz` alone does not establish dependencies.
3. In DCS, tune selected coalition to 251.000 AM and say **"Overlord, Viper one one, bogey dope"**. Pilot/callsign in telemetry must match. Expected: BRAA for the nearest hostile aircraft (or clean when none), never friendly/unknown-coalition contacts as hostile.
4. Say **"Overlord, Viper one one, request picture"** and **"Overlord, Viper one one, where is Sukhumi"**. Verify distinct tactical picture vs airfield-location responses, not merely that some audio played. With explicit wind configured, **"Anapa approach, Viper one one, inbound"** must produce runway/vector instructions for the matching pilot; otherwise an explicit negative.
5. Change `STT_URLS`/`TTS_URLS` to remote worker addresses and ensure POST authentication matches. Stop one worker and verify ordered failover; it may add the failed endpoint's timeout to reply latency.
6. Stop/pause telemetry beyond stale timeout: brain must become unready/refuse stale tactical replies. Resume a full telemetry connection and confirm state is rebuilt.
7. Reproduce target measurements with `sh scripts/benchmark_remote.sh leafman` (all three models) or `sh scripts/benchmark_remote.sh leafman tiny` (recommended four-thread TTS). Requires existing verified SSH and unprivileged namespaces; does not use Kubernetes or install host packages. Actual end-to-end LAN latency still depends on network and chosen reply length.

## Known limits

- Physical Xeon E5-2650 v2 inference is measured; complete radio reply timings were measured on Ryzen. Human/friends' SRS recordings were not available: corpus/audio checks use explicitly labeled synthetic local Piper speech, not human accuracy certification.
- Proper nouns remain a weakness: fresh synthetic calls have produced “and Afra/and our for” for Anapa and occasional ignored calls. Fixed recordings make functional regression repeatable; they do not hide or solve general acoustic accuracy. Reply retranscription can also alter numbers/names. Validate human SRS recordings and operational phraseology before relying on ATC instructions.
- One frequency and modulation per gateway. Reply path is half-duplex; receive audio during own transmission is discarded. No radio encryption/decryption, terrain line-of-sight model or concurrent overlapping-voice separation is implemented.
- End-of-transmission detection is packet inactivity, not a DCS PTT signal. Long pauses can split a transmission. Segments are capped at 15 seconds; overload drops rather than retaining unbounded audio.
- Whisper confidence is model token probability summary when present, not calibrated command correctness. Deterministic grammar deliberately ignores unrecognized/unaddressed calls rather than consulting a cloud/LLM fallback.
- Tacview positions/metadata cannot prove DCS runway occupancy, clearance authority or arbitrary mission-task assignment. Those limitations must remain visible in responses and operator acceptance.
- Native SRS/Tacview traffic is plaintext. Keep it on trusted network/VPN. Demo SRS attempts upstream checks/telemetry; its internal network blocks them. Official SRS shutdown may emit `ObjectDisposedException` after successful exchanges.
- apt packages follow the pinned base image's distribution repositories; Python packages, models, speech source, image base and GitHub actions are pinned explicitly.
