"""Visa sponsorship and relevance filtering for F1 students."""

import logging

from config import (
    SPONSORSHIP_POSITIVE,
    SPONSORSHIP_NEGATIVE,
    SENIOR_KEYWORDS,
    INTERN_KEYWORDS,
    PHD_REQUIRED_KEYWORDS,
    PHD_TITLE_KEYWORDS,
)
from h1b_data import build_h1b_employer_set, is_verified_h1b_sponsor

logger = logging.getLogger("job_hunter.filters")

# Build the employer set once at import time
_h1b_employers = None


def _get_h1b_employers() -> set[str]:
    global _h1b_employers
    if _h1b_employers is None:
        _h1b_employers = build_h1b_employer_set()
    return _h1b_employers


def _normalize(text: str) -> str:
    return text.lower().strip()


# --- US State abbreviations and keywords for location filtering ---
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

_US_KEYWORDS = [
    "united states", "usa", "u.s.", "us-", "us ",
    "remote", "hybrid",  # remote/hybrid without country usually means US for US-targeted queries
    "new york", "san francisco", "los angeles", "chicago", "seattle",
    "austin", "boston", "denver", "atlanta", "dallas", "houston",
    "san jose", "san diego", "washington", "philadelphia", "phoenix",
    "portland", "raleigh", "charlotte", "nashville", "salt lake",
    "minneapolis", "detroit", "pittsburgh", "miami", "tampa",
    "columbus", "indianapolis", "baltimore", "st. louis", "st louis",
    "kansas city", "milwaukee", "sacramento", "las vegas", "orlando",
    "silicon valley", "bay area", "research triangle",
    "mountain view", "palo alto", "menlo park", "sunnyvale",
    "cupertino", "redmond", "redwood city", "santa clara",
    "cambridge", "boulder", "ann arbor", "madison",
]

# Non-US locations to explicitly reject
_NON_US_KEYWORDS = [
    "canada", "uk", "united kingdom", "london", "england", "scotland",
    "ireland", "dublin", "germany", "berlin", "munich", "france", "paris",
    "india", "bangalore", "hyderabad", "mumbai", "pune", "delhi", "chennai",
    "china", "beijing", "shanghai", "japan", "tokyo", "singapore",
    "australia", "sydney", "melbourne", "brazil", "mexico",
    "netherlands", "amsterdam", "sweden", "stockholm", "spain", "madrid",
    "italy", "milan", "switzerland", "zurich", "israel", "tel aviv",
    "south korea", "seoul", "taiwan", "hong kong", "vietnam",
    "poland", "warsaw", "czech", "prague", "romania", "bucharest",
    "argentina", "buenos aires", "colombia", "bogota", "chile",
    "toronto", "vancouver", "montreal", "ottawa", "calgary",
    "manchester", "bristol", "edinburgh", "leeds", "birmingham",
    "latin america", "latam", "emea", "apac",
]


def is_us_location(job: dict) -> bool:
    """Check if a job is located in the United States."""
    location = _normalize(job.get("location", ""))

    # Empty location — allow (many US jobs just don't list location)
    if not location:
        return True

    # Check for explicit non-US keywords first
    for kw in _NON_US_KEYWORDS:
        if kw in location:
            return False

    # Check for US keywords
    for kw in _US_KEYWORDS:
        if kw in location:
            return True

    # Check for US state abbreviations (e.g. "San Jose, CA" or "NY")
    # Look for 2-letter state codes after a comma or at the end
    parts = [p.strip().rstrip(".") for p in location.replace(",", " ").split()]
    for part in parts:
        if part.lower() in _US_STATES:
            return True

    # If we can't determine — reject to keep results clean
    return False


def check_sponsorship(job: dict) -> dict:
    """Analyze a job listing and determine sponsorship likelihood.

    Uses three layers:
    1. Explicit negative signals in text → unlikely
    2. Explicit positive signals in text → likely
    3. Company name against verified H1B employer database → likely
    4. MyVisaJobs / h1b-verified source tag → likely
    5. None of the above → unknown

    Sponsorship status:
        - 'likely'    — verified sponsor or positive signals
        - 'unlikely'  — explicit no-sponsorship language
        - 'unknown'   — can't determine (still included, but flagged)
    """
    company = _normalize(job.get("company", ""))
    description = _normalize(job.get("description", ""))
    title = _normalize(job.get("title", ""))
    full_text = f"{title} {description}"
    tags = job.get("tags", [])

    # If already marked by source (e.g. MyVisaJobs), trust it
    if isinstance(tags, list) and "h1b-verified" in tags:
        job["sponsorship_status"] = "likely"
        job["is_h1b_sponsor"] = True
        return job

    # Layer 1: Explicit negative signals (highest priority)
    for neg in SPONSORSHIP_NEGATIVE:
        if neg in full_text:
            job["sponsorship_status"] = "unlikely"
            job["is_h1b_sponsor"] = False
            return job

    # Layer 2: Explicit positive signals in description
    has_positive_signal = any(pos in full_text for pos in SPONSORSHIP_POSITIVE)

    # Layer 3: Check against verified H1B employer database
    is_verified = is_verified_h1b_sponsor(company, _get_h1b_employers())

    if is_verified or has_positive_signal:
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


def requires_phd(job: dict) -> bool:
    """Check if a job requires a PhD — reject these for non-PhD F1 students."""
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))

    # PhD in title = PhD-track role
    for kw in PHD_TITLE_KEYWORDS:
        if kw in title:
            return True

    # PhD required in description
    for kw in PHD_REQUIRED_KEYWORDS:
        if kw in description:
            return True

    return False


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
        "location_filtered": 0,
        "role_filtered": 0,
        "seniority_filtered": 0,
        "phd_filtered": 0,
        "sponsorship_filtered": 0,
        "passed": 0,
    }

    for job in jobs:
        # Step 0: US location filter
        if not is_us_location(job):
            stats["location_filtered"] += 1
            continue

        # Step 1: Role relevance
        if not is_relevant_role(job):
            stats["role_filtered"] += 1
            continue

        # Step 2: Beginner-level filter (reject senior roles)
        if not is_beginner_level(job):
            stats["seniority_filtered"] += 1
            continue

        # Step 2.5: PhD filter (reject PhD-required roles)
        if requires_phd(job):
            stats["phd_filtered"] += 1
            continue

        # Step 3: Tag job type (internship vs full-time entry)
        if is_internship(job):
            job["tags"] = (job.get("tags") or []) + ["internship"]
        elif is_fulltime_entry(job):
            job["tags"] = (job.get("tags") or []) + ["full-time", "entry-level"]
        else:
            # No explicit level keyword but passed seniority filter — likely entry-level
            job["tags"] = (job.get("tags") or []) + ["full-time"]

        # Step 4: Sponsorship analysis (now uses H1B employer database)
        job = check_sponsorship(job)

        # Remove jobs that explicitly say no sponsorship
        if job["sponsorship_status"] == "unlikely":
            stats["sponsorship_filtered"] += 1
            continue

        stats["passed"] += 1
        filtered.append(job)

    logger.info(
        f"Filter stats — Total: {stats['total']}, "
        f"Location filtered: {stats['location_filtered']}, "
        f"Role filtered: {stats['role_filtered']}, "
        f"Seniority filtered: {stats['seniority_filtered']}, "
        f"PhD filtered: {stats['phd_filtered']}, "
        f"Sponsorship filtered: {stats['sponsorship_filtered']}, "
        f"Passed: {stats['passed']}"
    )
    return filtered
