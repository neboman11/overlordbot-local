import copy
import datetime
import math
import unittest
from unittest.mock import patch

from overlord import carrier, domain
from overlord.carrier import CarrierService

RADIO_A, RADIO_B = '2:127500000:0', '2:128500000:0'
AWACS = '2:251000000:0'
DATE = {'year': 2026, 'month': 11, 'day': 12}
WHEN = datetime.datetime(2026, 11, 12, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=43200)
DIGITS = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine')


def words(text):
    return ' '.join(DIGITS[int(c)] for c in text)


def bearing(value):
    return words(f'{(value % 360) or 360:03d}')


def magnetic(true_bearing, lat=43.0, lon=41.0):
    return round(domain.true_to_magnetic(lat, lon, true_bearing, WHEN)) % 360


def ship(cid='71', name='CVN-71 Theodore Roosevelt', frequency=127500000, **changes):
    value = {'id': cid, 'name': name, 'type': 'CVN_71', 'coalition': 2, 'alive': True,
             'frequency_hz': frequency, 'modulation': 0, 'point': {'x': 0, 'y': 0, 'z': 0},
             'velocity': {'x': 10, 'y': 0, 'z': 0}, 'heading_deg': 0, 'lat': 43.0, 'lon': 41.0,
             'recovery_case': 1, 'altimeter_inhg': 29.92, 'ceiling_ft': 5000, 'visibility_nm': 10,
             'deck_angle_deg': 9, 'deck_height_m': 20}
    value.update(changes)
    return value


def human(uid, callsign, name, modex, incarnation=None, is_human=True, coalition=2):
    return {'unit_id': uid, 'unit_name': 'Aircraft-' + uid, 'player_name': name, 'callsign': callsign,
            'modex': modex, 'coalition': coalition, 'incarnation': incarnation or uid + '-1', 'human': is_human}


def astern(nm, feet, final_true=351.0):
    angle = math.radians(final_true + 180)
    return (math.cos(angle) * nm * 1852, feet / 3.28084, math.sin(angle) * nm * 1852)


OVERHEAD_TWO = (0, 2000 / 3.28084, 0)
OVERHEAD_THREE = (0, 3000 / 3.28084, 0)
BALL = astern(1, 450)
ON_DECK = (0, 20, 0)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CarrierTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(carrier.time, 'monotonic', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.svc = CarrierService(reserved_channels=(AWACS, '2:252000000:0'), stale_seconds=10)
        self.seq, self.model, self.event_id = 0, 100.0, 0
        self.epoch = 'e1'
        self.ships = [ship(), ship('72', 'CVN-72 Abraham Lincoln', 128500000, point={'x': 200000, 'y': 0, 'z': 0})]
        self.players = [human('100', 'Viper 1-1', 'Bob', '101'), human('101', 'Viper 1-2', 'Ann', '102'),
                        human('102', 'Dodge 1-1', 'Carl', '103')]

    # -- helpers ---------------------------------------------------------------------

    def payload(self, aircraft=(), ships=None, players=None, seq=None, epoch=None):
        if epoch is not None:
            self.epoch = epoch
        self.seq = self.seq + 1 if seq is None else seq
        self.model += 1
        return {'version': 1, 'epoch': self.epoch, 'seq': self.seq, 'time': self.model, 'abs_time': 43200,
                'date': dict(DATE), 'carriers': copy.deepcopy(self.ships if ships is None else ships),
                'players': copy.deepcopy(self.players if players is None else players),
                'aircraft': list(aircraft)}

    def bridge(self, aircraft=(), **kwargs):
        response = self.svc.exchange(self.payload(aircraft, **kwargs))
        self.assertTrue(response['accepted'], response)
        return response

    def watched(self, response):
        return {(entry['unit_id'], entry['carrier_id']) for entry in response['watch']}

    def event(self, kind, place='71'):
        self.event_id += 1
        return {'id': self.event_id, 'type': kind, 'carrier_id': place, 'time': self.model}

    def track(self, uid='100', point=OVERHEAD_TWO, in_air=True, on_deck=False, events=(), place='71',
              incarnation=None):
        return {'unit_id': uid, 'incarnation': incarnation or uid + '-1', 'carrier_id': place,
                'point': {'x': point[0], 'y': point[1], 'z': point[2]}, 'velocity': {'x': 60, 'y': 0, 'z': 0},
                'heading_deg': 351, 'in_air': in_air, 'on_deck': on_deck, 'events': list(events)}

    def say(self, text, channel=RADIO_A, carrier_id='71', sender=None):
        request = {'text': text, 'coalition': 2, 'channel': channel}
        if carrier_id is not None:
            request['carrier_id'] = carrier_id
        if sender is not None:
            request['sender_callsign'] = sender
        answer = self.svc.respond(request)
        if answer['reply'] is not None:
            self.assertLessEqual(len(answer['reply']), carrier.MAX_REPLY_CHARS)
        return answer

    def events(self, channel=RADIO_A, carrier_id='71'):
        return [event['reply'] for event in self.svc.take_events(
            {'coalition': 2, 'channel': channel, 'carrier_id': carrier_id})['events']]

    def case_one_marshal(self, who='Viper 1 1', angels='two'):
        self.assertIn(f'angels {angels}', self.say(f'Marshal, {who}, checking in')['reply'])
        self.assertIn('readback correct',
                      self.say(f'Marshal, {who}, readback angels {angels}, altimeter two niner niner two')['reply'])

    def case_one_established(self):
        self.case_one_marshal()
        self.bridge([self.track()])
        return self.say('Marshal, Viper 1 1, established')

    def case_one_to_ball(self):
        self.bridge()
        self.assertIn('Charlie', self.case_one_established()['reply'])
        self.assertIn('report initial', self.say('Marshal, Viper 1 1, commencing')['reply'])
        self.bridge([self.track(point=(-5000, 250, 0))])
        self.assertIn('report the break', self.say('Tower, Viper 1 1, initial')['reply'])
        self.assertIn('report abeam', self.say('Tower, Viper 1 1, break')['reply'])
        self.assertIn('call the ball', self.say('Tower, Viper 1 1, abeam')['reply'])
        self.bridge([self.track(point=BALL)])
        answer = self.say('Paddles, Viper 1 1, Hornet ball, five point six')
        self.assertIn('roger ball', answer['reply'])
        return answer

    # -- registry, radios, routing ---------------------------------------------------

    def test_registry_preserves_mission_radios_and_disables_every_clash_explicitly(self):
        ships = [ship('71', frequency=127500000), ship('72', 'Lincoln', 127500200),
                 ship('73', 'Vinson', 127500399), ship('74', 'Stennis', 251000150),
                 ship('75', 'Kuznetsov', 130000000, coalition=1), ship('76', 'Tarawa', None),
                 ship('77', 'Forrestal', 140000000, modulation=1)]
        self.bridge(ships=ships)
        registry = {entry['id']: entry for entry in self.svc.registry()['carriers']}
        self.assertTrue(registry['71']['enabled'])                 # exactly 200 Hz apart is allowed
        self.assertFalse(registry['72']['enabled'])                # 199 Hz from 73
        self.assertFalse(registry['73']['enabled'])
        self.assertIn('73', registry['72']['error'])
        self.assertFalse(registry['74']['enabled'])                # never steals the AWACS radio
        self.assertIn(AWACS, registry['74']['error'])
        self.assertTrue(registry['75']['enabled'])
        self.assertFalse(registry['76']['enabled'])
        self.assertIsNone(registry['76']['channel'])
        self.assertEqual(registry['77']['channel'], '2:140000000:1')
        self.assertEqual(len(self.svc.registry()['errors']), 4)
        self.assertIsNone(self.say('Marshal, Viper 1 1, checking in', channel='2:127500200:0',
                                   carrier_id='72')['reply'])
        with self.assertRaises(ValueError):
            CarrierService(reserved_channels=('blue-251',))

    def test_frequency_windows_apply_across_coalitions_and_modulations(self):
        ships = [ship('81', 'Alpha', 150000000), ship('82', 'Bravo', 150000200, coalition=1, modulation=1),
                 ship('83', 'Charlie', 160000000, coalition=1), ship('84', 'Delta', 160000199, modulation=1),
                 ship('85', 'Echo', 251000199, coalition=1, modulation=1),
                 ship('86', 'Foxtrot', 252000200, coalition=1, modulation=1)]
        self.bridge(ships=ships)
        registry = {entry['id']: entry for entry in self.svc.registry()['carriers']}
        self.assertEqual({k: v['frequency_hz'] for k, v in registry.items()},
                         {'81': 150000000, '82': 150000200, '83': 160000000, '84': 160000199,
                          '85': 251000199, '86': 252000200})
        self.assertEqual({k for k, v in registry.items() if v['enabled']}, {'81', '82', '86'})
        self.assertIn('84', registry['83']['error'])
        self.assertIn('83', registry['84']['error'])
        self.assertIn(AWACS, registry['85']['error'])

    def test_ready_is_a_fresh_snapshot_and_stale_registry_keeps_radio_tombstones(self):
        self.assertFalse(self.svc.ready)
        self.assertEqual(self.svc.registry(), {'ready': False, 'epoch': None, 'carriers': [], 'errors': []})
        self.bridge(ships=[])
        self.assertTrue(self.svc.ready)
        self.assertEqual(self.svc.registry()['carriers'], [])
        self.bridge()
        self.clock.now += 11
        registry = self.svc.registry()
        self.assertFalse(registry['ready'])
        self.assertEqual([entry['channel'] for entry in registry['carriers']], [RADIO_A, RADIO_B])
        self.assertIn('no live carrier link', self.say('Marshal, Viper 1 1, checking in')['reply'])
        self.assertEqual(self.bridge()['watch'], [])

    def test_carrier_id_must_own_channel_and_unaddressed_or_ambiguous_radio_is_ignored(self):
        self.bridge()
        self.assertIsNone(self.say('Marshal, Viper 1 1, checking in', channel=RADIO_B)['reply'])
        self.assertIsNone(self.say('Marshal, Viper 1 1, checking in', channel=RADIO_A, carrier_id='72')['reply'])
        self.assertIsNone(self.say('Overlord, Viper 1 1, checking in')['reply'])
        self.assertIsNone(self.svc.respond({'text': 'Marshal, Viper 1 1, checking in', 'coalition': 1,
                                            'channel': '1:127500000:0'})['reply'])
        self.assertEqual(self.bridge()['watch'], [])
        answer = self.say('Roosevelt marshal, Viper 1 1, checking in', carrier_id=None)
        self.assertTrue(answer['reply'].startswith('viper 1 1, Roosevelt marshal, CASE one recovery'))

    # -- membership --------------------------------------------------------------------

    def test_only_explicit_unique_human_callers_enrol_with_a_first_watch_handshake(self):
        self.players.append(human('900', 'Enfield 1-1', 'AI', '900', is_human=False))
        response = self.bridge([self.track('102', place='71')])      # unrequested report is ignored
        self.assertEqual(response['watch'], [])
        self.assertIn('no player aircraft', self.say('Marshal, Enfield 1 1, checking in')['reply'])
        answer = self.say('Marshal, Viper 1 1, checking in')
        self.assertIn('CASE one recovery', answer['reply'])
        self.assertTrue(self.svc.validate_reply(answer['validation']))
        first = self.bridge()
        self.assertEqual(self.watched(first), {('100', '71')})
        self.assertEqual(first['watch'][0]['incarnation'], '100-1')
        self.assertEqual(self.watched(self.bridge()), {('100', '71')})   # the response may still be in flight
        self.assertTrue(self.svc.validate_reply(answer['validation']))
        self.assertEqual(self.bridge()['watch'], [])                     # second miss: tracking lost
        self.assertIn('radar contact lost', self.events()[0])
        self.assertFalse(self.svc.validate_reply(answer['validation']))

    def test_reported_watch_keeps_session_until_omitted_from_a_later_snapshot(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        self.bridge()
        self.bridge([self.track()])
        self.assertEqual(self.watched(self.bridge([self.track()])), {('100', '71')})
        self.assertEqual(self.bridge()['watch'], [])

    def test_same_callsign_on_two_decks_requires_modex_and_leading_zeros_count(self):
        self.players = [human('100', 'Viper 1-1', 'Bob', '201'), human('200', 'Viper 1-1', 'Bob', '007'),
                        human('300', 'Hornet 3-1', 'Eve', '7'), human('400', 'Hornet 4-1', 'Fay', '201')]
        self.bridge()
        answer = self.say('Marshal, Viper 1 1, checking in')
        self.assertIn('more than one aircraft matches, say modex', answer['reply'])
        self.assertIn('more than one aircraft matches, say callsign', self.say('Marshal, 201, checking in')['reply'])
        self.assertEqual(self.bridge()['watch'], [])
        self.assertIn('zero zero seven, Roosevelt marshal, CASE one',
                      self.say('Marshal, zero zero seven, checking in')['reply'])
        self.say('Marshal, Hornet 4 1, checking in', channel=RADIO_B, carrier_id='72')
        self.assertEqual(self.watched(self.bridge()), {('200', '71'), ('400', '72')})

    def test_srs_radio_name_must_positively_identify_the_caller(self):
        self.players[2] = human('102', 'Dodge 1-1', 'Bob', '103')          # a second human named Bob
        self.bridge()
        for sender in ('', '   ', 'SRS client', 'Bob', 'Ann', 'Viper 1-2 | Ann', 'Viper 1-1 | Ann',
                       'Viper 1-2 | Bob'):
            answer = self.say('Marshal, Viper 1 1, checking in', sender=sender)
            self.assertIn('your radio name does not identify you', answer['reply'], sender)
        self.assertEqual(self.bridge()['watch'], [])
        self.assertIn('CASE one', self.say('Marshal, Viper 1 1, checking in', sender='Viper 1-1 | Bob')['reply'])
        self.assertIn('CASE one', self.say('Marshal, Viper 1 2, checking in', sender='Ann')['reply'])
        # Blank, generic or contradictory radio names cannot cancel, migrate or prioritise a session.
        for text, channel, carrier_id in (('checking out', RADIO_A, '71'), ('cancel recovery', RADIO_A, '71'),
                                          ('mayday', RADIO_A, '71'), ('checking in', RADIO_B, '72')):
            for sender in ('', 'SRS client', 'Viper 1-2 | Ann'):
                answer = self.say(f'Marshal, Viper 1 1, {text}', channel=channel, carrier_id=carrier_id,
                                  sender=sender)
                self.assertIn('does not identify you', answer['reply'])
        self.assertEqual(self.watched(self.bridge()), {('100', '71'), ('101', '71')})
        self.assertIn('hold overhead at angels two',
                      self.say('Marshal, Viper 1 1, say again', sender='viper11')['reply'])

    def test_check_in_on_other_carrier_releases_old_assignment_and_calls(self):
        self.bridge()
        old = self.say('Marshal, Viper 1 1, checking in')['validation']
        self.assertIn('unable, you are checked in with Roosevelt',
                      self.say('Marshal, Viper 1 1, say again', channel=RADIO_B, carrier_id='72')['reply'])
        self.assertTrue(self.svc.validate_reply(old))
        new = self.say('Marshal, Viper 1 1, checking in', channel=RADIO_B, carrier_id='72')
        self.assertFalse(self.svc.validate_reply(old))
        self.assertTrue(self.svc.validate_reply(new['validation']))
        self.assertEqual(self.watched(self.bridge()), {('100', '72')})
        self.assertIn('checked in with Lincoln', self.say('Marshal, Viper 1 1, established')['reply'])

    def test_death_disconnect_and_respawn_end_sessions_once(self):
        self.bridge()
        for who in ('Viper 1 1', 'Viper 1 2', 'Dodge 1 1'):
            self.say(f'Marshal, {who}, checking in')
        self.bridge()
        response = self.bridge([self.track('100', events=[self.event('dead')]),
                                self.track('101', events=[self.event('disconnect')]), self.track('102')])
        self.assertEqual(self.watched(response), {('102', '71')})
        self.players[2] = human('102', 'Dodge 1-1', 'Carl', '103', incarnation='102-2')
        self.assertEqual(self.bridge([self.track('102')])['watch'], [])       # respawned slot is new
        self.assertEqual(self.events(), [])
        answer = self.say('Marshal, Dodge 1 1, checking in')
        self.assertIn('angels two', answer['reply'])                           # freed slot reused
        self.assertEqual(self.bridge()['watch'][0]['incarnation'], '102-2')

    # -- CASE I ---------------------------------------------------------------------

    def test_case_one_recovery_trap_needs_confirmed_deck_and_releases_on_deck_clear(self):
        self.bridge()
        reply = self.say('Marshal, Viper 1 1, checking in')['reply']
        self.assertEqual(reply, f'viper 1 1, Roosevelt marshal, CASE one recovery, BRC {bearing(magnetic(0))}, '
                                'hold overhead at angels two, altimeter two nine nine two, '
                                'read back angels and altimeter')
        self.assertNotEqual(magnetic(0), 0)       # spoken BRC is magnetic, not the true heading
        self.assertIn('negative readback, angels two',
                      self.say('Marshal, Viper 1 1, readback angels three, altimeter two niner niner two')['reply'])
        self.assertIn('unable, check in first', self.say('Marshal, Viper 1 2, established')['reply'])
        self.assertIn('readback correct',
                      self.say('Marshal, Viper 1 1, readback angels two altimeter two nine nine two')['reply'])
        self.assertIn('no radar contact', self.say('Marshal, Viper 1 1, established')['reply'])
        self.bridge([self.track(point=(0, 5000 / 3.28084, 0))])
        self.assertIn('not observed in holding', self.say('Marshal, Viper 1 1, established')['reply'])
        self.assertIn('unable', self.say('Tower, Viper 1 1, initial')['reply'])
        self.bridge([self.track()])
        established = self.say('Marshal, Viper 1 1, established')['reply']
        self.assertIn(f'roger established, number one, Charlie, BRC {bearing(magnetic(0))}', established)
        self.assertIn('report initial', self.say('Marshal, Viper 1 1, commencing')['reply'])
        self.assertIn('unable', self.say('Paddles, Viper 1 1, ball')['reply'])     # skipped the pattern
        self.bridge([self.track(point=(-5000, 250, 0))])
        self.say('Tower, Viper 1 1, initial')
        self.say('Tower, Viper 1 1, break')
        self.say('Tower, Viper 1 1, abeam')
        self.bridge([self.track(point=astern(1, 450, final_true=90))])
        self.assertIn('position not confirmed', self.say('Paddles, Viper 1 1, ball')['reply'])
        self.bridge([self.track(point=BALL)])
        self.assertIn('roger ball', self.say('Paddles, Viper 1 1, ball')['reply'])
        self.bridge([self.track(point=ON_DECK, in_air=False, events=[self.event('land')])])
        self.assertEqual(self.events(), [])                                 # contact alone is no trap
        self.assertIn('stand by for deck confirmation', self.say('Tower, Viper 1 1, trapped')['reply'])
        self.assertIn('no trap recorded', self.say('Tower, Viper 1 1, deck clear')['reply'])
        self.bridge([self.track(point=ON_DECK, in_air=False, on_deck=True)])
        self.assertEqual(self.events(), ['viper 1 1, Roosevelt tower, trap confirmed, clear the landing area, '
                                         'report deck clear'])
        self.assertIn('recovery complete', self.say('Tower, Viper 1 1, clear of the landing area')['reply'])
        self.assertEqual(self.bridge()['watch'], [])

    def test_touch_and_go_is_a_bolter_that_keeps_the_lane_and_revokes_the_ball(self):
        ball = self.case_one_to_ball()['validation']
        self.case_one_marshal('Viper 1 2', 'three')
        self.bridge([self.track(point=BALL, events=[self.event('land'), self.event('takeoff')]),
                     self.track('101', point=OVERHEAD_THREE)])
        self.assertEqual(self.events(), ['viper 1 1, Roosevelt paddles, bolter, bolter, climb straight ahead, '
                                         'rejoin the pattern, report abeam'])
        self.assertFalse(self.svc.validate_reply(ball))
        established = self.say('Marshal, Viper 1 2, established')['reply']
        self.assertTrue(established.endswith('roger established, number one'))     # no Charlie: lane kept
        self.assertIn('negative, continue holding, number one', self.say('Marshal, Viper 1 2, commencing')['reply'])
        self.assertIn('call the ball', self.say('Tower, Viper 1 1, abeam')['reply'])
        self.bridge([self.track(point=BALL), self.track('101', point=OVERHEAD_THREE)])
        self.say('Paddles, Viper 1 1, ball')
        self.bridge([self.track(point=ON_DECK, in_air=False, events=[self.event('land')]),
                     self.track('101', point=OVERHEAD_THREE)])
        self.assertIn('roger, stand by for deck status', self.say('Paddles, Viper 1 1, bolter')['reply'])
        self.assertEqual(self.events(), [])
        self.bridge([self.track(point=BALL), self.track('101', point=OVERHEAD_THREE)])     # airborne again
        self.assertIn('bolter, bolter', self.events()[0])

    def test_wave_off_beats_positive_words_and_negation_never_advances(self):
        self.case_one_to_ball()
        self.assertIn('say again', self.say('Tower, Viper 1 1, negative abeam')['reply'])
        self.assertIn('roger wave off, climb straight ahead, rejoin the pattern',
                      self.say('Paddles, Viper 1 1, ball, wave off')['reply'])
        self.assertIn('unable, no approach in progress', self.say('Paddles, Viper 1 1, waving off')['reply'])
        self.assertIn('roger abeam', self.say('Tower, Viper 1 1, abeam')['reply'])
        self.assertIn('roger clara', self.say('Paddles, Viper 1 1, clara')['reply'])
        self.assertIn('copy, recovery cancelled', self.say('Paddles, Viper 1 1, ball, cancel')['reply'])
        self.assertEqual(self.bridge()['watch'], [])

    def test_foul_deck_comes_only_from_enrolled_trapped_aircraft(self):
        self.bridge()
        self.case_one_established()
        self.case_one_marshal('Viper 1 2', 'three')
        self.say('Marshal, Viper 1 1, commencing')
        # An unenrolled aircraft parked on deck never fouls the deck.
        self.bridge([self.track(point=(-5000, 250, 0)), self.track('101'),
                     self.track('102', point=ON_DECK, in_air=False, on_deck=True)])
        for call in ('initial', 'break', 'abeam'):
            self.say(f'Tower, Viper 1 1, {call}')
        # Viper 1 2 lands without the lane: an enrolled deck obstruction.
        self.bridge([self.track(point=BALL), self.track('101', point=ON_DECK, in_air=False, on_deck=True,
                                                        events=[self.event('land')])])
        self.assertIn('trap confirmed', self.events()[0])
        self.assertIn('wave off, wave off, foul deck', self.say('Paddles, Viper 1 1, ball')['reply'])
        self.assertIn('copy deck clear', self.say('Tower, Viper 1 2, deck clear')['reply'])
        self.say('Tower, Viper 1 1, abeam')
        self.bridge([self.track(point=BALL)])
        self.assertIn('roger ball', self.say('Paddles, Viper 1 1, ball')['reply'])

    def test_emergency_gets_the_next_lane_ahead_of_earlier_traffic(self):
        self.case_one_to_ball()
        self.case_one_marshal('Viper 1 2', 'three')
        answer = self.say('Marshal, Dodge 1 1, mayday, low fuel')
        self.assertIn('copy emergency, you have priority, CASE one recovery', answer['reply'])
        self.assertIn('angels four', answer['reply'])
        self.say('Marshal, Dodge 1 1, readback angels four, altimeter two niner niner two')
        overhead_four = (0, 4000 / 3.28084, 0)
        self.bridge([self.track(point=BALL), self.track('101', point=OVERHEAD_THREE),
                     self.track('102', point=overhead_four)])
        self.assertIn('number two', self.say('Marshal, Viper 1 2, established')['reply'])
        self.assertIn('number one', self.say('Marshal, Dodge 1 1, established')['reply'])
        self.bridge([self.track(point=ON_DECK, in_air=False, on_deck=True), self.track('101', point=OVERHEAD_THREE),
                     self.track('102', point=overhead_four)])
        self.events()
        self.say('Tower, Viper 1 1, deck clear')
        self.assertEqual(self.events(), ['dodge 1 1, Roosevelt marshal, Charlie, BRC '
                                         f'{bearing(magnetic(0))}, report commencing'])

    # -- CASE II / III ----------------------------------------------------------------

    def case_three(self):
        self.ships[0]['recovery_case'] = 3
        self.bridge()
        final = magnetic(351)
        return final, (final + 180) % 360

    def test_case_three_marshal_strict_readback_eat_and_approach(self):
        final, radial = self.case_three()
        first = self.say('Marshal, Viper 1 1, checking in')['reply']
        self.assertIn(f'CASE three recovery, final bearing {bearing(final)}, marshal on the {bearing(radial)} '
                      'radial, two one DME, angels six, expected approach time zero five, '
                      'altimeter two nine nine two', first)
        second = self.say('Marshal, Viper 1 2, checking in')['reply']
        self.assertIn('two two DME, angels seven, expected approach time zero six', second)
        good = f'{bearing(radial)} radial, two one DME, angels six, approach time zero five'
        for spoken, field in ((f'{bearing(radial)} radial, two one zero DME, angels six, approach time zero five, '
                               'altimeter two niner niner two', 'two one DME'),
                              (good, 'altimeter two nine nine two'),
                              (f'minus {good}, altimeter two niner niner two', 'radial'),
                              (good + ', altimeter two nine nine two, angels six', 'altimeter'),
                              (good + ', altimeter two nine point nine', 'altimeter')):
            self.assertIn(f'negative readback, {field}' if field != 'radial' else 'negative readback',
                          self.say(f'Marshal, Viper 1 1, readback {spoken}')['reply'])
        self.assertIn('readback correct', self.say(
            f'Marshal, Viper 1 1, readback {good}, altimeter two nine point nine two')['reply'])
        fix = astern(21, 6000)
        self.bridge([self.track(point=fix)])
        self.assertEqual(self.say('Marshal, Viper 1 1, established')['reply'],
                         'viper 1 1, Roosevelt marshal, roger established, number one')
        self.assertIn('negative, continue holding', self.say('Marshal, Viper 1 1, commencing')['reply'])
        self.say('Marshal, Viper 1 2, readback '
                 f'{bearing(radial)} radial, two two DME, angels seven, approach time zero six, '
                 'altimeter two niner niner two')
        self.model += 300
        self.bridge([self.track(point=fix), self.track('101', point=astern(22, 7000))])
        self.say('Marshal, Viper 1 2, established')
        self.assertEqual(self.events(), [f'viper 1 1, Roosevelt marshal, commence, final bearing {bearing(final)}, '
                                         'report commencing'])
        self.model += 300
        self.bridge([self.track(point=fix), self.track('101', point=astern(22, 7000))])
        self.assertEqual(self.events(), [])            # one approach lane: Viper 1 2 keeps holding
        self.assertIn('report platform', self.say('Marshal, Viper 1 1, commencing')['reply'])
        self.assertIn('unable, report platform', self.say('Approach, Viper 1 1, ten miles')['reply'])
        self.assertIn('report ten miles', self.say('Approach, Viper 1 1, platform')['reply'])
        self.bridge([self.track(point=astern(10, 1200))])
        self.assertIn('call the ball', self.say('Approach, Viper 1 1, ten miles')['reply'])
        self.bridge([self.track(point=BALL)])
        ball = self.say('Paddles, Viper 1 1, ball')
        self.assertIn('roger ball', ball['reply'])
        self.bridge([self.track(point=BALL, events=[self.event('land'), self.event('takeoff')])])
        self.assertFalse(self.svc.validate_reply(ball['validation']))
        self.events()
        self.bridge([self.track(point=astern(10, 1200))])
        self.assertIn('call the ball', self.say('Approach, Viper 1 1, ten miles')['reply'])

    def test_case_two_flies_platform_then_visual_pattern(self):
        self.ships[0]['recovery_case'] = 2
        self.bridge()
        radial = (magnetic(351) + 180) % 360
        self.assertIn('CASE two recovery', self.say('Marshal, Viper 1 1, checking in')['reply'])
        self.say(f'Marshal, Viper 1 1, readback {bearing(radial)} radial, two one DME, angels six, '
                 'approach time zero five, altimeter two niner niner two')
        self.model += 400
        self.bridge([self.track(point=astern(21, 6000))])
        self.assertIn('commence', self.say('Marshal, Viper 1 1, established')['reply'])
        self.assertIn('report initial', self.say('Marshal, Viper 1 1, commencing')['reply'])
        self.assertIn('report initial', self.say('Approach, Viper 1 1, platform')['reply'])
        self.bridge([self.track(point=astern(3, 800))])
        self.assertIn('report the break', self.say('Tower, Viper 1 1, initial')['reply'])

    def test_missing_case_or_geometry_registers_and_assigns_only_when_facts_arrive(self):
        self.ships[0].update(recovery_case=None)
        self.ships[1].update(recovery_case=3, deck_angle_deg=None)
        self.bridge()
        self.assertIn('checked in, recovery case not available, stand by',
                      self.say('Marshal, Viper 1 1, checking in')['reply'])
        self.assertIn('deck geometry not available',
                      self.say('Marshal, Viper 1 2, checking in', channel=RADIO_B, carrier_id='72')['reply'])
        self.assertEqual(self.watched(self.bridge()), {('100', '71'), ('101', '72')})
        self.assertIn('unable, no marshal assignment', self.say('Marshal, Viper 1 1, established')['reply'])
        self.ships[0]['recovery_case'] = 1
        self.bridge([self.track('100'), self.track('101', place='72')])
        self.assertIn('CASE one recovery, BRC', self.events()[0])
        self.assertEqual(self.events(RADIO_B, '72'), [])

    def test_ship_turn_reissues_case_three_marshal_and_rerenders_case_one_brc(self):
        _, radial = self.case_three()
        old = self.say('Marshal, Viper 1 1, checking in')['validation']
        self.ships[0]['heading_deg'] = 30
        self.bridge()
        new_radial = (magnetic(21) + 180) % 360
        self.assertNotEqual(new_radial, radial)
        self.assertIn(f'new instructions, CASE three recovery, final bearing {bearing(magnetic(21))}, '
                      f'marshal on the {bearing(new_radial)} radial', self.events()[0])
        self.assertFalse(self.svc.validate_reply(old))
        stale = f'{bearing(radial)} radial, two one DME, angels six, approach time zero five, altimeter two niner niner two'
        self.assertIn('negative readback', self.say(f'Marshal, Viper 1 1, readback {stale}')['reply'])
        self.assertIn('readback correct', self.say(
            f'Marshal, Viper 1 1, readback {stale.replace(bearing(radial), bearing(new_radial))}')['reply'])
        self.ships[1]['heading_deg'] = 90
        self.bridge()
        self.say('Marshal, Viper 1 2, checking in', channel=RADIO_B, carrier_id='72')
        self.ships[1]['heading_deg'] = 180
        self.bridge()
        self.assertIn(f'BRC {bearing(magnetic(180))}',
                      self.say('Marshal, Viper 1 2, say again', channel=RADIO_B, carrier_id='72')['reply'])

    # -- departures ---------------------------------------------------------------

    def test_departure_queue_release_launch_and_handoff_follow_observed_state(self):
        self.bridge()
        self.assertEqual(self.say('Departure, Viper 1 1, request departure')['reply'],
                         'viper 1 1, Roosevelt departure, CASE one departure, number one for launch, hold')
        self.assertIn('report ready for launch', self.say('Departure, Viper 1 2, request startup')['reply'])
        self.assertIn('number two for launch', self.say('Departure, Viper 1 2, ready for launch')['reply'])
        deck = dict(point=ON_DECK, in_air=False, on_deck=True)
        self.bridge([self.track('100', **deck), self.track('101', **deck)])
        self.assertEqual(self.events(), [f'viper 1 1, Roosevelt departure, cleared for launch, CASE one departure, '
                                         f'BRC {bearing(magnetic(0))}, altimeter two nine nine two, report airborne'])
        self.assertIn('stand by for launch confirmation', self.say('Departure, Viper 1 2, airborne')['reply'])
        self.assertIn('launch not confirmed', self.say('Departure, Viper 1 2, departure complete')['reply'])
        self.bridge([self.track('100', point=(500, 100, 0), events=[self.event('takeoff')]),
                     self.track('101', **deck)])
        self.assertEqual(self.events(), ['viper 1 1, Roosevelt departure, airborne, CASE one departure, '
                                         'report departure complete',
                                         'viper 1 2, Roosevelt departure, cleared for launch, CASE one departure, '
                                         f'BRC {bearing(magnetic(0))}, altimeter two nine nine two, report airborne'])
        response = self.bridge([self.track('100', point=(11 * 1852, 3000, 0)), self.track('101', **deck)])
        self.assertEqual(self.watched(response), {('101', '71')})
        self.assertIn('departure complete, frequency change approved', self.events()[0])

    def test_launch_from_another_deck_or_unseen_deck_is_not_a_departure(self):
        self.bridge()
        self.say('Departure, Viper 1 1, request departure')
        self.say('Departure, Viper 1 2, request departure')
        self.bridge([self.track('100', point=ON_DECK, in_air=False, on_deck=True),
                     self.track('101', point=(300, 100, 0))])
        self.assertIn('unable departure, aircraft not on deck', ' '.join(self.events()))
        response = self.bridge([self.track('100', point=(300, 100, 0), events=[self.event('takeoff', '72')])])
        self.assertEqual(response['watch'], [])
        self.assertNotIn('airborne,', ' '.join(self.events()))

    def test_departures_wait_for_the_approach_lane(self):
        self.case_one_to_ball()
        self.say('Departure, Viper 1 2, request departure')
        deck = self.track('101', point=ON_DECK, in_air=False, on_deck=True)
        self.bridge([self.track(point=BALL), deck])
        self.assertEqual(self.events(), [])
        self.bridge([self.track(point=ON_DECK, in_air=False, on_deck=True), deck])
        self.events()
        self.say('Tower, Viper 1 1, deck clear')
        self.assertIn('cleared for launch', self.events()[0])

    # -- validation and bridge integrity ---------------------------------------------

    def test_queued_replies_are_revoked_by_later_transitions_and_cancellation(self):
        self.bridge()
        check_in = self.say('Marshal, Viper 1 1, checking in')['validation']
        repeat = self.say('Marshal, Viper 1 1, say again')
        self.assertIn('CASE one recovery', repeat['reply'])
        self.assertTrue(self.svc.validate_reply(check_in))           # say again changes nothing
        self.say('Marshal, Viper 1 1, readback angels two altimeter two nine nine two')
        self.assertFalse(self.svc.validate_reply(check_in))
        self.assertEqual(self.say('Marshal, Viper 1 1, say again')['reply'],
                         'viper 1 1, Roosevelt marshal, hold overhead at angels two, report established')
        status = self.say('Marshal, Viper 1 1, say again')['validation']
        self.assertTrue(self.svc.validate_reply(status))
        self.assertFalse(self.svc.validate_reply(dict(status, revision=status['revision'] + 1)))
        self.assertFalse(self.svc.validate_reply(dict(status, carrier_id='72', channel=RADIO_B)))
        self.assertFalse(self.svc.validate_reply(dict(status, role='jtac')))
        self.assertFalse(self.svc.validate_reply(dict(status, expires=0)))
        cancel = self.say('Marshal, Viper 1 1, checking out')
        self.assertEqual(cancel['reply'], 'viper 1 1, Roosevelt marshal, copy, checked out')
        self.assertFalse(self.svc.validate_reply(status))
        self.assertTrue(self.svc.validate_reply(cancel['validation']))
        self.say('Marshal, Viper 1 1, checking in')
        self.assertFalse(self.svc.validate_reply(cancel['validation']))

    def test_stale_link_suspends_control_but_allows_cancel_and_rejects_replays(self):
        self.bridge()
        answer = self.say('Marshal, Viper 1 1, checking in')
        replay = self.svc.exchange(self.payload(players=[], seq=1))
        self.assertEqual((replay['accepted'], replay['error'], replay['watch']), (False, 'stale sequence', []))
        self.assertTrue(self.svc.validate_reply(answer['validation']))
        self.clock.now += 11
        self.assertFalse(self.svc.validate_reply(answer['validation']))
        self.assertIn('no live carrier link', self.say('Marshal, Viper 1 1, readback angels two altimeter '
                                                       'two nine nine two')['reply'])
        cancelled = self.say('Marshal, Viper 1 1, cancel recovery')
        self.assertIn('copy, recovery cancelled', cancelled['reply'])
        self.assertTrue(self.svc.validate_reply(cancelled['validation']))
        self.assertEqual(self.bridge()['watch'], [])
        self.bridge(epoch='e2', seq=1)
        retired = self.svc.exchange(self.payload(epoch='e1', seq=99))
        self.assertEqual((retired['accepted'], retired['error']), (False, 'retired epoch'))
        self.assertFalse(self.svc.validate_reply(cancelled['validation']))

    def test_new_epoch_drops_traffic_and_old_validations(self):
        self.bridge()
        answer = self.say('Marshal, Viper 1 1, checking in')
        self.bridge(epoch='e2', seq=1)
        self.assertFalse(self.svc.validate_reply(answer['validation']))
        self.assertEqual(self.bridge()['watch'], [])

    def test_malformed_envelopes_are_rejected_before_any_change(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        before = self.svc.registry()
        bad = []
        for mutate in (lambda p: p['carriers'].append(dict(p['carriers'][0])),
                       lambda p: p['players'].append(dict(p['players'][0], modex='bad modex')),
                       lambda p: p['players'].extend([human(str(1000 + i), '', 'x', '') for i in range(300)]),
                       lambda p: p.update(date={'year': 2026, 'month': 2, 'day': 30}),
                       lambda p: p.update(abs_time=86400),
                       lambda p: p.update(seq=float('nan')),
                       lambda p: p.update(epoch='x' * 65)):
            payload = self.payload()
            self.seq -= 1
            mutate(payload)
            bad.append(payload)
        self.clock.now += 11
        for payload in bad:
            with self.assertRaises(ValueError):
                self.svc.exchange(payload)
        self.assertFalse(self.svc.ready)                 # a refused payload never renews freshness
        self.assertEqual(dict(self.svc.registry(), ready=True), before)
        empty = self.payload()
        empty.update(carriers={}, players={}, aircraft={})            # Lua empty tables
        self.assertEqual(self.svc.exchange(empty)['watch'], [])

    def test_invalid_records_are_dropped_alone_and_reported(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        self.say('Marshal, Viper 1 2, checking in')
        self.say('Marshal, Dodge 1 1, checking in')
        self.bridge([self.track('100'), self.track('101'), self.track('102')])
        self.players[2] = human('102', 'Dodge 1-1', 'Carl\u200b', '103')       # Lua-legal, not printable here
        self.players.append(human('103', 'Hornet 1-1', 'Dan', 'A-201'))
        self.players.extend(human(str(2000 + i), 'Ghost 1-1', 'Ghost', 'X-1') for i in range(12))
        self.ships.append(ship('73', 'Polar', 140000000, lat=90.0))
        self.ships.append({'id': 'bad id!', 'name': 'x'})
        sample = self.track('101')
        sample['velocity'] = {'x': 5000, 'y': 0, 'z': 0}
        payload = self.payload([self.track('100'), sample, self.track('102')])
        payload['error'] = 'bad\u200b'
        response = self.svc.exchange(payload)
        self.assertTrue(response['accepted'])
        self.assertEqual(self.watched(response), {('100', '71')})
        self.assertEqual(self.events(), ['viper 1 2, Roosevelt marshal, tracking data invalid, carrier session '
                                         'closed, check in again'])
        registry = self.svc.registry()
        self.assertTrue(registry['ready'])
        self.assertEqual([entry['id'] for entry in registry['carriers']], ['71', '72'])
        errors = registry['errors']
        self.assertEqual(len(errors), carrier.MAX_DIAGNOSTICS + 1)
        self.assertEqual(errors[-1], '2 more records rejected')
        joined = ' | '.join(errors)
        for text in ('mission error text rejected', 'carrier 73 rejected: carrier latitude at a pole',
                     'carrier #4 rejected', 'player 102 rejected', 'player 103 rejected: invalid modex'):
            self.assertIn(text, joined)
        self.assertNotIn('Carl', joined)
        self.assertIn('no player aircraft', self.say('Marshal, Dodge 1 1, checking in')['reply'])
        self.assertIn('CASE one recovery', self.say('Marshal, Viper 1 1, say again')['reply'])

    def test_landing_off_this_deck_releases_the_lane_without_a_false_bolter(self):
        self.bridge()
        self.assertIn('Charlie', self.case_one_established()['reply'])
        self.case_one_marshal('Viper 1 2', 'three')
        self.bridge([self.track(), self.track('101', point=OVERHEAD_THREE)])
        self.assertNotIn('Charlie', self.say('Marshal, Viper 1 2, established')['reply'])
        airfield = dict(point=(90000, 30, 0), in_air=False)
        response = self.bridge([self.track(events=[self.event('land', None)], **airfield),
                                self.track('101', point=OVERHEAD_THREE)])
        self.assertEqual(self.watched(response), {('101', '71')})
        self.assertEqual(self.events(), ['viper 1 1, Roosevelt marshal, landing not on this deck, recovery closed',
                                         f'viper 1 2, Roosevelt marshal, Charlie, BRC {bearing(magnetic(0))}, '
                                         'report commencing'])
        self.bridge([self.track(point=(90000, 300, 0), events=[self.event('takeoff', None)]),
                     self.track('101', point=OVERHEAD_THREE)])
        self.assertEqual(self.events(), [])

    def test_notices_reach_only_the_original_occupant(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        checked_out = self.say('Marshal, Viper 1 1, checking out')['validation']
        self.assertTrue(self.svc.validate_reply(checked_out))         # a negative ack survives the release
        unable = self.say('Marshal, Viper 1 2, established')['validation']
        self.assertTrue(self.svc.validate_reply(unable))
        self.players[0] = human('100', 'Viper 1-1', 'Bob', '101', incarnation='100-2')
        del self.players[1]
        self.bridge()
        self.assertFalse(self.svc.validate_reply(checked_out))         # respawned seat
        self.assertFalse(self.svc.validate_reply(unable))              # occupant gone

    def test_two_carriers_keep_independent_stacks_events_and_vocabulary(self):
        self.bridge()
        self.assertIn('angels two', self.say('Marshal, Viper 1 1, checking in')['reply'])
        self.assertIn('angels two', self.say('Marshal, Viper 1 2, checking in', channel=RADIO_B,
                                             carrier_id='72')['reply'])
        self.assertIn('angels three', self.say('Marshal, Dodge 1 1, checking in')['reply'])
        self.say('Marshal, Viper 1 2, readback angels two altimeter two nine nine two', channel=RADIO_B,
                 carrier_id='72')
        self.bridge([self.track('101', point=(200000, 610, 0), place='72')])
        self.assertIn('Lincoln marshal, roger established, number one, Charlie',
                      self.say('Marshal, Viper 1 2, established', channel=RADIO_B, carrier_id='72')['reply'])
        self.assertEqual(self.events(), [])
        events = self.svc.take_events({'coalition': 2, 'channel': RADIO_B, 'carrier_id': '72'})
        self.assertIn('Lincoln', events['vocabulary'])
        self.assertNotIn('Roosevelt', events['vocabulary'])
        self.assertEqual(self.svc.take_events({'coalition': 2, 'channel': RADIO_B, 'carrier_id': '71'}),
                         {'events': [], 'vocabulary': []})

    def test_long_names_never_exceed_one_transmission(self):
        long_name = 'Carrier ' + 'Supercalifragilistic ' * 2 + 'Washington'
        self.ships[0]['name'] = long_name[:64]
        self.ships[0]['recovery_case'] = 3
        self.bridge()
        for text in ('Marshal, Viper 1 1, checking in', 'Marshal, ' + 'blah ' * 400 + 'checking in',
                     'Marshal, Viper 1 1, say again'):
            reply = self.say(text)['reply']
            self.assertIsNotNone(reply)
            self.assertLessEqual(len(reply), carrier.MAX_REPLY_CHARS)

    def test_course_change_revokes_an_active_case_one_ball_call(self):
        ball = self.case_one_to_ball()
        self.ships[0]['heading_deg'] = 3
        self.bridge([self.track(point=BALL)])
        self.assertFalse(self.svc.validate_reply(ball['validation']))

    def test_parked_recovery_checkin_never_becomes_a_trap_after_deck_settles(self):
        self.bridge()
        first = self.say('Marshal, Viper 1 1, checking in')
        for settled in (False, False, True):
            response = self.bridge([self.track(point=ON_DECK, in_air=False, on_deck=settled)])
            self.assertNotIn(('100', '71'), self.watched(response))
        self.assertFalse(self.svc.validate_reply(first['validation']))
        self.case_one_marshal('Viper 1 2')
        self.bridge([self.track('101')])
        self.assertIn('Charlie', self.say('Marshal, Viper 1 2, established')['reply'])

    def test_touchdown_fouls_deck_and_revokes_a_ball_call_before_trap_confirmation(self):
        ball = self.case_one_to_ball()
        self.case_one_marshal('Viper 1 2', 'three')
        self.bridge([self.track(point=BALL), self.track('101', point=OVERHEAD_THREE)])
        self.say('Marshal, Viper 1 2, established')
        self.bridge([self.track(point=BALL), self.track('101', point=ON_DECK, in_air=False,
                                                      events=[self.event('land')])])
        self.assertFalse(self.svc.validate_reply(ball['validation']))
        events = self.svc.take_events({'coalition': 2, 'channel': RADIO_A, 'carrier_id': '71'})['events']
        self.assertTrue(any(event['unit_id'] == '100' and self.svc.validate_reply(event) for event in events))

    def test_case_change_revokes_a_grant_before_commencing(self):
        self.bridge()
        grant = self.case_one_established()
        self.ships[0]['recovery_case'] = 3
        self.bridge([self.track()])
        self.assertFalse(self.svc.validate_reply(grant['validation']))
        radial = (magnetic(351) + 180) % 360
        self.say(f'Marshal, Viper 1 1, readback {bearing(radial)} radial, two one DME, angels six, '
                 'approach time zero five, altimeter two nine nine two')
        self.bridge([self.track(point=astern(21, 6000))])
        self.say('Marshal, Viper 1 1, established')
        self.model += 300
        self.bridge([self.track(point=astern(21, 6000))])
        self.assertIn('report platform', self.say('Marshal, Viper 1 1, commencing')['reply'])

    def test_midnight_marshal_slots_remain_minutes_not_a_day_apart(self):
        self.ships[0]['recovery_case'] = 3
        before = self.payload()
        before['abs_time'] = 86340
        self.svc.exchange(before)
        self.say('Marshal, Viper 1 1, checking in')
        self.model += 120
        after = self.payload([self.track(point=astern(21, 6000))])
        after.update(abs_time=60, date={'year': 2026, 'month': 11, 'day': 13})
        self.svc.exchange(after)
        self.say('Marshal, Viper 1 2, checking in')
        when = datetime.datetime(2026, 11, 13, 0, 1, tzinfo=datetime.timezone.utc)
        radial = (round(domain.true_to_magnetic(43, 41, 351, when)) + 180) % 360
        self.say(f'Marshal, Viper 1 2, readback {bearing(radial)} radial, two two DME, angels seven, '
                 'approach time zero six, altimeter two nine nine two')
        tracks = [self.track(point=astern(21, 6000)), self.track('101', point=astern(22, 7000))]
        positioned = self.payload(tracks)
        positioned.update(abs_time=61, date=after['date'])
        self.svc.exchange(positioned)
        self.say('Marshal, Viper 1 2, established')
        self.model += 300
        due = self.payload(tracks)
        due.update(abs_time=362, date=after['date'])
        self.svc.exchange(due)
        self.assertIn('report platform', self.say('Marshal, Viper 1 2, commencing')['reply'])

    def test_unhashable_lifecycle_type_rejects_only_its_aircraft(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        self.say('Marshal, Viper 1 2, checking in')
        malformed = dict(self.event('land'), type=[])
        response = self.bridge([self.track(), self.track('101', events=[malformed])])
        self.assertEqual(self.watched(response), {('100', '71')})
        self.assertTrue(self.svc.ready)

    def test_catalog_rejection_cannot_reuse_an_old_enrollment_notice(self):
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        old = self.say('Marshal, Viper 1 1, checking out')['validation']
        self.assertTrue(self.svc.validate_reply(old))
        rejected = copy.deepcopy(self.players)
        rejected[0]['player_name'] = 'Bob\u200b'
        self.bridge(players=rejected)
        self.bridge()
        self.say('Marshal, Viper 1 1, checking in')
        self.assertFalse(self.svc.validate_reply(old))


if __name__ == '__main__':
    unittest.main()
