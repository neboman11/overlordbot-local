#!/bin/sh
# Investigation only. No cluster access; SRS container has no external network.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME=${CONTAINER_RUNTIME:-podman}
CACHE="$ROOT/.cache"
BINARY="$CACHE/SRS-Server-Commandline-Linux-2.4.1.0"
SHA=46268dd35204d04426974f66d25eaddaebbaa6b133a7bd227675fae30ed88127
mkdir -p "$CACHE" "$ROOT/spike/evidence"
if [ ! -f "$BINARY" ]; then
    curl -fL --retry 2 -o "$BINARY" https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/download/2.4.1.0/SRS-Server-Commandline-Linux
fi
printf '%s  %s\n' "$SHA" "$BINARY" | sha256sum -c -
chmod +x "$BINARY"
"$RUNTIME" run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=256m \
    -v "$BINARY:/srs-server:ro" \
    -v "$ROOT/spike/probe.py:/probe.py:ro" \
    -v "$ROOT/spike/evidence:/evidence:rw" \
    docker.io/library/python@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f \
    python /probe.py
