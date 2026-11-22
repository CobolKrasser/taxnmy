"""The rules carry one numbering everywhere (TAX-30): TR for the referee, TV for protection against
intent, TG for governance. The old numbers S1-S10 no longer appear as a word, outside the runs of
the referee (which stay as they were written) and the data (where a product name may hold one)."""

import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP = ("referee/runs/", "products/", "publishers/", "tests/test_numbering.py")  # this file names them
TEXT = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh", ".toml", ".cfg"}
OLD = re.compile(r"\bS(10|[1-9])\b")


def files(root: Path) -> list[Path]:
    """Every file under root, without walking into the folders of SKIP: the data is not read (TAX-56)."""
    out = []
    for folder, dirs, names in os.walk(root):
        rel = Path(folder).relative_to(root).as_posix() + "/"
        dirs[:] = [d for d in dirs if not (rel.lstrip("./") + d + "/").startswith(SKIP)]
        out += [Path(folder) / n for n in names]
    return sorted(out)


def is_name(rel: str, line: str) -> bool:
    """A product name, not a rule: the name field of a proposed file, or a name in an example of the
    writing rule."""
    stripped = line.lstrip()
    return stripped.startswith("name: ") or (rel == "tests/test_keyrule.py" and stripped.startswith('(("'))


def old_numbers(root: Path = REPO) -> list[str]:
    hits = []
    for path in files(root):
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


class AgreedModelTest(unittest.TestCase):
    """TG12 (TAX-46): every default for model and effort, in code and in workflows, is the agreed stand."""

    def test_every_default_is_the_agreed_stand(self):
        import re
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import referee
        root = Path(__file__).resolve().parent.parent
        models, efforts = set(), set()
        for wf in sorted((root / ".github/workflows").glob("*.yml")):
            text = wf.read_text(encoding="utf-8")
            models |= set(re.findall(r"REFEREE_MODEL \|\| '([^']+)'", text))
            efforts |= set(re.findall(r"REFEREE_EFFORT \|\| '([^']+)'", text))
            for block in re.findall(r"\n      (model|models|effort|efforts):\n((?:        .*\n)+)", text):
                default = re.search(r"default: \"?([^\"\n]*)\"?", block[1])
                if default and default.group(1):
                    (models if block[0].startswith("model") else efforts).update(default.group(1).split())
        self.assertEqual(models, {referee.MODEL})
        self.assertEqual(efforts, {referee.EFFORT})
        readme = (root / "referee/README.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count("### The agreed model"), 1)
