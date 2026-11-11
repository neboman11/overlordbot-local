#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME=${CONTAINER_RUNTIME:-podman}
BINARY="$ROOT/.cache/SRS-Server-Commandline-Linux-2.4.1.0"
printf '%s  %s\n' 46268dd35204d04426974f66d25eaddaebbaa6b133a7bd227675fae30ed88127 "$BINARY" | sha256sum -c -
"$RUNTIME" run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=256m \
 -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 \
 -v "$BINARY:/srs-server:ro" -v "$ROOT:/app:ro" -v "$ROOT/spike/evidence:/evidence:rw" \
 -v /usr/lib/libopus.so.0:/usr/lib/x86_64-linux-gnu/libopus.so.0:ro \
 docker.io/library/python@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f \
 python /app/spike/audio_roundtrip.py
