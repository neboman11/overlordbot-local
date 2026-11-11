"""Keep radio reception independent from sequential speech/reply processing."""
import json
import os
import queue
import threading
import time
from .http import METRICS, Remote
from .srs import Opus, SRSClient


class Gateway:
    def __init__(self):
        self.stt = Remote(os.environ.get('STT_URLS', 'http://stt-worker:8082'))
        self.tts = Remote(os.environ.get('TTS_URLS', 'http://tts-worker:8083'))
        self.brain = Remote(os.environ.get('BRAIN_URL', 'http://brain:8081'))
        self.host = os.environ.get('SRS_HOST', 'srs')
        self.port = int(os.environ.get('SRS_PORT', '5002'))
        self.password = os.environ.get('SRS_EAM_PASSWORD', '')
        self.coalition = int(os.environ.get('COALITION', '2'))
        self.frequency = float(os.environ.get('SRS_FREQUENCY_HZ', '251000000'))
        self.modulation = int(os.environ.get('SRS_MODULATION', '0'))
        self.name = os.environ.get('BOT_CALLSIGN', 'Overlord')
        self.eot = float(os.environ.get('EOT_SECONDS', '.2'))
        if not .08 <= self.eot <= 2:
            raise ValueError('EOT_SECONDS must be between .08 and 2')
        self.prompt = os.environ.get('STT_PROMPT', 'Overlord, Viper one one, bogey dope, request picture, declare, inbound, ready to taxi, airfield, BRAA.')
        self.channel = f'{self.coalition}:{int(self.frequency)}:{self.modulation}'
        self.vocabulary = ''
        self.jobs = queue.Queue(maxsize=4)
        self.client = None
        self.stop = threading.Event()
        self.transmitting = threading.Event()

    def ready(self):
        return bool(self.client and self.client.connected and self.stt.ready() and self.tts.ready() and self.brain.ready())

    def start(self):
        threading.Thread(target=self._receive, daemon=True).start()
        threading.Thread(target=self._process, daemon=True).start()
        threading.Thread(target=self._events, daemon=True).start()

    def _enqueue(self, client, speaker, state):
        try:
            self.jobs.put_nowait((client, speaker, state))
            METRICS.add('transmissions_total')
        except queue.Full:
            METRICS.add('transmissions_dropped_total')

    def _receive(self):
        while not self.stop.is_set():
            client = None
            streams = {}
            try:
                client = SRSClient(self.host, self.port, self.password, self.coalition, self.frequency, self.modulation, self.name)
                client.connect()
                self.client = client
                print(json.dumps({'event': 'srs_connected'}), flush=True)
                while not self.stop.is_set():
                    try:
                        packet = client.receive()
                    except (ValueError, UnicodeError):
                        METRICS.add('invalid_packets_total')
                        packet = None
                    now = time.monotonic()
                    if packet and client.clients.get(packet.original, {}).get('Coalition') != self.coalition:
                        METRICS.add('coalition_packets_rejected_total')
                        packet = None
                    if packet and not self.transmitting.is_set():
                        speaker = packet.original
                        if speaker not in streams and len(streams) < 32:
                            streams[speaker] = {'first': now, 'last': now, 'packets': {}}
                        if speaker in streams:
                            state = streams[speaker]
                            state['last'] = now
                            state['packets'][packet.number] = packet.audio
                        else:
                            METRICS.add('transmissions_dropped_total')
                    for speaker, state in list(streams.items()):
                        if now - state['last'] > self.eot or now - state['first'] > 15 or len(state['packets']) >= 375:
                            del streams[speaker]
                            self._enqueue(client, speaker, state)
            except (OSError, ConnectionError, PermissionError, ValueError) as error:
                METRICS.add('srs_disconnects_total')
                print(json.dumps({'event': 'srs_disconnected', 'type': type(error).__name__}), flush=True)
            finally:
                if client:
                    client.close()
                self.client = None
            self.stop.wait(2)

    def _events(self):
        while not self.stop.wait(1):
            client = self.client
            if not client or not client.connected:
                continue
            try:
                body = json.dumps({'coalition': self.coalition, 'channel': self.channel}).encode()
                requested = time.monotonic()
                result = json.loads(self.brain.post('/events', body))
                vocabulary = ', '.join(str(word) for word in result.get('vocabulary', []))
                self.vocabulary = vocabulary.replace('\r', ' ').replace('\n', ' ')[:1200]
                for event in result.get('events', []):
                    state = {'reply': event['reply'], 'notification': event,
                             'expires': requested + max(0, event['ttl_seconds'])}
                    try:
                        self.jobs.put_nowait((client, None, state))
                    except queue.Full:
                        METRICS.add('notifications_dropped_total')
            except (OSError, ValueError, ConnectionError):
                METRICS.add('notification_poll_errors_total')

    def _process(self):
        while not self.stop.is_set():
            client, speaker, state = self.jobs.get()
            codec = None
            try:
                if client is not self.client or not client.connected:
                    METRICS.add('transmissions_dropped_total')
                    continue
                text = None
                if 'reply' in state:
                    if time.monotonic() > state['expires']:
                        continue
                    answer = {'reply': state['reply']}
                else:
                    codec = Opus()
                    pcm = b''.join(codec.decode(audio) for _, audio in sorted(state['packets'].items()))
                    if len(pcm) < 3200:
                        continue
                    started = time.monotonic()
                    prompt = (self.prompt + ' ' + self.vocabulary)[:2048]
                    transcript = json.loads(self.stt.post('/transcribe', pcm, 'audio/pcm', {'X-Prompt': prompt}))
                    METRICS.observe('stt', time.monotonic() - started)
                    text = transcript.get('text', '').strip()
                    if not text:
                        continue
                    request = {'text': text, 'coalition': self.coalition, 'channel': self.channel,
                               'sender_callsign': client.clients.get(speaker, {}).get('Name', '')}
                    answer = json.loads(self.brain.post('/respond', json.dumps(request).encode()))
                    if not answer.get('reply'):
                        METRICS.add('parse_failures_total')
                        continue
                started = time.monotonic()
                pcm = self.tts.post('/synthesize', json.dumps({'text': answer['reply']}).encode())
                METRICS.observe('tts', time.monotonic() - started)
                if not pcm or len(pcm) % 2:
                    raise ValueError('TTS returned invalid PCM')
                if text is None:
                    if time.monotonic() >= state['expires']:
                        continue
                    valid = json.loads(self.brain.post('/validate-event', json.dumps(state['notification']).encode()))
                    if not valid.get('valid') or time.monotonic() >= state['expires']:
                        continue
                self.transmitting.set()
                event = {'event': 'reply' if text else 'notification', 'heard': text, 'reply': answer['reply']}
                if text:
                    latency = time.monotonic() - state['last']
                    METRICS.observe('reply_start', latency)
                    event['end_to_reply_seconds'] = round(latency, 4)
                print(json.dumps(event), flush=True)
                client.transmit(pcm)
            except Exception as error:
                METRICS.add('pipeline_errors_total')
                print(json.dumps({'event': 'pipeline_error', 'type': type(error).__name__, 'message': str(error)}), flush=True)
            finally:
                self.transmitting.clear()
                if codec:
                    codec.close()
                self.jobs.task_done()
