import socket
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
             patch('overlord.gateway.time.monotonic', side_effect=times):
            gateway._receive()
        self.assertEqual(gateway.jobs.qsize(), 4, 'expired transmissions must reach bounded processor queue even when speaker cap is full')


if __name__ == '__main__':
    unittest.main()
