"""Telemetry-backed requests and bounded, channel-scoped controller notifications."""
from collections import deque
import json
import math
import threading
import time
from . import domain
from . import parser


AWACS_COMMANDS = {
    'radioCheck': 'radio check', 'bogeyDope': 'bogey dope', 'picture': 'picture',
    'friendlyPicture': 'friendly picture', 'bullseyePicture': 'bullseye picture',
    'tanker': 'nearest tanker', 'vectorHome': 'vector home',
    'divert': 'nearest divert', 'sayAgain': 'say again',
}
REPEATABLE = frozenset(AWACS_COMMANDS) - {'sayAgain', 'radioCheck'}


def parse_wind(value):
    if not value.strip():
        return None
    decoded = json.loads(value)
    values = decoded.values() if isinstance(decoded, dict) else [decoded]
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool)
               and math.isfinite(item) and 0 <= item < 360 for item in values):
        raise ValueError('ATC_WIND_FROM_DEG must be degrees 0-359 or airfield-to-degrees JSON')
    return decoded


class Brain:
    def __init__(self, world, callsign='Overlord', wind=None, roles=('awacs', 'atc'), awacs_pilot=None,
                 *, nlp=None, jtac=None, bullseye=None, home_airfield=None, carriers=None, airfields=None):
        self.world, self.callsign, self.wind = world, callsign, wind
        self.roles = tuple(role for role in roles if role != 'atc' or 'airfield' not in roles)
        self.awacs_pilot = awacs_pilot
        self.warning, self.atc = domain.WarningMonitor(), domain.AtcMonitor()
        self.lock = threading.Lock()
        self.routes = {'awacs': {}, 'atc': {}}
        self.events = deque(maxlen=64)
        self.completed = {}
        self.stop = threading.Event()
        self.generation = world.generation
        self.failed = False
        self.nlp, self.jtac = nlp, jtac
        self.carriers = carriers
        self.airfields = airfields
        self.radio_lock = threading.RLock()
        self._mission_epoch = None
        self._retired_mission_epochs = deque(maxlen=256)
        self.bullseye, self.home_airfield = bullseye, home_airfield
        self.repeats = {}
        self._telemetry_available = bool(world.ready)

    def _native_service(self, role):
        if role not in ('carrier', 'airfield'):
            raise ValueError('unknown native controller')
        return self.carriers if role == 'carrier' else self.airfields

    def _native_current(self, service):
        return (service is not None and (self._mission_epoch is None
                or service.registry().get('epoch') == self._mission_epoch))

    def refresh_airfield_radios(self):
        """Existing carrier radios take priority; new ATC never silently retunes either service."""
        with self.radio_lock:
            if self.airfields is None:
                return
            channels = ()
            if self.carriers is not None:
                channels = tuple(entry['channel'] for entry in self.carriers.registry()['carriers']
                                 if entry['enabled'] and entry.get('channel'))
            self.airfields.set_reserved_channels(channels)

    def exchange_native(self, role, payload):
        """Commit one authenticated feature snapshot and fence older missions across native roles."""
        with self.radio_lock:
            service = self._native_service(role)
            if service is None or not isinstance(payload, dict):
                raise ValueError('native controller unavailable or invalid snapshot')
            epoch = payload.get('epoch')
            if isinstance(epoch, str) and epoch in self._retired_mission_epochs:
                return {'version': 1, 'accepted': False, 'epoch': epoch,
                        'seq': payload.get('seq'), 'watch': [], 'error': 'retired mission epoch'}
            response = service.exchange(payload)
            if response.get('accepted') and epoch != self._mission_epoch:
                if self._mission_epoch is not None:
                    self._retired_mission_epochs.append(self._mission_epoch)
                self._mission_epoch = epoch
            self.refresh_airfield_radios()
            return response

    def native_registry(self, role):
        with self.radio_lock:
            self.refresh_airfield_radios()
            service = self._native_service(role)
            if service is None:
                raise ValueError('native controller disabled')
            registry = service.registry()
            if not self._native_current(service):
                registry['ready'] = False
            return registry

    def native_ready(self):
        with self.radio_lock:
            services = [service for service in (self.carriers, self.airfields) if service is not None]
            return bool(services) and all(self._native_current(service) and service.ready for service in services)

    def _sync_generation(self, generation):
        if self.generation != generation:
            self.warning, self.atc = domain.WarningMonitor(), domain.AtcMonitor()
            self.routes = {'awacs': {}, 'atc': {}}
            self.events.clear()
            self.completed.clear()
            self.repeats.clear()
            if self.jtac is not None:
                self.jtac.reset_generation(generation)
            self.generation = generation
            self._telemetry_available = bool(self.world.ready)

    @property
    def ready(self):
        return self.world.ready and not self.failed

    def _monitors(self):
        return {'awacs': self.warning, 'atc': self.atc}

    @staticmethod
    def _channel(request):
        channel = request.get('channel')
        if channel is not None and (not isinstance(channel, str) or not 1 <= len(channel) <= 128
                                    or any(not (c.isalnum() or c in ':.-_') for c in channel)):
            raise ValueError('invalid radio channel identifier')
        if channel and ':' in channel:
            parts = channel.split(':')
            if len(parts) != 3 or not all(part.isdecimal() for part in parts):
                raise ValueError('expected coalition:frequency:modulation radio channel')
            coalition, frequency, modulation = map(int, parts)
            if coalition not in (1, 2) or not 1000000 <= frequency <= 2000000000 or modulation not in (0, 1):
                raise ValueError('invalid coalition/frequency/modulation')
            channel = f'{coalition}:{frequency}:{modulation}'
        return channel

    def is_carrier_request(self, request):
        return request.get('carrier_id') is not None

    def is_airfield_request(self, request):
        return request.get('airfield') is True

    def is_native_request(self, request):
        return self.is_carrier_request(request) or self.is_airfield_request(request)

    def respond(self, request):
        text = request['text']
        coalition = int(request.get('coalition', 2))
        channel = self._channel(request)
        if not isinstance(text, str) or len(text) > 2048 or coalition not in (1, 2):
            raise ValueError('invalid text or coalition')
        if channel and ':' in channel and channel.split(':', 1)[0] in ('1', '2'):
            if int(channel.split(':', 1)[0]) != coalition:
                raise ValueError('radio channel coalition mismatch')
        if self.is_carrier_request(request) and self.is_airfield_request(request):
            raise ValueError('choose one native controller family')
        if self.is_native_request(request):
            with self.radio_lock:
                self.refresh_airfield_radios()
                role = 'airfield' if self.is_airfield_request(request) else 'carrier'
                service = self._native_service(role)
                if not self._native_current(service):
                    return {'reply': None, 'parsed': None, 'notes': 'Native controller unavailable for this mission'}
                answer = service.respond(dict(request, text=text, coalition=coalition, channel=channel))
                transfer = answer.get('transfer_unit_id')
                validation = answer.get('validation') or {}
                if role == 'carrier' and validation.get('kind') == 'state' \
                        and (answer.get('parsed') or {}).get('intent') in ('checkIn', 'departure', 'emergency'):
                    transfer = validation.get('unit_id')
                if transfer is not None:
                    previous = self.carriers if role == 'airfield' else self.airfields
                    if previous is not None:
                        previous.release_unit(transfer)
                return answer
        is_jtac = self.jtac is not None and channel == self.jtac.channel
        controller_callsign = self.jtac.callsign if is_jtac else self.callsign
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            if is_jtac:
                parsed = self.jtac.parse(text, units, coalition)
            else:
                parsed = parser.parse(text, self.callsign, domain._known_groups(units))
            if not self.ready:
                if not (is_jtac and parsed and parsed.get('intent') in ('abort', 'laserOff', 'checkOut')):
                    return {'reply': None, 'parsed': parsed, 'notes': 'Telemetry unavailable or stale'}
            if parsed and (parsed.get('blocked') or parsed.get('intent') not in (None, 'None')):
                return self._answer(text, coalition, channel, generation, units, is_jtac)
            if self.nlp is None or not controller_callsign:
                return self._answer(text, coalition, channel, generation, units, is_jtac)
            from .nlp import addressed
            address = addressed(text, controller_callsign, units, coalition)
            if address is None:
                return {'reply': None, 'parsed': parsed, 'notes': 'No explicit controller and live caller'}
            prefix, _ = address
            identity = parser.parse(prefix + ' radio check', controller_callsign,
                                    domain._known_groups(units)).get('transmitter')
            try:
                player = domain.find_player(units, identity, coalition) if identity else None
            except domain.DuplicateCallsign:
                player = None
            if player is None:
                return {'reply': None, 'parsed': parsed, 'notes': 'Caller unavailable or ambiguous'}
            caller_id = player['id']
            commands = ({key: self.jtac.commands[key] for key in ('requestBrief', 'requestBda', 'status')}
                        if is_jtac else AWACS_COMMANDS)
        # Inference never holds either controller or telemetry lock.
        rewritten = self.nlp.rewrite(text, controller_callsign, units, coalition, commands)
        with self.lock, self.world.locked_snapshot() as (current_generation, current_units):
            self._sync_generation(current_generation)
            if current_generation != generation or not self.ready:
                return {'reply': None, 'parsed': None, 'notes': 'Mission changed during interpretation'}
            try:
                current_player = domain.find_player(current_units, identity, coalition)
            except domain.DuplicateCallsign:
                current_player = None
            if current_player is None or current_player['id'] != caller_id:
                return {'reply': None, 'parsed': None, 'notes': 'Caller changed during interpretation'}
            if rewritten is None:
                return {'reply': 'unable, say again using standard phraseology',
                        'parsed': parsed, 'notes': 'Local interpretation unavailable or rejected'}
            answer = self._answer(rewritten, coalition, channel, current_generation,
                                  current_units, is_jtac)
            answer['notes'] = 'Validated local intent fallback'
            return answer

    def _answer(self, text, coalition, channel, generation, units, is_jtac):
        if is_jtac:
            return self.jtac.respond(text, coalition, channel, units, generation)
        before = {role: monitor.revisions() for role, monitor in self._monitors().items()}
        parsed = parser.parse(text, self.callsign, domain._known_groups(units))
        transmitter = parsed.get('transmitter')
        try:
            player = domain.find_player(units, transmitter, coalition) if transmitter else None
        except domain.DuplicateCallsign:
            player = None
        key = (coalition, channel, player['id']) if player else None
        is_repeat = parsed.get('intent') == 'sayAgain'
        original_text = text
        if is_repeat and key:
            deadline, previous_text = self.repeats.get(key, (0, None))
            if deadline > time.monotonic():
                text = previous_text
        answer = domain.handle(text, coalition, units, self.callsign, monitor=self.warning,
                               atc_monitor=self.atc, wind=self.wind, roles=self.roles,
                               awacs_pilot=self.awacs_pilot, bullseye=self.bullseye,
                               home_airfield=self.home_airfield)
        if is_repeat:
            answer['parsed'] = parsed
        elif (key and answer.get('reply') and parsed.get('intent') in REPEATABLE
              and parsed.get('receiver', {}).get('kind') == 'awacs'):
            now = time.monotonic()
            self.repeats = {k: v for k, v in self.repeats.items() if v[0] > now}
            if len(self.repeats) >= 64:
                self.repeats.pop(next(iter(self.repeats)))
            self.repeats[key] = (now + 30, original_text)
        if channel and player:
            uid = player['id']
            for role, monitor in self._monitors().items():
                revision = monitor.revisions().get(uid)
                if revision != before[role].get(uid):
                    if role == 'atc':
                        self.completed.pop(uid, None)
                    if revision is None:
                        self.routes[role].pop(uid, None)
                    else:
                        self.routes[role][uid] = (coalition, channel)
                    self.events = deque((event for event in self.events
                                         if (event['role'], event['unit_id']) != (role, uid)), maxlen=64)
        return answer

    def poll(self):
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            if not self.ready:
                self.events.clear()
                if self.jtac is not None and self._telemetry_available:
                    self.jtac.reset()
                self._telemetry_available = False
                return
            self._telemetry_available = True
            live = {unit['id'] for unit in units}
            self.completed = {uid: entry for uid, entry in self.completed.items()
                              if uid in live and entry[1] > time.time()}
            for role, monitor in self._monitors().items():
                self.routes[role] = {uid: route for uid, route in self.routes[role].items() if uid in live}
                previous = monitor.revisions()
                notifications = (monitor.check(units, awacs_pilot=self.awacs_pilot)
                                 if role == 'awacs' else monitor.check(units))
                revisions = monitor.revisions()
                for notification in notifications:
                    uid = notification['unit_id']
                    if role == 'atc' and uid not in revisions and uid in previous:
                        token = str(previous[uid]) + ':complete'
                        if len(self.completed) >= 64:
                            self.completed.pop(next(iter(self.completed)))
                        self.completed[uid] = (token, time.time() + 10)
                        revisions[uid] = token
                    route = self.routes[role].get(uid)
                    if route and route[0] == notification['coalition'] and uid in revisions:
                        self.events.append(dict(notification, channel=route[1], role=role,
                                                generation=generation, revision=revisions[uid],
                                                expires=time.time() + 10))

    def _valid(self, event, units):
        role, uid = event.get('role'), event.get('unit_id')
        monitor = self._monitors().get(role)
        expected = monitor.revisions().get(uid) if monitor else None
        if expected is None and role == 'atc':
            token, deadline = self.completed.get(uid, (None, 0))
            expected = token if deadline > time.time() else None
        return bool(self.ready and monitor and event.get('generation') == self.generation
                    and event.get('expires', 0) > time.time()
                    and expected is not None and expected == event.get('revision')
                    and self.routes[role].get(uid) == (event.get('coalition'), event.get('channel'))
                    and any(unit['id'] == uid and unit['coalition'] == event.get('coalition') for unit in units))

    def validate_event(self, event):
        if not isinstance(event, dict):
            raise ValueError('expected event object')
        if event.get('role') in ('carrier', 'airfield'):
            with self.radio_lock:
                self.refresh_airfield_radios()
                service = self._native_service(event['role'])
                return {'valid': bool(self._native_current(service) and service.validate_reply(event))}
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            if event.get('role') == 'jtac':
                valid = ((self.ready or event.get('safety_cancel') is True) and self.jtac is not None
                         and event.get('generation') == generation
                         and event.get('channel') == self.jtac.channel
                         and any(unit['id'] == event.get('unit_id')
                                 and unit.get('coalition') == event.get('coalition') for unit in units)
                         and self.jtac.validate_reply(event))
                return {'valid': bool(valid)}
            return {'valid': self._valid(event, units)}

    def take_events(self, request):
        coalition = int(request['coalition'])
        channel = self._channel(request)
        if coalition not in (1, 2) or channel is None:
            raise ValueError('coalition and channel required')
        if self.is_carrier_request(request) and self.is_airfield_request(request):
            raise ValueError('choose one native controller family')
        if self.is_native_request(request):
            with self.radio_lock:
                self.refresh_airfield_radios()
                role = 'airfield' if self.is_airfield_request(request) else 'carrier'
                service = self._native_service(role)
                if not self._native_current(service):
                    return {'events': [], 'vocabulary': []}
                return service.take_events(dict(request, coalition=coalition, channel=channel))
        if self.jtac is not None and channel == self.jtac.channel:
            events = [dict(event['validation'], reply=event['reply'], ttl_seconds=10)
                      for event in self.jtac.take_events(coalition, channel)]
            return {'events': events, 'vocabulary': [self.jtac.callsign, 'check in', 'nine line',
                                                    'readback', 'contact', 'abort', 'laser on', 'laser off']}
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            out = []
            remaining = deque(maxlen=64)
            for event in self.events:
                if not self._valid(event, units):
                    continue
                if event['coalition'] == coalition and event['channel'] == channel:
                    out.append(dict(event, ttl_seconds=max(0, event['expires'] - time.time())))
                else:
                    remaining.append(event)
            self.events = remaining
            friendly = [unit for unit in units if unit['coalition'] == coalition
                        and (unit.get('callsign') or unit.get('pilot'))][:32]
            vocabulary = [self.callsign] + sorted({unit.get('callsign') or unit.get('pilot') for unit in friendly})
            nearby = [field for field in domain.AIRFIELDS
                      if any(domain.distance_m(field.lat, field.lon, unit['lat'], unit['lon']) <= 1000000
                             for unit in friendly)]
            vocabulary += [domain.pronounce_airbase(field.name) for field in (nearby or domain.AIRFIELDS)]
            return {'events': out, 'vocabulary': vocabulary}

    def start(self):
        def run():
            while not self.stop.wait(1):
                try:
                    self.poll()
                except Exception as error:
                    self.failed = True
                    print(json.dumps({'event': 'monitor_error', 'type': type(error).__name__}), flush=True)
                    return
        threading.Thread(target=run, name='controller-monitor', daemon=True).start()
