"""Verify actual PCM -> SRS Opus -> STT -> brain -> TTS -> SRS spoken replies."""
import array
import json
import hashlib
import math
import os
from pathlib import Path
import re
import time
import urllib.request
import wave
from overlord.http import Remote
from overlord.srs import Opus, SRSClient
from overlord import airfield_parser, parser


def atc_reply_facts(text):
    """The replay fixture's runway04/2000ft instruction, independent of ASR number punctuation."""
    runways = airfield_parser.runway_mentions(parser.tokenize(text))
    normalized = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    return runways == ['04'] and bool(re.search(r'\b(?:2\s*000|two thousand)\b', normalized))


def spoken_facts(expected, text):
    """Critical facts of the actual TTS source text for one fixed caller scenario."""
    normalized = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    altitude = bool(re.search(r'23[ ,]*000|twenty three thousand', normalized))
    return {'bogey': 'bra' in normalized and altitude and 'group' not in normalized,
            'picture': 'group' in normalized and altitude,
            'airfield': 'sukhumi' in normalized and 'bearing' in normalized,
            'atc': atc_reply_facts(text)}[expected]


def opus_roundtrip(pcm):
    """Exactly SRSClient.transmit's framing and codec, then SRS receive decoding, without pacing."""
    encoder = Opus()
    decoder = Opus()
    try:
        return b''.join(decoder.decode(encoder.encode(pcm[offset:offset + 1280].ljust(1280, b'\0')))
                        for offset in range(0, len(pcm), 1280))
    finally:
        encoder.close()
        decoder.close()


def trace_ids(directory):
    path = Path(directory) / 'tts-trace.jsonl'
    if not path.exists():
        return set()
    return {json.loads(line)['id'] for line in path.read_text().splitlines() if line}


def matched_tts(directory, known, reply):
    """New actual-TTS trace row whose hashed captured PCM Opus-roundtrips to exactly the received PCM."""
    directory = Path(directory)
    path = directory / 'tts-trace.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []
    matches = []
    for row in rows:
        if row['id'] in known:
            continue
        name = row['pcm_file']
        if (not re.fullmatch(r'[0-9a-f]{32}', row['id']) or name != f"tts-{row['id']}.pcm"
                or not isinstance(row['text'], str) or not isinstance(row['at'], (int, float))):
            raise RuntimeError('Malformed TTS trace row')
        pcm = (directory / name).read_bytes()
        if hashlib.sha256(pcm).hexdigest() != row['pcm_sha256']:
            raise RuntimeError('TTS trace PCM hash mismatch: ' + name)
        if pcm and opus_roundtrip(pcm) == reply:
            matches.append(row)
    if not matches:
        raise RuntimeError('Received SRS audio matches no new actual TTS trace')
    return matches[-1]['text']


def wait_ready(url, deadline):
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + '/readyz', timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('Service readiness timeout: ' + url)


def receive_reply(client, timeout=90):
    start = time.monotonic()
    first = last = None
    packets = {}
    while time.monotonic() - start < timeout:
        packet = client.receive()
        if packet:
            if first is None:
                first = time.monotonic()
            last = time.monotonic()
            packets[packet.number] = packet.audio
        elif last and time.monotonic() - last > .4:
            codec = Opus()
            try:
                return b''.join(codec.decode(audio) for _, audio in sorted(packets.items())), first - start
            finally:
                codec.close()
    raise RuntimeError('No completed spoken reply over SRS within timeout')


def main():
    deadline = time.monotonic() + 300
    for service in ('http://brain:8081', 'http://stt-worker:8082', 'http://tts-worker:8083', 'http://radio-gateway:8080'):
        wait_ready(service, deadline)
    stt = Remote('http://stt-worker:8082', timeout=60)
    client = SRSClient('srs', 5002, os.environ['SRS_EAM_PASSWORD'], name='Viper 1-1').connect()
    output = Path('/artifacts')
    output.mkdir(exist_ok=True)
    results = []
    # Fixed real-Piper caller recordings; replies still use live STT, brain and TTS.
    corpus = Path('/corpus')
    recordings = json.loads((corpus / 'corpus.json').read_text())['recordings']
    calls = [('Overlord, Viper one one, bogey dope.', 'bogey'),
             ('Overlord, Viper one one, request picture.', 'picture'),
             ('Overlord, Viper one one, where is Sukhumi?', 'airfield'),
             ('Anapa approach, Viper one one, inbound.', 'atc')]
    try:
        for index, (text, expected) in enumerate(calls):
            record = recordings[index]
            source = corpus / record['file']
            if record['text'] != text or hashlib.sha256(source.read_bytes()).hexdigest() != record['sha256']:
                raise RuntimeError('Recorded caller corpus identity mismatch')
            with wave.open(str(source), 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise RuntimeError('Caller recordings must be mono 16 kHz s16le WAV')
                pcm = audio.readframes(audio.getnframes())
            known = trace_ids(output)
            client.transmit(pcm)
            reply, latency = receive_reply(client)
            with wave.open(str(output / f'reply-{index}.wav'), 'wb') as recording:
                recording.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                recording.writeframes(reply)
            tts_text = matched_tts(output, known, reply)
            if not spoken_facts(expected, tts_text):
                raise RuntimeError(f'Unexpected {expected} spoken answer to {text!r}: {tts_text!r}')
            try:  # Outbound ASR caption is diagnostic only: it neither proves nor vetoes facts.
                caption = json.loads(stt.post('/transcribe', reply, 'audio/pcm'))['text']
            except Exception as error:
                caption = f'<unavailable: {error}>'
            samples = array.array('h', reply)
            rms = math.sqrt(sum(value * value for value in samples) / len(samples))
            if rms < 100:
                raise RuntimeError('Reply is silent')
            item = {'call': text, 'spoken_reply_transcript': caption,
                    'tts_text': tts_text, 'audio_verified': True,
                    'end_to_first_reply_audio_seconds': round(latency, 4),
                    'reply_samples': len(samples), 'rms': round(rms, 2)}
            results.append(item)
            print(json.dumps(item), flush=True)
            time.sleep(.5)
        (output / 'e2e.json').write_text(json.dumps({'corpus': 'fixed synthetic local Piper recordings, not human accuracy evaluation', 'results': results}, indent=2) + '\n')
    finally:
        client.close()


if __name__ == '__main__':
    main()
