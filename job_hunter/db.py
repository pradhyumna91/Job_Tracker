"""SQLite database for tracking job listings."""

import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            url TEXT,
            source TEXT,
            description TEXT,
            salary TEXT,
            sponsorship_status TEXT DEFAULT 'unknown',
            is_h1b_sponsor INTEGER DEFAULT 0,
            tags TEXT,
            date_posted TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            applied INTEGER DEFAULT 0,
            notes TEXT
        )
    """)
    # Migrations: add columns to tables that predate them. ALTER TABLE ADD
    # COLUMN is additive and safe to re-run — it just errors when the column
    # is already there.
    _MIGRATIONS = [
        "ALTER TABLE jobs ADD COLUMN date_posted TEXT",
        # USCIS H-1B evidence behind is_h1b_sponsor
        "ALTER TABLE jobs ADD COLUMN h1b_approvals INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN h1b_fiscal_year TEXT",
        "ALTER TABLE jobs ADD COLUMN h1b_match TEXT",
        "ALTER TABLE jobs ADD COLUMN h1b_confidence TEXT",
        "ALTER TABLE jobs ADD COLUMN sponsorship_reason TEXT",
        # How long each scan took — scans have silently run for hours
        "ALTER TABLE scan_log ADD COLUMN duration_seconds INTEGER",
    ]
    for statement in _MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT,
            query TEXT,
            jobs_found INTEGER DEFAULT 0,
            new_jobs INTEGER DEFAULT 0,
            errors TEXT
        )
    """)
    conn.commit()
    conn.close()


def insert_job(job: dict) -> bool:
    """Insert a new job. Returns True if it was new, False if already existed."""
    now = datetime.now().isoformat()
    job_id = job["id"]

    conn = get_connection()
    existing = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE jobs SET last_seen = ? WHERE id = ?",
            (now, job_id),
        )
        conn.commit()
        conn.close()
        return False

    conn.execute(
        """INSERT INTO jobs
           (id, title, company, location, url, source, description, salary,
            sponsorship_status, is_h1b_sponsor, h1b_approvals, h1b_fiscal_year,
            h1b_match, h1b_confidence, sponsorship_reason,
            tags, date_posted, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("url", ""),
            job.get("source", ""),
            job.get("description", ""),
            job.get("salary", ""),
            job.get("sponsorship_status", "unknown"),
            1 if job.get("is_h1b_sponsor") else 0,
            int(job.get("h1b_approvals") or 0),
            job.get("h1b_fiscal_year"),
            job.get("h1b_match"),
            job.get("h1b_confidence"),
            job.get("sponsorship_reason"),
            json.dumps(job.get("tags", [])),
            job.get("date_posted", ""),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return True


def log_scan(source: str, query: str, jobs_found: int, new_jobs: int,
             errors: str = "", duration_seconds: int = None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO scan_log
           (timestamp, source, query, jobs_found, new_jobs, errors, duration_seconds)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), source, query, jobs_found, new_jobs,
         errors, duration_seconds),
    )
    conn.commit()
    conn.close()


def get_last_scan_time():
    """Timestamp of the most recent scan, or None if the DB has never been scanned."""
    conn = get_connection()
    row = conn.execute(
        "SELECT timestamp FROM scan_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except (TypeError, ValueError):
        return None


def get_new_jobs(limit: int = 50):
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM jobs WHERE status = 'new'
           ORDER BY first_seen DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_jobs(limit: int = 200):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY first_seen DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_jobs_missing_description(limit: int = 1000):
    """Jobs with no fetched description, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM jobs WHERE (description IS NULL OR description = '')
           AND url IS NOT NULL AND url != ''
           ORDER BY date_posted DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job_description(job_id: str, description: str, job: dict):
    """Store a fetched description and the sponsorship verdict derived from it.

    `job` is the dict returned by filters.check_sponsorship().
    """
    conn = get_connection()
    conn.execute(
        """UPDATE jobs SET description = ?, sponsorship_status = ?, is_h1b_sponsor = ?,
                  h1b_approvals = ?, h1b_fiscal_year = ?, h1b_match = ?,
                  h1b_confidence = ?, sponsorship_reason = ?
           WHERE id = ?""",
        (
            description,
            job.get("sponsorship_status", "unknown"),
            1 if job.get("is_h1b_sponsor") else 0,
            int(job.get("h1b_approvals") or 0),
            job.get("h1b_fiscal_year"),
            job.get("h1b_match"),
            job.get("h1b_confidence"),
            job.get("sponsorship_reason"),
            job_id,
        ),
    )
    conn.commit()
    conn.close()


def mark_applied(job_id: str):
    conn = get_connection()
    conn.execute(
        "UPDATE jobs SET applied = 1, status = 'applied' WHERE id = ?", (job_id,)
    )
    conn.commit()
    conn.close()


def mark_seen(job_id: str):
    conn = get_connection()
    conn.execute("UPDATE jobs SET status = 'seen' WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()


def get_stats():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    new = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'new'").fetchone()[0]
    # "F1 friendly" on the dashboard matches the badge, which keys off
    # sponsorship_status — a posting that says it sponsors counts even when the
    # employer has no USCIS record yet.
    sponsor = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE sponsorship_status = 'likely'"
    ).fetchone()[0]
    applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE applied = 1").fetchone()[0]
    conn.close()
    return {"total": total, "new": new, "h1b_friendly": sponsor, "applied": applied}


init_db()
