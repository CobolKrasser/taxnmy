"""A row of models (TAX-62): tools/modelrow.py on made-up names, without network or data of the repository."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import modelrow  # noqa: E402

SERIES = {"book", "slate", "pad"}
ADD = [r"\s*-?\s*Region HDD Guard"]


def models(name, brands=("Acme",)):
    return modelrow.models(name, brands, SERIES, ADD)


class ModelRowTest(unittest.TestCase):
    def test_a_row(self):
        self.assertTrue(modelrow.is_row("Book 14 Gen 2/Pad 5"))
        self.assertTrue(modelrow.is_row("Book 5431, Slate 3470"))
        self.assertFalse(modelrow.is_row("Book 14 (A/B)"), "a slash in brackets is no row")
        self.assertFalse(modelrow.is_row("Book 14"))

    def test_full_models(self):
        self.assertEqual(models("Book 5431, Slate 3470"), (["Book 5431", "Slate 3470"], ""))
        self.assertEqual(models("Acme Book 9/Acme Pad 7"), (["Book 9", "Pad 7"], ""), "the brand is dropped")

    def test_a_shortened_part_is_completed(self):
        self.assertEqual(models("Book T14 Gen 2/P14s Gen 2")[0], ["Book T14 Gen 2", "Book P14s Gen 2"])
        self.assertEqual(models("Book G15 5520/5521")[0], ["Book G15 5520", "Book G15 5521"])

    def test_additions_and_doubles_fall_away(self):
        self.assertEqual(models("Book 30 - Region HDD Guard/Book 30s/Book 30")[0], ["Book 30", "Book 30s"])

    def test_rows_it_cannot_read(self):
        self.assertIn("longer than", models("/".join(["Book 1234567"] * 9))[1])
        self.assertIn("no model", models("Book 9/Turbo")[1])
        self.assertIn("addition for the whole row", models("Book 8x0/Book 8x0 Aero/Pad Fire G7 Notebook PC S70")[1])
        self.assertIn("no known series", models("Book 9/Zeta 7")[1])
        self.assertIn("cannot be completed", models("Book G15 5520/55")[1])

    def test_a_series_name_alone_is_no_model(self):
        self.assertEqual(models("Book 9/Pad")[0], ["Book 9"])

    def test_the_series_of_a_publisher(self):
        self.assertEqual(modelrow.series_of(["Book 9", "Pad 7/Pad 8", "Thing"], ["Acme"]), {"book", "pad"})


if __name__ == "__main__":
    unittest.main()
