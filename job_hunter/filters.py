"""Visa sponsorship and relevance filtering for F1 students."""

import logging

from config import (
    H1B_SPONSOR_COMPANIES,
    SPONSORSHIP_POSITIVE,
    SPONSORSHIP_NEGATIVE,
    SENIOR_KEYWORDS,
    INTERN_KEYWORDS,
)

logger = logging.getLogger("job_hunter.filters")


def _normalize(text: str) -> str:
    return text.lower().strip()


def check_sponsorship(job: dict) -> dict:
    """Analyze a job listing and determine sponsorship likelihood.

    Returns the job dict with updated sponsorship_status and is_h1b_sponsor fields.

    Sponsorship status:
        - 'likely'    — company is a known H1B sponsor or description has positive signals
        - 'unlikely'  — description explicitly says no sponsorship
        - 'unknown'   — can't determine from available information
    """
    company = _normalize(job.get("company", ""))
    description = _normalize(job.get("description", ""))
    title = _normalize(job.get("title", ""))
    full_text = f"{title} {description}"

    # Check for explicit negative signals first (highest priority)
    for neg in SPONSORSHIP_NEGATIVE:
        if neg in full_text:
            job["sponsorship_status"] = "unlikely"
            job["is_h1b_sponsor"] = False
            return job

    # Check if company is a known H1B sponsor
    is_known_sponsor = any(
        sponsor in company or company in sponsor
        for sponsor in H1B_SPONSOR_COMPANIES
    )

    # Check for positive sponsorship signals in description
    has_positive_signal = any(pos in full_text for pos in SPONSORSHIP_POSITIVE)

    if is_known_sponsor or has_positive_signal:
        job["sponsorship_status"] = "likely"
        job["is_h1b_sponsor"] = True
    else:
        job["sponsorship_status"] = "unknown"
        job["is_h1b_sponsor"] = False

    return job


def is_relevant_role(job: dict) -> bool:
    """Check if the job title matches one of the target roles."""
    title = _normalize(job.get("title", ""))

    relevant_keywords = [
        "data scien", "machine learning", "ml engineer", "ml ",
        "ai engineer", "ai/ml", "ai ", "artificial intelligence",
        "deep learning", "nlp", "natural language",
        "computer vision", "software develop", "software engineer",
        "sde", "swe", "backend engineer", "full stack",
        "research engineer", "research scientist",
        "applied scientist", "data engineer",
    ]
    return any(kw in title for kw in relevant_keywords)


def is_beginner_level(job: dict) -> bool:
    """Check if a job is internship / entry-level / new grad.

    Accepts jobs that:
    - Have intern/entry/new-grad keywords in title OR description, OR
    - Do NOT have explicit senior-level keywords in title
    """
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))
    full_text = f"{title} {description}"

    # Explicit senior-level keywords in TITLE = reject
    for sr in SENIOR_KEYWORDS:
        if sr in title:
            return False

    # If title/desc mentions intern/entry/new-grad = accept
    for kw in INTERN_KEYWORDS:
        if kw in full_text:
            return True

    # No senior keywords AND no intern keywords — accept cautiously
    # (some titles like "Data Scientist" could be entry-level too)
    return True


def is_internship(job: dict) -> bool:
    """Strict check — only internships & co-ops."""
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))
    full_text = f"{title} {description}"

    intern_only = ["intern", "internship", "co-op", "coop", "summer 20", "fall 20", "spring 20"]
    return any(kw in full_text for kw in intern_only)


def is_fulltime_entry(job: dict) -> bool:
    """Check if a job is full-time entry-level / new grad (not internship)."""
    if is_internship(job):
        return False
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))
    full_text = f"{title} {description}"

    entry_keywords = [
        "new grad", "new graduate", "entry level", "entry-level",
        "junior", "jr.", "jr ", "associate",
        "early career", "recent graduate",
        "0-2 years", "0-1 years", "1-2 years", "1+ years", "2+ years",
    ]
    return any(kw in full_text for kw in entry_keywords)


def filter_jobs(jobs: list[dict]) -> list[dict]:
    """Apply all filters: role relevance + beginner-level + sponsorship analysis.

    Returns only jobs that:
    1. Match target roles
    2. Are beginner-level (intern/new grad/entry — NOT senior)
    3. Are NOT explicitly anti-sponsorship
    """
    filtered = []
    stats = {
        "total": len(jobs),
        "role_filtered": 0,
        "seniority_filtered": 0,
        "sponsorship_filtered": 0,
        "passed": 0,
    }

    for job in jobs:
        # Step 1: Role relevance
        if not is_relevant_role(job):
            stats["role_filtered"] += 1
            continue

        # Step 2: Beginner-level filter (reject senior roles)
        if not is_beginner_level(job):
            stats["seniority_filtered"] += 1
            continue

        # Step 3: Tag job type (internship vs full-time entry)
        if is_internship(job):
            job["tags"] = (job.get("tags") or []) + ["internship"]
        elif is_fulltime_entry(job):
            job["tags"] = (job.get("tags") or []) + ["full-time", "entry-level"]
        else:
            # No explicit level keyword but passed seniority filter — likely entry-level
            job["tags"] = (job.get("tags") or []) + ["full-time"]

        # Step 4: Sponsorship analysis
        job = check_sponsorship(job)

        # Remove jobs that explicitly say no sponsorship
        if job["sponsorship_status"] == "unlikely":
            stats["sponsorship_filtered"] += 1
            continue

        stats["passed"] += 1
        filtered.append(job)

    logger.info(
        f"Filter stats — Total: {stats['total']}, "
        f"Role filtered: {stats['role_filtered']}, "
        f"Seniority filtered: {stats['seniority_filtered']}, "
        f"Sponsorship filtered: {stats['sponsorship_filtered']}, "
        f"Passed: {stats['passed']}"
    )
    return filtered
