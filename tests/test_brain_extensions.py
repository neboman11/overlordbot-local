from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from overlord.brain import Brain
from overlord.tacview import World

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures' / 'replay.acmi'
AWACS = '2:251000000:0'
JTAC = '2:252000000:0'


class BrainExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.world = World(stale_seconds=3600)
        self.world.feed_text(FIXTURE.read_text())

    def test_known_command_never_uses_model(self):
        class ForbiddenModel:
            def rewrite(self, *args):
                raise AssertionError('known grammar reached inference')
        brain = Brain(self.world, nlp=ForbiddenModel())
        answer = brain.respond({'text': 'Overlord Viper one one bogey dope',
                                'coalition': 2, 'channel': AWACS})
        self.assertEqual(answer['parsed']['intent'], 'bogeyDope')
        self.assertIn('bra', answer['reply'].lower())

    def test_inference_releases_locks_and_recalculates_current_geometry(self):
        test = self
        class Model:
            def rewrite(self, *args):
                acquired = brain.lock.acquire(blocking=False)
                test.assertTrue(acquired, 'brain lock held during inference')
                brain.lock.release()
                done = threading.Event()
                def update():
                    test.world.feed_text('#301\n200,T=0.8|0.5|9000\n')
                    done.set()
                thread = threading.Thread(target=update)
                thread.start()
                test.assertTrue(done.wait(2), 'world lock held during inference')
                thread.join()
                return 'Overlord Viper one one bogey dope'
        brain = Brain(self.world, nlp=Model())
        old = brain.respond({'text': 'Overlord Viper one one bogey dope',
                             'coalition': 2, 'channel': AWACS})['reply']
        answer = brain.respond({'text': 'Overlord Viper one one who is near me',
                                'coalition': 2, 'channel': AWACS})
        fresh = brain.respond({'text': 'Overlord Viper one one bogey dope',
                               'coalition': 2, 'channel': AWACS})['reply']
        self.assertNotEqual(old, fresh)
        self.assertEqual(answer['reply'], fresh)

    def test_mission_restart_during_inference_discards_result(self):
        world = self.world
        class Model:
            def rewrite(self, *args):
                world.reset()
                world.feed_text(FIXTURE.read_text())
                return 'Overlord Viper one one picture'
        brain = Brain(world, nlp=Model())
        answer = brain.respond({'text': 'Overlord Viper one one describe the skies',
                                'coalition': 2, 'channel': AWACS})
        self.assertIsNone(answer['reply'])

    def test_wrong_frequency_does_not_answer_or_infer_jtac_calls(self):
        from overlord.jtac import Controller
        class ForbiddenModel:
            def rewrite(self, *args):
                raise AssertionError('wrong frequency reached inference')
        brain = Brain(self.world, nlp=ForbiddenModel(), jtac=Controller(channel=JTAC))
        self.assertIsNone(brain.respond({'text': 'Axeman Viper one one check in',
                                        'coalition': 2, 'channel': AWACS})['reply'])
        self.assertIsNone(brain.respond({'text': 'Overlord Viper one one picture',
                                        'coalition': 2, 'channel': JTAC})['reply'])
        self.assertIsNone(brain.respond({'text': 'Axeman Viper one one check in',
                                        'coalition': 2})['reply'])

    def test_channel_coalition_mismatch_is_rejected(self):
        brain = Brain(self.world)
        with self.assertRaises(ValueError):
            brain.respond({'text': 'Overlord Viper one one picture',
                           'coalition': 1, 'channel': AWACS})

    def test_say_again_is_caller_channel_and_time_scoped(self):
        brain = Brain(self.world)
        request = {'coalition': 2, 'channel': AWACS}
        brain.respond(dict(request, text='Overlord Viper one one picture'))
        other = brain.respond(dict(request, text='Overlord Viper one two say again'))
        self.assertIn('no previous', other['reply'].lower())
        own = brain.respond(dict(request, text='Overlord Viper one one say again'))
        self.assertEqual(own['parsed']['intent'], 'sayAgain')
        self.assertNotIn('no previous', own['reply'].lower())
        wrong_channel = brain.respond({'text': 'Overlord Viper one one say again',
                                       'coalition': 2, 'channel': '2:253000000:0'})
        self.assertIn('no previous', wrong_channel['reply'].lower())
        with patch('overlord.brain.time.monotonic', return_value=10**12):
            expired = brain.respond(dict(request, text='Overlord Viper one one say again'))
        self.assertIn('no previous', expired['reply'].lower())


    def test_noncanonical_jtac_channel_cannot_bypass_frequency_isolation(self):
        from overlord.jtac import Controller
        brain = Brain(self.world, jtac=Controller(channel=JTAC))
        answer = brain.respond({'text': 'Overlord Viper one one picture', 'coalition': 2,
                                'channel': '02:0252000000:00'})
        self.assertIsNone(answer['reply'])

    def test_separate_frequency_receive_windows_do_not_overlap(self):
        from overlord.__main__ import jtac_channel
        for offset in (-199, 0, 50, 199):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                jtac_channel(2, 251000000 + offset, 0, 251000000)
        for offset in (-200, 200, 1000000):
            self.assertEqual(jtac_channel(2, 251000000 + offset, 0, 251000000),
                             f'2:{251000000 + offset}:0')


    def test_home_base_request_does_not_fuzzily_resolve_back_to_my_as_batumi(self):
        brain = Brain(self.world, home_airfield='Kutaisi')
        answer = brain.respond({'text': 'Overlord Viper one one give me the direction back to my home base',
                                'coalition': 2, 'channel': AWACS})
        self.assertEqual(answer['parsed']['intent'], 'vectorHome')
        self.assertIn('Kutaisi', answer['reply'])
        self.assertNotIn('Batumi', answer['reply'])


    def test_stale_telemetry_blocks_progress_but_not_local_jtac_abort(self):
        from overlord.jtac import Controller
        clock = [0.0]
        world = World(stale_seconds=30, clock=lambda: clock[0])
        world.feed_text(FIXTURE.read_text())
        controller = Controller(channel=JTAC)
        controller.exchange({'version': 1, 'epoch': 'stale-telemetry', 'seq': 1,
                             'time': 0, 'tasks': [], 'acks': []})
        brain = Brain(world, jtac=controller)
        request = {'coalition': 2, 'channel': JTAC}
        checked_in = brain.respond(dict(request, text='Axeman Viper one one checking in'))
        self.assertIsNotNone(checked_in['reply'])
        clock[0] = 31
        self.assertFalse(brain.ready)
        progress = brain.respond(dict(request, text='Axeman Viper one one request task'))
        self.assertIsNone(progress['reply'])
        aborted = brain.respond(dict(request, text='Axeman Viper one one abort'))
        self.assertEqual(aborted['parsed']['intent'], 'abort')
        self.assertIn('abort', aborted['reply'].lower())
        self.assertTrue(aborted['validation']['safety_cancel'])
        self.assertTrue(brain.validate_event(aborted['validation'])['valid'])


    def test_negated_jtac_statement_cannot_be_reinterpreted_by_model(self):
        from overlord.jtac import Controller
        class ForbiddenModel:
            def rewrite(self, *args):
                raise AssertionError('negated procedure fact reached inference')
        brain = Brain(self.world, jtac=Controller(channel=JTAC), nlp=ForbiddenModel())
        answer = brain.respond({'text': 'Axeman Viper one one negative contact',
                                'coalition': 2, 'channel': JTAC})
        self.assertEqual(answer['parsed']['intent'], 'None')
        self.assertTrue(answer['parsed']['blocked'])

    def test_say_again_does_not_override_explicit_phonetic_request(self):
        brain = Brain(self.world)
        answer = brain.respond({'text': 'Overlord Viper one one say again bobey dope',
                                'coalition': 2, 'channel': AWACS})
        self.assertEqual(answer['parsed']['intent'], 'bogeyDope')
        self.assertIn('bra', answer['reply'].lower())


if __name__ == '__main__':
    unittest.main()
