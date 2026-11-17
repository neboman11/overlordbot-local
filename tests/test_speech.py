import unittest

from overlord.speech import TTS_CHUNK_CHARS, speech_chunks


class SpeechChunksTest(unittest.TestCase):
    def test_long_reply_is_bounded_and_keeps_every_word(self):
        # Piper memory scales with sentence length: no chunk may exceed the bound, whatever the input shape.
        texts = [
            ', '.join(f'line {n}, armored column of six tanks parked along the tree line north of the bridge'
                      for n in range(12))[:1000],
            'x' * 1000,
            ' '.join(['unpunctuated remarks'] * 50),
        ]
        for text in texts:
            chunks = speech_chunks(text)
            self.assertTrue(all(0 < len(chunk) <= TTS_CHUNK_CHARS for chunk in chunks), text[:40])
            self.assertEqual(''.join(text.replace(',', ' ').split()), ''.join(' '.join(chunks).replace(',', ' ').split()))

    def test_short_reply_is_one_chunk(self):
        self.assertEqual(speech_chunks('silver 1 2, overlord, 5 by 5'), ['silver 1 2, overlord, 5 by 5'])


if __name__ == '__main__':
    unittest.main()
