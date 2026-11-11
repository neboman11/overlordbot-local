"""Transmission parser against the ported OverlordBot corpus and measured STT transcripts.

tests/fixtures/parser_cases.json holds every labelled utterance of the original
LUIS model (OverlordBot.Core/Documentation/OverlordBot.lu), textual equivalents
of OverlordBot.Cognitive.Tests/TransmissionParser, and whisper.cpp transcripts
from artifacts/speech-benchmark.json.  Every case is checked; where a case's
expectation departs from the original label the fixture records the label and
the reason (see data/PROVENANCE.md).
"""

import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from overlord.parser import parse  # noqa: E402

with open(os.path.join(ROOT, "tests", "fixtures", "parser_cases.json"), encoding="utf-8") as _f:
    CORPUS = json.load(_f)["cases"]


def _strip_spoken(value):
    if isinstance(value, dict):
        return {k: _strip_spoken(v) for k, v in value.items() if k != "spoken"}
    return value


class Corpus(unittest.TestCase):
    def _check(self, prefix):
        cases = [c for c in CORPUS if c["id"].startswith(prefix)]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case["id"], text=case["text"]):
                parsed = parse(case["text"], known_groups=case.get("known_groups"))
                for field, expected in case["expected"].items():
                    self.assertEqual(_strip_spoken(parsed[field]), _strip_spoken(expected), field)

    def test_original_lu_utterances(self):
        self._check("lu-")

    def test_original_transmission_parser_cases(self):
        self._check("cs-")

    def test_measured_whisper_transcripts(self):
        self._check("stt-")


class Grammar(unittest.TestCase):
    def test_spoken_numbers_and_separators(self):
        for text in ("overlord dolt one two bogey dope", "overlord, dolt 1-2, bogey dope",
                     "Overlord dolt 1/2 bogey dope", "overlord dolt twelve bogey dope",
                     "Overlord, Dolt 1.2, bogey dope"):
            with self.subTest(text):
                self.assertEqual(parse(text)["transmitter"],
                                 {"kind": "player", "group": "dolt", "flight": 1, "element": 2})

    def test_link_word_before_lone_number(self):
        for text, flight, element in (("overlord dodge for one radio check", 4, 1),
                                      ("overlord dolt on 2 radio check", -1, 2),
                                      ("overlord dolt 1 radio check", 1, -1)):
            with self.subTest(text):
                t = parse(text)["transmitter"]
                self.assertEqual((t["group"], t["flight"], t["element"]), (text.split()[1], flight, element))

    def test_noise_word_before_callsign_number_is_not_a_group(self):
        for text in ("overlord made a 11 bogey dope", "overlord don't 12 bogey dope",
                     "of lord 1 three set warning 40 miles"):
            with self.subTest(text):
                self.assertIsNone(parse(text)["transmitter"]["group"])

    def test_callsign_alias_and_two_word_group(self):
        self.assertEqual(parse("overlord adult 1 1 radio check")["transmitter"]["group"], "dolt")
        self.assertEqual(parse("overlord ghost rider 2 1 radio check")["transmitter"]["group"], "ghostrider")

    def test_known_groups_resolve_group_names(self):
        text = "command war crime 32 tasking cas"
        self.assertEqual(parse(text)["transmitter"]["group"], "crime")
        self.assertEqual(parse(text, known_groups={"War Crime"})["transmitter"]["group"], "warcrime")
        text = "anapa ground corner 3 1 ready to taxi"
        self.assertEqual(parse(text)["transmitter"]["group"], "corner")
        self.assertEqual(parse(text, known_groups={"Hornet", "Viper"})["transmitter"]["group"], "hornet")

    def test_phonetic_collisions_do_not_select_an_unrelated_pilot(self):
        parsed = parse("overlord vipper 1 1 bogey dope", known_groups={"Viper", "Vapor", "Vesper"})
        self.assertEqual(parsed["transmitter"]["group"], "vipper")
        self.assertEqual(parse("overlord viper 1 1 bogey dope",
                               known_groups={"Viper", "Vapor", "Vesper"})["transmitter"]["group"], "viper")

    def test_custom_bot_callsign(self):
        parsed = parse("dark star viper 1 1 picture", bot_callsign="Dark Star")
        self.assertEqual(parsed["receiver"], {"kind": "awacs", "callsign": "darkstar"})
        self.assertEqual(parsed["intent"], "picture")

    def test_airfield_aliases(self):
        for spoken, canonical in (("an apple", "Anapa-Vityazevo"), ("kras pash", "Krasnodar-Pashkovsky"),
                                  ("make up", "Maykop-Khanskaya"), ("go data", "Gudauta"),
                                  ("ras al khaimah", "Ras Al Khaimah Intl"), ("urkw", "Krymsk")):
            with self.subTest(spoken):
                self.assertEqual(parse(f"{spoken} tower dolt 1 2 inbound")["receiver"]["name"], canonical)

    def test_misheard_airfield_names(self):
        for spoken, canonical in (("an arper", "Anapa-Vityazevo"), ("qtacey", "Kutaisi"),
                                  ("bar tumi", "Batumi"), ("sukumi", "Sukhumi-Babushara")):
            with self.subTest(spoken):
                self.assertEqual(parse(f"{spoken} tower dolt 1 2 inbound")["receiver"]["name"], canonical)
                self.assertEqual(parse(f"overlord dolt 1 2 where is {spoken}")["subject"]["name"], canonical)

    def test_unknown_airfield_name_is_kept_as_spoken(self):
        self.assertEqual(parse("springfield tower dolt 1 2 inbound")["receiver"]["name"], "springfield")

    def test_misheard_command_phrases(self):
        for text in ("overlord viper 1 1 bobey dope", "overlord viper 1 1 bogey dote"):
            with self.subTest(text):
                self.assertEqual(parse(text)["intent"], "bogeyDope")

    def test_frequency_is_not_a_callsign(self):
        self.assertEqual(parse("249.5 radio check")["intent"], "None")

    def test_traffic_self_announcement_is_ignored(self):
        self.assertEqual(parse("kutaisi traffic dolt 11 inbound runway 25")["intent"], "None")

    def test_declare_bearing_range_and_reference(self):
        for text, expected in (("overlord dolt 12 declare 134 for 25", (134, 25, "requester")),
                               ("overlord dolt 12 declare 359 four 78", (359, 78, "requester")),
                               ("overlord dolt 12 declare 123 50", (123, 50, "requester")),
                               ("overlord dolt 12 declare zero four five thirty", (45, 30, "requester")),
                               ("overlord dolt 12 declare bullseye 045 30", (45, 30, "bullseye")),
                               ("overlord dolt 12 declare", (None, None, "requester"))):
            with self.subTest(text):
                parsed = parse(text)
                self.assertEqual(parsed["intent"], "declare")
                self.assertEqual((parsed["bearing"], parsed["range"], parsed["reference"]), expected)
        self.assertIsNone(parse("overlord dolt 12 bogey dope")["reference"])

    def test_mission_id_spoken_digits(self):
        self.assertEqual(parse("overlord dolt 12 join mission five one five zero")["mission_id"], 5150)
        self.assertEqual(parse("overlord dolt 12 joining mission fifty one fifty")["mission_id"], 5150)

    def test_warning_radius_mishearing_corrections(self):
        self.assertEqual(parse("overlord dolt 12 set warning 230 miles")["distance"], 30)
        self.assertEqual(parse("overlord dolt 12 set warning 440 miles")["distance"], 40)
        self.assertIsNone(parse("overlord dolt 12 set warning miles")["distance"])

    def test_bogey_dope_qualifier_rules(self):
        self.assertEqual(parse("overlord dolt 12 bogey dope long range bombers")["qualifier"],
                         {"identifier": None, "attributes": ["bomber"]})
        self.assertEqual(parse("overlord dolt 12 bogey dope mig 29")["qualifier"]["identifier"], "mig-29")
        parsed = parse("overlord dolt 12 where are the flankers")
        self.assertEqual((parsed["intent"], parsed["qualifier"]["identifier"]), ("bogeyDope", "flankers"))


if __name__ == "__main__":
    unittest.main()
