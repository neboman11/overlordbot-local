"""SRS 2.4 newline-JSON control and little-endian UDP Opus transport.

Wire format follows SRS 2.4.1.0 Common/Models/UDPVoicePacket.cs.
"""
import base64
import ctypes as C
import ctypes.util
from dataclasses import dataclass
import json
import math
import socket
import struct
import threading
import time
import uuid


@dataclass(frozen=True)
class VoicePacket:
    audio: bytes
    frequencies: tuple
    unit_id: int
    number: int
    hops: int
    original: str
    sender: str


def encode_packet(audio, frequency, modulation, guid, number, unit_id=0):
    tail = struct.pack('<IQB', unit_id, number, 0) + guid.encode('ascii') * 2
    size = 6 + len(audio) + 10 + len(tail)
    return struct.pack('<HHH', size, len(audio), 10) + audio + struct.pack('<dBB', frequency, modulation, 0) + tail


def decode_packet(data):
    if len(data) < 73:
        raise ValueError('short SRS voice packet')
    size, audio_size, frequencies_size = struct.unpack_from('<HHH', data)
    if size != len(data) or frequencies_size == 0 or frequencies_size % 10 or 6 + audio_size + frequencies_size + 57 != size:
        raise ValueError('invalid SRS packet lengths')
    radios = tuple(struct.unpack_from('<dBB', data, offset) for offset in range(6 + audio_size, 6 + audio_size + frequencies_size, 10))
    if any(not math.isfinite(f) or f <= 0 for f, _, _ in radios):
        raise ValueError('invalid radio frequency')
    unit, number, hops = struct.unpack_from('<IQB', data, 6 + audio_size + frequencies_size)
    return VoicePacket(data[6:6 + audio_size], radios, unit, number, hops,
                       data[-44:-22].decode('ascii'), data[-22:].decode('ascii'))


class Opus:
    """One codec instance per speaker/stream. Input/output: 16 kHz mono s16le."""
    def __init__(self):
        self.lib = C.CDLL(ctypes.util.find_library('opus') or 'libopus.so.0')
        self.lib.opus_encoder_create.argtypes = [C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int)]
        self.lib.opus_encoder_create.restype = C.c_void_p
        self.lib.opus_decoder_create.argtypes = [C.c_int, C.c_int, C.POINTER(C.c_int)]
        self.lib.opus_decoder_create.restype = C.c_void_p
        self.lib.opus_encode.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_void_p, C.c_int]
        self.lib.opus_decode.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_int]
        self.lib.opus_encoder_destroy.argtypes = [C.c_void_p]
        self.lib.opus_decoder_destroy.argtypes = [C.c_void_p]
        error = C.c_int()
        self.encoder = self.lib.opus_encoder_create(16000, 1, 2048, C.byref(error))
        if error.value:
            raise RuntimeError(f'Opus encoder: {error.value}')
        self.decoder = self.lib.opus_decoder_create(16000, 1, C.byref(error))
        if error.value:
            self.lib.opus_encoder_destroy(self.encoder)
            raise RuntimeError(f'Opus decoder: {error.value}')

    def encode(self, pcm):
        if len(pcm) != 1280:
            raise ValueError('Opus requires 640 samples / 40 ms')
        output = C.create_string_buffer(4000)
        length = self.lib.opus_encode(self.encoder, pcm, 640, output, len(output))
        if length < 0:
            raise ValueError(f'Opus encode: {length}')
        return output.raw[:length]

    def decode(self, audio):
        output = C.create_string_buffer(16000 * 2 * 120 // 1000)
        length = self.lib.opus_decode(self.decoder, audio, len(audio), output, len(output) // 2, 0)
        if length < 0:
            raise ValueError(f'Opus decode: {length}')
        return output.raw[:length * 2]

    def close(self):
        if self.encoder:
            self.lib.opus_encoder_destroy(self.encoder)
            self.lib.opus_decoder_destroy(self.decoder)
            self.encoder = self.decoder = None


class SRSClient:
    def __init__(self, host, port, password, coalition=2, frequency=251000000, modulation=0, name='Overlord'):
        if coalition not in (1, 2) or modulation not in (0, 1) or not math.isfinite(frequency) or frequency <= 10000:
            raise ValueError('invalid coalition, AM/FM modulation, or frequency')
        self.address = (socket.gethostbyname(host), port)
        self.password = password
        self.coalition = coalition
        self.frequency = frequency
        self.modulation = modulation
        self.guid = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip('=')
        self.client = {'ClientGuid': self.guid, 'Name': name, 'Coalition': 0,
                       'AllowRecord': True, 'LatLngPosition': {'lat': 0, 'lng': 0, 'alt': 0}}
        self.tcp = self.udp = self.stream = None
        self.clients = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.connected = False
        self.number = 0

    def send_control(self, kind, **fields):
        message = dict(Client=self.client, MsgType=kind, Version='2.4.1.0', **fields)
        with self.lock:
            self.tcp.sendall((json.dumps(message) + '\n').encode())

    def _read(self):
        line = self.stream.readline(1024 * 1024)
        if not line or len(line) >= 1024 * 1024:
            raise ConnectionError('SRS control connection closed or oversized message')
        message = json.loads(line)
        for client in message.get('Clients', []):
            self.clients[client['ClientGuid']] = client
        if message.get('Client', {}).get('ClientGuid'):
            client = message['Client']
            if message['MsgType'] == 5:
                self.clients.pop(client['ClientGuid'], None)
            else:
                self.clients[client['ClientGuid']] = client
        return message

    def _until(self, kind):
        for _ in range(128):
            message = self._read()
            if message['MsgType'] == 6:
                raise ConnectionError('SRS rejected protocol version')
            if message['MsgType'] == kind:
                return message
        raise ConnectionError('SRS handshake exceeded message bound')

    def connect(self):
        try:
            self.tcp = socket.create_connection(self.address, timeout=10)
            self.stream = self.tcp.makefile('rb')
            self.client['RadioInfo'] = {'unit': 'External AWACS', 'unitId': 0,
                                        'radios': [{'freq': self.frequency, 'modulation': self.modulation,
                                                    'secFreq': 0, 'enc': False, 'encKey': 0}]}
            self.send_control(2)
            self._until(2)
            self.send_control(7, ExternalAWACSModePassword=self.password)
            if self._until(7)['Client']['Coalition'] != self.coalition:
                raise PermissionError('SRS EAM password rejected or wrong coalition')
            self.client['Coalition'] = self.coalition
            self.send_control(3)
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.connect(self.address)
            self.udp.settimeout(2)
            self.udp.send(self.guid.encode())
            if self.udp.recv(65535) != self.guid.encode():
                raise ConnectionError('SRS UDP registration did not echo client GUID')
            self.udp.settimeout(0.05)
            self.tcp.settimeout(None)
            self.connected = True
            threading.Thread(target=self._control_loop, daemon=True).start()
            threading.Thread(target=self._heartbeat, daemon=True).start()
        except Exception:
            self.close()
            raise
        return self

    def _control_loop(self):
        try:
            while not self.stopped.is_set():
                self._read()
        except (OSError, ValueError, ConnectionError):
            self.connected = False
            self.stopped.set()

    def _heartbeat(self):
        while not self.stopped.wait(5):
            try:
                self.send_control(3)
                self.udp.send(self.guid.encode())
            except OSError:
                self.connected = False
                self.stopped.set()

    def receive(self):
        if not self.connected:
            raise ConnectionError('SRS disconnected')
        try:
            data = self.udp.recv(65535)
        except socket.timeout:
            return None
        if len(data) == 22:
            return None
        packet = decode_packet(data)
        if packet.sender == self.guid:
            return None
        if not any(abs(freq - self.frequency) < 100 and mod == self.modulation and enc == 0
                   for freq, mod, enc in packet.frequencies):
            return None
        return packet

    def transmit(self, pcm):
        codec = Opus()
        try:
            deadline = time.monotonic()
            for offset in range(0, len(pcm), 1280):
                if not self.connected:
                    raise ConnectionError('SRS disconnected during transmission')
                audio = codec.encode(pcm[offset:offset + 1280].ljust(1280, b'\0'))
                self.number += 1
                self.udp.send(encode_packet(audio, self.frequency, self.modulation, self.guid, self.number))
                deadline += .04
                time.sleep(max(0, deadline - time.monotonic()))
        finally:
            codec.close()

    def close(self):
        self.connected = False
        self.stopped.set()
        if self.tcp:
            try:
                self.tcp.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.tcp.close()
        if self.udp:
            self.udp.close()
        if self.stream:
            self.stream.close()
