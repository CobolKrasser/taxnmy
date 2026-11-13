"""Step 0: the readers of sources made for machines (tools/machine.py)."""
import gzip
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import machine  # noqa: E402

CATALOG = """<?xml version="1.0" encoding="utf-16"?>
<Manifest baseLocation="downloads.example.com" dateTime="2026-11-06T21:43:08+05:30">
  <SoftwareComponent vendorVersion="1.23.1" releaseDate="September 16, 2026">
    <Name><Display lang="en">Server BIOS Version 1.23.1</Display></Name>
    <ComponentType value="BIOS"><Display lang="en">BIOS</Display></ComponentType>
    <SupportedSystems><Brand><Display lang="en">PowerEdge</Display>
      <Model><Display lang="en">R650</Display></Model><Model><Display lang="en">R750</Display></Model>
    </Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent vendorVersion="7.10" releaseDate="May 1, 2026">
    <Name><Display lang="en">iDRAC firmware 7.10</Display></Name>
    <ComponentType value="FRMW"><Display lang="en">Firmware</Display></ComponentType>
    <SupportedSystems><Brand><Display lang="en">PowerEdge</Display><Model><Display lang="en">R650</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent vendorVersion="2.0" releaseDate="May 2, 2026">
    <Name><Display lang="en">Only for the R6500</Display></Name>
    <ComponentType value="BIOS"><Display lang="en">BIOS</Display></ComponentType>
    <SupportedSystems><Brand><Display lang="en">PowerEdge</Display><Model><Display lang="en">R6500</Display></Model></Brand></SupportedSystems>
  </SoftwareComponent>
</Manifest>
""".encode("utf-16")

APPSTREAM = b"""<components origin="lvfs">
  <component type="firmware"><name>Thunderbolt Dock WD19</name><summary>Firmware for the dock</summary>
    <developer_name>Dell</developer_name>
    <releases><release version="01.00.15" date="2026-03-01"/><release version="01.00.14" date="2025-10-01"/></releases>
  </component>
  <component type="firmware"><name>Other device</name><summary>x</summary></component>
</components>"""

CSAF = {
    "document": {"csaf_version": "2.0", "title": "Security update for Example OS", "category": "csaf_security_advisory",
                 "publisher": {"name": "Example Product Security", "namespace": "https://example.com", "category": "vendor"},
                 "tracking": {"id": "EX-2026-001", "initial_release_date": "2026-10-01T00:00:00Z"}},
    "product_tree": {"branches": [{"category": "vendor", "name": "Example", "branches": [
        {"category": "product_name", "name": "Example OS", "branches": [
            {"category": "product_version", "name": "9.4", "product": {"product_id": "P1", "name": "Example OS 9.4"}},
            {"category": "product_version", "name": "9.5", "product": {"product_id": "P2", "name": "Example OS 9.5"}}]},
        {"category": "product_name", "name": "Example Cloud", "product": {"product_id": "P3", "name": "Example Cloud"}}]}]},
    "vulnerabilities": [{"cve": "CVE-2026-1234", "product_status": {"known_affected": ["P1", "P3"], "fixed": ["P2"]}}],
}

PROVIDER = {"canonical_url": "https://example.com/.well-known/csaf/provider-metadata.json", "metadata_version": "2.0",
            "role": "csaf_trusted_provider", "publisher": {"name": "Example Product Security", "namespace": "https://example.com"},
            "distributions": [{"directory_url": "https://example.com/csaf/"}]}

NVD = {"format": "NVD_CPE", "totalResults": 88, "products": [
    {"cpe": {"cpeName": "cpe:2.3:a:oracle:graalvm:19.0.0:*:*:*:enterprise:*:*:*", "deprecated": False,
             "titles": [{"title": "Oracle GraalVM 19.0.0 Enterprise Edition", "lang": "en"}]}}]}


class ReaderTest(unittest.TestCase):
    def test_update_catalog(self):
        text = machine.read(gzip.compress(CATALOG), "application/x-gzip", "PowerEdge R650")
        lines = text.splitlines()
        self.assertIn("2 components for PowerEdge R650", lines[0])
        self.assertEqual(lines[1], "Server BIOS Version 1.23.1 | version 1.23.1 | released September 16, 2026 | type BIOS")
        self.assertIn("iDRAC firmware 7.10", lines[2])
        self.assertNotIn("R6500", text, "a model is matched as a whole, not as a prefix")

    def test_update_catalog_without_gzip(self):
        self.assertIn("2 components", machine.read(CATALOG, "text/xml", "PowerEdge R650"))

    def test_appstream(self):
        text = machine.read(APPSTREAM, "application/xml", "WD19")
        self.assertIn("Thunderbolt Dock WD19 | developer Dell", text)
        self.assertIn("01.00.15 (2026-03-01)", text)
        self.assertNotIn("Other device", text)

    def test_csaf(self):
        text = machine.read(json.dumps(CSAF).encode(), "application/json", "Example OS")
        self.assertIn("publisher: Example Product Security (https://example.com)", text)
        self.assertIn("product: Example OS 9.4", text)
        self.assertIn("CVE-2026-1234: fixed for Example OS 9.5", text)
        self.assertIn("CVE-2026-1234: known_affected for Example OS 9.4", text)
        self.assertNotIn("Example Cloud", text)

    def test_csaf_provider(self):
        text = machine.read(json.dumps(PROVIDER).encode(), "application/json", "anything")
        self.assertIn("advisories: https://example.com/csaf/", text)

    def test_nvd(self):
        text = machine.read(json.dumps(NVD).encode(), "application/json", "GraalVM")
        self.assertIn("88 matching records", text)
        self.assertIn("cpe:2.3:a:oracle:graalvm:19.0.0:*:*:*:enterprise:*:*:* | Oracle GraalVM 19.0.0 Enterprise Edition", text)

    def test_other_content_is_not_a_machine_source(self):
        self.assertIsNone(machine.read(b"<html><body>x</body></html>", "text/html", "x"))
        self.assertIsNone(machine.read(b'{"a": 1}', "application/json", "x"))
        self.assertIsNone(machine.read(b"<rss></rss>", "application/xml", "x"))
        self.assertIsNone(machine.read(b"\x1f\x8bbroken", "application/gzip", "x"))

    def test_matching(self):
        self.assertTrue(machine.matches("PowerEdge R650", "Dell PowerEdge R650 server"))
        self.assertFalse(machine.matches("PowerEdge R650", "PowerEdge R6500"))
        self.assertFalse(machine.matches("", "anything"))


if __name__ == "__main__":
    unittest.main()
