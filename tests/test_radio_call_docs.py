"""Documented pilot calls must parse and reproduce actual clearance values."""
import html
import json
from pathlib import Path
import re
import unittest

from overlord import airfield, airfield_parser, carrier, carrier_parser, jtac


GUIDES = Path(__file__).resolve().parents[1] / 'docs' / 'radio-calls'
FENCES = re.compile(r'^```mermaid\s*\n([\s\S]*?)^```\s*$', re.MULTILINE)
LABELS = re.compile(r'\b([A-Za-z][A-Za-z0-9_-]*)\["((?:[^"\\]|\\.)*)"\]')
PLACEHOLDERS = re.compile(r'\[([A-Z][A-Z0-9_]*)\]')
HEARD = re.compile(r'^\s*([A-Za-z]\w*)\(\["(.*)"\]\)\s*$', re.MULTILINE)


def call_labels(chart):
    result = {}
    for ident, raw in LABELS.findall(chart):
        label = html.unescape(raw.replace('\\"', '"'))
        spoken = re.fullmatch(r'`YOU:\s+\*\*(.+)\*\*`', label)
        if spoken:
            result[ident] = spoken[1]
    return result


def expect_fields(test, actual, expected):
    for name, value in expected.items():
        name = {'receiver_role': 'receiver.role', 'receiver_named': 'receiver.named',
                'receiver_fields': 'receiver.fields'}.get(name, name)
        current = actual
        for part in name.split('.'):
            current = current[part]
        if isinstance(value, dict):
            expect_fields(test, current, value)
        else:
            test.assertEqual(current, value, name)


def fill(template, values):
    return PLACEHOLDERS.sub(lambda match: str(values[match[1]]), template)


def heard_patterns(chart):
    """Each REPLY/EVENT node's spoken alternatives as regexes; placeholders match any text."""
    result = {}
    for ident, raw in HEARD.findall(chart):
        body = raw.split(':', 1)[1].strip()
        alternatives = [part.split(' — ')[0] for part in body.split(' — or — ')]
        result[ident] = [re.compile('.+'.join(map(re.escape, PLACEHOLDERS.split(alt)[::2])))
                         for alt in alternatives]
    return result


class RadioCallDocumentationTests(unittest.TestCase):
    def test_documented_calls_match_grammar_and_clearances(self):
        for family, parser in [('airfield', airfield_parser), ('carrier', carrier_parser)]:
            manifest = json.loads((GUIDES / f'{family}-calls.json').read_text())
            charts = FENCES.findall((GUIDES / f'{family}.md').read_text())
            self.assertEqual(len(charts), 2, family)
            documented_intents = set()
            all_ids = set()
            for source, chart in zip(charts, manifest['charts'], strict=True):
                labels = call_labels(source)
                self.assertEqual(set(labels), {node['id'] for node in chart['nodes']}, chart['id'])
                for node in chart['nodes']:
                    with self.subTest(family=family, chart=chart['id'], node=node['id']):
                        template = labels[node['id']]
                        values = node['values']
                        call = PLACEHOLDERS.sub(lambda m: str(values[m[1]]), template)
                        self.assertNotRegex(call, PLACEHOLDERS)
                        if family == 'airfield':
                            field = values.get('FIELD', 'Kutaisi')
                            parsed = parser.parse(call, {'1': parser.name_keys(field)})
                        else:
                            ship = values.get('OTHER_CARRIER') if '[OTHER_CARRIER]' in template else values.get('CARRIER')
                            names = parser.name_words(ship or 'Theodore Roosevelt')
                            parsed = parser.parse(call, names)
                        self.assertIsNotNone(parsed, call)
                        self.assertEqual(parsed['intent'], node['intent'], call)
                        self.assertFalse(parsed['blocked'], call)
                        self.assertTrue(parsed['transmitter'] or parsed['modex'], call)
                        expect_fields(self, parsed, node.get('expected_fields', {}))
                        if family == 'airfield' and 'readback_pending' in node:
                            self.assertIsNone(airfield.readback_problem(parsed['readback'], node['readback_pending']), call)
                        if node['intent'] == 'readback' and 'carrier_assignment' in node:
                            self.assertIsNone(carrier._readback_mismatch(parsed['readback'], node['carrier_assignment']), call)
                        documented_intents.add(node['intent'])
                        all_ids.add(node['id'])
            self.assertEqual(documented_intents, set(parser.INTENTS), family)
            self.assertEqual(set(manifest['intent_coverage']), set(parser.INTENTS), family)
            for intent, ids in manifest['intent_coverage'].items():
                self.assertTrue(ids, intent)
                self.assertLessEqual(set(ids), all_ids, intent)

    def test_documented_jtac_calls_match_grammar_and_fixture_values(self):
        from tests import test_jtac as fixtures

        manifest = json.loads((GUIDES / 'jtac-calls.json').read_text())
        charts = FENCES.findall((GUIDES / 'jtac.md').read_text())
        self.assertEqual(len(charts), 1)
        controller = jtac.Controller(callsign='Axeman', channel=fixtures.CHANNEL)
        task = jtac._task(fixtures.task())
        intents = {intent for intent, _ in jtac.COMMANDS}
        documented, all_ids = set(), set()
        for source, chart in zip(charts, manifest['charts'], strict=True):
            labels = call_labels(source)
            self.assertEqual(set(labels), {node['id'] for node in chart['nodes']}, chart['id'])
            for node in chart['nodes']:
                with self.subTest(chart=chart['id'], node=node['id']):
                    call = fill(labels[node['id']], node['values'])
                    self.assertNotRegex(call, PLACEHOLDERS)
                    parsed = controller.parse(call, fixtures.UNITS, 2)
                    self.assertIsNotNone(parsed, call)
                    self.assertEqual(parsed['intent'], node['intent'], call)
                    self.assertFalse(parsed['blocked'], call)
                    self.assertEqual(parsed['transmitter'],
                                     {'kind': 'player', 'group': 'viper', 'flight': 1, 'element': 1}, call)
                    expect_fields(self, parsed, node.get('expected_fields', {}))
                    if node['intent'] == 'readback':
                        self.assertIsNone(jtac._readback_mismatch(parsed['readback'], task), call)
                    if parsed['heading'] is not None:
                        self.assertTrue(jtac._heading_ok(parsed['heading'], task['attack_heading']), call)
                    documented.add(node['intent'])
                    all_ids.add(node['id'])
        self.assertEqual(documented, intents)
        self.assertEqual(set(manifest['intent_coverage']), intents)
        for intent, ids in manifest['intent_coverage'].items():
            self.assertTrue(ids, intent)
            self.assertLessEqual(set(ids), all_ids, intent)

    def test_jtac_chart_replies_match_the_controller(self):
        # Replays the chart's main path and its refusals against the real controller: every
        # reply and event the pilot hears must be one the chart prints at that step.
        from tests import test_jtac as fixtures

        manifest = json.loads((GUIDES / 'jtac-calls.json').read_text())
        nodes = {node['id']: node for chart in manifest['charts'] for node in chart['nodes']}
        source = FENCES.findall((GUIDES / 'jtac.md').read_text())[0]
        labels, heard = call_labels(source), heard_patterns(source)
        fixture = fixtures.JtacTests('test_full_procedure_to_clearance_and_abort')
        fixture.setUp()
        prefix = 'viper 1 1, Axeman, '

        def expect(text, node_id):
            self.assertTrue(text.startswith(prefix), text)
            message = text[len(prefix):]
            self.assertTrue(any(pattern.fullmatch(message) for pattern in heard[node_id]), (node_id, message))
            return text

        def call(node_id, expected, **values):
            return expect(fixture.say(fill(labels[node_id], nodes[node_id]['values'] | values))['reply'], expected)

        def repeated(previous):
            self.assertEqual(call('J_SAYAGAIN', 'J_SAYAGAIN_R'), previous)

        def events(*expected):
            spoken = [event['reply'] for event in fixture.jtac.take_events(2, fixtures.CHANNEL)]
            self.assertEqual(len(spoken), len(expected), spoken)
            for text, node_id in zip(spoken, expected):
                expect(text, node_id)

        try:
            fixture.bridge()
            call('J_REQ_TASK', 'J_G_NOSESSION')
            call('J_CHECKIN', 'J_CHECKIN_R')
            call('J_BRIEF', 'J_BRIEF_NOTASK')
            call('J_REQ_TASK_N', 'J_TASK_NA', TASK='three')
            call('J_REQ_TASK', 'J_TASK_R')
            call('J_REPEAT', 'J_NOBRIEF')
            call('J_BRIEF', 'J_BRIEF_R')
            call('J_REPEAT', 'J_REPEAT_R')
            call('J_REPEAT_RMK', 'J_REPEAT_R')
            call('J_CONTACT', 'J_CONTACT_NORB')
            call('J_RB', 'J_RB_BAD', READBACK_VALUES='elevation four five five, kilo mike one two three four')
            call('J_RB', 'J_RB_OK')
            call('J_IN_HDG', 'J_NOCLR')
            call('J_CONTACT', 'J_CONTACT_HDG')
            call('J_IN', 'J_NOCLR')
            call('J_IN_HDG', 'J_NOCLR')
            call('J_MARK', 'J_MARK_NA', MARK_KIND='illumination')
            call('J_LASER', 'J_LASER_PEND')
            laser = fixture.bridge()['commands'][0]
            lasing = fixtures.task(laser={'on': True, 'command': laser['id']})
            fixture.bridge([lasing], acks=[{'id': laser['id'], 'status': 'done'}])
            events('J_EV_LASER')
            call('J_LASER', 'J_MARK_LIVE')
            call('J_STATUS', 'J_STATUS_R')
            call('J_IN_HDG', 'J_CLEARED')
            call('J_OFF', 'J_OFF_R')
            call('J_OFF', 'J_OFF_NOCLR')
            call('J_BDA', 'J_BDA_REM')
            call('J_MARK', 'J_MARK_PEND')
            smoke = next(c for c in fixture.bridge([lasing])['commands'] if c['op'] == 'mark')
            fixture.bridge([lasing], acks=[{'id': smoke['id'], 'status': 'done'}])
            events('J_EV_MARK')
            call('J_LOFF', 'J_LOFF_PEND')
            off = next(c for c in fixture.bridge([lasing])['commands'] if c['op'] == 'laser_off')
            fixture.bridge(acks=[{'id': off['id'], 'status': 'done'}])
            events('J_EV_LOFF')
            call('J_LOFF', 'J_LOFF_OFF')
            repeated(call('J_ABORT', 'J_ABORT_R'))
            repeated(call('J_CHECKIN', 'J_CHECKIN_AGAIN'))
            call('J_REQ_TASK_N', 'J_TASK_NEXT')
            call('J_LASER', 'J_LASER_PEND')
            laser = fixture.bridge()['commands'][0]
            lasing = fixtures.task(laser={'on': True, 'command': laser['id']})
            fixture.bridge([lasing], acks=[{'id': laser['id'], 'status': 'done'}])
            events('J_EV_LASER')
            self.assertIn('laser off pending', call('J_ABORT', 'J_ABORT_R'))
            off = next(c for c in fixture.bridge([lasing])['commands'] if c['op'] == 'laser_off')
            fixture.bridge(acks=[{'id': off['id'], 'status': 'done'}])
            events('J_EV_LOFF')
            call('J_MARK', 'J_MARK_PEND')
            for _ in range(5):
                fixture.clock.now += 4
                fixture.bridge()
            events('J_EV_FAIL')
            call('J_CONTACT', 'J_CONTACT_HDG')
            call('J_MARK', 'J_MARK_PEND')
            smoke = fixture.bridge()['commands'][0]
            fixture.bridge(acks=[{'id': smoke['id'], 'status': 'done'}])
            events('J_EV_MARK')
            call('J_IN_HDG', 'J_CLEARED')
            fixture.bridge([fixtures.task(target={'alive': False, 'total': 3, 'remaining': 0, 'destroyed': True})])
            events('J_EV_TGT')
            call('J_CONTACT', 'J_LOST_R')
            call('J_IN', 'J_NOCLR')
            repeated(call('J_STATUS', 'J_STATUS_LOST'))
            call('J_BRIEF', 'J_BRIEF_DEAD')
            call('J_OFF', 'J_OFF_R')
            call('J_BDA', 'J_BDA_KILL')
            fixture.bridge()
            call('J_BRIEF', 'J_BRIEF_R')
            call('J_RB', 'J_RB_OK')
            call('J_CONTACT', 'J_CONTACT_HDG')
            fixture.bridge([fixtures.task(controller={'name': 'JTAC-1 Reaper', 'alive': False})])
            events('J_EV_CTRL')
            call('J_SAYAGAIN', 'J_STATUS_LOST')
            call('J_RB', 'J_LOST_R')
            fixture.clock.now += 11
            call('J_STATUS', 'J_G_STALE')
            call('J_ABORT', 'J_STALE_CXL')
            call('J_CHECKOUT', 'J_CHECKOUT_R')
            call('J_STATUS', 'J_G_STALE')
            fixture.bridge()
            call('J_STATUS', 'J_G_NOSESSION')
        finally:
            fixture.doCleanups()

    def test_tower_status_preserves_takeoff_but_ordinary_mayday_aborts(self):
        from tests import test_airfield as fixtures

        manifest = json.loads((GUIDES / 'airfield-calls.json').read_text())
        nodes = {node['id']: node for chart in manifest['charts'] for node in chart['nodes']}
        labels = {}
        for source in FENCES.findall((GUIDES / 'airfield.md').read_text()):
            labels.update(call_labels(source))
        fixture = fixtures.Base()
        fixture.setUp()
        try:
            fixture.fields[0]['radios'] = [
                fixtures.radio(121000000, ('ground',)),
                fixtures.radio(122000000, ('tower',)),
            ]
            fixture.fields[1]['radios'] = [fixtures.radio(124000000)]
            fixture.sync()
            fixture.to_hold()

            def transmit(node_id):
                values = nodes[node_id]['values'] | {'CURRENT_ROLE': 'tower'}
                call = PLACEHOLDERS.sub(lambda match: str(values[match[1]]), labels[node_id])
                return fixture.say(call, channel=fixtures.TOWER)['reply']

            self.assertIn('line up and wait', transmit('D_READY_DEP'))
            self.assertIn('readback correct', transmit('D_LU_RB'))
            fixture.put('100', fixtures.LINED_UP)
            fixture.sync()
            transmit('D_LINEDUP')
            self.assertIn('readback correct', transmit('D_TO_RB'))
            self.assertIn('cleared for takeoff', transmit('D_SAYAGAIN'))
            self.assertIn('takeoff aborted', transmit('D_EMER'))
        finally:
            fixture.doCleanups()

    def test_walkthrough_starts_and_chart_links_name_existing_steps(self):
        # The interactive walkthrough (docs/radio-calls/guide.js) reads these charts; a renamed step
        # would strand pilots at a dead link or an unloadable procedure.
        manifest = json.loads((GUIDES / 'walkthrough.json').read_text())
        steps = {}
        for spec in manifest['charts']:
            chart = FENCES.findall((GUIDES / spec['source']).read_text())[spec['index']]
            steps[spec['id']] = set(re.findall(r'^\s*([A-Za-z]\w*)(?:\(\(|\(\[|\{\{|\[|\{|\()"', chart, re.MULTILINE))
        for spec in manifest['charts']:
            with self.subTest(chart=spec['id']):
                self.assertIn(spec['start'], steps[spec['id']])
                for origin, targets in spec['jumps'].items():
                    self.assertIn(origin, steps[spec['id']])
                    for target in targets:
                        chart_id, step = target.split(':')
                        self.assertIn(step, steps[chart_id], target)
