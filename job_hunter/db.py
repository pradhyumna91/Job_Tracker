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
    # Migration: add date_posted column if table already exists without it
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN date_posted TEXT")
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


def job_exists(job_id: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row is not None


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
            sponsorship_status, is_h1b_sponsor, tags, date_posted, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            json.dumps(job.get("tags", [])),
            job.get("date_posted", ""),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return True


def log_scan(source: str, query: str, jobs_found: int, new_jobs: int, errors: str = ""):
    conn = get_connection()
    conn.execute(
        """INSERT INTO scan_log (timestamp, source, query, jobs_found, new_jobs, errors)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), source, query, jobs_found, new_jobs, errors),
    )
    conn.commit()
    conn.close()


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
    sponsor = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE is_h1b_sponsor = 1"
    ).fetchone()[0]
    applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE applied = 1").fetchone()[0]
    conn.close()
    return {"total": total, "new": new, "h1b_friendly": sponsor, "applied": applied}


init_db()
