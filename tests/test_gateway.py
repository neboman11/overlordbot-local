import socket
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from overlord.gateway import Gateway


class GatewayRecoveryTests(unittest.TestCase):
    def test_dns_failure_recovers_instead_of_killing_receiver(self):
        gateway = Gateway()
        client = Mock()
        client.connect.side_effect = gateway.stop.set
        with patch('overlord.gateway.SRSClient', side_effect=[socket.gaierror('temporary DNS failure'), client]), \
             patch.object(gateway.stop, 'wait', return_value=False):
            gateway._receive()
        self.assertTrue(gateway.stop.is_set(), 'receiver did not reach recovered connection')
        self.assertIsNone(gateway.client)

    def test_overflow_speaker_cannot_starve_existing_eot_deadlines(self):
        gateway = Gateway()
        client = Mock()
        client.clients = {str(i): {'Coalition': 2} for i in range(33)}
        packets = iter(SimpleNamespace(original=str(i), number=1, audio=b'pcm') for i in range(33))
        def receive():
            packet = next(packets)
            if packet.original == '32':
                gateway.stop.set()
            return packet
        client.receive.side_effect = receive
        times = [i / 1000 for i in range(32)] + [1.0]
        with patch('overlord.gateway.SRSClient', return_value=client), \
             patch('overlord.gateway.time', SimpleNamespace(monotonic=Mock(side_effect=times))):
            gateway._receive()
        self.assertEqual(gateway.jobs.qsize(), 4, 'expired transmissions must reach bounded processor queue even when speaker cap is full')

    def test_revoked_jtac_reply_is_not_transmitted_after_synthesis(self):
        gateway = Gateway()
        client = Mock(connected=True)
        client.clients = {'pilot': {'Name': 'Viper 1-1'}}
        gateway.client = client
        gateway.stt = Mock()
        gateway.stt.post.return_value = b'{"text":"Axeman Viper one one in hot"}'
        validation = {'role': 'jtac', 'channel': gateway.channel, 'coalition': gateway.coalition}
        gateway.brain = Mock()
        gateway.brain.post.side_effect = [
            json.dumps({'reply': 'cleared hot', 'validation': validation}).encode(),
            b'{"valid":false}',
        ]
        gateway.tts = Mock()
        def synthesize(*args, **kwargs):
            # Simulate abort/revocation while speech synthesis is in progress.
            gateway.stop.set()
            return b'\0\0' * 1600
        gateway.tts.post.side_effect = synthesize
        gateway.jobs.put((client, 'pilot', {'packets': {1: (1, b'opus')}, 'last': time.monotonic()}))
        codec = Mock()
        codec.decode.return_value = b'\0\0' * 1600
        with patch('overlord.gateway.Opus', return_value=codec):
            gateway._process()
        client.transmit.assert_not_called()

    def test_long_reply_is_spoken_in_the_role_voice(self):
        # A full JTAC 9-line is ~75 s of audio (~2.4 MB); it once failed the 1 MiB worker response cap.
        with patch.dict('os.environ', {'TTS_VOICE': 'en_US-bryce-medium'}):
            gateway = Gateway()
        client = Mock(connected=True)
        client.clients = {'pilot': {'Name': 'Viper 1-1'}}
        gateway.client = client
        client.transmit.side_effect = lambda pcm: gateway.stop.set()     # one reply, then end the loop
        gateway.stt = Mock()
        gateway.stt.post.return_value = b'{"text":"Axeman Viper one one ready for brief"}'
        gateway.brain = Mock()
        gateway.brain.post.return_value = json.dumps({'reply': 'type two control, line one, ...'}).encode()
        gateway.tts = Mock()
        gateway.tts.post.return_value = b'\0\0' * 16000 * 75
        gateway.jobs.put((client, 'pilot', {'packets': {1: (1, b'opus')}, 'last': time.monotonic()}))
        codec = Mock()
        codec.decode.return_value = b'\0\0' * 1600
        with patch('overlord.gateway.Opus', return_value=codec):
            gateway._process()
        _, body = gateway.tts.post.call_args.args[:2]
        self.assertEqual(json.loads(body)['voice'], 'en_US-bryce-medium')
        self.assertGreaterEqual(gateway.tts.post.call_args.kwargs['limit'], 2 * 16000 * 75)
        client.transmit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
