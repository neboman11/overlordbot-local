from pathlib import Path
import unittest
from overlord.brain import Brain, parse_wind
from overlord.tacview import World

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'replay.acmi'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.world.feed_text(FIXTURE.read_text())
        self.brain = Brain(self.world)

    def register_warning(self):
        answer = self.brain.respond({'text': 'Overlord Viper one one set warning radius thirty miles',
                                     'coalition': 2, 'channel': '2:251000000:0'})
        self.assertIsNotNone(answer['reply'])
        self.assertEqual(self.brain.warning.monitored(), {'100': 30})

    def test_warning_reaches_only_registered_coalition_and_frequency(self):
        self.register_warning()
        self.brain.poll()
        self.assertEqual(self.brain.take_events({'coalition': 1, 'channel': '2:251000000:0'})['events'], [])
        self.assertEqual(self.brain.take_events({'coalition': 2, 'channel': '2:260000000:0'})['events'], [])
        events = self.brain.take_events({'coalition': 2, 'channel': '2:251000000:0'})['events']
        self.assertEqual(len(events), 1)
        self.assertIn('threat', events[0]['reply'].lower())
        self.assertEqual(events[0]['unit_id'], '100')
        self.assertEqual(self.brain.take_events({'coalition': 2, 'channel': '2:251000000:0'})['events'], [])

    def test_new_telemetry_connection_does_not_inherit_subscriptions(self):
        self.register_warning()
        self.brain.poll()
        self.world.reset()
        self.world.feed_text(FIXTURE.read_text())
        # Retrieval before the next monitor tick must already discard old events.
        self.assertEqual(self.brain.take_events({'coalition': 2, 'channel': '2:251000000:0'})['events'], [])
        self.assertEqual(self.brain.warning.monitored(), {})

    def test_radio_check_keeps_pending_warning_but_cancel_invalidates_downloaded_event(self):
        self.register_warning()
        self.brain.poll()
        self.brain.respond({'text': 'Overlord Viper one one radio check',
                            'coalition': 2, 'channel': '2:251000000:0'})
        event = self.brain.take_events({'coalition': 2, 'channel': '2:251000000:0'})['events'][0]
        self.assertTrue(self.brain.validate_event(event)['valid'])
        self.brain.respond({'text': 'Overlord Viper one one cancel warnings',
                            'coalition': 2, 'channel': '2:251000000:0'})
        self.assertFalse(self.brain.validate_event(event)['valid'])

    def test_tower_instructions_are_not_rerouted_by_awacs_calls(self):
        from overlord import domain
        airfield = next(field for field in domain.AIRFIELDS if field.name.lower().startswith('anapa'))
        point = next(point for point in airfield.taxi_points if point['kind'] == 'parking')
        world = World()
        world.feed_line(f'1,T={point["lon"]}|{point["lat"]}|{airfield.altitude}|0|0|90,'
                        'Type=Air+FixedWing,Name=F-16C_50,Pilot=Viper 1-1,Color=Blue')
        brain = Brain(world, wind=90)
        brain.respond({'text': 'Anapa ground Viper one one ready to taxi', 'coalition': 2, 'channel': 'tower'})
        _, _, points = airfield.taxi_route(world.snapshot()[0], 90)
        end = points[-1]
        brain.respond({'text': 'Overlord Viper one one set warning radius thirty miles',
                       'coalition': 2, 'channel': 'awacs'})
        world.feed_line('#1')
        world.feed_line(f'1,T={end["lon"]}|{end["lat"]}|{airfield.altitude}|0|0|90')
        world.feed_line('#2')
        brain.poll()
        tower = brain.take_events({'coalition': 2, 'channel': 'tower'})['events']
        self.assertEqual(len(tower), 1)
        self.assertIn('take-off', tower[0]['reply'].lower())
        self.assertEqual(brain.take_events({'coalition': 2, 'channel': 'awacs'})['events'], [])

    def test_expired_downloaded_instruction_is_invalid(self):
        from unittest.mock import patch
        self.register_warning()
        self.brain.poll()
        event = self.brain.take_events({'coalition': 2, 'channel': '2:251000000:0'})['events'][0]
        with patch('overlord.brain.time.time', return_value=event['expires'] + 1):
            self.assertFalse(self.brain.validate_event(event)['valid'])

    def test_terminal_taxi_to_ramp_remains_deliverable(self):
        from contextlib import contextmanager
        from overlord import domain
        airfield = next(field for field in domain.AIRFIELDS if field.name.lower().startswith('anapa'))
        caller = {'id': 'landing', 'pilot': 'Viper 1-1', 'callsign': 'Viper 1-1',
                  'coalition': 2, 'type': 'F-16C_50', 'category': 'Airplane',
                  'lat': 45.3, 'lon': 37.6, 'altitude': 1500, 'heading': 90, 'speed': 100}
        class Scenario:
            generation = 0
            ready = True
            @contextmanager
            def locked_snapshot(self):
                yield self.generation, [dict(caller)]
        brain = Brain(Scenario(), wind=90)
        brain.respond({'text': 'Anapa approach Viper one one inbound', 'coalition': 2, 'channel': 'tower'})
        def move(point_name, **fields):
            point = next(point for point in airfield.vertices if point['name'] == point_name)
            caller.update(lat=point['lat'], lon=point['lon'], **fields)
            brain.poll()
            return brain.take_events({'coalition': 2, 'channel': 'tower'})['events']
        for name in ('Entry-Exit West', 'Initial-0 4', 'Runway-0 4'):
            move(name)
        move('Runway-0 4', speed=30, altitude=airfield.altitude + 5)
        events = move('Whiskey Spots')
        self.assertEqual(brain.atc.tracked(), {})
        self.assertEqual(len(events), 1)
        self.assertIn('taxi to ramp', events[0]['reply'].lower())
        self.assertTrue(brain.validate_event(events[0])['valid'])

    def test_wind_is_explicit_and_finite(self):
        self.assertIsNone(parse_wind(''))
        self.assertEqual(parse_wind('90'), 90)
        self.assertEqual(parse_wind('{"Anapa":270}'), {'Anapa': 270})
        for invalid in ('NaN', '360', '-1', 'true', '{"Anapa":Infinity}', '"north"'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                parse_wind(invalid)


if __name__ == '__main__':
    unittest.main()
