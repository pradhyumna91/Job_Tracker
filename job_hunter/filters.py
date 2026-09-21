"""Visa sponsorship and relevance filtering for F1 students."""

import logging
import re

from config import (
    SPONSORSHIP_POSITIVE,
    SPONSORSHIP_NEGATIVE,
    SPONSORSHIP_NEGATIVE_PATTERNS,
    SPONSORSHIP_EEO_BOILERPLATE,
    SENIOR_KEYWORDS,
    INTERN_KEYWORDS,
    PHD_REQUIRED_KEYWORDS,
    PHD_TITLE_KEYWORDS,
    EARLIEST_START,
)
from h1b_data import lookup_sponsor

logger = logging.getLogger("job_hunter.filters")

_NEGATIVE_RE = [re.compile(p) for p in SPONSORSHIP_NEGATIVE_PATTERNS]
_EEO_RE = [re.compile(p) for p in SPONSORSHIP_EEO_BOILERPLATE]

# How far either side of a citizenship phrase to look for EEO boilerplate.
_EEO_WINDOW = 160


def _eeo_boilerplate_near(text: str, start: int, end: int) -> bool:
    """Whether a citizenship phrase sits inside an equal-opportunity statement.

    "...without regard to race, religion, or citizenship status" is not a
    refusal to sponsor, but it contains the same words as one.
    """
    window = text[max(0, start - _EEO_WINDOW):end + _EEO_WINDOW]
    return any(rx.search(window) for rx in _EEO_RE)


def find_negative_signal(text: str) -> "str | None":
    """The phrase showing the employer won't sponsor, or None.

    Checked in two passes: unambiguous phrases, then citizenship/clearance
    patterns that are ignored when they appear inside EEO boilerplate.
    """
    for phrase in SPONSORSHIP_NEGATIVE:
        if phrase in text:
            return phrase

    for rx in _NEGATIVE_RE:
        for m in rx.finditer(text):
            if not _eeo_boilerplate_near(text, m.start(), m.end()):
                return m.group(0)

    return None


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

    Layers, in priority order:
    0. 'h1b-verified' source tag (e.g. MyVisaJobs) → likely
    1. Employer requires US citizenship / clearance → unlikely
    2. Explicit no-sponsorship language in the posting → unlikely
    3. USCIS-verified sponsor, or explicit positive language → likely
    4. Nothing conclusive → unknown

    Sets on the job:
        sponsorship_status  — 'likely' | 'unlikely' | 'unknown'
        is_h1b_sponsor      — bool, the employer has sponsored before
        h1b_approvals       — USCIS approvals for the employer (0 if unknown)
        h1b_fiscal_year     — fiscal year those counts come from
        h1b_match           — how the name was matched (exact/alias/prefix/...)
        h1b_confidence      — high | medium | low | none
        sponsorship_reason  — short human-readable explanation
    """
    company = job.get("company", "")
    description = _normalize(job.get("description", ""))
    title = _normalize(job.get("title", ""))
    full_text = f"{title} {description}"
    tags = job.get("tags", [])

    match = lookup_sponsor(company)
    job["h1b_approvals"] = match.approvals
    job["h1b_fiscal_year"] = match.fiscal_year
    job["h1b_match"] = match.match
    job["h1b_confidence"] = match.confidence

    def verdict(status: str, sponsor: bool, reason: str) -> dict:
        job["sponsorship_status"] = status
        job["is_h1b_sponsor"] = sponsor
        job["sponsorship_reason"] = reason
        return job

    # Layer 0: trusted source tag
    if isinstance(tags, list) and "h1b-verified" in tags:
        return verdict("likely", True, "listed on an H-1B verified job source")

    # Layer 1: employer requires citizenship — no posting language can fix this
    if match.match == "citizenship-required":
        return verdict(
            "unlikely", False,
            f"{match.matched_name} requires US citizenship or a clearance",
        )

    # Layer 2: explicit no-sponsorship language
    negative = find_negative_signal(full_text)
    if negative:
        return verdict("unlikely", False, f"posting says: “{negative}”")

    # Layer 3: verified sponsor or explicit positive language
    positive = next((p for p in SPONSORSHIP_POSITIVE if p in full_text), None)

    if match.is_sponsor and match.approvals:
        plural = "" if match.approvals == 1 else "s"
        return verdict(
            "likely", True,
            f"{match.approvals:,} H-1B approval{plural} in FY{match.fiscal_year}",
        )
    if match.is_sponsor:
        return verdict("likely", True, "known H-1B sponsor (no USCIS count)")
    if positive:
        return verdict("likely", False, f"posting says: “{positive}”")

    return verdict("unknown", False, "no sponsorship signal either way")


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


# Whole words only: plain substring matching on "intern" also hits "international",
# "internal" and "internet", which wrongly rejected full-time roles.
_INTERN_TITLE_RE = re.compile(r"\b(interns?|internships?|co-?ops?)\b")
# Descriptions of full-time roles often mention internships ("prior internship
# experience"), so only phrases that describe the role itself count.
_INTERN_DESC_RE = re.compile(
    r"\bthis (summer |fall |spring |winter )?(internship|co-?op)\b"
    r"|\binternship (position|role|opportunity)\b"
)


def is_internship(job: dict) -> bool:
    """Strict check — only internships & co-ops."""
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))
    return bool(_INTERN_TITLE_RE.search(title) or _INTERN_DESC_RE.search(description))


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
    2. Are beginner-level (new grad/entry — NOT senior, NOT internships)
    3. Are NOT explicitly anti-sponsorship
    """
    filtered = []
    stats = {
        "total": len(jobs),
        "location_filtered": 0,
        "role_filtered": 0,
        "seniority_filtered": 0,
        "phd_filtered": 0,
        "internship_filtered": 0,
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

        # Step 2.75: Internship filter (internships are excluded)
        if is_internship(job):
            stats["internship_filtered"] += 1
            continue

        # Step 3: Tag job type
        if is_fulltime_entry(job):
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
        f"Internships filtered: {stats['internship_filtered']}, "
        f"Sponsorship filtered: {stats['sponsorship_filtered']}, "
        f"Passed: {stats['passed']}"
    )
    return filtered


# ---------------------------------------------------------------------------
# Candidate-fit signals for the dashboard pages (recent grad / OPT / start date)
# ---------------------------------------------------------------------------
# (label, pattern, title_only). Title-only patterns are ambiguous in descriptions:
# "mentor junior engineers" appears in senior postings, and "graduate" usually
# refers to a degree requirement.
_GRAD_PATTERNS = [
    ("new grad", r"\bnew[- ]grad(uate)?s?\b", False),
    ("recent graduate", r"\brecent(ly)?[- ]grad(uate|uated|uates)?\b", False),
    ("entry level", r"\bentry[- ]level\b", False),
    ("early career", r"\bearly[- ]career\b", False),
    ("university / campus hire", r"\b(university|campus|college) (grad|graduate|hire|hiring|recruit\w*)\b", False),
    ("graduate program", r"\bgraduate (program|rotational|development)\b|\brotational program\b", False),
    ("class of 20xx", r"\bclass of 20\d\d\b", False),
    ("0-2 years experience", r"\b0\s*(-|–|to)\s*[12]\+? years?\b|\b1\s*(-|–|to)\s*2 years?\b|\b[01]\+ years?\b", False),
    ("junior", r"\bjunior\b|\bjr\.? ", True),
    ("graduate role", r"\bgraduate\b", True),
    ("level I role", r"\b(engineer|developer|scientist|analyst|sde|swe)\s*(i|1)\b(?![-/]?\s*(i|ii|2)\b)", True),
]
_GRAD_RE = [(label, re.compile(p), title_only) for label, p, title_only in _GRAD_PATTERNS]

_INTL_PATTERNS = [
    ("mentions OPT", r"\b(stem[- ])?opt\b(?![- ]?(in|out)\b)|optional practical training"),
    ("mentions CPT", r"\bcpt\b|curricular practical training"),
    ("F-1 students welcome", r"\bf-?1 (visas?|students?|status|holders?)\b"),
    ("international students", r"\binternational (students?|candidates|applicants|graduates)\b"),
    ("offers visa sponsorship", r"\b(visa|h-?1b|immigration) sponsorship (is )?(available|provided|offered)\b"
                                r"|\bwill sponsor\b|\bopen to sponsorship\b|\bsponsorship available\b"),
]
_INTL_RE = [(label, re.compile(p)) for label, p in _INTL_PATTERNS]

# Roles an F-1 / OPT candidate generally can't take even at a sponsoring company.
_INTL_BLOCKER_RE = re.compile(
    r"\bsecurity clearance\b|\b(active|secret|top secret|ts/sci) clearance\b|\bitar\b"
    r"|\bu\.?s\.? persons?\b|\bu\.?s\.? citizen(ship)?\b|\bpermanent resident\b"
    r"|\bwithout (visa |current or future )?sponsorship\b|\bnot (be )?(able|eligible) to sponsor\b"
)

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS.update({"sept": 9})
_SEASONS = {"winter": 1, "spring": 3, "summer": 6, "fall": 9, "autumn": 9}

_START_RE = re.compile(
    r"\b(start(s|ing)?( date)?|begin(s|ning)?|commenc\w+|join(ing)? us)\b[^.;\n]{0,40}?"
    r"\b(?P<when>" + "|".join(sorted(list(_MONTHS) + list(_SEASONS), key=len, reverse=True))
    + r")\.?,?\s+(?P<year>20\d\d)\b"
)
_IMMEDIATE_RE = re.compile(r"\b(immediate(ly)? start|start(ing)? immediately|immediate availability|start asap)\b")
_COHORT_RE = re.compile(r"\b(?P<year>20\d\d) (new grad|university grad|graduate|entry[- ]level|start)\b"
                        r"|\bnew grad(uate)?[\s,\-–(]*(?P<year2>20\d\d)\b")
# A year in the title of an entry-level role ("Software Developer 2027") is its cohort.
_TITLE_YEAR_RE = re.compile(r"\b(?P<year>20\d\d)\b")
_MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def start_date_signal(job: dict) -> dict:
    """Best-effort start date from the posting text.

    Returns {"label": str, "month": "YYYY-MM" | None, "early": bool}, where
    `early` means the role explicitly starts before EARLIEST_START.
    """
    title = _normalize(job.get("title", ""))
    text = f"{title} {_normalize(job.get('description', ''))}"

    m = _START_RE.search(text)
    if m:
        when, year = m.group("when"), int(m.group("year"))
        month = _MONTHS.get(when) or _SEASONS[when]
        ym = f"{year}-{month:02d}"
        label = (f"Starts {when.title()} {year}" if when in _SEASONS
                 else f"Starts {_MONTH_ABBR[month]} {year}")
        return {"label": label, "month": ym, "early": ym < EARLIEST_START}

    if _IMMEDIATE_RE.search(text):
        return {"label": "Immediate start", "month": None, "early": True}

    # "2026 New Grad" style cohorts: a cohort year before EARLIEST_START's year
    # starts too early; the same year or later is treated as compatible.
    m = _COHORT_RE.search(text)
    title_year = _TITLE_YEAR_RE.search(title)
    if m or title_year:
        year = int(title_year.group("year")) if title_year else int(m.group("year") or m.group("year2"))
        earliest_year = int(EARLIEST_START[:4])
        return {"label": f"{year} cohort", "month": None, "early": year < earliest_year}

    return {"label": "Start date not listed", "month": None, "early": False}


def candidate_signals(job: dict) -> dict:
    """Why a role does (or doesn't) suit a recent grad on F-1 OPT.

    grad      — evidence the role hires new / recent graduates
    intl      — evidence international students or visa holders are considered
    blockers  — clearance / citizenship / no-sponsorship language
    start     — see start_date_signal()
    """
    title = _normalize(job.get("title", ""))
    description = _normalize(job.get("description", ""))
    text = f"{title} {description}"
    tags = job.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tag_text = " ".join(tags).lower()

    grad = [label for label, rx, title_only in _GRAD_RE if rx.search(title if title_only else text)]
    if "new-grad" in tag_text and "new grad" not in grad:
        grad.append("new grad")  # SimplifyJobs new-grad list

    intl = [label for label, rx in _INTL_RE if rx.search(text)]
    if job.get("is_h1b_sponsor") or job.get("sponsorship_status") == "likely":
        intl.append("sponsors H-1B")

    blockers = sorted({b.group(0) for b in _INTL_BLOCKER_RE.finditer(text)})

    return {
        "grad": grad,
        "intl": intl,
        "blockers": blockers,
        "start": start_date_signal(job),
    }
