# overlordbot-local

**Status: stopped at milestone 1, as requested when protocol incompatibility is found. This is an investigation, not a working bot.**

[PLAN.md](PLAN.md) remains the rewrite specification. No Kubernetes clusters or argocd-definitions files were accessed or modified.

## Measured incompatibility

Against the official SRS 2.4.1.0 Linux executable in a local, network-isolated container:

| EAM password JSON property | TCP SYNC | Returned coalition |
|---|---|---|
| Original `ExternalAwacsModePassword` | Accepted | 0: authentication rejected |
| Current `ExternalAWACSModePassword` | Accepted | 2: blue authentication accepted |

Both probes advertise the original protocol version `1.9.0.0`. The positive control changes only password-property spelling (each connection also has a distinct client GUID). The failure is **not** the advertised version. Evidence: [spike/evidence/protocol.json](spike/evidence/protocol.json).

Original emits `ExternalAwacsModePassword` without a JSON property override. SRS now declares `ExternalAWACSModePassword` and deserializes with System.Text.Json without enabling case-insensitive matching. The original key is therefore ignored. Original parser also uses the old spelling for the response property, but the authentication probe tests outbound compatibility only.

## Reproduce

Requirements: Linux amd64, Podman (or Docker), curl, sha256sum; initial downloads require internet. No speech/cloud API is used. After downloads, the SRS process and probes run with `--network none`; this also prevents upstream SRS update checks and Sentry telemetry from reaching the internet.

```sh
sh spike/run.sh
# Alternatively:
CONTAINER_RUNTIME=docker sh spike/run.sh
```

Exit 0 means the **incompatibility was reproduced**, not that milestone 1 passed. The probe checks real newline-delimited JSON over TCP loopback inside the server container, asserts both results, and saves JSON evidence. A changed outcome or transport failure exits nonzero. It uses only a disposable test password, not a real credential. `CONTAINER_RUNTIME` defaults to `podman`; this is the only launcher configuration variable. SRS listens on TCP/UDP 5002 inside its isolated container, with no published ports.

Pinned SRS binary:

- URL: https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/download/2.4.1.0/SRS-Server-Commandline-Linux
- SHA256: `46268dd35204d04426974f66d25eaddaebbaa6b133a7bd227675fae30ed88127`
- Python container: `docker.io/library/python@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f`

The script verifies the binary checksum on every run. Downloads are cached in ignored `.cache/`. Python standard library was chosen for this disposable protocol probe: no dependencies, direct TCP/JSON, easy-to-inspect evidence. The production runtime has not been selected at this gate.

## Source references and licensing

Original reference cloned at `../overlordbot-reference`, revision `c7d34d51bfe66faf6bb8ff9e22688351e99cee70`:

- [NetworkMessage.cs](https://gitlab.com/overlordbot/srs-bot/-/blob/c7d34d51bfe66faf6bb8ff9e22688351e99cee70/OverlordBot.SimpleRadio/Network/Util/NetworkMessage.cs): lines 46, 50–54, old password key and serialization.
- [DataClient.cs](https://gitlab.com/overlordbot/srs-bot/-/blob/c7d34d51bfe66faf6bb8ff9e22688351e99cee70/OverlordBot.SimpleRadio/Network/DataClient.cs): lines 84–101, SYNC and EAM requests.
- Original copyright: Jeffrey Jones, 2020–2022; AGPL-3.0-or-later. Domain code, parser cases, and encyclopedia have not yet been copied or ported.

SRS reference cloned at `../srs-2.4.1.0-reference`, release tag `2.4.1.0`, revision `f7bdcd42d41eff1e912cd0465b8a22c78108c53c`:

- [NetworkMessage.cs](https://github.com/ciribob/DCS-SimpleRadioStandalone/blob/f7bdcd42d41eff1e912cd0465b8a22c78108c53c/Common/Models/NetworkMessage.cs): line 47, current password key.
- [SRSClientSession.cs](https://github.com/ciribob/DCS-SimpleRadioStandalone/blob/f7bdcd42d41eff1e912cd0465b8a22c78108c53c/Common/Network/Server/SRSClientSession.cs): lines 113–116, deserializer options.
- [ServerSync.cs](https://github.com/ciribob/DCS-SimpleRadioStandalone/blob/f7bdcd42d41eff1e912cd0465b8a22c78108c53c/Common/Network/Server/ServerSync.cs): lines 551–583, authentication response.

The probe is newly written; upstream binaries/sources are not committed or redistributed here. This repository's code is licensed AGPL-3.0-or-later; see [LICENSE](LICENSE).

## Gate and known limits

- UDP Opus reception, frequency routing, WAV recording/playback: **not attempted after EAM incompatibility was established**.
- Tacview, STT/TTS, parser/responders, speech corpus, latency/resource measurements, CI images, and deployment: **not implemented or measured**.
- No AVX-only Xeon measurements were performed; no latency claim is made.
- No images were published; no finished `DEPLOY.md` is supplied. The handoff contract applies when the rewrite is ready, which it is not.
- Official Linux server required a writable .NET bundle extraction directory and an explicit `--clientExportPath /tmp/clients.json` in this read-only container. The probe sets both. Its shutdown emitted an upstream `ObjectDisposedException` after completing authentication; this is not audio validation.

Next engineering step, after lifting the requested stop gate: use the exact SRS 2.4.1.0 EAM property spelling, then finish milestone 1 with two radio clients, real Opus traffic, WAV capture, and playback before any worker/domain implementation.
