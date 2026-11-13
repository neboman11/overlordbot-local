"""Keep radio reception independent from sequential speech/reply processing."""
import json
import os
import queue
import threading
import time
from .http import METRICS, Remote
from .srs import Opus, SRSClient


class Gateway:
    """One SRS radio. Per-instance overrides never touch os.environ; unset ones use env defaults."""
    def __init__(self, *, name=None, coalition=None, frequency=None, modulation=None, password=None, carrier_id=None,
                 airfield=False):
        if airfield and carrier_id is not None:
            raise ValueError('airfield and carrier_id are mutually exclusive')
        self.stt = Remote(os.environ.get('STT_URLS', 'http://stt-worker:8082'))
        self.tts = Remote(os.environ.get('TTS_URLS', 'http://tts-worker:8083'))
        self.brain = Remote(os.environ.get('BRAIN_URL', 'http://brain:8081'))
        self.host = os.environ.get('SRS_HOST', 'srs')
        self.port = int(os.environ.get('SRS_PORT', '5002'))
        self.password = os.environ.get('SRS_EAM_PASSWORD', '') if password is None else password
        self.coalition = int(os.environ.get('COALITION', '2')) if coalition is None else int(coalition)
        self.frequency = float(os.environ.get('SRS_FREQUENCY_HZ', '251000000')) if frequency is None else float(frequency)
        self.modulation = int(os.environ.get('SRS_MODULATION', '0')) if modulation is None else int(modulation)
        self.name = os.environ.get('BOT_CALLSIGN', 'Overlord') if name is None else name
        self.carrier_id = carrier_id
        self.airfield = bool(airfield)
        self.eot = float(os.environ.get('EOT_SECONDS', '.2'))
        if not .08 <= self.eot <= 2:
            raise ValueError('EOT_SECONDS must be between .08 and 2')
        if self.airfield:
            default_prompt = ('Ground, Tower, Approach, request startup, request taxi, holding short, ready for departure, line up and wait, '
                              'cleared for takeoff, inbound, request landing, final, going around, runway vacated, wind check, '
                              'altimeter, say again, checking out, emergency.')
        elif carrier_id is None:
            default_prompt = 'Overlord, Viper one one, bogey dope, request picture, declare, inbound, ready to taxi, airfield, BRAA.'
        else:
            default_prompt = ('Marshal, Tower, Case one, Case two, Case three, checking in, inbound, request marshal, established, commencing, '
                              'platform, see you at ten, ball, bolter, waveoff, say again, ready for departure, deck clear, checking out.')
        self.prompt = os.environ.get('STT_PROMPT', default_prompt)
        self.channel = f'{self.coalition}:{int(self.frequency)}:{self.modulation}'
        self.vocabulary = ''
        self.jobs = queue.Queue(maxsize=4)
        self.client = None
        self.stop = threading.Event()
        self.transmitting = threading.Event()
        self._threads = []
        self._pending = None
        self._lifecycle = threading.Lock()

    def ready(self):
        if not (self.client and self.client.connected and self.stt.ready() and self.tts.ready()):
            return False
        if self.airfield:
            return self._registered('/airfield/registry', 'radios', lambda entry: True)
        if self.carrier_id is not None:
            return self._registered('/carrier/registry', 'carriers', lambda entry: entry.get('id') == self.carrier_id)
        return self.brain.ready()

    def _registered(self, path, listing, own):
        """Mission-registry radios depend on their own fresh enabled entry, not on AWACS/JTAC telemetry readiness."""
        try:
            registry = json.loads(self.brain.post(path, b'{}'))
        except (OSError, ValueError, ConnectionError):
            return False
        if not isinstance(registry, dict) or registry.get('ready') is not True or not isinstance(registry.get(listing), list):
            return False
        return any(isinstance(entry, dict) and own(entry) and entry.get('enabled') is True
                   and entry.get('channel') == self.channel and entry.get('coalition') == self.coalition
                   for entry in registry[listing])

    def _bound(self, item, *, notification=False):
        """True when a validation/notification is scoped to exactly this radio's role.

        Airfield radios carry many fields on one channel, so the field id is dynamic (checked by the brain);
        the radio itself only binds role, channel and coalition. Every other gateway rejects airfield metadata."""
        if self.airfield:
            return (item.get('role') == 'airfield' and item.get('channel') == self.channel
                    and item.get('coalition') == self.coalition and item.get('carrier_id') is None
                    and item.get('airfield', True) is True)
        if item.get('role') == 'airfield' or item.get('airfield') is not None or item.get('airfield_id') is not None:
            return False
        if item.get('carrier_id') != self.carrier_id:
            return False
        if notification:
            return item.get('channel', self.channel) == self.channel and item.get('coalition', self.coalition) == self.coalition
        return item.get('channel') == self.channel and item.get('coalition') == self.coalition

    def start(self):
        with self._lifecycle:
            if self.stop.is_set():
                raise RuntimeError('gateway is closed')
            if self._threads:
                return
            self._threads = [threading.Thread(target=target, name=f'gateway-{target.__name__.strip("_")}', daemon=True)
                             for target in (self._receive, self._process, self._events)]
            for thread in self._threads:
                thread.start()

    def close(self, timeout=5.0):
        """Stop and join every worker thread; idempotent. True when all threads ended."""
        self.stop.set()
        with self._lifecycle:
            clients = [self._pending, self.client]
            threads = list(self._threads)
        for client in clients:
            if client:
                client.close()
        while True:
            try:
                self.jobs.get_nowait()
                self.jobs.task_done()
            except queue.Empty:
                break
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass
        deadline = time.monotonic() + timeout
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        return not any(thread.is_alive() for thread in threads)

    def _may_transmit(self, client):
        return not self.stop.is_set() and client is self.client and bool(client.connected)

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
                with self._lifecycle:
                    if self.stop.is_set():
                        break
                    self._pending = client
                client.connect()
                if self.stop.is_set():
                    break
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
                self._pending = None
            self.stop.wait(2)

    def _events(self):
        while not self.stop.wait(1):
            client = self.client
            if not client or not client.connected:
                continue
            try:
                request = {'coalition': self.coalition, 'channel': self.channel}
                if self.carrier_id is not None:
                    request['carrier_id'] = self.carrier_id
                if self.airfield:
                    request['airfield'] = True
                requested = time.monotonic()
                result = json.loads(self.brain.post('/events', json.dumps(request).encode()))
                if self.stop.is_set():
                    return
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
            job = self.jobs.get()
            if job is None or self.stop.is_set():
                self.jobs.task_done()
                continue
            client, speaker, state = job
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
                    if self.carrier_id is not None:
                        request['carrier_id'] = self.carrier_id
                    if self.airfield:
                        request['airfield'] = True
                    answer = json.loads(self.brain.post('/respond', json.dumps(request).encode()))
                    if not answer.get('reply'):
                        METRICS.add('parse_failures_total')
                        continue
                started = time.monotonic()
                pcm = self.tts.post('/synthesize', json.dumps({'text': answer['reply']}).encode())
                METRICS.observe('tts', time.monotonic() - started)
                if not pcm or len(pcm) % 2:
                    raise ValueError('TTS returned invalid PCM')
                validation = answer.get('validation')
                if (self.carrier_id is not None or self.airfield) and validation is None and text is not None:
                    continue
                if validation is not None:
                    if not self._bound(validation):
                        continue
                    valid = json.loads(self.brain.post('/validate-event', json.dumps(validation).encode()))
                    if not valid.get('valid'):
                        continue
                if text is None:
                    if time.monotonic() >= state['expires']:
                        continue
                    notification = state['notification']
                    if not self._bound(notification, notification=True):
                        continue
                    valid = json.loads(self.brain.post('/validate-event', json.dumps(notification).encode()))
                    if not valid.get('valid') or time.monotonic() >= state['expires']:
                        continue
                if not self._may_transmit(client):
                    METRICS.add('transmissions_dropped_total')
                    continue
                self.transmitting.set()
                event = {'event': 'reply' if text else 'notification', 'heard': text, 'reply': answer['reply']}
                if text:
                    latency = time.monotonic() - state['last']
                    METRICS.observe('reply_start', latency)
                    event['end_to_reply_seconds'] = round(latency, 4)
                print(json.dumps(event), flush=True)
                if not self._may_transmit(client):
                    METRICS.add('transmissions_dropped_total')
                    continue
                client.transmit(pcm)
            except Exception as error:
                METRICS.add('pipeline_errors_total')
                print(json.dumps({'event': 'pipeline_error', 'type': type(error).__name__, 'message': str(error)}), flush=True)
            finally:
                self.transmitting.clear()
                if codec:
                    codec.close()
                self.jobs.task_done()
