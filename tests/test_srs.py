import math
import struct
import unittest
from overlord.srs import decode_packet, encode_packet


class VoicePacketTests(unittest.TestCase):
    def test_current_wire_packet_fields(self):
        # Independent wire layout guards endianness/offset mistakes, not only round trips.
        guid = b'abcdefghijklmnopqrstuv'
        audio = b'\xf8\xff\xfe'
        wire = struct.pack('<HHH', 76, 3, 10) + audio + struct.pack('<dBB', 251000000, 0, 0)
        wire += struct.pack('<IQB', 123, 0x123456789abcdef, 0) + guid * 2
        packet = decode_packet(wire)
        self.assertEqual(packet.audio, audio)
        self.assertEqual(packet.frequencies, ((251000000, 0, 0),))
        self.assertEqual(packet.unit_id, 123)
        self.assertEqual(packet.number, 0x123456789abcdef)
        self.assertEqual(packet.sender, guid.decode())
        self.assertEqual(encode_packet(audio, 251000000, 0, guid.decode(), packet.number, 123), wire)

    def test_rejects_truncated_and_invalid_frequency_packets(self):
        good = encode_packet(b'123', 251000000, 0, 'abcdefghijklmnopqrstuv', 1)
        for bad in (good[:-1], good[:20], good + b'0', good[:4] + b'\x09\x00' + good[6:]):
            with self.subTest(length=len(bad)), self.assertRaises(ValueError):
                decode_packet(bad)
        for frequency in (math.nan, math.inf, -1):
            with self.subTest(frequency=frequency), self.assertRaises(ValueError):
                decode_packet(encode_packet(b'123', frequency, 0, 'abcdefghijklmnopqrstuv', 1))


if __name__ == '__main__':
    unittest.main()
