"""Job scrapers for multiple sources."""

import hashlib
import json
import time
import random
import logging
import re
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

from datetime import datetime, timezone

from config import SEARCH_QUERIES, LOCATIONS, EXPERIENCE_LEVEL

logger = logging.getLogger("job_hunter.scrapers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _make_id(title: str, company: str, location: str) -> str:
    """Source-agnostic ID — same job from different boards deduplicates properly."""
    raw = f"{title}|{company}|{location}".lower().strip()
    # Normalize whitespace
    raw = re.sub(r"\s+", " ", raw)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _polite_delay():
    time.sleep(random.uniform(1.5, 3.0))


def _short_delay():
    time.sleep(random.uniform(0.5, 1.2))


# ---------------------------------------------------------------------------
# Description fetcher (second-pass enrichment)
# ---------------------------------------------------------------------------
def fetch_description(url: str) -> str:
    """Fetch a job URL and extract description text.

    Works best for Lever, Greenhouse, LinkedIn, and generic career pages.
    Returns cleaned plain-text description or empty string on failure.
    """
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        # Try known job page selectors (most specific first)
        selectors = [
            # Lever
            {"class_": "section-wrapper page-centered"},
            {"class_": "content"},
            # Greenhouse
            {"id": "content"},
            {"id": "app_body"},
            # LinkedIn
            {"class_": "description__text"},
            {"class_": "show-more-less-html__markup"},
            # Jobright
            {"class_": re.compile(r"job.?description", re.I)},
            # Generic
            {"class_": re.compile(r"description|job.?detail|posting.?body", re.I)},
            {"role": "main"},
        ]

        text = ""
        for sel in selectors:
            el = soup.find("div", **sel) or soup.find("section", **sel)
            if el:
                text = el.get_text(separator=" ", strip=True)
                break

        if not text:
            # Last resort: grab the body text
            body = soup.find("body")
            if body:
                text = body.get_text(separator=" ", strip=True)

        # Truncate to reasonable size (sponsorship keywords are usually near top)
        return text[:5000]

    except Exception as e:
        logger.debug(f"Could not fetch description from {url}: {e}")
        return ""


def enrich_descriptions(
    jobs: list[dict], max_fetch: int = 50, deadline: float = None
) -> list[dict]:
    """Fetch descriptions for jobs that don't have one.

    Bounded by both `max_fetch` and `deadline` (a time.monotonic() value).
    The count alone is not enough: job pages are the slowest fetches in a scan
    — 40 of them have taken over an hour when a host trickles its response.
    """
    fetched = 0
    attempted = 0
    ran_out_of_time = False

    for job in jobs:
        if job.get("description"):
            continue
        if fetched >= max_fetch:
            break
        if deadline is not None and time.monotonic() >= deadline:
            ran_out_of_time = True
            break
        attempted += 1
        desc = fetch_description(job.get("url", ""))
        if desc:
            job["description"] = desc
            fetched += 1
        _short_delay()

    if ran_out_of_time:
        logger.warning(
            "Scan budget exhausted during enrichment — %d fetched from %d attempts",
            fetched, attempted,
        )
    else:
        logger.info(f"Enriched {fetched} job descriptions")
    return jobs


# ---------------------------------------------------------------------------
# LinkedIn (public guest API — no login required)
# ---------------------------------------------------------------------------
def scrape_linkedin(query: str, location: str = "United States") -> list[dict]:
    """Scrape LinkedIn public job listings via guest API."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        encoded_loc = quote_plus(location)
        # LinkedIn f_E: 1=Internship, 2=Entry level.
        exp_map = {"intern": "&f_E=1", "entry": "&f_E=2"}
        exp_filter = exp_map.get(EXPERIENCE_LEVEL, "")
        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={encoded_query}&location={encoded_loc}"
            f"&f_TPR=r86400{exp_filter}&start=0"  # last 24 hours
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"LinkedIn returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("div", class_="base-card")

        for card in cards:
            title_el = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            location_el = card.find("span", class_="job-search-card__location")
            link_el = card.find("a", class_="base-card__full-link")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""
            link = link_el["href"] if link_el and link_el.has_attr("href") else ""

            if not title or not company:
                continue

            jobs.append({
                "id": _make_id(title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link.split("?")[0] if link else "",
                "source": "linkedin",
                "description": "",
                "salary": "",
                "date_posted": datetime.now(timezone.utc).isoformat(),
                "tags": [query],
            })

        logger.info(f"LinkedIn: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"LinkedIn scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# RemoteOK (JSON API — supports remote ML/AI jobs)
# ---------------------------------------------------------------------------
def scrape_remoteok() -> list[dict]:
    """Scrape RemoteOK API for remote ML/AI/SWE jobs."""
    jobs = []
    try:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"RemoteOK returned {resp.status_code}")
            return jobs

        data = resp.json()
        if isinstance(data, list) and len(data) > 1:
            data = data[1:]  # first element is metadata

        target_tags = {
            "machine learning", "ml", "ai", "data science", "data scientist",
            "python", "deep learning", "software engineer", "sde", "nlp",
            "computer vision", "tensorflow", "pytorch",
        }

        for item in data:
            tags_raw = [t.lower() for t in item.get("tags", [])]
            position = item.get("position", "").lower()

            is_relevant = (
                any(t in target_tags for t in tags_raw)
                or any(kw in position for kw in [
                    "machine learning", "data scien", "ml ", "ai ",
                    "software engineer", "sde", "deep learning",
                ])
            )
            if not is_relevant:
                continue

            company = item.get("company", "")
            title = item.get("position", "")
            loc = item.get("location", "Remote")

            # Extract salary if available
            salary = ""
            sal_min = item.get("salary_min")
            sal_max = item.get("salary_max")
            if sal_min and sal_max:
                salary = f"${int(sal_min):,} - ${int(sal_max):,}"
            elif sal_min:
                salary = f"${int(sal_min):,}+"

            # Clean HTML from description
            desc_html = item.get("description", "")
            if desc_html:
                desc_soup = BeautifulSoup(desc_html, "html.parser")
                desc_text = desc_soup.get_text(separator=" ", strip=True)[:5000]
            else:
                desc_text = ""

            jobs.append({
                "id": _make_id(title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": item.get("url", f"https://remoteok.com/remote-jobs/{item.get('slug', '')}"),
                "source": "remoteok",
                "description": desc_text,
                "salary": salary,
                "date_posted": datetime.fromtimestamp(item.get("epoch", 0), tz=timezone.utc).isoformat() if item.get("epoch") else datetime.now(timezone.utc).isoformat(),
                "tags": tags_raw,
            })

        logger.info(f"RemoteOK: found {len(jobs)} relevant jobs")
    except Exception as e:
        logger.error(f"RemoteOK scraper error: {e}")
    return jobs


# ---------------------------------------------------------------------------
# Jobright.ai (job search with visa sponsorship filter)
# ---------------------------------------------------------------------------
def scrape_jobright(query: str) -> list[dict]:
    """Scrape Jobright.ai search results."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        url = (
            f"https://jobright.ai/jobs/search"
            f"?keyword={encoded_query}&location=United+States"
            f"&sort=date"
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Jobright returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")

        # Jobright renders job cards — try multiple selectors
        cards = soup.find_all("div", attrs={"data-testid": "job-card"})
        if not cards:
            cards = soup.find_all("a", class_=lambda c: c and "job" in c.lower())
        if not cards:
            cards = soup.find_all("div", class_=lambda c: c and ("card" in (c or "").lower() or "job" in (c or "").lower()))

        for card in cards:
            title_el = (
                card.find("h2") or card.find("h3")
                or card.find("span", class_=lambda c: c and "title" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "title" in (c or "").lower())
            )
            company_el = (
                card.find("span", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("p")
            )
            location_el = (
                card.find("span", class_=lambda c: c and "location" in (c or "").lower())
                or card.find("div", class_=lambda c: c and "location" in (c or "").lower())
            )

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else ""

            link = ""
            link_el = card.find("a", href=True)
            if link_el:
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://jobright.ai{href}"

            if not title:
                continue

            jobs.append({
                "id": _make_id(title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link,
                "source": "jobright",
                "description": "",
                "salary": "",
                "date_posted": datetime.now(timezone.utc).isoformat(),
                "tags": [query],
            })

        logger.info(f"Jobright: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Jobright scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# Wellfound (formerly AngelList Talent) — startup jobs, often sponsor-friendly
# ---------------------------------------------------------------------------
def scrape_wellfound(query: str) -> list[dict]:
    """Scrape Wellfound (AngelList) for startup jobs."""
    jobs = []
    try:
        slug = query.lower().replace(" ", "-").replace("/", "-")
        url = f"https://wellfound.com/role/l/r/{slug}/united-states"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Wellfound returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")

        # Wellfound uses startup-styled listing cards
        cards = soup.find_all("div", class_=lambda c: c and "styles_result" in (c or ""))
        if not cards:
            cards = soup.find_all("div", class_=lambda c: c and ("job" in (c or "").lower() or "listing" in (c or "").lower()))
        if not cards:
            cards = soup.find_all("a", href=lambda h: h and "/jobs/" in (h or ""))

        for card in cards:
            title_el = card.find("h2") or card.find("h3") or card.find("a")
            company_el = (
                card.find("h2", class_=lambda c: c and "company" in (c or "").lower())
                or card.find("span", class_=lambda c: c and "company" in (c or "").lower())
            )
            location_el = card.find("span", class_=lambda c: c and "location" in (c or "").lower())
            salary_el = card.find("span", class_=lambda c: c and "salary" in (c or "").lower() or "compensation" in (c or "").lower())

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else "United States"
            salary = salary_el.get_text(strip=True) if salary_el else ""

            link = ""
            link_el = card.find("a", href=True)
            if link_el:
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://wellfound.com{href}"

            if not title:
                continue

            jobs.append({
                "id": _make_id(title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link,
                "source": "wellfound",
                "description": "",
                "salary": salary,
                "date_posted": datetime.now(timezone.utc).isoformat(),
                "tags": [query],
            })

        logger.info(f"Wellfound: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"Wellfound scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# MyVisaJobs — dedicated H1B / visa sponsorship job data
# ---------------------------------------------------------------------------
def scrape_myvisajobs(query: str) -> list[dict]:
    """Scrape MyVisaJobs.com for H1B-sponsored positions."""
    jobs = []
    try:
        encoded_query = quote_plus(query)
        url = f"https://www.myvisajobs.com/Search_Jobs.aspx?q={encoded_query}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"MyVisaJobs returned {resp.status_code} for '{query}'")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")

        # MyVisaJobs uses table-based layout for results
        rows = soup.find_all("tr", class_=lambda c: c and ("tblrow" in (c or "").lower() or "odd" in (c or "").lower() or "even" in (c or "").lower()))
        if not rows:
            # Fallback: try finding any table with job-like data
            table = soup.find("table", {"id": lambda x: x and "job" in (x or "").lower()})
            if table:
                rows = table.find_all("tr")[1:]  # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            link_el = row.find("a", href=True)
            title = link_el.get_text(strip=True) if link_el else cells[0].get_text(strip=True)
            company = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            loc = cells[2].get_text(strip=True) if len(cells) > 2 else ""

            link = ""
            if link_el:
                href = link_el["href"]
                link = href if href.startswith("http") else f"https://www.myvisajobs.com/{href}"

            if not title:
                continue

            jobs.append({
                "id": _make_id(title, company, loc),
                "title": title,
                "company": company,
                "location": loc,
                "url": link,
                "source": "myvisajobs",
                "description": "",
                "salary": "",
                "date_posted": datetime.now(timezone.utc).isoformat(),
                # These are all from H1B data — mark as likely sponsors
                "sponsorship_status": "likely",
                "is_h1b_sponsor": True,
                "tags": [query, "h1b-verified"],
            })

        logger.info(f"MyVisaJobs: found {len(jobs)} jobs for '{query}'")
    except Exception as e:
        logger.error(f"MyVisaJobs scraper error for '{query}': {e}")
    return jobs


# ---------------------------------------------------------------------------
# SimplifyJobs GitHub — curated new grad list
# ---------------------------------------------------------------------------
def scrape_simplify_github() -> list[dict]:
    """Fetch curated job lists from SimplifyJobs GitHub repositories.

    These repos are community-maintained lists of verified new grad
    positions with rich metadata: date_posted, sponsorship status, degree
    requirements, and active/closed status.
    """
    from datetime import datetime, timedelta, timezone

    jobs = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_ts = int(cutoff.timestamp())

    repos = [
        {
            "url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
            "tag": "new-grad",
        },
    ]

    for repo in repos:
        repo_count = 0
        try:
            resp = requests.get(repo["url"], headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"SimplifyJobs GitHub returned {resp.status_code} for {repo['tag']}")
                continue

            data = resp.json()
            if not isinstance(data, list):
                continue

            for item in data:
                title = item.get("title", "") or item.get("role", "")
                company = item.get("company_name", "") or item.get("company", "")
                locations = item.get("locations", [])
                loc = ", ".join(locations) if isinstance(locations, list) else str(locations)
                link = item.get("url", "") or item.get("application_link", "")

                # --- Filter: must be active ---
                if item.get("is_closed") or item.get("active") is False:
                    continue

                if not title or not company:
                    continue

                # --- Filter: posted in last 24 hours ---
                date_posted_ts = item.get("date_posted", 0)
                if date_posted_ts and date_posted_ts < cutoff_ts:
                    continue

                # Convert unix timestamp to ISO string
                date_posted_str = ""
                if date_posted_ts:
                    date_posted_str = datetime.fromtimestamp(
                        date_posted_ts, tz=timezone.utc
                    ).isoformat()

                # --- Filter: skip PhD-only roles ---
                degrees = item.get("degrees", [])
                if degrees:
                    degrees_lower = [d.lower() for d in degrees]
                    # If ONLY PhD/doctorate listed, skip
                    has_phd_only = all(
                        "phd" in d or "doctor" in d or "postdoc" in d
                        for d in degrees_lower
                    )
                    if has_phd_only:
                        continue

                # --- Filter: sponsorship ---
                sponsorship_raw = (item.get("sponsorship") or "").lower()
                # "Does Sponsor" = F1 friendly, "Doesn't Sponsor" = reject, "Other"/"" = unknown
                if "doesn't sponsor" in sponsorship_raw or "does not sponsor" in sponsorship_raw:
                    continue  # Skip — won't hire F1

                is_sponsor = "does sponsor" in sponsorship_raw
                sponsorship_status = "likely" if is_sponsor else "unknown"

                # Check relevance to our target roles
                title_lower = title.lower()
                relevant = any(kw in title_lower for kw in [
                    "data", "machine learning", "ml", "ai", "software",
                    "sde", "swe", "engineer", "scientist", "nlp",
                    "deep learning", "computer vision", "research",
                ])
                if not relevant:
                    continue

                tags = [repo["tag"]]
                if is_sponsor:
                    tags.append("f1-friendly")

                repo_count += 1
                jobs.append({
                    "id": _make_id(title, company, loc),
                    "title": title,
                    "company": company,
                    "location": loc if loc else "United States",
                    "url": link,
                    "source": "simplify",
                    "description": "",
                    "salary": "",
                    "date_posted": date_posted_str,
                    "sponsorship_status": sponsorship_status,
                    "is_h1b_sponsor": is_sponsor,
                    "tags": tags,
                })

            logger.info(f"SimplifyJobs ({repo['tag']}): found {repo_count} jobs posted in last 24h")
        except Exception as e:
            logger.error(f"SimplifyJobs GitHub error for {repo['tag']}: {e}")

    return jobs


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def scrape_all(deadline: float = None) -> list[dict]:
    """Run all scrapers and return combined job list.

    `deadline` is a time.monotonic() value past which no new work is started.
    It bounds the scan as a whole: requests' own `timeout` only bounds each
    socket operation, so one slow server can otherwise stall a scan for hours.
    Work already in flight still finishes, so the budget is a floor, not a cap.
    """
    all_jobs = []
    skipped = 0

    def out_of_time() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    # Per-query scrapers: LinkedIn, Jobright, Wellfound, MyVisaJobs
    for index, query in enumerate(SEARCH_QUERIES):
        if out_of_time():
            skipped = len(SEARCH_QUERIES) - index
            logger.warning(
                "Scan budget exhausted — skipping %d of %d queries",
                skipped, len(SEARCH_QUERIES),
            )
            break
        all_jobs.extend(scrape_linkedin(query))
        _polite_delay()
        all_jobs.extend(scrape_jobright(query))
        _polite_delay()
        all_jobs.extend(scrape_wellfound(query))
        _polite_delay()
        all_jobs.extend(scrape_myvisajobs(query))
        _polite_delay()

    # One-shot scrapers (not per-query)
    if not out_of_time():
        all_jobs.extend(scrape_remoteok())
        _polite_delay()
    if not out_of_time():
        all_jobs.extend(scrape_simplify_github())

    # Deduplicate by ID (now source-agnostic)
    seen_ids = set()
    unique_jobs = []
    for job in all_jobs:
        if job["id"] not in seen_ids:
            seen_ids.add(job["id"])
            unique_jobs.append(job)

    logger.info(f"Total unique jobs scraped: {len(unique_jobs)} (from {len(all_jobs)} raw)")

    # Enrich: fetch descriptions for top jobs that lack them
    unique_jobs = enrich_descriptions(unique_jobs, max_fetch=40, deadline=deadline)

    return unique_jobs
