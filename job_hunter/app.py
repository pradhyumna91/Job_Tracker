#!/usr/bin/env python3
"""Flask web app for the Job Hunter dashboard."""

import json
import logging
import os
import threading
import sys
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, jsonify, request, redirect, url_for

from config import (
    LOG_DIR, CHECK_INTERVAL_MINUTES, FRESH_POSTING_HOURS, EARLIEST_START,
    SCAN_TIME_BUDGET_MINUTES, MIN_SCAN_GAP_MINUTES,
)
from scrapers import scrape_all
from filters import (
    filter_jobs, is_internship, candidate_signals, company_group, COMPANY_GROUPS,
)
from db import (
    insert_job, log_scan, get_new_jobs, get_all_jobs, get_stats,
    get_last_scan_time, mark_applied, mark_seen,
)
from notifier import notify_new_jobs

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "job_hunter_web.log"),
    ],
)
logger = logging.getLogger("job_hunter.web")

app = Flask(__name__)

# Track scan state so the frontend can show a spinner
_scan_lock = threading.Lock()
_scan_state = {
    "running": False,
    "last_scan": None,      # ISO timestamp of the last completed scan
    "last_new_count": 0,
    "next_scan": None,      # ISO timestamp of the next automatic refresh
}

# Seed from the database so the dashboard shows the real last scan straight away,
# even before the scheduler thread starts (and across app restarts).
_last = get_last_scan_time()
if _last:
    _scan_state["last_scan"] = _last.isoformat()


def _run_scan(trigger: str = "manual"):
    """Execute a full scan cycle.

    Thread-safe: the lock guards only the `running` flag, so a second caller is
    turned away immediately rather than queueing behind a scan that runs for
    minutes. `trigger` is "manual" (refresh button) or "auto" (hourly refresh).
    """
    with _scan_lock:
        if _scan_state["running"]:
            return {"error": "Scan already in progress"}
        _scan_state["running"] = True

    started_at = datetime.now()
    started_mono = time.monotonic()
    budget = SCAN_TIME_BUDGET_MINUTES * 60
    # Set before the work begins so /api/stats can report elapsed time while
    # the scan is still running, not only once it finishes.
    _scan_state["last_started"] = started_at.isoformat()
    try:
        logger.info(
            f"Scan starting ({trigger}), budget {SCAN_TIME_BUDGET_MINUTES} min"
        )
        raw_jobs = scrape_all(deadline=started_mono + budget)
        filtered_jobs = filter_jobs(raw_jobs)

        new_count = 0
        new_jobs = []
        for job in filtered_jobs:
            is_new = insert_job(job)
            if is_new:
                new_count += 1
                new_jobs.append(job)

        elapsed = time.monotonic() - started_mono
        log_scan("all", "all_queries", len(raw_jobs), new_count,
                 duration_seconds=round(elapsed))

        if new_jobs:
            notify_new_jobs(new_jobs)

        _scan_state["last_scan"] = datetime.now().isoformat()
        _scan_state["last_new_count"] = new_count
        _scan_state["last_duration_seconds"] = round(elapsed)
        _scan_state["last_scan_truncated"] = elapsed >= budget

        note = " (hit time budget — results incomplete)" if elapsed >= budget else ""
        logger.info(
            f"Scan complete ({trigger}) in {elapsed / 60:.1f} min{note}: "
            f"{len(raw_jobs)} raw, {len(filtered_jobs)} filtered, {new_count} new"
        )
        return {
            "raw": len(raw_jobs),
            "filtered": len(filtered_jobs),
            "new": new_count,
            "duration_seconds": round(elapsed),
            "truncated": elapsed >= budget,
        }
    except Exception as e:
        elapsed = time.monotonic() - started_mono
        logger.error(f"Scan error ({trigger}) after {elapsed / 60:.1f} min: {e}")
        return {"error": str(e)}
    finally:
        with _scan_lock:
            _scan_state["running"] = False


# ---- Hourly refresh ----

def _next_run_at(started, finished, interval, min_gap):
    """When the next automatic scan should happen.

    Paced from when the scan STARTED, so a slow scan doesn't push the schedule
    out: pacing from the end meant a scan that overran left the data stale and
    then waited a further full interval. `min_gap` from `finished` keeps a scan
    that overruns its whole interval from becoming a tight retry loop.
    """
    return max(started + interval, finished + min_gap)


def _scheduler_loop():
    """Keep the database fresh: rescan every CHECK_INTERVAL_MINUTES.

    On startup, the next run is scheduled one interval after the last scan
    recorded in the database — so a database that is already stale (or empty)
    is refreshed right away rather than waiting a full hour.
    """
    interval = timedelta(minutes=CHECK_INTERVAL_MINUTES)

    min_gap = timedelta(minutes=MIN_SCAN_GAP_MINUTES)

    last = get_last_scan_time()
    if last:
        _scan_state["last_scan"] = last.isoformat()
        next_at = last + interval
    else:
        next_at = datetime.now()
    _scan_state["next_scan"] = next_at.isoformat()

    due = "now (database is stale)" if next_at <= datetime.now() else \
        next_at.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        f"Auto-refresh enabled: every {CHECK_INTERVAL_MINUTES} min (next run {due})"
    )

    while True:
        if datetime.now() >= next_at:
            started = datetime.now()
            _run_scan(trigger="auto")
            next_at = _next_run_at(started, datetime.now(), interval, min_gap)
            _scan_state["next_scan"] = next_at.isoformat()
            logger.info(f"Next auto-refresh at {next_at.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(30)


def start_scheduler():
    """Start the hourly refresh in a daemon thread."""
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="job-refresh")
    thread.start()
    return thread


# ---- Routes ----

@app.route("/")
def index():
    return render_template("index.html", company_group="all")


# Company pages. Each reuses the main jobs table — with every filter intact —
# scoped to one company group. The groups partition the jobs: a company lands
# in exactly one, so nothing is shown twice across the three pages.
_GROUP_TITLES = {
    "referral": "Referrals",
    "target": "Target Companies",
    "other": "Other Companies",
}


@app.route("/companies/<group>")
def company_group_page(group):
    if group not in _GROUP_TITLES:
        return redirect(url_for("index"))
    return render_template(
        "index.html", company_group=group, group_title=_GROUP_TITLES[group]
    )


@app.route("/fresh")
def fresh_page():
    return render_template(
        "companies.html", active_page="fresh",
        fresh_hours=FRESH_POSTING_HOURS, earliest_start=EARLIEST_START,
    )


@app.route("/opt")
def opt_page():
    return render_template(
        "companies.html", active_page="opt",
        fresh_hours=FRESH_POSTING_HOURS, earliest_start=EARLIEST_START,
    )


def _posted_at(job: dict):
    """When a job was posted (UTC), falling back to when we first saw it."""
    for field in ("date_posted", "first_seen"):
        raw = (job.get(field) or "").replace("Z", "+00:00")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        # first_seen is stored as naive local time
        return dt.astimezone(timezone.utc) if dt.tzinfo is None else dt
    return None


def _is_opt_suitable(job: dict) -> bool:
    """Whether a role suits an international student on F-1 / OPT.

    Requires a recent-grad signal, an international / sponsorship signal, no
    clearance or citizenship blocker, and a start date you could make. This is
    the same standard the OPT & New Grad page applies.
    """
    if is_internship(job) or job.get("sponsorship_status") == "unlikely":
        return False
    if isinstance(job.get("tags"), str):
        try:
            job["tags"] = json.loads(job["tags"] or "[]")
        except ValueError:
            job["tags"] = []
    signals = candidate_signals(job)
    return bool(
        signals["grad"]
        and signals["intl"]
        and not signals["blockers"]
        and not signals["start"]["early"]
    )


def _group_by_company(roles: list[dict], fresh_first: bool = False) -> list[dict]:
    """Group roles into companies, busiest and most recently active first.

    `fresh_first` floats companies with a just-posted role to the top. The OPT
    page now includes brand-new roles, and ranking by role count alone would
    bury a company with one fresh opening under one with many stale ones.
    """
    companies = {}
    for role in roles:
        name = " ".join(role["company"].split()) or "Company not listed"
        c = companies.setdefault(name.lower(), {
            "company": name, "roles": [],
            "sponsors_h1b": False, "has_fresh": False,
            "grad": set(), "intl": set(),
        })
        c["roles"].append(role)
        c["sponsors_h1b"] |= bool(role["is_h1b_sponsor"])
        c["has_fresh"] |= bool(role.get("is_fresh"))
        c["grad"].update(role["signals"]["grad"])
        c["intl"].update(role["signals"]["intl"])

    result = []
    for c in companies.values():
        c["roles"].sort(key=lambda r: r["posted_at"] or "", reverse=True)
        c["latest_posted"] = c["roles"][0]["posted_at"]
        c["grad"], c["intl"] = sorted(c["grad"]), sorted(c["intl"])
        result.append(c)

    result.sort(
        key=lambda c: (
            (c["has_fresh"] if fresh_first else False),
            len(c["roles"]),
            c["latest_posted"] or "",
        ),
        reverse=True,
    )
    return result


@app.route("/api/companies")
def api_companies():
    """Full-time roles grouped by company, for the two company pages.

    view=fresh  roles posted within FRESH_POSTING_HOURS
    view=opt    the remaining (older) roles, limited to ones showing both a
                recent-grad signal and an international / OPT / sponsorship
                signal, with no clearance or citizenship requirement, and not
                explicitly starting before EARLIEST_START
                (include_early=1 keeps early-start roles; max_age_days=N limits age)
    """
    view = request.args.get("view", "fresh")
    include_early = request.args.get("include_early") == "1"
    try:
        max_age_days = int(request.args.get("max_age_days") or 0)
    except ValueError:
        max_age_days = 0

    now = datetime.now(timezone.utc)
    fresh_cutoff = now - timedelta(hours=FRESH_POSTING_HOURS)
    hidden = {"not_sponsoring": 0, "no_grad_signal": 0, "no_intl_signal": 0,
              "clearance_or_citizenship": 0, "starts_too_early": 0, "too_old": 0}
    roles = []

    for job in get_all_jobs(limit=100000):
        if is_internship(job):
            continue
        posted = _posted_at(job)
        is_fresh = posted is not None and posted >= fresh_cutoff
        # The fresh page shows only recent postings. The OPT page shows every
        # eligible role regardless of age — it used to exclude anything newer
        # than the fresh window, which hid the most actionable roles and meant
        # checking two pages to see them all.
        if view == "fresh" and not is_fresh:
            continue
        if job.get("sponsorship_status") == "unlikely":
            hidden["not_sponsoring"] += 1
            continue

        try:
            job["tags"] = json.loads(job.get("tags") or "[]")
        except ValueError:
            job["tags"] = []
        signals = candidate_signals(job)

        if view == "opt":
            if max_age_days and posted and posted < now - timedelta(days=max_age_days):
                hidden["too_old"] += 1
                continue
            if signals["blockers"]:
                hidden["clearance_or_citizenship"] += 1
                continue
            if not signals["grad"]:
                hidden["no_grad_signal"] += 1
                continue
            if not signals["intl"]:
                hidden["no_intl_signal"] += 1
                continue
            if signals["start"]["early"] and not include_early:
                hidden["starts_too_early"] += 1
                continue

        roles.append({
            "id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "location": job.get("location") or "",
            "url": job.get("url") or "",
            "source": job.get("source") or "",
            "sponsorship_status": job.get("sponsorship_status"),
            "is_h1b_sponsor": bool(job.get("is_h1b_sponsor")),
            "applied": bool(job.get("applied")),
            "posted_at": posted.isoformat() if posted else None,
            "is_fresh": is_fresh,
            "has_description": bool(job.get("description")),
            "signals": signals,
        })

    return jsonify({
        "view": view,
        "fresh_hours": FRESH_POSTING_HOURS,
        "earliest_start": EARLIEST_START,
        "role_count": len(roles),
        "companies": _group_by_company(roles, fresh_first=(view == "opt")),
        "fresh_count": sum(1 for r in roles if r["is_fresh"]),
        "hidden": hidden if view == "opt" else {"not_sponsoring": hidden["not_sponsoring"]},
    })


@app.route("/api/jobs")
def api_jobs():
    """Get jobs with optional filters.

    filter          all | new | h1b | fulltime
    max_age_hours   only jobs posted within this many hours (0 / absent = any)
    company_group   all | referral | target | other
    search          substring match on title, company or location

    All four are orthogonal, so "referral companies, H1B friendly, posted in
    the last 24h" is one request.
    """
    filter_type = request.args.get("filter", "all")  # all, new, h1b, fulltime
    search = request.args.get("search", "").lower()
    group = request.args.get("company_group", "all")
    try:
        max_age_hours = max(0, int(request.args.get("max_age_hours") or 0))
    except ValueError:
        max_age_hours = 0

    if filter_type == "new":
        jobs = get_new_jobs(limit=500)
    else:
        jobs = get_all_jobs(limit=500)

    if max_age_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        # _posted_at prefers date_posted and falls back to first_seen, so a
        # listing with no publish date counts from when we first saw it.
        jobs = [j for j in jobs if (_posted_at(j) or datetime.min.replace(
            tzinfo=timezone.utc)) >= cutoff]

    if group in COMPANY_GROUPS:
        jobs = [j for j in jobs if company_group(j.get("company", "")) == group]

    if filter_type == "opt":
        jobs = [j for j in jobs if _is_opt_suitable(j)]

    # Apply filters on server
    if filter_type == "h1b":
        # Matches the "F1 Friendly" badge and the dashboard stat, both of which
        # key off sponsorship_status rather than the employer-level flag.
        jobs = [j for j in jobs if j.get("sponsorship_status") == "likely"]
    elif filter_type == "fulltime":
        jobs = [j for j in jobs if "full-time" in (j.get("tags") or "")]

    if search:
        jobs = [
            j for j in jobs
            if search in j.get("title", "").lower()
            or search in j.get("company", "").lower()
            or search in j.get("location", "").lower()
        ]

    return jsonify(jobs)


@app.route("/api/stats")
def api_stats():
    stats = get_stats()
    stats["scan_running"] = _scan_state["running"]
    stats["last_scan"] = _scan_state["last_scan"]
    stats["last_new_count"] = _scan_state["last_new_count"]
    stats["next_scan"] = _scan_state["next_scan"]
    stats["refresh_interval_minutes"] = CHECK_INTERVAL_MINUTES
    # Scan health, so a stalled or truncated refresh is visible rather than
    # showing up only as mysteriously old data.
    stats["last_duration_seconds"] = _scan_state.get("last_duration_seconds")
    stats["last_scan_truncated"] = _scan_state.get("last_scan_truncated", False)
    stats["scan_budget_minutes"] = SCAN_TIME_BUDGET_MINUTES
    if _scan_state["running"] and _scan_state.get("last_started"):
        started = datetime.fromisoformat(_scan_state["last_started"])
        stats["current_scan_elapsed_seconds"] = round(
            (datetime.now() - started).total_seconds()
        )
    return jsonify(stats)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Trigger a new scan in a background thread and return immediately."""
    if _scan_state["running"]:
        return jsonify({"status": "already_running"})

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/mark", methods=["POST"])
def api_mark():
    """Mark a job as applied or seen."""
    data = request.get_json()
    job_id = data.get("id")
    action = data.get("action", "seen")  # "applied" or "seen"

    if not job_id:
        return jsonify({"error": "Missing job id"}), 400

    if action == "applied":
        mark_applied(job_id)
    else:
        mark_seen(job_id)

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    DEBUG = os.environ.get("JOB_HUNTER_DEBUG", "1") == "1"

    print("\n  Job Hunter Web Dashboard")
    print("  http://localhost:5050")
    print(f"  Auto-refreshing the database every {CHECK_INTERVAL_MINUTES} minutes\n")

    # In debug mode Flask's reloader runs two processes; only the worker (the one
    # with WERKZEUG_RUN_MAIN set) should own the scheduler, or scans run twice.
    if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()

    app.run(host="0.0.0.0", port=5050, debug=DEBUG)
