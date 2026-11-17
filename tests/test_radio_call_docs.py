"""Documented pilot calls must parse and reproduce actual clearance values."""
import html
import json
from pathlib import Path
import re
import unittest

from overlord import airfield, airfield_parser, carrier, carrier_parser


GUIDES = Path(__file__).resolve().parents[1] / 'docs' / 'radio-calls'
FENCES = re.compile(r'^```mermaid\s*\n([\s\S]*?)^```\s*$', re.MULTILINE)
LABELS = re.compile(r'\b([A-Za-z][A-Za-z0-9_-]*)\["((?:[^"\\]|\\.)*)"\]')
PLACEHOLDERS = re.compile(r'\[([A-Z][A-Z0-9_]*)\]')


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
