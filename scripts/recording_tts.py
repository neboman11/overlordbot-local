"""Verification-only TTS worker: the real NativeTTS behind the real overlord.http.serve,
recording every synthesized PCM and its source text for the end-to-end verifier.

Mirrors the tts branch of overlord/__main__.py (auth, validation, readiness, metrics).
Each request atomically writes /artifacts/tts-<uuidhex>.pcm, then appends one JSONL row
{id, text, pcm_file, pcm_sha256, at} to /artifacts/tts-trace.jsonl before the HTTP reply.
Identifiers are random; no request headers or secrets are recorded.
"""
import hashlib
import json
import os
import threading
import time
import uuid

from overlord.http import METRICS, serve
from overlord.speech import NativeTTS

ARTIFACTS = os.environ.get('TTS_TRACE_DIR', '/artifacts')
TRACE = os.path.join(ARTIFACTS, 'tts-trace.jsonl')
_trace_lock = threading.Lock()


def record(text, audio):
    ident = uuid.uuid4().hex
    name = f'tts-{ident}.pcm'
    final = os.path.join(ARTIFACTS, name)
    partial = os.path.join(ARTIFACTS, f'.tts-{ident}.tmp')
    with open(partial, 'wb') as handle:
        handle.write(audio)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, final)
    row = json.dumps({'id': ident, 'text': text, 'pcm_file': name,
                      'pcm_sha256': hashlib.sha256(audio).hexdigest(),
                      'at': time.monotonic()}, ensure_ascii=False) + '\n'
    with _trace_lock:
        with open(TRACE, 'a', encoding='utf-8') as handle:
            handle.write(row)
            handle.flush()
            os.fsync(handle.fileno())


def main():
    host = os.environ.get('HTTP_HOST', '0.0.0.0')
    port = int(os.environ.get('HTTP_PORT', 8083))
    worker = NativeTTS()

    def synthesize(headers, body):
        request = json.loads(body)
        text = request['text']
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError('text must contain 1-1000 characters')
        start = time.monotonic()
        audio = worker.synthesize(text, request.get('voice'))
        METRICS.observe('tts', time.monotonic() - start)
        record(text, audio)
        return 200, 'audio/pcm', audio

    serve({'/synthesize': synthesize}, lambda: bool(worker.ready), port, host)


if __name__ == '__main__':
    main()
