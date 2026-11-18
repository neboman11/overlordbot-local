# Speech image third-party components

Image `overlordbot-local-speech` (Dockerfile.speech). This repository's own code is
AGPL-3.0-or-later (/app/LICENSE). No third-party source is committed here; everything below is
fetched at image build time from the pinned location and verified.

| Component | Version / pin | Licence | Source | In image |
|---|---|---|---|---|
| whisper.cpp `whisper-server` (incl. ggml, cpp-httplib, nlohmann/json) | tag v1.9.5, commit `d1be6fde11ac6e0407606b4e42fe72d34add8037` | MIT | https://github.com/ggml-org/whisper.cpp/tree/d1be6fde11ac6e0407606b4e42fe72d34add8037 | `/usr/local/bin/whisper-server`, licence `/usr/local/share/licenses/whisper.cpp/LICENSE`, CMake cache flags `/usr/local/share/whisper-build-flags.txt` |
| piper-tts (embeds espeak-ng and its data) | 1.8.0, wheel sha256 `25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3` | GPL-3.0-or-later | https://github.com/OHF-Voice/piper1-gpl (sdist: https://pypi.org/project/piper-tts/1.8.0/) | site-packages; licence files in `piper_tts-1.8.0.dist-info/licenses/` |
| onnxruntime | 1.31.1 | MIT | https://github.com/microsoft/onnxruntime | site-packages (`onnxruntime-1.31.1.dist-info`) |
| numpy, protobuf, flatbuffers, packaging, pathvalidate | see requirements-speech.txt | BSD-3-Clause, BSD-3-Clause, Apache-2.0, Apache-2.0/BSD-2-Clause, MIT | PyPI | site-packages dist-info |
| Whisper tiny.en (ggml) | HF `ggerganov/whisper.cpp` rev `5359861c739e955e79d9a303bcbc70fb988958b1` | MIT | models.lock.json | `/models/ggml-tiny.en.bin` |
| Piper voice en_US-ljspeech-medium | HF `rhasspy/piper-voices` rev `cf910fb59f1d79ab3b625a63993a108751c6c25d`, path `en/en_US/ljspeech/medium/` | public domain: MODEL_CARD states LJ Speech dataset licence "public domain"; trainer Bryce Beattie publishes it as public domain ("use these for any legal and ethical purpose", https://brycebeattie.com/files/tts/); repository tagged MIT | models.lock.json | `/models/en_US-ljspeech-medium.onnx{,.json}`, `/models/en_US-ljspeech-medium.MODEL_CARD` |
| Piper voices en_US-john-medium, en_US-bryce-medium, en_US-kristin-medium | HF `rhasspy/piper-voices` rev `cf910fb59f1d79ab3b625a63993a108751c6c25d`, paths `en/en_US/{john,bryce,kristin}/medium/` | public domain: each MODEL_CARD states licence "public domain" (john and kristin: LibriVox recordings; bryce: the trainer's own voice); trainer Bryce Beattie publishes them as public domain (https://brycebeattie.com/files/tts/) | models.lock.json | `/models/en_US-{john,bryce,kristin}-medium.onnx{,.json}` and `.MODEL_CARD` |

Exact URLs, sizes and SHA256 for every model file: `/app/models.lock.json`.

Voices: LJ Speech (https://keithito.com/LJ-Speech-Dataset/) is public-domain LibriVox audio. All four
voices are 22050 Hz natively; NativeTTS resamples them to 16000 Hz. Each config selects espeak-ng voice `en`.
Gateways pick a voice per radio role (`TTS_VOICE`); see DEPLOY.md.

GPL-3.0 piper-tts combined with AGPL-3.0 code is permitted by section 13 of both licences;
corresponding source for piper-tts and espeak-ng is at the URLs above.
