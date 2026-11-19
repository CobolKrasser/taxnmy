"""The importer of a CVE round (TAX-27): tools/importer.py."""

import argparse
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from dataclasses import asdict
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
        self.assertEqual((out[8].outcome, out[8].aliases), ("candidate", ["beta-systems/box"]),
                         "TAX-39: no alias of a product that is neither in TAX nor proposed; the row is the product")

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

    def counted(self, out, verdicts=None):
        t = importer.threshold(out, verdicts, {"acme/router-os"})
        return t["parked"], t["proposed"], t["aside"], t["final"], t["stop"]

    def test_the_threshold_leaves_closed_sources_out(self):
        out, _ = self.outcomes(FakeFetcher(pages(**{"https://acme.example/widget": ("refused", "")})))
        self.assertEqual(self.counted(out), (1, 4, 4, False, False),
                         "series, edition, not found and no versions are set aside; the closed product (two rows) "
                         "does not count as parked; no kind does")
        out, _ = self.outcomes(FakeFetcher(pages()), ROWS[:2] + ROWS[4:5] + ROWS[8:])
        self.assertEqual(self.counted(out), (0, 3, 0, False, False), "two rows of one product count once")

    def test_the_threshold_counts_only_what_is_proposed(self):
        """Decision 33: two of three proposed rows parked stops the round, however many the find round set aside."""
        rows = [ROWS[2], ROWS[3], ROWS[5], ROWS[7], ROWS[0], ROWS[6],
                "Acme;Other;Acme;https://acme.example/;Acme Other;software;https://acme.example/o;https://acme.example/o/n;"]
        out, _ = self.outcomes(FakeFetcher(pages()), rows)
        self.assertEqual(self.counted(out), (2, 3, 4, False, False), "no kind and an unreadable page, of three")
        report = importer.report(out, {}, {}, None, True, set())
        self.assertIn("| apart (vindronde) | geen versions.source | 1 |", report)
        self.assertIn("Apart gezet door de vindronde (telt niet mee): 4 producten.", report)
        self.assertIn("Voorlopige stand, vóór het oordeel van de scheidsrechter:** 2 van 3", report)
        self.assertNotRegex(report, r"\*\*stop\*\*|gaat door", "before the verdict no word on going on (TAX-39)")
        accept = {"verdicts": [{"id": "products/acme/acme-widget.yaml", "outcome": "accept"}]}
        self.assertEqual(self.counted(out, accept), (2, 3, 4, True, True), "after the verdict: two of three, stop")


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
                 write=False, browser=False, gated=None, out=str(self.tmp / "out"))
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
                 effort="low", max_tokens=8000, budget=50.0, cap=None, wait=60, live=False, out=str(self.tmp / "g"))
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


class CapTest(GroupingTest):
    def test_the_cap_of_this_run_is_the_limit(self):
        kev, cve = self.kev()
        client = FakeClient()
        got = importer.run_grouping(self.args(kev=str(kev), cve=str(cve), live=True, cap=0.0), client=client,
                                    data_root=self.root, now=NOW, log=QUIET, sleep=lambda s: None)
        self.assertEqual(client.batches, [], "no room: nothing is sent")
        self.assertEqual(got["cost"], 0.0)
        classes = json.loads((self.tmp / "g/classes.json").read_text())
        self.assertEqual(classes["acme"]["widget-pro"]["class"], "doubt", "what was not asked is doubt, and parked")


class ListTest(Base):
    def test_only_the_records_of_the_vendors_of_the_rows(self):
        kev = {"catalogVersion": "x", "vulnerabilities": [
            {"cveID": "CVE-2025-1234", "vendorProject": "Acme", "product": "Widget", "dateAdded": "2025-01-01"},
            {"cveID": "CVE-2022-0001", "vendorProject": "Acme", "product": "Widget", "dateAdded": "2022-01-01"},
            {"cveID": "CVE-2025-99999", "vendorProject": "Beta", "product": "Box", "dateAdded": "2025-02-01"},
            {"cveID": "CVE-2025-5555", "vendorProject": "Other", "product": "Thing", "dateAdded": "2025-02-01"}]}
        asked = []

        def get(url):
            asked.append(url)
            if url == importer.KEV_URL:
                return json.dumps(kev).encode()
            if url.endswith("CVE-2025-99999.json"):
                raise OSError("404")
            return b'{"containers": {}}'

        args = argparse.Namespace(found=[str(csv_file(self.tmp, ROWS))], first=None, since="2023-11-16", out=str(self.tmp / "l"))
        got = importer.fetch_list(args, get=get, log=QUIET)
        self.assertEqual(got["wanted"], ["CVE-2025-1234", "CVE-2025-99999"])
        self.assertEqual(got["missing"], ["CVE-2025-99999 (OSError)"])
        self.assertIn("https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2025/1xxx/CVE-2025-1234.json", asked)
        self.assertIn("https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2025/99xxx/CVE-2025-99999.json",
                      [importer.cve_url("CVE-2025-99999")])
        self.assertTrue((self.tmp / "l/cve/CVE-2025-1234.json").is_file())
        self.assertTrue((self.tmp / "l/kev.json").is_file())


class GatedTest(ProposeTest):
    def test_the_gated_items_are_for_the_referee_of_the_pull_request(self):
        gated = self.tmp / "gated/gated.json"
        importer.propose(self.args(gated=str(gated)), FakeFetcher(pages()), self.root, NOW, QUIET)
        data = json.loads(gated.read_text())
        self.assertEqual((data["version"], data["pr"], data["fork"]), (1, None, False))
        self.assertEqual(sorted(i["id"] for i in data["items"]),
                         ["products/acme/acme-widget.yaml", "products/beta-systems/beta-box.yaml"])
        items = data["items"]
        importer.pr_referee.judge(items, argparse.Namespace(live=False))
        self.assertEqual({i["outcome"] for i in items}, {"parked"}, "a dry judge parks what it was not asked")


class TrialTest(unittest.TestCase):
    """The input of the paid trial: the first 40 rows of group 1, and nothing more of the find round."""

    def test_forty_rows_with_the_columns_of_the_find_round(self):
        path = REPO / "referee/import/kev-proef-40.csv"
        self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], HEAD)
        rows = importer.read_found([path])
        self.assertEqual(len(rows), 40)
        outcomes = importer.plan(rows)
        t = importer.threshold(outcomes, None, importer.existing_keys(REPO))
        self.assertEqual((t["rows"], t["final"], t["stop"]), (40, False, False), "before the verdict: provisional")


TRIAL = REPO / "tests/fixtures/import-44537500370"  # outcomes, verdicts and grouping of the paid trial


class TrialCountTest(unittest.TestCase):
    """TAX-39 on the artifacts of run 44537500370 (pull request 53): no paid calls."""

    def setUp(self):
        self.outcomes = importer.load_outcomes(TRIAL / "outcomes.json")
        self.verdicts = json.loads((TRIAL / "verdicts.json").read_text())
        # The products TAX held at the time of the trial, as its outcomes name them: not the data as it is now.
        self.keys = {o.of for o in self.outcomes if o.outcome == "alias" and "/" in o.of}

    def test_the_trial_counted_after_the_verdict_and_per_product_stops(self):
        t = importer.threshold(self.outcomes, self.verdicts, self.keys)
        self.assertEqual((t["parked"], t["proposed"], t["final"], t["stop"]), (9, 22, True, True),
                         "the analysis of 19 November, section 2: 9 of 22 products; the report said 7 of 30, go on")
        self.assertEqual((t["rows"], t["products"]), (40, 32))
        ps = importer.products(self.outcomes, self.verdicts, self.keys)
        self.assertEqual(sum(len(p["rows"]) for p in ps.values()), 40, "every row one outcome")
        self.assertEqual(len(ps["adobe/magento-open-source"]["rows"]), 3, "three rows, one product")
        self.assertEqual(sorted(k for k, p in ps.items() if p["status"] == "verloren"), ["Apache HugeGraph-Server", "MegaRAC SPx"])

    def test_new_publishers_parked_on_tr5(self):
        got = importer.tr5_new_publishers(self.outcomes, self.verdicts, self.keys)
        self.assertEqual([(c["publisher"], c["product"]) for c in got], [("jakub-vrana", "jakub-vrana/adminer")],
                         "three more were parked on TR5, but of a publisher TAX has")
        self.assertIn("footer", got[0]["reason"])
        report = importer.final_report(self.outcomes, self.verdicts, self.keys)
        self.assertIn("**Nieuwe uitgevers geparkeerd op TR5** (besluit 35): 1", report)
        self.assertIn("9 van 22 voorgedragen producten", report)
        self.assertIn("**stop**", report)

    def test_break_one_the_referee_parks_everything(self):
        """Deliberate break: with every verdict parked, the report must say stop."""
        parked = {**self.verdicts, "verdicts": [{**v, "outcome": "parked", "rule": "TR5", "reason": "TR5: doubt",
                                                 "waits_for": "better-proposal"} for v in self.verdicts["verdicts"]]}
        t = importer.threshold(self.outcomes, parked, self.keys)
        self.assertEqual((t["parked"], t["final"], t["stop"]), (16, True, True))

    def test_break_two_an_alias_of_a_product_that_does_not_exist(self):
        """Deliberate break: an alias whose product is neither in TAX nor proposed must fail."""
        lost = importer.dangling(self.outcomes, self.keys)
        self.assertEqual(sorted(o.row.line for o in lost), [22, 30], "the trial as it ran")
        root = dataset()
        self.addCleanup(shutil.rmtree, root)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        args = argparse.Namespace(found=[str(csv_file(tmp, ROWS))], first=None, classes=None, names=None, fetch=False,
                                  write=False, browser=False, gated=None, out=str(tmp / "out"))
        real = importer.plan

        def broken(rows, data_root, classes):
            out = real(rows, data_root, classes)
            out[0].outcome, out[0].reason, out[0].of = "alias", "grouping", "acme/ghost"
            return out

        importer.plan = broken
        self.addCleanup(setattr, importer, "plan", real)
        with self.assertRaisesRegex(SystemExit, "acme/ghost"):
            importer.propose(args, None, root, NOW, QUIET)

    def test_the_rows_of_the_trial_planned_again(self):
        """HugeGraph-Server is a candidate with the other name as alias; MegaRAC SP-X has no versions page."""
        classes = json.loads((TRIAL / "classes.json").read_text())
        # A data root of its own, frozen as TAX was before the trial: what TAX holds later (such as the
        # products the trial took in) does not change what this test shows.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        pub = "the-apache-software-foundation"
        write(root, f"publishers/{pub}.yaml", {"key": pub, "name": "The Apache Software Foundation",
                                               "source": "https://www.apache.org/", "aliases": []})
        write(root, f"products/{pub}/apache-activemq-classic.yaml", {
            "key": f"{pub}/apache-activemq-classic", "publisher": pub, "name": "Apache ActiveMQ Classic",
            "kind": "software", "source": "https://activemq.apache.org/", "aliases": [],
            "versions": {"source": "https://activemq.apache.org/components/classic/download"}})
        out = {o.row.line: o for o in importer.plan(importer.read_found([REPO / "referee/import/kev-proef-40.csv"]),
                                                    root, classes)}
        hg, mr = out[30], out[22]
        self.assertEqual((hg.outcome, hg.key), ("candidate", "the-apache-software-foundation/hugegraph-server"))
        self.assertEqual(hg.aliases, ["the-apache-software-foundation/apache-hugegraph-server"])
        self.assertEqual((mr.outcome, mr.reason), ("parked", "no-versions"))
        self.assertTrue(importer.found_aside(mr))
        self.assertEqual(importer.dangling(list(out.values()), importer.existing_keys(root)), [])
        self.assertEqual((out[27].outcome, out[27].of), ("alias", "the-apache-software-foundation/apache-activemq-classic"),
                         "an alias of a product TAX has stays one")

    def test_an_alias_of_a_product_of_the_same_proposal_stays_one(self):
        root = dataset()
        self.addCleanup(shutil.rmtree, root)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        rows = importer.read_found([csv_file(tmp, [
            "Acme;W;Acme;https://acme.example/;Acme W;software;https://acme.example/w;https://acme.example/w/n;",
            "Acme;Widget;Acme;https://acme.example/;Acme Widget;software;https://acme.example/widget;https://acme.example/widget/notes;"])])
        classes = {"acme": {"acme-w": {"class": "alias", "of": "Acme Widget"}}}
        out = importer.plan(rows, root, classes)
        self.assertEqual((out[0].outcome, out[0].of), ("alias", "acme/acme-widget"), "the product comes in a later row")
        self.assertEqual(out[1].outcome, "candidate")

    def test_the_count_command(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            importer.main(["count", "--outcomes", str(TRIAL / "outcomes.json"), "--verdicts", str(TRIAL / "verdicts.json"),
                           "--out", str(tmp / "count.md")])
        self.assertIn("**stop**", (tmp / "count.md").read_text())


class WorkflowTest(unittest.TestCase):
    def test_the_trial_waits_for_fred_and_keeps_the_key_apart(self):
        wf = yaml.safe_load((REPO / ".github/workflows/referee-import.yml").read_text())
        on = wf.get("on", wf.get(True))
        self.assertEqual(set(on), {"workflow_dispatch"})
        inputs = on["workflow_dispatch"]["inputs"]
        self.assertFalse(inputs["live"]["default"])
        self.assertEqual(inputs["budget"]["default"], "1.00")
        self.assertEqual(wf["concurrency"], {"group": "referee-import", "cancel-in-progress": True})
        self.assertEqual(wf["permissions"], {"contents": "read"})
        jobs = wf["jobs"]
        for name, job in jobs.items():
            self.assertIn("timeout-minutes", job, name)
            keyed = "secrets." in json.dumps(job)
            self.assertEqual(keyed, name in ("group", "judge"), name)
            if keyed:
                self.assertEqual(job["environment"], "referee")
                self.assertEqual(job["concurrency"], {"group": "referee-judge", "cancel-in-progress": False})
                self.assertNotIn("write", json.dumps(job.get("permissions", {})))
            for step in job["steps"]:
                self.assertNotRegex(step.get("run", ""), r"\$\{\{", "inputs reach a script through env only")
        self.assertEqual(jobs["group"]["if"], "github.ref == 'refs/heads/main' && inputs.live")
        self.assertEqual(jobs["pull-request"]["permissions"], {"contents": "write", "pull-requests": "write"})
        self.assertNotRegex(json.dumps(wf), r"pr merge|--auto|auto-merge", "never merged by itself")
        self.assertEqual(wf["env"]["FOUND"], "${{ inputs.found }}")
        self.assertEqual(inputs["found"]["default"], "referee/import/kev-proef-40.csv")
        for name in ("prepare", "group", "propose", "pull-request"):
            script = "".join(st.get("run", "") for st in jobs[name]["steps"])
            self.assertIn('list=$(.github/scripts/import-found.sh "$FOUND")', script, name)
            self.assertNotIn('--found "$FOUND"', script, name)
        pr = "".join(st.get("run", "") for st in jobs["pull-request"]["steps"])
        self.assertIn('branch="import/kev-ronde-${RUN_ID}"', pr)
        self.assertIn('--title "TAX-27: KEV-ronde, ${names% }"', pr)
        self.assertNotRegex(pr, r"proef")
        script = next(st["run"] for st in jobs["pull-request"]["steps"] if "run" in st and "importer.py count" in st["run"])
        self.assertLess(script.index("importer.py count"), script.index("cp -R"), "counted against main, before the copy")


class FoundTest(unittest.TestCase):
    """The input `found` of the workflow: files in referee/import/, checked before any job uses them."""

    def found(self, value):
        r = subprocess.run([str(REPO / ".github/scripts/import-found.sh"), value], cwd=REPO, capture_output=True, text=True)
        return r.returncode, r.stdout.split()

    def test_names_and_patterns(self):
        self.assertEqual(self.found("referee/import/kev-proef-40.csv"), (0, ["referee/import/kev-proef-40.csv"]))
        code, files = self.found("referee/import/kev-groep-0[1-6].csv")
        self.assertEqual((code, len(files)), (0, 6))
        code, files = self.found("referee/import/kev-groep-*.csv referee/import/kev-groep-01.csv")
        self.assertEqual((code, len(files)), (0, 11), "each file once")

    def test_anything_else_stops(self):
        for value in ("", "../x.csv", "referee/import/../../etc/passwd.csv", "referee/import/a.csv;rm -rf",
                      "referee/import/none.csv", "referee/testset/cases.yaml", "/referee/import/kev-proef-40.csv"):
            self.assertNotEqual(self.found(value)[0], 0, value)

    def test_the_round_is_the_find_round(self):
        rows = importer.read_found(sorted((REPO / "referee/import").glob("kev-groep-*.csv")))
        self.assertEqual(len(rows), 422)
        trial = importer.read_found([REPO / "referee/import/kev-proef-40.csv"])
        self.assertEqual([asdict(r) | {"file": ""} for r in rows[:40]], [asdict(r) | {"file": ""} for r in trial],
                         "the trial is the first 40 rows of group 1")


class Silent:
    """The Message Batches API, answering every request with nothing usable: enough to see who got room."""

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
        usage = {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                 "output_tokens": 10, "web_search_requests": 0}
        for cid, _ in self.batches[int(batch_id.split("_")[1]) - 1]:
            yield cid, referee.Response(content=[Block("{}")], stop_reason="end_turn", usage=usage)


class AgreedRoundTest(ProposeTest):
    """Fred, 19 November: the KEV round gets an exception from the ceilings per run, per pull request and
    per day, with $10 for the whole round as the hard limit; the budget for every run together stays.
    One place: the submitter kev-ronde-1 in referee/intake.yaml (TAX-32), no code of its own."""

    def test_the_exception_is_one_entry_of_the_intake(self):
        cfg = importer.intake.load(REPO)
        self.assertEqual(importer.intake.own(cfg, "kev-ronde-1"), {"open": 500, "day_cap": 10.0})
        self.assertNotIn("pr_cap", importer.intake.own(cfg, "kev-ronde-1"),
                         "the ceiling per pull request stays what the grouping left of the budget of the run")
        self.assertNotIn("kev-ronde", (REPO / "tools/pr_referee.py").read_text() + (REPO / "tools/importer.py").read_text(),
                         "no code of its own")

    def judged(self, submitter):
        root = self.root
        write(root, "referee/intake.yaml", (REPO / "referee/intake.yaml").read_text())
        gated = self.tmp / f"gated-{submitter or 'none'}.json"
        importer.propose(self.args(gated=str(gated), submitter=submitter), FakeFetcher(pages()), root, NOW, QUIET)
        items = json.loads(gated.read_text())["items"]
        self.assertEqual({i["submitter"] for i in items}, {submitter})
        out = self.tmp / f"runs-{submitter or 'none'}"
        out.mkdir()
        args = argparse.Namespace(model="claude-opus-5-5", effort="low", max_tokens=4000, budget=50.0, pr_cap=1.0,
                                  day_cap=0.0, wait=60, live=True, prompt=str(REPO / "referee/prompt.md"),
                                  schema=str(REPO / "referee/verdict.schema.json"), prices=str(REPO / "referee/prices.yaml"),
                                  out=str(out), pr=None)
        client = Silent()
        importer.pr_referee.judge(items, args, client=client, now=NOW, log=QUIET, sleep=lambda s: None,
                                  limits=importer.intake.load(root))
        return items, client

    def test_the_round_has_its_own_day_ceiling(self):
        items, client = self.judged("")
        self.assertEqual(client.batches, [], "the shared day ceiling (here 0) leaves no room")
        self.assertTrue(all("ceiling leaves no room" in i["reason"] for i in items))
        items, client = self.judged("kev-ronde-1")
        self.assertEqual(len(client.batches[0]), 2, "the day ceiling of the round leaves room for both")
        self.assertFalse(any("ceiling" in i["reason"] for i in items))

    def test_the_workflow_names_the_round_only_when_asked(self):
        wf = yaml.safe_load((REPO / ".github/workflows/referee-import.yml").read_text())
        inputs = wf.get("on", wf.get(True))["workflow_dispatch"]["inputs"]
        self.assertEqual((inputs["submitter"]["default"], inputs["budget"]["default"]), ("", "1.00"))
        step = next(st for st in wf["jobs"]["propose"]["steps"] if "importer.py propose" in st.get("run", ""))
        self.assertEqual(step["env"]["SUBMITTER"], "${{ inputs.submitter }}")
        self.assertIn('--submitter "$SUBMITTER"', step["run"])


class CustomIdTest(GroupingTest):
    """Run 44583147603: a publisher without a Latin key became a custom id, and the second batch got a
    400 after the first was paid. The id is a number now, and every id is checked before anything is sent."""

    def test_the_rule_of_the_batches_api(self):
        ok = [("g0", {}), ("p12", {}), ("r1-P01", {}), ("x" * 64, {})]
        self.assertEqual(referee.id_problems(ok), [])
        wrong = referee.id_problems([("例株式会社", {}), ("a b", {}), ("x" * 65, {}), ("", {}), ("g0", {}), ("g0", {})])
        self.assertEqual(len(wrong), 5, wrong)
        self.assertIn("twice", wrong[-1])

    def test_nothing_is_sent_with_an_id_the_api_refuses(self):
        client = FakeClient()
        budget = referee.Budget(10.0, 0.0, "claude-opus-5-5", referee.load_prices(), batch=True)
        with self.assertRaisesRegex(SystemExit, "nothing sent"):
            referee.batch_rounds(client, budget, [("g0", {}), ("例株式会社", {})], log=QUIET, sleep=lambda s: None)
        self.assertEqual(client.batches, [])

    def test_a_publisher_without_a_latin_key(self):
        kev, cve = self.kev()
        rows = ROWS + ["例;Thing;例株式会社;https://rei.example/;Rei Thing;software;https://rei.example/t;https://rei.example/n;",
                       "例;Other;例株式会社;https://rei.example/;Rei Other;software;https://rei.example/o;https://rei.example/n;"]
        client = FakeClient()
        got = importer.run_grouping(self.args(found=[str(csv_file(self.tmp, rows, "rei.csv"))], kev=str(kev), cve=str(cve),
                                              live=True), client=client, data_root=self.root, now=NOW, log=QUIET,
                                    sleep=lambda s: None)
        sent = [cid for cid, _ in client.batches[0]]
        self.assertEqual(referee.id_problems(client.batches[0]), [])
        self.assertTrue(all(re.fullmatch(r"g\d+", cid) for cid in sent), sent)
        classes = json.loads((self.tmp / "g/classes.json").read_text())
        self.assertIn("例株式会社", classes, "the answer reaches its publisher through the table")
        self.assertEqual(classes["例株式会社"]["rei-thing"]["class"], "product")
        self.assertEqual(got["never"], [])

    def test_the_eleven_groups_of_the_round(self):
        """The real input, without a key: every request of the grouping has an id the API takes."""
        rows = importer.read_found(sorted((REPO / "referee/import").glob("kev-groep-*.csv")))
        candidates, mapping = importer.grouping_candidates(rows, None, None, "", REPO)
        groups = importer.group.collect(candidates, REPO, mapping)
        self.assertTrue(any(not referee.CUSTOM_ID.fullmatch(g.publisher) for g in groups),
                        "the round has a publisher key the API would refuse as an id")
        decided = {g.publisher: importer.group.literal(g) for g in groups}
        requests, by_id = importer.grouping_requests(groups, decided, "system", {"type": "object"}, "claude-opus-5-5", "low")
        self.assertGreater(len(requests), 150)
        self.assertEqual(referee.id_problems(requests), [])
        self.assertEqual(sorted(by_id.values()), sorted({g.publisher for g in groups if len(decided[g.publisher]) < len(g.names)}))


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
