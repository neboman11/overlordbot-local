import json
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch
from overlord.gateway import Gateway
from overlord.radio_gateways import RadioGateways

ENV = {'BRAIN_URL': 'http://127.0.0.1:9', 'STT_URLS': 'http://127.0.0.1:9', 'TTS_URLS': 'http://127.0.0.1:9',
       'COALITION': '2', 'SRS_FREQUENCY_HZ': '251000000', 'SRS_MODULATION': '0', 'BOT_CALLSIGN': 'Overlord',
       'SRS_EAM_PASSWORD': '', 'SRS_EAM_PASSWORDS': json.dumps({'1': 'red-secret', '2': 'blue-secret'}),
       'API_TOKEN': '', 'STT_PROMPT': ''}


class FakeSRS:
    """Stands in for the SRS wire only; Gateway threading/lifecycle code under test is real."""
    instances = []

    def __init__(self, host, port, password, coalition, frequency, modulation, name):
        self.password, self.coalition, self.frequency, self.modulation, self.name = password, coalition, frequency, modulation, name
        self.connected = False
        self.closed = threading.Event()
        self.clients = {}
        self.transmitted = []
        FakeSRS.instances.append(self)

    def connect(self):
        self.connected = True
        return self

    def receive(self):
        if self.closed.is_set():
            raise ConnectionError('closed')
        time.sleep(.005)

    def transmit(self, pcm):
        self.transmitted.append(pcm)

    def close(self):
        self.connected = False
        self.closed.set()


def radio(freq=121000000, side=2, mod=0, enabled=True, error=None, name='Ironwood / Maple'):
    channel = f'{side}:{freq}:{mod}'
    return {'id': channel, 'name': name, 'coalition': side, 'frequency_hz': freq, 'modulation': mod,
            'channel': channel, 'enabled': enabled, 'error': error}


def registry(*radios, epoch='a1'):
    return {'ready': True, 'epoch': epoch, 'radios': list(radios), 'airfields': [], 'errors': []}


class Base(unittest.TestCase):
    def setUp(self):
        FakeSRS.instances = []
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        srs = patch('overlord.gateway.SRSClient', FakeSRS)
        srs.start()
        self.addCleanup(srs.stop)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.assertLess(time.monotonic(), deadline, 'condition not reached')
            time.sleep(.005)

    def live(self):
        return [client for client in FakeSRS.instances if not client.closed.is_set()]


class AirfieldGatewayTests(Base):
    def test_flag_is_mutually_exclusive_with_carrier_id_and_defaults_off(self):
        with self.assertRaises(ValueError):
            Gateway(airfield=True, carrier_id='71')
        self.assertFalse(Gateway().airfield)
        self.assertFalse(Gateway(carrier_id='71').airfield)

    def process_one(self, gateway, validation, reply_state=None, on_validate=None):
        client = FakeSRS('h', 1, 'p', gateway.coalition, gateway.frequency, gateway.modulation, 'n')
        client.connected = True
        client.clients = {'pilot': {'Name': 'Viper 1-1'}}
        gateway.client = client
        gateway.stt, gateway.tts, gateway.brain = Mock(), Mock(), Mock()
        gateway.tts.post.return_value = b'\0\0' * 1600
        gateway.respond_body = None
        def post(path, body, *args):
            if path == '/respond':
                gateway.respond_body = json.loads(body)
                return (json.dumps({'reply': 'ok', 'validation': validation}).encode()
                        if validation is not False else b'{"reply":"ok"}')
            if on_validate:
                on_validate()
            return b'{"valid":true}'
        gateway.brain.post.side_effect = post
        if reply_state:
            gateway.jobs.put((client, None, reply_state))
        else:
            gateway.stt.post.return_value = b'{"text":"Ironwood ground request startup"}'
            gateway.jobs.put((client, 'pilot', {'packets': {1: (1, b'o')}, 'last': time.monotonic()}))
        codec = Mock()
        codec.decode.return_value = b'\0\0' * 1600
        threading.Timer(.2, gateway.close).start()
        with patch('overlord.gateway.Opus', return_value=codec):
            gateway._process()
        return client

    def scoped(self, gateway, **overrides):
        return {'role': 'airfield', 'airfield': True, 'airfield_id': '12', 'channel': gateway.channel,
                'coalition': gateway.coalition, **overrides}

    def test_request_carries_airfield_flag_and_dynamic_field_validation_is_transmitted(self):
        for field in ('12', '99'):  # the field is chosen per call by the brain, never fixed per radio
            gateway = Gateway(coalition=2, frequency=121000000, modulation=0, airfield=True)
            client = self.process_one(gateway, self.scoped(gateway, airfield_id=field))
            self.assertIs(gateway.respond_body['airfield'], True)
            self.assertNotIn('carrier_id', gateway.respond_body)
            self.assertEqual(len(client.transmitted), 1, field)

    def test_airfield_gateway_requires_matching_role_channel_and_coalition(self):
        for change in ({'role': 'carrier'}, {'role': None}, {'channel': '2:122000000:0'}, {'coalition': 1},
                       {'carrier_id': '71'}, {'airfield': False}):
            gateway = Gateway(coalition=2, frequency=121000000, modulation=0, airfield=True)
            client = self.process_one(gateway, self.scoped(gateway, **change))
            self.assertEqual(client.transmitted, [], change)
        gateway = Gateway(coalition=2, frequency=121000000, modulation=0, airfield=True)
        self.assertEqual(self.process_one(gateway, False).transmitted, [], 'unvalidated airfield reply')

    def test_ordinary_and_carrier_gateways_reject_airfield_metadata(self):
        for kwargs in ({}, {'carrier_id': '71'}):
            for extra in ({'role': 'airfield'}, {'airfield': True}, {'airfield_id': '12'}):
                gateway = Gateway(coalition=2, frequency=264000000, modulation=0, **kwargs)
                validation = {'role': 'carrier' if kwargs else 'jtac', 'carrier_id': kwargs.get('carrier_id'),
                              'channel': gateway.channel, 'coalition': 2, **extra}
                client = self.process_one(gateway, validation)
                self.assertEqual(client.transmitted, [], (kwargs, extra))

    def test_notification_must_be_airfield_role_scoped_and_is_validated(self):
        for notification, sent in (({'role': 'airfield', 'airfield_id': '12'}, 1), ({'role': 'carrier'}, 0),
                                   ({'role': 'airfield', 'channel': '2:122000000:0'}, 0), ({}, 0),
                                   ({'role': 'airfield', 'coalition': 1}, 0)):
            gateway = Gateway(coalition=2, frequency=121000000, modulation=0, airfield=True)
            note = {'reply': 'n', 'ttl_seconds': 5, 'channel': gateway.channel, 'coalition': 2, **notification}
            if notification.get('channel'):
                note['channel'] = notification['channel']
            state = {'reply': 'n', 'notification': note, 'expires': time.monotonic() + 30}
            client = self.process_one(gateway, None, reply_state=state)
            self.assertEqual(len(client.transmitted), sent, notification)

    def test_airfield_validation_rechecked_after_synthesis_blocks_transmission(self):
        gateway = Gateway(coalition=2, frequency=121000000, modulation=0, airfield=True)
        client = self.process_one(gateway, self.scoped(gateway), on_validate=gateway.stop.set)
        self.assertEqual(client.transmitted, [])

    def test_events_poll_carries_airfield_flag_not_a_field_id(self):
        gateway = Gateway(airfield=True, coalition=2, frequency=121000000, modulation=0, password='p')
        gateway.client = FakeSRS('h', 1, 'p', 2, 1, 0, 'n')
        gateway.client.connected = True
        bodies = []
        gateway.brain = Mock()
        gateway.brain.post.side_effect = lambda path, body: bodies.append((path, json.loads(body))) or b'{"events":[]}'
        thread = threading.Thread(target=gateway._events)
        thread.start()
        self.wait_for(lambda: bodies)
        gateway.close()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(bodies[0], ('/events', {'coalition': 2, 'channel': gateway.channel, 'airfield': True}))


class AirfieldReadinessTests(Base):
    def gateway(self, payload):
        gateway = Gateway(airfield=True, coalition=2, frequency=121000000, modulation=0, password='p')
        gateway.client = FakeSRS('h', 1, 'p', 2, 121000000, 0, 'n')
        gateway.client.connected = True
        gateway.stt, gateway.tts, gateway.brain = Mock(), Mock(), Mock()
        gateway.stt.ready.return_value = gateway.tts.ready.return_value = True
        gateway.brain.ready.return_value = False  # Tacview telemetry is stale/absent
        gateway.brain.post.return_value = json.dumps(payload).encode()
        return gateway

    def test_ready_uses_own_airfield_registry_entry_not_general_brain_health(self):
        gateway = self.gateway(registry(radio()))
        self.assertTrue(gateway.ready())
        self.assertEqual(gateway.brain.post.call_args[0][0], '/airfield/registry')

    def test_not_ready_when_registry_stale_disabled_missing_or_radio_differs(self):
        stale = registry(radio())
        stale['ready'] = False
        malformed = registry(radio())
        malformed['radios'] = {}
        for payload in (stale, malformed, registry(radio(enabled=False)), registry(radio(122000000)),
                        registry(radio(side=1)), registry()):
            self.assertFalse(self.gateway(payload).ready())
        failing = self.gateway(registry())
        failing.brain.post.side_effect = ConnectionError('down')
        self.assertFalse(failing.ready())


class AirfieldManagerTests(Base):
    def manager(self, **kwargs):
        manager = RadioGateways('airfield', poll_seconds=.01, **kwargs)
        self.addCleanup(manager.close)
        manager.registry = Mock()
        return manager

    def feed(self, manager, payload):
        manager.registry.post.side_effect = None
        manager.registry.post.return_value = json.dumps(payload).encode()
        manager.refresh()

    def test_role_validation_and_registry_path(self):
        with self.assertRaises(ValueError):
            RadioGateways('jtac')
        manager = self.manager()
        self.feed(manager, registry(radio()))
        self.assertEqual(manager.registry.post.call_args[0][0], '/airfield/registry')

    def test_one_airfield_gateway_per_canonical_channel_even_with_many_fields(self):
        manager = self.manager()
        self.feed(manager, registry(radio(121000000, name='Ironwood / Maple / Pine'), radio(122000000, side=1, mod=1)))
        self.wait_for(lambda: len(self.live()) == 2)
        gateways = {gateway.channel: gateway for _, gateway in manager._running.values()}
        self.assertEqual(set(gateways), {'2:121000000:0', '1:122000000:1'})
        for gateway in gateways.values():
            self.assertTrue(gateway.airfield)
            self.assertIsNone(gateway.carrier_id)
        self.assertEqual(gateways['2:121000000:0'].name, 'Ironwood / Maple / Pine')
        self.assertNotIn(251000000.0, {c.frequency for c in self.live()})
        report = manager.snapshot()
        self.assertEqual({r['id'] for r in report['radios']}, {'2:121000000:0', '1:122000000:1'})
        self.assertNotIn('carriers', report)

    def test_same_native_frequency_on_different_coalitions_is_allowed(self):
        manager = self.manager()
        self.feed(manager, registry(radio(121000000, side=1), radio(121000000, side=2), radio(121000050, side=2, mod=1)))
        self.wait_for(lambda: len(self.live()) == 1)
        states = {r['id']: r for r in manager.snapshot()['radios']}
        self.assertEqual(states['1:121000000:0']['state'], 'running')
        self.assertEqual(states['2:121000000:0']['state'], 'error')
        self.assertEqual(states['2:121000050:1']['state'], 'error')
        self.assertIn('200 Hz', states['2:121000000:0']['error'])

    def test_same_coalition_ambiguous_sub_200hz_and_distinct_cross_coalition_pairs(self):
        manager = self.manager()
        self.feed(manager, registry(radio(121000000, side=2), radio(121000199, side=2, mod=1),
                                    radio(122000000, side=1), radio(122000100, side=2, mod=1)))
        self.wait_for(lambda: len(self.live()) == 2)
        states = {r['id']: r['state'] for r in manager.snapshot()['radios']}
        self.assertEqual(states, {'2:121000000:0': 'error', '2:121000199:1': 'error',
                                  '1:122000000:0': 'running', '2:122000100:1': 'running'})

    def test_non_canonical_duplicate_and_invalid_radios_never_start(self):
        manager = self.manager()
        wrong_id = radio(121000000)
        wrong_id['id'] = 'ironwood'
        bad_channel = radio(122000000)
        bad_channel['channel'] = '2:122000001:0'
        self.feed(manager, registry(wrong_id, bad_channel, radio(123000000), radio(123000000),
                                    radio(124000000, mod=7), radio(None), radio(125000000, enabled=False, error='closed'),
                                    radio(126000000)))
        self.wait_for(lambda: len(self.live()) == 1)
        self.assertEqual(self.live()[0].frequency, 126000000.0)
        states = {r['id']: r['state'] for r in manager.snapshot()['radios']}
        self.assertEqual(states['ironwood'], 'error')
        self.assertEqual(states['2:125000000:0'], 'disabled')
        self.assertEqual(states['2:123000000:0'], 'error')

    def test_changes_replace_only_that_radio_and_epoch_replaces_all(self):
        manager = self.manager()
        self.feed(manager, registry(radio(121000000), radio(122000000)))
        self.wait_for(lambda: len(self.live()) == 2)
        before = list(FakeSRS.instances)
        self.feed(manager, registry(radio(121000000), radio(122000000)))
        self.assertEqual(FakeSRS.instances, before)
        self.feed(manager, registry(radio(121000000), radio(123000000)))
        self.wait_for(lambda: sorted(c.frequency for c in self.live()) == [121000000.0, 123000000.0])
        self.assertTrue(next(c for c in before if c.frequency == 122000000.0).closed.is_set())
        self.assertFalse(next(c for c in before if c.frequency == 121000000.0).closed.is_set())
        old = self.live()
        self.feed(manager, registry(radio(121000000), radio(123000000), epoch='a2'))
        self.assertTrue(all(client.closed.is_set() for client in old))
        self.wait_for(lambda: len(self.live()) == 2)

    def test_credentials_are_per_coalition_never_snapshotted_and_never_borrowed(self):
        with patch.dict(os.environ, {'SRS_EAM_PASSWORDS': '', 'SRS_EAM_PASSWORD': 'blue-only', 'COALITION': '2'}):
            manager = self.manager()
        self.feed(manager, registry(radio(121000000, side=1), radio(122000000, side=2)))
        self.wait_for(lambda: len(self.live()) == 1)
        client = self.live()[0]
        self.assertEqual((client.coalition, client.password), (2, 'blue-only'))
        report = {r['id']: r for r in manager.snapshot()['radios']}
        self.assertEqual(report['1:121000000:0']['state'], 'error')
        self.assertIn('coalition 1', report['1:121000000:0']['error'])
        text = json.dumps(manager.snapshot())
        self.assertNotIn('blue-only', text)

    def test_registry_outage_grace_then_fail_closed_and_not_ready_registry_adds_nothing(self):
        manager = self.manager(stale_seconds=.3)
        self.feed(manager, registry(radio()))
        self.wait_for(lambda: len(self.live()) == 1)
        manager.registry.post.side_effect = ConnectionError('brain down')
        manager.refresh()
        self.assertEqual(len(self.live()), 1)
        self.assertFalse(manager.ready())
        time.sleep(.35)
        manager.refresh()
        self.assertEqual(self.live(), [])
        self.assertEqual(manager.snapshot()['radios'], [])
        fresh = self.manager()
        payload = registry(radio())
        payload['ready'] = False
        before = len(FakeSRS.instances)
        self.feed(fresh, payload)
        self.assertEqual(len(FakeSRS.instances), before)

    def test_one_failing_start_does_not_block_others_and_close_stops_every_thread(self):
        manager = self.manager()
        real = Gateway
        def factory(**kwargs):
            if kwargs['frequency'] == 121000000:
                raise ValueError('bad radio')
            return real(**kwargs)
        with patch('overlord.radio_gateways.Gateway', factory):
            self.feed(manager, registry(radio(121000000), radio(122000000)))
        self.wait_for(lambda: len(self.live()) == 1)
        states = {r['id']: r['state'] for r in manager.snapshot()['radios']}
        self.assertEqual(states, {'2:121000000:0': 'error', '2:122000000:0': 'running'})
        manager.start()
        threads = [thread for _, gateway in manager._running.values() for thread in gateway._threads] + [manager._thread]
        self.assertTrue(manager.close())
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(self.live(), [])
        with self.assertRaises(RuntimeError):
            manager.start()

    def test_close_during_slow_reconcile_leaves_no_running_gateway(self):
        manager = self.manager()
        self.feed(manager, registry(radio(121000000), radio(122000000)))
        self.wait_for(lambda: len(self.live()) == 2)
        entered, release = threading.Event(), threading.Event()
        key = '2:121000000:0'
        old_close = manager._running[key][1].close
        def slow_close(*args, **kwargs):
            entered.set()
            release.wait(3)
            return old_close(*args, **kwargs)
        manager._running[key][1].close = slow_close
        manager.registry.post.return_value = json.dumps(registry(radio(122000000), radio(123000000))).encode()
        worker = threading.Thread(target=manager.refresh)
        worker.start()
        self.assertTrue(entered.wait(2))
        closer = threading.Thread(target=manager.close)
        closer.start()
        time.sleep(.05)
        release.set()
        worker.join(5)
        closer.join(5)
        self.assertFalse(worker.is_alive() or closer.is_alive())
        self.assertEqual(self.live(), [])
        self.assertEqual(manager.snapshot()['running'], 0)

    def test_close_reports_gateway_that_would_not_stop(self):
        manager = self.manager()
        self.feed(manager, registry(radio()))
        self.wait_for(lambda: len(self.live()) == 1)
        next(iter(manager._running.values()))[1].close = lambda *a, **k: False
        self.assertFalse(manager.close())


if __name__ == '__main__':
    unittest.main()
