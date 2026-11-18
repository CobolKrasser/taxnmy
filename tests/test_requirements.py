"""Every rule number that the code, the prompt, the test set or the documentation names is a rule in
REQUIREMENTS.md (TAX-33). The runs of the referee stay as they were written, and the data may hold
a product name that looks like a rule number; both are left out."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = ("referee/runs/", "products/", "publishers/", "tests/test_requirements.py")  # this file names a wrong one
TEXT = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh", ".toml", ".cfg"}
NUMBER = re.compile(r"\bT[PKERVG][0-9]{1,2}\b")
DEFINED = re.compile(r"^\| (T[PKERVG][0-9]{1,2}) \|", re.M)  # the first cell of a row of a rule table


def defined(root: Path = REPO) -> set[str]:
    return set(DEFINED.findall((root / "REQUIREMENTS.md").read_text(encoding="utf-8")))


def unknown_numbers(root: Path = REPO) -> list[str]:
    rules = defined(root)
    hits = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(SKIP) or "/." in "/" + rel or path.suffix not in TEXT:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for number in NUMBER.findall(line):
                if number not in rules:
                    hits.append(f"{rel}:{n}: {number}")
    return hits


class RequirementsTest(unittest.TestCase):
    def test_every_rule_number_is_in_the_requirements(self):
        hits = unknown_numbers()
        self.assertEqual(hits, [], "rule numbers not in REQUIREMENTS.md:\n" + "\n".join(hits))

    def test_the_requirements_hold_every_series(self):
        series = {number[:2] for number in defined()}
        self.assertEqual(series, {"TP", "TK", "TE", "TR", "TV", "TG"})

    def test_the_guard_sees_an_unknown_number(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "REQUIREMENTS.md").write_text("| # | requirement |\n| --- | --- |\n| TR1 | Filled. |\n")
            (root / "tools").mkdir()
            (root / "tools/x.py").write_text("RULES = ('TR1', 'TR99')\n")
            (root / "referee/runs/r").mkdir(parents=True)
            (root / "referee/runs/r/summary.md").write_text("TR42 stays in a run\n")
            self.assertEqual(unknown_numbers(root), ["tools/x.py:1: TR99"])


if __name__ == "__main__":
    unittest.main()
