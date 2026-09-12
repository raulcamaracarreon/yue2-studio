import unittest

from tools import abc_tools
from tools.piano_roll_score import PPQ, build_score, inspect_score

SAMPLE = '''X:1
T:
M:4/4
L:1/16
Q:1/4=92
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Dm
% verse
V: Vocal
"Dm"A4A4z8|"Gm"G8A8|
V: Ins
D4F4A4F4|G4B4d4B4|
% chorus
V: Vocal
"Bb"d4c4B4A4|"A7"A8z8|
V: Ins
B,4D4F4D4|A,4E4G4E4|
'''


class PianoRollScoreRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = inspect_score(SAMPLE)
        cls.rebuilt = build_score(cls.original)

    def test_metadata(self):
        self.assertEqual(self.original["bpm"], 92)
        self.assertEqual(self.original["meter"], "4/4")
        self.assertEqual(self.original["key"], "Dm")
        self.assertEqual(self.original["roll"]["ppq"], PPQ)
        self.assertTrue(self.original["grid_available"])
        self.assertEqual([s["name"] for s in self.original["roll"]["sections"]], ["verse", "chorus"])

    def test_roundtrip_preserves_sounding_events(self):
        before = abc_tools.parse(self.original["abc"])
        after = abc_tools.parse(self.rebuilt["abc"])
        result = abc_tools.compare(before, after)
        self.assertTrue(result["match"], result["differences"])

    def test_roundtrip_preserves_roll_and_chords(self):
        self.assertEqual(self.rebuilt["roll"]["tracks"], self.original["roll"]["tracks"])
        self.assertEqual(self.rebuilt["roll"]["chords"], self.original["roll"]["chords"])
        self.assertEqual(self.rebuilt["roll"]["sections"], self.original["roll"]["sections"])

    def test_rejects_overlap(self):
        data = inspect_score(SAMPLE)
        data["roll"]["tracks"]["Vocal"].append({"start": 0, "duration": 128, "pitch": 72})
        with self.assertRaisesRegex(abc_tools.AbcError, "solapan"):
            build_score(data)


if __name__ == "__main__":
    unittest.main()
