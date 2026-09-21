"""Tests for the auto-refresh scheduling logic.

Run with:  python -m unittest test_scheduler -v
"""

import unittest
from datetime import datetime, timedelta

import app
import scrapers


HOUR = timedelta(hours=1)
MIN_GAP = timedelta(minutes=5)
T0 = datetime(2026, 9, 21, 9, 0, 0)


class TestNextRunAt(unittest.TestCase):
    def test_fast_scan_paces_from_the_start(self):
        # 4-minute scan on an hourly interval -> next run one hour after it
        # began, not one hour after it finished.
        nxt = app._next_run_at(T0, T0 + timedelta(minutes=4), HOUR, MIN_GAP)
        self.assertEqual(nxt, T0 + HOUR)

    def test_slow_scan_does_not_push_the_schedule_out(self):
        # The old behaviour paced from the end: a 4-hour scan left the data
        # 4 hours stale and then waited another full hour. Now it refreshes
        # as soon as the minimum gap allows.
        finished = T0 + timedelta(hours=4)
        nxt = app._next_run_at(T0, finished, HOUR, MIN_GAP)
        self.assertEqual(nxt, finished + MIN_GAP)
        self.assertLess(nxt, finished + HOUR)

    def test_minimum_gap_prevents_a_tight_retry_loop(self):
        # A scan that fails instantly must not re-run immediately.
        finished = T0 + timedelta(seconds=1)
        nxt = app._next_run_at(T0, finished, timedelta(seconds=0), MIN_GAP)
        self.assertEqual(nxt, finished + MIN_GAP)

    def test_next_run_is_always_in_the_future(self):
        for minutes in (0, 1, 4, 59, 60, 61, 240, 600):
            with self.subTest(scan_minutes=minutes):
                finished = T0 + timedelta(minutes=minutes)
                self.assertGreater(
                    app._next_run_at(T0, finished, HOUR, MIN_GAP), finished
                )


class TestScanBudget(unittest.TestCase):
    """The budget is what stops a scan running for hours."""

    def test_deadline_in_the_past_skips_all_queries(self):
        # No network: an already-expired deadline must short-circuit before
        # the first request.
        jobs = scrapers.scrape_all(deadline=0.0)
        self.assertEqual(jobs, [])

    def test_enrichment_stops_at_the_deadline(self):
        jobs = [{"id": "a", "url": "https://example.com/1"},
                {"id": "b", "url": "https://example.com/2"}]
        out = scrapers.enrich_descriptions(jobs, max_fetch=50, deadline=0.0)
        # Nothing fetched, and the jobs come back untouched rather than lost.
        self.assertEqual(len(out), 2)
        self.assertNotIn("description", out[0])

    def test_no_deadline_means_unbounded(self):
        # enrich_descriptions with nothing to do returns immediately even
        # without a deadline — guards the `deadline is None` branch.
        jobs = [{"id": "a", "description": "already here"}]
        out = scrapers.enrich_descriptions(jobs, max_fetch=50)
        self.assertEqual(out[0]["description"], "already here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
