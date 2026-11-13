"""Native airfield radio/controller integration, with synthetic mission facts."""
import copy
import json
import unittest

from overlord.airfield import AirfieldService
from overlord.brain import Brain
from overlord.carrier import CarrierService
from overlord.tacview import World
from overlord.http import json_response
from tests.test_carrier_integration import snapshot as carrier_snapshot

RADIO_ONE = '2:121000000:0'
RADIO_TWO = '2:122000000:0'


def airfield(fid='1', name='Ironwood', frequency=121000000, x=0):
    return {'id': fid, 'name': name, 'coalition': 2, 'alive': True,
            'point': {'x': x, 'y': 100, 'z': 0}, 'lat': 43.0, 'lon': 41.0,
            'wind_from_deg': 90, 'wind_speed_mps': 3, 'altimeter_inhg': 29.92,
            'radios': [{'frequency_hz': frequency, 'modulation': 0,
                        'roles': ['ground', 'tower', 'approach'], 'aliases': [name]}],
            'runways': [{'id': 'strip-one', 'name': '09', 'opposite_name': '27',
                         'point': {'x': x, 'y': 100, 'z': 0}, 'heading_deg': 90,
                         'length_m': 2400, 'width_m': 60, 'closed': False}]}


def player(uid, callsign, name):
    return {'unit_id': uid, 'unit_name': 'Plane-' + uid, 'player_name': name,
            'callsign': callsign, 'modex': str(int(uid) + 1), 'coalition': 2,
            'incarnation': uid + '-birth1', 'human': True}


def snapshot(seq=1, *, shared=True, epoch='airfield-mission'):
    return {'version': 1, 'epoch': epoch, 'seq': seq, 'time': 100 + seq,
            'abs_time': 43200 + seq, 'date': {'year': 2026, 'month': 11, 'day': 13},
            'airfields': [airfield(), airfield('2', 'Maple', 121000000 if shared else 122000000, 100000)],
            'players': [player('100', 'Viper 1-1', 'Bob'), player('101', 'Viper 1-2', 'Ann'),
                        player('102', 'Dodge 1-1', 'Unregistered')], 'aircraft': []}


def track(uid='100', fid='1', *, x=0, in_air=False):
    return {'unit_id': uid, 'incarnation': uid + '-birth1', 'airfield_id': fid,
            'point': {'x': x, 'y': 100 if not in_air else 500, 'z': -1350},
            'lat': 43.0, 'lon': 41.0, 'velocity': {'x': 0, 'y': 0, 'z': 0},
            'heading_deg': 90, 'in_air': in_air,
            'ground_airfield_id': None if in_air else fid, 'events': []}


class AirfieldIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.service = AirfieldService(reserved_channels=('2:251000000:0', '2:252000000:0'))
        self.service.exchange(snapshot())
        self.brain = Brain(World(), roles=('airfield',), airfields=self.service)

    def call(self, field='Ironwood', who='Viper one one', words='request startup', channel=RADIO_ONE):
        return self.brain.respond({'airfield': True, 'text': f'{field} ground {who} {words}',
                                   'coalition': 2, 'channel': channel})

    def test_control_reply_survives_http_json_boundary_with_valid_authority(self):
        status, content_type, body = json_response(self.call())
        answer = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(content_type, 'application/json')
        self.assertIn('Ironwood ground', answer['reply'])
        self.assertTrue(self.brain.validate_event(answer['validation'])['valid'])
        self.assertEqual([(w['unit_id'], w['airfield_id'])
                          for w in self.service.exchange(snapshot(2))['watch']], [('100', '1')])

    def test_shared_frequency_keeps_each_airfields_enrolled_humans_separate(self):
        self.call()
        self.call('Maple', 'Viper one two')
        watch = self.service.exchange(snapshot(2))['watch']
        self.assertEqual({(w['unit_id'], w['airfield_id']) for w in watch}, {('100', '1'), ('101', '2')})
        self.assertNotIn('102', {w['unit_id'] for w in watch})

    def test_ambiguous_initial_call_on_shared_frequency_cannot_enroll(self):
        answer = self.brain.respond({'airfield': True, 'text': 'Tower Viper one one checking in',
                                    'coalition': 2, 'channel': RADIO_ONE})
        self.assertTrue(answer['reply'])
        self.assertEqual(self.service.exchange(snapshot(2))['watch'], [])

    def test_other_airfields_frequency_cannot_enroll_a_named_aircraft(self):
        self.service.exchange(snapshot(2, shared=False))
        self.assertIsNone(self.call(channel=RADIO_TWO)['reply'])
        self.assertEqual(self.service.exchange(snapshot(3, shared=False))['watch'], [])

    def test_field_reassignment_revokes_previous_radio_calls(self):
        first = self.call()
        self.assertTrue(self.brain.validate_event(first['validation'])['valid'])
        self.call('Maple')
        self.assertFalse(self.brain.validate_event(first['validation'])['valid'])
        watch = self.service.exchange(snapshot(2))['watch']
        self.assertEqual([(w['unit_id'], w['airfield_id']) for w in watch], [('100', '2')])

    def test_airfield_capture_revokes_only_the_affected_session(self):
        first = self.call()
        second = self.call('Maple', 'Viper one two')
        captured = snapshot(2)
        captured['airfields'][0]['coalition'] = 1
        self.service.exchange(captured)
        self.assertFalse(self.brain.validate_event(first['validation'])['valid'])
        self.assertTrue(self.brain.validate_event(second['validation'])['valid'])

    def test_ordinary_radio_cannot_implicitly_enroll_airfield_traffic(self):
        ordinary = {'text': 'Ironwood ground Viper one one request startup',
                    'coalition': 2, 'channel': RADIO_ONE}
        self.assertIsNone(self.brain.respond(ordinary)['reply'])
        self.assertEqual(self.service.exchange(snapshot(2))['watch'], [])

    def test_airfield_enrollment_cancels_previous_carrier_authority(self):
        carriers = CarrierService()
        carriers.exchange(carrier_snapshot())
        brain = Brain(World(), roles=('carrier', 'airfield'), carriers=carriers, airfields=self.service)
        old = brain.respond({'carrier_id': '71', 'text': 'Marshal Viper one one checking in',
                             'coalition': 2, 'channel': '2:127500000:0'})
        self.assertTrue(brain.validate_event(old['validation'])['valid'])
        brain.respond({'airfield': True, 'text': 'Ironwood ground Viper one one request startup',
                       'coalition': 2, 'channel': RADIO_ONE})
        self.assertFalse(brain.validate_event(old['validation'])['valid'])
        self.assertEqual(carriers.exchange(carrier_snapshot(2))['watch'], [])

    def test_native_radio_change_revokes_old_instruction_without_remapping(self):
        old = self.call()
        changed = copy.deepcopy(snapshot(2))
        changed['airfields'][0]['radios'][0]['frequency_hz'] = 123000000
        self.service.exchange(changed)
        self.assertFalse(self.brain.validate_event(old['validation'])['valid'])
        self.assertIsNone(self.call()['reply'])
        self.assertTrue(self.call(channel='2:123000000:0')['reply'])

    def test_carrier_enrollment_revokes_previous_airfield_authority(self):
        carriers = CarrierService()
        carriers.exchange(carrier_snapshot())
        self.brain = Brain(World(), roles=('carrier', 'airfield'), carriers=carriers, airfields=self.service)
        old = self.call()
        self.assertTrue(self.brain.validate_event(old['validation'])['valid'])
        self.brain.respond({'carrier_id': '71', 'text': 'Marshal Viper one one checking in',
                            'coalition': 2, 'channel': '2:127500000:0'})
        self.assertFalse(self.brain.validate_event(old['validation'])['valid'])
        self.assertEqual(self.service.exchange(snapshot(2))['watch'], [])

    def test_new_mission_in_one_feature_fences_the_other_features_old_calls(self):
        carriers = CarrierService()
        self.brain = Brain(World(), roles=('carrier', 'airfield'), carriers=carriers, airfields=self.service)
        self.brain.exchange_native('airfield', snapshot(2, epoch='common'))
        self.brain.exchange_native('carrier', carrier_snapshot(epoch='common'))
        old = self.call()['validation']
        self.assertTrue(self.brain.validate_event(old)['valid'])
        self.brain.exchange_native('carrier', carrier_snapshot(epoch='next-mission'))
        self.assertFalse(self.brain.validate_event(old)['valid'])
        self.assertFalse(self.brain.native_registry('airfield')['ready'])
        delayed = self.brain.exchange_native('airfield', snapshot(3, epoch='common'))
        self.assertFalse(delayed['accepted'])
        self.assertTrue(self.brain.exchange_native('airfield', snapshot(epoch='next-mission'))['accepted'])
        self.assertTrue(self.brain.native_registry('airfield')['ready'])

    def test_new_carrier_radio_revokes_only_conflicting_airfield_calls(self):
        carriers = CarrierService()
        self.brain = Brain(World(), roles=('carrier', 'airfield'), carriers=carriers, airfields=self.service)
        self.brain.exchange_native('airfield', snapshot(2, shared=False, epoch='common'))
        self.brain.exchange_native('carrier', carrier_snapshot(epoch='common'))
        first = self.call()['validation']
        second = self.call('Maple', 'Viper one two', channel=RADIO_TWO)['validation']
        moved = carrier_snapshot(2, epoch='common')
        moved['carriers'][0]['frequency_hz'] = 121000000
        self.brain.exchange_native('carrier', moved)
        self.assertFalse(self.brain.validate_event(first)['valid'])
        self.assertTrue(self.brain.validate_event(second)['valid'])


if __name__ == '__main__':
    unittest.main()
