import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.relay import minecraft_handshake_state


def varint(value):
    result = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        result.append(byte | (128 if value else 0))
        if not value:
            return bytes(result)


class RelayHandshakeTests(unittest.TestCase):
    def test_valid_login_handshake(self):
        body = varint(0) + varint(765) + varint(9) + b'localhost' + b'c\xdd' + varint(2)
        self.assertTrue(minecraft_handshake_state(varint(len(body)) + body))

    def test_partial_and_non_minecraft_payload(self):
        body = varint(0) + varint(765) + varint(9) + b'localhost' + b'c\xdd' + varint(2)
        packet = varint(len(body)) + body
        self.assertIsNone(minecraft_handshake_state(packet[:3]))
        self.assertFalse(minecraft_handshake_state(varint(3) + b'GET'))


if __name__ == '__main__':
    unittest.main()
