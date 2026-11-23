"""CSAF as a filler (TAX-23): tools/csaf.py on made-up advisories, list and dataset, without network.
No test reads products/ or publishers/ of the repository (TAX-56)."""

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import csaf  # noqa: E402
import fetch  # noqa: E402
import lint  # noqa: E402

VENDOR = "https://psirt.acme.example/.well-known/csaf/provider-metadata.json"
COORD = "https://cert.example/.well-known/csaf/provider-metadata.json"
LIST = {"version": 1, "addresses": [{"url": VENDOR, "role": "vendor"}, {"url": COORD, "role": "coordinator"}],
        "not_publishers": ["open source", "unknown"], "team_words": ["psirt"]}


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def product(name, pid, helper=None, versions=()):
    br = {"category": "product_name", "name": name,
          "branches": [{"category": "product_version", "name": v, "product": {"product_id": f"{pid}-{v}", "name": f"{name} {v}"}}
                       for v in versions]}
    if helper is not None:
        br["product"] = {"product_id": pid, "name": name, "product_identification_helper": helper}
    elif not versions:
        br["product"] = {"product_id": pid, "name": name}
    return br


def advisory(publisher, category, vendors, relationships=()):
    return {"document": {"csaf_version": "2.0", "title": "t", "publisher": {"name": publisher, "category": category,
                                                                              "namespace": "https://acme.example"},
                         "tracking": {"id": "X-1"}},
            "product_tree": {"branches": [{"category": "vendor", "name": v, "branches": b} for v, b in vendors],
                             "relationships": list(relationships)}}


DOCS = [
    (VENDOR, advisory("Acme", "vendor", [("Acme Corp", [
        product("Router 9", "p1", {"cpe": "cpe:2.3:h:acme:router_9:-:*:*:*:*:*:*:*"}),          # R1: a device
        product("Edge OS", "p2", {"cpe": "cpe:/o:acme:edge_os:3"}),                              # R1, the 2.2 binding
        product("Widget Library", "p3", {"purl": "pkg:npm/acme-widget@1.2.3"}),                 # R2
        product("Panel Firmware", "p4", versions=("V1.0", "V2.0")),                             # R3, versions are versions
        product("Sensor Q7 (6AB1234-5CD00)", "p5", {"model_numbers": ["6AB1234-5CD00"]}),       # R4, order number off
        product("Sensor Q7 (6AB1234-5CD01)", "p6", {"model_numbers": ["6AB1234-5CD01"]}),       # the same product
        product("Agent", "p7"),                                                                 # R6, installed on p1
        product("Dict Thing", "p8"),                                                            # R8
        product("Mystery", "p9"),                                                               # no kind
        product("Rocket", "p10", {"cpe": "cpe:2.3:a:acme:rocket"}),                             # in TAX
    ])], relationships=[{"category": "installed_on", "product_reference": "p7", "relates_to_product_reference": "p1",
                         "full_product_name": {"product_id": "p7-on-p1", "name": "Agent on Router 9"}}])),
    (COORD, advisory("Cert", "coordinator", [
        ("Acme", [product("Router 9", "c1", {"model_numbers": ["R9"]})]),                       # the same product as p1
        ("Zeta Systems GmbH", [product("Camera Z", "c2", {"skus": ["Z-1"]})]),                  # a new publisher
        ("Open Source", [product("tar", "c3", {"cpe": "cpe:2.3:a:gnu:tar"})]),                  # no publisher
        ("Acme PSIRT", [product("Thirdparty", "c4", {"cpe": "cpe:2.3:a:x:thirdparty"})]),       # a team, no publisher
        ("Alpha, Beta, Gamma", [product("Box", "c5", {"skus": ["B"]})]),                        # several vendors
    ])),
]
DICTIONARY = {"acme|dict_thing": {"a"}}


class CsafTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        shutil.copytree(REPO / "schema", self.root / "schema")
        write(self.root, "publishers/acme.yaml", "key: acme\nname: Acme\nsource: https://acme.example/about\naliases: []\n")
        write(self.root, "products/acme/launcher.yaml",
              "key: acme/launcher\npublisher: acme\nname: Launcher\nkind: software\nsource: https://acme.example/rocket\n"
              "versions:\n  source: https://acme.example/rocket\nidentifiers:\n- cpe: cpe:2.3:a:acme:rocket\n"
              "  source: https://acme.example/rocket\naliases: []\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_round(self, **kw):
        got = csaf.run(iter(DOCS), self.tmp / "out", self.root, data=LIST, dictionary=DICTIONARY, **kw)
        return got, {(i.address, i.vendor, i.name): i for i in got["items"]}

    def test_every_product_has_one_outcome_with_a_reason(self):
        got, _ = self.run_round()
        self.assertEqual(len(got["items"]), 10 + 5)
        for i in got["items"]:
            self.assertIn(i.outcome, ("taken-in", "in-tax", "apart"), i.name)
            self.assertTrue(i.reason, i.name)

    def test_the_kind_follows_the_rules_in_order(self):
        got, _ = self.run_round()
        kind = {k: (p.kind, p.rule[:2]) for k, p in got["products"].items()}
        self.assertEqual(kind["acme/router-9"], ("device", "R1"))
        self.assertEqual(kind["acme/edge-os"], ("operating-system", "R1"))
        self.assertEqual(kind["acme/widget-library"], ("software", "R2"))
        self.assertEqual(kind["acme/panel"], ("device", "R3"))
        self.assertEqual(kind["acme/sensor-q7"], ("device", "R4"))
        self.assertEqual(kind["acme/agent"], ("software", "R6"))
        self.assertEqual(kind["acme/dict-thing"], ("software", "R8"))
        self.assertEqual(kind["zeta-systems/camera-z"], ("device", "R4"))

    def test_the_outcomes(self):
        _, by = self.run_round()
        self.assertEqual(by[(VENDOR, "Acme Corp", "Mystery")].outcome, "apart")
        self.assertIn("no kind", by[(VENDOR, "Acme Corp", "Mystery")].reason)
        self.assertEqual((by[(VENDOR, "Acme Corp", "Rocket")].outcome, by[(VENDOR, "Acme Corp", "Rocket")].product),
                         ("in-tax", "acme/launcher"), "its CPE is on a product in TAX: no new file")
        self.assertEqual(by[(COORD, "Acme", "Router 9")].product, "acme/router-9", "fabricant and coordinator: one product")
        for vendor, name in (("Open Source", "tar"), ("Acme PSIRT", "Thirdparty"), ("Alpha, Beta, Gamma", "Box")):
            self.assertEqual(by[(COORD, vendor, name)].outcome, "apart", vendor)
        self.assertIn("several vendors", by[(COORD, "Alpha, Beta, Gamma", "Box")].reason)

    def test_the_kind_of_an_ai_run_only_where_no_rule_gives_one(self):
        kinds_csv = self.tmp / "kinds.csv"
        kinds_csv.write_text("nr,vendor,name,kind,why\n1,Acme Corp,Mystery,device,a box\n"
                             "2,Acme Corp,Router 9,software,wrong\n", encoding="utf-8")
        got, by = self.run_round(kinds=csaf.read_kinds(kinds_csv))
        self.assertEqual((got["products"]["acme/mystery"].kind, got["products"]["acme/mystery"].rule[:3]), ("device", "R11"))
        self.assertEqual(got["products"]["acme/router-9"].rule[:2], "R1", "a rule goes before the AI run")
        self.assertIn("kinds from an AI run", got["report"])
        _, by = self.run_round(kinds={("Acme Corp", "Mystery"): "unknown"})
        self.assertEqual(by[(VENDOR, "Acme Corp", "Mystery")].outcome, "apart")
        self.assertIn("unknown", by[(VENDOR, "Acme Corp", "Mystery")].reason)

    def test_variants_and_versions_are_one_product(self):
        got, by = self.run_round()
        self.assertEqual(by[(VENDOR, "Acme Corp", "Sensor Q7 (6AB1234-5CD01)")].product, "acme/sensor-q7")
        self.assertEqual(got["products"]["acme/sensor-q7"].name, "Sensor Q7")
        self.assertEqual(csaf.product_name("Enterprise Linux AppStream (v. 9)"), "Enterprise Linux AppStream")
        self.assertEqual(csaf.product_name("Bed (enabled with Wireless)"), "Bed (enabled with Wireless)")
        self.assertNotIn("acme/panel-v1-0", got["products"], "a version is no product of its own (TP3)")

    def test_the_source_is_the_address_of_the_vendor_before_the_coordinator(self):
        got, _ = self.run_round()
        router = yaml.safe_load(got["products"]["acme/router-9"].text())
        self.assertEqual((router["source"], router["versions"]["source"]), (VENDOR, VENDOR))
        self.assertEqual(router["identifiers"], [{"cpe": "cpe:2.3:h:acme:router_9", "source": VENDOR}])
        widget = yaml.safe_load(got["products"]["acme/widget-library"].text())
        self.assertEqual(widget["identifiers"][0]["purl"], "pkg:npm/acme-widget", "without its version")
        camera = yaml.safe_load(got["products"]["zeta-systems/camera-z"].text())
        self.assertEqual(camera["source"], COORD, "only the coordinator names it")

    def test_publishers_keep_their_key_and_get_the_csaf_address(self):
        got, _ = self.run_round()
        self.assertEqual(set(got["publishers"]), {"zeta-systems"})
        acme = yaml.safe_load(got["files"]["publishers/acme.yaml"])
        self.assertEqual((acme["key"], acme["source"]), ("acme", "https://acme.example/about"), "criterion 7")
        self.assertEqual([m["url"] for m in acme["machine_sources"]], [VENDOR], "its own address, not the coordinator's")
        zeta = yaml.safe_load(got["files"]["publishers/zeta-systems.yaml"])
        self.assertEqual((zeta["name"], zeta["source"]), ("Zeta Systems GmbH", COORD))
        self.assertEqual(zeta["machine_sources"], [{"url": COORD, "format": "csaf-provider", "source": COORD}])

    def test_what_is_written_passes_the_lint(self):
        self.run_round(write=True)
        self.assertTrue((self.root / "products/acme/router-9.yaml").is_file())
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    def test_the_round_copies_the_advisory(self):
        """The round copies: every name it writes is a name of the advisory, less the variant and version (TP3)."""
        got, _ = self.run_round()
        named = {csaf.product_name(b["name"]) for _, d in DOCS for v in d["product_tree"]["branches"] for b in v["branches"]}
        self.assertTrue({p.name for p in got["products"].values()} <= named)

    def test_the_report(self):
        got, _ = self.run_round()
        self.assertIn("No AI call", got["report"])
        self.assertIn("| device | 4 |", got["report"])

    def test_the_dump_is_read_back(self):
        dump = self.tmp / "dump"
        (dump / "docs").mkdir(parents=True)
        (dump / "docs/a.json.gz").write_bytes(gzip.compress(json.dumps(DOCS[0][1]).encode()))
        (dump / "index.tsv").write_text(f"{VENDOR}\thttps://psirt.acme.example/a.json\tdocs/a.json.gz\n")
        (dump / "refused.tsv").write_text("https://psirt.acme.example/b.json\t429\n")
        self.assertEqual([a for a, _ in csaf.read_dump(dump)], [VENDOR])
        self.assertEqual(csaf.refused(dump), [("https://psirt.acme.example/b.json", "429")])

    def test_fetch_follows_the_conduct_and_does_not_try_a_refusal_again(self):
        meta = {"canonical_url": VENDOR, "distributions": [
            {"directory_url": "https://psirt.acme.example/csaf/"},
            {"rolie": {"feeds": [{"url": "https://psirt.acme.example/feed.json"}]}}]}
        feed = {"feed": {"entry": [{"link": [{"rel": "self", "href": "https://psirt.acme.example/csaf/2.json"}]}]}}
        pages = {VENDOR: (200, json.dumps(meta)), "https://psirt.acme.example/csaf/index.txt": (200, "1.json\n3.json\n"),
                 "https://psirt.acme.example/feed.json": (200, json.dumps(feed)),
                 "https://psirt.acme.example/csaf/1.json": (200, json.dumps(DOCS[0][1])),
                 "https://psirt.acme.example/csaf/2.json": (200, json.dumps(DOCS[0][1])),
                 "https://psirt.acme.example/csaf/3.json": (403, ""),
                 "https://psirt.acme.example/robots.txt": (200, "User-agent: *\nDisallow: /csaf/2.json\n")}
        asked = []

        def get(url, timeout):
            asked.append(url)
            status, body = pages.get(url, (404, ""))
            return fetch.Response(status=status, ctype="application/json", body=body.encode(), final_url=url)

        f = fetch.Fetcher(use_browser=False, get=get, sleep=lambda s: None)
        dump = self.tmp / "dump"
        data = {"version": 1, "addresses": [{"url": VENDOR, "role": "vendor"}]}
        got = csaf.fetch(dump, data, f, log=lambda *a: None)
        self.assertEqual((got["documents"], got["refused"]), (1, 2))
        self.assertNotIn("https://psirt.acme.example/csaf/2.json", asked, "robots.txt forbids it")
        self.assertEqual(sorted(r for _, r in csaf.refused(dump)), ["403", "robots.txt"])
        asked.clear()
        csaf.fetch(dump, data, fetch.Fetcher(use_browser=False, get=get, sleep=lambda s: None), log=lambda *a: None)
        self.assertNotIn("https://psirt.acme.example/csaf/3.json", asked, "a refusal is not tried again")
        self.assertNotIn("https://psirt.acme.example/csaf/1.json", asked, "what is in the dump is not fetched again")

    def test_the_list_of_the_repository_holds_addresses_not_code(self):
        data = csaf.load_list()
        self.assertTrue(data["addresses"])
        for a in data["addresses"]:
            self.assertTrue(a["url"].startswith("https://"), a)
            self.assertIn(a["role"], ("vendor", "coordinator"))
        code = (REPO / "tools/csaf.py").read_text()
        for a in data["addresses"]:
            self.assertNotIn(a["url"].split("/")[2], code, "the code names no address (TG4)")


if __name__ == "__main__":
    unittest.main()
