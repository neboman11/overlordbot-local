"""Speech transport acceptance checks operating facts, not ASR punctuation."""
import array
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from scripts.e2e import atc_reply_facts, matched_tts, opus_roundtrip, spoken_facts, trace_ids


def tone(samples, hz=440):
    return array.array('h', (int(8000 * math.sin(2 * math.pi * hz * i / 16000)) for i in range(samples))).tobytes()


def record(directory, ident, text, pcm):
    name = f'tts-{ident}.pcm'
    (directory / name).write_bytes(pcm)
    with (directory / 'tts-trace.jsonl').open('a') as trace:
        trace.write(json.dumps({'id': ident, 'text': text, 'pcm_file': name,
                                'pcm_sha256': hashlib.sha256(pcm).hexdigest(), 'at': 1.0}) + '\n')


class TtsTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.first, self.second = '1' * 32, '2' * 32
        self.pcm_a, self.pcm_b = tone(1000), tone(1500, 880)  # 1000 forces a zero-padded last frame

    def test_selects_new_trace_whose_opus_roundtrip_is_received_audio(self):
        record(self.dir, self.first, 'old reply', self.pcm_a)
        known = trace_ids(self.dir)
        record(self.dir, self.second, 'runway 04 maintain 2000', self.pcm_b)
        record(self.dir, '3' * 32, 'unrelated', tone(1500, 220))
        self.assertEqual(matched_tts(self.dir, known, opus_roundtrip(self.pcm_b)), 'runway 04 maintain 2000')

    def test_previous_trace_with_identical_audio_is_not_accepted(self):
        record(self.dir, self.first, 'old reply', self.pcm_a)
        with self.assertRaises(RuntimeError):
            matched_tts(self.dir, trace_ids(self.dir), opus_roundtrip(self.pcm_a))

    def test_truncated_unrelated_or_missing_audio_is_rejected(self):
        record(self.dir, self.second, 'text', self.pcm_b)
        expected = opus_roundtrip(self.pcm_b)
        for received in (expected[:-1280], expected[:1280], opus_roundtrip(tone(1500, 220)), expected + expected[:1280], b''):
            with self.subTest(length=len(received)):
                with self.assertRaises(RuntimeError):
                    matched_tts(self.dir, set(), received)
        with self.assertRaises(RuntimeError):
            matched_tts(self.dir / 'absent', set(), expected)

    def test_corrupted_or_traversing_trace_is_rejected(self):
        record(self.dir, self.second, 'text', self.pcm_b)
        (self.dir / f'tts-{self.second}.pcm').write_bytes(self.pcm_b[:-2])
        with self.assertRaises(RuntimeError):
            matched_tts(self.dir, set(), opus_roundtrip(self.pcm_b))
        other = tempfile.mkdtemp(dir=self.dir)
        (Path(other) / 'tts-trace.jsonl').write_text(json.dumps(
            {'id': self.first, 'text': 't', 'pcm_file': '../x.pcm', 'pcm_sha256': '0', 'at': 1.0}) + '\n')
        with self.assertRaises(RuntimeError):
            matched_tts(other, set(), b'')


class SpokenSourceFactsTests(unittest.TestCase):
    def test_valid_source_field_replies_accepted_independent_of_caption(self):
        self.assertTrue(spoken_facts('bogey', 'Viper one one, Overlord, BRA, zero five two five, twenty three thousand, drag east Fulcrum.'))
        self.assertTrue(spoken_facts('picture', 'Viper one one, Overlord, single group, BRA, 052, 23,000, drag east.'))
        self.assertTrue(spoken_facts('airfield', 'Viper one one, Overlord, Sukhumi, BRA, bearing one nine eight three.'))
        self.assertTrue(spoken_facts('atc', 'Anapa approach, descend and maintain 2000, vectors to runway 04'))

    def test_wrong_runway_altitude_or_picture_facts_rejected(self):
        self.assertFalse(spoken_facts('atc', 'descend and maintain 2000, vectors to runway 22'))
        self.assertFalse(spoken_facts('atc', 'descend and maintain 3000, vectors to runway 04'))
        self.assertFalse(spoken_facts('bogey', 'Overlord, BRA, zero five two five, nineteen thousand, drag east'))
        self.assertFalse(spoken_facts('bogey', 'Overlord, single group, BRA, twenty three thousand'))
        self.assertFalse(spoken_facts('picture', 'Overlord, BRA, twenty three thousand, drag east'))
        self.assertFalse(spoken_facts('airfield', 'Overlord, Anapa, BRA, bearing one nine eight three'))
        self.assertFalse(spoken_facts('airfield', 'Overlord, Sukhumi, BRA, one nine eight three'))


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
