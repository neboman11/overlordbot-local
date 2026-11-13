import json
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch
from overlord.radio_gateways import RadioGateways
from overlord.gateway import Gateway

ENV = {'BRAIN_URL': 'http://127.0.0.1:9', 'STT_URLS': 'http://127.0.0.1:9', 'TTS_URLS': 'http://127.0.0.1:9',
       'COALITION': '2', 'SRS_FREQUENCY_HZ': '251000000', 'SRS_MODULATION': '0', 'BOT_CALLSIGN': 'Overlord',
       'SRS_EAM_PASSWORD': '', 'SRS_EAM_PASSWORDS': json.dumps({'1': 'red-secret', '2': 'blue-secret'}), 'API_TOKEN': ''}


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


def entry(ident, freq=264000000, side=2, mod=0, enabled=True, error=None):
    return {'id': ident, 'name': 'CVN-' + ident, 'type': 'Stennis', 'coalition': side, 'frequency_hz': freq,
            'modulation': mod, 'channel': f'{side}:{freq}:{mod}', 'enabled': enabled, 'error': error}


def registry(*carriers, epoch='e1'):
    return {'ready': True, 'epoch': epoch, 'carriers': list(carriers), 'errors': []}


class Base(unittest.TestCase):
    def setUp(self):
        FakeSRS.instances = []
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        srs = patch('overlord.gateway.SRSClient', FakeSRS)
        srs.start()
        self.addCleanup(srs.stop)

    def manager(self, **kwargs):
        manager = RadioGateways('carrier', poll_seconds=.01, **kwargs)
        self.addCleanup(manager.close)
        manager.registry = Mock()
        return manager

    def feed(self, manager, payload):
        manager.registry.post.side_effect = None
        manager.registry.post.return_value = json.dumps(payload).encode()
        manager.refresh()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.assertLess(time.monotonic(), deadline, 'condition not reached')
            time.sleep(.005)

    def live(self):
        return [client for client in FakeSRS.instances if not client.closed.is_set()]


class GatewayInstanceTests(Base):
    def test_overrides_are_per_instance_and_leave_environment_alone(self):
        before = dict(os.environ)
        carrier = Gateway(name='CVN-71', coalition=1, frequency=264000000, modulation=0, password='x', carrier_id='71')
        plain = Gateway()
        self.assertEqual(os.environ, before)
        self.assertEqual((carrier.channel, carrier.password, carrier.carrier_id), ('1:264000000:0', 'x', '71'))
        self.assertEqual((plain.channel, plain.name, plain.carrier_id), ('2:251000000:0', 'Overlord', None))

    def test_close_terminates_all_threads_idempotently_and_closes_client(self):
        gateway = Gateway(carrier_id='1', password='p')
        gateway.start()
        gateway.start()
        self.wait_for(lambda: gateway.client is not None)
        threads = list(gateway._threads)
        self.assertEqual(len(threads), 3)
        self.assertTrue(gateway.close())
        self.assertTrue(gateway.close())
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertTrue(FakeSRS.instances[0].closed.is_set())
        with self.assertRaises(RuntimeError):
            gateway.start()

    def test_close_while_srs_connect_in_flight_does_not_leave_connected_client(self):
        entered, release = threading.Event(), threading.Event()
        class Slow(FakeSRS):
            def connect(self):
                entered.set()
                release.wait(2)
                return super().connect()
        with patch('overlord.gateway.SRSClient', Slow):
            gateway = Gateway(password='p')
            gateway.start()
            self.assertTrue(entered.wait(2))
            threading.Timer(.05, release.set).start()
            self.assertTrue(gateway.close())
        self.assertTrue(FakeSRS.instances[-1].closed.is_set())
        self.assertIsNone(gateway.client)

    def process_one(self, gateway, validation, reply_state=None, on_validate=None):
        client = FakeSRS('h', 1, 'p', gateway.coalition, gateway.frequency, gateway.modulation, 'n')
        client.connected = True
        client.clients = {'pilot': {'Name': 'Dollar 1-1'}}
        gateway.client = client
        gateway.stt, gateway.tts, gateway.brain = Mock(), Mock(), Mock()
        gateway.tts.post.return_value = b'\0\0' * 1600
        def post(path, body, *args):
            if path == '/respond':
                gateway.respond_body = json.loads(body)
                return json.dumps({'reply': 'ok', 'validation': validation}).encode() if validation is not False else b'{"reply":"ok"}'
            if on_validate:
                on_validate()
            return b'{"valid":true}'
        gateway.brain.post.side_effect = post
        if reply_state:
            gateway.jobs.put((client, None, reply_state))
            gateway.tts.post.return_value = b'\0\0' * 1600
        else:
            gateway.stt.post.return_value = b'{"text":"request"}'
            gateway.jobs.put((client, 'pilot', {'packets': {1: (1, b'o')}, 'last': time.monotonic()}))
        codec = Mock()
        codec.decode.return_value = b'\0\0' * 1600
        stopper = threading.Timer(1, gateway.stop.set)
        stopper.start()
        # exit after the single job: closing wakes the blocked queue read
        threading.Timer(.2, gateway.close).start()
        with patch('overlord.gateway.Opus', return_value=codec):
            gateway._process()
        stopper.cancel()
        return client

    def test_carrier_id_is_sent_and_matching_valid_reply_is_transmitted(self):
        gateway = Gateway(coalition=2, frequency=264000000, modulation=0, carrier_id='71')
        validation = {'role': 'carrier', 'carrier_id': '71', 'channel': gateway.channel, 'coalition': 2}
        client = self.process_one(gateway, validation)
        self.assertEqual(gateway.respond_body['carrier_id'], '71')
        self.assertEqual(len(client.transmitted), 1)

    def test_reply_bound_to_another_carrier_or_unvalidated_is_not_transmitted(self):
        for validation in ({'role': 'carrier', 'carrier_id': '72', 'channel': '2:264000000:0', 'coalition': 2}, False):
            gateway = Gateway(coalition=2, frequency=264000000, modulation=0, carrier_id='71')
            client = self.process_one(gateway, validation)
            self.assertEqual(client.transmitted, [], validation)

    def test_non_carrier_gateway_rejects_carrier_validation(self):
        gateway = Gateway()
        validation = {'role': 'carrier', 'carrier_id': '71', 'channel': gateway.channel, 'coalition': 2}
        self.assertEqual(self.process_one(gateway, validation).transmitted, [])

    def test_stop_during_final_validation_prevents_transmission(self):
        gateway = Gateway(coalition=2, frequency=264000000, modulation=0, carrier_id='71')
        validation = {'role': 'carrier', 'carrier_id': '71', 'channel': gateway.channel, 'coalition': 2}
        client = self.process_one(gateway, validation, on_validate=gateway.stop.set)
        self.assertEqual(client.transmitted, [])

    def test_disconnect_during_validation_prevents_transmission(self):
        gateway = Gateway(carrier_id='71', coalition=2, frequency=264000000, modulation=0)
        validation = {'role': 'carrier', 'carrier_id': '71', 'channel': gateway.channel, 'coalition': 2}
        client = self.process_one(gateway, validation, on_validate=lambda: gateway.client.close())
        self.assertEqual(client.transmitted, [])

    def test_queued_notification_for_other_carrier_is_dropped_valid_one_sent(self):
        for carrier_id, sent in (('72', 0), ('71', 1)):
            gateway = Gateway(coalition=2, frequency=264000000, modulation=0, carrier_id='71')
            notification = {'reply': 'n', 'ttl_seconds': 5, 'carrier_id': carrier_id, 'role': 'carrier'}
            state = {'reply': 'n', 'notification': notification, 'expires': time.monotonic() + 30}
            client = self.process_one(gateway, None, reply_state=state)
            self.assertEqual(len(client.transmitted), sent, carrier_id)

    def test_events_poll_carries_carrier_id_and_close_ends_event_thread(self):
        gateway = Gateway(carrier_id='71', password='p')
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
        self.assertEqual(bodies[0], ('/events', {'coalition': 2, 'channel': gateway.channel, 'carrier_id': '71'}))


class CarrierReadinessTests(Base):
    def gateway(self, payload, **overrides):
        gateway = Gateway(carrier_id='71', coalition=2, frequency=264000000, modulation=0, password='p', **overrides)
        gateway.client = FakeSRS('h', 1, 'p', 2, 264000000, 0, 'n')
        gateway.client.connected = True
        gateway.stt, gateway.tts, gateway.brain = Mock(), Mock(), Mock()
        gateway.stt.ready.return_value = gateway.tts.ready.return_value = True
        gateway.brain.ready.return_value = False  # AWACS/JTAC telemetry is stale
        gateway.brain.post.return_value = json.dumps(payload).encode()
        return gateway

    def test_carrier_ready_uses_own_registry_entry_not_general_brain_health(self):
        self.assertTrue(self.gateway(registry(entry('71', 264000000))).ready())

    def test_carrier_not_ready_when_registry_stale_disabled_or_radio_differs(self):
        stale = registry(entry('71', 264000000))
        stale['ready'] = False
        for payload in (stale, registry(entry('71', 264000000, enabled=False)), registry(entry('71', 265000000)),
                        registry(entry('71', 264000000, side=1)), registry(entry('72', 264000000)), registry()):
            self.assertFalse(self.gateway(payload).ready())
        failing = self.gateway(registry())
        failing.brain.post.side_effect = ConnectionError('down')
        self.assertFalse(failing.ready())

    def test_ordinary_gateway_still_requires_general_brain_readiness(self):
        gateway = self.gateway(registry())
        gateway.carrier_id = None
        self.assertFalse(gateway.ready())
        gateway.brain.ready.return_value = True
        self.assertTrue(gateway.ready())

    def test_carrier_gateway_biases_stt_toward_carrier_phrases_unless_operator_prompt_set(self):
        self.assertIn('Case one', Gateway(carrier_id='71').prompt)
        self.assertNotIn('bogey', Gateway(carrier_id='71').prompt)
        self.assertIn('bogey', Gateway().prompt)
        with patch.dict(os.environ, {'STT_PROMPT': 'custom words'}):
            self.assertEqual(Gateway(carrier_id='71').prompt, 'custom words')


class ManagerTests(Base):
    def test_one_gateway_per_enabled_carrier_using_registry_radio(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1', 264000000, 2), entry('2', 265000000, 1, mod=1)))
        self.wait_for(lambda: len(self.live()) == 2)
        by_freq = {client.frequency: client for client in self.live()}
        self.assertEqual(set(by_freq), {264000000.0, 265000000.0})
        self.assertEqual((by_freq[265000000.0].coalition, by_freq[265000000.0].modulation), (1, 1))
        # SRS_FREQUENCY_HZ env default never leaks into a carrier radio.
        self.assertNotIn(251000000.0, by_freq)

    def test_unchanged_registry_does_not_churn_and_change_replaces_only_that_radio(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1', 264000000), entry('2', 265000000)))
        self.wait_for(lambda: len(self.live()) == 2)
        before = list(FakeSRS.instances)
        self.feed(manager, registry(entry('1', 264000000), entry('2', 265000000)))
        self.assertEqual(FakeSRS.instances, before)
        self.feed(manager, registry(entry('1', 264000000), entry('2', 266000000)))
        self.wait_for(lambda: sorted(c.frequency for c in self.live()) == [264000000.0, 266000000.0])
        self.assertTrue(next(c for c in before if c.frequency == 265000000.0).closed.is_set())
        self.assertFalse(next(c for c in before if c.frequency == 264000000.0).closed.is_set())

    def test_removed_dead_disabled_and_epoch_changed_radios_are_closed(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1'), entry('2', 265000000)))
        self.wait_for(lambda: len(self.live()) == 2)
        self.feed(manager, registry(entry('1'), entry('2', 265000000, enabled=False, error='ship destroyed')))
        self.wait_for(lambda: len(self.live()) == 1)
        self.assertEqual(manager.snapshot()['carriers'][1]['state'], 'disabled')
        old = self.live()[0]
        self.feed(manager, registry(entry('1'), epoch='e2'))
        self.assertTrue(old.closed.is_set())
        self.wait_for(lambda: len(self.live()) == 1 and self.live()[0] is not old)

    def test_empty_valid_roster_closes_everything(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1')))
        self.wait_for(lambda: len(self.live()) == 1)
        self.feed(manager, registry())
        self.assertEqual(self.live(), [])
        self.assertTrue(manager.ready())
        self.assertEqual(manager.snapshot()['running'], 0)

    def test_conflicting_and_invalid_entries_never_start(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1', 264000000), entry('2', 264000000), entry('3', None), entry('4', 267000000, mod=7),
                                    entry('5', 268000000, enabled=False), entry('6', 269000000)))
        self.wait_for(lambda: len(self.live()) == 1)
        self.assertEqual(self.live()[0].frequency, 269000000.0)
        states = {c['id']: c['state'] for c in manager.snapshot()['carriers']}
        self.assertEqual(states, {'1': 'error', '2': 'error', '3': 'error', '4': 'error', '5': 'disabled', '6': 'running'})

    def test_channel_mismatch_and_sub_200hz_spacing_are_rejected_across_coalitions(self):
        manager = self.manager()
        wrong = entry('wrong', 270000000)
        wrong['channel'] = '2:270000001:0'
        self.feed(manager, registry(wrong, entry('a', 264000000, side=1), entry('b', 264000199, side=2, mod=1),
                                    entry('c', 265000000), entry('d', 265000200, side=1)))
        self.wait_for(lambda: len(self.live()) == 2)
        states = {c['id']: c for c in manager.snapshot()['carriers']}
        self.assertEqual({k: v['state'] for k, v in states.items()},
                         {'wrong': 'error', 'a': 'error', 'b': 'error', 'c': 'running', 'd': 'running'})
        self.assertIn('channel', states['wrong']['error'])
        self.assertIn('200 Hz', states['a']['error'])

    def test_missing_credential_is_per_carrier_and_never_borrows_other_coalition(self):
        with patch.dict(os.environ, {'SRS_EAM_PASSWORDS': '', 'SRS_EAM_PASSWORD': 'blue-only', 'COALITION': '2'}):
            manager = self.manager()
        self.feed(manager, registry(entry('red', 264000000, side=1), entry('blue', 265000000, side=2)))
        self.wait_for(lambda: len(self.live()) == 1)
        client = self.live()[0]
        self.assertEqual((client.coalition, client.password), (2, 'blue-only'))
        report = {c['id']: c for c in manager.snapshot()['carriers']}
        self.assertEqual(report['red']['state'], 'error')
        self.assertIn('coalition 1', report['red']['error'])
        self.assertEqual(report['blue']['state'], 'running')

    def test_per_coalition_passwords_map_selects_matching_secret(self):
        manager = self.manager()
        self.feed(manager, registry(entry('red', 264000000, side=1), entry('blue', 265000000, side=2)))
        self.wait_for(lambda: len(self.live()) == 2)
        self.assertEqual({c.coalition: c.password for c in self.live()}, {1: 'red-secret', 2: 'blue-secret'})

    def test_snapshot_contains_no_secrets(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1', side=1), entry('2', 265000000, side=2)))
        self.wait_for(lambda: len(self.live()) == 2)
        text = json.dumps(manager.snapshot())
        self.assertNotIn('secret', text)
        self.assertNotIn('password', text.lower())

    def test_registry_outage_keeps_radios_during_grace_then_fails_closed(self):
        manager = self.manager(stale_seconds=.3)
        self.feed(manager, registry(entry('1')))
        self.wait_for(lambda: len(self.live()) == 1)
        manager.registry.post.side_effect = ConnectionError('brain down')
        manager.refresh()
        self.assertEqual(len(self.live()), 1)
        self.assertFalse(manager.ready())
        time.sleep(.35)
        manager.refresh()
        self.assertEqual(self.live(), [])
        self.assertEqual(manager.snapshot()['carriers'], [])
        self.assertIn('ConnectionError', manager.snapshot()['registry_error'])

    def test_not_ready_registry_never_adds_radios(self):
        manager = self.manager()
        payload = registry(entry('1'))
        payload['ready'] = False
        self.feed(manager, payload)
        self.assertEqual(FakeSRS.instances, [])
        self.assertFalse(manager.ready())

    def test_recovery_after_outage_restarts_radios(self):
        manager = self.manager(stale_seconds=.05)
        self.feed(manager, registry(entry('1')))
        self.wait_for(lambda: len(self.live()) == 1)
        manager.registry.post.side_effect = ConnectionError('down')
        time.sleep(.1)
        manager.refresh()
        self.assertEqual(self.live(), [])
        self.feed(manager, registry(entry('1')))
        self.wait_for(lambda: len(self.live()) == 1)

    def test_close_during_slow_reconcile_leaves_no_running_gateway(self):
        manager = self.manager()
        self.feed(manager, registry(entry('1'), entry('2', 265000000)))
        self.wait_for(lambda: len(self.live()) == 2)
        # Removing '1' blocks inside Gateway.close while close() of the pool races the in-flight reconcile
        # that is about to start replacement '3'.
        entered, release = threading.Event(), threading.Event()
        old_close = manager._running['1'][1].close
        def slow_close(*args, **kwargs):
            entered.set()
            release.wait(3)
            return old_close(*args, **kwargs)
        manager._running['1'][1].close = slow_close
        manager.registry.post.return_value = json.dumps(registry(entry('2', 265000000), entry('3', 266000000))).encode()
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
        self.feed(manager, registry(entry('1')))
        self.wait_for(lambda: len(self.live()) == 1)
        manager._running['1'][1].close = lambda *a, **k: False
        self.assertFalse(manager.close())

    def test_close_stops_poller_and_every_gateway_thread(self):
        manager = self.manager()
        manager.registry.post.return_value = json.dumps(registry(entry('1'), entry('2', 265000000))).encode()
        manager.start()
        self.wait_for(lambda: len(self.live()) == 2)
        gateways = [gateway for _, gateway in manager._running.values()]
        threads = [thread for gateway in gateways for thread in gateway._threads] + [manager._thread]
        self.assertTrue(manager.close())
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(self.live(), [])
        manager.close()

    def test_one_failing_gateway_start_does_not_block_others(self):
        manager = self.manager()
        real = Gateway
        def factory(**kwargs):
            if kwargs['carrier_id'] == 'bad':
                raise ValueError('bad radio')
            return real(**kwargs)
        with patch('overlord.radio_gateways.Gateway', factory):
            self.feed(manager, registry(entry('bad', 264000000), entry('good', 265000000)))
        self.wait_for(lambda: len(self.live()) == 1)
        states = {c['id']: c['state'] for c in manager.snapshot()['carriers']}
        self.assertEqual(states, {'bad': 'error', 'good': 'running'})


if __name__ == '__main__':
    unittest.main()
