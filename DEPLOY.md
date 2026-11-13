# Deployment handoff

## Status and scope

Local-only SRS 2.4.1.0 AWACS/ATC/JTAC/carrier rewrite. Deployment is separate: **no Kubernetes API calls, objects, services, or argocd-definitions files were touched**. Physical Xeon benchmarks used existing SSH access to leafman, an isolated unprivileged user/mount/PID namespace, and temporary files removed afterward; no host packages or daemons were installed.

The current-client SRS gate passed: EAM blue authentication, 251 MHz AM UDP Opus reception into a 16 kHz WAV, then WAV playback over SRS to a second client. Evidence: `spike/evidence/audio-roundtrip.json`. Historical old-client JSON casing mismatch is not a rewrite blocker.

## Images and CPU requirements

Images are built for `linux/amd64` by `.github/workflows/images.yml`. Action references are pinned to full commit SHAs. Runtime commands are shared-image subcommands, not four duplicate images.

Published source: `3a3e881f22e8a58845cb745544f0855459eef051`. [Release workflow 43077999368](https://github.com/neboman11/overlordbot-local/actions/runs/43077999368) passed all 103 tests, published all images, then anonymously pulled those public packages on a fresh runner and passed four actual SRS speech calls. Machine-readable references: `benchmarks/images.json`. Pin digests, not mutable local `:dev` tags.

| Components | Full image reference (tag + digest) |
|---|---|
| radio-gateway, brain, demo Tacview replay | `ghcr.io/neboman11/overlordbot-local-runtime:3a3e881f22e8a58845cb745544f0855459eef051@sha256:9bb5ee49cb359589b1324d4bd96a9437ab0b46d41a8937aa0f413b063c778681` |
| stt-worker, tts-worker | `ghcr.io/neboman11/overlordbot-local-speech:3a3e881f22e8a58845cb745544f0855459eef051@sha256:57572acd93079c414f21ad2f20527f70bb753675d9473de9c9c7bfce9c414a12` |
| demo SRS 2.4.1.0 | `ghcr.io/neboman11/overlordbot-local-srs:3a3e881f22e8a58845cb745544f0855459eef051@sha256:b2cfb9fa8dafcef895cba2bfaee6df7f07cae5572f40875e4c36cad36d065451` |

These published images describe the historical baseline. **Build the current source
for local NLP, AWACS extensions, and the DCS JTAC/carrier/airfield companions**; those additions are
not present in the release digests above.

- Runtime image: gateway, brain and local Tacview replay. Python/libopus; no AVX2 baseline.
- Speech image: both workers; baked whisper tiny.en and Piper LJSpeech voice. whisper.cpp explicitly enables AVX, F16C and SSE4.2 and disables AVX2, FMA, BMI2, AVX-512, AMX, native tuning, GPU backends, BLAS and dynamic CPU variants. Static binary instruction audit runs during the build. Host libraries and ONNX Runtime use runtime dispatch.
- SRS image: unmodified official 2.4.1.0 Linux release, SHA256 verified. Only needed for local verification, not alongside the already deployed SRS server. GPL source offer and license are included in the image.

## Components and connections

| Component | Command | Listening ports | Outbound connections | Near SRS? |
|---|---|---|---|---|
| radio-gateway | `python -m overlord radio-gateway` | HTTP TCP 8080 | SRS TCP+UDP 5002; STT HTTP 8082; brain HTTP 8081; TTS HTTP 8083 | Yes, latency-sensitive |
| carrier-gateways | `python -m overlord carrier-gateways` | HTTP TCP 8084 | One SRS TCP+UDP client per enabled carrier; shared STT, brain and TTS HTTP | Yes |
| airfield-gateways | `python -m overlord airfield-gateways` | HTTP TCP 8085 | One SRS client per native coalition/frequency/modulation channel; shared speech and brain HTTP | Yes |
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
| `HTTP_HOST` | `0.0.0.0` | Bind address, all services | No |
| `HTTP_PORT` | gateway 8080; carrier supervisor 8084; airfield supervisor 8085; brain 8081; STT 8082; TTS 8083 | HTTP listen port; set explicitly if overriding image healthcheck default | No |
| `API_TOKEN` | empty | Common Bearer token for POST authentication; mandatory for native carrier or airfield control | **Yes** |
| `SRS_HOST` | `srs` | Existing SRS hostname/address | No |
| `SRS_PORT` | `5002` | SRS TCP and UDP port | No |
| `SRS_EAM_PASSWORD` | empty | Password for selected SRS coalition; required for successful EAM | **Yes** |
| `COALITION` | `2` | 1 red, 2 blue | No |
| `SRS_FREQUENCY_HZ` | `251000000` | AWACS/legacy ATC frequency, Hz; never overrides native carrier or airfield radios | No |
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
| `BRAIN_ROLES` | `awacs,atc` | Enabled roles: `awacs`, `atc`, `jtac`, `carrier`, `airfield`; native `airfield` supersedes legacy `atc` when both are listed | No |
| `ATC_WIND_FROM_DEG` | empty | Legacy ATC only: explicit wind-from degrees or airfield JSON map. Native airfield ATC uses DCS weather instead. | No |
| `AWACS_PILOT` | empty | Optional live AWACS pilot/name prefix required for AWACS station availability | No |
| `LLM_URL` | empty/disabled | Local OpenAI-compatible endpoint; bare host URL uses `/v1/chat/completions`, or provide the complete route | No |
| `LLM_MODEL` | empty | Installed local model name; required when `LLM_URL` is set | No |
| `LLM_TIMEOUT_SECONDS` | `3` | Local inference timeout, positive and at most 60 seconds | No |
| `AWACS_BULLSEYE` | empty | JSON `[latitude, longitude]` fallback when no live coalition bullseye exists | No |
| `AWACS_HOME_AIRFIELD` | empty | Explicit canonical known field for `vector home`; not inferred from nearest base | No |
| `JTAC_CALLSIGN` | `Axeman` | Separate JTAC controller callsign and gateway display name | No |
| `JTAC_SRS_FREQUENCY_HZ` | `252000000` | JTAC radio frequency; must differ from AWACS `SRS_FREQUENCY_HZ` | No |
| `JTAC_SRS_MODULATION` | `0` | JTAC modulation, 0 AM or 1 FM | No |
| `DCS_BRIDGE_TOKEN` | empty | Required server-hook secret for JTAC, carrier or airfield; sent in `X-DCS-Token` | **Yes** |
| `DCS_STALE_SECONDS` | `10` | Maximum mission snapshot age before native live procedures are refused | No |
| `SRS_EAM_PASSWORDS` | empty | Native radio supervisors' server-only JSON map `{"1":"red password","2":"blue password"}`; legacy password applies only to `COALITION` | **Yes** |
| `CARRIER_GATEWAY_POLL_SECONDS` | `1` | Carrier registry reconciliation interval | No |
| `CARRIER_GATEWAY_STALE_SECONDS` | `15` | Registry failure grace before carrier SRS clients close; live clearances still require fresh DCS state | No |
| `AIRFIELD_GATEWAY_POLL_SECONDS` | `1` | Native airfield registry reconciliation interval | No |
| `AIRFIELD_GATEWAY_STALE_SECONDS` | `15` | Registry failure grace before airfield clients close; live clearances still require fresh mission state | No |
| `BRAIN_BIND_ADDRESS` | `127.0.0.1` | Host bind address for optional `compose.local-ai.yaml`; change to a private LAN/VPN address for a remote DCS host | No |
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

No Discord token is used or supported; DCSServerBot remains its owner. Local LLM inference is optional and uses no cloud credentials or fallback. DCS JTAC/carrier control uses the bundled companion protocol, not DCS-gRPC.

Auxiliary configuration:

- `CONTAINER_RUNTIME=podman`: `spike/run.sh` and `spike/audio.sh`; override with `docker`.
- `scripts/stack.sh` selects installed podman-compose, otherwise Docker Compose; generates a fresh disposable `SRS_EAM_PASSWORD` when absent. `.env` is ignored by git. It accepts `build`, `up`, `verify`, `down`.
- `scripts/replay_tacview.py`: `--host 0.0.0.0`, `--port 42674`, `--file fixtures/replay.acmi`, `--interval 1`, `--password ''`. Password is secret; replay only.
- `scripts/benchmark_speech.py`: `--models tiny,base,small`, `--models-dir` from `MODEL_DIR` or `/models`, `--threads` from `WHISPER_THREADS` or 4 (comma-list supported), `--runs 3`, `--port` from `WHISPER_PORT` or 18080, `--fetch`, `--prompt`, `--output`.
- `scripts/measure_stack.py`: `--runtime podman|docker`, `--output artifacts/resources.json`, then actual load command after `--`.
- `scripts/record_corpus.py`: no arguments; run inside speech image with `/output` mounted to `fixtures/radio`. Uses baked LJSpeech model and zero synthesis noise, writes 16 kHz mono WAVs plus SHA256/text manifest. These are fixed synthetic functional inputs, not human accuracy measurements.
- Dockerfile build inputs: pinned `PYTHON_IMAGE`; speech `WHISPER_CPP_REPOSITORY` and `WHISPER_CPP_COMMIT` from `models.lock.json`. `WHISPER_CPP_COMMIT` in speech runtime is provenance, not an inference selector. `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=/app` are fixed image settings. SRS image fixes `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` and writable `DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/dotnet`.

## Optional local intent interpretation

Run a local OpenAI-compatible server with a model you have already installed. For
example, with Ollama running on the same host as a native brain process:

```sh
export LLM_URL=http://127.0.0.1:11434
export LLM_MODEL=gemma4:e2b
python -m overlord brain
```

This model name is an example, not an automatic download or a requirement. The
classifier was smoke-tested against an installed Ollama model of this name.
Optional LLM weights remain operator-managed and are not baked into the speech
image or added to `models.lock.json`. Check their license and resource needs.

`LLM_URL` accepts only loopback/private/link-local destinations; DNS addresses
are checked before connection. Public destinations, URL credentials, redirects,
ambient proxies, oversized replies and extra model-output fields are rejected.
Use a bare server URL or the full `/v1/chat/completions` path, **not** `/v1` alone.
Standard grammar never calls inference. Only explicit controller/caller requests
with a uniquely identified live friendly caller qualify. JTAC fallback is restricted
to brief/status/BDA queries; procedure facts, numerical slots, task selection, and
marking/laser actions require deterministic grammar. Negated procedure statements
never enter inference. Unsupported/ambiguous output produces a fixed clarification.

The default Compose network is deliberately isolated. For an existing LLM on the
host/LAN, add `compose.local-ai.yaml`; use a reachable private address in `LLM_URL`.
Inside a container `127.0.0.1` means that container, not the host. The override
provides `host.docker.internal`, but the inference server must listen on a reachable
interface. Firewall it to trusted callers; do not expose Ollama publicly.

## Separate JTAC radio and private bridge

Configure `.env` (ignored) with `BRAIN_ROLES=awacs,atc,jtac`, a freshly generated
`DCS_BRIDGE_TOKEN`, and optionally a common `API_TOKEN`. Keep
`SRS_FREQUENCY_HZ=251000000` and `JTAC_SRS_FREQUENCY_HZ=252000000`, or choose two
different frequencies. Modulation differences alone do not count as separation.
Both gateways use the selected coalition and its EAM password.

```sh
docker compose -f compose.yaml -f compose.local-ai.yaml --profile jtac up -d --build
```

AWACS/ATC stays on the original gateway; Axeman gets its own SRS EAM client, receive
queue, speech pipeline and radio transmitter. Both use the same brain and can share
STT/TTS workers. The brain routes by coalition/frequency/modulation; JTAC calls on
AWACS frequency and AWACS calls on JTAC frequency are ignored. Live JTAC still needs
Tacview caller identity, the configured mission companion and fresh mission state.

`compose.local-ai.yaml` publishes brain TCP 8081 to loopback by default. For a DCS
server on another machine, set `BRAIN_BIND_ADDRESS` to a specific private LAN/VPN
address and restrict the firewall to that machine. Do not publish it on an
untrusted interface. `/dcs/exchange` requires `X-DCS-Token`; when `API_TOKEN` is set
the hook must also send `Authorization: Bearer ...`. Plain HTTP requires a trusted
LAN/VPN or a TLS tunnel. Never put these secrets in a downloadable mission.

JTAC replies are fixed templates from mission-authored facts. Missing brief fields,
bad readbacks, missing contact/restriction prerequisites and stale/dead targets
block clearance. Mark/laser requests remain pending until DCS confirms execution.
Abort invalidates pending work and replies already undergoing speech synthesis.
Laser lifecycle cleanup also runs inside the mission, including lease expiry.
No autonomous attack planning, ordnance selection, or real-world JTAC certification
is provided.

### Install the DCS companion

1. Copy `dcs/overlord_hook.lua` to the DCS server's
   `Saved Games/DCS/Scripts/Hooks/overlord_hook.lua` (use the actual instance
   directory, e.g. `DCS.server`). Restart that server instance.
2. Create server-only `Saved Games/DCS/Config/overlord-hook.lua`:

   ```lua
   return {
     host = "127.0.0.1", -- numeric private IPv4 for a remote brain
     port = 8081,
     bridgeToken = "<same value as DCS_BRIDGE_TOKEN>",
     -- apiToken = "<same value as API_TOKEN>", -- include when configured
     interval = 1,
     timeout = 3,
     state = "server",
   }
   ```

   Only canonical loopback/RFC1918 IPv4 literals are accepted: no DNS names or
   public destinations. Network I/O is nonblocking with an exchange deadline.
3. Adapt `dcs/jtac-config.example.lua` to the mission. Match controller and target
   unit/group/static names exactly. Add MISSION START / DO SCRIPT FILE actions:
   configuration first, then `dcs/overlord_jtac.lua`. Mission configuration ships
   inside the downloadable `.miz`: **never put tokens or passwords in it**.
4. Keep the mission sandbox intact. The companion needs no files, `os`, or sockets.
   The hook calls fixed mission entry points through `net.dostring_in`.
   If the DCS build restricts this bridge, use its official permission setting;
   do not blanket-desanitize `MissionScripting.lua`. The hook always targets the
   `server` Lua state, where mission scripts run; `state = "mission"` (the mission
   description, without `world`) is refused.
5. Tune JTAC frequency. Start with `Axeman, Viper one one, checking in`,
   `request task`, and `ready brief`; include controller and caller on every call.
   Read back requested critical fields with labels and exact numerical values.
   Then `contact`, `request laser`, `request mark`, `laser off`,
   `in heading two seven zero`, `off target`, `request BDA`, `say status`, and
   `abort` drive the authored procedure. The example requires a heading of
   240–300 degrees and readback of elevation, location, and remarks.

Control-type labels select **simplified simulation phraseology**, not certified
Type 1 visual acquisition or a complete doctrinal control model. Restrictions
come from the author. `brief.location` is a fixed authored briefing reference/area,
not an automatically recomputed live target grid. For moving targets, author an
appropriate fixed-area reference; do not describe it as a tracked coordinate.
The laser spot itself follows the live target.

Responses bind to the latest issued snapshot sequence and epoch. Old responses
cannot execute commands or renew authorization. Loss of valid brain responses
expires the mission-side spot lease even if the hook keeps polling. Restarting
the brain loses pilot sessions and requires fresh procedure steps.

On the target DCS build, verify both frequencies, wrong/partial readback refusal,
marking acknowledgements, prerequisites before clearance, pending-command abort,
target/controller death, brain transport loss/lease expiry, mission restart, and
suppression of revoked replies during TTS. `luajit tests/jtac_harness.lua` exercises
offline API doubles; it does not replace in-game/radio acceptance.


## Independent carrier operations

Build the current source. Add `carrier` to `BRAIN_ROLES`, set fresh nonempty
`API_TOKEN` and `DCS_BRIDGE_TOKEN`, and enable the `carrier` Compose profile:

```sh
docker compose -f compose.yaml -f compose.local-ai.yaml --profile jtac --profile carrier up -d --build
```

Omit `--profile jtac` and the `jtac` role when not using JTAC. The carrier
supervisor starts one independent SRS EAM client, receive/speech queue and
transmitter per enabled carrier. Speech workers remain shared. Carrier-only
native operation uses `BRAIN_ROLES=carrier` and needs no Tacview connection;
the Compose demo still includes its replay service.

### Mission radios and credentials

The mission is authoritative: the companion reads each declared carrier ship
unit's `frequency` in **Hz** and its `modulation` (native AM default). This
includes Supercarriers. Group/aircraft MHz settings are not used or converted.
There is no carrier frequency environment variable, override or allocator.
Missing/invalid radios disable that carrier with a diagnostic. Any radios
less than 200 Hz apart conflict, even across coalition/modulation; affected
carriers are disabled, never remapped. Enabled AWACS/ATC and JTAC radios are
reserved too. Change conflicting radios in the mission.

For multiple coalitions, configure the existing SRS server's red and blue EAM
passwords and supply the matching server-only `SRS_EAM_PASSWORDS` JSON map.
With no map, `SRS_EAM_PASSWORD` applies only to `COALITION`; another coalition
never borrows it. The bundled demo SRS configures blue EAM only. Do not put
passwords, API tokens or bridge tokens inside the mission.

### Install alongside or without JTAC

1. Install the shared `dcs/overlord_hook.lua` as described above. In the private
   `Config/overlord-hook.lua`, include both secrets and enable the features:

   ```lua
   return {
     host = "127.0.0.1",
     port = 8081,
     bridgeToken = "<same value as DCS_BRIDGE_TOKEN>",
     apiToken = "<same value as API_TOKEN>",
     features = {"jtac", "carriers"}, -- or {"carriers"} alone
     interval = 1,
     timeout = 3,
     state = "server",
   }
   ```

   Omitting `features` preserves the original JTAC-only behavior. Each feature
   has its own exchange sequence, timeout and error state; one cannot starve
   the other. Private numeric IPv4 and sandbox restrictions are unchanged.
2. Add MISSION START / DO SCRIPT FILE for `dcs/overlord_carrier.lua`. Optionally
   load an adapted `dcs/carrier-config.example.lua` first. Its keys are exact
   carrier **unit names** already in the mission. It cannot add carriers or
   change their frequency/modulation.
3. Supply `recoveryCase` and, as appropriate, `ceilingFt`, `visibilityNm` and
   `altimeterInHg`. CASE/weather are never guessed. Without a QNH override,
   the script uses live sea-level pressure; an unavailable value blocks
   dependent procedures. The mission must provide its real calendar date.
   An optional known `profile` supplies deck geometry for an unsupported
   ship type; see the example's allowed keys/profile names.
4. Use the human aircraft's unique DCS callsign or modex in radio calls.
   Set its SRS display name to the DCS player name or aircraft callsign;
   blank/generic/contradictory SRS names cannot control carrier sessions.
   This correlates identities, not cryptographic player authentication:
   coalition radio users and holders of the shared API token must be trusted.
5. Tune the selected carrier's mission frequency. Check in separately on that
   carrier. Switching carriers releases the previous session and invalidates
   its pending calls; no queue or lane is shared between ships.

### Calls and aircraft lifecycle

Start with **“Marshal, Viper one one, checking in”**. Repeat the assigned
labelled readback values exactly: CASE I angels/altimeter; CASE II/III
radial/DME/angels/expected approach time/altimeter. Partial, negated, reordered
or inconsistent values do not advance the procedure. Fly the assigned hold
before reporting **“established”**, wait for Charlie/commence, then report
**“commencing”**.

- CASE I: `initial` → `break` → `abeam` → `ball`.
- CASE II: `platform`, then the visual CASE I pattern.
- CASE III: `platform` → `ten miles` → `ball`.
- `clara`, `waveoff`, `bolter`, `emergency`, `say status`, `say again`,
  `checking out` and `cancel` follow fixed grammar. Position/state gates still
  apply: a radio assertion alone does not prove a trap or takeoff.
- Departures start with **“Tower, Viper one one, request startup”** or
  **“ready for launch”**. The aircraft must be observed on that carrier's
  deck; launch approval requires its lane free. Observed takeoff advances
  departure; distance or `departure complete` ends the session.

Only checked-in humans receive movement subscriptions. Before check-in the
companion reads minimal human identities, not positions; it never samples AI.
Touchdown is not a trap until the aircraft is stopped relative to its own
ship. A touch-and-go/bolter invalidates the old ball call. After recovery,
report `deck clear` to release the lane. Landing elsewhere, death, ejection,
disconnect, respawn, incarnation changes and missing watched state end or
invalidate the session. Unstarted grants are revoked when the bridge goes
stale. Mission epoch/radio changes invalidate old speech, including speech
already being synthesized.

The policy is deliberately conservative: one approach/departure lane per
carrier, explicit deck-clear reports, no AI deck-occupancy model, no automatic
LSO grading or autopilot. Runtime-spawned carriers absent from `env.mission`
are not discovered. Starting the companion after an aircraft has already
landed away from its mission spawn can lack reliable deck history; install
it at mission start.

### Diagnostics and acceptance

Authenticated `POST /carrier/registry` on the brain and `POST /status` on the
supervisor accept `{}` and show per-carrier radios/errors/connections without
secrets. Check these even when health is green: a disabled carrier is reported
individually. Carrier readiness uses its fresh registry, independently of
Tacview; mixed-role brain `/readyz` retains AWACS/JTAC telemetry semantics.

Run `python3 -m unittest tests.test_carrier tests.test_carrier_integration
tests.test_carrier_gateways tests.test_gateway` and
`luajit tests/carrier_harness.lua && luajit tests/jtac_harness.lua`.
The Lua harnesses use offline API doubles. On the actual DCS build, verify
two moving carriers with different native radios, concurrent human check-ins,
CASE readback/hold gates, bolter/trap/launch transitions, slot changes,
disconnects, stale-link cancellation, mission restart, and silence for AI
and aircraft that never called. Offline checks do not establish in-game
DCS acceptance or human speech-recognition accuracy.

## Independent airfield ATC

Build the current runtime source. Add `airfield` to `BRAIN_ROLES`, set both
`API_TOKEN` and `DCS_BRIDGE_TOKEN`, and enable the Compose `airfield` profile.
The `airfield-gateways` command uses port 8085 by default and shares the
existing STT, TTS and brain services. It does not need Tacview. If `atc` is
also listed, native airfield control replaces the old shared-radio ATC role.
AWACS, JTAC and carrier control remain independently selectable.

### Native radio and mission installation

Use the updated `dcs/overlord_hook.lua` as the single shared server hook;
do not install separate copies for each controller family. In its private
server-only configuration, enable the required features and, where necessary,
give the exact installed terrain radio-data path:

```lua
features = { "jtac", "carriers", "airfields" },
airfieldRadioFiles = {
  Caucasus = [[C:/Program Files/Eagle Dynamics/DCS World Server/Mods/terrains/Caucasus/Radio.lua]],
},
```

Keep the existing host, port, API token and bridge-token settings described
above. `airfieldRadioFiles` keys are native theatre names. Without an explicit
entry the hook tries `Mods/terrains/<theatre>/Radio.lua` under the DCS working
directory; configure a path when the installed folder name differs. The hook
reads the native file privately in a bounded environment and sends only
validated radio data into the mission. Do not copy `Radio.lua`, downloaded
reference documents, passwords or tokens into the repository or `.miz`.
No mission desanitization or MOOSE installation is required.

For server-managed installation, keep the hook in `Scripts/Hooks/` and place
the three mission companions under `Scripts/Overlord/`. Set `autoLoad = true`
in the private hook configuration. The hook waits one
exchange for mission start triggers, then loads only a missing fixed
companion, once per feature per mission epoch. An existing mission-defined
module is never replaced. With this option, no companion trigger or `.miz`
rewrite is required; mission-specific JTAC tasks and optional airfield facts
still come from the mission's own configuration.

The `argocd-definitions` charts install these assets from the pinned runtime
image using a memory-backed init volume and read-only file overlays. The
private config uses `dcs-server-secret/OVERLORD_API_TOKEN` and the separate
`overlordbot-bridge/DCS_BRIDGE_TOKEN` Secret. Keep that bridge credential out
of Git; do not add it by patching a Vault-managed destination Secret.
The nightly server backup excludes the mounted credential/config assets.

When upgrading the original brain Deployment to `Recreate`, Kubernetes
server-side apply can retain its old default `rollingUpdate` field. Remove
that default once before Argo sync; this changes rollout policy, not the
running pod template:

```sh
kubectl patch deployment overlordbot-brain -n dcs --type=merge \
  -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'
```

The chart retains `Recreate` for subsequent releases so authoritative
controllers never overlap. DCS itself restarts to install the hook overlays;
the installation PVC is preserved.

For manual installation with `autoLoad` disabled, add a MISSION START **DO SCRIPT FILE** trigger loading
`dcs/overlord_airfield.lua`. Optionally load an adapted
`dcs/airfield-config.example.lua` immediately before it. That optional mission
configuration contains operating facts only:

```lua
OverlordAirfieldConfig = {
  leaseSeconds = 15,
  airfields = {
    ["Exact native airfield name"] = {
      pattern = "left",
      -- Set only when verified against this field's actual native geometry:
      -- runwayNames = { ["native:1"] = "09L", ["native:2"] = "09R" },
      -- closedRunways = { "09L" },
      -- activeRunway = "09R",
    },
  },
}
```

Runway-name corrections use a native label or `native:<getRunways index>`.
Index corrections take precedence and allow ambiguous parallel strips to be
identified without inventing geometry. Invalid/unlabelled strips remain
occupancy obstacles but cannot be selected for a clearance. Do not copy
example labels into a real mission without checking them. Configuration
cannot add frequencies, fields or runway geometry.

Only native airdromes are airfield controllers; carriers use their separate
family and FARPs are not treated as airports. Wind, altimeter, ownership,
geometry and human lifecycle come from live DCS data. Missing facts produce
an explicit unavailable/negative response, not an LLM substitute.

### Radio and aircraft lifecycle

Each airfield owns its own sessions and conservative runway movement lane.
Native ground/tower/approach frequencies and modulation drive the registry,
not a static environment frequency list. Every distinct coalition/channel
gets its own SRS client and speech queue. Fields publishing the same channel
share one serialized transport; their controller state stays separate and
first contacts must name the field. Tower authority requires a published
tower role on the current channel; real handoffs use published frequencies.
Reserved AWACS/JTAC and current carrier channels disable conflicting airfield
radios instead of creating competing transmitters. Keep SRS coalition
security enabled and configure per-coalition EAM passwords server-side.

Start with, for example, “Batumi ground, Viper one one, request startup.”
Supported requests include taxi, holding short, crossing, line-up, takeoff,
inbound, pattern/final, landing, go-around, vacated, taxi-in and parking.
Use the explicit word **readback**, the clearance terms and the assigned
runway; arrival briefing readbacks also require the received altimeter.
Unanswered clearances expire, and a fresh pilot request is required before
they can be reissued. Status queries and late readbacks do not renew them.
Emergency traffic has priority only when physically eligible.

Only checked-in humans are position-polled. Registration, incarnation,
field/channel binding, measured runway movement, disconnect/death/ejection,
capture, bridge staleness and mission epoch fence old speech and authority.
Read-only wind/altimeter/runway queries do not enrol or transfer aircraft;
their replies are revalidated against fresh facts immediately before audio.
An accepted carrier/airfield control transfer revokes the previous family's
authority. Parking/check-out releases the session; do not check out while
still obstructing a runway.

AI and aircraft that never called in are deliberately excluded. Consequently
the controller cannot guarantee separation from all mission traffic.
Taxi is at pilot discretion with runway hold-short protection when no named
route is available. Approach support is visual/pattern procedural control,
not fabricated IFR vectors, approach plates or terrain clearance. Built-in
DCS ATC and human controllers are not automatically silenced; coordinate
which controller owns each frequency.

### Diagnostics and acceptance

Use authenticated `POST /airfield/registry` on the brain and `POST /status`
on the airfield supervisor, each with `{}`. They show channel and field
availability without exposing credentials. Supervisor readiness follows
its native registry; a mixed AWACS/JTAC brain can remain unready for missing
Tacview while native airfield radio is operational.

Offline checks:

```sh
python3 -m unittest tests.test_airfield tests.test_airfield_integration tests.test_airfield_gateways
luajit tests/airfield_harness.lua
```

Before mission use, verify native radio discovery on the actual DCS/theatre
version, two separate fields and shared-frequency fields, correct runway
labels/weather, strict readbacks, crossing/landing/takeoff occupancy, taxi-in,
handoffs, capture, disconnect, restart, stale-link cancellation and silence
for AI/unregistered aircraft. Offline API doubles and local SRS transport
checks do not establish in-game DCS acceptance or human STT accuracy.

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

No PVC, database or shared worker storage is needed. Models are baked read-only; worker processes need writable temporary space. Each gateway buffers at most 32 active speakers, 15 seconds per segment, and four queued transmissions. Domain/game state is ephemeral and rebuilt from Tacview or the mission companion. Restart loses transient state and carrier pilots must check in again. Demo artifacts and optional benchmark model downloads are local verification files, not required deployment volumes.

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
4. No DCS-gRPC installation is required. Live JTAC/carrier control requires the bundled mission script and server hook; ACMI alone does not provide mission task state, carrier procedures or execute marking/laser actions.
5. Place speech workers on leafman/stickman or desktop with direct LAN HTTP connectivity, not on sunman's DCS cores. Do not expose the whisper-native loopback port; expose only the stateless worker API.

## Health and metrics

Every service exposes `GET /healthz` for process liveness, `GET /readyz` for dependencies/model/state, and `GET /metrics` in Prometheus text format.

- Gateway ready: connected to SRS and at least one ready endpoint for STT, TTS and brain. It reconnects after DNS/connection failure.
- Brain ready: enabled AWACS/ATC/JTAC roles retain fresh positioned Tacview requirements; carrier-only mode requires a fresh mission bridge. Carrier gateways check their own enabled registry entry independently. Disconnect/reconnect clears world state; stale telemetry is refused.
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
- Whisper confidence is a model token probability summary when present, not calibrated command correctness. Optional local NLP classifies only verified, explicitly addressed requests; it is not a substitute for acoustic accuracy or deterministic readback checks. No cloud fallback exists.
- Tacview positions/metadata cannot prove DCS runway occupancy, clearance authority or arbitrary mission-task assignment. JTAC acts only on explicitly mission-authored tasks and live companion data; the bridge is not an unrestricted mission-command API.
- Companion Lua/API harness checks do not establish actual in-game DCS acceptance. Validate mission installation, hook permissions, line-of-sight/marking behavior, abort cleanup, and every configured SRS frequency on the target DCS build. Carrier procedures are simplified; full LSO automation is not included.
- Native SRS/Tacview traffic is plaintext. Keep it on trusted network/VPN. Demo SRS attempts upstream checks/telemetry; its internal network blocks them. Official SRS shutdown may emit `ObjectDisposedException` after successful exchanges.
- apt packages follow the pinned base image's distribution repositories; Python packages, models, speech source, image base and GitHub actions are pinned explicitly.
