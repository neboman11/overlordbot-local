"""Behavioural tests of the ported AWACS/ATC responders against canned worlds.

Scenarios port OverlordBot.Core.Tests (AwacsResponder/, AtcResponder/, AtcLogic/).
Magnetic bearings are pinned with ``when=2026-01-01`` and NOAA WMM2025, so the
original's "3 5 3" (WMM2020, 2021) stays "3 5 3" at 41N 37E while the
original's "0 0 4" at 0N 0E is "0 0 3" in 2026 (declination -3.89 degrees).
"""

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from overlord import domain  # noqa: E402
from overlord.domain import AtcMonitor, WarningMonitor, handle  # noqa: E402
from overlord.tacview import World  # noqa: E402

WHEN = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
BLUE, RED = 2, 1


def unit(uid, lat, lon, coalition=BLUE, type_="F-15C", altitude=0.0, heading=0.0, speed=200.0,
         pilot="", category="Airplane"):
    u = {"id": uid, "name": pilot or type_, "pilot": pilot, "callsign": pilot, "coalition": coalition,
         "type": type_, "category": category, "lat": lat, "lon": lon, "altitude": altitude,
         "heading": heading, "group": ""}
    if speed is not None:
        u["speed"] = speed
    return u


def me(lat=41.0, lon=37.0, type_="F-15C", **kw):
    return unit("me", lat, lon, type_=type_, pilot="Dolt 1-2", **kw)


def fishbed(uid="b1", lat=41.5, altitude=1450.0, **kw):
    return unit(uid, lat, 37.0, RED, "MiG-21Bis", altitude, **kw)


def say(text, units, **kw):
    return handle(text, BLUE, units, when=WHEN, **kw)["reply"]


class MagneticModel(unittest.TestCase):
    def test_matches_noaa_wmm2025_test_values(self):
        path = os.path.join(domain.DATA_DIR, "WMM2025_TestValues.txt")
        with open(path, encoding="utf-8") as f:
            rows = [list(map(float, line.split())) for line in f if line.strip() and not line.startswith("#")]
        self.assertEqual(len(rows), 100)
        for year, alt_km, lat, lon, decl, *_ in rows:
            x, y, _z = domain.magnetic_field(lat, lon, alt_km, year)
            self.assertAlmostEqual(domain.math.degrees(domain.math.atan2(y, x)), decl, delta=0.006)

    def test_vincenty_reference_line(self):
        distance, azimuth = domain.inverse(-37.95103341666667, 144.42486788888888,
                                           -37.65282113888889, 143.92649552777777)
        self.assertAlmostEqual(distance, 54972.271, places=2)
        self.assertAlmostEqual(azimuth, 306.86816, places=4)


class Phraseology(unittest.TestCase):
    def test_altitude_words(self):
        self.assertEqual(domain.altitude_to_words(1450, metric=False), "five thousand")
        self.assertEqual(domain.altitude_to_words(12000, metric=False), "thirty-nine thousand")
        self.assertEqual(domain.altitude_to_words(1450, metric=True), "one thousand four hundred")
        self.assertEqual(domain.altitude_to_words(100, metric=False), "five hundred")
        self.assertEqual(domain.number_to_words(1050), "one thousand and fifty")

    def test_aspect(self):
        self.assertEqual(domain.aspect(0, 0), "DRAG NORTH")
        self.assertEqual(domain.aspect(0, 90), "BEAM EAST")
        self.assertEqual(domain.aspect(0, 225), "FLANK SOUTH WEST")
        self.assertEqual(domain.aspect(0, 180), "HOT")


class BogeyDope(unittest.TestCase):
    def test_nothing_on_scope(self):
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [me()]), "dolt 1 2, overlord, Nothing on Scope")

    def test_imperial_and_metric(self):
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [me(), fishbed()]),
                         "dolt 1 2, overlord, bra, 3 5 3, 29, five thousand, DRAG NORTH, Fishbed")
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [me(type_="MiG-21Bis"), fishbed()]),
                         "dolt 1 2, overlord, bra, 3 5 3, 55, one thousand four hundred, DRAG NORTH, Fishbed")

    def test_comments(self):
        cases = ((dict(altitude=12000), "thirty-nine thousand, DRAG NORTH, Fishbed"),
                 (dict(altitude=15000), "forty-nine thousand, DRAG NORTH, high, Fishbed"),
                 (dict(altitude=1500, speed=500), "five thousand, DRAG NORTH, very fast, Fishbed"),
                 (dict(altitude=1500, speed=400), "five thousand, DRAG NORTH, fast, Fishbed"),
                 (dict(altitude=15000, speed=400), "forty-nine thousand, DRAG NORTH, high, fast, Fishbed"),
                 (dict(altitude=100), "five hundred, DRAG NORTH, Fishbed"))
        for kw, tail in cases:
            with self.subTest(kw):
                self.assertEqual(say("overlord dolt 1 2 bogey dope", [me(), fishbed(**kw)]),
                                 f"dolt 1 2, overlord, bra, 3 5 3, 29, {tail}")
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [me(type_="MiG-21Bis"), fishbed(altitude=15000)]),
                         "dolt 1 2, overlord, bra, 3 5 3, 55, fifteen thousand, DRAG NORTH, high, Fishbed")

    def test_group_counts(self):
        two = [fishbed(), fishbed("b2", 41.50001)]
        three = two + [fishbed("b3", 41.50002)]
        mixed = [fishbed(), unit("b2", 41.50001, 37.0, RED, "MiG-19P", 1450)]
        pair_single = two + [unit("b3", 41.50002, 37.0, RED, "MiG-19P", 1450)]
        for contacts, tail in ((two, "2-ship Fishbed"), (three, "3-ship Fishbed"),
                               (mixed, "Farmer, Fishbed"), (pair_single, "Farmer, 2-ship Fishbed")):
            with self.subTest(tail):
                self.assertEqual(say("overlord dolt 1 2 bogey dope", [me()] + contacts),
                                 f"dolt 1 2, overlord, bra, 3 5 3, 29, five thousand, DRAG NORTH, {tail}")

    def test_coalition_safe_filtering(self):
        friendly = unit("f1", 41.2, 37.0, BLUE, "F-16C_50", 3000)
        unknown = unit("n1", 41.2, 37.0, 0, "MiG-29S", 3000)
        parked = unit("p1", 41.2, 37.0, RED, "MiG-29S", 3000, speed=None)
        slow = unit("s1", 41.2, 37.0, RED, "MiG-29S", 3000, speed=20)
        ground = unit("g1", 41.2, 37.0, RED, "T-72B", 300, category="Ground")
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [me(), friendly, unknown, parked, slow, ground]),
                         "dolt 1 2, overlord, Nothing on Scope")

    def test_red_caller_gets_blue_contacts(self):
        red_me = unit("r", 41.0, 37.0, RED, "Su-27", 5000, pilot="Dolt 1-2")
        blue = unit("b", 41.5, 37.0, BLUE, "F-16C_50", 1450)
        self.assertEqual(handle("overlord dolt 1 2 bogey dope", RED, [red_me, blue], when=WHEN)["reply"],
                         "dolt 1 2, overlord, bra, 3 5 3, 55, one thousand four hundred, DRAG NORTH, Falcon")

    def test_caller_of_other_coalition_is_not_on_scope(self):
        red_me = unit("r", 41.0, 37.0, RED, "Su-27", 5000, pilot="Dolt 1-2")
        self.assertEqual(say("overlord dolt 1 2 bogey dope", [red_me, fishbed()]),
                         "dolt 1 2, overlord, i could not find you on scope")

    def test_attribute_qualifier(self):
        hip = unit("h", 41.2, 37.0, RED, "Mi-8MT", 300, speed=50, category="Helicopter")
        self.assertEqual(say("overlord dolt 1 2 bogey dope helicopters", [me(), fishbed(), hip]),
                         "dolt 1 2, overlord, bra, 3 5 3, 11, one thousand, DRAG NORTH, Hip")
        self.assertEqual(say("overlord dolt 1 2 bogey dope fishbed", [me(), fishbed(), hip]),
                         "dolt 1 2, overlord, bra, 3 5 3, 29, five thousand, DRAG NORTH, Fishbed")


class Recognition(unittest.TestCase):
    def test_receiver_callsign_rules(self):
        world = [me()]
        self.assertIsNone(say("magic dolt 1 2 radio check", world))
        self.assertEqual(handle("magic dolt 1 2 radio check", BLUE, world, None, when=WHEN)["reply"],
                         "dolt 1 2, magic, 5 by 5")
        self.assertEqual(handle("anyface dolt 1 2 radio check", BLUE, world, None, when=WHEN)["reply"],
                         "dolt 1 2, overlord, 5 by 5")
        self.assertEqual(handle("anyface dolt 1 2 radio check", BLUE, world, "magic", when=WHEN)["reply"],
                         "dolt 1 2, magic, 5 by 5")

    def test_transmitter_recognition(self):
        self.assertEqual(say("overlord dolt radio check", [me()]),
                         "dolt x x, overlord, i could not fully recognize your callsign")
        self.assertEqual(say("overlord radio check", [me()]),
                         "last transmitter, overlord, i could not recognize your callsign")
        self.assertEqual(say("overlord dolt 1 2 radio check", []), "dolt 1 2, overlord, i could not find you on scope")

    def test_duplicate_callsign(self):
        twin = unit("me2", 42.0, 37.0, pilot="DOLT 12")
        self.assertEqual(say("overlord dolt 1 2 radio check", [me(), twin]),
                         "dolt 1 2, overlord, I found more than one unit with a matching callsign")
        self.assertEqual(say("overlord dolt 1 2 radio check", [me(), unit("x", 42, 37, pilot="Dolt 1-21")]),
                         "dolt 1 2, overlord, 5 by 5")

    def test_roles_and_unaddressed(self):
        self.assertIsNone(handle("kutaisi tower dolt 1 2 radio check", BLUE, [me()], roles=("awacs",))["reply"])
        self.assertIsNone(handle("overlord dolt 1 2 radio check", BLUE, [me()], roles=("atc",))["reply"])
        result = handle("what is the weather like", BLUE, [me()])
        self.assertIsNone(result["reply"])
        self.assertEqual(result["parsed"]["intent"], "None")
        self.assertEqual(handle("", BLUE, []), {"reply": None, "parsed": None, "notes": None})

    def test_awacs_on_station(self):
        self.assertEqual(say("overlord dolt 1 2 radio check", [me()], awacs_pilot="Overlord AWACS"),
                         "overlord, overlord is not on station")
        awacs = unit("aw", 42.0, 37.0, BLUE, "E-3A", 9000, pilot="Overlord AWACS 1")
        self.assertEqual(say("overlord dolt 1 2 radio check", [me(), awacs], awacs_pilot="Overlord AWACS"),
                         "dolt 1 2, overlord, 5 by 5")


class Location(unittest.TestCase):
    def test_airfield(self):
        krymsk = unit("a", 0.2, 0.0, RED, "Krymsk", category="Airfield", speed=None)
        friendly_krymsk = dict(krymsk, coalition=BLUE)
        self.assertEqual(say("overlord dolt 1 2 bearing to krymsk", [me(0, 0), krymsk]),
                         "dolt 1 2, overlord, Hostile airfield, Krymsk, bearing 0 0 3, 11")
        self.assertEqual(say("overlord dolt 1 2 bearing to krymsk", [me(0, 0, "MiG-21Bis"), friendly_krymsk]),
                         "dolt 1 2, overlord, Krymsk, bearing 0 0 3, 22")
        self.assertEqual(say("overlord dolt 1 2 what's the nearest airfield", [me(0, 0), friendly_krymsk]),
                         "dolt 1 2, overlord, Krymsk, bearing 0 0 3, 11")

    def test_unknown_ownership_is_not_called_neutral(self):
        neutral = unit("a", 0.2, 0.0, 0, "Krymsk", category="Airfield", speed=None)
        self.assertEqual(say("overlord dolt 1 2 bearing to krymsk", [me(0, 0), neutral]),
                         "dolt 1 2, overlord, Krymsk, bearing 0 0 3, 11")

    def test_encyclopedia_airfield_when_tacview_has_none(self):
        self.assertEqual(say("overlord dolt 1 2 vector to krymsk", [me(44.9, 38.0)]),
                         "dolt 1 2, overlord, Krymsk, bearing 3 4 3, 3")

    def test_unknown_airfield(self):
        self.assertEqual(say("overlord dolt 1 2 where is ras assanya", [me(0, 0)]),
                         "dolt 1 2, overlord, I could not find ras assanya")

    def test_unit(self):
        wingman = unit("w", 0.2, 0.0, BLUE, "F-15C", 1000, pilot="DOLT 1-3")
        self.assertEqual(say("overlord dolt 1 2 where is dolt 1 3", [me(0, 0), wingman]),
                         "dolt 1 2, overlord, Bra, 0 0 3, 11, Angels 3")
        self.assertEqual(say("overlord dolt 1 2 where is dolt 1 3", [me(0, 0, "MiG-29A"), wingman]),
                         "dolt 1 2, overlord, Bra, 0 0 3, 22, Angels 1")
        enemy_namesake = dict(wingman, coalition=RED)
        self.assertEqual(say("overlord dolt 1 2 where is dolt 1 3", [me(0, 0), enemy_namesake]),
                         "dolt 1 2, overlord, I could not find dolt 1 3")

    def test_tanker_unsupported(self):
        self.assertEqual(say("overlord dolt 1 2 bearing to texaco", [me()]),
                         "dolt 1 2, overlord, we do not yet support vectors to tankers")


class PictureDeclare(unittest.TestCase):
    def test_picture(self):
        self.assertEqual(say("overlord dolt 1 2 request picture", [me()]), "dolt 1 2, overlord, clean")
        flanker = unit("f", 40.3, 37.0, RED, "Su-27", 8000, heading=0)
        self.assertEqual(say("overlord dolt 1 2 picture", [me(), fishbed(), fishbed("b2", 41.50001), flanker]),
                         "dolt 1 2, overlord, 2 groups, bra, 3 5 3, 29, five thousand, DRAG NORTH, 2-ship Fishbed, "
                         "bra, 1 7 3, 41, twenty-six thousand, HOT, Flanker")

    def test_declare(self):
        call = "overlord dolt 1 2 declare 353 for 29"
        self.assertEqual(say(call, [me(), fishbed()]), "dolt 1 2, overlord, hostile, Fishbed")
        self.assertEqual(say(call, [me(), unit("f", 41.5, 37.0, BLUE, "F-16C_50", 1450)]), "dolt 1 2, overlord, friendly")
        self.assertEqual(say(call, [me(), unit("n", 41.5, 37.0, 0, "MiG-21Bis", 1450)]), "dolt 1 2, overlord, bogey")
        self.assertEqual(say(call, [me(), fishbed(), unit("f", 41.51, 37.0, BLUE, "F-16C_50", 1450)]),
                         "dolt 1 2, overlord, furball")
        self.assertEqual(say("overlord dolt 1 2 declare 90 for 20", [me(), fishbed()]), "dolt 1 2, overlord, clean")
        self.assertEqual(say("overlord dolt 1 2 declare", [me(), fishbed()]),
                         "dolt 1 2, overlord, unable to declare, say bearing and range")

    def test_declare_from_bullseye(self):
        bullseye = unit("bull", 41.0, 37.0, BLUE, "Bullseye", speed=None, category="Navaid")
        bullseye["tacview_type"] = "Navaid+Static+Bullseye"
        red_bullseye = dict(bullseye, id="rbull", coalition=RED, lat=40.0)
        caller = me(40.0, 36.0)
        world = [caller, fishbed(), bullseye, red_bullseye]
        call = {"bearing": 353, "range": 29}
        self.assertEqual(domain._declare(call, caller, world, WHEN), "clean")             # from the requester
        self.assertEqual(domain._declare(dict(call, reference="requester"), caller, world, WHEN), "clean")
        self.assertEqual(domain._declare(dict(call, reference="bullseye"), caller, world, WHEN),
                         "hostile, Fishbed")                                                # from blue bullseye
        self.assertEqual(domain._declare(dict(call, reference="bullseye"), caller, [caller, fishbed(), red_bullseye],
                                         WHEN), "unable, bullseye reference unavailable")


class WarningRadius(unittest.TestCase):
    def test_without_monitor_is_an_honest_negative(self):
        self.assertEqual(say("overlord dolt 1 2 set tripwire 40 miles", [me()]),
                         "dolt 1 2, overlord, unable, warning radius monitoring is not available")
        self.assertEqual(say("overlord dolt 1 2 cancel tripwire", [me()]),
                         "dolt 1 2, overlord, unable, warning radius monitoring is not available")

    def test_set_check_cancel(self):
        monitor = WarningMonitor()
        world = [me(), fishbed()]
        self.assertEqual(say("overlord dolt 1 2 set tripwire 40 miles", world, monitor=monitor),
                         "dolt 1 2, overlord, copy, warning 40 nautical miles")
        self.assertEqual(say("overlord dolt 1 2 set warning 3", world, monitor=monitor),
                         "dolt 1 2, overlord, copy, merge calls only")
        self.assertEqual(say("overlord dolt 1 2 set tripwire 40 miles", world, monitor=monitor),
                         "dolt 1 2, overlord, copy, warning 40 nautical miles")
        self.assertEqual(monitor.check(world, WHEN), [
            {"unit_id": "me", "coalition": BLUE,
             "reply": "dolt 1 2, threat, bra, 3 5 3, 29, five thousand, DRAG NORTH, Fishbed"}])
        self.assertEqual(monitor.check(world, WHEN), [])
        merged = world + [unit("f", 41.02, 37.0, RED, "Su-27", 5000, heading=180)]
        self.assertEqual(monitor.check(merged, WHEN), [
            {"unit_id": "me", "coalition": BLUE, "reply": "dolt 1 2, merged, 3 5 3, sixteen thousand, Flanker"}])
        self.assertEqual(say("overlord dolt 1 2 cancel tripwire", world, monitor=monitor),
                         "dolt 1 2, overlord, copy, cancelling warnings")
        self.assertEqual(monitor.monitored(), {})

    def test_stationary_and_outside_radius(self):
        monitor = WarningMonitor()
        say("overlord dolt 1 2 set tripwire 20 miles", [me()], monitor=monitor)
        self.assertEqual(monitor.check([me(), fishbed()], WHEN), [])            # 29 nm > 20 nm
        self.assertEqual(monitor.check([me(speed=5), fishbed(lat=41.2)], WHEN), [])  # caller on the ground
        self.assertEqual(monitor.check([fishbed()], WHEN), [])                   # caller gone: dropped
        self.assertEqual(monitor.monitored(), {})

    def test_distance_not_heard(self):
        self.assertEqual(say("overlord dolt 1 2 set warning miles", [me()], monitor=WarningMonitor()),
                         "dolt 1 2, overlord, i could not catch the distance")


class Missions(unittest.TestCase):
    def test_no_mission_service_means_no_fake_success(self):
        world = [me()]
        self.assertEqual(say("overlord dolt 1 2 request mission", world),
                         "dolt 1 2, overlord, I did not hear the mission type")
        self.assertEqual(say("overlord dolt 1 2 request strike mission", world),
                         "dolt 1 2, overlord, unable to forward request")
        self.assertEqual(say("overlord dolt 1 2 join mission", world), "dolt 1 2, overlord, I did not catch the mission ID")
        self.assertEqual(say("overlord dolt 1 2 join mission 5150", world), "dolt 1 2, overlord, unable to forward request")


TAXI_SCENARIOS = (  # OverlordBot.Core.Tests/AtcLogic/TaxiToActiveRunwayRoutingTests.cs
    ("Al Dhafra AFB", 310, (24.244862290776, 54.562445823216), "Runway-3 1 Right", ["Alfa"]),
    ("Al Dhafra AFB", 170, (24.244862290776, 54.562445823216), "Runway-1 3 Left", ["Alfa", "Foxtrot"]),
    ("Al Dhafra AFB", 310, (24.216944646008, 54.563235561423), "Runway-3 1 Left", ["Uniform", "Uniform 0"]),
    ("Al Dhafra AFB", 10, (24.216944646008, 54.563235561423), "Runway-3 1 Left", ["Uniform", "Uniform 0"]),
    ("Anapa-Vityazevo", 90, (45.0101581, 37.3481765), "Runway-0 4", ["Mike", "Alpha"]),
    ("Anapa-Vityazevo", 90, (45.0094606, 37.3635130), "Runway-0 4", ["Echo", "Delta", "Mike", "Alpha"]),
    ("Anapa-Vityazevo", 90, (45.0101581, 37.3481765), "Runway-0 4", ["Mike", "Alpha"]),  # wind -1 -> 90
    ("Anapa-Vityazevo", 350, (45.0101581, 37.3481765), "Runway-0 4", ["Mike", "Alpha"]),
    ("Anapa-Vityazevo", 10, (45.0101581, 37.3481765), "Runway-0 4", ["Mike", "Alpha"]),
    ("Anapa-Vityazevo", 270, (45.0101581, 37.3481765), "Runway-2 2", ["Mike", "Delta"]),
    ("Anapa-Vityazevo", 270, (45.0094606, 37.3635130), "Runway-2 2", ["Echo"]),
    ("Anapa-Vityazevo", 40, (45.0094606, 37.3635130), "Runway-0 4", ["Echo", "Delta", "Mike", "Alpha"]),
    ("Krasnodar-Center", 340, (45.082339143765, 38.954220071576), "Runway-2 7", ["Echo"]),
    ("Krasnodar-Center", 270, (45.082339143765, 38.954220071576), "Runway-2 7", ["Echo"]),
    ("Krasnodar-Pashkovsky", 90, (45.038881971882, 39.14140614585), "Runway-0 5 Left", ["Alfa"]),
    ("Krasnodar-Pashkovsky", 90, (45.047601257612, 39.200777416505), "Runway-0 5 Right", ["November", "Echo"]),
    ("Mineralnye Vody", 90, (44.223130773545, 43.104834499253), "Runway-1 2", ["November", "Echo", "Alfa"]),
    ("Maykop-Khanskaya", 270, (44.671288619807, 40.030771437314), "Runway-2 2", ["Victor", "Echo"]),
)


class Atc(unittest.TestCase):
    def test_encyclopedia_is_complete(self):
        self.assertEqual(len(domain.AIRFIELDS), 25)
        self.assertTrue(all(a.runways and a.edge_count for a in domain.AIRFIELDS))

    def test_taxi_routing_scenarios(self):
        for name, wind, (lat, lon), runway, taxiways in TAXI_SCENARIOS:
            with self.subTest(name=name, wind=wind, start=(lat, lon)):
                airfield = domain.encyclopedia_airfield(name)
                self.assertEqual(airfield.taxi_instructions({"lat": lat, "lon": lon}, wind), (runway, taxiways))

    def test_ready_to_taxi(self):
        parked = me(45.0101581, 37.3481765, speed=0)
        self.assertEqual(say("anapa ground dolt 1 2 ready to taxi", [parked], wind=90),
                         "dolt 1 2, anapa ground, taxi to Runway-0 4 via Mike, Alpha.")
        echo = me(45.0094606, 37.3635130, speed=0)
        self.assertEqual(say("anapa ground dolt 1 2 request taxi", [echo], wind={"Anapa-Vityazevo": 270}),
                         "dolt 1 2, anapa ground, taxi to Runway-2 2 via Echo.")

    def test_inbound(self):
        self.assertEqual(say("anapa approach dolt 1 2 inbound", [me(45.3, 37.6)], wind=40),
                         "dolt 1 2, anapa approach, fly heading 2 1 5, descend and maintain 2,000, "
                         "reduce speed below 2 5 0 knots, for vectors to Runway-0 4, Entry-Exit West")

    def test_no_wind_is_an_honest_negative(self):
        self.assertEqual(say("kutaisi tower dolt 1 2 inbound", [me()]),
                         "dolt 1 2, Kutaisi tower, wind information is unavailable, unable to determine the active runway")

    def test_other_calls(self):
        world = [me()]
        self.assertEqual(say("dubai tower dolt 1 2 inbound", world, wind=40),
                         "dolt 1 2, Dubai tower, There are no ATC services currently available at this airfield.")
        self.assertEqual(say("kutaisi tower dolt 1 2 bogey dope", world),
                         "dolt 1 2, Kutaisi tower, this is an ATC frequency and we only respond to ATC calls")
        self.assertEqual(say("overlord dolt 1 2 ready to taxi", world),
                         "dolt 1 2, overlord, this is an AYWACS frequency and we only respond to AYWACS calls")
        self.assertEqual(say("krymsk tower dolt 1 2 abort inbound", world),
                         "dolt 1 2, Krymsk tower, Copy abort, call inbound again when ready")
        self.assertEqual(say("kutaisi ground dolt 1 2 radio check", world), "dolt 1 2, Kutaisi ground, 5 by 5")
        self.assertEqual(say("kutaisi ground dolt radio check", world),
                         "dolt x x, Kutaisi ground, i could not fully recognize your callsign")
        self.assertEqual(say("kutaisi ground radio check", world),
                         "last transmitter, Kutaisi ground, i could not recognize your callsign")
        self.assertIsNone(say("kutaisi dolt 1 2 radio check", world))   # no controller name: ignored


ANAPA = domain.encyclopedia_airfield("Anapa-Vityazevo")
RAMP = (45.0101581, 37.3481765)          # taxi to Runway-0 4 via Mike, Alpha with wind 90
ECHO = (45.0094606, 37.3635130)          # taxi to Runway-0 4 via Echo (back-taxi from Runway-2 2) with wind 90
INBOUND = (45.3, 37.6)                   # inbound via Entry-Exit West, Initial-0 4 to Runway-0 4 with wind 90


def pilot(uid, callsign, position, **kw):
    kw.setdefault("speed", 0.0)
    kw.setdefault("altitude", ANAPA.altitude)
    return unit(uid, *position, pilot=callsign, **kw)


def at(u, point_name, **kw):
    point = next(v for v in ANAPA.vertices if v["name"] == point_name)
    return dict(u, lat=point["lat"], lon=point["lon"], **kw)


def short_of(u, point_name, metres):
    """``u`` moved ``metres`` before ``point_name`` on its present line towards it."""
    point = next(v for v in ANAPA.vertices if v["name"] == point_name)
    back = (domain.true_bearing(u, point) + 180) % 360
    lat, lon = domain.destination(point["lat"], point["lon"], back, metres)
    return dict(u, lat=lat, lon=lon)


class AtcFollowUp(unittest.TestCase):
    """AtcMonitor/ControlledAircraft transitions driven by telemetry position, speed and altitude."""

    def setUp(self):
        self.monitor = AtcMonitor()

    def call(self, text, world):
        return handle(text, BLUE, world, wind=90, when=WHEN, atc_monitor=self.monitor)["reply"]

    def check(self, world):
        return [(c["unit_id"], c["reply"]) for c in self.monitor.check(world, WHEN)]

    def test_initial_replies_unchanged_and_tracked(self):
        a = pilot("a", "Dolt 1-1", RAMP)
        self.assertEqual(self.call("anapa ground dolt 1 1 ready to taxi", [a]),
                         "dolt 1 1, anapa ground, taxi to Runway-0 4 via Mike, Alpha.")
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.assertEqual(self.call("anapa approach dolt 1 3 inbound", [a, c]),
                         "dolt 1 3, anapa approach, fly heading 2 1 5, descend and maintain 2,000, "
                         "reduce speed below 2 5 0 knots, for vectors to Runway-0 4, Entry-Exit West")
        self.assertEqual(self.monitor.tracked(), {"a": "TaxiToRunway", "c": "Inbound"})
        self.assertEqual(self.check([a, c]), [])   # nothing has moved: nothing to say

    def test_negative_replies_are_not_tracked(self):
        a = pilot("a", "Dolt 1-1", RAMP)
        self.assertEqual(handle("anapa ground dolt 1 1 ready to taxi", BLUE, [a], when=WHEN, atc_monitor=self.monitor)
                         ["reply"], "dolt 1 1, anapa ground, " + domain.NO_WIND_MESSAGE)
        self.assertEqual(self.call("dubai ground dolt 1 1 ready to taxi", [a]),
                         "dolt 1 1, Dubai ground, There are no ATC services currently available at this airfield.")
        self.assertEqual(self.call("kobuleti approach dolt 1 1 inbound", [a]),       # no "Entry" waypoints
                         "dolt 1 1, Kobuleti approach, We could not find an approach route to the active runway.")
        self.assertEqual(self.monitor.tracked(), {})

    def test_departure_sequence(self):
        a, b = pilot("a", "Dolt 1-1", RAMP), pilot("b", "Dolt 1-2", RAMP)
        self.call("anapa ground dolt 1 1 ready to taxi", [a, b])
        a = at(a, "Runway-0 4")
        self.assertEqual(self.check([a, b]), [("a", "dolt 1 1, Take-off Runway-0 4 at your discretion")])
        self.call("anapa ground dolt 1 2 ready to taxi", [a, b])
        b = at(b, "Runway-0 4")
        self.assertEqual(self.check([a, b]), [("b", "dolt 1 2, hold short of runway")])
        a = dict(a, speed=35)                                     # take-off roll
        self.assertEqual(self.check([a, b]), [])
        self.assertEqual(self.monitor.tracked(), {"a": "Rolling", "b": "HoldingShort"})
        self.assertEqual(self.check([a, b]), [("b", "dolt 1 2, Line up and wait")])
        a = dict(a, speed=70, altitude=ANAPA.altitude + 20)       # fast but below 33 m: still rolling
        self.assertEqual(self.check([a, b]), [])
        a = dict(a, altitude=ANAPA.altitude + 40)                 # airborne: leaves ATC control
        self.assertEqual(self.check([a, b]), [])
        self.assertEqual(self.monitor.tracked(), {"b": "LinedUpAndWaiting"})
        self.assertEqual(self.check([a, b]), [("b", "dolt 1 2, Take-off Runway-0 4 at your discretion")])

    def test_departure_holds_for_inbound_on_base(self):
        a = pilot("a", "Dolt 1-1", RAMP)
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.call("anapa ground dolt 1 1 ready to taxi", [a, c])
        self.call("anapa approach dolt 1 3 inbound", [a, c])
        c = at(c, "Entry-Exit West")
        self.assertEqual(self.check([a, c]), [
            ("c", "dolt 1 3, fly heading 1 2 2 for Initial-0 4, descend 700, reduce speed your discretion")])
        a = at(a, "Runway-0 4")
        self.assertEqual(self.check([a, c]), [("a", "dolt 1 1, hold short of runway")])
        self.assertEqual(self.check([a, c]), [])
        self.assertEqual(self.monitor.tracked(), {"a": "HoldingShort", "c": "Base"})

    def test_inbound_sequence(self):
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.call("anapa approach dolt 1 3 inbound", [c])
        self.assertEqual(self.check([short_of(c, "Entry-Exit West", 12000)]), [])            # 120 s out
        self.assertEqual(self.check([short_of(c, "Entry-Exit West", 6000)]),                  # 60 s out
                         [("c", "dolt 1 3, fly heading 2 1 5 for Entry-Exit West")])
        self.assertEqual(self.check([short_of(c, "Entry-Exit West", 5000)]), [])              # said once
        self.assertEqual(self.check([short_of(c, "Entry-Exit West", 3000)]),                  # 30 s out
                         [("c", "dolt 1 3, fly heading 2 1 5 for Entry-Exit West, descend and maintain 1000")])
        self.assertEqual(self.check([at(c, "Entry-Exit West")]), [
            ("c", "dolt 1 3, fly heading 1 2 2 for Initial-0 4, descend 700, reduce speed your discretion")])
        self.assertEqual(self.check([at(c, "Initial-0 4")]), [
            ("c", "dolt 1 3, Turn final Runway-0 4, approach speed and altitude your discretion")])
        self.assertEqual(self.check([at(c, "Runway-0 4")]),
                         [("c", "dolt 1 3, Check gear, land Runway-0 4 at your discretion")])
        self.assertEqual(self.check([at(c, "Runway-0 4", speed=60, altitude=ANAPA.altitude + 5)]), [])  # too fast
        landed = at(c, "Runway-0 4", speed=30, altitude=ANAPA.altitude + 5)
        self.assertEqual(self.check([landed]), [("c", "dolt 1 3, Exit runway when able")])
        self.assertEqual(self.check([landed]), [])                                            # still on the runway
        self.assertEqual(self.check([at(landed, "Whiskey Spots")]), [("c", "dolt 1 3, Taxi to ramp")])
        self.assertEqual(self.monitor.tracked(), {})

    def test_runway_crossing_waits_for_landing_traffic(self):
        b = pilot("b", "Dolt 1-2", ECHO)
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.assertEqual(self.call("anapa ground dolt 1 2 request taxi", [b, c]),
                         "dolt 1 2, anapa ground, taxi to Runway-0 4 via Echo, Delta, Mike, Alpha.")
        self.call("anapa approach dolt 1 3 inbound", [b, c])
        c = at(c, "Entry-Exit West")
        self.check([b, c])                                                     # c turns base
        b = at(b, "Runway-2 2")                                                # b reaches the runway it crosses
        self.assertEqual(self.check([b, c]), [("b", "dolt 1 2, hold short of runway")])
        c = at(c, "Runway-0 4", speed=30, altitude=ANAPA.altitude + 5)
        for state in ("Final", "ShortFinal", "Landed"):
            replies = self.check([b, c])
            self.assertNotIn("b", [unit_id for unit_id, _ in replies])
            self.assertEqual(self.monitor.tracked(), {"b": "HoldingShort", "c": state})
        c = at(c, "Whiskey Spots")
        self.assertEqual(self.check([b, c]), [("c", "dolt 1 3, Taxi to ramp")])   # b checked first, still blocked
        self.assertEqual(self.check([b, c]), [("b", "dolt 1 2, cross runway at your discretion")])
        self.assertEqual(self.monitor.tracked(), {"b": "TaxiToRunway"})

    def test_runway_crossing_without_traffic(self):
        b = pilot("b", "Dolt 1-2", ECHO)
        self.call("anapa ground dolt 1 2 request taxi", [b])
        b = at(b, "Runway-2 2")
        self.assertEqual(self.check([b]), [("b", "dolt 1 2, cross runway at your discretion")])
        self.assertEqual(self.check([b]), [])                                  # not repeated at the same point
        self.assertEqual(self.monitor.tracked(), {"b": "TaxiToRunway"})

    def test_missing_telemetry_gives_no_clearance(self):
        a = pilot("a", "Dolt 1-1", RAMP)
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.call("anapa ground dolt 1 1 ready to taxi", [a, c])
        self.call("anapa approach dolt 1 3 inbound", [a, c])
        a = at(a, "Runway-0 4")
        self.check([a, c])
        no_speed_a, no_speed_c = dict(a), at(c, "Entry-Exit West")
        del no_speed_a["speed"], no_speed_c["speed"]
        self.assertEqual(self.check([no_speed_a, no_speed_c]), [])
        self.assertEqual(self.monitor.tracked(), {"a": "ClearedForTakeoff", "c": "Inbound"})
        self.assertEqual(self.check([dict(no_speed_c, speed=0.0)]), [])      # a vanished; c stationary
        self.assertEqual(self.monitor.tracked(), {"c": "Inbound"})

    def test_dropped_when_out_of_range_or_coalition_changes(self):
        a, b = pilot("a", "Dolt 1-1", RAMP), pilot("b", "Dolt 1-2", RAMP)
        self.call("anapa ground dolt 1 1 ready to taxi", [a, b])
        self.call("anapa ground dolt 1 2 ready to taxi", [a, b])
        far = dict(a, lat=a["lat"] + 0.1)                                     # about 6 nm north
        self.assertEqual(self.check([far, dict(b, coalition=RED)]), [])
        self.assertEqual(self.monitor.tracked(), {})

    def test_abort_inbound(self):
        a = pilot("a", "Dolt 1-1", RAMP)
        c = pilot("c", "Dolt 1-3", INBOUND, speed=100, altitude=1500)
        self.call("anapa ground dolt 1 1 ready to taxi", [a, c])
        self.call("anapa approach dolt 1 3 inbound", [a, c])
        self.assertEqual(self.call("anapa approach dolt 1 3 abort inbound", [a, c]),
                         "dolt 1 3, anapa approach, Copy abort, call inbound again when ready")
        self.assertEqual(self.call("anapa ground dolt 1 1 abort inbound", [a, c]),
                         "dolt 1 1, anapa ground, Copy abort, call inbound again when ready")
        self.assertEqual(self.monitor.tracked(), {"a": "TaxiToRunway"})   # taxiing cannot abort an approach


class TacviewWorld(unittest.TestCase):
    def test_replay_fixture(self):
        world = World()
        with open(os.path.join(ROOT, "fixtures", "replay.acmi"), encoding="utf-8-sig") as f:
            for line in f:
                world.feed_line(line.rstrip("\n"))
        reply = handle("Overlord, Viper 1-1, bogey dope", BLUE, world.snapshot(), when=WHEN)["reply"]
        self.assertTrue(reply.startswith("viper 1 1, overlord, bra, "), reply)
        self.assertTrue(reply.endswith(", Fulcrum"), reply)
        self.assertEqual(handle("Overlord, Bandit 1-1, bogey dope", BLUE, world.snapshot(), when=WHEN)["reply"],
                         "bandit 1 1, overlord, i could not find you on scope")


if __name__ == "__main__":
    unittest.main()
