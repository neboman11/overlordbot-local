"""Native airfield controller edges, driven only through the bridge and radio APIs."""
import copy
import math
import unittest
from unittest.mock import patch

from overlord import airfield
from overlord.airfield import AirfieldService, readback_problem

GROUND, TOWER = '2:121000000:0', '2:122000000:0'
AWACS = '2:251000000:0'
FEET = 3.28084


def radio(frequency, roles=('ground', 'tower', 'approach'), aliases=()):
    return {'frequency_hz': frequency, 'modulation': 0, 'roles': list(roles), 'aliases': list(aliases)}


def field(fid='1', name='Ironwood', radios=None, x=0, elevation=100, **changes):
    value = {'id': fid, 'name': name, 'coalition': 2, 'alive': True,
             'point': {'x': x, 'y': elevation, 'z': 0}, 'lat': 43.0, 'lon': 41.0,
             'wind_from_deg': 90, 'wind_speed_mps': 5, 'altimeter_inhg': 29.92,
             'radios': radios if radios is not None else [radio(121000000)],
             'runways': [{'id': '09-27', 'name': '09', 'opposite_name': '27',
                          'point': {'x': x, 'y': elevation, 'z': 0}, 'heading_deg': 90,
                          'length_m': 2400, 'width_m': 60}]}
    value.update(changes)
    return value


def human(uid, callsign, name, incarnation=None, is_human=True):
    return {'unit_id': uid, 'unit_name': 'Plane-' + uid, 'player_name': name, 'callsign': callsign,
            'modex': str(int(uid) + 1), 'coalition': 2, 'incarnation': incarnation or uid + '-1',
            'human': is_human}


# Runway 09: centre (0, 0), threshold at z=-1200, footprint |z| <= 1260, |x| <= 55.
PARKING = (-400, 0)
HOLD = (-150, -1150)
LINED_UP = (0, -1150)
ON_RUNWAY = (0, 0)
OFF_RUNWAY = (-300, 300)
FINAL = (0, -1200 - 3 * 1852)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(airfield.time, 'monotonic', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.svc = AirfieldService(reserved_channels=(AWACS,))
        self.seq, self.event_id, self.epoch = 0, 0, 'e1'
        self.fields = [field(), field('2', 'Maple', x=200000)]
        self.players = [human('100', 'Viper 1-1', 'Bob'), human('101', 'Viper 1-2', 'Ann'),
                        human('102', 'Dodge 1-1', 'Carl')]
        self.tracks = {}
        self.sync()

    def sync(self):
        self.seq += 1
        self.clock.now += 1
        payload = {'version': 1, 'epoch': self.epoch, 'seq': self.seq, 'time': 100 + self.seq,
                   'abs_time': 43200 + self.seq, 'date': {'year': 2026, 'month': 11, 'day': 13},
                   'airfields': copy.deepcopy(self.fields), 'players': copy.deepcopy(self.players),
                   'aircraft': copy.deepcopy(list(self.tracks.values()))}
        for track in self.tracks.values():
            track['events'] = []
        return self.svc.exchange(payload)

    def put(self, uid, where, *, in_air=False, agl_ft=0, heading=90, ground='1', fid='1', speed=0, elevation=100):
        self.tracks[uid] = {'unit_id': uid, 'incarnation': uid + '-1', 'airfield_id': fid,
                            'point': {'x': where[0], 'y': elevation + agl_ft / FEET, 'z': where[1]},
                            'lat': 43.0, 'lon': 41.0, 'velocity': {'x': 0, 'y': 0, 'z': speed},
                            'heading_deg': heading, 'in_air': in_air,
                            'ground_airfield_id': None if in_air else ground,
                            'events': self.tracks.get(uid, {}).get('events', [])}

    def event(self, uid, kind, place='1'):
        self.event_id += 1
        self.tracks[uid]['events'].append({'id': self.event_id, 'type': kind, 'airfield_id': place,
                                           'time': 100 + self.seq + 0.5})

    def say(self, text, channel=GROUND, **extra):
        return self.svc.respond(dict({'airfield': True, 'text': text, 'coalition': 2, 'channel': channel}, **extra))

    def watch(self):
        return {(w['unit_id'], w['airfield_id']) for w in self.sync()['watch']}

    def valid(self, answer):
        return self.svc.validate_reply(answer['validation'])

    def events(self, channel=GROUND):
        return self.svc.take_events({'airfield': True, 'coalition': 2, 'channel': channel})['events']

    # -- procedures ---------------------------------------------------------

    def to_hold(self, uid='100', who='Viper one one'):
        self.put(uid, PARKING)
        self.sync()
        self.say(f'Ironwood ground {who} request startup')
        self.sync()
        taxi = self.say(f'Ironwood ground {who} request taxi')
        self.assertIn('hold short of all runways', taxi['reply'])
        back = self.say(f'Ironwood ground {who} read back taxi to holding point runway zero nine, hold short')
        self.assertIn('readback correct', back['reply'])
        self.put(uid, HOLD)
        self.sync()
        return self.say(f'Ironwood tower {who} holding short')

    def cleared_to_land(self, uid='101', who='Viper one two'):
        self.put(uid, FINAL, in_air=True, agl_ft=1000)
        self.sync()
        brief = self.say(f'Ironwood tower {who} inbound')
        self.assertIn('altimeter two nine nine two', brief['reply'])
        self.assertIn('readback correct',
                      self.say(f'Ironwood tower {who} read back runway zero nine, altimeter two niner niner two')['reply'])
        self.sync()
        final = self.say(f'Ironwood tower {who} final')
        self.assertIn('cleared to land', final['reply'])
        back = self.say(f'Ironwood tower {who} read back cleared to land runway zero nine')
        self.assertEqual(back['reply'].split(', ')[-1], 'readback correct')
        return back


class MovementAuthorityTests(Base):
    def test_landing_clearance_holds_the_only_lane_until_the_runway_is_measured_vacated(self):
        self.cleared_to_land()
        holding = self.to_hold()
        self.assertNotIn('line up', holding['reply'])
        self.event('101', 'land')
        self.put('101', ON_RUNWAY, ground='1')
        self.sync()
        self.assertFalse(any('line up' in e['reply'] for e in self.events()))
        # A pilot claim alone proves nothing.
        self.assertIn('negative', self.say('Ironwood tower Viper one two vacated')['reply'])
        self.put('101', OFF_RUNWAY)
        self.sync()
        self.assertTrue(any('line up and wait' in e['reply'] and e['unit_id'] == '100' for e in self.events()))

    def test_line_up_is_not_takeoff_and_pilot_assertion_does_not_prove_lined_up(self):
        lineup = self.to_hold()
        self.assertIn('line up and wait', lineup['reply'])
        back = self.say('Ironwood tower Viper one one read back line up and wait runway zero nine')
        self.assertIn('readback correct', back['reply'])
        self.assertNotIn('takeoff', back['reply'])
        claim = self.say('Ironwood tower Viper one one lined up')
        self.assertNotIn('cleared for takeoff', claim['reply'])
        self.assertEqual(self.svc._sessions['100'].phase, 'lineup')
        self.assertTrue(self.valid(claim))
        self.put('100', LINED_UP)
        self.sync()
        self.assertTrue(any('cleared for takeoff' in e['reply'] for e in self.events()))

    def test_unnamed_native_strip_is_protected_but_never_selected(self):
        # Native geometry with an unusable label: occupancy-only, crossing runway 09 at z=600.
        self.fields[0]['runways'].append({'id': 'native:2', 'name': '', 'point': {'x': 0, 'y': 100, 'z': 600},
                                          'heading_deg': 0, 'length_m': 1500, 'width_m': 45})
        self.fields[0]['wind_from_deg'] = 0
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        landing = self.cleared_to_land()
        self.assertEqual(self.svc.registry()['airfields'][0]['runway'], '09')
        self.put('100', (500, 600))         # on the unnamed strip only, clear of runway 09
        self.sync()
        self.assertFalse(self.valid(landing))
        self.assertIsNone(self.svc._control['1']['lane'])
        self.assertNotIn('cleared to land', self.say('Ironwood tower Viper one two final')['reply'])
        self.put('100', PARKING)
        self.sync()
        self.assertTrue(any('cleared to land' in e['reply'] for e in self.events() if e['unit_id'] == '101'))

    def test_second_departure_waits_behind_the_first_lane_holder(self):
        first = self.to_hold()
        self.assertIn('line up and wait', first['reply'])
        self.put('101', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one two request startup')
        self.sync()
        self.say('Ironwood ground Viper one two request taxi')
        self.say('Ironwood ground Viper one two read back taxi to holding point runway zero nine, hold short')
        self.put('101', (-150, -1100))
        self.sync()
        second = self.say('Ironwood tower Viper one two holding short')
        self.assertNotIn('line up', second['reply'])
        self.assertIsNotNone(self.svc._control['1']['lane'])

    def test_new_runway_occupant_sends_the_cleared_arrival_around_and_revokes_its_clearance(self):
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        landing = self.cleared_to_land()
        self.put('100', ON_RUNWAY)          # taxied onto the runway without any clearance
        self.sync()
        self.assertFalse(self.valid(landing))
        events = [e for e in self.events() if e['unit_id'] == '101']
        self.assertTrue(events and 'go around' in events[-1]['reply'])

    def test_unauthorised_takeoff_is_warned_and_does_not_take_the_lane(self):
        landing = self.cleared_to_land()
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        self.put('100', HOLD, in_air=True, agl_ft=200)
        self.event('100', 'takeoff')
        self.sync()
        self.assertTrue(any('not cleared' in e['reply'] for e in self.events() if e['unit_id'] == '100'))
        self.assertTrue(self.valid(landing))

    def test_landing_at_another_airfield_frees_the_lane_without_a_false_landing(self):
        self.cleared_to_land()
        self.to_hold()
        self.event('101', 'land', place='2')
        self.put('101', (200000, 0), ground='2')
        self.assertNotIn(('101', '1'), self.watch())
        self.sync()
        self.assertTrue(any('line up and wait' in e['reply'] for e in self.events() if e['unit_id'] == '100'))

    def test_touch_and_go_ends_the_landing_clearance(self):
        landing = self.cleared_to_land()
        self.event('101', 'land')
        self.put('101', ON_RUNWAY)
        self.sync()
        self.put('101', (0, 600), in_air=True, agl_ft=300)
        self.event('101', 'takeoff')
        self.sync()
        self.assertFalse(self.valid(landing))
        self.assertTrue(any('go around' in e['reply'] for e in self.events() if e['unit_id'] == '101'))

    def test_high_elevation_final_uses_height_above_the_field(self):
        self.fields[0] = field(elevation=1500)
        self.sync()
        self.put('101', FINAL, in_air=True, agl_ft=1000, elevation=1500)
        self.sync()
        self.say('Ironwood tower Viper one two inbound')
        self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.assertIn('cleared to land', self.say('Ironwood tower Viper one two final')['reply'])

    def test_final_report_far_off_the_centreline_is_not_eligible(self):
        self.put('101', (4000, -1200 - 3 * 1852), in_air=True, agl_ft=1000)
        self.sync()
        self.say('Ironwood tower Viper one two inbound')
        self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.assertNotIn('cleared to land', self.say('Ironwood tower Viper one two final')['reply'])

    def test_emergency_arrival_is_cleared_before_an_earlier_arrival(self):
        # A controlled aircraft on the runway makes both arrivals wait; the earlier one enrolled first.
        self.put('102', ON_RUNWAY)
        self.put('100', (0, -1200 - 4 * 1852), in_air=True, agl_ft=1200)
        self.put('101', FINAL, in_air=True, agl_ft=1000)
        self.sync()
        self.say('Ironwood ground Dodge one one request startup')
        self.sync()
        for who in ('Viper one two', 'Viper one one'):
            self.say(f'Ironwood tower {who} inbound')
            self.say(f'Ironwood tower {who} read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.assertNotIn('cleared to land', self.say('Ironwood tower Viper one two final')['reply'])
        self.assertNotIn('cleared to land', self.say('Ironwood tower Viper one one final')['reply'])
        self.say('Ironwood tower Viper one one mayday low fuel')
        self.put('102', OFF_RUNWAY)
        self.sync()
        cleared = [e['unit_id'] for e in self.events() if 'cleared to land' in e['reply']]
        self.assertEqual(cleared, ['100'])


class ReadbackTests(Base):
    def setUp(self):
        super().setUp()
        self.put('101', FINAL, in_air=True, agl_ft=1000)
        self.sync()
        self.brief = self.say('Ironwood tower Viper one two inbound')

    def test_arrival_readback_needs_the_exact_altimeter(self):
        wrong = self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner one')
        self.assertIn('negative readback', wrong['reply'])
        self.assertTrue(self.valid(self.brief))
        self.assertIn('unable', self.say('Ironwood tower Viper one two final')['reply'])

    def test_negated_or_wrong_side_readbacks_do_not_advance(self):
        self.assertIn('negative readback', self.say(
            'Ironwood tower Viper one two read back runway zero nine left, altimeter two niner niner two')['reply'])
        self.assertIn('negative readback', self.say(
            'Ironwood tower Viper one two read back not runway zero nine, altimeter two niner niner two')['reply'])
        self.assertIn('unable', self.say('Ironwood tower Viper one two final')['reply'])

    def test_takeoff_and_landing_terms_never_satisfy_each_other(self):
        takeoff = {'kind': 'takeoff', 'runway': '09', 'altimeter': None}
        landing = {'kind': 'landing', 'runway': '09', 'altimeter': None}
        self.assertIsNotNone(readback_problem('cleared to land runway zero nine', takeoff))
        self.assertIsNotNone(readback_problem('cleared for takeoff runway zero nine', landing))
        self.assertIsNotNone(readback_problem('cleared for takeoff, cleared to land runway zero nine', landing))
        self.assertIsNone(readback_problem('runway zero niner cleared for take off', takeoff))
        self.assertIsNotNone(readback_problem('runway zero nine, runway two seven, cleared to land', landing))

    def test_say_again_recalculates_instead_of_replaying_a_revoked_clearance(self):
        self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.say('Ironwood tower Viper one two final')
        self.say('Ironwood tower Viper one two read back cleared to land runway zero nine')
        self.say('Ironwood tower Viper one two going around')
        again = self.say('Ironwood tower Viper one two say again')
        self.assertNotIn('cleared to land', again['reply'])


class IdentityAndRadioTests(Base):
    def test_unregistered_or_ai_aircraft_never_enrol(self):
        self.players[2]['human'] = False
        self.sync()
        answer = self.say('Ironwood ground Dodge one one request startup')
        self.assertIsNone(answer.get('transfer_unit_id'))
        self.assertEqual(self.watch(), set())

    def test_read_only_queries_do_not_enrol_or_transfer(self):
        answer = self.say('Ironwood tower Viper one one request wind')
        self.assertIn('wind', answer['reply'])
        self.assertNotIn('transfer_unit_id', answer)
        self.assertEqual(self.watch(), set())

    def test_enrolment_reports_the_transfer_unit(self):
        self.assertEqual(self.say('Ironwood ground Viper one one request startup')['transfer_unit_id'], '100')

    def test_shared_channel_follows_an_existing_session_without_a_field_name(self):
        self.say('Maple ground Viper one one request startup')
        answer = self.say('Ground Viper one one request taxi')
        self.assertIn('Maple', answer['reply'])
        self.assertEqual(self.watch(), {('100', '2')})

    def test_same_field_band_change_keeps_phase_and_revokes_old_channel_speech(self):
        self.fields[0]['radios'] = [radio(121000000, ('ground',)), radio(122000000, ('tower',))]
        self.fields[1]['radios'] = [radio(124000000)]
        self.sync()
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        taxi = self.say('Ironwood ground Viper one one request taxi')
        self.say('Ironwood ground Viper one one read back taxi to holding point runway zero nine, hold short')
        self.put('100', HOLD)
        self.sync()
        old = self.say('Ironwood ground Viper one one holding short')
        self.assertIn('contact Ironwood tower', old['reply'])
        self.assertNotIn('line up', old['reply'])
        tower = self.say('Ironwood tower Viper one one ready for departure', channel=TOWER)
        self.assertIn('line up and wait', tower['reply'])
        self.assertFalse(self.valid(old))
        self.assertFalse(self.valid(taxi))
        self.assertTrue(self.valid(tower))
        self.assertEqual(self.watch(), {('100', '1')})

    def test_role_not_published_on_channel_is_refused_without_enrolment(self):
        self.fields[0]['radios'] = [radio(121000000, ('ground',)), radio(122000000, ('tower',))]
        self.fields[1]['radios'] = [radio(124000000)]
        self.sync()
        answer = self.say('Ironwood tower Viper one one inbound')
        self.assertIn('not on this frequency', answer['reply'])
        self.assertEqual(self.watch(), set())

    def test_respawn_and_release_revoke_old_notices(self):
        notice = self.say('Ironwood ground Viper one one request wind')
        self.assertTrue(self.valid(notice))
        self.svc.release_unit('100')
        self.assertFalse(self.valid(notice))
        notice = self.say('Ironwood ground Viper one one request wind')
        self.players[0]['incarnation'] = '100-2'
        self.sync()
        self.assertFalse(self.valid(notice))

    def test_new_epoch_revokes_and_old_epoch_is_refused(self):
        answer = self.say('Ironwood ground Viper one one request startup')
        old_epoch = self.epoch
        self.epoch = 'e2'
        self.sync()
        self.assertFalse(self.valid(answer))
        self.epoch, self.seq = old_epoch, 99
        self.assertFalse(self.sync()['accepted'])

    def test_take_events_drains_only_its_own_channel(self):
        self.fields[1]['radios'] = [radio(122000000)]
        self.sync()
        self.put('100', PARKING)
        self.put('101', (200000 - 400, 0), ground='2', fid='2')
        self.say('Ironwood ground Viper one one request startup')
        self.say('Maple ground Viper one two request startup', channel=TOWER)
        self.sync()
        self.assertEqual({e['unit_id'] for e in self.events(TOWER)}, {'101'})
        self.assertEqual({e['unit_id'] for e in self.events(GROUND)}, {'100'})

    def test_dynamic_reservations_add_to_constructor_reservations(self):
        self.fields[1]['radios'] = [radio(251000000), radio(122000000)]
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.svc.set_reserved_channels(['2:121000000:0'])
        self.assertEqual(self.watch(), set())
        self.assertIsNone(self.say('Ironwood ground Viper one one request startup')['reply'])
        self.svc.set_reserved_channels([])
        enabled = {r['channel']: r['enabled'] for r in self.svc.registry()['radios']}
        self.assertTrue(enabled['2:121000000:0'])
        self.assertFalse(enabled[AWACS])
        self.assertTrue(enabled['2:122000000:0'])

    def test_emergency_reasons_and_cancelled_emergency_never_release(self):
        self.say('Ironwood tower Viper one two inbound')
        self.say('Ironwood tower Viper one two mayday mayday, gear not down, aborting approach')
        self.assertEqual(self.watch(), {('101', '1')})
        answer = self.say('Ironwood tower Viper one two cancel emergency')
        self.assertNotIn('checked out', answer['reply'])
        self.assertEqual(self.watch(), {('101', '1')})
        self.put('100', LINED_UP)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.say('Ironwood tower Viper one one mayday aborting takeoff')
        self.assertIn(('100', '1'), self.watch())


class LifecycleAndDataTests(Base):
    def test_parked_arrival_check_in_never_becomes_a_landing(self):
        self.say('Ironwood tower Viper one two inbound')
        self.put('101', PARKING)
        self.sync()
        self.assertEqual(self.watch(), set())

    def test_real_touchdown_needs_a_land_event_here(self):
        self.cleared_to_land()
        self.put('101', ON_RUNWAY)
        self.tracks['101']['ground_airfield_id'] = None    # on the ground, place not yet known
        self.sync()
        self.assertNotIn('vacated', ' '.join(e['reply'] for e in self.events()))
        self.event('101', 'land')
        self.put('101', ON_RUNWAY)
        self.sync()
        self.assertTrue(any('landing observed' in e['reply'] for e in self.events()))

    def test_startup_without_a_ground_sample_is_standby_not_approval(self):
        answer = self.say('Ironwood ground Viper one one request startup')
        self.assertNotIn('approved', answer['reply'])
        self.assertNotIn('taxi to', self.say('Ironwood ground Viper one one request taxi')['reply'])
        self.put('100', PARKING, ground='2', fid='1')
        self.sync()
        self.assertEqual(self.watch(), set())

    def test_stale_link_revokes_grants_and_refuses_positive_calls(self):
        lineup = self.to_hold()
        self.clock.now += 30
        self.assertFalse(self.valid(lineup))
        self.assertIn('no live airfield link', self.say('Ironwood tower Viper one one say again')['reply'])
        self.assertIsNone(self.svc._control['1']['lane'])

    def test_missing_wind_gives_no_runway_and_no_taxi_clearance(self):
        for key in ('wind_from_deg', 'wind_speed_mps'):
            del self.fields[0][key]
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        taxi = self.say('Ironwood ground Viper one one request taxi')
        self.assertIn('wind information not available', taxi['reply'])
        self.assertIsNone(self.svc.registry()['airfields'][0]['runway'])

    def test_calm_wind_selects_a_real_runway_deterministically(self):
        self.fields[0]['wind_speed_mps'] = 0
        del self.fields[0]['wind_from_deg']
        self.sync()
        self.assertEqual(self.svc.registry()['airfields'][0]['runway'], '09')

    def test_wind_shift_keeps_runway_until_tailwind_then_revokes_pending_taxi(self):
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        taxi = self.say('Ironwood ground Viper one one request taxi')
        self.fields[0]['wind_from_deg'] = 170
        self.sync()
        self.assertTrue(self.valid(taxi))
        self.fields[0]['wind_from_deg'] = 270
        self.sync()
        self.assertFalse(self.valid(taxi))
        self.assertTrue(any('two seven' in e['reply'] for e in self.events()))
        self.assertIn('negative readback', self.say(
            'Ironwood ground Viper one one read back taxi to holding point runway zero nine, hold short')['reply'])

    def test_inconsistent_runway_label_is_not_selectable(self):
        self.fields[0]['runways'][0].update(name='18', opposite_name='36')
        self.sync()
        self.assertIsNone(self.svc.registry()['airfields'][0]['runway'])
        self.assertTrue(any('runway' in e and 'heading' in e for e in self.svc.registry()['errors']))

    def test_one_bad_airfield_record_does_not_disable_the_others(self):
        self.fields[1]['point']['x'] = math.nan
        self.sync()
        registry = self.svc.registry()
        self.assertEqual([f['id'] for f in registry['airfields']], ['1'])
        self.assertTrue(any('airfield 2' in e for e in registry['errors']))
        self.assertIn('Ironwood', self.say('Ironwood ground Viper one one request wind')['reply'])

    def test_duplicate_airfield_ids_fail_the_whole_payload_atomically(self):
        self.fields[1]['id'] = '1'
        with self.assertRaises(ValueError):
            self.sync()
        self.seq -= 1
        self.fields[1]['id'] = '2'
        self.assertTrue(self.sync()['accepted'])

    def test_invalid_sample_closes_only_that_session(self):
        self.put('100', PARKING)
        self.put('101', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.say('Ironwood ground Viper one two request startup')
        self.tracks['100']['velocity']['x'] = math.inf
        self.assertEqual(self.watch(), {('101', '1')})

    def test_crossing_requires_explicit_clearance_and_measured_vacate(self):
        self.fields[0]['runways'].append({'id': '18-36', 'name': '18', 'opposite_name': '36',
                                          'point': {'x': 3000, 'y': 100, 'z': 3000}, 'heading_deg': 180,
                                          'length_m': 2000, 'width_m': 45})
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        self.say('Ironwood ground Viper one one request taxi')
        self.say('Ironwood ground Viper one one read back taxi to holding point runway zero nine, hold short')
        self.put('100', (3000, 3150))
        self.sync()
        cross = self.say('Ironwood ground Viper one one request crossing runway one eight')
        self.assertIn('cross runway one eight', cross['reply'])
        self.assertIn('negative readback', self.say(
            'Ironwood ground Viper one one read back cross runway three six')['reply'])
        self.say('Ironwood ground Viper one one read back cross runway one eight')
        self.put('100', (3000, 3000))
        self.sync()
        self.assertIsNotNone(self.svc._control['1']['lane'])
        self.put('100', (3000, 2850))
        self.sync()
        self.assertIsNone(self.svc._control['1']['lane'])


CROSS_STRIP = {'id': '18-36', 'name': '18', 'opposite_name': '36', 'point': {'x': 3000, 'y': 100, 'z': 3000},
               'heading_deg': 180, 'length_m': 2000, 'width_m': 45}
AT_CROSSING, ON_CROSS_STRIP, PAST_CROSSING = (3000, 3150), (3000, 3000), (3000, 2850)


class ReviewRegressionTests(Base):
    def lapse(self):
        """Let more than the readback timeout of model time pass in one snapshot."""
        self.seq += 61
        return self.sync()

    def lined_up_with_takeoff_clearance(self):
        self.to_hold()
        self.say('Ironwood tower Viper one one read back line up and wait runway zero nine')
        self.put('100', LINED_UP)
        self.sync()
        self.assertEqual(self.svc._sessions['100'].phase, 'takeoff_clearance')
        self.events()

    def taxi_out(self, uid='100', who='Viper one one'):
        self.put(uid, PARKING)
        self.sync()
        self.say(f'Ironwood ground {who} request startup')
        self.sync()
        self.say(f'Ironwood ground {who} request taxi')
        self.say(f'Ironwood ground {who} read back taxi to holding point runway zero nine, hold short')

    # 1: cancel words inside a readback
    def test_cancel_or_abort_inside_a_readback_never_advances(self):
        self.lined_up_with_takeoff_clearance()
        for text in ('read back cancel cleared for takeoff runway zero nine',
                     'read back cleared for takeoff runway zero nine, abort'):
            answer = self.say(f'Ironwood tower Viper one one {text}')
            self.assertIn('negative readback', answer['reply'])
            self.assertEqual(self.svc._sessions['100'].phase, 'takeoff_clearance')

    # 2: lapsed clearances stay revoked until the pilot asks again
    def test_unread_line_up_lapses_and_is_not_reissued_until_requested_again(self):
        self.to_hold()
        self.lapse()
        self.assertEqual(self.svc._sessions['100'].phase, 'holding_short')
        self.sync()
        self.assertIsNone(self.svc._control['1']['lane'])
        self.assertFalse(any('line up' in e['reply'] for e in self.events() if 'no readback' not in e['reply']))
        self.assertNotIn('line up and wait', self.say('Ironwood tower Viper one one say again')['reply'])
        late = self.say('Ironwood tower Viper one one read back line up and wait runway zero nine')
        self.assertIn('unable', late['reply'])
        self.assertIsNone(self.svc._control['1']['lane'])
        again = self.say('Ironwood tower Viper one one ready for departure')
        self.assertIn('line up and wait', again['reply'])

    def test_unread_takeoff_lapses_keeps_runway_occupancy_and_waits_for_a_new_request(self):
        self.lined_up_with_takeoff_clearance()
        self.lapse()
        self.sync()
        self.assertEqual(self.svc._sessions['100'].phase, 'lined_up')
        self.assertEqual(self.svc._control['1']['lane'], '100')       # still physically on the runway
        self.assertFalse(any('cleared for takeoff' in e['reply'] for e in self.events()))
        again = self.say('Ironwood tower Viper one one ready for takeoff')
        self.assertIn('cleared for takeoff', again['reply'])

    def test_unread_crossing_lapses_and_is_not_reissued_until_requested_again(self):
        self.fields[0]['runways'].append(dict(CROSS_STRIP))
        self.taxi_out()
        self.put('100', AT_CROSSING)
        self.sync()
        self.assertIn('cross runway one eight', self.say(
            'Ironwood ground Viper one one request crossing runway one eight')['reply'])
        self.lapse()
        self.sync()
        self.assertIsNone(self.svc._control['1']['lane'])
        self.assertEqual(self.svc._sessions['100'].phase, 'crossing_requested')
        again = self.say('Ironwood ground Viper one one request crossing runway one eight')
        self.assertIn('cross runway one eight', again['reply'])

    # 3: one total order on current eligibility
    def test_emergency_departure_goes_before_a_normal_arrival(self):
        self.put('102', ON_RUNWAY)
        self.sync()
        self.say('Ironwood ground Dodge one one request startup')
        self.sync()
        self.to_hold()
        self.put('101', FINAL, in_air=True, agl_ft=1000)
        self.sync()
        self.say('Ironwood tower Viper one two inbound')
        self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.assertNotIn('cleared to land', self.say('Ironwood tower Viper one two final')['reply'])
        self.say('Ironwood tower Viper one one mayday low fuel')
        self.put('102', OFF_RUNWAY)
        self.sync()
        events = self.events()
        self.assertTrue(any('line up and wait' in e['reply'] and e['unit_id'] == '100' for e in events))
        self.assertFalse(any('cleared to land' in e['reply'] for e in events))

    def test_arrival_no_longer_on_final_does_not_block_departures(self):
        self.put('102', ON_RUNWAY)
        self.sync()
        self.say('Ironwood ground Dodge one one request startup')
        self.sync()
        self.to_hold()
        self.put('101', FINAL, in_air=True, agl_ft=1000)
        self.sync()
        self.say('Ironwood tower Viper one two inbound')
        self.say('Ironwood tower Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        self.say('Ironwood tower Viper one two final')
        self.put('101', (4000, -1200 - 3 * 1852), in_air=True, agl_ft=1000)
        self.put('102', OFF_RUNWAY)
        self.sync()
        self.assertTrue(any('line up and wait' in e['reply'] and e['unit_id'] == '100' for e in self.events()))

    # 4: taxi-in lifecycle
    def test_taxi_in_can_cross_a_runway_and_resumes_taxi_in_after_measured_vacate(self):
        self.fields[0]['runways'].append(dict(CROSS_STRIP))
        self.cleared_to_land()
        self.event('101', 'land')
        self.put('101', ON_RUNWAY)
        self.sync()
        self.put('101', OFF_RUNWAY)
        self.sync()
        self.assertIn('hold short of all runways', self.say('Ironwood ground Viper one two request taxi to parking')['reply'])
        self.say('Ironwood ground Viper one two read back hold short of all runways')
        self.put('101', AT_CROSSING)
        self.sync()
        self.assertIn('cross runway one eight', self.say(
            'Ironwood ground Viper one two request crossing runway one eight')['reply'])
        self.say('Ironwood ground Viper one two read back cross runway one eight')
        self.put('101', ON_CROSS_STRIP)
        self.sync()
        self.put('101', PAST_CROSSING)
        self.sync()
        self.assertEqual(self.svc._sessions['101'].phase, 'taxi_in')
        self.assertIsNone(self.svc._control['1']['lane'])

    def test_departure_returning_to_parking_can_start_a_new_departure(self):
        self.taxi_out()
        self.say('Ironwood ground Viper one one request taxi to parking')
        self.say('Ironwood ground Viper one one read back hold short of all runways')
        self.assertEqual(self.svc._sessions['100'].phase, 'taxi_in')
        again = self.say('Ironwood ground Viper one one request taxi')
        self.assertIn('taxi to holding point runway zero nine', again['reply'])

    # 5: informational replies are bound to the live facts they speak
    def test_wind_reply_is_revoked_when_the_wind_changes_or_the_link_goes_stale(self):
        wind = self.say('Ironwood tower Viper one one request wind')
        self.assertTrue(self.valid(wind))
        self.fields[0]['wind_speed_mps'] = 9            # same runway, different spoken wind
        self.sync()
        self.assertFalse(self.valid(wind))
        wind = self.say('Ironwood tower Viper one one request wind')
        self.clock.now += 30
        self.assertFalse(self.valid(wind))

    def test_altimeter_reply_is_revoked_when_the_altimeter_changes(self):
        qnh = self.say('Ironwood tower Viper one one request altimeter')
        self.assertTrue(self.valid(qnh))
        self.fields[0]['altimeter_inhg'] = 29.80
        self.sync()
        self.assertFalse(self.valid(qnh))

    def test_active_runway_reply_is_revoked_when_the_runway_changes_for_an_enrolled_pilot(self):
        self.put('100', PARKING)
        self.sync()
        self.say('Ironwood ground Viper one one request startup')
        self.sync()
        revision = self.svc._sessions['100'].revision
        active = self.say('Ironwood ground Viper one one request active runway')
        self.assertTrue(self.valid(active))
        self.fields[0]['active_runway'] = '27'
        self.sync()
        self.assertEqual(self.svc._sessions['100'].revision, revision)
        self.assertFalse(self.valid(active))

    def test_cancel_acknowledgement_stays_valid_on_a_stale_link(self):
        ack = self.say('Ironwood tower Viper one one cancel')
        self.clock.now += 30
        self.assertTrue(self.valid(ack))

    # 6: runway authority needs actual tower service
    def test_ground_only_airfield_never_grants_runway_authority(self):
        self.fields[0]['radios'] = [radio(121000000, ('ground',))]
        self.fields[1]['radios'] = [radio(124000000)]
        self.put('101', FINAL, in_air=True, agl_ft=1000)
        self.sync()
        self.say('Ironwood ground Viper one two inbound')
        self.say('Ironwood ground Viper one two read back runway zero nine, altimeter two niner niner two')
        self.sync()
        final = self.say('Ironwood ground Viper one two final')
        self.assertNotIn('cleared to land', final['reply'])
        self.assertIn('no tower service', final['reply'])
        self.sync()
        self.assertIsNone(self.svc._control['1']['lane'])

    def test_tower_removed_from_the_same_channel_revokes_landing_clearance(self):
        landing = self.cleared_to_land()
        self.fields[0]['radios'] = [radio(121000000, ('ground', 'approach'))]
        self.fields[1]['radios'] = [radio(124000000)]
        self.sync()
        self.assertFalse(self.valid(landing))
        self.assertIsNone(self.svc._control['1']['lane'])
        self.assertEqual(self.watch(), {('101', '1')})

    def test_tower_removed_keeps_a_lined_up_aircraft_owning_the_runway(self):
        self.lined_up_with_takeoff_clearance()
        self.fields[0]['radios'] = [radio(121000000, ('ground',))]
        self.fields[1]['radios'] = [radio(124000000)]
        self.sync()
        self.assertEqual(self.svc._sessions['100'].phase, 'lined_up')
        self.assertEqual(self.svc._control['1']['lane'], '100')

    def rolling_tower_loss(self, acknowledged):
        self.lined_up_with_takeoff_clearance()
        offer = self.say('Ironwood tower Viper one one say again')
        if acknowledged:
            offer = self.say('Ironwood tower Viper one one read back cleared for takeoff runway zero nine')
        self.put('100', LINED_UP, speed=20)
        self.sync()
        self.fields[0]['radios'] = [radio(121000000, ('ground',))]
        self.sync()
        self.assertFalse(self.valid(offer))
        self.assertEqual(self.svc._control['1']['lane'], '100')
        self.assertNotIn('cleared for takeoff', self.say('Ironwood ground Viper one one say again')['reply'])
        self.fields[0]['radios'] = [radio(121000000)]
        self.sync()
        self.assertFalse(self.valid(offer))
        self.assertNotIn('cleared for takeoff', self.say('Ironwood tower Viper one one say again')['reply'])
        self.put('100', LINED_UP, in_air=True, agl_ft=100, speed=30)
        self.sync()
        self.assertIsNone(self.svc._control['1']['lane'])

    def test_tower_loss_revokes_unread_rolling_takeoff_without_releasing_occupancy(self):
        self.rolling_tower_loss(False)

    def test_tower_loss_revokes_confirmed_rolling_takeoff_without_releasing_occupancy(self):
        self.rolling_tower_loss(True)

    # 7: explicit receivers
    def test_role_first_call_naming_another_channels_airfield_is_not_taken_by_this_one(self):
        self.fields[1]['radios'] = [radio(122000000)]
        self.sync()
        self.assertIsNone(self.say('Tower Maple Viper one one inbound')['reply'])
        self.assertEqual(self.watch(), set())

    def test_unrecognised_receiver_words_ask_again_without_enrolling(self):
        self.fields[1]['radios'] = [radio(122000000)]
        self.sync()
        answer = self.say('Tower Mapel Viper one one inbound')
        self.assertIn('airfield name', answer['reply'])
        self.assertNotIn('transfer_unit_id', answer)
        self.assertEqual(self.watch(), set())

    def test_alias_shared_by_two_airfields_is_ambiguous_and_full_alias_is_exact(self):
        self.fields[1]['radios'] = [radio(122000000, aliases=('Ironwood North',))]
        self.sync()
        self.assertIn('more than one airfield', self.say('Ironwood ground Viper one one request startup')['reply'])
        self.assertIsNone(self.say('Ironwood North ground Viper one one request startup')['reply'])
        self.assertEqual(self.watch(), set())


if __name__ == '__main__':
    unittest.main()
