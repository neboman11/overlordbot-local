#!/bin/sh
# Standalone SSH + user namespaces only. No Kubernetes/container daemon commands on target.
# Requires verified SSH host key, unshare, chroot, tar and Python already present.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
HOST=${1:-leafman}
RUNTIME=${CONTAINER_RUNTIME:-podman}
MODELS=${2:-tiny,base,small}
case "$MODELS" in ''|*[!a-z,]*) printf 'invalid model list\\n' >&2; exit 2 ;; esac
SSH='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10'
REMOTE=$($SSH "$HOST" mktemp -d /tmp/overlordbot-benchmark.XXXXXXXX)
case "$REMOTE" in /tmp/overlordbot-benchmark.*) ;; *) exit 2 ;; esac
CONTAINER="overlord-benchmark-export-$$"
cleanup() {
    "$RUNTIME" rm "$CONTAINER" >/dev/null 2>&1 || true
    $SSH "$HOST" "rm -rf -- '$REMOTE'" || true
}
trap cleanup EXIT INT TERM
mkdir -p "$ROOT/.cache" "$ROOT/artifacts"
"$RUNTIME" create --name "$CONTAINER" localhost/overlordbot-local-speech:dev >/dev/null
"$RUNTIME" export --output "$ROOT/.cache/speech-rootfs.tar" "$CONTAINER"
scp -q "$ROOT/.cache/speech-rootfs.tar" "$HOST:$REMOTE/rootfs.tar"
$SSH "$HOST" "mkdir '$REMOTE/root' && tar -xf '$REMOTE/rootfs.tar' -C '$REMOTE/root' --no-same-owner --exclude='dev/*' && mkdir -p '$REMOTE/root/bench' '$REMOTE/root/proc' '$REMOTE/root/dev' && touch '$REMOTE/root/dev/null' '$REMOTE/root/dev/urandom' '$REMOTE/root/dev/random'"
# Reuse checksum-verified models, or fetch them locally before transfer.
python3 "$ROOT/scripts/fetch_models.py" --dest "$ROOT/.cache/benchmark" ggml-base.en.bin ggml-small.en.bin
scp -q "$ROOT/.cache/benchmark/ggml-base.en.bin" "$ROOT/.cache/benchmark/ggml-small.en.bin" "$HOST:$REMOTE/root/bench/"
# All mounts occur inside a new unprivileged user+mount+PID namespace.
$SSH "$HOST" "unshare --user --map-root-user --mount --pid --fork --mount-proc='$REMOTE/root/proc' sh -ec 'mount --bind /dev/null \"\$1/dev/null\"; mount --bind /dev/urandom \"\$1/dev/urandom\"; mount --bind /dev/random \"\$1/dev/random\"; exec /usr/sbin/chroot \"\$1\" /usr/bin/env PIPER_THREADS=4 /usr/local/bin/python /app/scripts/benchmark_speech.py --models-dir /bench --models $MODELS --runs 1 --threads 4 --output /bench/xeon.json' sh '$REMOTE/root'"
scp -q "$HOST:$REMOTE/root/bench/xeon.json" "$ROOT/artifacts/speech-xeon.json"
