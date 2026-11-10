#!/usr/bin/env python3
"""What a fact-check verdict rests on, counted from the fact-check record.

The review pages show this line under each verdict. The cases here are the
ways the count can mislead a reviewer: pages of one site read as independent
sources, an archived copy read as a second source, and a claim nobody checked
read as one that was checked and found unsupported.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from spotlight_orchestration.claim_basis import claim_basis  # noqa: E402


def claim(verdict: str, supporting: tuple[str, ...] = (), against: tuple[str, ...] = ()) -> dict:
    return {"verdict": verdict,
            "evidence_for": [{"source": source, "description": "x"} for source in supporting],
            "evidence_against": [{"source": source, "description": "x"} for source in against]}


class ClaimBasis(unittest.TestCase):
    def kind(self, *args, **kwargs) -> str:
        return claim_basis(claim(*args, **kwargs))["kind"]

    def test_pages_of_one_site_and_its_archived_copy_are_one_source(self) -> None:
        basis = claim_basis(claim("verified", (
            "https://www.torproject.org/about/people/",
            "https://blog.torproject.org/new-board-members/",
            "https://web.archive.org/web/20260901120000/https://www.torproject.org/about/people/",
        )))
        self.assertEqual((basis["kind"], basis["source_families"]), ("single_source", ["torproject.org"]))
        self.assertIn("one source (torproject.org)", basis["label"])

    def test_sites_under_a_country_second_level_are_told_apart(self) -> None:
        basis = claim_basis(claim("verified", ("https://www.bbc.co.uk/news/1", "https://www.theguardian.co.uk/a")))
        self.assertEqual((basis["kind"], basis["source_families"]), ("independent_sources", ["bbc.co.uk", "theguardian.co.uk"]))
        self.assertIn("2 independent sources", basis["label"])

    def test_a_government_host_carries_a_verdict_on_its_own(self) -> None:
        for url in ("https://find-and-update.company-information.service.gov.uk/company/01232804",
                    "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/3-01102026-ap",
                    "https://www.sec.gov/enforce/public-alerts"):
            self.assertEqual(self.kind("verified", (url,)), "government_source", url)
        # A name that only contains "gov" is not a government host.
        self.assertEqual(self.kind("verified", ("https://govtrack.us/congress/bills",)), "single_source")

    def test_a_verified_claim_without_a_source_url_says_so(self) -> None:
        self.assertEqual(self.kind("verified", ("research/primary-record.txt",)), "no_source")

    def test_a_claim_that_is_not_verified_says_whether_it_was_checked(self) -> None:
        self.assertEqual(self.kind("unverified"), "not_checked")
        self.assertEqual(claim_basis(None)["kind"], "not_checked")
        self.assertEqual(self.kind("unverified", ("https://example.org/a",)), "unconfirmed")
        self.assertEqual(self.kind("false", (), ("https://example.org/a",)), "evidence_against")
        self.assertEqual(self.kind("disputed", ("https://example.org/a",), ("https://example.net/b",)), "evidence_against")

    def test_only_a_long_claim_carries_the_bundling_note(self) -> None:
        self.assertEqual(claim_basis(claim("verified"), " ".join(["word"] * 39))["note"], "")
        self.assertIn("40 words", claim_basis(claim("verified"), " ".join(["word"] * 40))["note"])


if __name__ == "__main__":
    unittest.main()
