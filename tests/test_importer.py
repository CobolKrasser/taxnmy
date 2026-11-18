"""The importer of a CVE round (TAX-27): tools/importer.py."""

import argparse
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import importer  # noqa: E402
import referee  # noqa: E402
from fetch import Source  # noqa: E402
from test_group import Block  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
QUIET = lambda *a: None  # noqa: E731
HEAD = ";".join(importer.COLUMNS)
TEXT = "x" * 300  # enough text to count as read


def write(root: Path, rel: str, data) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def dataset() -> Path:
    """Publisher acme, with one product; no other publisher."""
    root = Path(tempfile.mkdtemp())
    write(root, "publishers/acme.yaml", {"key": "acme", "name": "Acme", "source": "https://acme.example/", "aliases": []})
    write(root, "products/acme/router-os.yaml", {
        "key": "acme/router-os", "publisher": "acme", "name": "Router OS", "kind": "operating-system",
        "source": "https://acme.example/os", "versions": {"source": "https://acme.example/os/notes"},
        "aliases": ["acme/ros"]})
    (root / "referee").mkdir()
    shutil.copy(REPO / "referee/parking.yaml", root / "referee/parking.yaml")
    return root


def csv_file(folder: Path, rows: list[str], name="261118-kev-groep-01.csv") -> Path:
    path = folder / name
    path.write_text("\n".join([HEAD, *rows]) + "\n", encoding="utf-8")
    return path


ROWS = [
    # vendor;listed;publisher;publisher_url;name;kind;source;versions;note
    "Acme;Widget;Acme;https://acme.example/;Acme Widget;software;https://acme.example/widget;https://acme.example/widget/notes;",
    "Acme;Widget and Gadget;Acme;https://acme.example/;Acme Widget;software;https://acme.example/widget;https://acme.example/widget/notes;zelfde product als Widget",
    "Acme;Multiple Products;Acme;https://acme.example/;;software;;;reeks",
    "Acme;Widget Core;Acme;https://acme.example/;Acme Widget;software;https://acme.example/widget;https://acme.example/widget/notes;editie of versie van Acme Widget",
    "Acme;ROS;Acme;https://acme.example/;Router OS;operating-system;https://acme.example/os;https://acme.example/os/notes;",
    "Acme;Thing;Acme;niet gevonden;niet gevonden;software;niet gevonden;niet gevonden;niet gevonden",
    "Acme;Kindless;Acme;https://acme.example/;Kindless;tool;https://acme.example/k;https://acme.example/k/notes;",
    "Acme;Noversions;Acme;https://acme.example/;Noversions;software;https://acme.example/n;;niet meer ondersteund",
    "Beta;Box;Beta Systems GmbH;https://beta.example/;Beta Box;device;https://beta.example/box;https://beta.example/box/fw;",
]


class FakeFetcher:
    """url -> (status, text); anything else is a 404."""

    def __init__(self, pages: dict, final: dict | None = None):
        self.pages, self.final, self.asked = pages, final or {}, []

    def fetch(self, url, product=""):
        self.asked.append(url)
        status, text = self.pages.get(url, ("404", ""))
        return Source(url, status, text)

    def close(self):
        pass


def pages(**changes) -> dict:
    base = {
        "https://acme.example/": ("200", f"Acme {TEXT}"),
        "https://acme.example/widget": ("200", f"The Acme Widget {TEXT}"),
        "https://acme.example/widget/notes": ("200", f"Release notes {TEXT}"),
        "https://beta.example/": ("200", f"Welcome to Beta Systems {TEXT}"),
        "https://beta.example/box": ("200", f"Beta Box {TEXT}"),
        "https://beta.example/box/fw": ("200", f"Firmware {TEXT}"),
    }
    base.update(changes)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.root = dataset()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.addCleanup(shutil.rmtree, self.tmp)

    def rows(self, rows=ROWS):
        return importer.read_found([csv_file(self.tmp, rows)])

    def by_listed(self, outcomes):
        return {(o.row.listed, o.row.name): o for o in outcomes}


class ReadTest(Base):
    def test_a_value_that_is_no_url_counts_as_empty(self):
        row = self.rows()[5]
        self.assertEqual((row.publisher_url, row.name, row.source, row.versions), ("", "", "", ""))
        self.assertEqual(row.publisher, "Acme")

    def test_a_kind_outside_the_three_is_empty(self):
        self.assertEqual(self.rows()[6].kind, "")

    def test_missing_columns_are_refused(self):
        path = self.tmp / "bad.csv"
        path.write_text("a;b\n1;2\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            importer.read_found([path])

    def test_first_takes_the_trial(self):
        self.assertEqual(len(importer.first(self.rows(), 4)), 4)


class PlanTest(Base):
    def test_one_outcome_per_row(self):
        got = [(o.outcome, o.reason) for o in importer.plan(self.rows(), self.root)]
        self.assertEqual(got, [
            ("candidate", ""), ("alias", "same-row"), ("parked", "series"), ("parked", "edition"),
            ("alias", "existing"), ("parked", "not-found"), ("parked", "no-kind"), ("parked", "no-versions"),
            ("candidate", "")])

    def test_keys_and_publishers(self):
        out = importer.plan(self.rows(), self.root)
        self.assertEqual(out[0].key, "acme/acme-widget")
        self.assertFalse(out[0].new_publisher)
        self.assertEqual(out[1].of, "acme/acme-widget")
        self.assertEqual(out[2].key, "acme/multiple-products", "a series is parked under the listed name")
        self.assertEqual(out[3].key, "acme/widget-core", "an edition too, not under the product it belongs to")
        self.assertEqual(out[4].of, "acme/router-os")
        self.assertEqual((out[8].publisher, out[8].new_publisher), ("beta-systems", True))

    def test_an_existing_product_is_found_by_its_name_or_an_alias(self):
        rows = self.rows(["Acme;R;Acme;https://acme.example/;ROS;software;https://acme.example/os;https://acme.example/os/notes;"])
        o = importer.plan(rows, self.root)[0]
        self.assertEqual((o.outcome, o.reason, o.of), ("alias", "existing", "acme/router-os"))

    def test_the_vendor_of_the_list_is_no_publisher(self):
        rows = self.rows(["Acme;Widget;;;Acme Widget;software;https://acme.example/widget;https://acme.example/widget/notes;"])
        o = importer.plan(rows, self.root)[0]
        self.assertEqual(o.publisher, "acme", "used only to name the publisher key")
        rows = self.rows(["Gamma;Widget;Gamma Inc;;Gamma Widget;software;;https://g.example/n;"])
        self.assertEqual(importer.plan(rows, self.root)[0].reason, "not-found", "no page, no candidate")
        rows = self.rows(["Gamma;Widget;;;Gamma Widget;software;https://g.example/w;https://g.example/n;"])
        o = importer.plan(rows, self.root)[0]
        self.assertEqual((o.reason, o.detail), ("not-found", "uitgever"), "a new publisher only by the finder's name")

    def test_the_grouping_parks_and_aliases(self):
        classes = {"acme": {"acme-widget": {"class": "series", "of": None}},
                   "beta-systems": {"beta-box": {"class": "alias", "of": "beta-systems/box"}}}
        out = importer.plan(self.rows(), self.root, classes)
        self.assertEqual((out[0].outcome, out[0].reason), ("parked", "grouping"))
        self.assertEqual((out[8].outcome, out[8].of), ("alias", "beta-systems/box"))

    def test_grouped_aliases_go_to_the_candidate_or_the_existing_product(self):
        out = importer.plan(self.rows(), self.root)
        classes = {"acme": {"widget": {"class": "alias", "of": "Acme Widget"},
                            "ros-classic": {"class": "alias", "of": "acme/router-os"}}}
        extra = importer.add_grouped_aliases(out, classes)
        self.assertEqual(out[0].aliases, ["acme/widget"])
        self.assertEqual(extra, {"acme/router-os": ["acme/ros-classic"]})
        files = importer.files_of(out, {}, extra, self.root)
        self.assertEqual(yaml.safe_load(files["products/acme/router-os.yaml"])["aliases"], ["acme/ros", "acme/ros-classic"])


class PagesTest(Base):
    def outcomes(self, fetcher, rows=ROWS):
        out = importer.plan(self.rows(rows), self.root)
        pubs = importer.check_pages(out, fetcher, getattr(fetcher, "final", {}))
        importer.follow(out)
        return out, pubs

    def test_all_read(self):
        out, pubs = self.outcomes(FakeFetcher(pages()))
        self.assertEqual([o.outcome for o in out if o.outcome == "candidate"], ["candidate", "candidate"])
        self.assertEqual(pubs, {"beta-systems": ("Beta Systems GmbH", "https://beta.example/")},
                         "the name without its legal form is on the page")

    def test_a_closed_source_is_parked_and_never_worked_around(self):
        f = FakeFetcher(pages(**{"https://acme.example/widget": ("refused", "")}))
        out, _ = self.outcomes(f)
        self.assertEqual((out[0].outcome, out[0].reason), ("parked", "closed"))
        self.assertEqual((out[1].outcome, out[1].reason), ("parked", "closed"), "the same product follows its row")
        self.assertIn("volgt rij", out[1].detail)
        self.assertEqual(f.asked.count("https://acme.example/widget"), 1)

    def test_an_unreadable_source(self):
        out, _ = self.outcomes(FakeFetcher(pages(**{"https://beta.example/box/fw": ("404", "")})))
        self.assertEqual((out[8].outcome, out[8].reason), ("parked", "unreadable"))

    def test_a_redirect_to_another_product_shows_on_the_name(self):
        f = FakeFetcher(pages(**{"https://beta.example/box": ("200", f"Beta Sandbox {TEXT}")}),
                        final={"https://beta.example/box": "https://beta.example/sandbox"})
        out, _ = self.outcomes(f)
        self.assertEqual((out[8].outcome, out[8].reason), ("parked", "name-not-on-page"))
        self.assertIn("omgeleid naar https://beta.example/sandbox", out[8].detail)

    def test_a_wrong_publisher_page_is_passed_over(self):
        f = FakeFetcher(pages(**{"https://beta.example/": ("200", f"Another {TEXT}"),
                                 "https://beta.example/box": ("200", f"Beta Box by Beta Systems {TEXT}")}))
        _, pubs = self.outcomes(f)
        self.assertEqual(pubs["beta-systems"][1], "https://beta.example/box")

    def test_no_page_names_the_new_publisher(self):
        f = FakeFetcher(pages(**{"https://beta.example/": ("200", f"Another {TEXT}")}))
        out, pubs = self.outcomes(f)
        self.assertNotIn("beta-systems", pubs)
        self.assertEqual((out[8].outcome, out[8].reason), ("parked", "no-publisher-page"))

    def test_the_threshold_leaves_closed_sources_out(self):
        out, _ = self.outcomes(FakeFetcher(pages(**{"https://acme.example/widget": ("refused", "")})))
        parked, total, stop = importer.threshold(out)
        self.assertEqual((parked, total, stop), (5, 9, True), "series, edition, not found, no kind, no versions")
        out, _ = self.outcomes(FakeFetcher(pages()), ROWS[:2] + ROWS[4:5] + ROWS[8:])
        self.assertEqual(importer.threshold(out), (0, 4, False))


class FilesTest(Base):
    def test_the_files_pass_the_schemas(self):
        out = importer.plan(self.rows(), self.root)
        pubs = importer.check_pages(out, FakeFetcher(pages()))
        files = importer.files_of(out, pubs, {}, self.root)
        self.assertEqual(sorted(files), ["products/acme/acme-widget.yaml", "products/beta-systems/beta-box.yaml",
                                         "publishers/beta-systems.yaml"])
        for rel, text in files.items():
            schema = json.loads((REPO / "schema" / ("product" if rel.startswith("products/") else "publisher")).with_suffix(
                ".schema.json").read_text())
            jsonschema.validate(yaml.safe_load(text), schema)

    def test_parked_entries_wait_and_carry_their_files(self):
        out = importer.plan(self.rows(), self.root)
        entries = {e["key"]: e for e in importer.parked_entries(out, {}, "2026-11-18")}
        self.assertEqual(set(entries), {"acme/multiple-products", "acme/widget-core", "acme/thing", "acme/kindless",
                                        "acme/noversions"})
        e = entries["acme/noversions"]
        self.assertEqual((e["waits_for"], e["reason"]), ("better-proposal", "geen versions.source"))
        self.assertEqual(e["fingerprint"], importer.pr_referee.fingerprint(e["files"]))


class ProposeTest(Base):
    def args(self, **kw):
        a = dict(found=[str(csv_file(self.tmp, ROWS))], first=None, classes=None, names=None, fetch=True,
                 write=False, out=str(self.tmp / "out"))
        a.update(kw)
        return argparse.Namespace(**a)

    def test_a_dry_run_changes_no_data(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        got = importer.propose(self.args(), FakeFetcher(pages()), self.root, NOW, QUIET)
        self.assertEqual(sorted(p.relative_to(self.root) for p in self.root.rglob("*")), before)
        out = self.tmp / "out"
        self.assertTrue((out / "data/products/acme/acme-widget.yaml").is_file())
        parked = yaml.safe_load((out / "data/referee/parked.yaml").read_text())["parked"]
        self.assertEqual(len(parked), 5)
        self.assertIn("| opgenomen-kandidaat | voor de scheidsrechter | 2 |", got["report"])
        self.assertIn("2 voordrachten x $0.027 = $0.05", got["report"])
        self.assertEqual({i["outcome"] for i in got["items"]}, {"pending"}, "the gate leaves both to the referee")

    def test_write_puts_the_files_into_the_data(self):
        importer.propose(self.args(write=True), FakeFetcher(pages()), self.root, NOW, QUIET)
        self.assertTrue((self.root / "products/beta-systems/beta-box.yaml").is_file())
        self.assertTrue((self.root / "publishers/beta-systems.yaml").is_file())
        self.assertEqual(len(yaml.safe_load((self.root / "referee/parked.yaml").read_text())["parked"]), 5)

    def test_too_many_files_for_one_pull_request(self):
        rows = [f"Acme;P{i};Acme;https://acme.example/;Product {i};software;https://acme.example/p{i};https://acme.example/n;"
                for i in range(importer.pr_referee.MAX_FILES + 1)]
        with self.assertRaises(SystemExit):
            importer.propose(self.args(found=[str(csv_file(self.tmp, rows, "big.csv"))], fetch=False), None, self.root, NOW, QUIET)


class FakeClient:
    """The Message Batches API: every name of a publisher classed product."""

    def __init__(self):
        self.batches = []

    def count(self, request):
        return 1000

    def submit(self, requests):
        self.batches.append(requests)
        return f"batch_{len(self.batches)}"

    def status(self, batch_id):
        return "ended"

    def cancel(self, batch_id):
        pass

    def results(self, batch_id):
        for cid, request in self.batches[int(batch_id.split("_")[1]) - 1]:
            asked = re.search(r"Answer: (.*)", request["messages"][0]["content"]).group(1).split(", ")
            names = [{"id": i, "class": "product", "of": None, "reason": "fake"} for i in asked]
            usage = {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                     "output_tokens": 100, "web_search_requests": 0}
            yield cid, referee.Response(content=[Block(json.dumps({"names": names}))], stop_reason="end_turn", usage=usage)


class GroupingTest(Base):
    def args(self, **kw):
        a = dict(found=[str(csv_file(self.tmp, ROWS))], first=None, kev=None, cve=None, since="",
                 prompt=str(REPO / "referee/prompt-group.md"), schema=str(REPO / "referee/group.schema.json"),
                 prices=str(REPO / "referee/prices.yaml"), runs=str(self.tmp / "runs"), model="claude-opus-5-5",
                 effort="low", max_tokens=8000, budget=50.0, wait=60, live=False, out=str(self.tmp / "g"))
        a.update(kw)
        return argparse.Namespace(**a)

    def kev(self) -> tuple[Path, Path]:
        cve = self.tmp / "cve"
        cve.mkdir()
        (cve / "CVE-2025-0001.json").write_text(json.dumps({"containers": {"cna": {"affected": [
            {"product": "Widget Pro", "versions": [{"version": "2.1"}]}]}}}))
        kev = self.tmp / "kev.json"
        kev.write_text(json.dumps({"vulnerabilities": [
            {"cveID": "CVE-2025-0001", "vendorProject": "Acme", "product": "Widget", "dateAdded": "2025-01-01"},
            {"cveID": "CVE-2025-0002", "vendorProject": "Other", "product": "Unrelated", "dateAdded": "2025-01-01"}]}))
        return kev, cve

    def test_the_list_names_go_to_the_publisher_the_finder_found(self):
        kev, cve = self.kev()
        candidates, mapping = importer.grouping_candidates(self.rows(), json.loads(kev.read_text()), cve, "", self.root)
        self.assertEqual(mapping["Beta"], "beta-systems")
        names = {(c["publisher"], c["name"]) for c in candidates}
        self.assertIn(("Acme", "Widget Pro"), names)
        self.assertNotIn(("Other", "Unrelated"), names, "only the vendors of the rows")

    def test_a_dry_run_sends_nothing(self):
        kev, cve = self.kev()
        got = importer.run_grouping(self.args(kev=str(kev), cve=str(cve)), client=object(), data_root=self.root, log=QUIET)
        self.assertLess(got["estimate"]["expected"], 0.10)
        classes = json.loads((self.tmp / "g/classes.json").read_text())
        self.assertEqual(classes["acme"]["router-os"], {"class": "alias", "of": "acme/router-os"}, "decided by code")
        self.assertFalse((self.tmp / "runs").exists())

    def test_a_live_run_is_one_batch(self):
        kev, cve = self.kev()
        client = FakeClient()
        got = importer.run_grouping(self.args(kev=str(kev), cve=str(cve), live=True), client=client, data_root=self.root,
                                    now=NOW, log=QUIET, sleep=lambda s: None)
        self.assertEqual(len(client.batches), 1)
        self.assertGreater(got["cost"], 0)
        classes = json.loads((self.tmp / "g/classes.json").read_text())
        self.assertEqual(classes["acme"]["widget-pro"]["class"], "product")
        self.assertTrue(list((self.tmp / "runs").glob("*/run.json")), "the budget sees the run")


class NoBrandTest(unittest.TestCase):
    """R3: the importer names no publisher of the dataset."""

    def test_no_publisher_name_in_the_code(self):
        code = (REPO / "tools/importer.py").read_text().lower()
        for f in (REPO / "publishers").glob("*.yaml"):
            p = yaml.safe_load(f.read_text())
            for n in {p["key"].replace("-", " "), p["name"]}:
                if len(n) >= 4:
                    self.assertIsNone(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", code), n)


if __name__ == "__main__":
    unittest.main()
