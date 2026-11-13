"""Speech transport acceptance checks operating facts, not ASR punctuation."""
import unittest

from scripts.e2e import atc_reply_facts


class SpokenAtcFactsTests(unittest.TestCase):
    def test_equivalent_spoken_runway_and_altitude_formats(self):
        for runway in ('04', '0/4', 'zero four'):
            for altitude in ('2000', '2,000', 'two thousand'):
                with self.subTest(runway=runway, altitude=altitude):
                    self.assertTrue(atc_reply_facts(
                        f'Anapa approach, descend and maintain {altitude}, Vekkers to runway {runway}'))

    def test_wrong_or_contradictory_operating_facts_are_rejected(self):
        for text in ('maintain 2000, runway 22',
                     'maintain 3000, runway 04',
                     'maintain 23000, runway 04',
                     'heading 04, maintain 2000, runway 22',
                     'maintain 2000, runway 04 or runway 22',
                     'maintain 2000, vectors available'):
            with self.subTest(text=text):
                self.assertFalse(atc_reply_facts(text))
