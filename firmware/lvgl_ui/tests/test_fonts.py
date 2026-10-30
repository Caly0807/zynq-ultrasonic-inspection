from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FontSubsetTests(unittest.TestCase):
    def test_all_ui_characters_and_symbols_are_in_each_generated_font(self):
        source = (ROOT / "src/ut_ui.c").read_text(encoding="utf-8")
        expected = sorted(set(range(32, 127)) | {ord(character) for character in source if ord(character) >= 0x80})
        asset = (ROOT / "assets/ut_font.c").read_text(encoding="utf-8")
        for size in (14, 16, 20):
            with self.subTest(size=size):
                match = re.search(rf"{re.escape('ut_font_' + str(size))}_unicode\[\] = \{{([^}}]*)\}}", asset)
                self.assertIsNotNone(match)
                actual = [int(value) + 32 for value in match.group(1).split(",")]
                self.assertEqual(actual, expected)
                self.assertIn(ord("触"), actual)
                self.assertIn(ord("→"), actual)
                self.assertIn(ord("↓"), actual)


if __name__ == "__main__":
    unittest.main()
