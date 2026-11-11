"""Telemetry-backed requests and bounded, channel-scoped controller notifications."""
from collections import deque
import json
import math
import threading
import time
from . import domain


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
    def __init__(self, world, callsign='Overlord', wind=None, roles=('awacs', 'atc'), awacs_pilot=None):
        self.world, self.callsign, self.wind = world, callsign, wind
        self.roles, self.awacs_pilot = roles, awacs_pilot
        self.warning, self.atc = domain.WarningMonitor(), domain.AtcMonitor()
        self.lock = threading.Lock()
        self.routes = {'awacs': {}, 'atc': {}}
        self.events = deque(maxlen=64)
        self.completed = {}
        self.stop = threading.Event()
        self.generation = world.generation
        self.failed = False

    def _sync_generation(self, generation):
        if self.generation != generation:
            self.warning, self.atc = domain.WarningMonitor(), domain.AtcMonitor()
            self.routes = {'awacs': {}, 'atc': {}}
            self.events.clear()
            self.completed.clear()
            self.generation = generation

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
        return channel

    def respond(self, request):
        text = request['text']
        coalition = int(request.get('coalition', 2))
        channel = self._channel(request)
        if not isinstance(text, str) or len(text) > 2048 or coalition not in (1, 2):
            raise ValueError('invalid text or coalition')
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            before = {role: monitor.revisions() for role, monitor in self._monitors().items()}
            answer = domain.handle(text, coalition, units, self.callsign, monitor=self.warning,
                                   atc_monitor=self.atc, wind=self.wind, roles=self.roles,
                                   awacs_pilot=self.awacs_pilot)
            parsed = answer.get('parsed')
            transmitter = parsed.get('transmitter') if parsed else None
            if channel and transmitter:
                try:
                    player = domain.find_player(units, transmitter, coalition)
                except domain.DuplicateCallsign:
                    player = None
                if player:
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
                return
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
        with self.lock, self.world.locked_snapshot() as (generation, units):
            self._sync_generation(generation)
            return {'valid': self._valid(event, units)}

    def take_events(self, request):
        coalition = int(request['coalition'])
        channel = self._channel(request)
        if coalition not in (1, 2) or channel is None:
            raise ValueError('coalition and channel required')
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
