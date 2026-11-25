"""Electric cars as a filler (TAX-59): tools/ev.py on small made-up lists and dataset, without network."""

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import ev  # noqa: E402
import lint  # noqa: E402


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def vehicle(make: str, model: str, trim: str = "Base", variant: str | None = None) -> dict:
    v = {"make": {"name": make}, "model": {"name": model}, "trim": {"name": trim}}
    if variant:
        v["variant"] = {"name": variant}
    return v


def xlsx(path: Path, rows: list[list]):
    """A minimal workbook with a sheet EV, its cells as inline strings."""
    def cell(i, j, v):
        ref = chr(65 + j) + str(i + 1)
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>' if v is not None else ""
    data = "".join(f'<row r="{i + 1}">' + "".join(cell(i, j, v) for j, v in enumerate(r)) + "</row>"
                   for i, r in enumerate(rows))
    m = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{m}" xmlns:r="{r}"><sheets>'
                                      f'<sheet name="FEguide" sheetId="1" r:id="rId1"/>'
                                      f'<sheet name="EV" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{m}"><sheetData/></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", f'<worksheet xmlns="{m}"><sheetData>{data}</sheetData></worksheet>')


OEV = {"vehicles": [
    vehicle("Acme", "Volt 3"), vehicle("Acme", "Volt 3", "Long Range"), vehicle("Acme", "Volt 4"),
    vehicle("Acme", "Volt 3", "N"),  # a trim the page publishes as a model of its own
    vehicle("Acme", "Volt 9"),  # not on the page
    vehicle("Zed", "Z1"), vehicle("Zed", "Z2", "Base", "Coupé"),
    vehicle("Closed", "C1"), vehicle("Nothing", "N1"),
    vehicle("Old", "O1"),  # the product is in TAX
]}

FE = [["Model Year", "Mfr Name", "Division", "Carline"],
      [None, None, "2026 Acme Volt 3 AWD", None],  # a title row
      ["2026", "Acme", "ACME MOTORS", "Volt 3 AWD (20in Wheels)"],
      ["2026", "Acme", "ACME MOTORS", "Volt 3 N"],
      ["2026", "Acme", "ACME MOTORS", "V7 RWD"],  # neither on the page nor in Open EV Data
      ["2026", "Nothing", "Nothing", "N2 AWD"],
      ["2026", "Stranger", "Stranger", "Q1"]]  # a brand the list does not name

RDW_FUEL = ["AA01", "AA02", "AA03", "AA04", "AA05", "AA06"]
RDW_VEHICLES = [
    {"kenteken": "AA01", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "VOLT3 LONG RANGE"},
    {"kenteken": "AA02", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "VOLT3 LONG RANGE"},  # twice
    {"kenteken": "AA03", "voertuigsoort": "Bromfiets", "merk": "ZED", "handelsbenaming": "Z2 SCOOTER"},
    {"kenteken": "AA04", "voertuigsoort": "Motorfiets", "merk": "SPARK", "handelsbenaming": "SPARK Q9 PRO"},
    {"kenteken": "AA05", "voertuigsoort": "Oplegger", "merk": "ACME", "handelsbenaming": "TRAILER"},  # not a kind read
    {"kenteken": "AA06", "voertuigsoort": "Personenauto", "merk": "ZED", "handelsbenaming": "Z3"},
    {"kenteken": "BB01", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "VOLT 8"},  # not electric
]

LIST = {"version": 1,
        "reasons": {"refuses": "refuses", "none": "no versions", "other": "the page does not name the model",
                    "unsearched": "not looked for"},
        "rdw": {"kinds": {"Personenauto": "car", "Bromfiets": "moped", "Motorfiets": "motorcycle"}},
        "brands": {
            "Acme": {"fueleconomy": ["ACME MOTORS"], "publisher": {"key": "acme", "name": "Acme"},
                     "products": [{"name": "Volt 3", "versions": "https://acme.example/updates", "models": ["Volt 3"]},
                                  {"name": "Volt 3 N", "versions": "https://acme.example/updates", "models": ["Volt 3 N"]},
                                  {"name": "Volt 4", "versions": "https://acme.example/updates", "models": ["Volt 4"]}]},
            "Zed": {"publisher": {"key": "zed", "name": "Zed"},
                    "products": [{"name": "Zed OS", "series": True, "versions": "https://zed.example/os",
                                  "models": ["Z1", "Z2"]}]},
            "Old": {"publisher": {"key": "old", "name": "Old"},
                    "products": [{"name": "O1", "versions": "https://old.example/o1", "models": ["O1"]}]},
            "Closed": {"apart": "refuses"},
            "Nothing": {"apart": "none"}}}


class EvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "data"
        shutil.copytree(REPO / "schema", self.root / "schema")
        write(self.root, "publishers/old.yaml", "key: old\nname: Old\nsource: https://old.example/\naliases: []\n")
        write(self.root, "products/old/o1.yaml", "key: old/o1\npublisher: old\nname: O1\nkind: device\n"
                                                 "source: https://old.example/o1\nversions:\n  source: https://old.example/o1\naliases: []\n")
        self.oev = self.tmp / "oev.json"
        self.oev.write_text(json.dumps(OEV))
        self.fe = self.tmp / "fe.xlsx"
        xlsx(self.fe, FE)
        self.rdw = self.tmp / "rdw"
        self.rdw.mkdir()
        (self.rdw / "fuel.jsonl").write_text("".join(json.dumps({"kenteken": k}) + "\n" for k in RDW_FUEL))
        (self.rdw / "vehicles.jsonl").write_text("".join(json.dumps(v) + "\n" for v in RDW_VEHICLES))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_round(self, **kw):
        got = ev.run(self.oev, [self.fe], self.tmp / "out", self.root, data=LIST, **kw)
        return got, {(m.brand, m.name): m for m in got["models"]}

    def test_the_sheet_is_read_without_its_title_rows(self):
        rows = ev.read_fe(self.fe)
        self.assertEqual([r.model for r in rows], ["Volt 3 AWD (20in Wheels)", "Volt 3 N", "V7 RWD", "N2 AWD", "Q1"])

    def test_every_model_has_one_outcome_with_a_reason(self):
        got, by = self.run_round()
        for m in got["models"]:
            self.assertIn(m.outcome, ("taken-in", "in-tax", "apart"), m.name)
            self.assertTrue(m.outcome == "taken-in" or m.reason, m.name)
        self.assertEqual([r.brand for r in got["unknown"]], ["Stranger"])

    def test_a_trim_is_a_variant_of_its_model(self):
        _, by = self.run_round()
        self.assertEqual((by[("Acme", "Volt 3")].product, by[("Acme", "Volt 3")].rows), ("acme/volt-3", 3),
                         "two trims of Open EV Data and a carline of the FE Guide are one model (TP3)")
        self.assertEqual(by[("Acme", "Volt 3")].sources, {"open-ev-data", "fueleconomy"})
        self.assertEqual(by[("Acme", "Volt 3 N")].product, "acme/volt-3-n", "the page names it: a model of its own")

    def test_the_outcomes(self):
        _, by = self.run_round()
        outcome = {k: (m.outcome, m.reason) for k, m in by.items()}
        self.assertEqual(outcome[("Acme", "Volt 4")], ("taken-in", ""))
        self.assertEqual(outcome[("Acme", "Volt 9")], ("apart", "the page does not name the model"))
        self.assertEqual(outcome[("Acme", "V7")], ("apart", "the page does not name the model"),
                         "a carline without a model: its first word")
        self.assertEqual(outcome[("Closed", "C1")], ("apart", "refuses"))
        self.assertEqual(outcome[("Nothing", "N1")], ("apart", "no versions"))
        self.assertEqual(outcome[("Nothing", "N2")], ("apart", "no versions"))
        self.assertEqual(by[("Old", "O1")].outcome, "in-tax")

    def test_one_series_for_several_models_is_one_product_with_aliases(self):
        got, by = self.run_round()
        self.assertEqual({by[("Zed", "Z1")].product, by[("Zed", "Z2")].product}, {"zed/os"})
        os = yaml.safe_load(got["products"]["zed/os"].text())
        self.assertEqual((os["name"], os["kind"], os["source"], os["versions"]["source"]),
                         ("Zed OS", "device", "https://zed.example/os", "https://zed.example/os"))
        self.assertEqual(os["aliases"], ["zed/zed-os", "zed/z1", "zed/z2"], "the long form, then the models")
        volt = yaml.safe_load(got["products"]["acme/volt-3"].text())
        self.assertEqual(volt["aliases"], [], "a model of its own has no models as aliases")

    def test_new_publishers_only_for_products_taken_in(self):
        got, _ = self.run_round()
        self.assertEqual(set(got["publishers"]), {"acme", "zed"})
        pub = yaml.safe_load(ev.publisher_text("zed", got["publishers"]["zed"]))
        self.assertEqual((pub["name"], pub["source"]), ("Zed", "https://zed.example/os"))

    def test_what_is_written_passes_the_lint(self):
        self.run_round(write=True)
        self.assertTrue((self.root / "products/zed/os.yaml").is_file())
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    def test_the_apart_list(self):
        self.run_round()
        lines = (self.tmp / "out/apart.csv").read_text().splitlines()
        self.assertEqual(lines[0], "brand,model,kinds,sources,outcome,reason,product")
        self.assertIn("Closed,C1,car,open-ev-data,apart,refuses,", lines)
        self.assertFalse(any(line.startswith("Zed,") for line in lines))

    def test_the_list_of_the_repository(self):
        data = ev.load_list()
        self.assertEqual(len(data["brands"]), 72, "the 71 brands of TAX-59 and Zero Motorcycles of the RDW prompt")
        counts = {r: sum(1 for b in data["brands"].values() if b.get("apart") == r) for r in data["reasons"]}
        self.assertEqual(counts, {"refuses": 16, "none": 41, "other": 1, "unsearched": 0})
        self.assertIn("Personenauto", data["rdw"]["kinds"])
        for brand, entry in data["brands"].items():
            for p in entry.get("products") or []:
                self.assertTrue(p["versions"].startswith("https://"), brand)
                self.assertTrue(p["models"], brand)

    def test_the_rdw_gives_one_row_per_brand_trade_name_and_kind(self):
        rows = ev.read_rdw(self.rdw, LIST["rdw"]["kinds"])
        self.assertEqual([(r.brand, r.model, r.kind) for r in rows],
                         [("ACME", "VOLT3 LONG RANGE", "car"), ("ZED", "Z2 SCOOTER", "moped"),
                          ("SPARK", "SPARK Q9 PRO", "motorcycle"), ("ZED", "Z3", "car")])

    def test_the_rdw_leaves_out_old_vehicles_and_rare_trade_names(self):
        vehicles = [{"kenteken": f"CC{i:02}", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "VOLT 4",
                     "datum_eerste_toelating": "20240101"} for i in range(3)]
        vehicles += [{"kenteken": "CC90", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "VOLT 4",
                      "datum_eerste_toelating": "19720301"},  # converted: its first date stays
                     {"kenteken": "CC91", "voertuigsoort": "Personenauto", "merk": "ACME", "handelsbenaming": "ONE OFF",
                      "datum_eerste_toelating": "20240101"}]
        (self.rdw / "fuel.jsonl").write_text("".join(json.dumps({"kenteken": v["kenteken"]}) + "\n" for v in vehicles))
        (self.rdw / "vehicles.jsonl").write_text("".join(json.dumps(v) + "\n" for v in vehicles))
        dropped = ev.Counter()
        rows = ev.read_rdw(self.rdw, {"Personenauto": "car"}, since=2010, at_least=2, dropped=dropped)
        self.assertEqual([(r.brand, r.model) for r in rows], [("ACME", "VOLT 4")])
        self.assertEqual(dropped, {"vehicles admitted before 2010": 1, "vehicles of a trade name with fewer than 2": 1,
                                   "trade names with fewer than 2 vehicles": 1})

    def test_the_rdw_in_the_round(self):
        got = ev.run(self.oev, [self.fe], self.tmp / "out", self.root, data=LIST, rdw=self.rdw)
        by = {(m.brand, m.name): m for m in got["models"]}
        self.assertEqual(by[("Acme", "Volt 3")].sources, {"open-ev-data", "fueleconomy", "rdw"},
                         "VOLT3 is Volt 3: letters and digits apart, the brand in capitals")
        self.assertEqual((by[("Zed", "Z2")].product, by[("Zed", "Z2")].kinds), ("zed/os", {"car", "moped"}))
        self.assertEqual((by[("Zed", "Z3")].outcome, by[("Zed", "Z3")].reason), ("apart", "the page does not name the model"))
        spark = by[("SPARK", "Q9")]
        self.assertEqual((spark.outcome, spark.reason, spark.kinds), ("apart", "not looked for", {"motorcycle"}),
                         "a brand outside the list: its first word after the brand")
        self.assertIn("| motorcycle | 1 | 0 | 0 | 1 | 1 |", got["report"])

    def test_a_series_in_tax_gets_a_model_of_the_rdw_as_alias(self):
        write(self.root, "publishers/zed.yaml", "key: zed\nname: Zed\nsource: https://zed.example/os\naliases: []\n")
        write(self.root, "products/zed/os.yaml", "key: zed/os\npublisher: zed\nname: Zed OS\nkind: device\n"
                                                 "source: https://zed.example/os\nversions:\n  source: https://zed.example/os\n"
                                                 "aliases:\n- zed/zed-os\n- zed/z1\n- zed/z2\n")
        data = {**LIST, "brands": {**LIST["brands"], "Zed": {**LIST["brands"]["Zed"]}}}
        data["brands"]["Zed"]["products"] = [{**LIST["brands"]["Zed"]["products"][0], "models": ["Z1", "Z2", "Z3"]}]
        got = ev.run(self.oev, [self.fe], self.tmp / "out", self.root, data=data, rdw=self.rdw, write=True)
        by = {(m.brand, m.name): m for m in got["models"]}
        self.assertEqual((by[("Zed", "Z1")].outcome, by[("Zed", "Z3")].outcome), ("in-tax", "taken-in"))
        os = yaml.safe_load((self.root / "products/zed/os.yaml").read_text())
        self.assertEqual(os["aliases"], ["zed/zed-os", "zed/z1", "zed/z2", "zed/z3"], "the file in TAX keeps what it had")
        self.assertIn("1 products in TAX with a model more, 1 new aliases", got["report"])
        self.assertEqual([f for f in lint.lint(self.root) if f.level == "error"], [])

    def test_a_row_of_the_rdw_is_electric_without_saying_so(self):
        data = {**LIST, "rdw": {**LIST["rdw"], "implied": ["Electric"]}}
        data["brands"] = {**LIST["brands"], "Acme": {**LIST["brands"]["Acme"]}}
        data["brands"]["Acme"]["products"] = [*LIST["brands"]["Acme"]["products"],
                                              {"name": "Volt 8 Electric", "versions": "https://acme.example/updates",
                                               "models": ["Volt 8 Electric"]}]
        (self.rdw / "fuel.jsonl").write_text(json.dumps({"kenteken": "BB01"}) + "\n")
        got = ev.run(self.oev, [self.fe], self.tmp / "out", self.root, data=data, rdw=self.rdw)
        by = {(m.brand, m.name): m for m in got["models"]}
        self.assertEqual(by[("Acme", "Volt 8 Electric")].product, "acme/volt-8-electric", "VOLT 8 of the RDW")


if __name__ == "__main__":
    unittest.main()
