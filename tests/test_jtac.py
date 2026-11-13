import unittest
from unittest.mock import patch

from overlord import jtac
from overlord.jtac import Controller

CHANNEL = '2:252000000:0'
UNITS = [
    {'id': '100', 'pilot': 'Viper 1-1 | Bob', 'callsign': 'Viper11', 'coalition': 2, 'type': 'F-16C_50',
     'category': 'Air', 'lat': 42.0, 'lon': 41.0, 'alt': 3000},
    {'id': '101', 'pilot': 'Viper 1-2 | Ann', 'callsign': 'Viper12', 'coalition': 2, 'type': 'F-16C_50',
     'category': 'Air', 'lat': 42.1, 'lon': 41.0, 'alt': 3000},
    {'id': '300', 'pilot': 'Dodge 1-1 | Ivan', 'callsign': 'Dodge11', 'coalition': 1, 'type': 'Su-25T',
     'category': 'Air', 'lat': 43.0, 'lon': 41.0, 'alt': 3000},
]
BRIEF = {'type': 2, 'ip': 'IP Dog', 'heading': 270, 'offset': 'offset left', 'distance': 6.5,
         'elevation': 450, 'description': 'three tanks on the road', 'location': 'KM 1234',
         'mark': 'laser code one six eight eight', 'friendlies': 'south three kilometers',
         'egress': 'north to IP Cat', 'remarks': 'final attack heading two four zero to three zero zero'}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def task(**changes):
    value = {'id': 1, 'name': 'armor column', 'coalition': 2,
             'controller': {'name': 'JTAC-1 Reaper', 'alive': True},
             'target': {'alive': True, 'total': 3, 'remaining': 3, 'lat': 42.2, 'lon': 41.1, 'alt': 20},
             'brief': dict(BRIEF), 'laser_code': 1688, 'marks': ['laser', 'smoke'],
             'readback': ['elevation', 'location'], 'attack_heading': [240, 300], 'require_mark': True,
             'laser': {'on': False}, 'ir': {'on': False}}
    value.update(changes)
    return value


class JtacTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(jtac.time, 'monotonic', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.jtac = Controller(callsign='Axeman', channel=CHANNEL, stale_seconds=10)
        self.seq = 0
        self.epoch = 'e1'

    def bridge(self, tasks=None, acks=(), epoch=None, seq=None):
        if epoch is not None:
            self.epoch = epoch
        self.seq = self.seq + 1 if seq is None else seq
        return self.jtac.exchange({'version': 1, 'epoch': self.epoch, 'seq': self.seq, 'time': 100.0,
                                   'tasks': [task()] if tasks is None else tasks, 'acks': list(acks)})

    def say(self, text, units=UNITS, coalition=2, channel=CHANNEL, generation=1):
        return self.jtac.respond(text, coalition, channel, units, generation)

    def brief_and_readback(self, who='Viper 1 1'):
        self.say(f'Axeman, {who}, checking in')
        self.say(f'Axeman, {who}, request task')
        self.say(f'Axeman, {who}, ready for brief')
        return self.say(f'Axeman, {who}, readback elevation four five zero, kilo mike one two three four')

    def laser_on(self, who='Viper 1 1'):
        self.say(f'Axeman, {who}, request laser')
        command = self.bridge()['commands'][0]
        self.assertEqual(command['op'], 'laser_on')
        self.bridge([task(laser={'on': True, 'command': command['id']})],
                    acks=[{'id': command['id'], 'status': 'done'}])
        return command['id']

    # -- frequency, addressing, liveness ------------------------------------------

    def test_other_channels_coalitions_and_callsigns_are_ignored(self):
        self.bridge()
        self.assertIsNone(self.say('Axeman, Viper 1 1, checking in', channel='2:251000000:0')['reply'])
        self.assertIsNone(self.say('Axeman, Dodge 1 1, checking in', coalition=1)['reply'])
        self.assertIsNone(self.say('Overlord, Viper 1 1, checking in')['reply'])
        self.assertIsNone(self.jtac.parse('Overlord, Viper 1 1, checking in', UNITS, 2))
        with self.assertRaises(ValueError):
            Controller(channel='blue-252')

    def test_parse_is_side_effect_free_and_flags_unknown_requests(self):
        self.bridge()
        parsed = self.jtac.parse('Axeman, Viper 1 1, checking in', UNITS, 2)
        self.assertEqual(parsed['intent'], 'checkIn')
        self.assertEqual(parsed['transmitter'], {'kind': 'player', 'group': 'viper', 'flight': 1, 'element': 1})
        self.assertEqual(self.jtac.parse('Axeman, Viper 1 1, nice weather', UNITS, 2)['intent'], 'None')
        self.assertEqual(self.jtac._sessions, {})
        self.assertIn('check in first', self.say('Axeman, Viper 1 1, request task')['reply'])

    def test_every_canonical_command_parses_and_no_readback_slots_are_exposed(self):
        self.assertFalse({'readback', 'repeatLine', 'inbound'} & set(Controller.commands))
        for intent, phrase in Controller.commands.items():
            parsed = self.jtac.parse(f'Axeman, Viper 1 1, {phrase}', UNITS, 2)
            self.assertEqual(parsed['intent'], intent, phrase)

    def test_no_live_bridge_means_explicit_unable_without_validation(self):
        answer = self.say('Axeman, Viper 1 1, checking in')
        self.assertIn('unable, no live mission link', answer['reply'])
        self.assertNotIn('validation', answer)
        self.bridge()
        self.clock.now += 11
        self.assertFalse(self.jtac.ready)
        self.assertIn('no live mission link', self.say('Axeman, Viper 1 1, checking in')['reply'])

    # -- procedure --------------------------------------------------------------------

    def test_full_procedure_to_clearance_and_abort(self):
        self.bridge()
        reply = self.say('Axeman, Viper 1 1, checking in')['reply']
        self.assertEqual(reply, 'viper 1 1, Axeman, checked in, available task one, armor column, '
                                'request task when ready')
        self.assertIn('task one, armor column', self.say('Axeman, Viper 1 1, request task')['reply'])
        brief = self.say('Axeman, Viper 1 1, ready for brief')['reply']
        for line in ('type two control', 'line one, IP IP Dog', 'heading two seven zero, offset left',
                     'distance six point five', 'elevation four five zero feet', 'line six, KM 1234',
                     'remarks, final attack heading', 'read back line four, line six'):
            self.assertIn(line, brief)
        self.assertLessEqual(len(brief), jtac.MAX_REPLY_CHARS)
        self.assertIn('line four, elevation four five zero feet',
                      self.say('Axeman, Viper 1 1, say again line four')['reply'])
        wrong = self.say('Axeman, Viper 1 1, readback elevation four five five, kilo mike one two three four')
        self.assertIn('negative readback, line four', wrong['reply'])
        self.assertIn('readback required', self.say('Axeman, Viper 1 1, contact')['reply'])
        correct = self.say('Axeman, Viper 1 1, readback elevation four five zero, kilo mike one two three four')
        self.assertIn('readback correct', correct['reply'])
        self.assertIn('continue, no clearance, report contact',
                      self.say('Axeman, Viper 1 1, in heading two seven zero')['reply'])
        self.assertIn('copy contact', self.say('Axeman, Viper 1 1, contact')['reply'])
        self.assertIn('attack heading two four zero to three zero zero only',
                      self.say('Axeman, Viper 1 1, in heading one eight zero')['reply'])
        self.assertIn('mark not confirmed', self.say('Axeman, Viper 1 1, in heading two seven zero')['reply'])
        command_id = self.laser_on()
        events = self.jtac.take_events(2, CHANNEL)
        self.assertEqual([e['reply'] for e in events], ['viper 1 1, Axeman, laser on, code one six eight eight'])
        self.assertEqual(self.jtac.take_events(2, CHANNEL), [])
        cleared = self.say('Axeman, Viper 1 1, in heading two seven zero')
        self.assertEqual(cleared['reply'], 'viper 1 1, Axeman, cleared hot')
        self.assertTrue(self.jtac.validate_reply(cleared['validation']))
        self.assertIn('copy abort', self.say('Axeman, Viper 1 1, abort')['reply'])
        self.assertFalse(self.jtac.validate_reply(cleared['validation']))
        response = self.bridge([task(laser={'on': True, 'command': command_id})])
        self.assertEqual([c['op'] for c in response['commands']], ['laser_off'])
        self.assertNotIn('cleared', self.say('Axeman, Viper 1 1, say status')['reply'])

    def test_readback_requires_exact_numeric_and_coordinate_runs(self):
        self.bridge([task(brief=dict(BRIEF, location='KM 1234 5678'))])
        self.say('Axeman, Viper 1 1, checking in')
        self.say('Axeman, Viper 1 1, request task')
        self.say('Axeman, Viper 1 1, ready for brief')
        for wrong, line in (
                ('elevation four five zero zero, kilo mike one two three four five six seven eight', 'line four'),
                ('elevation 4500, KM 12345678', 'line four'),
                ('elevation four five zero, kilo mike one two three four five six seven eight nine', 'line six'),
                ('elevation four five zero, kilo mike one two three four five six seven', 'line six'),
                ('elevation four five, kilo mike one two three four five six seven eight', 'line four'),
                ('kilo mike one two three four five six seven eight, elevation four five zero', 'line four')):
            reply = self.say(f'Axeman, Viper 1 1, readback {wrong}')['reply']
            self.assertIn(f'negative readback, {line}', reply, wrong)
        for right in ('elevation four hundred fifty feet, location kilo mike twelve thirty four fifty six seventy eight',
                      '450, KM 1234 5678'):
            self.assertIn('readback correct', self.say(f'Axeman, Viper 1 1, readback {right}')['reply'], right)

    def test_laser_off_or_negated_requests_never_select_a_positive_action(self):
        for text, intent in (('request laser off', 'laserOff'), ('laser off', 'laserOff'),
                             ('cancel the laser', 'laserOff'), ('negative laser', 'laserOff'),
                             ('request smoke, no laser', 'laserOff'), ('cancel the smoke mark target', 'abort'),
                             ('do not request smoke', 'None'), ('request laser', 'requestLaser'),
                             ('request smoke', 'requestMark')):
            parsed = self.jtac.parse(f'Axeman, Viper 1 1, {text}', UNITS, 2)
            self.assertEqual(parsed['intent'], intent, text)
        self.bridge()
        self.brief_and_readback()
        self.assertIn('laser is off', self.say('Axeman, Viper 1 1, request laser off')['reply'])
        self.assertEqual(self.bridge()['commands'], [])

    def readback_task(self, readback, **brief):
        self.setUp()
        self.bridge([task(brief=dict(BRIEF, **brief), readback=readback)])
        self.say('Axeman, Viper 1 1, checking in')
        self.say('Axeman, Viper 1 1, request task')
        self.say('Axeman, Viper 1 1, ready for brief')
        return lambda text: self.say(f'Axeman, Viper 1 1, readback {text}')['reply']

    def test_typed_readback_compares_complete_signed_decimal_values(self):
        cases = [
            (['distance'], {}, 'six five', False), (['distance'], {}, 'six point five', True),
            (['distance'], {}, '6.5', True), (['distance'], {}, 'sixty five', False),
            (['elevation'], {}, 'minus four five zero', False), (['elevation'], {}, 'four five zero zero', False),
            (['elevation'], {'elevation': -120}, 'minus one two zero', True),
            (['elevation'], {'elevation': -120}, 'one two zero', False),
            (['heading', 'elevation'], {}, 'heading two seven zero elevation four five zero', True),
            (['heading', 'elevation'], {}, 'two seven zero, four five zero', False),
            (['heading', 'elevation'], {}, 'heading two seven zero elevation four five zero zero', False),
            (['location'], {}, 'kilo mike one two three four', True),
            (['location'], {}, 'kilo mike one two three four five', False),
            (['location'], {}, 'kilo one two three four', False),
            (['elevation'], {}, 'elevation four five five, elevation four five zero', False),
        ]
        for readback, brief, text, accepted in cases:
            with self.subTest(text=text, readback=readback):
                reply = self.readback_task(readback, **brief)(text)
                self.assertIn('readback correct' if accepted else 'negative readback', reply)

    def test_negated_statements_never_advance_procedure(self):
        self.bridge()
        self.brief_and_readback()
        for text in ('negative contact', 'no tally', 'not contact', 'lost contact', 'unable in heading two seven zero',
                     'not ready for brief', 'negative, request laser'):
            self.assertIn('say again', self.say(f'Axeman, Viper 1 1, {text}')['reply'], text)
        status = self.say('Axeman, Viper 1 1, say status')['reply']
        self.assertNotIn('contact', status)
        self.assertEqual(self.bridge()['commands'], [])

    def test_abort_during_stale_link_cancels_work_before_reconnect(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request laser')
        command = self.bridge()['commands'][0]
        self.clock.now += 11
        reply = self.say('Axeman, Viper 1 1, abort')
        self.assertIn('copy abort', reply['reply'])
        self.assertIn('no live mission link', reply['reply'])
        self.assertNotIn('validation', reply)
        response = self.bridge()
        self.assertNotIn(command['id'], [c['id'] for c in response['commands']])
        self.assertIn(command['id'], response['cancel'])
        self.assertEqual([c['op'] for c in response['commands']], ['laser_off'])
        self.assertIn('awaiting brief', self.say('Axeman, Viper 1 1, say status')['reply'])

    def test_stale_gap_or_target_loss_requires_new_brief_readback_and_contact(self):
        for change in ('gap', 'target'):
            with self.subTest(change):
                self.setUp()
                self.bridge([task(require_mark=False, attack_heading=None)])
                self.brief_and_readback()
                self.say('Axeman, Viper 1 1, contact')
                if change == 'gap':
                    self.clock.now += 11
                    self.bridge([task(require_mark=False, attack_heading=None)])
                else:
                    self.bridge([task(require_mark=False, attack_heading=None,
                                      target={'alive': False, 'total': 3, 'remaining': 0})])
                    self.bridge([task(require_mark=False, attack_heading=None)])
                self.assertIn('readback required', self.say('Axeman, Viper 1 1, in')['reply'])
                self.assertIn('no brief issued',
                              self.say('Axeman, Viper 1 1, readback elevation four five zero, kilo mike one two '
                                       'three four')['reply'])

    def test_spot_acknowledgement_is_valid_only_while_that_spot_is_live(self):
        self.bridge([task(marks=['laser', 'ir'])])
        self.brief_and_readback()
        command_id = self.laser_on()
        event = self.jtac.take_events(2, CHANNEL)[0]
        self.assertEqual(event['validation']['spot'], command_id)
        self.assertTrue(self.jtac.validate_reply(event['validation']))
        self.bridge([task(marks=['laser', 'ir'])])
        self.assertFalse(self.jtac.validate_reply(event['validation']), 'spot gone before playback')
        self.say('Axeman, Viper 1 1, request sparkle')
        ir = self.bridge([task(marks=['laser', 'ir'])])['commands'][0]
        self.assertEqual((ir['op'], ir['kind']), ('mark', 'ir'))
        self.bridge([task(marks=['laser', 'ir'], ir={'on': True, 'command': ir['id']})],
                    acks=[{'id': ir['id'], 'status': 'done'}])
        self.say('Axeman, Viper 1 1, contact')
        cleared = self.say('Axeman, Viper 1 1, in heading two seven zero')
        self.assertIn('cleared hot', cleared['reply'])
        self.assertTrue(self.jtac.validate_reply(cleared['validation']))
        self.bridge([task(marks=['laser', 'ir'])])
        self.assertFalse(self.jtac.validate_reply(cleared['validation']), 'required live mark lost')
        self.assertNotIn('cleared', self.say('Axeman, Viper 1 1, say status')['reply'])
        self.assertIn('mark not confirmed', self.say('Axeman, Viper 1 1, in heading two seven zero')['reply'])

    def test_acknowledged_smoke_keeps_mark_dependent_clearance(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request smoke')
        smoke = self.bridge()['commands'][0]
        self.bridge(acks=[{'id': smoke['id'], 'status': 'done'}])
        self.say('Axeman, Viper 1 1, contact')
        cleared = self.say('Axeman, Viper 1 1, in heading two seven zero')
        self.assertIn('cleared hot', cleared['reply'])
        self.bridge()
        self.assertTrue(self.jtac.validate_reply(cleared['validation']))

    def test_trailing_abort_or_cancel_overrides_the_whole_request(self):
        for text in ('request laser, abort', 'in heading two seven zero, abort abort abort',
                     'contact, cancel', 'request smoke, belay that'):
            parsed = self.jtac.parse(f'Axeman, Viper 1 1, {text}', UNITS, 2)
            self.assertEqual(parsed['intent'], 'abort', text)
            self.assertEqual(parsed['transmitter'], {'kind': 'player', 'group': 'viper', 'flight': 1, 'element': 1})
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request laser, abort')
        self.assertEqual(self.bridge()['commands'], [], 'no switch-on queued')

    def test_negated_or_rejected_requests_are_blocked_from_inference(self):
        for text, intent, blocked in (('negative contact', 'None', True), ('contact, negative', 'None', True),
                                      ('not ready for brief', 'None', True), ('no joy', 'None', True),
                                      ('unable', 'None', True), ('nice weather', 'None', False),
                                      ('say status', 'status', False)):
            parsed = self.jtac.parse(f'Axeman, Viper 1 1, {text}', UNITS, 2)
            self.assertEqual((parsed['intent'], parsed['blocked']), (intent, blocked), text)

    def test_literal_signs_and_decimals_in_readback(self):
        cases = [
            (['elevation'], {}, '-450', False), (['elevation'], {}, '+450', True), (['elevation'], {}, '450', True),
            (['elevation'], {'elevation': -120}, '-120', True), (['elevation'], {'elevation': -120}, '+120', False),
            (['elevation'], {'elevation': -120}, 'negative one two zero', True),
            (['distance'], {}, '+6.5', True), (['distance'], {}, '6.50', True), (['distance'], {}, '-6.5', False),
            (['distance'], {}, '65', False),
        ]
        for readback, brief, text, accepted in cases:
            with self.subTest(text=text, brief=brief):
                reply = self.readback_task(readback, **brief)(text)
                self.assertIn('readback correct' if accepted else 'negative readback', reply)

    def test_safety_cancel_validation_is_bound_to_the_cancellation_reply(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, contact')
        command_id = self.laser_on()
        cleared = self.say('Axeman, Viper 1 1, in heading two seven zero')
        forged = dict(cleared['validation'], safety_cancel=True, action='abort', target=False)
        self.assertFalse(self.jtac.validate_reply(forged), 'clearance cannot pose as a cancellation')
        abort = self.say('Axeman, Viper 1 1, abort')
        self.assertEqual((abort['validation']['safety_cancel'], abort['validation']['action']), (True, 'abort'))
        self.assertTrue(self.jtac.validate_reply(abort['validation']))
        self.assertFalse(self.jtac.validate_reply(dict(abort['validation'], action='laserOff')))
        self.assertFalse(self.jtac.validate_reply(dict(abort['validation'], generation=99)))
        self.assertFalse(self.jtac.validate_reply(dict(abort['validation'], unit_id='101')))
        self.assertFalse(self.jtac.validate_reply(cleared['validation']))
        self.bridge([task(laser={'on': True, 'command': command_id})])
        status = self.say('Axeman, Viper 1 1, say status')
        self.assertFalse(status['validation']['safety_cancel'])
        self.assertFalse(self.jtac.validate_reply(abort['validation']), 'superseded by a newer transmission')

    def test_laser_off_while_switch_on_in_flight_is_pending_not_off(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request laser')
        command = self.bridge()['commands'][0]
        self.assertEqual(self.say('Axeman, Viper 1 1, laser off')['reply'],
                         'viper 1 1, Axeman, copy, laser off pending')
        response = self.bridge()
        self.assertEqual([c['op'] for c in response['commands']], ['laser_off'])
        self.assertEqual(response['cancel'], [command['id']])
        off = response['commands'][0]
        self.bridge(acks=[{'id': command['id'], 'status': 'cancelled'}, {'id': off['id'], 'status': 'done'}])
        self.assertEqual([e['reply'] for e in self.jtac.take_events(2, CHANNEL)], ['viper 1 1, Axeman, laser off'])
        self.assertEqual(self.say('Axeman, Viper 1 1, laser off')['reply'], 'viper 1 1, Axeman, laser is off')

    def test_bda_needs_affirmative_destruction_evidence(self):
        self.bridge()
        self.brief_and_readback()
        self.bridge([task(target={'alive': False, 'total': 3, 'remaining': 0})])
        self.assertIn('BDA unconfirmed, target not observed', self.say('Axeman, Viper 1 1, request bda')['reply'])
        self.bridge([task(target={'alive': False, 'total': 3, 'remaining': 0, 'destroyed': True})])
        self.assertIn('BDA, target destroyed', self.say('Axeman, Viper 1 1, request bda')['reply'])

    def test_pilot_claim_of_clearance_is_not_a_clearance(self):
        self.bridge()
        self.brief_and_readback()
        self.assertIn('say again', self.say('Axeman, Viper 1 1, cleared hot')['reply'])
        self.assertNotIn('cleared', self.say('Axeman, Viper 1 1, say status')['reply'])

    def test_type_three_and_unrestricted_task_clear_to_engage(self):
        self.bridge([task(brief=dict(BRIEF, type=3), attack_heading=None, require_mark=False)])
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, contact')
        cleared = self.say('Axeman, Viper 1 1, in')
        self.assertEqual(cleared['reply'], 'viper 1 1, Axeman, cleared to engage')
        self.assertIn('copy off, request BDA', self.say('Axeman, Viper 1 1, off target')['reply'])
        self.assertFalse(self.jtac.validate_reply(cleared['validation']))
        self.assertNotIn('cleared', self.say('Axeman, Viper 1 1, say status')['reply'])
        self.bridge([task(brief=dict(BRIEF, type=3), attack_heading=None, require_mark=False,
                          target={'alive': True, 'total': 3, 'remaining': 2, 'lat': 42.2, 'lon': 41.1})])
        self.assertIn('BDA, two of three target elements remain',
                      self.say('Axeman, Viper 1 1, request bda')['reply'])

    def test_missing_authored_line_blocks_brief(self):
        brief = dict(BRIEF)
        del brief['elevation']
        self.bridge([task(brief=brief)])
        self.say('Axeman, Viper 1 1, checking in')
        self.say('Axeman, Viper 1 1, request task one')
        reply = self.say('Axeman, Viper 1 1, ready for brief')['reply']
        self.assertIn('missing line four', reply)
        self.assertIn('no brief issued', self.say('Axeman, Viper 1 1, readback elevation four five zero')['reply'])

    def test_longest_authored_brief_fits_one_transmission(self):
        brief = {key: 'x' * limit for key, limit in jtac.TEXT_LIMITS.items()}
        brief.update(type=2, heading=359, distance=99.9, elevation=-1499)
        self.bridge([task(brief=brief, name='n' * 40, readback=['elevation', 'location', 'remarks', 'egress'])])
        self.say('Axeman, Viper 1 1, checking in')
        self.say('Axeman, Viper 1 1, request task')
        reply = self.say('Axeman, Viper 1 1, ready for brief')['reply']
        self.assertLessEqual(len(reply), jtac.MAX_REPLY_CHARS)
        self.assertIn('x' * 160, reply)

    def test_tasks_are_exclusive_per_pilot_and_coalition(self):
        self.bridge([task(), task(id=2, name='red task', coalition=1)])
        self.brief_and_readback('Viper 1 1')
        self.say('Axeman, Viper 1 2, checking in')
        self.assertIn('no tasks available', self.say('Axeman, Viper 1 2, request task')['reply'])
        self.assertIn('task one not available', self.say('Axeman, Viper 1 2, request task one')['reply'])
        self.assertEqual(self.say('Axeman, Viper 1 2, abort')['reply'], 'viper 1 2, Axeman, copy abort')
        self.assertIn('readback complete', self.say('Axeman, Viper 1 1, say status')['reply'])

    def test_unknown_or_duplicate_caller_is_refused(self):
        self.bridge()
        self.assertIn('could not find you', self.say('Axeman, Viper 3 1, checking in')['reply'])
        twins = UNITS + [dict(UNITS[0], id='999')]
        self.assertIn('more than one unit', self.say('Axeman, Viper 1 1, checking in', units=twins)['reply'])

    # -- bridge lifecycle -------------------------------------------------------------

    def test_sequence_replay_and_epoch_reset(self):
        self.bridge()
        self.brief_and_readback()
        stale = self.bridge(seq=self.seq)
        self.assertFalse(stale['accepted'])
        self.assertEqual(stale['commands'], [])
        answer = self.say('Axeman, Viper 1 1, say status')
        self.assertTrue(self.jtac.validate_reply(answer['validation']))
        self.bridge(epoch='e2', seq=1)
        self.assertFalse(self.jtac.validate_reply(answer['validation']))
        self.assertIn('check in first', self.say('Axeman, Viper 1 1, say status')['reply'])

    def test_cancelled_pending_laser_is_never_reported_and_late_spot_is_switched_off(self):
        self.bridge()
        self.brief_and_readback()
        self.assertIn('pending', self.say('Axeman, Viper 1 1, request laser')['reply'])
        command = self.bridge()['commands'][0]
        self.assertEqual(self.say('Axeman, Viper 1 1, abort')['reply'],
                         'viper 1 1, Axeman, copy abort, laser off pending')
        response = self.bridge()
        self.assertEqual([c['op'] for c in response['commands']], ['laser_off'], 'in-flight switch-on is stopped')
        self.assertEqual(response['cancel'], [command['id']])
        late = self.bridge([task(laser={'on': True, 'command': command['id']})],
                           acks=[{'id': command['id'], 'status': 'done'}])
        self.assertEqual(self.jtac.take_events(2, CHANNEL), [])
        self.assertEqual([c['op'] for c in late['commands']], ['laser_off'])
        self.assertEqual(late['cancel'], [])

    def test_unacknowledged_request_expires_with_failure_event(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request mark')
        command = self.bridge()['commands'][0]
        self.assertEqual((command['op'], command['kind']), ('mark', 'smoke'))
        self.assertGreater(command['deadline'], 100)
        self.clock.now += 8          # bridge stays fresh while the acknowledgement never arrives
        self.assertEqual(self.bridge()['commands'][0]['id'], command['id'])
        self.clock.now += jtac.COMMAND_TTL - 7
        response = self.bridge()
        self.assertEqual(response['commands'], [])
        self.assertEqual(response['cancel'], [command['id']])
        self.assertIn('unable mark, no acknowledgement', self.jtac.take_events(2, CHANNEL)[0]['reply'])

    def test_failed_acknowledgement_is_reported_not_success(self):
        self.bridge()
        self.brief_and_readback()
        self.say('Axeman, Viper 1 1, request laser')
        command = self.bridge()['commands'][0]
        self.bridge(acks=[{'id': command['id'], 'status': 'failed', 'reason': 'no line of sight'}])
        self.assertEqual([e['reply'] for e in self.jtac.take_events(2, CHANNEL)],
                         ['viper 1 1, Axeman, unable laser, no line of sight'])

    def test_target_controller_and_staleness_invalidate_clearance(self):
        for change in ('target', 'controller', 'stale', 'generation', 'checkout'):
            with self.subTest(change):
                self.setUp()
                self.bridge()
                self.brief_and_readback()
                self.say('Axeman, Viper 1 1, contact')
                command_id = self.laser_on()
                cleared = self.say('Axeman, Viper 1 1, in heading two seven zero')
                self.assertTrue(self.jtac.validate_reply(cleared['validation']))
                if change == 'target':
                    self.bridge([task(target={'alive': False, 'total': 3, 'remaining': 0, 'destroyed': True})])
                    self.assertIn('target destroyed', self.say('Axeman, Viper 1 1, request bda')['reply'])
                elif change == 'controller':
                    self.bridge([task(controller={'name': 'JTAC-1 Reaper', 'alive': False},
                                      laser={'on': True, 'command': command_id})])
                elif change == 'stale':
                    self.clock.now += 11
                elif change == 'generation':
                    self.jtac.reset_generation(2)
                    response = self.bridge([task(laser={'on': True, 'command': command_id})])
                    self.assertEqual([c['op'] for c in response['commands']], ['laser_off'])
                else:
                    self.say('Axeman, Viper 1 1, checking out')
                self.assertFalse(self.jtac.validate_reply(cleared['validation']))

    def test_mission_side_spot_loss_is_announced(self):
        self.bridge()
        self.brief_and_readback()
        self.laser_on()
        self.jtac.take_events(2, CHANNEL)
        self.bridge([task(laser={'on': False})])
        self.assertEqual([e['reply'] for e in self.jtac.take_events(2, CHANNEL)],
                         ['viper 1 1, Axeman, spot terminated'])

    def test_departed_pilot_session_is_closed(self):
        self.bridge()
        self.brief_and_readback()
        answer = self.say('Axeman, Viper 1 1, say status')
        self.say('Axeman, Viper 1 2, checking in', units=UNITS[1:])
        self.assertFalse(self.jtac.validate_reply(answer['validation']))

    def test_malformed_bridge_payloads_are_rejected(self):
        bad_tasks = [
            task(laser_code=1999), task(brief=dict(BRIEF, script='os.exit()')), task(id=True),
            task(marks=['napalm']), task(readback=['type']), task(name='x' * 41),
            task(brief=dict(BRIEF, remarks='bad\x07text')), task(attack_heading=[10]),
        ]
        for bad in bad_tasks:
            with self.assertRaises(ValueError):
                self.bridge([bad])
        with self.assertRaises(ValueError):
            self.bridge([task(), task()])
        with self.assertRaises(ValueError):
            self.bridge([task(id=i) for i in range(1, 40)])
        with self.assertRaises(ValueError):
            self.jtac.exchange({'version': 1, 'epoch': 'bad epoch', 'seq': 1, 'time': 0, 'tasks': []})
        with self.assertRaises(ValueError):
            self.bridge(acks=[{'id': 'x', 'status': 'executed'}])
        self.assertTrue(self.bridge([task(marks={}, readback=[], laser=[])])['accepted'])

    def test_unhashable_huge_and_non_finite_values_are_value_errors_without_mutation(self):
        self.bridge()
        answer = self.brief_and_readback()
        seq = self.seq
        nan, inf = float('nan'), float('inf')
        bad_payloads = [
            {'tasks': [task(readback=[{}])]}, {'tasks': [task(marks=[{}])]},
            {'acks': [{'id': 'x', 'status': []}]}, {'acks': [{'id': 'x', 'status': {}}]},
            {'seq': 10 ** 1000}, {'time': 10 ** 1000}, {'time': nan}, {'time': inf},
            {'tasks': [task(brief=dict(BRIEF, heading=10 ** 1000))]},
            {'tasks': [task(target={'alive': True, 'total': 3, 'remaining': 3, 'lat': nan, 'lon': 41.0})]},
            {'tasks': [task(), task(laser_code=-inf)]},
        ]
        for changes in bad_payloads:
            payload = {'version': 1, 'epoch': 'e1', 'seq': seq + 1, 'time': 100.0, 'tasks': [task()], 'acks': []}
            payload.update(changes)
            with self.assertRaises(ValueError, msg=str(changes)[:80]):
                self.jtac.exchange(payload)
        self.assertTrue(self.jtac.validate_reply(answer['validation']), 'rejected snapshots changed nothing')
        self.assertEqual(self.jtac.exchange({'version': 1, 'epoch': 'e1', 'seq': seq + 1, 'time': 101.0,
                                             'tasks': [task()]})['seq'], seq + 1)

    def test_sequence_is_echoed_and_replays_are_refused(self):
        first = self.bridge()
        self.assertEqual((first['accepted'], first['epoch'], first['seq']), (True, 'e1', 1))
        self.assertEqual(self.bridge(seq=5)['seq'], 5)
        for replay in (5, 4):
            refused = self.bridge(seq=replay)
            self.assertEqual((refused['accepted'], refused['error'], refused['seq']),
                             (False, 'stale sequence', replay))
        self.jtac.reset()
        self.assertFalse(self.bridge(seq=5)['accepted'], 'reset keeps the replay boundary')
        self.assertTrue(self.bridge(seq=6)['accepted'])

    def test_retired_epoch_snapshot_cannot_resurrect_a_finished_mission(self):
        self.bridge(seq=5)
        self.bridge([task(name='new mission task')], epoch='e2', seq=1)
        answer = self.brief_and_readback()
        self.assertIn('readback correct', answer['reply'])
        for replay_seq in (5, 6, 100):
            refused = self.bridge([task(name='old mission task')], epoch='e1', seq=replay_seq)
            self.assertEqual((refused['accepted'], refused['error']), (False, 'retired epoch'))
        self.assertTrue(self.jtac.validate_reply(answer['validation']))
        self.assertIn('new mission task', self.say('Axeman, Viper 1 1, say status')['reply'])
        self.jtac.reset()
        self.assertFalse(self.bridge(epoch='e1', seq=7)['accepted'], 'telemetry reset keeps retired epochs')
        self.assertTrue(self.bridge(epoch='e2', seq=2)['accepted'])

    def test_reset_drops_bridge_and_sessions(self):
        self.bridge()
        answer = self.say('Axeman, Viper 1 1, checking in')
        self.jtac.reset()
        self.assertFalse(self.jtac.ready)
        self.assertFalse(self.jtac.validate_reply(answer['validation']))


if __name__ == '__main__':
    unittest.main()
