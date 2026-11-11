"""Actual SRS server: send PCM as Opus, capture WAV, play WAV back."""
import array
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import wave
from overlord.srs import Opus, SRSClient


def capture(client, seconds=6):
    codec = Opus()
    chunks = []
    last = None
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            packet = client.receive()
            if packet:
                chunks.append(codec.decode(packet.audio))
                last = time.monotonic()
            elif last and time.monotonic() - last > .3:
                return b''.join(chunks)
        raise RuntimeError('Audio capture timed out')
    finally:
        codec.close()


def save(path, pcm):
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm)


def main():
    output = Path('/evidence')
    password = secrets.token_urlsafe(24)
    env = dict(os.environ, DOTNET_SYSTEM_GLOBALIZATION_INVARIANT='1', DOTNET_BUNDLE_EXTRACT_BASE_DIR='/tmp/bundle')
    with open('/tmp/srs-audio.log', 'w+') as log:
        process = subprocess.Popen(['/srs-server', '--enableEAM', 'true', '--eamBluePassword', password,
                                    '--clientExportPath', '/tmp/clients.json', '--coalitionSecurity', 'true'],
                                   cwd='/tmp', env=env, stdout=log, stderr=subprocess.STDOUT)
        clients = []
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError('SRS exited before listen')
                try:
                    with socket.create_connection(('127.0.0.1', 5002), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('SRS listen timeout')
                    time.sleep(.1)
            sender = SRSClient('127.0.0.1', 5002, password, name='Viper 1-1').connect()
            clients.append(sender)
            gateway = SRSClient('127.0.0.1', 5002, password, name='Overlord').connect()
            clients.append(gateway)
            source = array.array('h', (int(7000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000))).tobytes()
            time.sleep(.2)
            transmitter = threading.Thread(target=sender.transmit, args=(source,))
            transmitter.start()
            captured = capture(gateway)
            transmitter.join()
            save(output / 'received.wav', captured)
            with wave.open(str(output / 'received.wav'), 'rb') as recorded:
                playback = recorded.readframes(recorded.getnframes())
            transmitter = threading.Thread(target=gateway.transmit, args=(playback,))
            transmitter.start()
            returned = capture(sender)
            transmitter.join()
            save(output / 'playback.wav', returned)
            samples = array.array('h', returned)
            rms = math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0
            # Opus is lossy and introduces delay. Verify expected tone, not byte equality.
            power = sum(x * math.cos(2 * math.pi * 440 * i / 16000) for i, x in enumerate(samples)) ** 2
            power += sum(x * math.sin(2 * math.pi * 440 * i / 16000) for i, x in enumerate(samples)) ** 2
            tone_fraction = 2 * power / (len(samples) * sum(x * x for x in samples)) if rms else 0
            if len(captured) != len(source) or len(returned) != len(source) or rms < 1000 or tone_fraction < .75:
                raise RuntimeError(f'Audio corruption: {len(captured)=}, {len(returned)=}, {rms=}, {tone_fraction=}')
            result = dict(server='2.4.1.0', frequency_hz=251000000, modulation='AM', coalition=2,
                          source_samples=len(source)//2, captured_samples=len(captured)//2,
                          playback_samples=len(returned)//2, playback_rms=round(rms, 2),
                          tone_energy_fraction=round(tone_fraction, 4), passed=True)
            (output / 'audio-roundtrip.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result, indent=2))
        finally:
            for client in clients:
                client.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.returncode not in (0, -15):
                log.seek(0)
                print(log.read())


if __name__ == '__main__':
    main()
