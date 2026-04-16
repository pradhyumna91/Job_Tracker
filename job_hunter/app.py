#!/usr/bin/env python3
"""Flask web app for the Job Hunter dashboard."""

import logging
import threading
import sys
from datetime import datetime

from flask import Flask, render_template, jsonify, request

from config import LOG_DIR
from scrapers import scrape_all
from filters import filter_jobs
from db import insert_job, log_scan, get_new_jobs, get_all_jobs, get_stats, mark_applied, mark_seen
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
_scan_state = {"running": False, "last_scan": None, "last_new_count": 0}


def _run_scan():
    """Execute a full scan cycle. Thread-safe."""
    if _scan_state["running"]:
        return {"error": "Scan already in progress"}

    with _scan_lock:
        _scan_state["running"] = True
        try:
            raw_jobs = scrape_all()
            filtered_jobs = filter_jobs(raw_jobs)

            new_count = 0
            new_jobs = []
            for job in filtered_jobs:
                is_new = insert_job(job)
                if is_new:
                    new_count += 1
                    new_jobs.append(job)

            log_scan("all", "all_queries", len(raw_jobs), new_count)

            if new_jobs:
                notify_new_jobs(new_jobs)

            _scan_state["last_scan"] = datetime.now().isoformat()
            _scan_state["last_new_count"] = new_count

            logger.info(f"Scan complete: {len(raw_jobs)} raw, {len(filtered_jobs)} filtered, {new_count} new")
            return {
                "raw": len(raw_jobs),
                "filtered": len(filtered_jobs),
                "new": new_count,
            }
        except Exception as e:
            logger.error(f"Scan error: {e}")
            return {"error": str(e)}
        finally:
            _scan_state["running"] = False


# ---- Routes ----

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def api_jobs():
    """Get jobs with optional filters."""
    filter_type = request.args.get("filter", "all")  # all, new, h1b, internship, fulltime
    search = request.args.get("search", "").lower()

    if filter_type == "new":
        jobs = get_new_jobs(limit=500)
    else:
        jobs = get_all_jobs(limit=500)

    # Apply filters on server
    if filter_type == "h1b":
        jobs = [j for j in jobs if j.get("is_h1b_sponsor")]
    elif filter_type == "internship":
        jobs = [j for j in jobs if "internship" in (j.get("tags") or "")]
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
    print("\n  Job Hunter Web Dashboard")
    print("  http://localhost:5050\n")
    app.run(host="0.0.0.0", port=5050, debug=True)
