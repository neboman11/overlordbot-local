"""Carrier routing uses mission identities/channels, independently of Tacview."""
import copy
import unittest

from overlord.brain import Brain
from overlord.carrier import CarrierService
from overlord.tacview import World

RADIO_ONE = '2:127500000:0'
RADIO_TWO = '2:128500000:0'


def carrier(ship_id, name, frequency, x=0):
    return {'id': ship_id, 'name': name, 'type': 'CVN_71', 'coalition': 2, 'alive': True,
            'frequency_hz': frequency, 'modulation': 0, 'point': {'x': x, 'y': 0, 'z': 0},
            'velocity': {'x': 10, 'y': 0, 'z': 0}, 'heading_deg': 90, 'lat': 43.0, 'lon': 41.0,
            'recovery_case': 1, 'altimeter_inhg': 29.92, 'ceiling_ft': 5000,
            'visibility_nm': 10, 'deck_angle_deg': 9, 'deck_height_m': 20}


def player(unit_id, callsign, name, modex):
    return {'unit_id': unit_id, 'unit_name': 'Aircraft-' + unit_id, 'player_name': name,
            'callsign': callsign, 'modex': modex, 'coalition': 2, 'incarnation': unit_id + '-birth1',
            'human': True}


def snapshot(seq=1, epoch='mission-one'):
    return {'version': 1, 'epoch': epoch, 'seq': seq, 'time': 100 + seq,
            'abs_time': 43200 + seq, 'date': {'year': 2026, 'month': 11, 'day': 12},
            'carriers': [carrier('71', 'Roosevelt', 127500000),
                         carrier('72', 'Lincoln', 128500000, x=100000)],
            'players': [player('100', 'Viper 1-1', 'Bob', '101'),
                        player('101', 'Viper 1-2', 'Ann', '102'),
                        player('102', 'Dodge 1-1', 'Unregistered', '103')],
            'aircraft': []}


class CarrierIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.service = CarrierService(reserved_channels=('2:251000000:0', '2:252000000:0'))
        self.service.exchange(snapshot())
        self.world = World()
        self.brain = Brain(self.world, roles=('carrier',), carriers=self.service)

    def call(self, callsign='Viper one one', ship_id='71', channel=RADIO_ONE, words='checking in'):
        return self.brain.respond({'text': f'Marshal {callsign} {words}', 'coalition': 2,
                                   'channel': channel, 'carrier_id': ship_id})

    def test_mission_carrier_registration_does_not_require_tacview(self):
        self.assertFalse(self.world.ready)
        answer = self.call()
        self.assertIsNotNone(answer['reply'])
        watches = self.service.exchange(snapshot(2))['watch']
        self.assertEqual([(entry['unit_id'], entry['carrier_id']) for entry in watches], [('100', '71')])

    def test_each_radio_enrolls_only_its_explicitly_checked_in_human(self):
        self.call()
        self.call('Viper one two', '72', RADIO_TWO)
        watches = self.service.exchange(snapshot(2))['watch']
        self.assertEqual({(entry['unit_id'], entry['carrier_id']) for entry in watches},
                         {('100', '71'), ('101', '72')})
        self.assertNotIn('102', {entry['unit_id'] for entry in watches})

    def test_wrong_carrier_frequency_does_not_enroll_or_answer(self):
        answer = self.call(ship_id='71', channel=RADIO_TWO)
        self.assertIsNone(answer['reply'])
        self.assertEqual(self.service.exchange(snapshot(2))['watch'], [])

    def test_reassignment_invalidates_old_instruction_and_releases_old_carrier(self):
        first = self.call()
        validation = first['validation']
        self.assertTrue(self.brain.validate_event(validation)['valid'])
        self.call(ship_id='72', channel=RADIO_TWO)
        self.assertFalse(self.brain.validate_event(validation)['valid'])
        watches = self.service.exchange(snapshot(2))['watch']
        self.assertEqual([(entry['unit_id'], entry['carrier_id']) for entry in watches], [('100', '72')])

    def test_new_mission_cannot_reuse_old_aircraft_call_validation(self):
        validation = self.call()['validation']
        self.service.exchange(snapshot(1, epoch='mission-two'))
        self.assertFalse(self.brain.validate_event(validation)['valid'])
        self.assertEqual(self.service.exchange(snapshot(2, epoch='mission-two'))['watch'], [])

    def test_overlapping_mission_radios_disable_conflict_without_reassigning_frequencies(self):
        bad = copy.deepcopy(snapshot(2))
        bad['carriers'][1]['frequency_hz'] = 127500050
        self.service.exchange(bad)
        registry = self.service.registry()
        self.assertTrue(registry['errors'])
        self.assertEqual({item['frequency_hz'] for item in registry['carriers']}, {127500000, 127500050})
        self.assertTrue(all(not item['enabled'] for item in registry['carriers']))
        self.assertIsNone(self.call()['reply'])

    def test_ordinary_gateway_cannot_mutate_or_drain_carrier_sessions(self):
        first = self.call()
        ordinary = {'text': 'Marshal Viper one one checking out', 'coalition': 2,
                    'channel': RADIO_ONE}
        self.assertIsNone(self.brain.respond(ordinary)['reply'])
        self.assertTrue(self.brain.validate_event(first['validation'])['valid'])
        for seq in (2, 3, 4):
            self.service.exchange(snapshot(seq))
        self.assertEqual(self.brain.take_events(ordinary)['events'], [])
        carrier_events = self.brain.take_events(dict(ordinary, carrier_id='71'))['events']
        self.assertTrue(any(event['unit_id'] == '100' for event in carrier_events))

    def test_emergency_reason_does_not_cancel_an_enrolled_aircraft(self):
        self.call()
        for seq, words in enumerate(('mayday, gear not down', 'emergency, aborting takeoff'), 2):
            answer = self.call(words=words)
            self.assertEqual(answer['parsed']['intent'], 'emergency')
            watches = self.service.exchange(snapshot(seq))['watch']
            self.assertEqual([(entry['unit_id'], entry['carrier_id']) for entry in watches], [('100', '71')])
        self.assertEqual(self.call(words='not emergency')['parsed']['intent'], 'None')
        self.assertEqual(self.call(words='cancel emergency')['parsed']['intent'], 'cancel')
        self.assertEqual(self.service.exchange(snapshot(4))['watch'], [])


if __name__ == '__main__':
    unittest.main()
