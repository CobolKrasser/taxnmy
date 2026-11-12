import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class SchemaVersionTest(unittest.TestCase):
    def versions(self):
        return {
            kind: json.loads((REPO / "schema" / f"{kind}.schema.json").read_text(encoding="utf-8")).get("version")
            for kind in ("product", "publisher")
        }

    def test_schemas_have_one_semantic_version(self):
        versions = self.versions()
        for kind, version in versions.items():
            with self.subTest(kind=kind):
                self.assertRegex(version or "", r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_changelog_has_the_current_version(self):
        version = self.versions()["product"]
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        headings = re.findall(r"^## (\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
        self.assertTrue(headings, "CHANGELOG.md has no version headings")
        self.assertEqual(headings[0], version, "the newest CHANGELOG entry must match the schema version")


if __name__ == "__main__":
    unittest.main()
