"""Behavioural tests of the AWACS extensions: friendly/bullseye picture, nearest tanker,
vector home, nearest divert, say again and NATO reporting-name aliases.

Expected numbers come from the domain's own geometry helpers so that the tests check
what the reply is built from (who is reported, from where, to whom), not incidental wording.
"""

import datetime as dt
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from overlord import domain, parser  # noqa: E402
from overlord.domain import handle  # noqa: E402

WHEN = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
BLUE, RED = 2, 1
CALL = "overlord dolt 1 2 "


def unit(uid, lat, lon, coalition=BLUE, type_="F-15C", altitude=6000.0, heading=0.0, speed=200.0,
         pilot="", category="Airplane", **extra):
    u = {"id": uid, "name": pilot or type_, "pilot": pilot, "callsign": pilot, "coalition": coalition,
         "type": type_, "category": category, "lat": lat, "lon": lon, "altitude": altitude,
         "heading": heading, "group": ""}
    if speed is not None:
        u["speed"] = speed
    u.update(extra)
    return u


def me(**kw):
    return unit("me", 41.0, 37.0, pilot="Dolt 1-2", **kw)


def say(text, units, **kw):
    return handle(CALL + text, BLUE, units, when=WHEN, **kw)["reply"]


def bra_words(source, target, metric=False):
    """Bearing and range words exactly as the responders speak them."""
    bearing = int(domain.true_to_magnetic(source["lat"], source["lon"], domain.true_bearing(source, target), WHEN))
    return (domain.bearing_to_words(bearing),
            domain.range_to_words(int(domain.dist_km(source, target)), metric))


class Grammar(unittest.TestCase):
    def intent(self, text):
        return parser.parse(CALL + text)["intent"]

    def test_canonical_phrases(self):
        for phrase, intent in (("friendly picture", "friendlyPicture"), ("bullseye picture", "bullseyePicture"),
                               ("nearest tanker", "tanker"), ("vector home", "vectorHome"),
                               ("nearest divert", "divert"), ("say again", "sayAgain")):
            with self.subTest(phrase):
                parsed = parser.parse(CALL + phrase)
                self.assertEqual(parsed["intent"], intent)
                self.assertEqual(parsed["transmitter"]["group"], "dolt")
                self.assertEqual((parsed["transmitter"]["flight"], parsed["transmitter"]["element"]), (1, 2))
                self.assertEqual(parsed["receiver"]["kind"], "awacs")
        self.assertTrue(set(parser.INTENTS) >= {"friendlyPicture", "bullseyePicture", "tanker",
                                                "vectorHome", "divert", "sayAgain"})

    def test_paraphrases(self):
        for text, intent in (("request vectors home", "vectorHome"), ("take me home", "vectorHome"),
                             ("closest tanker", "tanker"), ("vector to the nearest tanker", "tanker"),
                             ("where is the nearest tanker", "tanker"), ("request closest divert", "divert"),
                             ("bulls eye picture", "bullseyePicture"), ("say that again", "sayAgain"),
                             ("friendlies picture", "friendlyPicture")):
            with self.subTest(text):
                self.assertEqual(self.intent(text), intent)

    def test_original_phrases_keep_their_intent(self):
        self.assertEqual(self.intent("picture"), "picture")
        self.assertEqual(self.intent("bearing to texaco"), "locationOfEntity")
        self.assertEqual(self.intent("where is the nearest airfield"), "locationOfEntity")
        self.assertEqual(self.intent("bogey dope"), "bogeyDope")
        self.assertEqual(self.intent("declare bullseye 134 for 25"), "declare")

    def test_named_tanker_subject_is_unchanged(self):
        parsed = parser.parse(CALL + "bearing to texaco")
        self.assertEqual(parsed["subject"]["kind"], "tanker")

    def test_chatter_is_not_a_request(self):
        self.assertEqual(self.intent("diverting to batumi"), "None")
        self.assertEqual(self.intent("going home"), "None")

    def test_atc_frequency_refuses_awacs_extensions(self):
        for phrase in ("friendly picture", "bullseye picture", "nearest tanker", "vector home",
                       "nearest divert", "say again"):
            with self.subTest(phrase):
                reply = handle(f"kutaisi tower dolt 1 2 {phrase}", BLUE, [me()], when=WHEN)["reply"]
                self.assertEqual(reply.rsplit(", ", 1)[-1], domain.ATC_ONLY_MESSAGE)


class FriendlyPicture(unittest.TestCase):
    def test_reports_wingman_without_caller_and_without_hostiles(self):
        wing = unit("w1", 41.5, 37.0, pilot="Dolt 1-1", type_="F-16C")
        hostile = unit("b1", 41.2, 37.0, RED, "MiG-21Bis")
        reply = say("friendly picture", [me(), wing, hostile])
        bearing, rng = bra_words(me(), wing)
        self.assertIn(bearing, reply)
        self.assertIn(rng, reply)
        self.assertIn("Falcon", reply)
        self.assertNotIn("Eagle", reply)      # the caller is an F-15C
        self.assertNotIn("Fishbed", reply)

    def test_only_caller_is_empty(self):
        reply = say("friendly picture", [me(), unit("b1", 41.2, 37.0, RED, "MiG-21Bis")])
        self.assertNotIn("bra", reply)
        self.assertNotIn("Eagle", reply)
        self.assertNotIn("Fishbed", reply)

    def test_ground_and_stationary_and_neutral_units_are_not_pictured(self):
        units = [me(),
                 unit("g1", 41.1, 37.0, type_="Su-25", speed=0.0),
                 unit("t1", 41.1, 37.1, type_="M-1 Abrams", category="Ground", speed=15.0),
                 unit("n1", 41.1, 37.2, 0, "Su-27"),
                 unit("u1", 41.1, 37.3, type_="Tornado_GR4", speed=None)]
        self.assertNotIn("bra", say("friendly picture", units))

    def test_groups_are_counted(self):
        units = [me(), unit("w1", 41.5, 37.0, type_="F-16C"), unit("w2", 41.5, 37.02, type_="F-16C"),
                 unit("f1", 40.0, 36.0, type_="A-10C", heading=90.0)]
        reply = say("friendly picture", units)
        self.assertIn("2 groups", reply)
        self.assertIn("2-ship Falcon", reply)
        self.assertIn("Warthog", reply)

    def test_unknown_coalition_is_explicit(self):
        caller = me()
        caller["coalition"] = 0
        reply = handle(CALL + "friendly picture", 0, [caller, unit("w1", 41.5, 37.0)], when=WHEN)["reply"]
        self.assertIn("coalition is unknown", reply)

    def test_plain_picture_still_hostile_only(self):
        units = [me(), unit("w1", 41.5, 37.0, type_="F-16C"), unit("b1", 41.2, 37.0, RED, "MiG-21Bis")]
        reply = say("picture", units)
        self.assertIn("Fishbed", reply)
        self.assertNotIn("Falcon", reply)


class BullseyePicture(unittest.TestCase):
    BULLS = (41.0, 36.0)

    def hostile(self):
        return unit("b1", 41.0, 36.5, RED, "MiG-21Bis", altitude=6000.0, heading=90.0)

    def test_missing_reference_is_explicit_unable(self):
        reply = say("bullseye picture", [me(), self.hostile()])
        self.assertTrue(reply.endswith(domain.BULLSEYE_UNAVAILABLE_MESSAGE))

    def test_configured_bullseye_is_the_origin_not_the_caller(self):
        hostile = self.hostile()
        reply = say("bullseye picture", [me(), hostile], bullseye=self.BULLS)
        origin = {"lat": self.BULLS[0], "lon": self.BULLS[1]}
        bearing, rng = bra_words(origin, hostile)
        self.assertIn(f"bullseye, {bearing}, {rng}", reply)
        self.assertIn("Fishbed", reply)
        self.assertIn("track east", reply)
        caller_bearing, caller_rng = bra_words(me(), hostile)
        self.assertNotIn(f"bullseye, {caller_bearing}, {caller_rng}", reply)

    def test_live_coalition_bullseye_wins_over_configured(self):
        hostile = self.hostile()
        live = unit("bull", 41.0, 35.0, BLUE, "Bullseye", category="Navaid", speed=None,
                    tacview_type="Navaid+Static+Bullseye")
        reply = say("bullseye picture", [me(), hostile, live], bullseye=self.BULLS)
        bearing, rng = bra_words(live, hostile)
        self.assertIn(f"bullseye, {bearing}, {rng}", reply)

    def test_enemy_bullseye_object_is_not_used(self):
        enemy_bull = unit("bull", 41.0, 35.0, RED, "Bullseye", category="Navaid", speed=None,
                          tacview_type="Navaid+Static+Bullseye")
        reply = say("bullseye picture", [me(), self.hostile(), enemy_bull])
        self.assertTrue(reply.endswith(domain.BULLSEYE_UNAVAILABLE_MESSAGE))

    def test_invalid_configured_bullseye_is_unable(self):
        for bad in ((float("nan"), 36.0), (95.0, 36.0), ("x", "y"), (41.0,)):
            with self.subTest(bad):
                reply = say("bullseye picture", [me(), self.hostile()], bullseye=bad)
                self.assertTrue(reply.endswith(domain.BULLSEYE_UNAVAILABLE_MESSAGE))

    def test_clean_and_friendlies_not_reported(self):
        friendly = unit("w1", 41.0, 36.5, BLUE, "F-16C")
        self.assertEqual(say("bullseye picture", [me(), friendly], bullseye=self.BULLS).rsplit(", ", 1)[-1], "clean")

    def test_declare_uses_configured_bullseye_too(self):
        text = "declare bullseye 270 for 1"
        reply = say(text, [me(), unit("b2", 41.0, 36.0, RED, "MiG-21Bis")], bullseye=(41.0, 36.0))
        self.assertNotIn(domain.BULLSEYE_UNAVAILABLE_MESSAGE, reply)
        self.assertIn("hostile", reply)
        self.assertTrue(say(text, [me()]).endswith(domain.BULLSEYE_UNAVAILABLE_MESSAGE))


class NearestTanker(unittest.TestCase):
    def test_nearest_friendly_tanker_is_reported(self):
        near = unit("t1", 41.5, 37.0, type_="KC-135")
        far = unit("t2", 43.0, 37.0, type_="KC-130")
        reply = say("nearest tanker", [me(), far, near])
        self.assertIn("Stratotanker", reply)
        self.assertIn(domain._location_of_unit(me(), near, WHEN), reply)
        self.assertNotIn("KC-130", reply)

    def test_enemy_and_non_tanker_aircraft_are_ignored(self):
        units = [me(), unit("e1", 41.1, 37.0, RED, "KC-135"), unit("f1", 41.1, 37.1, BLUE, "F-16C"),
                 unit("p1", 41.1, 37.2, BLUE, "KC-135", speed=0.0)]
        self.assertEqual(say("nearest tanker", units), "dolt 1 2, overlord, unable, no friendly tanker on scope")

    def test_tanker_callsign_comes_from_telemetry_only(self):
        tanker = unit("t1", 41.5, 37.0, type_="KC135MPRS", pilot="Texaco 1-1")
        reply = say("nearest tanker", [me(), tanker])
        self.assertIn("texaco 1 1", reply)
        for invented in ("tacan", "frequency", "fuel"):
            self.assertNotIn(invented, reply.lower())

    def test_caller_that_is_a_tanker_is_not_its_own_answer(self):
        caller = me(type_="KC-135")
        self.assertIn("no friendly tanker", say("nearest tanker", [caller]))

    def test_named_tanker_vectors_stay_unsupported(self):
        self.assertIn("we do not yet support vectors to tankers", say("bearing to texaco", [me()]))


class VectorHome(unittest.TestCase):
    def test_unconfigured_home_is_explicit(self):
        self.assertIn("home airfield is not configured", say("vector home", [me()]))
        self.assertIn("home airfield is not configured", say("vector home", [me()], home_airfield="  "))

    def test_unknown_home_is_not_guessed(self):
        reply = say("vector home", [me()], home_airfield="Atlantis")
        self.assertIn("home airfield is not known", reply)

    def test_known_encyclopedia_field(self):
        reply = say("vector home", [me()], home_airfield="Kutaisi")
        field = domain.find_airfield([], "Kutaisi")
        bearing, rng = bra_words(me(), field)
        self.assertIn(bearing, reply)
        self.assertIn(rng, reply)
        self.assertIn("Kutaisi", reply)

    def test_telemetry_field_is_used_for_position(self):
        home = unit("a1", 41.5, 37.0, BLUE, "Alpha", category="Airfield", speed=None, name="Alpha")
        reply = say("vector home", [me(), home], home_airfield="Alpha")
        bearing, rng = bra_words(me(), home)
        self.assertIn("Alpha", reply)
        self.assertIn(bearing, reply)
        self.assertIn(rng, reply)

    def test_enemy_owned_home_is_refused(self):
        home = unit("a1", 41.5, 37.0, RED, "Alpha", category="Airfield", speed=None, name="Alpha")
        self.assertIn("not friendly", say("vector home", [me(), home], home_airfield="Alpha"))

    def test_home_is_never_taken_from_the_nearest_field(self):
        near = unit("a1", 41.01, 37.0, BLUE, "Near", category="Airfield", speed=None, name="Near")
        reply = say("vector home", [me(), near], home_airfield="Kutaisi")
        self.assertNotIn("Near", reply)
        self.assertIn("Kutaisi", reply)


class NearestDivert(unittest.TestCase):
    def fields(self):
        return [unit("a1", 41.01, 37.0, RED, "Alpha", category="Airfield", speed=None, name="Alpha"),
                unit("a2", 41.03, 37.0, BLUE, "Bravo", category="Airfield", speed=None, name="Bravo"),
                unit("a3", 41.04, 37.0, 0, "Charlie", category="Airfield", speed=None, name="Charlie")]

    def test_nearest_friendly_or_neutral_field(self):
        reply = say("nearest divert", [me(), *self.fields()])
        self.assertIn("Bravo", reply)
        self.assertNotIn("Alpha", reply)

    def test_hostile_field_is_not_diverted_to_even_when_alone_nearby(self):
        reply = say("nearest divert", [me(), self.fields()[0]])
        self.assertNotIn("Alpha", reply)
        self.assertNotIn("Hostile airfield", reply)

    def test_bearing_and_range_are_to_the_field(self):
        fields = self.fields()
        reply = say("nearest divert", [me(), *fields])
        bearing, rng = bra_words(me(), fields[1])
        self.assertIn(bearing, reply)
        self.assertIn(rng, reply)


class SayAgain(unittest.TestCase):
    def test_domain_has_explicit_no_previous_fallback(self):
        result = handle(CALL + "say again", BLUE, [me()], when=WHEN)
        self.assertIn("no previous transmission", result["reply"])
        self.assertEqual(result["parsed"]["intent"], "sayAgain")

    def test_unknown_caller_is_still_refused_first(self):
        reply = say("say again", [])
        self.assertIn("could not find you on scope", reply)


class Aliases(unittest.TestCase):
    def test_alias_data_references_existing_types_only(self):
        with open(os.path.join(domain.DATA_DIR, "aliases.json"), encoding="utf-8") as f:
            data = json.load(f)
        by_code = {e["code"]: e for e in domain.AIRCRAFT}
        all_dcs = {c for e in domain.AIRCRAFT for c in e["dcs_codes"]}
        for code, names in data["aircraft"].items():
            self.assertIn(code, by_code)
            self.assertTrue(names)
        for alias, codes in data["aircraft_variants"].items():
            self.assertTrue(codes, alias)
            self.assertLessEqual(set(codes), all_dcs, alias)

    def test_original_labels_survive(self):
        self.assertEqual(domain.aircraft_name("Su-33"), "Flanker")
        self.assertEqual(domain.aircraft_name("Ka-50"), "Black Shark")
        self.assertEqual(domain.aircraft_name("Mi-28N"), "Havok")
        self.assertEqual(len(domain.codes_by_identifier("flanker")), 4)

    def test_reporting_names_resolve_to_dcs_types(self):
        self.assertEqual(domain.codes_by_identifier("hokum"), ["Ka-50"])
        self.assertEqual(domain.codes_by_identifier("havoc"), ["Mi-28N"])
        self.assertEqual(domain.codes_by_identifier("flanker-d"), ["Su-33"])
        self.assertEqual(domain.codes_by_identifier("fulcrum-c"), ["MiG-29S"])
        self.assertEqual(domain.codes_by_identifier("bear-h"), ["Tu-95MS"])
        self.assertEqual(domain.codes_by_identifier("hind-f"), ["Mi-24P"])
        self.assertEqual(domain.codes_by_identifier("hokums"), ["Ka-50"])
        self.assertEqual(domain.codes_by_identifier("flogger-k"), ["MiG-23MLD"])
        self.assertEqual(domain.codes_by_identifier("flogger-j-2"), ["MiG-27K"])
        self.assertEqual(domain.codes_by_identifier("flogger-j2"), ["MiG-27K"])
        self.assertEqual(domain.codes_by_identifier("flanker-l"), ["J-11A"])
        self.assertEqual(domain.codes_by_identifier("flanker-b"), ["Su-27"])
        self.assertEqual(domain.codes_by_identifier("havoc-b"), ["Mi-28N"])

    def test_unsourced_or_contested_variants_are_not_mapped(self):
        for identifier in ("flogger-g", "flogger-d", "foxbat-b", "fencer-e", "box"):
            with self.subTest(identifier):
                self.assertIsNone(domain.codes_by_identifier(identifier))

    def test_aliases_name_types_not_coalitions(self):
        hostile_flanker = unit("e1", 41.5, 37.0, RED, "Su-27")
        hostile_d = unit("e2", 41.6, 37.0, RED, "Su-33")
        friendly_d = unit("f1", 41.2, 37.0, BLUE, "Su-33")
        only_friendly = say("bogey dope flanker d", [me(), friendly_d, hostile_flanker])
        self.assertIn("Nothing on Scope", only_friendly)
        both = say("bogey dope flanker d", [me(), friendly_d, hostile_flanker, hostile_d])
        bearing, _ = bra_words(me(), hostile_d)
        self.assertIn(bearing, both)
        self.assertNotIn("Nothing on Scope", both)
        generic = say("bogey dope flanker", [me(), hostile_flanker, hostile_d])
        near_bearing, near_range = bra_words(me(), hostile_flanker)
        self.assertIn(near_range, generic)
        self.assertIn(near_bearing, generic)

    def test_where_with_spoken_numeric_variant_is_bogey_dope(self):
        for spoken in ("where are flogger j two", "where are flogger j 2", "where are flogger j2"):
            with self.subTest(spoken):
                parsed = parser.parse("Overlord Dolt one two " + spoken)
                self.assertEqual(parsed["intent"], "bogeyDope")
                self.assertEqual(domain.codes_by_identifier(parsed["qualifier"]["identifier"]), ["MiG-27K"])
        target = unit("e1", 41.5, 37.0, RED, "MiG-27K")
        other = unit("e2", 41.2, 37.0, RED, "MiG-23MLD")
        reply = handle("Overlord Dolt one two where are flogger j two", BLUE, [me(), target, other],
                       when=WHEN)["reply"]
        self.assertIn(bra_words(me(), target)[0], reply)
        self.assertIn("Flogger", reply)
        self.assertEqual(reply.count("bra"), 1)

    def test_where_with_callsign_digits_stays_a_lookup(self):
        wing = unit("w1", 41.5, 37.0, pilot="Dolt 1-1", type_="F-16C")
        parsed = parser.parse("Overlord Dolt one two where is dolt one one", known_groups={"dolt"})
        self.assertEqual(parsed["intent"], "locationOfEntity")
        reply = handle("Overlord Dolt one two where is dolt one one", BLUE, [me(), wing], when=WHEN)["reply"]
        self.assertIn(domain._location_of_unit(me(), wing, WHEN), reply)
        mixed = parser.parse("Overlord Dolt one two where is flanker one one", known_groups={"dolt"})
        self.assertEqual(mixed["intent"], "locationOfEntity")

    def test_helicopter_alias_filters_bogey_dope(self):
        helicopter = unit("h1", 41.5, 37.0, RED, "Ka-50", altitude=100.0, speed=60.0)
        fighter = unit("f1", 41.2, 37.0, RED, "MiG-29S")
        reply = say("bogey dope hokum", [me(), helicopter, fighter])
        self.assertIn("Black Shark", reply)
        self.assertNotIn("Fulcrum", reply)
        self.assertEqual(say("bogey dope hokum", [me(), fighter]).rsplit(", ", 1)[-1], "Nothing on Scope")


if __name__ == "__main__":
    unittest.main()
