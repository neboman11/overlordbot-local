"""Functional CPU compatibility test, NOT a physical Xeon latency benchmark.

Run Python and whisper-server under qemu-x86_64 -cpu IvyBridge. QEMU_CPU must
be set for child ELF processes too. No model download or network egress needed.
"""
import json
import os
from pathlib import Path
import time
from overlord.speech import NativeSTT, NativeTTS


def main():
    start = time.monotonic()
    tts = NativeTTS()
    pcm = tts.synthesize('Overlord, Viper one one, bogey dope.')
    stt = NativeSTT()
    try:
        transcript = stt.transcribe(pcm, 'Overlord, Viper one one, bogey dope.')
        if not any(term in transcript['text'].lower() for term in ('bogey', 'bogie')):
            raise RuntimeError('Emulated speech result did not preserve command: ' + repr(transcript))
        result = {'emulator': 'qemu-x86_64 -cpu IvyBridge', 'physical_xeon_benchmark': False,
                  'scope': 'Piper/ONNX synthesis and whisper.cpp tiny.en transcription',
                  'text': transcript['text'], 'samples': len(pcm)//2,
                  'emulated_elapsed_seconds': round(time.monotonic()-start, 3), 'passed': True}
        Path('/artifacts/avx-compatibility.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2), flush=True)
    finally:
        stt.close()


if __name__ == '__main__':
    main()
