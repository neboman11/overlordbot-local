"""Record fixed synthetic caller WAVs; not a human speech accuracy benchmark.

Run in the pinned speech image with /output mounted to fixtures/radio. Bot replies
still use the actual remote Piper service. Zero source noise prevents regression
results changing merely because the caller fixture was randomly regenerated.
"""
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
from piper import PiperVoice
from piper.config import SynthesisConfig
from overlord.speech import resample

CALLS = ['Overlord, Viper one one, bogey dope.',
         'Overlord, Viper one one, request picture.',
         'Overlord, Viper one one, where is Sukhumi?',
         'Anapa approach, Viper one one, inbound.']


def main():
    destination = Path('/output')
    destination.mkdir(exist_ok=True)
    voice = PiperVoice.load('/models/en_US-ljspeech-medium.onnx', use_cuda=False)
    configuration = SynthesisConfig(noise_scale=0, noise_w_scale=0)
    records = []
    for index, text in enumerate(CALLS):
        audio = np.concatenate([chunk.audio_float_array for chunk in voice.synthesize(text, syn_config=configuration)])
        pcm = (np.clip(resample(audio, voice.config.sample_rate), -1, 1) * 32767).round().astype('<i2').tobytes()
        path = destination / f'call-{index}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(pcm)
        records.append({'text': text, 'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (destination / 'corpus.json').write_text(json.dumps({'kind': 'curated synthetic functional regression corpus, not human accuracy evaluation',
                                                       'model': 'en_US-ljspeech-medium (models.lock.json)',
                                                       'noise_scale': 0, 'noise_w_scale': 0, 'recordings': records}, indent=2)+'\n')


if __name__ == '__main__':
    main()
