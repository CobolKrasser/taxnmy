"""TG4 (TAX-46): the code in tools/ names no site; the classes of sites are data in referee/sites.yaml."""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import fetch  # noqa: E402
import gate  # noqa: E402
import guard  # noqa: E402
import sites  # noqa: E402


class SitesTest(unittest.TestCase):
    def test_no_site_of_the_file_is_in_the_code(self):
        data = sites.load()
        names = [n for key in ("ai_chats", "owner_in_path", "owner_in_host", "many_owners", "api_hosts", "datasets")
                 for n in data[key]]
        names += [re.sub(r"^https?://([^/]+).*$", r"\1", u) for us in data["finders"].values() for u in us if "://" in u]
        names += [re.sub(r"^https?://([^/]+).*$", r"\1", u) for u in (data["project_url"], *data["imports"].values(), *data["fillers"].values())]
        hits = [f"{p.name}: {n}" for p in sorted((REPO / "tools").glob("*.py")) for n in names
                if n in p.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])

    def test_the_code_reads_the_file(self):
        self.assertIn("claude.ai", gate.AI_CHAT_DOMAINS)
        self.assertIn("github.com", guard.OWNER_IN_PATH)
        self.assertIsNone(guard.owner("https://endoflife.date/x"), "a site with many owners is nobody's own")
        self.assertTrue(fetch.api_url("https://nvd.nist.gov/products/cpe/search/results?keyword=cpe:2.3:a:x:y")
                        .endswith("cpeMatchString=cpe:2.3:a:x:y&resultsPerPage=1"))
        self.assertIn(sites.value("project_url"), fetch.USER_AGENT)


if __name__ == "__main__":
    unittest.main()
