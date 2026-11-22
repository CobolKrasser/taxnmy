"""TAX-16: the lint of a pull request reports only on its own files, and stays fast on a large dataset.

The full size (20,000 products, a pull request of 10,000 new ones) runs when TAXNMY_SCALE_TEST=1,
as the CI job lint-scale does; it must finish within 10 minutes. Without it the same test runs on
a tenth of that size."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import bulkdata  # noqa: E402
import lint  # noqa: E402

FULL = os.environ.get("TAXNMY_SCALE_TEST") == "1"
OLD, NEW = (20_000, 10_000) if FULL else (1_000, 500)
LIMIT = 600  # seconds: the target of TAX-16


def outside(findings, changed) -> list:
    """The findings on a file that the pull request did not change: there must be none."""
    return [f for f in findings if f.file not in changed]


class ScaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        cls.changed = set(bulkdata.generate(cls.root, OLD, NEW))
        (cls.root / "changed.txt").write_text("\n".join(sorted(cls.changed)) + "\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_a_pull_request_is_linted_in_time_and_only_on_its_files(self):
        start = time.monotonic()
        done = subprocess.run([sys.executable, str(REPO / "tools/lint.py"), str(self.root), "--changed", str(self.root / "changed.txt")],
                              capture_output=True, text=True, timeout=LIMIT + 60)
        seconds = time.monotonic() - start
        print(f"\nlint of {NEW} changed files against {OLD + NEW + 1000} files: {seconds:.1f} s", file=sys.stderr)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
                out.write(f"TAX-16 scale test: lint of {NEW} changed files against {OLD + NEW + 1000} files in {seconds:.1f} s\n")
        self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-2000:])
        lines = done.stdout.splitlines()
        self.assertEqual(lines[-2], f"{NEW} changed files checked against {OLD + NEW + 1000}: 0 errors, {NEW // 100} signals")
        self.assertTrue(lines[-1].startswith("pairs of names that look alike (TV3): "), lines[-1])
        reported = {line.split(":", 1)[0] for line in lines[:-2] if not line.startswith("::")}  # not the CI annotations
        self.assertEqual(len(reported), NEW // 100)
        self.assertEqual(reported - self.changed, set(), "a signal on a file outside the pull request")
        self.assertLess(seconds, LIMIT)

    def test_signals_only_on_changed_files(self):
        findings = lint.lint(self.root, REPO / "schema", self.changed)
        self.assertEqual(len(findings), NEW // 100, "each shared CPE once: on the new file, not on the old one")
        self.assertEqual(outside(findings, self.changed), [])

    def test_a_signal_on_an_unchanged_file_is_caught(self):
        """The deliberate break of TAX-16: compare without the restriction to the changed files.
        The check above must then see the signals on the old files."""
        real = lint.check_unique
        with unittest.mock.patch.object(lint, "check_unique", lambda records, kind, findings, report=None: real(records, kind, findings)):
            broken = lint.lint(self.root, REPO / "schema", self.changed)
        self.assertEqual(len(outside(broken, self.changed)), NEW // 100)


if __name__ == "__main__":
    unittest.main()
