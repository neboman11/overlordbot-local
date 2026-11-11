"""Native local speech engines.

NativeSTT supervises one persistent whisper.cpp ``whisper-server`` bound to loopback.
The model is loaded once by that process; each utterance is one HTTP request to it.

NativeTTS runs Piper (piper-tts, ONNX Runtime CPU provider) in-process with the voice
loaded once. Output is always 16000 Hz mono signed 16-bit little-endian PCM; voices with
another native rate are resampled with a Kaiser-windowed sinc polyphase filter.
"""
import collections
import ctypes
import http.client
import io
import json
import logging
import math
import os
import signal
import subprocess
import threading
import time
import uuid
import wave
import weakref

SAMPLE_RATE = 16000
_LOG = logging.getLogger(__name__)


def _env(name, default):
    value = os.environ.get(name, '')
    return value if value else default


def _die_with_parent():
    # Linux PR_SET_PDEATHSIG: the server must not outlive a crashed worker.
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def pcm_to_wav(pcm, rate=SAMPLE_RATE):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def _multipart(fields, file_bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                     + value.encode('utf-8') + b'\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="utterance.wav"\r\n'
                 'Content-Type: audio/wav\r\n\r\n'.encode() + file_bytes + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


class NativeSTT:
    """whisper.cpp server on 127.0.0.1; constructor returns only once the model is loaded.

    Environment (constructor arguments override):
      WHISPER_MODEL            /models/ggml-tiny.en.bin
      WHISPER_SERVER           /usr/local/bin/whisper-server
      WHISPER_THREADS          4
      WHISPER_PORT             18080 (loopback only)
      WHISPER_STARTUP_TIMEOUT  120 seconds to load the model
      WHISPER_REQUEST_TIMEOUT  60 seconds per utterance
    """

    def __init__(self, model=None, server=None, threads=None, port=None,
                 startup_timeout=None, request_timeout=None):
        self.model = model or _env('WHISPER_MODEL', '/models/ggml-tiny.en.bin')
        self.server = server or _env('WHISPER_SERVER', '/usr/local/bin/whisper-server')
        self.threads = int(threads or _env('WHISPER_THREADS', '4'))
        self.port = int(port or _env('WHISPER_PORT', '18080'))
        self.host = '127.0.0.1'
        startup_timeout = float(startup_timeout or _env('WHISPER_STARTUP_TIMEOUT', '120'))
        self.request_timeout = float(request_timeout or _env('WHISPER_REQUEST_TIMEOUT', '60'))
        if self.threads < 1 or not 0 < self.port < 65536:
            raise ValueError('WHISPER_THREADS must be >= 1 and WHISPER_PORT 1-65535')
        if not os.path.isfile(self.model):
            raise FileNotFoundError(f'whisper model not found: {self.model}')
        self._log = collections.deque(maxlen=400)
        self._system_info = None
        self._closed = False
        self.load_seconds = None
        command = [self.server, '--model', self.model, '--threads', str(self.threads),
                   '--host', self.host, '--port', str(self.port), '--language', 'en',
                   '--no-timestamps', '--no-language-probabilities', '--no-gpu']
        self.command = tuple(command)
        start = time.monotonic()
        self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, preexec_fn=_die_with_parent)
        self._finalizer = weakref.finalize(self, _stop_process, self._process)
        threading.Thread(target=self._drain, name='whisper-server-log', daemon=True).start()
        deadline = start + startup_timeout
        while not self._health():
            if self._process.poll() is not None:
                self.close()
                raise RuntimeError(f'whisper-server exited with {self._process.returncode}: {self.log_tail()}')
            if time.monotonic() > deadline:
                self.close()
                raise TimeoutError(f'whisper-server did not load {self.model} in {startup_timeout}s: {self.log_tail()}')
            time.sleep(0.05)
        self.load_seconds = time.monotonic() - start

    def _drain(self):
        for raw in self._process.stdout:
            line = raw.decode('utf-8', 'replace').rstrip()
            self._log.append(line)
            if 'system_info:' in line:
                self._system_info = line.split('system_info:', 1)[1].strip()
            _LOG.debug('whisper-server: %s', line)

    def log_tail(self, lines=40):
        return '\n'.join(list(self._log)[-lines:])

    @property
    def system_info(self):
        """whisper_print_system_info() of the running binary (compiled CPU features); set after the first request."""
        return self._system_info

    @property
    def pid(self):
        return self._process.pid

    def _health(self):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=2)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            body = response.read()
            return response.status == 200 and json.loads(body).get('status') == 'ok'
        except (OSError, ValueError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    @property
    def ready(self):
        """True while our own server process is alive and reports its model loaded."""
        return not self._closed and self._process.poll() is None and self._health()

    def transcribe(self, pcm: bytes, prompt: str = '') -> dict:
        """16000 Hz mono s16le PCM -> {text, confidence}.

        confidence is the geometric mean of decoded token probabilities, None when no
        tokens were decoded.
        """
        if self._closed:
            raise RuntimeError('NativeSTT is closed')
        if len(pcm) % 2:
            raise ValueError('PCM must be whole signed 16-bit samples')
        fields = {'response_format': 'verbose_json', 'temperature': '0.0'}
        if prompt:
            fields['prompt'] = prompt
        body, content_type = _multipart(fields, pcm_to_wav(pcm))
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.request_timeout)
        try:
            connection.request('POST', '/inference', body, {'Content-Type': content_type})
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
        if response.status != 200:
            raise RuntimeError(f'whisper-server HTTP {response.status}: {payload[:500]!r}')
        result = json.loads(payload)
        segments = result.get('segments', [])
        text = ' '.join(segment['text'].strip() for segment in segments if segment['text'].strip())
        logs = [math.log(max(float(word['probability']), 1e-12))
                for segment in segments for word in segment.get('words', [])]
        confidence = math.exp(sum(logs) / len(logs)) if logs else None
        return {'text': text, 'confidence': confidence}

    def close(self):
        self._closed = True
        self._finalizer()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def resample(samples, source_rate, target_rate=SAMPLE_RATE, zeros=16, beta=8.6, rolloff=0.95):
    """Band-limited rational resampling of a float numpy array (Kaiser-windowed sinc, polyphase)."""
    import numpy as np
    if source_rate == target_rate or len(samples) == 0:
        return np.asarray(samples, dtype=np.float32)
    divisor = math.gcd(source_rate, target_rate)
    up, down = target_rate // divisor, source_rate // divisor
    cutoff = min(1.0, target_rate / source_rate) * rolloff
    half = math.ceil(zeros / cutoff)
    offsets = np.arange(-half + 1, half + 1)
    distance = np.arange(up)[:, None] / up - offsets[None, :]
    window = np.i0(beta * np.sqrt(np.clip(1.0 - (distance / half) ** 2, 0.0, 1.0))) / np.i0(beta)
    taps = cutoff * np.sinc(cutoff * distance) * window
    taps /= taps.sum(axis=1, keepdims=True)
    count = -(-len(samples) * up // down)
    position = np.arange(count, dtype=np.int64) * down
    base, phase = position // up, position % up
    padded = np.concatenate([np.zeros(half, np.float64), np.asarray(samples, np.float64), np.zeros(half + 1, np.float64)])
    output = np.einsum('ij,ij->i', padded[base[:, None] + offsets[None, :] + half], taps[phase])
    return output.astype(np.float32)


class NativeTTS:
    """Piper voice loaded once on the ONNX Runtime CPU provider.

    Environment (constructor arguments override):
      PIPER_MODEL    /models/en_US-ljspeech-medium.onnx (22050 Hz native, resampled to 16000)
      PIPER_CONFIG   PIPER_MODEL + '.json'
      PIPER_VOICE    en_US-ljspeech-medium (the only voice name synthesize accepts)
      PIPER_THREADS  0 (ONNX Runtime default intra-op threads)
    """

    def __init__(self, model=None, config=None, voice=None, threads=None):
        import numpy as np
        import onnxruntime
        from piper import PiperVoice
        from piper.config import PiperConfig
        self._np = np
        self.model = model or _env('PIPER_MODEL', '/models/en_US-ljspeech-medium.onnx')
        self.config = config or _env('PIPER_CONFIG', self.model + '.json')
        self.voice = voice or _env('PIPER_VOICE', 'en_US-ljspeech-medium')
        self.threads = int(threads if threads is not None else _env('PIPER_THREADS', '0'))
        start = time.monotonic()
        with open(self.config, encoding='utf-8') as handle:
            piper_config = PiperConfig.from_dict(json.load(handle))
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = self.threads
        session = onnxruntime.InferenceSession(self.model, sess_options=options, providers=['CPUExecutionProvider'])
        self._voice = PiperVoice(session=session, config=piper_config)
        self.native_rate = piper_config.sample_rate
        self._lock = threading.Lock()
        self._closed = False
        if not self.synthesize('Ready.'):
            raise RuntimeError(f'Piper voice {self.model} produced no audio')
        self.load_seconds = time.monotonic() - start

    @property
    def ready(self):
        return not self._closed

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Text -> 16000 Hz mono s16le PCM."""
        if self._closed:
            raise RuntimeError('NativeTTS is closed')
        if voice not in (None, self.voice):
            raise ValueError(f'voice {voice!r} is not loaded; available: {self.voice}')
        np = self._np
        with self._lock:
            chunks = [chunk.audio_float_array for chunk in self._voice.synthesize(text)]
        if not chunks:
            return b''
        audio = resample(np.concatenate(chunks), self.native_rate, SAMPLE_RATE)
        return (np.clip(audio, -1.0, 1.0) * 32767.0).round().astype('<i2').tobytes()

    def close(self):
        self._closed = True
        self._voice = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
