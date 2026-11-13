"""Run one SRS Gateway per enabled radio, driven by the brain's mission-derived registry.

role='carrier': one gateway per carrier from /carrier/registry (the mission's own ship unit radio settings).
role='airfield': one gateway per canonical native airfield channel from /airfield/registry; several fields
sharing a native channel are served by that single worker/transmitter, so speech never overlaps.

Frequencies, modulation and coalition come ONLY from the registry. This module never parses mission files
and never allocates frequencies.
"""
import json
import math
import os
import threading
import time
from .gateway import Gateway
from .http import METRICS, Remote


def _log(event, **fields):
    print(json.dumps({'event': event, **fields}), flush=True)


_ROLES = {
    'carrier': {'path': '/carrier/registry', 'listing': 'carriers', 'noun': 'carrier'},
    'airfield': {'path': '/airfield/registry', 'listing': 'radios', 'noun': 'airfield'},
}


class RadioGateways:
    def __init__(self, role='carrier', *, registry_url=None, poll_seconds=1, stale_seconds=15):
        if role not in _ROLES:
            raise ValueError("role must be 'carrier' or 'airfield'")
        if poll_seconds <= 0 or stale_seconds <= 0:
            raise ValueError('poll_seconds and stale_seconds must be positive')
        self.role = role
        self._path = _ROLES[role]['path']
        self._listing = _ROLES[role]['listing']
        self._noun = _ROLES[role]['noun']
        self.registry = Remote(registry_url or os.environ.get('BRAIN_URL', 'http://brain:8081'))
        self.poll_seconds = poll_seconds
        self.stale_seconds = stale_seconds
        self._passwords, self._credential_error = self._load_credentials()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._running = {}  # radio id -> (spec, Gateway)
        self._status = {}  # radio id -> public per-radio report (no secrets)
        self._epoch = None
        self._last_ok = None
        self._registry_error = None
        self._registry_ready = False

    @staticmethod
    def _load_credentials():
        """Server-only per-coalition secrets. SRS_EAM_PASSWORDS = {"1": red, "2": blue}; the single legacy
        SRS_EAM_PASSWORD applies only to the coalition named by COALITION."""
        passwords = {}
        error = None
        raw = os.environ.get('SRS_EAM_PASSWORDS', '').strip()
        if raw:
            try:
                mapping = json.loads(raw)
                if not isinstance(mapping, dict):
                    raise ValueError
                for side, secret in mapping.items():
                    if side in ('1', '2') and isinstance(secret, str) and secret:
                        passwords[int(side)] = secret
            except ValueError:
                error = 'SRS_EAM_PASSWORDS is not a JSON object'
        legacy = os.environ.get('SRS_EAM_PASSWORD', '')
        try:
            side = int(os.environ.get('COALITION', '2'))
        except ValueError:
            side = None
        if legacy and side in (1, 2):
            passwords.setdefault(side, legacy)
        return passwords, error

    def start(self):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError(f'{self._noun} gateways are closed')
            if self._thread:
                return
            self._thread = threading.Thread(target=self._poll, name=f'{self._noun}-gateways', daemon=True)
            self._thread.start()

    def close(self, timeout=10.0):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            idents = list(self._running)
        closed = self._close_gateways(idents)
        return closed and not (thread and thread.is_alive())

    def _poll(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as error:  # supervisor must outlive any single bad poll
                METRICS.add(f'{self._noun}_registry_errors_total')
                _log(f'{self._noun}_registry_error', type=type(error).__name__)
            self._stop.wait(self.poll_seconds)

    def refresh(self):
        """One registry poll plus reconciliation. Public so callers/tests can drive it deterministically."""
        try:
            registry = json.loads(self.registry.post(self._path, b'{}'))
            desired, rejected, epoch = self._parse(registry)
        except (OSError, ValueError, ConnectionError) as error:
            self._registry_failed(f'{type(error).__name__}: {error}'[:200])
            return
        self._reconcile(epoch, desired, rejected)

    def _parse(self, registry):
        listing = self._listing
        airfield = self.role == 'airfield'
        if not isinstance(registry, dict) or not isinstance(registry.get(listing), list):
            raise ValueError('registry response malformed')
        if registry.get('ready') is not True:
            raise ValueError('registry not ready')
        epoch = registry.get('epoch')
        if not isinstance(epoch, str) or not epoch:
            raise ValueError('registry epoch missing')
        counts = {}
        for entry in registry[listing]:
            if isinstance(entry, dict) and isinstance(entry.get('id'), str):
                counts[entry['id']] = counts.get(entry['id'], 0) + 1
        desired, rejected = {}, {}
        for entry in registry[listing]:
            if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
                continue
            ident = entry['id']
            name = entry.get('name') if isinstance(entry.get('name'), str) else ident
            report = {'id': ident, 'name': name[:64], 'coalition': entry.get('coalition'),
                      'frequency_hz': entry.get('frequency_hz'), 'modulation': entry.get('modulation'),
                      'channel': entry.get('channel')}
            if entry.get('enabled') is not True:
                rejected[ident] = {**report, 'state': 'disabled', 'error': str(entry.get('error') or 'disabled by registry')[:200]}
                continue
            freq, mod, side = entry.get('frequency_hz'), entry.get('modulation'), entry.get('coalition')
            problem = None
            if counts[ident] > 1:
                problem = f'duplicate {self._noun} id in registry'
            elif isinstance(side, bool) or side not in (1, 2):
                problem = 'invalid coalition'
            elif isinstance(mod, bool) or mod not in (0, 1):
                problem = 'invalid modulation'
            elif isinstance(freq, bool) or not isinstance(freq, (int, float)) or not math.isfinite(freq) or freq <= 10000:
                problem = 'invalid frequency'
            elif freq != int(freq):
                problem = 'frequency_hz must be integral Hz'
            elif entry.get('channel') != f'{side}:{int(freq)}:{mod}':
                problem = 'registry channel does not match coalition:frequency_hz:modulation'
            elif airfield and ident != entry['channel']:
                problem = 'airfield radio id must be its canonical channel'
            if problem:
                rejected[ident] = {**report, 'state': 'error', 'error': problem}
            else:
                desired[ident] = {**report, 'frequency_hz': int(freq), 'modulation': mod, 'coalition': side}
        # Radio windows must be >=200 Hz apart, so a bad registry cannot make the manager start overlapping radios.
        # Carriers: whatever the coalition/modulation. Airfields: SRS coalition security lets the native
        # frequency be reused on another coalition, so only same-coalition neighbours are ambiguous.
        def clash(one, other):
            if abs(one['frequency_hz'] - other['frequency_hz']) >= 200:
                return False
            return not airfield or one['coalition'] == other['coalition']
        clashing = {ident for ident, one in desired.items()
                    if any(other != ident and clash(one, desired[other]) for other in desired)}
        for ident in clashing:
            rejected[ident] = {**desired.pop(ident), 'state': 'error',
                               'error': f'radio frequency within 200 Hz of another {"same-coalition " if airfield else ""}{self._noun} radio'}
        return desired, rejected, epoch

    def _registry_failed(self, message):
        METRICS.add(f'{self._noun}_registry_errors_total')
        with self._lock:
            self._registry_error = message
            self._registry_ready = False
            expired = self._last_ok is None or time.monotonic() - self._last_ok > self.stale_seconds
        if expired:
            # Fail closed: without a fresh registry we cannot prove the radios still match the mission.
            with self._lock:
                idents = list(self._running)
            self._close_gateways(idents)
            with self._lock:
                self._status = {}
                self._epoch = None

    def _reconcile(self, epoch, desired, rejected):
        statuses = dict(rejected)
        wanted = {}
        for ident, entry in desired.items():
            password = self._passwords.get(entry['coalition'])
            if not password:
                statuses[ident] = {**entry, 'state': 'error',
                                   'error': f"no SRS EAM password configured for coalition {entry['coalition']}"}
                continue
            spec = (epoch, ident, entry['coalition'], entry['frequency_hz'], entry['modulation'], entry['name'], password)
            wanted[ident] = (spec, entry)
        with self._lock:
            current = dict(self._running)
        stop = [ident for ident, (spec, _) in current.items() if ident not in wanted or wanted[ident][0] != spec]
        self._close_gateways(stop)
        for ident, (spec, entry) in wanted.items():
            with self._lock:
                if ident in self._running or self._stop.is_set():
                    continue
            try:
                scope = {'airfield': True} if self.role == 'airfield' else {'carrier_id': ident}
                gateway = Gateway(name=entry['name'][:32] or None, coalition=entry['coalition'],
                                  frequency=entry['frequency_hz'], modulation=entry['modulation'],
                                  password=spec[-1], **scope)
                gateway.start()
            except Exception as error:  # one radio's failure must not affect the others
                statuses[ident] = {**entry, 'state': 'error', 'error': f'gateway start failed: {type(error).__name__}'}
                continue
            with self._lock:
                published = not self._stop.is_set()
                if published:
                    self._running[ident] = (spec, gateway)
            if not published:
                gateway.close()  # close() raced this start; never leave an unowned radio running
                continue
            _log(f'{self._noun}_gateway_started', **{f'{self._noun}_id': ident}, channel=gateway.channel)
        with self._lock:
            for ident, (spec, entry) in wanted.items():
                if ident in self._running and ident not in statuses:
                    statuses[ident] = {**entry, 'state': 'running', 'error': None}
            self._status = statuses
            self._epoch = epoch
            self._last_ok = time.monotonic()
            self._registry_error = self._credential_error
            self._registry_ready = True

    def _close_gateways(self, idents):
        with self._lock:
            removed = [(ident, self._running.pop(ident)[1]) for ident in idents if ident in self._running]
        clean = True
        for ident, gateway in removed:
            if not gateway.close():
                clean = False
                METRICS.add(f'{self._noun}_gateway_close_timeouts_total')
            _log(f'{self._noun}_gateway_stopped', **{f'{self._noun}_id': ident})
        return clean

    def ready(self):
        with self._lock:
            if not self._registry_ready or self._last_ok is None or time.monotonic() - self._last_ok > self.stale_seconds:
                return False
            gateways = [gateway for _, gateway in self._running.values()]
        return all(gateway.ready() for gateway in gateways)

    def snapshot(self):
        """Secret-free health view for the supervisor endpoint."""
        with self._lock:
            age = None if self._last_ok is None else round(time.monotonic() - self._last_ok, 3)
            radios = []
            for ident, report in sorted(self._status.items()):
                running = self._running.get(ident)
                client = running[1].client if running else None
                radios.append({**report, 'connected': bool(client and client.connected)})
            return {'ready_registry': self._registry_ready, 'epoch': self._epoch, 'registry_age_seconds': age,
                    'registry_error': self._registry_error, 'running': len(self._running), self._listing: radios}
