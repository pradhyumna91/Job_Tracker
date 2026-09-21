#!/usr/bin/env python3
"""
Job Hunter — Automated job search for F1 students.

Scrapes multiple job boards, filters for H1B-friendly companies,
and notifies you of new listings every hour.

Usage:
    python main.py              # Run once and show results
    python main.py --loop       # Run every hour continuously
    python main.py --dashboard  # Show current dashboard only
    python main.py --export     # Export all jobs to CSV
    python main.py --mark-applied <job_id>  # Mark a job as applied
    python main.py --backfill-descriptions  # Fetch descriptions for saved jobs missing one
"""

import argparse
import csv
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

from config import CHECK_INTERVAL_MINUTES, LOG_DIR, SCAN_TIME_BUDGET_MINUTES
from scrapers import scrape_all, fetch_description, _short_delay
from filters import filter_jobs, check_sponsorship
from db import (
    insert_job, log_scan, get_all_jobs, get_stats, mark_applied,
    get_jobs_missing_description, update_job_description,
)
from notifier import notify_new_jobs
from dashboard import show_stats, show_new_jobs, show_all_jobs, console
from rich.panel import Panel
from rich import box

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "job_hunter.log"),
    ],
)
logger = logging.getLogger("job_hunter")


def run_scan():
    """Execute a full scan: scrape -> filter -> store -> notify."""
    console.print(f"\n[bold cyan]{'='*60}[/]")
    console.print(f"[bold]Scan started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/]")
    console.print(f"[bold cyan]{'='*60}[/]\n")

    # Step 1: Scrape + enrich descriptions
    console.print("[bold]Step 1/3:[/] Scraping job boards + fetching descriptions...")
    console.print("  [dim]Sources: LinkedIn, Jobright, Wellfound, MyVisaJobs, RemoteOK, SimplifyJobs[/dim]")
    # Same wall-clock budget as the web scheduler, so a tarpitting host
    # can't stall the CLI scan for hours either.
    _scan_started = time.monotonic()
    raw_jobs = scrape_all(deadline=_scan_started + SCAN_TIME_BUDGET_MINUTES * 60)
    console.print(f"  Found [bold]{len(raw_jobs)}[/] unique listings\n")

    # Step 2: Filter (role + seniority + H1B sponsorship)
    console.print("[bold]Step 2/3:[/] Filtering for relevant roles & sponsorship...")
    console.print("  [dim]Checking against H1B employer database + description keywords[/dim]")
    filtered_jobs = filter_jobs(raw_jobs)
    console.print(f"  [bold]{len(filtered_jobs)}[/] jobs passed filters\n")

    # Step 3: Store, deduplicate & notify
    console.print("[bold]Step 3/3:[/] Saving to database & notifying...")
    new_count = 0
    new_jobs = []
    for job in filtered_jobs:
        is_new = insert_job(job)
        if is_new:
            new_count += 1
            new_jobs.append(job)

    log_scan("all", "all_queries", len(raw_jobs), new_count,
             duration_seconds=round(time.monotonic() - _scan_started))
    console.print(f"  [bold green]{new_count}[/] new jobs added (out of {len(filtered_jobs)} filtered)")

    if new_jobs:
        notify_new_jobs(new_jobs)
        console.print(f"  [bold green]Notified about {len(new_jobs)} new jobs![/]\n")
    else:
        console.print("  [dim]No new jobs since last scan.[/dim]\n")

    # Show dashboard
    show_new_jobs()

    console.print(f"\n[dim]Next scan in {CHECK_INTERVAL_MINUTES} minutes...[/dim]\n")
    return new_jobs


def export_csv():
    """Export all jobs to a CSV file."""
    jobs = get_all_jobs(limit=10000)
    if not jobs:
        console.print("[yellow]No jobs to export.[/]")
        return

    output_path = Path(__file__).parent / f"jobs_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    fieldnames = [
        "title", "company", "location", "source", "url",
        "sponsorship_status", "is_h1b_sponsor", "date_posted", "first_seen", "status", "applied",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(jobs)

    console.print(f"[bold green]Exported {len(jobs)} jobs to {output_path}[/]")


def backfill_descriptions():
    """Fetch descriptions for saved jobs that lack one, then re-check sponsorship.

    Scans only enrich a handful of jobs each run, so most saved jobs have no
    description — which hides OPT / start-date / clearance language.
    """
    jobs = get_jobs_missing_description()
    console.print(f"[bold]Fetching descriptions for {len(jobs)} jobs...[/]")
    fetched = newly_unlikely = 0
    for i, job in enumerate(jobs, 1):
        desc = fetch_description(job["url"])
        if desc:
            before = job["sponsorship_status"]
            job["description"] = desc
            job = check_sponsorship(job)
            update_job_description(job["id"], desc, job)
            fetched += 1
            if job["sponsorship_status"] == "unlikely" and before != "unlikely":
                newly_unlikely += 1
        if i % 25 == 0:
            logger.info(f"Backfill progress: {i}/{len(jobs)} checked, {fetched} fetched")
        _short_delay()
    console.print(
        f"[bold green]Fetched {fetched}/{len(jobs)} descriptions[/] — "
        f"{newly_unlikely} now flagged as not sponsoring"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Job Hunter — Automated F1-friendly job search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--loop", action="store_true",
        help=f"Run continuously, scanning every {CHECK_INTERVAL_MINUTES} minutes",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Show the current dashboard without scanning",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show all jobs (not just new ones)",
    )
    parser.add_argument(
        "--export", action="store_true",
        help="Export all jobs to CSV",
    )
    parser.add_argument(
        "--mark-applied", type=str, metavar="JOB_ID",
        help="Mark a job as applied by its ID",
    )
    parser.add_argument(
        "--backfill-descriptions", action="store_true",
        help="Fetch descriptions for saved jobs that don't have one",
    )

    args = parser.parse_args()

    # Print banner
    console.print(Panel(
        "[bold cyan]Job Hunter[/bold cyan] — Automated Job Search for F1 Students\n"
        "[dim]Roles: Data Scientist | ML Engineer | AI/ML Engineer | SDE[/dim]\n"
        "[dim]Filter: H1B sponsorship friendly companies only[/dim]",
        box=box.DOUBLE,
    ))

    if args.dashboard:
        if args.all:
            show_all_jobs()
        else:
            show_new_jobs()
        return

    if args.export:
        export_csv()
        return

    if args.backfill_descriptions:
        backfill_descriptions()
        return

    if args.mark_applied:
        mark_applied(args.mark_applied)
        console.print(f"[green]Marked job {args.mark_applied} as applied.[/]")
        return

    if args.loop:
        console.print(f"[bold]Starting continuous mode — scanning every {CHECK_INTERVAL_MINUTES} minutes[/]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        # Handle graceful shutdown
        def signal_handler(sig, frame):
            console.print("\n[bold yellow]Shutting down gracefully...[/]")
            stats = get_stats()
            console.print(f"[dim]Final stats: {stats}[/dim]")
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # Run immediately, then schedule
        run_scan()
        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_scan)

        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        # Single run
        run_scan()


if __name__ == "__main__":
    main()
