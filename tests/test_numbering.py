"""The rules carry one numbering everywhere (TAX-30): TR for the referee, TV for protection against
intent, TG for governance. The old numbers S1-S10 no longer appear as a word, outside the runs of
the referee (which stay as they were written) and the data (where a product name may hold one)."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = ("referee/runs/", "products/", "publishers/", "tests/test_numbering.py")  # this file names them
TEXT = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh", ".toml", ".cfg"}
OLD = re.compile(r"\bS(10|[1-9])\b")


def is_name(rel: str, line: str) -> bool:
    """A product name, not a rule: the name field of a proposed file, or a name in an example of the
    writing rule."""
    stripped = line.lstrip()
    return stripped.startswith("name: ") or (rel == "tests/test_keyrule.py" and stripped.startswith('(("'))


def old_numbers(root: Path = REPO) -> list[str]:
    hits = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(SKIP) or "/." in "/" + rel or path.suffix not in TEXT:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OLD.search(line) and not is_name(rel, line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    return hits


class NumberingTest(unittest.TestCase):
    def test_no_old_rule_numbers(self):
        hits = old_numbers()
        self.assertEqual(hits, [], "old rule numbers (S1-S10); use TR, TV or TG:\n" + "\n".join(hits))

    def test_the_guard_sees_an_old_number(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            (root / "tools/x.py").write_text("RULES = ('S4',)\n")
            (root / "referee/runs/r").mkdir(parents=True)
            (root / "referee/runs/r/summary.md").write_text("S4 stays in a run\n")
            (root / "case.yaml").write_text("      name: Tab S9\n")
            self.assertEqual(old_numbers(root), ["tools/x.py:1: RULES = ('S4',)"])


if __name__ == "__main__":
    unittest.main()
