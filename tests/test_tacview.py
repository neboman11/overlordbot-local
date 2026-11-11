import importlib.util
import os
import socket
import sys
import threading
import time
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from overlord import tacview  # noqa: E402
from overlord.tacview import World  # noqa: E402

FIXTURE = os.path.join(ROOT, "fixtures", "replay.acmi")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def by_id(world, uid, coalition=None):
    return next(u for u in world.snapshot(coalition) if u["id"] == uid)


class WorldParsing(unittest.TestCase):
    def feed(self, w, *lines):
        for line in lines:
            w.feed_line(line)

    def test_six_field_coordinates_and_reference(self):
        w = World()
        self.feed(w, "FileType=text/acmi/tacview", "0,ReferenceLongitude=40", "0,ReferenceLatitude=42",
                  "a1,T=0.5|0.25|1000|1|2|90,Name=F-16C_50,Pilot=Viper 1-1,Color=Blue,Type=Air+FixedWing")
        u = by_id(w, "a1")
        self.assertAlmostEqual(u["lon"], 40.5)
        self.assertAlmostEqual(u["lat"], 42.25)
        self.assertEqual(u["altitude"], 1000)
        self.assertEqual(u["heading"], 90)
        self.assertEqual(u["coalition"], 2)
        self.assertEqual(u["callsign"], "Viper 1-1")
        self.assertEqual(u["type"], "F-16C_50")
        self.assertEqual(u["category"], "Airplane")

    def test_three_and_nine_fields(self):
        w = World()
        self.feed(w, "1,T=41|43|500", "2,T=41|43|500|1|2|3|4|5|271.5")
        self.assertEqual(by_id(w, "1")["heading"], 0.0)
        self.assertEqual(by_id(w, "2")["heading"], 3)

    def test_partial_update_retains_fields(self):
        w = World()
        self.feed(w, "1,T=41|43|500|0|0|45,Pilot=Viper 1-1,Color=Blue,Name=F-16C_50",
                  "1,T=42||600")
        u = by_id(w, "1")
        self.assertEqual((u["lon"], u["lat"], u["altitude"]), (42, 43, 600))
        self.assertEqual(u["heading"], 45)
        self.assertEqual(u["callsign"], "Viper 1-1")
        self.assertEqual(u["coalition"], 2)

    def test_escaped_comma(self):
        w = World()
        self.feed(w, r"1,T=41|43|5,Pilot=Smith\, John,Name=F-16C_50")
        self.assertEqual(by_id(w, "1")["pilot"], "Smith, John")

    def test_multiline_continuation(self):
        w = World()
        self.feed(w, "0,Briefing=line one\\", "line two", "1,T=41|43|5")
        self.assertEqual(len(w.snapshot()), 1)

    def test_deletion(self):
        w = World()
        self.feed(w, "#0", "1,T=41|43|5", "2,T=41|43|5", "-1")
        self.assertEqual([u["id"] for u in w.snapshot()], ["2"])
        self.assertEqual(w.time, 0)

    def test_category_and_coalition(self):
        w = World()
        self.feed(w, "1,T=41|43|5,Type=Air+Rotorcraft,Color=Red",
                  "2,T=41|43|5,Type=Ground+Static+Aerodrome,Color=Green",
                  "3,T=41|43|5,Type=Air+FixedWing,Coalition=Blue",
                  "4,T=41|43|5,Type=Air+FixedWing,Coalition=Enemies,Color=Violet",
                  "5,T=41|43|5")
        self.assertEqual(by_id(w, "1")["category"], "Helicopter")
        self.assertEqual(by_id(w, "1")["coalition"], 1)
        self.assertEqual(by_id(w, "2")["category"], "Airfield")
        self.assertEqual(by_id(w, "3")["coalition"], 2)
        # Unknown coalition is never red/blue.
        self.assertEqual(by_id(w, "4")["coalition"], 0)
        self.assertEqual(by_id(w, "5")["coalition"], 0)

    def test_coalition_filter(self):
        w = World()
        self.feed(w, "1,T=41|43|5,Color=Blue", "2,T=41|43|5,Color=Red", "3,T=41|43|5")
        self.assertEqual({u["id"] for u in w.snapshot(2)}, {"1"})
        self.assertEqual({u["id"] for u in w.snapshot(1)}, {"2"})
        self.assertEqual({u["id"] for u in w.snapshot(0)}, {"3"})
        self.assertEqual(len(w.snapshot()), 3)

    def test_invalid_values_rejected(self):
        w = World()
        self.feed(w, "1,T=41|43|5,Color=Blue", "1,T=nan|43|5", "1,T=41|inf|5", "1,T=41|43|-inf",
                  "1,T=41|95|5", "2,T=nan|nan|nan", "1,T=41|43", "#nan")
        u = by_id(w, "1")
        self.assertEqual((u["lon"], u["lat"], u["altitude"]), (41, 43, 5))
        self.assertEqual(len(w.snapshot()), 1)

    def test_object_without_position_hidden_and_snapshot_copied(self):
        w = World()
        self.feed(w, "1,Name=X", "2,T=41|43|5")
        snap = w.snapshot()
        self.assertEqual(len(snap), 1)
        snap[0]["lat"] = 0
        self.assertEqual(w.snapshot()[0]["lat"], 43)

    def test_stale_and_ready(self):
        clock = Clock()
        w = World(stale_seconds=30, clock=clock)
        self.assertFalse(w.ready)
        w.feed_line("#0")
        self.assertFalse(w.ready)  # no positions yet
        w.feed_line("1,T=41|43|5")
        self.assertTrue(w.ready)
        clock.t += 29
        self.assertTrue(w.ready)
        clock.t += 2
        self.assertFalse(w.ready)
        w.feed_line("#1")
        self.assertTrue(w.ready)

    def test_reset_drops_state(self):
        w = World()
        self.feed(w, "0,ReferenceLongitude=10", "1,T=1|43|5")
        w.reset()
        self.assertFalse(w.ready)
        self.assertEqual(w.snapshot(), [])
        self.feed(w, "1,T=1|43|5")
        self.assertEqual(by_id(w, "1")["lon"], 1)  # reference was reset

    def test_fixture(self):
        w = World()
        with open(FIXTURE, encoding="utf-8") as fh:
            w.feed_text(fh.read())
        ids = {u["id"] for u in w.snapshot()}
        self.assertEqual(ids, {"100", "101", "200", "300"})  # 900 deleted
        viper = by_id(w, "100")
        self.assertEqual(viper["callsign"], "Viper 1-1")
        self.assertEqual(viper["coalition"], 2)
        self.assertEqual(by_id(w, "200")["coalition"], 1)
        self.assertEqual(by_id(w, "300")["category"], "Airfield")


    def test_speed_uses_completed_frames_including_unchanged_objects(self):
        w = World()
        self.feed(w, "#0", "1,T=41|43|5", "#10")
        self.assertNotIn("speed", by_id(w, "1"))
        self.feed(w, "1,T=41.001|43|5", "#20")
        self.assertAlmostEqual(by_id(w, "1")["speed"], 8.14, delta=0.2)
        # ACMI omits unchanged positions; unrelated traffic keeps stream alive.
        self.feed(w, "2,T=42|43|5", "#30")
        self.assertAlmostEqual(by_id(w, "1")["speed"], 0.0, delta=0.01)

    def test_projected_heading_does_not_change_geographic_aspect(self):
        w = World()
        self.feed(w, "1,T=41|43|500|1|2|90",
                  "2,T=41|43|500|1|2|90|100|200|271.5")
        self.assertEqual(by_id(w, "1")["heading"], by_id(w, "2")["heading"])


class Handshake(unittest.TestCase):
    def test_roundtrip(self):
        name, pw = tacview.parse_handshake(tacview.build_client_handshake("secret", "Bot").rstrip(b"\0"))
        self.assertEqual(name, "Bot")
        self.assertEqual(pw, tacview.password_hash("secret"))
        self.assertEqual(tacview.password_hash(""), tacview.password_hash("0"))
        self.assertNotEqual(tacview.password_hash("secret"), tacview.password_hash(""))
        self.assertEqual(tacview.parse_handshake(tacview.build_server_greeting("H").rstrip(b"\0")), ("H", ""))

    def test_crc64_we_check_value(self):
        self.assertEqual(tacview.crc64_we(b"123456789"), 0x62EC59E3F1A4F00A)

    def test_password_hash_is_utf16le_crc(self):
        self.assertEqual(tacview.password_hash("secret"),
                         format(tacview.crc64_we("secret".encode("utf-16-le")), "x"))

    def test_bad_protocol(self):
        with self.assertRaises(ValueError):
            tacview.parse_handshake(b"HTTP/1.1 200 OK\n\n")

    def test_line_splitter(self):
        s = tacview.LineSplitter()
        self.assertEqual(s.feed(b"ab\r\ncd"), ["ab"])
        self.assertEqual(s.feed(b"e\nf\n"), ["cde", "f"])


class Network(unittest.TestCase):
    def test_replay_server_and_reconnect(self):
        spec = importlib.util.spec_from_file_location(
            "replay_tacview", os.path.join(ROOT, "scripts", "replay_tacview.py"))
        replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(replay)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        stop_server = threading.Event()
        threading.Thread(target=replay.serve, daemon=True,
                         args=("127.0.0.1", port, FIXTURE, 0.02, "pw", stop_server)).start()

        world = World()
        stop = threading.Event()
        t = threading.Thread(target=world.run, args=("127.0.0.1", port, "pw", stop), daemon=True)
        t.start()
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not (world.ready and len(world.snapshot()) >= 4):
                time.sleep(0.05)
            self.assertTrue(world.ready)
            self.assertIn("Viper 1-1", {u["callsign"] for u in world.snapshot(2)})
        finally:
            stop.set()
            stop_server.set()
            t.join(5)


if __name__ == "__main__":
    unittest.main()
