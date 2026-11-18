"""The count before the bulk (TAX-25): tools/census.py on small made-up dumps."""

import io
import json
import re
import sys
import tarfile
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import census  # noqa: E402


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def dataset(root: Path):
    """One publisher with one product, a CPE and a winget code; prices as in the referee."""
    write(root, "publishers/acme.yaml", "key: acme\nname: Acme Widgets\nsource: https://www.acme.example/about\naliases: []\n")
    write(root, "products/acme/rocket.yaml",
          "key: acme/rocket\npublisher: acme\nname: Rocket\nkind: software\nsource: https://acme.example/rocket\n"
          "versions:\n  source: https://acme.example/rocket/notes\nidentifiers:\n- cpe: cpe:2.3:a:acme:rocket\n"
          "  source: https://nvd.example\n- winget: Acme.Rocket\n  source: https://winget.example\naliases: []\n")
    (root / "referee").mkdir()
    (root / "referee/prices.yaml").write_text((REPO / "referee/prices.yaml").read_text())


def cve(cve_id, cna, product, published="2025-03-01", refs=(), cpes=(), vendor="Acme", state="PUBLISHED"):
    return {"cveMetadata": {"cveId": cve_id, "assignerShortName": cna, "state": state, "datePublished": published + "T00:00:00"},
            "containers": {"cna": {"affected": [{"vendor": vendor, "product": product, "cpes": list(cpes)}],
                                   "references": [{"url": u, "tags": list(t)} for u, t in refs]}}}


CNAS = [
    {"shortName": "acme", "organizationName": "Acme Widgets Inc.", "CNA": {"type": ["Vendor"]},
     "securityAdvisories": {"advisories": [{"url": "https://security.acme.example/advisories"}]}, "contact": []},
    {"shortName": "mixedco", "organizationName": "Mixed Co", "CNA": {"type": ["Open Source", "Vendor"]},
     "securityAdvisories": {"advisories": []}, "contact": []},
    {"shortName": "finder", "organizationName": "Finder", "CNA": {"type": ["Researcher"]},
     "securityAdvisories": {"advisories": []}, "contact": []},
]


class ShapeTest(unittest.TestCase):
    def test_versions_and_ranges(self):
        for name in ("Router 2.1.4", "Archer v1.2", "Suite 2019", "Server Service Pack 2", "Tool build 77"):
            self.assertEqual(census.shape(name), "version", name)
        for name in ("Multiple Products", "AK-SM 8xxA Series", "Router A, Router B", "Switch family"):
            self.assertEqual(census.shape(name), "range", name)
        for name in ("Rocket", "Office Suite", "Model 10"):
            self.assertEqual(census.shape(name), "", name)

    def test_fold_company(self):
        self.assertEqual(census.fold_company("Acme Widgets Inc."), census.fold_company("acme widgets"))


class CveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        dataset(self.tmp)
        self.tax = census.Tax(self.tmp)
        (self.tmp / "cnas.json").write_text(json.dumps(CNAS))
        records = [
            cve("CVE-2025-0001", "acme", "Rocket", refs=[("https://acme.example/rocket", ["product"])], cpes=["cpe:2.3:a:acme:rocket:*"]),
            cve("CVE-2025-0002", "acme", "rocket"),
            cve("CVE-2025-0003", "acme", "Launcher", refs=[("https://acme.example/launcher/notes", ["release-notes"]),
                                                          ("https://acme.example/launcher", ["product"])],
                cpes=["cpe:2.3:h:acme:launcher:*"]),
            cve("CVE-2025-0004", "acme", "n/a"),
            cve("CVE-2025-0005", "acme", "Old", published="2020-01-01"),
            cve("CVE-2025-0006", "acme", "Gone", state="REJECTED"),
            cve("CVE-2025-0007", "mixedco", "Thing", vendor="Mixed Co"),
            cve("CVE-2025-0008", "finder", "Other", vendor="Someone"),
        ]
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            for r in records:
                year = r["cveMetadata"]["cveId"].split("-")[1]
                z.writestr(f"cves/{year}/0xxx/{r['cveMetadata']['cveId']}.json", json.dumps(r))
        with zipfile.ZipFile(self.tmp / "dump.zip", "w") as z:   # the release: a zip in a zip
            z.writestr("cves.zip", inner.getvalue())

    def test_count(self):
        data = census.count_cve(self.tmp / "dump.zip", self.tmp / "cnas.json", date(2026, 11, 18), tax=self.tax)
        self.assertEqual(data["seen"]["records"], 6)          # the old and the rejected one fall out
        rows = {r["cna"]: r for r in data["rows"]}
        self.assertEqual(set(rows), {"acme", "mixedco"})
        acme = rows["acme"]
        self.assertEqual(acme["known"], "acme")
        self.assertEqual(acme["group"], "small")
        prods = {p["name"]: p for p in acme["products"]}
        self.assertEqual(set(prods), {"Rocket", "Launcher"})   # 'rocket' is the same name; n/a is no name
        self.assertEqual(prods["Rocket"]["cves"], 2)
        self.assertTrue(prods["Rocket"]["in_tax"])
        self.assertFalse(prods["Launcher"]["in_tax"])
        self.assertEqual((prods["Launcher"]["kind"], prods["Launcher"]["source"], prods["Launcher"]["versions"]), (True, True, True))
        self.assertTrue(rows["mixedco"]["out_of_scope"])      # mixed, and TAX does not know the publisher
        self.assertEqual(len(data["check"]), 3)               # records of vendor CNAs with a product name

        s = census.summarise_cve(json.loads(json.dumps(data, default=sorted)), census.prices(self.tmp))
        small = s["rounds"]["vendor small, all names"]
        self.assertEqual((small["publishers"], small["names"], small["candidates"]), (1, 2, 1))
        self.assertGreater(small["cost"]["claude-opus-5-5"], small["cost"]["claude-haiku-5-5"])


class WingetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        dataset(self.tmp)
        self.tax = census.Tax(self.tmp)
        files = {
            "manifests/a/Acme/Rocket/1.9.0/Acme.Rocket.yaml": "PackageIdentifier: Acme.Rocket\nPackageVersion: 1.9.0\nDefaultLocale: en-US\n",
            "manifests/a/Acme/Rocket/1.10.0/Acme.Rocket.yaml": "PackageIdentifier: Acme.Rocket\nPackageVersion: 1.10.0\nDefaultLocale: en-US\n",
            "manifests/a/Acme/Rocket/1.10.0/Acme.Rocket.locale.en-US.yaml":
                "PackageIdentifier: Acme.Rocket\nPublisher: Acme Widgets\nPackageName: Rocket\n"
                "PublisherUrl: https://acme.example\nPackageUrl: https://acme.example/rocket\nReleaseNotesUrl: https://acme.example/rocket/notes\n",
            "manifests/a/Acme/Rocket/1.10.0/Acme.Rocket.installer.yaml": "Installers: []\n",
            "manifests/z/Zed/Pad/2.0/Zed.Pad.yaml":
                "PackageIdentifier: Zed.Pad\nPackageVersion: '2.0'\nPublisher: Zed\nPackageName: Pad 2024\n"
                "PublisherUrl: https://zed.example\nPackageUrl: https://elsewhere.example/pad\nReleaseNotesUrl: https://zed.example/pad/2.0\n",
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo("winget-pkgs-master/" + rel)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        (self.tmp / "winget.tar.gz").write_bytes(buf.getvalue())

    def test_count(self):
        data = census.count_winget(self.tmp / "winget.tar.gz", tax=self.tax)
        rows = {r["id"]: r for r in data["rows"]}
        self.assertEqual(set(rows), {"Acme.Rocket", "Zed.Pad"})
        rocket, pad = rows["Acme.Rocket"], rows["Zed.Pad"]
        self.assertEqual((rocket["known"], rocket["in_tax"], rocket["source"], rocket["versions"]), ("acme", True, True, True))
        self.assertEqual((pad["known"], pad["in_tax"], pad["source"], pad["versions"]), (None, False, False, False))
        self.assertEqual(pad["shape"], "version")
        s = census.summarise_winget(data, census.prices(self.tmp))
        self.assertEqual((s["packages"], s["candidates"], s["publishers_known"]), (2, 1, 1))


class NoBrandTest(unittest.TestCase):
    """R3: the census names no publisher of the dataset."""

    def test_no_publisher_name_in_the_code(self):
        code = (REPO / "tools/census.py").read_text().lower()
        for f in (REPO / "publishers").glob("*.yaml"):
            p = yaml.safe_load(f.read_text())
            for n in {p["key"].replace("-", " "), p["name"]}:
                if len(n) >= 4:
                    self.assertIsNone(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", code), n)


if __name__ == "__main__":
    unittest.main()
