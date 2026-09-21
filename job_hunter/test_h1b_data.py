"""Tests for H-1B employer matching.

Run with:  python -m unittest test_h1b_data -v

These never touch the network — the index is built from small fixtures so the
matching rules are tested in isolation from whatever USCIS published.
"""

import unittest

import filters
from h1b_data import (
    EmployerRecord,
    H1BIndex,
    _parse_uscis_csv,
    name_variants,
    normalize_company,
    normalize_phrase,
)


def make_index(**employers) -> H1BIndex:
    """Build an index from {normalized_name: approvals} pairs."""
    records = {
        name: EmployerRecord(name=name, initial_approval=count)
        for name, count in employers.items()
    }
    return H1BIndex(records, fiscal_year="2023")


class TestNormalization(unittest.TestCase):
    def test_strips_legal_suffixes(self):
        self.assertEqual(normalize_company("Google LLC"), "google")
        self.assertEqual(normalize_company("SeatGeek, Inc."), "seatgeek")
        self.assertEqual(normalize_company("Stripe Inc"), "stripe")

    def test_strips_stacked_and_geographic_suffixes(self):
        self.assertEqual(normalize_company("Ericsson Inc. USA"), "ericsson")
        self.assertEqual(normalize_company("Infosys Limited"), "infosys")

    def test_expands_ampersand(self):
        self.assertEqual(
            normalize_company("Johnson & Johnson, Inc."), "johnson and johnson"
        )

    def test_drops_leading_article(self):
        self.assertEqual(normalize_company("The Boeing Company"), "boeing")

    def test_never_strips_name_to_empty(self):
        # "Co" is a legal suffix but it's all there is — keep it.
        self.assertEqual(normalize_company("Co"), "co")

    def test_phrase_normalization_keeps_suffixes(self):
        # This is why blocklist entries use normalize_phrase: stripping
        # "Corporation" here would leave the far-too-broad token "aerospace".
        self.assertEqual(
            normalize_phrase("The Aerospace Corporation"), "aerospace corporation"
        )
        self.assertEqual(normalize_company("The Aerospace Corporation"), "aerospace")


class TestNameVariants(unittest.TestCase):
    def test_indexes_both_sides_of_dba(self):
        variants = name_variants("0965688 BC LTD DBA PROCOGIA")
        self.assertIn("procogia", variants)

    def test_indexes_parenthetical(self):
        self.assertIn("aws", name_variants("Amazon Web Services (AWS)"))


class TestSubstringRegression(unittest.TestCase):
    """The bug this module was rewritten to fix.

    Bidirectional substring matching against short sponsor names flagged a
    third of the job database as verified sponsors: "ge" matched SeatGeek,
    "ey" matched Valley Bank, "ea" matched Health Research.
    """

    def setUp(self):
        self.index = make_index(**{
            "general electric": 500,
            "ernst and young": 500,
            "electronic arts": 500,
            "gusto": 500,
            "x corp": 500,
        })

    def test_short_sponsor_name_does_not_match_inside_other_names(self):
        for company in ("SeatGeek", "Valley Bank", "Genalyte", "EarthCam",
                        "Bertram Capital Management", "Athena Health"):
            with self.subTest(company=company):
                self.assertFalse(self.index.lookup(company).is_sponsor)

    def test_company_name_inside_a_sponsor_name_does_not_match(self):
        # The old code also matched in reverse: "UST" inside "gusto".
        self.assertFalse(self.index.lookup("UST").is_sponsor)

    def test_real_match_still_works(self):
        self.assertTrue(self.index.lookup("General Electric").is_sponsor)


class TestMatching(unittest.TestCase):
    def test_exact_match_reports_counts(self):
        index = make_index(google=2460)
        match = index.lookup("Google LLC")
        self.assertTrue(match.is_sponsor)
        self.assertEqual(match.match, "exact")
        self.assertEqual(match.approvals, 2460)
        self.assertEqual(match.fiscal_year, "2023")
        self.assertEqual(match.confidence, "high")

    def test_prefix_match_aggregates_corporate_family(self):
        index = make_index(**{
            "amazon com services": 1000,
            "amazon web services": 954,
        })
        match = index.lookup("Amazon")
        self.assertTrue(match.is_sponsor)
        self.assertEqual(match.match, "prefix")
        self.assertEqual(match.approvals, 1954)
        self.assertEqual(match.confidence, "medium")

    def test_prefix_match_needs_meaningful_volume(self):
        # One stray petition by a similarly-named small company is not evidence.
        index = make_index(**{"beacon academy": 1})
        self.assertFalse(index.lookup("Beacon").is_sponsor)

    def test_generic_single_token_never_prefix_matches(self):
        index = make_index(**{"general motors": 900})
        self.assertFalse(index.lookup("General").is_sponsor)
        self.assertTrue(index.lookup("General Motors").is_sponsor)

    def test_zero_approval_employer_is_not_a_sponsor(self):
        # Present in USCIS only as denials.
        index = H1BIndex(
            {"acme": EmployerRecord(name="acme", initial_denial=4)},
            fiscal_year="2023",
        )
        self.assertFalse(index.lookup("Acme").is_sponsor)

    def test_unknown_company_is_not_a_sponsor(self):
        match = make_index(google=2460).lookup("Bob's Plumbing LLC")
        self.assertFalse(match.is_sponsor)
        self.assertEqual(match.match, "none")

    def test_empty_name(self):
        self.assertFalse(make_index(google=1).lookup("").is_sponsor)
        self.assertFalse(make_index(google=1).lookup("   ").is_sponsor)


class TestCitizenshipBlocklist(unittest.TestCase):
    def test_blocks_national_labs_even_with_uscis_records(self):
        index = make_index(**{"argonne national laboratory": 40})
        match = index.lookup("Argonne National Laboratory")
        self.assertFalse(match.is_sponsor)
        self.assertEqual(match.match, "citizenship-required")

    def test_blocklist_does_not_over_match(self):
        # Regression: "The Aerospace Corporation" once normalized to
        # "aerospace" and blocked Collins Aerospace, which does sponsor.
        index = make_index(**{"collins aerospace": 87})
        self.assertTrue(index.lookup("Collins Aerospace").is_sponsor)
        self.assertFalse(index.lookup("The Aerospace Corporation").is_sponsor)

    def test_matches_on_token_boundary_within_longer_name(self):
        index = make_index()
        self.assertEqual(
            index.lookup("Naval Nuclear Laboratory (FMP)").match,
            "citizenship-required",
        )


class TestAliases(unittest.TestCase):
    def test_alias_resolves_brand_to_registered_name(self):
        index = make_index(**{"meta platforms": 59})
        match = index.lookup("Facebook")
        self.assertTrue(match.is_sponsor)
        self.assertEqual(match.match, "alias")

    def test_alias_recovers_uscis_spellings(self):
        # USCIS writes "WAL MART ASSOCIATES" and "TATA CONSULTANCY SVCS";
        # without these aliases two of the largest sponsors look absent.
        index = make_index(**{
            "wal mart associates": 1327,
            "tata consultancy svcs": 3777,
        })
        for name in ("Walmart", "Tata Consultancy Services", "TCS"):
            with self.subTest(name=name):
                self.assertTrue(index.lookup(name).is_sponsor)


class TestCuratedFallback(unittest.TestCase):
    """The curated list must not override real USCIS data.

    It previously claimed SpaceX, Lockheed Martin and Northrop Grumman as
    sponsors; USCIS FY2023 records no H-1B approvals for any of them.
    """

    def test_curated_list_is_ignored_when_uscis_data_is_loaded(self):
        index = make_index(google=2460)  # a non-empty USCIS index
        match = index.lookup("Lockheed Martin")
        self.assertFalse(match.is_sponsor)
        self.assertEqual(match.match, "none")

    def test_curated_list_applies_only_as_an_offline_fallback(self):
        offline = H1BIndex({}, fiscal_year=None)  # download failed
        match = offline.lookup("Google")
        self.assertTrue(match.is_sponsor)
        self.assertEqual(match.match, "curated")
        self.assertEqual(match.confidence, "low")
        self.assertEqual(match.approvals, 0)

    def test_itar_employer_is_blocked_not_merely_absent(self):
        self.assertEqual(
            make_index(google=1).lookup("SpaceX").match, "citizenship-required"
        )


class TestCsvParsing(unittest.TestCase):
    CSV = (
        '"Fiscal Year",Employer,"Initial Approval","Initial Denial",'
        '"Continuing Approval","Continuing Denial",NAICS,"Tax ID",State,City,ZIP\n'
        '2023,"GOOGLE LLC",10,1,5,0,51,1234,CA,MOUNTAIN VIEW,94043\n'
        '2023,"GOOGLE LLC",7,0,3,1,51,1234,NY,NEW YORK,10011\n'
        '2023,,1,0,0,0,51,8070,DE,WILMINGTON,19801\n'
    )

    def test_aggregates_rows_for_the_same_employer(self):
        # USCIS emits one row per employer per city; counts must be summed or
        # Google looks like it sponsored one person.
        employers, fiscal_year = _parse_uscis_csv(self.CSV)
        self.assertEqual(fiscal_year, "2023")
        google = employers["google"]
        self.assertEqual(google.approvals, 25)  # 10+5+7+3
        self.assertEqual(google.denials, 2)

    def test_skips_blank_employer_names(self):
        employers, _ = _parse_uscis_csv(self.CSV)
        self.assertNotIn("", employers)


class TestNegativeSignals(unittest.TestCase):
    """EEO boilerplate must not read as a refusal to sponsor."""

    def test_eeo_boilerplate_is_not_a_refusal(self):
        for text in (
            "we are an equal opportunity employer and consider all qualified "
            "applicants without regard to race, religion, or citizenship status",
            "all qualified applicants will receive consideration regardless of "
            "citizenship status or national origin",
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNone(filters.find_negative_signal(text))

    def test_genuine_refusals_are_caught(self):
        for text in (
            "we are unable to sponsor visas for this position",
            "applicants must be a u.s. citizen",
            "this role requires us citizenship",
            "u.s. citizens only",
            "an active security clearance is required",
            "candidates must be authorized to work in the us without "
            "sponsorship now or in the future",
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNotNone(filters.find_negative_signal(text))

    def test_mentioning_sponsorship_positively_is_not_a_refusal(self):
        self.assertIsNone(
            filters.find_negative_signal(
                "visa sponsorship is available for this role"
            )
        )


class TestCheckSponsorship(unittest.TestCase):
    def test_citizenship_employer_beats_positive_posting_language(self):
        job = filters.check_sponsorship({
            "company": "Argonne National Laboratory",
            "title": "Data Scientist",
            "description": "visa sponsorship available",
        })
        self.assertEqual(job["sponsorship_status"], "unlikely")
        self.assertFalse(job["is_h1b_sponsor"])

    def test_refusal_beats_verified_sponsor(self):
        job = filters.check_sponsorship({
            "company": "Google",
            "title": "Data Scientist",
            "description": "we are unable to sponsor visas for this role",
        })
        self.assertEqual(job["sponsorship_status"], "unlikely")

    def test_unknown_company_with_no_signal(self):
        job = filters.check_sponsorship({
            "company": "Bob's Plumbing LLC",
            "title": "Data Scientist",
            "description": "great team",
        })
        self.assertEqual(job["sponsorship_status"], "unknown")
        self.assertEqual(job["h1b_match"], "none")

    def test_sets_evidence_fields(self):
        job = filters.check_sponsorship({
            "company": "Google", "title": "Data Scientist", "description": "",
        })
        for key in ("h1b_approvals", "h1b_match", "h1b_confidence",
                    "sponsorship_reason"):
            self.assertIn(key, job)


class TestCompanyGroups(unittest.TestCase):
    """Company grouping for the three dashboard pages."""

    def test_referral_beats_target(self):
        # Cognizant is one of the largest H-1B sponsors AND a referral.
        # The referral is the stronger signal, so it must not show as a target.
        self.assertEqual(filters.company_group("Cognizant"), "referral")

    def test_named_referrals(self):
        for name in ("EXL", "PwC", "Humana", "Cognizant", "Merck",
                     "Merck & Co., Inc.", "Synechron", "Ares Management"):
            with self.subTest(name=name):
                self.assertEqual(filters.company_group(name), "referral")

    def test_referral_keys_do_not_over_claim(self):
        # "ares management" is listed rather than "ares" so unrelated firms
        # starting with that token aren't swept onto the referral page.
        for name in ("Ares Capital Corp", "Aresty Institute"):
            with self.subTest(name=name):
                self.assertEqual(filters.company_group(name), "other")

    def test_named_targets(self):
        for name in ("Amazon", "Amazon Web Services (AWS)", "JPMorganChase",
                     "Goldman Sachs", "Google", "Microsoft"):
            with self.subTest(name=name):
                self.assertEqual(filters.company_group(name), "target")

    def test_unlisted_company_falls_to_other(self):
        for name in ("Bob's Plumbing LLC", "Nue.io", "BeaconFire Inc."):
            with self.subTest(name=name):
                self.assertEqual(filters.company_group(name), "other")

    def test_matches_on_token_boundaries_only(self):
        # Same discipline as sponsor matching: no bare substrings.
        for name in ("Amazonia Health", "Applelink Systems", "Metabolon",
                     "Blockchain Capital", "Googol Analytics"):
            with self.subTest(name=name):
                self.assertEqual(filters.company_group(name), "other")

    def test_legal_suffixes_do_not_break_matching(self):
        for name in ("Amazon.com Inc", "Google LLC", "Cognizant Technology "
                     "Solutions Corp"):
            with self.subTest(name=name):
                self.assertIn(filters.company_group(name), ("target", "referral"))

    def test_blank_company(self):
        self.assertEqual(filters.company_group(""), "other")
        self.assertEqual(filters.company_group("   "), "other")

    def test_groups_are_a_partition(self):
        # Every company lands in exactly one group, so the three pages never
        # show the same job twice.
        for name in ("Amazon", "Cognizant", "Bob's Plumbing LLC", ""):
            with self.subTest(name=name):
                self.assertIn(filters.company_group(name), filters.COMPANY_GROUPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
